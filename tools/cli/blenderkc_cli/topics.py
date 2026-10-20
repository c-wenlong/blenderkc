"""Every topic under topics/ as data: the `topics` verb, and the manifest
reader every other verb goes through.

A topic is a folder topics/<domain>/[<subdomain>/]<slug>/ holding topic.yml,
narration.json and shots/<SceneName>.py, one shot script per narration scene.

`topics --json` is manimkc's report shape, field for field, so the self-learn
catalog indexes blenderkc with the reader it uses for the other three tools:

    outRoot (top level)                          absolute, the data root
    out_dir, deliverable.path, sidecars.*.path,  relative to outRoot
      drift.orphans[].path
    a topic's path                               relative to topics/
    sections[].files[].file                      relative to the topic folder

Each section lists one file per narration scene, `shots/<Scene>.py`, with
that one scene, which is manimkc's multi-file form.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from .repo import CliError, Repo, probe_duration, rel, which
from .yaml import YamlError, as_list, parse_yaml

BLOCK = "narrated"
SHOTS = "shots/"
OUTPUT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.mp4$")
SCENE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def sidecar_paths(out_dir: Path, stem: str) -> dict[str, Path]:
    return {
        "transcript": out_dir / f"{stem}.transcript.json",
        "chapters": out_dir / f"{stem}.chapters.json",
        "vtt": out_dir / f"{stem}.vtt",
    }


def topic_paths(repo: Repo) -> list[str]:
    """Every topic.yml under topics/, as topic paths sorted by string (the key
    every reader of the report sorts on)."""
    found = []
    if not repo.topics_dir.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(repo.topics_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
        if "topic.yml" in filenames:
            found.append(Path(os.path.relpath(dirpath, repo.topics_dir)).as_posix())
    return sorted(found)


def blocks(yml: dict) -> list[dict]:
    """The build blocks of a topic.yml: top-level mappings naming an output or
    a shots folder. One per topic folder."""
    out = []
    for key, v in yml.items():
        if isinstance(v, dict) and ("output" in v or "shots" in v):
            o = v.get("output")
            o = None if o is None else (str(o) if str(o).endswith(".mp4") else f"{o}.mp4")
            out.append({"section": key, "shots": str(v.get("shots") or SHOTS), "output": o})
    return out


def block_problem(b: dict | None) -> str | None:
    if not b:
        return "no build block"
    if b["output"] and not OUTPUT_RE.match(b["output"]):
        return f"output {b['output']!r} must be a plain file name like hbm-how-it-is-built.mp4"
    s = b["shots"]
    if s.startswith("/") or ".." in Path(s).parts:
        return f"shots {s!r} must be a folder inside the topic, like shots/"
    return None


def narration_scenes(d: Path) -> list[str]:
    try:
        n = json.loads((d / "narration.json").read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(n, list):
        return []
    return [s["scene"] for s in n if isinstance(s, dict) and isinstance(s.get("scene"), str)]


class Topic:
    def __init__(self, repo: Repo, path: str):
        self.path = path
        self.dir = repo.topics_dir / path
        self.yml_file = self.dir / "topic.yml"
        try:
            self.yml = parse_yaml(self.yml_file.read_text())
        except (OSError, YamlError) as e:
            raise CliError(f"{rel(repo.root, self.yml_file)}: {e}")
        self.blocks = blocks(self.yml)
        self.block = self.blocks[0] if self.blocks else None
        self.out_dir = repo.out_dir(path)

    @property
    def shots_dir(self) -> Path:
        return self.dir / (self.block["shots"] if self.block else SHOTS)

    def shot_file(self, scene: str) -> Path:
        return self.shots_dir / f"{scene}.py"

    @property
    def stem(self) -> str:
        return self.block["output"][: -len(".mp4")]

    def require_buildable(self) -> None:
        if not self.yml.get("topic"):
            raise CliError(f"{self.path}/topic.yml has no `topic:`; the catalog refuses a video without one")
        if not self.block or not self.block["output"]:
            raise CliError(f"{self.path}/topic.yml has no `narrated:` block with an `output:`")
        bad = block_problem(self.block)
        if bad:
            raise CliError(f"{self.path}/topic.yml: {bad}")


def load_topic(repo: Repo, path: str) -> Topic:
    return Topic(repo, path)


def resolve_topic(repo: Repo, arg: str | None) -> str:
    """A topic path relative to topics/ (or with a topics/ prefix, or a folder
    path from anywhere), else a substring that names exactly one topic."""
    if not arg:
        raise CliError("name a topic: a path under topics/, or a unique part of one")
    every = topic_paths(repo)
    as_dir = Path(arg).resolve()
    if (as_dir / "topic.yml").is_file() and str(as_dir).startswith(str(repo.topics_dir.resolve()) + os.sep):
        return as_dir.relative_to(repo.topics_dir.resolve()).as_posix()
    clean = re.sub(r"^\.?/?topics/", "", arg).rstrip("/")
    if clean in every:
        return clean
    hits = [t for t in every if clean in t]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise CliError(f"no topic matches {arg!r}; `blenderkc topics` lists them")
    raise CliError(f"{arg!r} matches {len(hits)} topics: {', '.join(hits)}")


def _deliverable(repo: Repo, out_dir: Path, output: str, probe: bool) -> dict:
    mp4 = out_dir / output
    exists = mp4.is_file()
    stem = output[: -len(".mp4")]
    side = {k: {"path": rel(repo.home, p), "exists": p.is_file()} for k, p in sidecar_paths(out_dir, stem).items()}
    return {"path": rel(repo.home, mp4), "exists": exists,
            "duration": probe_duration(mp4) if exists and probe else None, "sidecars": side}


def entry(repo: Repo, path: str, probe: bool) -> dict:
    t = load_topic(repo, path)
    scenes = narration_scenes(t.dir)
    s = lambda v: None if v is None else str(v)  # noqa: E731
    return {
        "path": path,
        "title": s(t.yml.get("title")),
        "topic": s(t.yml.get("topic")),
        "concepts": as_list(t.yml.get("concepts")),
        "tags": as_list(t.yml.get("tags")),
        "prerequisites": as_list(t.yml.get("prerequisites")),
        "materials": as_list(t.yml.get("materials")),
        "prefix": s(t.yml.get("prefix")),
        "out_dir": rel(repo.home, t.out_dir),
        "sections": [
            {
                "section": b["section"],
                "files": [{"file": (Path(b["shots"]) / f"{sc}.py").as_posix(), "scenes": [sc]} for sc in scenes],
                "output": b["output"],
                "deliverable": _deliverable(repo, t.out_dir, b["output"], probe) if b["output"] else None,
            }
            for b in t.blocks
        ],
    }


def collect(repo: Repo, probe: bool | None = None) -> list[dict]:
    if probe is None:
        probe = which("ffprobe") is not None
    return [entry(repo, p, probe) for p in topic_paths(repo)]


def _rendered_dirs(out_root: Path) -> list[tuple[Path, list[str]]]:
    found = []
    if not out_root.is_dir():
        return found
    for dirpath, _dirs, files in os.walk(out_root):
        vids = sorted(f for f in files if f.endswith(".mp4"))
        if vids and Path(dirpath) != out_root:
            found.append((Path(dirpath), vids))
    return sorted(found, key=lambda x: str(x[0]))


def drift(repo: Repo, topics: list[dict]) -> dict:
    """Orphans (a render with no source: an error) and unrendered topics
    (informational). A parent of a topic's out_dir is accounted for."""
    mirrors = {(repo.home / t["out_dir"]).resolve() for t in topics}
    parents = {m.parent for m in mirrors}
    orphans = [{"path": rel(repo.home, d), "videos": v} for d, v in _rendered_dirs(repo.out)
               if d.resolve() not in mirrors and d.resolve() not in parents]
    unrendered = [t["path"] for t in topics
                  if not any(s["deliverable"] and s["deliverable"]["exists"] for s in t["sections"])]
    return {"orphans": orphans, "unrendered": unrendered}


