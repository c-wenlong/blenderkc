"""The detached host job: what `build --host` starts on the render host, once
per topic, so a render that takes a day does not depend on one ssh
connection staying up. Standard library only; it runs on the host.

    host-job start --job - [--ensure-venv] [--venv V]
        reads a render job (runner.py's schema) on stdin and, unless a job of
        this topic is already running, writes it down and starts
        `host-job run` in a session of its own (stdin, stdout and stderr off
        the ssh channel, so the ssh call returns at once and a dropped
        connection cannot reach it). Answers {state: started|attached, pid}.
        A running job that already covers every scene asked for is attached
        to; one that does not is refused, since there is one job per topic.
    host-job run --topic T
        the detached process: runner.run_job over the job's scenes in order,
        frames resumable, each plate encoded and checked, keeping status.json
        as it goes. Renders run inside the same memory scope as before.
    host-job status --topic T
        status.json read with what is on disk: scenes done, the current
        scene and how many of its frames exist, seconds a frame, an ETA.

Everything sits under the host data root (~/.cache/blenderkc/home/ by
default), so nothing outside ~/.cache/blenderkc/ is touched:

    cache/hostjob/<topic>/job.json     the job as sent
                          pid          the detached runner's pid
                          status.json  what it has done, written as it goes
                          log          its output and the renderer's (log.1: the previous job's)
                          code         the checkout copy it runs from
                          .start.lock  held while a start decides
    cache/jobs/<topic>/.lock           runner.py's lock: one render job per topic

A job that died (a reboot, an out-of-memory kill) is not restarted here: the
build on the Mac sees `died` in the status and starts it again, and it
resumes from the frames already rendered.
"""

from __future__ import annotations

import json
import os
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import runner
from .locks import _alive, _pid, acquire
from .repo import CliError, Repo, read_json, write_json

VERB = "host-job"
LOG_TAIL = 20
CLI = Path(__file__).resolve().parent.parent / "blenderkc"  # tools/cli/blenderkc


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def job_dir(repo: Repo, topic: str) -> Path:
    return repo.cache / "hostjob" / topic


def _command_of(pid: int) -> str | None:
    """A process's command line, or None when it cannot be read."""
    f = Path(f"/proc/{pid}/cmdline")
    if f.exists():
        try:
            return f.read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            return None
    try:
        r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() or None


def _ours(pid: int | None, *words: str) -> bool:
    """`pid` is alive and is one of ours (its command line names a word), so a
    pid file left by a reboot never vouches for an unrelated process."""
    if not pid or not _alive(pid):
        return False
    cmd = _command_of(pid)
    return cmd is None or any(w in cmd for w in words)


def runner_pid(repo: Repo, topic: str) -> int | None:
    pid = _pid(job_dir(repo, topic) / "pid")
    return pid if _ours(pid, VERB) else None


def _pairs(job: dict) -> list[tuple[str, str]]:
    return [(s["scene"], s["plate"]) for s in job.get("scenes", [])]


def start(repo: Repo, job: dict, *, venv: Path | None = None, ensure_venv: bool = False) -> dict:
    """Starts the job detached, or attaches to the one running."""
    runner.validate_job(job)
    topic = job["topic"]
    if not (repo.topics_dir / topic).is_dir():
        raise CliError(f"no topic folder {repo.topics_dir / topic}")
    d = job_dir(repo, topic)
    d.mkdir(parents=True, exist_ok=True)
    release = acquire(d / ".start.lock", "host job start")
    try:
        pid = runner_pid(repo, topic)
        if pid:
            running = read_json(d / "job.json", {})
            have = set(_pairs(running))
            extra = [s for s, p in _pairs(job) if (s, p) not in have]
            if extra:
                raise CliError(
                    f"a host job of {topic} is running (pid {pid}) without {', '.join(extra[:5])}"
                    f"{' and more' if len(extra) > 5 else ''} at these keys (other code or settings); "
                    f"wait for it to finish, or stop it on the host with `kill {pid}`")
            return {"state": "attached", "pid": pid, "topic": topic, "scenes": len(running.get("scenes", []))}
        held = _pid(repo.cache / "jobs" / topic / ".lock")
        if _ours(held, VERB):
            raise CliError(f"a host job of {topic} holds the render lock (pid {held}) but its pid file names another "
                           f"process; wait for it, or stop it on the host with `kill {held}`")
        if _ours(held, "render-job"):
            raise CliError(f"a render job of {topic} is already running on this host (pid {held}), from a build "
                           "that runs one ssh job per scene; stop that build first (its host job ends with it)")
        if ensure_venv:
            log(f"venv: {runner.ensure_venv(repo, venv)}")
        python = str(venv / "bin" / "python") if venv else repo.python()[0]
        if not Path(python).exists():
            raise CliError(f"no Python at {python}: run with --ensure-venv, or make deps")
        before = read_json(d / "job.json", None)
        same = isinstance(before, dict) and _pairs(before) == _pairs(job)
        if (d / "log").exists() and not same:
            os.replace(d / "log", d / "log.1")
        write_json(d / "job.json", job)
        (d / "code").write_text(str(repo.root) + "\n")
        old = read_json(d / "status.json", {}) if same else {}
        # no `done` carried over: run_job checks every plate again and says which are ready
        write_json(d / "status.json", {"state": "starting", "at": now_iso(), "done": [],
                                       "restarts": (old.get("restarts", -1) + 1) if same else 0})
        env = {k: v for k, v in os.environ.items() if k != "BLENDERKC_WATCH_PARENT"}
        env.update(BLENDERKC_PYTHON=python, BLENDERKC_HOME=str(repo.home))
        with open(d / "log", "ab") as fh:
            fh.write(f"--- {'resumed' if same else 'started'} {now_iso()}\n".encode())
            fh.flush()
            proc = subprocess.Popen([sys.executable, str(CLI), "--root", str(repo.root), VERB, "run", "--topic", topic],
                                    stdin=subprocess.DEVNULL, stdout=fh, stderr=fh, env=env, cwd=repo.root,
                                    start_new_session=True, close_fds=True)
        (d / "pid").write_text(f"{proc.pid}\n")
        return {"state": "started", "pid": proc.pid, "topic": topic, "scenes": len(job["scenes"]), "resumed": same}
    finally:
        release()


