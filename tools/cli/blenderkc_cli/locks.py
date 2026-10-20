"""One build (or one render job) per topic at a time: an exclusive lock file
holding the holder's pid. A lock whose process is gone is stale and is taken
over; one with no pid yet, made in the last ten seconds, is a holder starting."""

from __future__ import annotations

import os
import time
from pathlib import Path

from .repo import CliError

STARTING_S = 10


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _pid(f: Path) -> int | None:
    try:
        t = f.read_text().strip()
        return int(t) if t.isdigit() else None
    except OSError:
        return None


def acquire(f: Path, what: str = "build"):
    """Takes the lock; returns the release function."""
    f.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(4):
        try:
            fd = os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)

            def release():
                if _pid(f) == os.getpid():
                    f.unlink(missing_ok=True)
            return release
        except FileExistsError:
            pid = _pid(f)
            if pid and _alive(pid):
                raise CliError(f"another {what} of this topic is running (pid {pid}); wait for it, or delete {f} if that process is not one")
            if pid is None:
                try:
                    if abs(time.time() - f.stat().st_mtime) < STARTING_S:
                        raise CliError(f"another {what} of this topic is starting; wait for it, or delete {f}")
                except FileNotFoundError:
                    continue
            # stale: take it over only if it still names what we saw
            if _pid(f) == pid:
                f.unlink(missing_ok=True)
    raise CliError(f"could not take the lock {f}")