def problems(repo: Repo, topics: list[dict]) -> list[str]:
    """What a catalog or a build would refuse."""
    out = []
    for t in topics:
        if not t["topic"]:
            out.append(f"{t['path']}: topic.yml has no `topic:` (a catalog topic slug is required)")
        if not t["sections"]:
            out.append(f"{t['path']}: topic.yml has no build block (`narrated:` with an `output:`)")
        elif len(t["sections"]) > 1:
            out.append(f"{t['path']}: topic.yml has {len(t['sections'])} build blocks; one per topic folder")
        for s in t["sections"]:
            if not s["output"]:
                out.append(f"{t['path']}: the `{s['section']}:` block has no `output:` (the deliverable's file name)")
            shots = str(Path(s["files"][0]["file"]).parent) + "/" if s["files"] else SHOTS
            bad = block_problem({"output": s["output"], "shots": shots})
            if bad:
                out.append(f"{t['path']}: {bad}")
    return out


def missing_shots(repo: Repo, topics: list[dict]) -> list[str]:
    """Narration scenes with no shot script: a topic still being written."""
    out = []
    for t in topics:
        for s in t["sections"]:
            for f in s["files"]:
                if not (repo.topics_dir / t["path"] / f["file"]).is_file():
                    out.append(f"{t['path']}: {f['file']} (scene {f['scenes'][0]}) does not exist yet")
    return out