def _processes() -> list[tuple[int, str]]:
    """(pid, command line) of every process here."""
    try:
        r = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in r.stdout.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if pid.isdigit():
            out.append((int(pid), cmd))
    return out


def is_renderer_of(cmd: str, repo: Repo, topic: str) -> bool:
    """`cmd` is runner.render_cmd's command line for this topic, in this data root."""
    return ("blenderkc.render" in cmd and re.search(rf"--topic .*/topics/{re.escape(topic)} --scene ", cmd) is not None
            and f" --out {repo.frames / topic}/" in cmd)


def stop_orphan_renderers(repo: Repo, topic: str, grace: float = 10.0) -> list[int]:
    """Stops renderers of this topic that outlived their job: a runner killed
    outright (SIGKILL, an out-of-memory kill) leaves Blender rendering, and a
    second one would write the same frames. Any renderer of exactly this
    topic writing under its frames is one of those, since the render lock
    says no job of it runs. The topic is matched whole (`--topic
    .../topics/<topic> --scene`), so a topic nested under this one's path is
    never touched; the copy of the code it ran from may be any."""
    pids = [pid for pid, cmd in _processes() if pid != os.getpid() and is_renderer_of(cmd, repo, topic)]
    for pid in pids:
        log(f"stopping a renderer left by an earlier job of {topic} (pid {pid})")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    end = time.monotonic() + grace
    while pids and time.monotonic() < end and any(_alive(p) for p in pids):
        time.sleep(0.2)
    for pid in pids:
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return pids


def run(repo: Repo, topic: str) -> dict:
    """The detached process. Writes status.json at every scene and plate."""
    d = job_dir(repo, topic)
    (d / "pid").write_text(f"{os.getpid()}\n")
    job = read_json(d / "job.json")
    status = read_json(d / "status.json", {})
    status.update(state="running", pid=os.getpid(), startedAt=now_iso(), finishedAt=None, error=None, current=None)
    done: dict[str, dict] = {}  # filled as run_job checks each plate, cached or new
    used = repo.root / ".used"  # the snapshot's mark on the host: a running job keeps it fresh against pruning
    ticking = threading.Event()

    def keep_used():  # every hour, so a scene longer than the prune's two days never loses its code
        while not ticking.wait(3600):
            try:
                used.touch()
            except OSError:
                pass
    if used.exists():
        threading.Thread(target=keep_used, daemon=True).start()

    def save():
        status["done"] = list(done.values())
        write_json(d / "status.json", status)

    def progress(event: str, info: dict) -> None:
        if event == "scene":
            done.pop(info["scene"], None)  # a plate being made again is not ready until it is checked
            status["current"] = {"scene": info["scene"], "frames": info["frames"], "since": now_iso()}
            if used.exists():
                used.touch()
        elif event == "plate":
            done[info["scene"]] = info
            status["current"] = None
        save()

    save()
    try:
        release = acquire(repo.cache / "jobs" / topic / ".lock", "render job")  # held through the sweep below
        try:
            stop_orphan_renderers(repo, topic)
        finally:
            release()
        reply = runner.run_job(repo, job, progress=progress)
        status.update(state="done", finishedAt=now_iso())
        return reply
    except KeyboardInterrupt:
        status.update(state="stopped", finishedAt=now_iso(), error="stopped by a signal")
        raise
    except CliError as e:
        status.update(state="failed", finishedAt=now_iso(), error=str(e))
        raise
    except Exception as e:  # noqa: BLE001 - recorded, then raised
        status.update(state="failed", finishedAt=now_iso(), error=f"{type(e).__name__}: {e}")
        raise
    finally:
        ticking.set()
        save()


