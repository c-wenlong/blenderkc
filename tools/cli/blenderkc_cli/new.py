"""`blenderkc new <path> --topic <slug>`: scaffold a topic folder:
topic.yml with a free prefix, narration.json with one placeholder line per
scene, and shots/<Scene>.py stubs that import blenderkc.shot and build a
plain scene, so `still` works on a fresh topic before anything is written."""

from __future__ import annotations

import json
import re

from .repo import CliError, Repo, rel
from .topics import SCENE_RE, load_topic, topic_paths

PATH_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*){1,2}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{0,3}$")

SHOT = '''"""{scene}: {title}.

One continuous shot. Times come from narration.json through `shot`; run
`blenderkc api` for the library and `blenderkc still {topic}` to look.
"""

import bpy

from blenderkc.shot import Shot  # noqa: F401  (the type of `shot`)


def build(shot):
    """Build the scene in bpy and animate it on shot's clock."""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.5))
    shot.title({title!r}, start=0.0, end=shot.line(0).end)
'''


def used_prefixes(repo: Repo) -> dict[str, str]:
    out = {}
    for p in topic_paths(repo):
        try:
            pre = load_topic(repo, p).yml.get("prefix")
        except CliError:
            continue
        if pre:
            out[str(pre)] = p
    return out


def free_prefix(slug: str, used: dict) -> str:
    words = [w for w in slug.split("-") if w]
    base = "".join(w[0] for w in words).upper()[:4] or "T"
    if not base[0].isalpha():
        base = "T" + base[:3]
    cands = [base, *(base[:3] + c for c in "ABCDEFGHJKLMNPQRSTUVWXYZ"), *(base[:3] + str(d) for d in range(2, 10))]
    for c in cands:
        if PREFIX_RE.match(c) and c not in used:
            return c
    raise CliError("no free prefix; pass --prefix")


def camel(s: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[-_\s]+", s) if w)


def scaffold(repo: Repo, path: str, *, topic: str, title: str | None, prefix: str | None, scenes: str | None) -> dict:
    if not PATH_RE.match(path):
        raise CliError(f"{path!r}: a topic path is domain/[subdomain/]slug in kebab-case, e.g. computer-architecture/gpu-architecture/hbm")
    if not topic or not SLUG_RE.match(topic):
        raise CliError("--topic <slug> is required: the catalog topic the video is filed under")
    d = repo.topics_dir / path
    if d.exists():
        raise CliError(f"{rel(repo.root, d)} already exists")
    for other in topic_paths(repo):
        if other.startswith(path + "/") or path.startswith(other + "/"):
            raise CliError(f"{path} would nest with the topic {other}")
    slug = path.split("/")[-1]
    used = used_prefixes(repo)
    pre = prefix or free_prefix(slug, used)
    if not PREFIX_RE.match(pre):
        raise CliError(f"--prefix must be 1 to 4 capitals or digits, starting with a capital; got {pre}")
    if pre in used:
        raise CliError(f"prefix {pre} belongs to topics/{used[pre]}; pick another")
    names = [x.strip() for x in (scenes or "intro,idea,close").split(",") if x.strip()]
    if not names:
        raise CliError("--scenes names no scenes")
    ids = [f"{pre}{i}_{camel(n)}" for i, n in enumerate(names, 1)]
    bad = [f"{n!r} ({sid})" for n, sid in zip(names, ids) if not SCENE_RE.match(sid)]
    if bad:
        raise CliError(f"--scenes: {', '.join(bad)} would not be a scene name; use letters, digits, - and _")
    title = title or slug.replace("-", " ").capitalize()
    output = f"{slug}.mp4"
    shots = d / "shots"
    shots.mkdir(parents=True)
    (d / "topic.yml").write_text("\n".join([
        f"title: {json.dumps(title)}", f"topic: {topic}", "concepts: []", "tags: []", "prerequisites: []",
        "materials: []", f"prefix: {pre}", "narrated:", "  shots: shots/", f"  output: {output}", ""]))
    narration = [{"scene": sid, "title": n.replace("-", " ").capitalize(),
                  "lines": [f"Say in one sentence what {n.replace('-', ' ')} shows."]} for sid, n in zip(ids, names)]
    (d / "narration.json").write_text(json.dumps(narration, indent=1) + "\n")
    for sid, s in zip(ids, narration):
        (shots / f"{sid}.py").write_text(SHOT.format(scene=sid, title=s["title"], topic=path))
    return {"path": path, "dir": rel(repo.root, d), "prefix": pre, "scenes": ids, "output": output,
            "files": ["topic.yml", "narration.json", *(f"shots/{sid}.py" for sid in ids)]}


def main(args, repo: Repo) -> int:
    r = scaffold(repo, args.path, topic=args.topic, title=args.title, prefix=args.prefix, scenes=args.scenes)
    if args.json:
        print(json.dumps(r, indent=1))
    else:
        print("\n".join([f"created {r['dir']}/ (prefix {r['prefix']}, output {r['output']})",
                         *(f"  {f}" for f in r["files"]),
                         "", f"next: write narration.json, then the shots; `blenderkc still {r['path']}` to look"]))
    return 0
