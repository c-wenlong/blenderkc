"""`build --host <ssh host>`: the 3D on another machine (the homeserver's
GTX 1070), everything 2D here. The host needs ssh, rsync, python3, ffmpeg and
uv, and nothing else of ours: it cannot clone a private repository, so the
checkout is copied.

On the host, everything lives under ~/.cache/blenderkc/ (BLENDERKC_HOST_BASE
names another folder under the host's home, for a trial run):

    ~/.cache/blenderkc/src/<name>-<digest>/
                                      a snapshot of this checkout (<name> is
                                      its folder name, <digest> the hash of
                                      what is copied), minus .git, venvs, .env
                                      and data, with the topics tree pushed to
                                      <snapshot>/topics/ by its own rsync (so
                                      BLENDERKC_TOPICS outside the checkout is
                                      copied too). Content-addressed, so two
                                      builds of different code or different
                                      topics never share one, and a build's
                                      host job runs the code its keys were
                                      computed from
    ~/.cache/blenderkc/venv-<hash>/   bpy and Pillow, made with uv on first
                                      use; <hash> is requirements.txt's, so
                                      checkout copies share one venv
    ~/.cache/blenderkc/home/          the host's BLENDERKC_HOME: frames while
                                      they render, plates, the host job's
                                      folder (hostjob.py), the job lock

The steps:

    1. rsync the checkout to its snapshot on the host; snapshots of this
       checkout's name not used for two days are removed, newest five kept
    2. one ssh: `host-job start --job -` with every scene still needing a
       plate on stdin (timing and plate key each). The host starts ONE
       detached job for the topic, or attaches to the one already running
       (a build run again after this process died), and the call returns
    3. poll the job (`host-job status`, a small JSON reply) every minute or
       so; rsync each plate back here as soon as the host has checked it;
       start the job again if it died, and it resumes from its frames
    4. return when every plate is here; build composites and finishes here

Every ssh and rsync call goes through Link: a failure of the connection
(ssh exit 255, an rsync connection error, a call that hangs past its time,
a Tailscale SSH check waiting for a browser login) is retried with backoff
for up to BLENDERKC_HOST_RETRY_HOURS (24 by default, `--retry-hours`)
instead of failing the build, and a Tailscale login URL is printed once, as
a line a person can act on. BatchMode stays on, so nothing ever waits on a
prompt. Stopping this process stops only the watching: the host job keeps
rendering, and the same build run again picks it up.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .repo import TOPICS_ENV, CliError, Repo, is_inside

REMOTE_BASE = ".cache/blenderkc"  # relative to the host's $HOME; BLENDERKC_HOST_BASE overrides
DATA_DIRS = ("out", "public", "stills", "frames", "plates", "cache", "state")
ALWAYS_EXCLUDE = ("/.used", "/.git", "/.venv", "/.venv-*", "/.env", "__pycache__", "*.pyc", ".DS_Store", "*.blend1")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=6"]
HOST_PATH = 'export PATH="$HOME/.nix-profile/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"'

RETRY_HOURS = 24.0
POLL_S = 60.0
FIRST_WAIT_S = 15.0
MAX_WAIT_S = 300.0
REMIND_S = 1800.0  # a still-pending Tailscale login is shown again, with its newest URL, this often
REPORT_S = 600.0  # a progress line at least this often while nothing changes
MAX_RESTARTS = 3  # a host job that stops this many times running without a new frame is a real failure

TIMED_OUT = -1  # the exit code a call gets when it ran past its time and was stopped
SSH_TRANSIENT = frozenset({255, TIMED_OUT})
RSYNC_TRANSIENT = frozenset({255, 10, 12, 30, 35, TIMED_OUT})  # ssh, socket, stream, timeout, daemon timeout
TAILSCALE_URL = re.compile(r"https://login\.tailscale\.com/a/[A-Za-z0-9_-]+")
TAILSCALE_NOISE = ("# Tailscale SSH requires an additional check", "# To authenticate, visit")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def remote_base(repo: Repo) -> str:
    """The host folder for everything of ours, relative to the host's $HOME."""
    v = (repo.setting("BLENDERKC_HOST_BASE") or REMOTE_BASE).strip()
    if v.startswith(("~", "/")) or ".." in v.split("/") or not v.strip("/") or any(c.isspace() for c in v):
        raise CliError(f"BLENDERKC_HOST_BASE must be a folder under the host's home without spaces, like .cache/blenderkc, got {v!r}")
    return v.strip("/")


