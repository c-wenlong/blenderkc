"""Where the checkout is, where its data goes, and the small helpers every
verb shares. Two roots, the same two manimkc and revideokc have, plus the
topics tree:

    root    the checkout: blenderkc/, tools/. From --root, then
            $BLENDERKC_ROOT, then by walking up from the working directory,
            then from this file's own location.
    home    the data root, $BLENDERKC_HOME: out/, public/, stills/, frames/,
            plates/, cache/, state/. The process environment, then a
            BLENDERKC_HOME= line in the CHECKOUT's .env, then the checkout.
    topics  the topic tree, $BLENDERKC_TOPICS: the <domain>/.../<slug>
            folders every verb reads and `new` writes. The process
            environment, then a BLENDERKC_TOPICS= line in the CHECKOUT's
            .env, then <checkout>/topics. Configurable so this repository
            can be public while the course videos live in a private one.

The home is read from the checkout's .env and never from the data root's own,
because that file's location is the answer being computed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

HOME_ENV = "BLENDERKC_HOME"
ROOT_ENV = "BLENDERKC_ROOT"
TOPICS_ENV = "BLENDERKC_TOPICS"
MARKER = Path("tools") / "cli" / "blenderkc"
FPS = 30


class CliError(Exception):
    """A refusal or failure with a message for a person; exit status 2."""


def is_checkout(d: Path) -> bool:
    return (d / MARKER).is_file()


def _walk_up(start: Path) -> Path | None:
    p = start.resolve()
    for c in (p, *p.parents):
        if is_checkout(c):
            return c
    return None


def expand_home(p: str) -> str:
    return os.path.expanduser(p) if p.startswith("~") else p


def find_root(explicit: str | None = None) -> Path:
    if explicit:
        root = Path(expand_home(explicit)).resolve()
        if not is_checkout(root):
            raise CliError(f"--root {root} is not a blenderkc checkout (no {MARKER})")
        return root
    env = os.environ.get(ROOT_ENV)
    if env:
        return find_root(env)
    found = _walk_up(Path.cwd()) or _walk_up(Path(__file__))
    if not found:
        raise CliError("cannot find the blenderkc checkout: run from inside it, set BLENDERKC_ROOT, or pass --root")
    return found


def parse_env(path: Path) -> dict[str, str]:
    """KEY=VALUE lines: comments, blank lines and optional quotes, nothing more."""
    out: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        if k.strip():
            out[k.strip()] = v
    return out


def _setting_value(root: Path, env: str) -> tuple[str, str]:
    """The env or checkout-.env value of `env`, and who said so."""
    value = os.environ.get(env, "").strip()
    if value:
        return value, "the environment"
    return parse_env(root / ".env").get(env, "").strip(), str(root / ".env")


def _absolute(value: str, env: str, source: str) -> Path:
    p = Path(expand_home(value))
    if not p.is_absolute():
        raise CliError(f"{env} must be an absolute path, got {value!r} (from {source})")
    return p.resolve()


def find_home(root: Path) -> tuple[Path, str]:
    """The data root and who said so. Refuses a relative path."""
    value, source = _setting_value(root, HOME_ENV)
    if not value:
        return root, "default (the checkout)"
    return _absolute(value, HOME_ENV, source), source


def find_topics(root: Path) -> tuple[Path, str]:
    """The topics tree and who said so. Refuses a relative path."""
    value, source = _setting_value(root, TOPICS_ENV)
    if not value:
        return root / "topics", "default (the checkout's topics/)"
    return _absolute(value, TOPICS_ENV, source), source


def is_inside(child: Path, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


class Repo:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.home, self.home_source = find_home(self.root)
        self.topics_dir, self.topics_source = find_topics(self.root)
    lib_dir = property(lambda self: self.root / "blenderkc")
    out = property(lambda self: self.home / "out")
    public = property(lambda self: self.home / "public")
    stills = property(lambda self: self.home / "stills")
    frames = property(lambda self: self.home / "frames")
    plates = property(lambda self: self.home / "plates")
    cache = property(lambda self: self.home / "cache")
    state = property(lambda self: self.home / "state")

    def setting(self, key: str) -> str | None:
        """The environment, then the data root's .env, then the checkout's."""
        if os.environ.get(key):
            return os.environ[key]
        for f in (self.home / ".env", self.root / ".env"):
            v = parse_env(f).get(key)
            if v:
                return v
        return None

    @property
    def venv(self) -> Path:
        return self.root / ".venv"

    def python(self) -> tuple[str, str]:
        """The interpreter that runs blenderkc.render and blenderkc.overlay:
        BLENDERKC_PYTHON (tests point it at a stand-in), else .venv's."""
        v = self.setting("BLENDERKC_PYTHON")
        if v:
            return str(Path(expand_home(v))), "BLENDERKC_PYTHON"
        return str(self.venv / "bin" / "python"), ".venv"

    def manimkc_root(self) -> tuple[Path, str]:
        """The manimkc checkout gnosia-audio takes its voice and clip cache
        from: $MANIMKC_ROOT (environment, then .env), else a sibling
        ../manimkc, which is where the video tools sit in self-learn."""
        v = self.setting("MANIMKC_ROOT")
        if v:
            return Path(expand_home(v)).resolve(), "MANIMKC_ROOT"
        return (self.root / ".." / "manimkc").resolve(), "default (sibling ../manimkc)"

    def out_dir(self, topic: str) -> Path:
        return self.out / topic

    def public_dir(self, topic: str) -> Path:
        return self.public / topic

    def stills_dir(self, topic: str) -> Path:
        return self.stills / topic


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def probe_duration(f: Path) -> float | None:
    """Seconds of a media file to two places, or None when ffprobe cannot say."""
    if not which("ffprobe"):
        return None
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                       capture_output=True, text=True, timeout=60)
    try:
        return round(float(r.stdout.strip()), 2)
    except ValueError:
        return None


def read_json(f: Path, fallback=...):
    try:
        return json.loads(Path(f).read_text())
    except (OSError, ValueError):
        if fallback is not ...:
            return fallback
        raise


def write_json(f: Path, value) -> None:
    f = Path(f)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=1) + "\n")
    os.replace(tmp, f)


def rel(base: Path, p: Path) -> str:
    """A path as the reports print it: relative to `base`, with "/"."""
    return Path(os.path.relpath(p, base)).as_posix()


def sha256_file(f: Path) -> str:
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