def _frames_on_disk(fd: Path) -> list[Path]:
    try:
        return [fd / n for n in os.listdir(fd) if n.startswith("f_") and n.endswith(".png")]
    except OSError:
        return []


def seconds_a_frame(frames: list[Path], last: int = 20) -> float | None:
    """The median gap between the newest frames' write times: what a frame
    costs now, with a pause (a restart) between two frames ignored."""
    times = []
    for f in frames:
        try:
            times.append(f.stat().st_mtime)
        except OSError:
            pass
    times = sorted(times)[-(last + 1):]
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    return round(statistics.median(gaps), 2) if len(gaps) >= 2 else None


def _tail(f: Path, n: int = LOG_TAIL) -> list[str]:
    try:
        with open(f, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 16384))
            return fh.read().decode(errors="replace").splitlines()[-n:]
    except OSError:
        return []


def status(repo: Repo, topic: str) -> dict:
    """The job as a person or the Mac wants it: state (none, starting,
    running, done, failed, stopped, died), scenes done, the current scene's
    frame, seconds a frame, and the seconds left."""
    d = job_dir(repo, topic)
    job = read_json(d / "job.json", None)
    if not isinstance(job, dict):
        return {"topic": topic, "state": "none", "scenes": [], "done": 0, "total": 0}
    st = read_json(d / "status.json", {})
    pid = runner_pid(repo, topic)
    state = st.get("state") or "starting"
    if state in ("starting", "running") and not pid:
        state = "died"
    done = {x.get("scene"): x for x in st.get("done", []) if isinstance(x, dict)}
    scenes, left, spf, current = [], 0, None, None
    cur_name = (st.get("current") or {}).get("scene")
    for s in job.get("scenes", []):
        n = int(s["frames"])
        fd = runner.frames_dir(repo, topic, s["scene"], s["plate"])
        frames = [] if s["scene"] in done else _frames_on_disk(fd)
        row = {"scene": s["scene"], "frames": n, "plate": runner.plate_path(repo, topic, s["scene"], s["plate"])
               .relative_to(repo.home).as_posix(), "done": s["scene"] in done, "rendered": n if s["scene"] in done else len(frames)}
        if row["done"]:
            row["cached"] = bool(done[s["scene"]].get("cached"))
            row["seconds"] = done[s["scene"]].get("seconds")
        else:
            left += n - len(frames)
        if s["scene"] == cur_name and not row["done"]:
            current = {"scene": s["scene"], "frame": len(frames), "frames": n}
            spf = seconds_a_frame(frames)
        scenes.append(row)
    if spf is None:  # between scenes, or a scene's first frames: the last scene rendered
        made = [x for x in done.values() if not x.get("cached") and x.get("frames") and x.get("seconds")]
        if made:
            spf = round(made[-1]["seconds"] / made[-1]["frames"], 2)
    out = {"topic": topic, "state": state, "pid": pid, "startedAt": st.get("startedAt"), "finishedAt": st.get("finishedAt"),
           "restarts": st.get("restarts", 0), "error": st.get("error"), "settings": job.get("settings"),
           "scenes": scenes, "done": sum(1 for r in scenes if r["done"]), "total": len(scenes), "current": current,
           "secondsPerFrame": spf, "framesLeft": left, "etaSeconds": round(left * spf) if spf and state != "done" else None}
    if state in ("failed", "died", "stopped"):
        out["logTail"] = _tail(d / "log")
    return out


def main(args, repo: Repo) -> int:
    """`host-job start|run|status`: internal, what build --host runs on the host."""
    if args.action == "start":
        text = sys.stdin.read() if args.job == "-" else Path(args.job).read_text()
        try:
            job = json.loads(text)
        except ValueError as e:
            raise CliError(f"the render job is not JSON: {e}")
        venv = Path(os.path.expanduser(args.venv)) if args.venv else None
        print(json.dumps(start(repo, job, venv=venv, ensure_venv=args.ensure_venv)))
        return 0
    if not args.topic:
        raise CliError(f"{VERB} {args.action} needs --topic")
    if args.action == "run":
        run(repo, args.topic)
        return 0
    print(json.dumps(status(repo, args.topic), indent=1))
    return 0