def ssh_prog(repo: Repo) -> str:
    """ssh, or BLENDERKC_SSH (the tests' stand-in host)."""
    return repo.setting("BLENDERKC_SSH") or "ssh"


def ssh_base(repo: Repo) -> list[str]:
    return [ssh_prog(repo), *SSH_OPTS]


def rsync_ssh(repo: Repo) -> str:
    return " ".join(shlex.quote(x) for x in ssh_base(repo))


def host_name(root: Path) -> str:
    """The snapshot's name prefix on the host: this checkout's folder name."""
    return root.resolve().name


def _excluded(rel: str, top: set[str], anchored: set[str]) -> bool:
    """The Python form of the rsync excludes: `top` names a first component
    (.git, .env), `anchored` a whole path from the walk's base (/topics)."""
    parts = rel.split("/")
    if parts[0] in top or parts[0] == ".venv" or parts[0].startswith(".venv-"):
        return True
    if any(rel == a or rel.startswith(a + "/") for a in anchored):
        return True
    name = parts[-1]
    return "__pycache__" in parts or name.endswith((".pyc", ".blend1")) or name == ".DS_Store"


_digests: dict[tuple[str, str], str] = {}


def _hash_tree(h, base: Path, prefix: str, top: set[str], anchored: set[str]) -> None:
    for d, dirs, files in os.walk(base):
        dirs.sort()
        dirs[:] = [x for x in dirs if not _excluded((Path(d) / x).relative_to(base).as_posix(), top, anchored)]
        for f in sorted(files):
            p = Path(d) / f
            rel = p.relative_to(base).as_posix()
            if _excluded(rel, top, anchored) or not p.is_file():
                continue
            h.update((prefix + rel).encode() + b"\0" + (b"x" if os.access(p, os.X_OK) else b"-"))
            h.update(hashlib.sha256(p.read_bytes()).digest())


def tree_digest(repo: Repo) -> str:
    """The hash of every file pushed: names, contents, exec bits. The checkout
    first, then the topics tree under topics/ — where it lands in the
    snapshot whether it lives inside the checkout or not."""
    key = (str(repo.root), str(repo.topics_dir))
    if key in _digests:
        return _digests[key]
    top = {".git", ".env"}
    anchored = set()
    for e in excludes(repo):
        if e.startswith("/") and "*" not in e:
            anchored.add(e[1:])
    h = hashlib.sha256()
    _hash_tree(h, repo.root.resolve(), "", top, anchored)
    _hash_tree(h, repo.topics_dir.resolve(), "topics/", {".git", ".env", ".used"}, set())
    _digests[key] = h.hexdigest()
    return _digests[key]


def remote_code(repo: Repo) -> str:
    return f"{remote_base(repo)}/src/{host_name(repo.root)}-{tree_digest(repo)[:12]}"


def remote_home(repo: Repo) -> str:
    return f"{remote_base(repo)}/home"


def excludes(repo: Repo) -> list[str]:
    """What not to copy: git, venvs, .env, caches, the data root when it sits
    inside the checkout (the default), and the topics tree — it always goes
    to <snapshot>/topics/ by push_topics_cmd, wherever it lives."""
    out = list(ALWAYS_EXCLUDE) + ["/topics"]
    if is_inside(repo.home, repo.root):
        relpath = repo.home.resolve().relative_to(repo.root.resolve()).as_posix()
        if relpath == ".":
            out += [f"/{d}" for d in DATA_DIRS]
        else:
            out.append(f"/{relpath}")
    if is_inside(repo.topics_dir, repo.root):
        relpath = repo.topics_dir.resolve().relative_to(repo.root.resolve()).as_posix()
        if relpath not in (".", "topics"):
            out.append(f"/{relpath}")
    return out