def report(repo: Repo) -> dict:
    topics = collect(repo)
    return {"outRoot": str(repo.home), "topics": topics, "drift": drift(repo, topics)}


def mmss(s: float) -> str:
    return f"{int(s // 60)}:{int(round(s % 60)):02d}"


def format_report(rep: dict) -> str:
    topics, d = rep["topics"], rep["drift"]
    dels = [s["deliverable"] for t in topics for s in t["sections"] if s["deliverable"]]
    L = [f"topics/ ({len(topics)} topics, {len(dels)} deliverables, {sum(1 for x in dels if x['exists'])} rendered)", ""]
    if topics:
        wide = max(4, *(len(t["path"]) for t in topics))
        L.append(f"  {'PATH'.ljust(wide)}  {'TITLE'.ljust(40)}  {'LENGTH':>10}  SCENES")
        for t in topics:
            got = [s["deliverable"] for s in t["sections"] if s["deliverable"] and s["deliverable"]["exists"]]
            if not got:
                ln = "unrendered"
            elif any(x["duration"] is None for x in got):
                ln = "rendered"
            else:
                ln = mmss(sum(x["duration"] for x in got))
            n = sum(len(f["scenes"]) for s in t["sections"] for f in s["files"])
            title = t["title"] or ""
            title = title[:39] + "…" if len(title) > 40 else title
            L.append(f"  {t['path'].ljust(wide)}  {title.ljust(40)}  {ln:>10}  {n:>6}")
        L.append("")
    if not d["orphans"] and not d["unrendered"]:
        L.append("drift   none")
    else:
        L.append("drift")
        L += [f"  ORPHAN      {o['path']}: {', '.join(o['videos'])} with no topics/ source" for o in d["orphans"]]
        L += [f"  unrendered  {u}" for u in d["unrendered"]]
    return "\n".join(L)


def main(args, repo: Repo) -> int:
    rep = report(repo)
    probs = problems(repo, rep["topics"])
    if args.json:
        print(json.dumps(rep, indent=1))
        for p in probs:
            print(f"problem: {p}", file=sys.stderr)
    else:
        print(format_report(rep))
        if probs:
            print("\n".join(["", "problems", *(f"  {p}" for p in probs)]))
        notes = missing_shots(repo, rep["topics"])
        if notes:
            print("\n".join(["", "shots not written yet", *(f"  {n}" for n in notes)]))
    return 1 if args.check and (rep["drift"]["orphans"] or probs) else 0
