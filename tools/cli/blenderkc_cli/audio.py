"""gnosia-audio, the narration layer: where it is installed, what commit it
is, and how to call its JSON command (schema 1, its docs/CLI.md). A port of
revideokc's audio.mjs, so the tools narrate alike:

    requirements-narration.txt   pins one merged gnosia-audio commit, the same
                                 one revideokc pins
    make narration-deps          installs it into <checkout>/.venv, the bpy
                                 venv (Python 3.13; measured 2026-10-09: it
                                 installs there with audioop-lts and voices)
    .venv/bin/gnosia-audio       what narrate, build, doctor and home run;
                                 GNOSIA_AUDIO_BIN overrides it

Configuration (backend, voice, clip cache) is gnosia-audio's business.
blenderkc names the manimkc checkout to read it from with --manimkc-root when
there is one, so a line any of the four tools voiced is never voiced again.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .repo import CliError, Repo, expand_home

NARRATION_PIN = "requirements-narration.txt"
SCHEMA = 1
INSTALL = "make narration-deps"


def pinned_gnosia_audio(root: Path) -> str | None:
    try:
        text = (root / NARRATION_PIN).read_text()
    except OSError:
        return None
    for raw in text.splitlines():
        line = re.sub(r"#.*", "", raw).strip()
        m = re.match(r"^gnosia-audio\s*@\s*git\+\S+?@([0-9a-f]{40})$", line)
        if m:
            return m.group(1)
    return None


def installed_dist(root: Path, name: str) -> dict | None:
    """What of a package is installed in the checkout's .venv, read from its
    dist-info rather than by running Python, so doctor can answer with a broken
    venv: {version, commit, editable, url}, or None."""
    prefix = name.replace("-", "_") + "-"
    for sp in sorted((root / ".venv" / "lib").glob("python*/site-packages")):
        for info in sorted(sp.glob(f"{prefix}*.dist-info")):
            try:
                direct = json.loads((info / "direct_url.json").read_text())
            except (OSError, ValueError):
                direct = {}
            return {
                "version": info.name[len(prefix):-len(".dist-info")],
                "commit": (direct.get("vcs_info") or {}).get("commit_id"),
                "editable": bool((direct.get("dir_info") or {}).get("editable")),
                "url": direct.get("url"),
                "python": sp.parent.name,
            }
    return None


def gnosia_bin(repo: Repo) -> tuple[str, str]:
    v = repo.setting("GNOSIA_AUDIO_BIN")
    if v:
        return str(Path(expand_home(v.strip())).resolve()), "GNOSIA_AUDIO_BIN"
    return str(repo.venv / "bin" / "gnosia-audio"), f"{NARRATION_PIN} (.venv)"


def manimkc_config(repo: Repo) -> dict:
    """{root, source, args}: MANIMKC_ROOT when set (it must exist), else the
    sibling ../manimkc when there is one, else none."""
    root, source = repo.manimkc_root()
    if source == "MANIMKC_ROOT" and not root.is_dir():
        raise CliError(f"MANIMKC_ROOT is {root}, which does not exist; fix it in .env, or unset it to narrate with gnosia-audio's own settings")
    if not root.is_dir():
        return {"root": None, "source": "none (gnosia-audio's own environment)", "args": []}
    return {"root": str(root), "source": source, "args": ["--manimkc-root", str(root)]}


def call_gnosia(repo: Repo, command: str, request=None, *, quiet: bool = False, config: bool = True) -> dict:
    """Runs `gnosia-audio <command> --json` with `request` on stdin and
    returns the reply. Throws a CliError naming the fix when the command is
    missing, fails, or speaks another schema."""
    binary, source = gnosia_bin(repo)
    args = [binary, command, "--json", *(manimkc_config(repo)["args"] if config else [])]
    try:
        r = subprocess.run(args, input="" if request is None else json.dumps(request), text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE if quiet else None)
    except FileNotFoundError:
        raise CliError(f"gnosia-audio is not installed at {binary} ({source}): {INSTALL}. `blenderkc doctor` checks this.")
    except OSError as e:
        raise CliError(f"gnosia-audio {command}: {e}")
    tail = f"\n{r.stderr[-1500:]}" if quiet and r.stderr else ""
    try:
        reply = json.loads(r.stdout)
    except ValueError:
        raise CliError(f"gnosia-audio {command} exited {r.returncode} without a JSON reply{tail or ' (see above)'}")
    if not isinstance(reply, dict) or reply.get("schema") != SCHEMA:
        got = reply.get("schema") if isinstance(reply, dict) else None
        raise CliError(f"gnosia-audio at {binary} speaks schema {got}; blenderkc reads schema {SCHEMA}. Move {NARRATION_PIN} to a commit that does, or run {INSTALL}.")
    failed_checks = command == "doctor" and r.returncode == 1 and not reply.get("error")
    if (r.returncode != 0 and not failed_checks) or reply.get("error"):
        e = reply.get("error") or {}
        raise CliError(f"gnosia-audio {command} failed ({e.get('kind') or f'exit {r.returncode}'}): {e.get('message') or 'no message'}{tail}")
    return reply


def try_gnosia(repo: Repo, command: str, request=None, **kw) -> dict:
    try:
        return {"reply": call_gnosia(repo, command, request, quiet=True, **kw)}
    except CliError as e:
        return {"error": str(e)}


def runnable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)