def mkdir_cmd(repo: Repo, host: str) -> list[str]:
    """The folders rsync will not make, and the snapshot's `.used` marker,
    touched before anything is copied so a prune never takes a snapshot
    that is being uploaded (rsync excludes the marker, so --delete keeps it)."""
    snap = shlex.quote(remote_code(repo))
    return [*ssh_base(repo), host, f'mkdir -p "$HOME"/{shlex.quote(remote_home(repo))} "$HOME"/{snap} && touch "$HOME"/{snap}/.used']


def prune_cmd(repo: Repo, host: str) -> list[str]:
    """Removes this checkout's snapshots whose `.used` is missing or older than
    two days, never the current one, and never the five most recently used.
    A running host job touches its snapshot's `.used` at every scene."""
    src = shlex.quote(remote_base(repo) + "/src")
    name = host_name(repo.root)
    pat = shlex.quote(name + "-") + "*"
    cur = shlex.quote(Path(remote_code(repo)).name)
    script = (f'cd "$HOME"/{src} 2>/dev/null || exit 0; '
              f'keep="$(ls -1t {pat}/.used 2>/dev/null | head -n 5 | sed "s|/.used$||")"; '
              f'for d in {pat}; do [ -d "$d" ] || continue; [ "$d" = {cur} ] && continue; '
              f'printf "%s\\n" "$keep" | grep -qxF -- "$d" && continue; '
              f'[ -n "$(find "$d/.used" -mmin -2880 2>/dev/null)" ] && continue; rm -rf -- "$d"; done; true')
    # sh, not the login shell: zsh (the homeserver's) aborts on a glob that matches nothing
    return [*ssh_base(repo), host, f"sh -c {shlex.quote(script)}"]


def push_cmd(repo: Repo, host: str) -> list[str]:
    cmd = ["rsync", "-az", "--delete", "--timeout=300"]
    for e in excludes(repo):
        cmd += ["--exclude", e]
    return [*cmd, "-e", rsync_ssh(repo), f"{repo.root}/", f"{host}:{remote_code(repo)}/"]


def push_topics_cmd(repo: Repo, host: str) -> list[str]:
    """The topics tree to <snapshot>/topics/, the host's default
    BLENDERKC_TOPICS — its own rsync, because it may sit outside the
    checkout (a private repository beside this public one)."""
    cmd = ["rsync", "-az", "--delete", "--timeout=300"]
    for e in ALWAYS_EXCLUDE:
        cmd += ["--exclude", e]
    return [*cmd, "-e", rsync_ssh(repo), f"{repo.topics_dir}/", f"{host}:{remote_code(repo)}/topics/"]


def remote_venv(repo: Repo) -> str:
    """One venv on the host per requirements.txt, shared by every snapshot."""
    return f"{remote_base(repo)}/venv-{hashlib.sha256((repo.root / 'requirements.txt').read_bytes()).hexdigest()[:12]}"


def host_cli(repo: Repo, args: str, *, code: str | None = None) -> str:
    """The shell the host runs for one of our verbs: its own PATH (uv may live
    in ~/.nix-profile), the snapshot, its own data root, then the verb.
    `code` is a shell expression for the folder to run from instead."""
    where = code or f'"$HOME"/{shlex.quote(remote_code(repo))}'
    return " && ".join([
        HOST_PATH,
        f"cd {where}",
        "touch .used",
        f'export BLENDERKC_HOME="$HOME"/{shlex.quote(remote_home(repo))}',
        "unset BLENDERKC_ROOT BLENDERKC_TOPICS BLENDERKC_PYTHON GNOSIA_AUDIO_BIN BLENDERKC_WATCH_PARENT",
        f"exec python3 tools/cli/blenderkc --root . {args}",
    ])


def start_cmd(repo: Repo, host: str) -> list[str]:
    """`host-job start` with the job on stdin; it makes the venv if missing."""
    venv = shlex.quote(remote_venv(repo))
    return [*ssh_base(repo), host, host_cli(repo, f'host-job start --job - --ensure-venv --venv "$HOME"/{venv}')]


