"""`blenderkc doctor [--host home]`: everything a render needs, checked
before anything is spent, each failure with the command that fixes it.
Standard library only, so it runs with a missing or broken .venv and says so."""

from __future__ import annotations

import json
import sys

from . import remote
from .audio import (INSTALL, NARRATION_PIN, gnosia_bin, installed_dist, manimkc_config, pinned_gnosia_audio,
                    runnable, try_gnosia)
from .keys import bpy_pin
from .repo import Repo, is_inside, which
from .topics import topic_paths

LIB_FILES = ("blenderkc/__init__.py", "blenderkc/shot.py", "blenderkc/render.py", "blenderkc/overlay.py")


def narration_checks(repo: Repo, add) -> None:
    pin = pinned_gnosia_audio(repo.root)
    binary, source = gnosia_bin(repo)
    override = source == "GNOSIA_AUDIO_BIN"
    ok_bin = runnable(binary)
    if override:
        add("gnosia-audio", "ok" if ok_bin else "fail", f"{binary} (GNOSIA_AUDIO_BIN{'; the pin is not checked' if ok_bin else ' is not an executable file'})",
            None if ok_bin else "point GNOSIA_AUDIO_BIN at a gnosia-audio command, or unset it")
    else:
        got = installed_dist(repo.root, "gnosia-audio")
        if not got:
            add("gnosia-audio", "fail", f"not installed in {repo.venv}", INSTALL)
        elif not ok_bin:
            add("gnosia-audio", "fail", f"{got['version']} is in .venv, but {binary} is missing", INSTALL)
        else:
            pinned = not got["editable"] and pin and got["commit"] == pin
            how = f"editable, {got['url']}" if got["editable"] else (f"commit {got['commit'][:12]}" if got["commit"] else "from a directory")
            add("gnosia-audio", "ok" if pinned else "warn",
                f"{got['version']} ({how}){', the commit ' + NARRATION_PIN + ' pins' if pinned else '; ' + NARRATION_PIN + ' pins ' + (pin[:12] if pin else 'nothing')}",
                None if pinned else INSTALL)
    try:
        mk = manimkc_config(repo)
    except Exception as e:  # CliError: MANIMKC_ROOT names nothing
        add("manimkc", "fail", str(e), "set MANIMKC_ROOT=<manimkc checkout> in .env, or unset it")
        return
    add("manimkc", "ok" if mk["root"] else "warn",
        f"{mk['root']} ({mk['source']}): gnosia-audio reads its voice and clip cache from there" if mk["root"] else "none: gnosia-audio uses its own environment",
        None if mk["root"] else "set MANIMKC_ROOT=<manimkc checkout> in .env to share the series voice and clip cache")
    if not ok_bin:
        return
    res = try_gnosia(repo, "doctor")
    if "error" in res:
        add("narration", "fail", res["error"], INSTALL)
        return
    reply = res["reply"]
    failed = [c for c in reply.get("checks", []) if not c.get("ok")]
    for c in failed:
        add(f"narration: {c['name']}", "warn" if c.get("warn") else "fail", c.get("detail", ""), c.get("fix"))
    add("narration", "ok" if reply.get("ok") else "fail",
        f"gnosia-audio doctor: {len(reply.get('checks', [])) - len(failed)}/{len(reply.get('checks', []))} checks pass", None)
    kokoro = reply.get("backend") == "kokoro"
    add("narration backend", "ok" if kokoro else "warn",
        f"{reply.get('backend')}{': exact word times' if kokoro else ': not the series voice, and word times are estimates'}",
        None if kokoro else "set MANIMKC_ROOT to the manimkc checkout (its data root's .env selects kokoro)")


def host_checks(repo, host: str, add) -> None:
    h = remote.probe_host(repo, host)
    if not h.get("reachable"):
        add(f"host {host}", "fail", f"ssh {host} failed: {h.get('error')}", f"check `ssh {host} true` works without a password prompt")
        return
    add(f"host {host}", "ok", f"reachable; {h.get('free') or '?'} free in ~; copies under ~/{remote.remote_base(repo)}/")
    for tool, fix in (("python3", "install python3 on the host"), ("uv", "install uv on the host"),
                      ("ffmpeg", "install ffmpeg on the host"), ("rsync", "install rsync on the host")):
        p = h.get(tool)
        add(f"host {tool}", "ok" if p else "fail", (p + (f" ({h.get('python3v')})" if tool == "python3" else "")) if p else "not found", None if p else fix)
    add("host gpu", "ok" if h.get("gpu") else "warn", h.get("gpu") or "no NVIDIA GPU found (renders on the CPU, about 10x slower)", None)
    add("host memory cap", "ok" if h.get("scope") == "ok" else "warn",
        "systemd-run --user --scope -p MemoryMax=3G works" if h.get("scope") == "ok" else "no systemd user scope: renders run without a memory cap", None)
    if h.get("linger") or h.get("systemd-run"):
        add("host linger", "ok" if h.get("linger") == "yes" else "warn",
            "the user's systemd manager outlives ssh logins, so a detached job's memory scope does too" if h.get("linger") == "yes"
            else f"Linger={h.get('linger') or '?'}: when the last ssh login ends, the scope a detached render runs in may be stopped",
            None if h.get("linger") == "yes" else "on the host: loginctl enable-linger \"$USER\"")


