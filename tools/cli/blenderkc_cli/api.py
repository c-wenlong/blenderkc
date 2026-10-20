"""`blenderkc api [name ...]`: the rules a shot script lives by, then every
public function and class the library exports, read from blenderkc/ with
`ast` (nothing is imported, so it works without bpy), each with its
signature, the first line of its doc and a real usage line from topics/."""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

from .repo import Repo

RULES = """\
A shot script is topics/<topic>/shots/<Scene>.py and defines build(shot):
build the scene in bpy and animate it on shot's clock, in seconds.
- Time comes from the narration: shot.at("word", line=0, nth=1), shot.line(i).start/.end,
  shot.duration. Never a frame number, never a hard-coded second.
- 2D text is an overlay drawn after rendering: shot.label, shot.title, shot.equation.
  Keep each such call a statement of its own: then editing it re-composites
  without rendering 3D again (keys.py).
- Plain bpy is always allowed beside the kit. No randomness without a fixed seed.
- Sentence case, plain Unicode, no LaTeX, no company logos.
- Look before calling it done: blenderkc still <topic>, then Read the sheet."""


def _sig(node) -> str:
    a = node.args
    parts = []
    pos = a.posonlyargs + a.args
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    for arg, d in zip(pos, defaults):
        parts.append(arg.arg + (f"={ast.unparse(d)}" if d is not None else ""))
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")
    for arg, d in zip(a.kwonlyargs, a.kw_defaults):
        parts.append(arg.arg + (f"={ast.unparse(d)}" if d is not None else ""))
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    if parts and parts[0] in ("self", "cls"):
        parts = parts[1:]
    return f"({', '.join(parts)})"


def _doc(node) -> str:
    d = ast.get_docstring(node) or ""
    return d.strip().split("\n")[0]


def exports(repo: Repo) -> list[dict]:
    out = []
    lib = repo.lib_dir
    if not lib.is_dir():
        return out
    for dirpath, dirs, files in os.walk(lib):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")) and d != "fonts")
        for f in sorted(files):
            if not f.endswith(".py") or (f.startswith("_") and f != "__init__.py"):
                continue
            p = Path(dirpath) / f
            mod = p.relative_to(repo.root).with_suffix("").as_posix().replace("/", ".").removesuffix(".__init__")
            try:
                tree = ast.parse(p.read_text())
            except (SyntaxError, UnicodeDecodeError) as e:
                out.append({"module": mod, "name": "(unreadable)", "kind": "error", "signature": "", "doc": str(e)})
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                    out.append({"module": mod, "name": node.name, "kind": "function", "signature": _sig(node), "doc": _doc(node)})
                elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                    out.append({"module": mod, "name": node.name, "kind": "class", "signature": "", "doc": _doc(node)})
                    for m in node.body:
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and not m.name.startswith("_"):
                            kind = "property" if any(isinstance(d, ast.Name) and d.id == "property" for d in m.decorator_list) else "method"
                            out.append({"module": mod, "name": f"{node.name}.{m.name}", "kind": kind,
                                        "signature": "" if kind == "property" else _sig(m), "doc": _doc(m)})
    return out


def usage(repo: Repo, name: str) -> str | None:
    short = name.split(".")[-1]
    pat = re.compile(rf"\b{re.escape(short)}\(")
    for p in sorted(repo.topics_dir.glob("**/shots/*.py")) if repo.topics_dir.is_dir() else []:
        for line in p.read_text(errors="replace").splitlines():
            if pat.search(line) and not line.lstrip().startswith(("def ", "#")):
                return f"{p.relative_to(repo.topics_dir).as_posix()}: {line.strip()}"
    return None


def main(args, repo: Repo) -> int:
    ex = exports(repo)
    if args.names:
        want = set(args.names)
        ex = [e for e in ex if e["name"] in want or e["name"].split(".")[-1] in want or e["module"] in want]
    for e in ex:
        e["usage"] = usage(repo, e["name"]) if e["kind"] != "class" else None
    if args.json:
        print(json.dumps({"rules": RULES, "library": str(repo.lib_dir), "exports": ex}, indent=1))
        return 0
    L = [RULES, ""]
    if not repo.lib_dir.is_dir():
        L.append(f"(no library at {repo.lib_dir} yet: blenderkc/shot.py, kit/, render.py and overlay.py land with the library)")
    mod = None
    for e in ex:
        if e["module"] != mod:
            mod = e["module"]
            L += ["", mod]
        L.append(f"  {e['name']}{e['signature']}{'  [' + e['kind'] + ']' if e['kind'] in ('class', 'property') else ''}")
        if e["doc"]:
            L.append(f"      {e['doc']}")
        if e.get("usage"):
            L.append(f"      e.g. {e['usage']}")
    print("\n".join(L))
    return 0