def status_cmd(repo: Repo, host: str, topic: str) -> list[str]:
    """`host-job status`, run from a copy of the code that has it: the one
    the job itself runs from (its `code` file), else this checkout's, else
    the most recently used copy on the host that has hostjob.py. So
    `build-status` works without copying anything first, and long after
    the job's own copy was pruned. No job answers `none`; no copy at all,
    `unreadable`."""
    base = shlex.quote(remote_base(repo))
    d = f'"$HOME"/{shlex.quote(remote_home(repo))}/cache/hostjob/{shlex.quote(topic)}'
    none = shlex.quote(json.dumps({"topic": topic, "state": "none", "scenes": [], "done": 0, "total": 0}))
    lost = shlex.quote(json.dumps({"topic": topic, "state": "unreadable", "scenes": [], "done": 0, "total": 0,
                                   "error": "no copy of the code on the host can read the job; run the build to copy one"}))
    has = "tools/cli/blenderkc_cli/hostjob.py"
    script = (f"[ -f {d}/job.json ] || {{ printf '%s\\n' {none}; exit 0; }}; "
              f'c="$(cat {d}/code 2>/dev/null)"; [ -n "$c" ] && [ -f "$c/{has}" ] || c="$HOME"/{shlex.quote(remote_code(repo))}; '
              f'[ -f "$c/{has}" ] || c="$(ls -1t "$HOME"/{base}/src/*/.used 2>/dev/null | while IFS= read -r u; do '
              f'[ -f "${{u%/.used}}/{has}" ] && {{ printf "%s\\n" "${{u%/.used}}"; break; }}; done)"; '
              f"[ -n \"$c\" ] || {{ printf '%s\\n' {lost}; exit 0; }}; "
              f"{host_cli(repo, f'host-job status --topic {shlex.quote(topic)}', code=chr(34) + '$c' + chr(34))}")
    # sh, not the login shell: zsh (the homeserver's) aborts on a glob that matches nothing
    return [*ssh_base(repo), host, f"sh -c {shlex.quote(script)}"]


def pull_cmd(repo: Repo, host: str, topic: str, files: list[str]) -> list[str]:
    """Only the named plates of one topic, into this data root's plates/."""
    cmd = ["rsync", "-a", "--timeout=300"]
    for f in files:
        cmd += ["--include", f]
    return [*cmd, "--exclude", "*", "-e", rsync_ssh(repo), f"{host}:{remote_home(repo)}/plates/{topic}/",
            f"{repo.plates / topic}/"]


def tailscale_url(text: str) -> str | None:
    """The login URL a Tailscale SSH check prints, if the text holds one."""
    m = TAILSCALE_URL.search(text or "")
    return m.group(0) if m else None


def _clock_at(seconds_from_now: float) -> str:
    return (datetime.now() + timedelta(seconds=seconds_from_now)).strftime("%a %H:%M")


