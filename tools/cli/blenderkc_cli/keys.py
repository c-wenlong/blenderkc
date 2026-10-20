"""The render cache keys: what decides that a scene must be rendered again.

Two keys per scene:

    plate key  the 3D picture: the shot script with its overlay calls and
               comments taken out, every file under blenderkc/ but
               overlay.py and fonts/, the topic's own helper files, the
               scene's timing file, the render settings that change pixels
               (res, samples, fps), the bpy pin in requirements.txt, and
               CACHE_VERSION. Its frames and plate video are filed under it.
    clip key   the finished scene: the plate key plus the shot script as
               written, blenderkc/overlay.py and blenderkc/fonts/. A label,
               title, equation or glow edit changes this key only, so it
               re-composites the overlays on the plate and renders no 3D.

The device is not in either key: a scene rendered on the homeserver (OptiX)
satisfies a build on the Mac (Metal), measured visually the same.

Comment-only lines and blank lines are left out of every Python file hashed,
so tidying a comment never costs a render. An overlay call is left out of the
plate key only as a whole statement of its own on build's `shot` parameter,
`shot.label(...)` (or title, equation, glow); anything else stays in, which
can only cause an extra render, never a stale one. `hero.glow(shot, ...)`, a
kit part animating its emission, stays in: only the receiver decides. The
library guarantees the shot's overlay calls append records and never touch
bpy.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import tokenize
from pathlib import Path

CACHE_VERSION = 1
# media.PLATE_ARGS, repeated here so keys.py imports nothing: a plate encoded
# another way is another plate (tests/test_units.py checks they agree)
PLATE_ENCODE = "libx264 4 medium crf14 yuv444p bt709"
OVERLAY_CALLS = frozenset({"label", "title", "equation", "glow"})
OVERLAY_FILES = ("blenderkc/overlay.py",)
OVERLAY_DIRS = ("blenderkc/fonts/",)
SKIP_DIRS = {"__pycache__", ".git", ".venv"}


def _overlay_statement_lines(src: str, tree: ast.AST) -> set[int]:
    """Line numbers of whole-statement overlay calls on the shot itself
    (`shot.label(...)` where `shot` is build's first parameter) that own
    their lines. A same-named method on anything else (a kit part's
    `hero.glow(shot, ...)` animates 3D) is never taken out."""
    lines = src.splitlines()
    out: set[int] = set()
    for fn in ast.walk(tree):
        if not (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == "build"):
            continue
        params = fn.args.posonlyargs + fn.args.args
        if not params:
            continue
        shot = params[0].arg
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
                continue
            f = node.value.func
            if not (isinstance(f, ast.Attribute) and f.attr in OVERLAY_CALLS
                    and isinstance(f.value, ast.Name) and f.value.id == shot):
                continue
            first, last = node.lineno, node.end_lineno or node.lineno
            before = lines[first - 1][: node.col_offset]
            after = lines[last - 1][node.end_col_offset:] if node.end_col_offset is not None else ""
            if before.strip() == "" and (after.strip() == "" or after.strip().startswith("#")):
                out.update(range(first, last + 1))
    return out


def _quiet_lines(src: str) -> set[int]:
    """Lines holding only a comment or nothing, outside any string."""
    tokens_on: dict[int, set[int]] = {}
    covered: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            (r0, _), (r1, _) = tok.start, tok.end
            if r1 > r0:
                covered.update(range(r0, r1 + 1))
            tokens_on.setdefault(r0, set()).add(tok.type)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return set()
    quiet = {tokenize.COMMENT, tokenize.NL}
    return {n for n, types in tokens_on.items() if types <= quiet and n not in covered}


def normalise_python(src: str, *, strip_overlays: bool) -> str:
    """The source a key hashes: comment-only and blank lines out, trailing
    spaces off, and with `strip_overlays` the overlay statements out too."""
    drop = _quiet_lines(src)
    if strip_overlays:
        try:
            drop |= _overlay_statement_lines(src, ast.parse(src))
        except SyntaxError:
            pass
    return "\n".join(l.rstrip() for n, l in enumerate(src.splitlines(), 1) if n not in drop)


def _file_digest(path: Path, *, strip_overlays: bool = False) -> str:
    data = path.read_bytes()
    if path.suffix == ".py":
        try:
            data = normalise_python(data.decode("utf-8"), strip_overlays=strip_overlays).encode()
        except UnicodeDecodeError:
            pass
    return hashlib.sha256(data).hexdigest()


def _walk(root: Path, sub: str) -> list[Path]:
    base = root / sub
    if not base.is_dir():
        return []
    out = []
    for d, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        out += [Path(d) / f for f in sorted(files) if not f.endswith((".pyc", ".blend1")) and not f.startswith(".")]
    return out


def library_files(root: Path, *, overlay: bool) -> list[Path]:
    """blenderkc/ files: the 3D part (overlay=False) or the overlay part."""
    def is_overlay(p: Path) -> bool:
        r = p.relative_to(root).as_posix()
        return r in OVERLAY_FILES or any(r.startswith(d) for d in OVERLAY_DIRS)
    return [p for p in _walk(root, "blenderkc") if is_overlay(p) == overlay]


def topic_files(topic_dir: Path, scene: str, scenes: list[str], shots: str) -> list[Path]:
    """A topic's own helper files and assets: everything in its folder but
    topic.yml, narration.json and the shot scripts (each scene's own script
    is hashed on its own)."""
    root = topic_dir
    scripts = {(Path(shots) / f"{s}.py").as_posix() for s in scenes}
    out = []
    for p in _walk(root, "."):
        r = p.relative_to(root).as_posix()
        if r in ("topic.yml", "narration.json") or r in scripts:
            continue
        out.append(p)
    return out


def bpy_pin(root: Path) -> str | None:
    try:
        for line in (root / "requirements.txt").read_text().splitlines():
            m = re.match(r"^\s*bpy\s*==\s*([^\s#;]+)", line)
            if m:
                return m.group(1)
    except OSError:
        pass
    return None


def _hash(parts: list) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def plate_key(root: Path, topic_dir: Path, scene: str, scenes: list[str], shots: str,
              timing: dict, settings: dict) -> str:
    script = topic_dir / shots / f"{scene}.py"
    parts = [
        ["version", CACHE_VERSION],
        ["bpy", bpy_pin(root)],
        ["settings", {k: settings[k] for k in ("res", "samples", "fps")}],
        ["plate", PLATE_ENCODE],
        ["timing", timing],
        ["shot", _file_digest(script, strip_overlays=True)],
        ["library", [[p.relative_to(root).as_posix(), _file_digest(p)] for p in library_files(root, overlay=False)]],
        ["topic", [[p.relative_to(topic_dir).as_posix(), _file_digest(p)] for p in topic_files(topic_dir, scene, scenes, shots)]],
    ]
    return _hash(parts)


def clip_key(root: Path, topic_dir: Path, scene: str, shots: str, plate: str) -> str:
    script = topic_dir / shots / f"{scene}.py"
    parts = [
        ["version", CACHE_VERSION],
        ["plate", plate],
        ["shot", _file_digest(script)],
        ["overlay", [[p.relative_to(root).as_posix(), _file_digest(p)] for p in library_files(root, overlay=True)]],
    ]
    return _hash(parts)


def short(key: str) -> str:
    return key[:16]
