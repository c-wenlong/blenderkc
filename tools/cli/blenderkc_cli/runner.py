"""The render job: render each scene's frames with the library's renderer,
encode them into a plate, and drop the frames. It runs wherever the 3D runs:
in-process for a local build, and on the render host for `build --host`,
where `render-job --job -` reads the same job from ssh's stdin. Standard
library only, so the host needs nothing but python3, uv and ffmpeg.

A job is self-contained JSON, every path derived from the data root of the
machine it runs on:

    {"schema": 1, "topic": "<topic path>", "shots": "shots/", "fps": 30,
     "settings": {"res": 100, "samples": 16, "device": "auto"},
     "keepFrames": false,
     "scenes": [{"scene": "HB1_Hook", "frames": 912, "plate": "<plate key>",
                 "timing": {...the scene timing file...}}]}

Per scene:

    <home>/plates/<topic>/<Scene>.<key16>.mp4        the plate; present = done
    <home>/frames/<topic>/<Scene>/<key16>/f_0001.png  frames while rendering;
                                                      resumable, removed once
                                                      the plate is checked
    <home>/frames/<topic>/<Scene>/<key16>.timing.json the --timing file

The renderer runs as `<python> -m blenderkc.render --topic <dir> --scene <S>
--timing <file> --frames 1 <N> --out <dir> --res --samples --device`, from
the checkout, inside `systemd-run --user --scope -q -p MemoryMax=3G` where
that works (Linux), so a scene can never take the shared server down.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import keys, media
from .locks import acquire
from .repo import CliError, Repo, which

JOB_SCHEMA = 1
MEMORY_MAX_DEFAULT = "3G"
UV_PLACES = ("~/.nix-profile/bin/uv", "~/.local/bin/uv", "~/.cargo/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv")
VENV_PYTHON = "3.13"
STAMP = ".blenderkc-requirements.sha256"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def plate_path(repo: Repo, topic: str, scene: str, key: str) -> Path:
    return repo.plates / topic / f"{scene}.{keys.short(key)}.mp4"


def frames_dir(repo: Repo, topic: str, scene: str, key: str) -> Path:
    return repo.frames / topic / scene / keys.short(key)


def find_uv() -> str | None:
    found = which("uv")
    if found:
        return found
    for p in UV_PLACES:
        p = os.path.expanduser(p)
        if os.access(p, os.X_OK):
            return p
    return None


def requirements_hash(root: Path) -> str:
    return hashlib.sha256((root / "requirements.txt").read_bytes()).hexdigest()


def ensure_venv(repo: Repo, venv: Path | None = None) -> str:
    """`make deps` in Python: a venv at Python 3.13 with requirements.txt,
    reinstalled when requirements.txt changed since. The checkout's .venv by
    default; build --host passes a venv shared by every checkout copy on the
    host, named by the hash of requirements.txt. Returns what it did."""
    venv = venv or repo.venv
    py = venv / "bin" / "python"
    want = requirements_hash(repo.root)
    stamp = venv / STAMP
    if py.exists() and stamp.exists() and stamp.read_text().strip() == want:
        return f"{venv} ready"
    uv = find_uv()
    if not uv:
        raise CliError("uv is not on PATH (nor in ~/.nix-profile/bin or ~/.local/bin); install uv to create the venv")
    did = []
    if not py.exists():
        log(f"creating {venv} (Python {VENV_PYTHON}) with uv")
        if subprocess.run([uv, "venv", "-q", "--python", VENV_PYTHON, str(venv)], stdout=sys.stderr).returncode:
            raise CliError(f"uv could not create {venv} with Python {VENV_PYTHON}")
        did.append("created")
    log(f"installing requirements.txt into {venv} (bpy is a large wheel; uv caches it)")
    if subprocess.run([uv, "pip", "install", "-q", "--python", str(py), "--compile-bytecode", "-r",
                       str(repo.root / "requirements.txt")], stdout=sys.stderr).returncode:
        raise CliError(f"uv could not install requirements.txt into {venv}")
    stamp.write_text(want + "\n")
    did.append("installed")
    return f"{venv} {' and '.join(did)}"


_scope_ok: bool | None = None


def scope_prefix(repo: Repo) -> list[str]:
    """`systemd-run --user --scope -q -p MemoryMax=<cap>` when it works here,
    else nothing. Probed once per process with a no-op."""
    global _scope_ok
    if sys.platform != "linux" or (repo.setting("BLENDERKC_MEMORY_MAX") or "") == "none":
        return []
    cap = repo.setting("BLENDERKC_MEMORY_MAX") or MEMORY_MAX_DEFAULT
    pre = ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={cap}"]
    if _scope_ok is None:
        _scope_ok = bool(which("systemd-run")) and subprocess.run([*pre, "true"], capture_output=True).returncode == 0
        if not _scope_ok:
            log("systemd-run --user --scope is not available here; rendering without a memory cap")
    return pre if _scope_ok else []


def render_cmd(repo: Repo, python: str, topic_dir: Path, scene: str, timing: Path, first: int, last: int,
               out: Path, settings: dict, *, overlays_only: bool = False, prefix: list[str] | None = None) -> list[str]:
    cmd = [*(prefix or []), python, "-m", "blenderkc.render", "--topic", str(topic_dir), "--scene", scene,
           "--timing", str(timing), "--frames", str(first), str(last), "--out", str(out),
           "--res", str(settings["res"]), "--samples", str(settings["samples"]),
           "--device", str(settings.get("device") or "auto")]
    if overlays_only:
        cmd.append("--overlays-only")
    return cmd


def lib_env(repo: Repo) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(repo.root), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])])
    return env


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def run_renderer(repo: Repo, cmd: list[str], what: str) -> None:
    """Runs the renderer with its output on our stderr, so stdout stays a reply.

    The renderer never outlives us. Interrupted (Ctrl-C, SIGTERM), we stop it
    first. With BLENDERKC_WATCH_PARENT=1, which build --host sets on the host,
    we also stop it and exit when our parent goes away: ssh without a terminal
    sends no hangup when the connection drops, so without this a cut
    connection would leave Blender rendering on a shared machine."""
    proc = subprocess.Popen(cmd, cwd=repo.root, env=lib_env(repo), stdout=sys.stderr, stderr=sys.stderr)
    parent = os.getppid()
    watch = os.environ.get("BLENDERKC_WATCH_PARENT") == "1"
    try:
        while True:
            try:
                code = proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                if watch and os.getppid() != parent:
                    _stop(proc)
                    os._exit(3)
    except BaseException:
        _stop(proc)
        raise
    if code != 0:
        raise CliError(f"the renderer failed on {what} (exit {code}); its output is above")


def missing_frames(d: Path, first: int, last: int) -> list[int]:
    return [n for n in range(first, last + 1) if not (d / media.frame_name(n)).is_file()]


def validate_job(job: dict) -> None:
    if not isinstance(job, dict) or job.get("schema") != JOB_SCHEMA:
        raise CliError(f"not a render job of schema {JOB_SCHEMA}")
    for k in ("topic", "fps", "settings", "scenes"):
        if k not in job:
            raise CliError(f"render job has no {k!r}")
    for s in job["scenes"]:
        for k in ("scene", "frames", "plate", "timing"):
            if k not in s:
                raise CliError(f"render job scene has no {k!r}")


def run_job(repo: Repo, job: dict, progress=None) -> dict:
    """Renders every scene of the job that has no plate yet. Returns
    {plates: [{scene, plate, frames, cached, seconds}], seconds}.

    `progress(event, info)`, when given, hears ("scene", {scene, frames,
    key}) as a scene starts rendering and ("plate", <its reply entry>) once
    its plate is checked, cached or new: the detached host job (hostjob.py)
    keeps its status file with it."""
    tell = progress or (lambda event, info: None)
    validate_job(job)
    media.require_ffmpeg()
    topic = job["topic"]
    topic_dir = repo.topics_dir / topic
    if not topic_dir.is_dir():
        raise CliError(f"no topic folder {topic_dir}")
    python, src = repo.python()
    if not Path(python).exists():
        raise CliError(f"no Python at {python} ({src}): make deps")
    release = acquire(repo.cache / "jobs" / topic / ".lock", "render job")
    started = time.monotonic()
    out = []
    try:
        for i, s in enumerate(job["scenes"], 1):
            scene, n, key = s["scene"], int(s["frames"]), s["plate"]
            plate = plate_path(repo, topic, scene, key)
            tag = f"[{i}/{len(job['scenes'])}] {scene}"
            if plate.is_file() and media.count_frames(plate) == n:
                log(f"{tag}: plate cached ({plate.name})")
                out.append({"scene": scene, "plate": plate.relative_to(repo.home).as_posix(), "frames": n, "cached": True, "seconds": 0})
                tell("plate", out[-1])
                continue
            t0 = time.monotonic()
            tell("scene", {"scene": scene, "frames": n, "key": key})
            fd = frames_dir(repo, topic, scene, key)
            parent = fd.parent
            if parent.is_dir():  # renders of this scene under other keys are stale
                for other in parent.iterdir():
                    if other.is_dir() and other.name != fd.name:
                        shutil.rmtree(other, ignore_errors=True)
                    elif other.name.endswith(".timing.json") and other.name != f"{fd.name}.timing.json":
                        other.unlink(missing_ok=True)
            fd.mkdir(parents=True, exist_ok=True)
            timing = parent / f"{fd.name}.timing.json"
            timing.write_text(json.dumps(s["timing"], indent=1, sort_keys=True) + "\n")
            todo = missing_frames(fd, 1, n)
            if todo:
                prefix = scope_prefix(repo)
                cap = f", in a {prefix[-1].split('=')[1]} memory scope" if prefix else ""
                log(f"{tag}: rendering {len(todo)} of {n} frames ({job['settings']['res']}%, {job['settings']['samples']} samples{cap}) into {fd}")
                run_renderer(repo, render_cmd(repo, python, topic_dir, scene, timing, 1, n, fd, job["settings"],
                                              prefix=prefix), scene)
                left = missing_frames(fd, 1, n)
                if left:
                    raise CliError(f"{scene}: the renderer exited 0 but {len(left)} frames are missing (first f_{left[0]:04d}.png)")
            log(f"{tag}: encoding the plate")
            media.encode_frames(fd, plate, fps=job["fps"], start=1, count=n, plate=True)
            got = media.count_frames(plate)
            if got != n:
                plate.unlink(missing_ok=True)
                raise CliError(f"{scene}: the plate has {got} frames, the scene {n}")
            if not job.get("keepFrames"):
                shutil.rmtree(fd, ignore_errors=True)
            out.append({"scene": scene, "plate": plate.relative_to(repo.home).as_posix(), "frames": n, "cached": False,
                        "seconds": round(time.monotonic() - t0, 1)})
            tell("plate", out[-1])
    finally:
        release()
    return {"topic": topic, "plates": out, "seconds": round(time.monotonic() - started, 1)}


def main(args, repo: Repo) -> int:
    """`render-job --job <file|->`: what build runs, locally and on --host."""
    text = sys.stdin.read() if args.job == "-" else Path(args.job).read_text()
    try:
        job = json.loads(text)
    except ValueError as e:
        raise CliError(f"the render job is not JSON: {e}")
    venv = Path(os.path.expanduser(args.venv)) if args.venv else None
    if args.ensure_venv:
        log(f"venv: {ensure_venv(repo, venv)}")
    if venv:
        os.environ["BLENDERKC_PYTHON"] = str(venv / "bin" / "python")
    reply = run_job(repo, job)
    print(json.dumps(reply, indent=1))
    return 0