class Link:
    """ssh and rsync to one host, patient with the connection.

    A call whose exit says the connection failed (in `transient`) is tried
    again after 15 s, then 30, 60 and so on up to 5 minutes, until it works
    or the connection has been failing for `patience_s` (24 h by default;
    0 means one try). Any other exit is the command's own answer and fails
    at once. A Tailscale check URL in the output is shown once per outage,
    then again with its newest URL every half hour while it waits."""

    def __init__(self, host: str, *, patience_s: float = RETRY_HOURS * 3600, first_wait: float = FIRST_WAIT_S,
                 max_wait: float = MAX_WAIT_S, say=log, sleep=time.sleep, clock=time.monotonic):
        self.host, self.patience_s, self.first_wait, self.max_wait = host, patience_s, first_wait, max_wait
        self.say, self.sleep, self.clock = say, sleep, clock
        self.down_since: float | None = None
        self.url_shown_at: float | None = None
        self.retries = 0

    def tailscale(self, url: str) -> None:
        now = self.clock()
        if self.url_shown_at is None:
            self.say(f"the host needs a Tailscale login: {url}")
            self.say("  open it in a browser signed in to the tailnet; the build waits and goes on by itself")
            self.url_shown_at = now
        elif now - self.url_shown_at >= REMIND_S:
            self.say(f"still waiting for the Tailscale login on {self.host}: {url}")
            self.url_shown_at = now

    def once(self, cmd: list[str], *, input: str | None = None, timeout: float | None = None,
             echo: bool = True) -> tuple[int, str, str]:
        """One try: (exit code, stdout, stderr). stderr is streamed here as it
        comes (Tailscale's own banner replaced by our line), so a check URL
        shows while ssh still waits on it."""
        # a group of its own, so a timeout or Ctrl-C stops rsync's ssh child too, and no reader is left waiting on it
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)

        def stop():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait()
        out: list[str] = []
        err: list[str] = []

        def read_err():
            for line in proc.stderr:
                err.append(line)
                url = tailscale_url(line)
                if url:
                    self.tailscale(url)
                elif echo and not line.startswith(TAILSCALE_NOISE):
                    sys.stderr.write(line)
                    sys.stderr.flush()

        def read_out():
            out.append(proc.stdout.read())

        def feed():
            try:
                proc.stdin.write(input)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        threads = [threading.Thread(target=read_err, daemon=True), threading.Thread(target=read_out, daemon=True)]
        if input is not None:
            threads.append(threading.Thread(target=feed, daemon=True))
        for t in threads:
            t.start()
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop()
            code = TIMED_OUT
        except BaseException:
            stop()
            raise
        for t in threads:
            t.join(5)
        if not any(t.is_alive() for t in threads):  # a reader still blocked holds its stream's lock: leave it
            for f in (proc.stdout, proc.stderr):
                try:
                    f.close()
                except OSError:
                    pass
        return code, "".join(out), "".join(err)

    def call(self, cmd: list[str], what: str, *, input: str | None = None, timeout: float | None = None,
             transient=SSH_TRANSIENT, echo: bool = True) -> str:
        """Runs `cmd` until it works; returns its stdout."""
        wait = self.first_wait
        while True:
            code, out, err = self.once(cmd, input=input, timeout=timeout, echo=echo)
            if code == 0:
                if self.down_since is not None:
                    self.say(f"{self.host} answers again")
                self.down_since = self.url_shown_at = None
                return out
            last = next((l.strip() for l in reversed(err.splitlines()) if l.strip() and not l.startswith(TAILSCALE_NOISE)), "")
            reason = (f"no answer in {timeout:.0f}s" if code == TIMED_OUT else f"exit {code}") + (f": {last}" if last else "")
            if code not in transient:
                raise CliError(f"{what} failed ({reason})")
            now = self.clock()
            if self.down_since is None:
                self.down_since = now
            left = self.down_since + self.patience_s - now
            if left <= 0:
                hours = self.patience_s / 3600
                raise CliError(f"{what} failed ({reason}); {self.host} has not answered for "
                               f"{hours:g} h, so this build stops. The host job, if one started, keeps running: "
                               f"run the same build again to pick it up")
            pause = min(wait, self.max_wait, left)
            self.retries += 1
            self.say(f"{what}: {reason}; trying again in {pause:.0f}s (giving up {_clock_at(left)})")
            self.sleep(pause)
            wait = min(wait * 2, self.max_wait)


def _settings(repo: Repo, retry_hours: float | None) -> tuple[float, float, float]:
    """(patience s, poll s, first wait s), checked before anything reaches the host."""
    def num(key: str, default: float) -> float:
        v = repo.setting(key)
        try:
            return float(v) if v else default
        except ValueError:
            raise CliError(f"{key} must be a number, got {v!r}")
    hours = retry_hours if retry_hours is not None else num("BLENDERKC_HOST_RETRY_HOURS", RETRY_HOURS)
    if not math.isfinite(hours) or not 0 <= hours <= 24 * 365:
        raise CliError(f"--retry-hours (or BLENDERKC_HOST_RETRY_HOURS) must be from 0 to {24 * 365}, got {hours}")
    poll, first = num("BLENDERKC_POLL_S", POLL_S), num("BLENDERKC_RETRY_FIRST_S", FIRST_WAIT_S)
    for key, v in (("BLENDERKC_POLL_S", poll), ("BLENDERKC_RETRY_FIRST_S", first)):
        if not math.isfinite(v) or not 0 < v <= 86400:
            raise CliError(f"{key} must be a number of seconds above 0 and at most a day, got {v}")
    return hours * 3600, poll, first