def checks(repo: Repo, host: str | None = None) -> list[dict]:
    out: list[dict] = []

    def add(name, status, detail, fix=None):
        out.append({"name": name, "status": status, "detail": detail, **({"fix": fix} if fix else {})})

    v = sys.version_info
    add("python3", "ok" if v >= (3, 10) else "fail", f"{sys.executable} {v.major}.{v.minor}.{v.micro}", None if v >= (3, 10) else "Python 3.10 or newer for the CLI")
    add("checkout", "ok", f"{repo.root} ({len(topic_paths(repo))} topics)")
    if repo.topics_dir.is_dir():
        add("topics", "ok", f"{repo.topics_dir} ({repo.topics_source})")
    elif repo.topics_source.startswith("default"):
        add("topics", "warn", f"no {repo.topics_dir}; the checkout has no topics",
            "set BLENDERKC_TOPICS=<topics checkout> in .env for the private topics repo")
    else:
        add("topics", "fail", f"{repo.topics_dir} ({repo.topics_source}) is not a folder",
            "set BLENDERKC_TOPICS to an existing topics folder, or unset it")
    inside = is_inside(repo.home, repo.root)
    add("data root", "warn" if inside else "ok",
        f"{repo.home} (from {repo.home_source}){'; inside the checkout, so a relayout or a deleted clone takes the renders with it' if inside else ''}",
        "echo 'BLENDERKC_HOME=~/.self-learn/blenderkc' >> .env" if inside else None)
    missing = [f for f in LIB_FILES if not (repo.root / f).is_file()]
    add("library", "fail" if missing else "ok", f"missing {', '.join(missing)}" if missing else "blenderkc/ (shot, render, overlay)",
        "the library is not in this checkout yet" if missing else None)
    python, src = repo.python()
    import os
    has_py = os.path.isfile(python)
    bpy = installed_dist(repo.root, "bpy")
    pin = bpy_pin(repo.root)
    if src == "BLENDERKC_PYTHON":
        add("venv", "ok" if has_py else "fail", f"{python} (BLENDERKC_PYTHON)", None if has_py else "point BLENDERKC_PYTHON at a Python with bpy")
    elif not has_py:
        add("venv", "fail", f"no {python}", "make deps")
    else:
        ok = bpy and bpy["version"] == pin
        add("venv", "ok" if ok else "fail", f"{bpy['python'] if bpy else 'python?'}, bpy {bpy['version'] if bpy else 'missing'} (requirements.txt pins {pin})",
            None if ok else "make deps")
        pil = installed_dist(repo.root, "pillow")
        add("pillow", "ok" if pil else "fail", f"Pillow {pil['version']}" if pil else "missing from .venv", None if pil else "make deps")
    for tool in ("ffmpeg", "ffprobe"):
        p = which(tool)
        add(tool, "ok" if p else "fail", p or "not on PATH", None if p else "brew install ffmpeg")
    for tool in ("rsync", "ssh"):
        p = which(tool)
        add(tool, "ok" if p else "warn", p or "not on PATH (only build --host needs it)", None)
    narration_checks(repo, add)
    if host:
        host_checks(repo, host, add)
    return out


def main(args, repo: Repo) -> int:
    lst = checks(repo, args.host)
    ok = not any(c["status"] == "fail" for c in lst)
    if args.json:
        print(json.dumps({"ok": ok, "checks": lst}, indent=1))
        return 0 if ok else 1
    wide = max(len(c["name"]) for c in lst)
    L = []
    for c in lst:
        L.append(f"{'ok  ' if c['status'] == 'ok' else 'WARN' if c['status'] == 'warn' else 'FAIL'}  {c['name'].ljust(wide)}  {c['detail']}")
        if c.get("fix"):
            L.append(f"      {''.ljust(wide)}  fix: {c['fix']}")
    L += ["", "ready to render" if ok else "not ready: fix every FAIL above first"]
    print("\n".join(L))
    return 0 if ok else 1