def link_for(repo: Repo, host: str, retry_hours: float | None = None) -> Link:
    patience, _, first = _settings(repo, retry_hours)
    return Link(host, patience_s=patience, first_wait=first)


def fetch_status(link: Link, repo: Repo, host: str, topic: str) -> dict:
    out = link.call(status_cmd(repo, host, topic), f"asking {host} how the job of {topic} is going", timeout=600)
    try:
        return json.loads(out)
    except ValueError:
        raise CliError(f"the host job status from {host} is not JSON:\n{out[-1500:]}")


def start_job(link: Link, repo: Repo, host: str, job: dict) -> dict:
    out = link.call(start_cmd(repo, host), f"starting the render job on {host}", input=json.dumps(job), timeout=3600)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise CliError(f"the host job on {host} answered no JSON:\n{out[-1500:]}")


def plate_short(row: dict) -> str | None:
    """The 16-character key in a status row's plate name, <Scene>.<key16>.mp4."""
    parts = Path(row.get("plate") or "").name.split(".")
    return parts[1] if len(parts) == 3 else None


def human_seconds(s: float | None) -> str:
    if s is None:
        return "?"
    s = int(round(s))
    if s < 90:
        return f"{s} s"
    h, m = divmod((s + 30) // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def progress_line(st: dict, host: str) -> str:
    cur = st.get("current")
    bits = [f"{host}: {st.get('done', 0)} of {st.get('total', 0)} plates"]
    if cur:
        bits.append(f"{cur['scene']} encoding its plate" if cur["frame"] >= cur["frames"] else
                    f"{cur['scene']} frame {cur['frame']} of {cur['frames']}")
    if st.get("secondsPerFrame"):
        bits.append(f"{st['secondsPerFrame']} s a frame")
    if st.get("etaSeconds") is not None:
        bits.append(f"about {human_seconds(st['etaSeconds'])} left")
    return "; ".join(bits)


def render_remote(repo: Repo, host: str, job: dict, *, retry_hours: float | None = None) -> dict:
    """Steps 1 to 4 above. Returns {topic, plates: [{scene, plate, frames,
    cached, seconds}], seconds} as runner.run_job does."""
    patience, poll_s, first = _settings(repo, retry_hours)
    link = Link(host, patience_s=patience, first_wait=first)
    topic = job["topic"]
    started = time.monotonic()
    log(f"copying the checkout to {host}:~/{remote_code(repo)}/")
    link.call(mkdir_cmd(repo, host), f"making the folders on {host}", timeout=600)
    link.call(push_cmd(repo, host), f"copying the checkout to {host}", transient=RSYNC_TRANSIENT, timeout=3600)
    if not repo.topics_dir.is_dir():
        raise CliError(f"no topics tree at {repo.topics_dir} ({repo.topics_source}); "
                       f"set {TOPICS_ENV} in the environment or the checkout's .env")
    link.call(push_topics_cmd(repo, host), f"copying the topics to {host}", transient=RSYNC_TRANSIENT, timeout=3600)
    try:  # housekeeping, one try: a host that does not answer now is no reason to stop
        subprocess.run(prune_cmd(repo, host), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=120)
    except subprocess.TimeoutExpired:
        pass
    (repo.plates / topic).mkdir(parents=True, exist_ok=True)

    def begin(why: str) -> None:
        r = start_job(link, repo, host, job)
        verb = "attached to the running" if r.get("state") == "attached" else ("resumed the" if r.get("resumed") else "started a")
        log(f"{why}{verb} host job of {topic} on {host} (pid {r.get('pid')}, {r.get('scenes')} scenes); "
            f"polling every {poll_s:g}s. If this process stops, the host keeps rendering: run the same build again to pick it up")

    begin("")
    want = [s["scene"] for s in job["scenes"]]
    short = {s["scene"]: s["plate"][:16] for s in job["scenes"]}
    got: dict[str, dict] = {}
    last_sig, stalls, said, said_at = None, 0, None, 0.0
    try:
        while True:
            st = fetch_status(link, repo, host, topic)
            rows = {r["scene"]: r for r in st.get("scenes", []) if plate_short(r) == short.get(r["scene"])}
            for name in want:
                r = rows.get(name)
                if name in got or not r or not r.get("done"):
                    continue
                plate = Path(r["plate"]).name
                local = repo.home / r["plate"]
                if not local.is_file():
                    log(f"copying {plate} back from {host}")
                    link.call(pull_cmd(repo, host, topic, [plate]), f"copying {plate} back",
                              transient=RSYNC_TRANSIENT, timeout=4 * 3600)
                    if not local.is_file():
                        raise CliError(f"{plate} did not arrive from {host}")
                got[name] = {"scene": name, "plate": r["plate"], "frames": r["frames"], "cached": bool(r.get("cached")),
                             "seconds": r.get("seconds") or 0}
            if len(got) == len(want):
                break
            now = time.monotonic()
            what = (st.get("done"), (st.get("current") or {}).get("scene"), st.get("state"))
            if what != said or now - said_at >= REPORT_S:
                log(progress_line(st, host))
                said, said_at = what, now
            state = st.get("state")
            if not (state in ("starting", "running") and all(n in rows for n in want)):
                # gone, failed, stopped, or the topic's job is not this build's: start ours, which resumes
                sig = (st.get("done"), sum(r.get("rendered", 0) for r in st.get("scenes", [])))
                stalls = stalls + 1 if sig == last_sig else 0
                last_sig = sig
                if stalls >= MAX_RESTARTS:
                    tail = "\n".join(st.get("logTail") or [])
                    raise CliError(f"the host job of {topic} on {host} stopped {stalls + 1} times without a new frame"
                                   f"{': ' + st['error'] if st.get('error') else ''}\nits log ends:\n{tail}")
                why = {"none": "no host job found; ", "died": "the host job died; ", "stopped": "the host job was stopped; ",
                       "unreadable": "the host job could not be read; ",
                       "failed": f"the host job failed ({st.get('error')}); "}.get(state, "the host job held other scenes; ")
                begin(why)
            time.sleep(poll_s)
    except KeyboardInterrupt:
        log(f"stopped watching {host}. The host job of {topic} keeps rendering: run the same build again to pick it up, "
            f"or `blenderkc build-status {topic} --host {host}` to look")
        raise
    if link.retries:
        log(f"{host}: the connection failed {link.retries} times along the way and was retried each time")
    return {"topic": topic, "plates": [got[n] for n in want], "seconds": round(time.monotonic() - started, 1)}


def probe_host(repo: Repo, host: str) -> dict:
    """What doctor --host reports: reachability and the host's tools."""
    script = " ; ".join([
        HOST_PATH,
        "for c in python3 uv ffmpeg rsync systemd-run nvidia-smi; do printf '%s=%s\\n' $c \"$(command -v $c)\"; done",
        "printf 'python3v=%s\\n' \"$(python3 -c 'import sys; print(sys.version.split()[0])' 2>/dev/null)\"",
        "printf 'gpu=%s\\n' \"$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)\"",
        "printf 'scope=%s\\n' \"$(systemd-run --user --scope -q -p MemoryMax=3G true >/dev/null 2>&1 && echo ok)\"",
        "printf 'linger=%s\\n' \"$(loginctl show-user \"$USER\" -p Linger --value 2>/dev/null)\"",
        f"printf 'free=%s\\n' \"$(df -h \"$HOME\" | awk 'NR==2{{print $4}}')\"",
    ])
    try:
        r = subprocess.run([*ssh_base(repo), host, script], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {"reachable": False, "error": "timed out"}
    if r.returncode != 0 and not r.stdout:
        url = tailscale_url(r.stderr)
        return {"reachable": False, "error": f"the host needs a Tailscale login: {url}" if url else
                (r.stderr.strip() or f"ssh exit {r.returncode}")[-300:]}
    vals = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    return {"reachable": True, **vals}
