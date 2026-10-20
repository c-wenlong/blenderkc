"""`blenderkc narrate <topic>`: voice every line of narration.json with
gnosia-audio, stage the clips in the data root, and write the two files a
topic is timed from (the same files, shapes and names as revideokc):

    <home>/public/<topic>/narration.manifest.json   clips, durations, lead-in, tail
    <home>/public/<topic>/words.json                when each written word is spoken
    <home>/public/<topic>/audio/<clip>.wav          the clips (hard links where possible)

`gnosia-audio narrate --json` fills the clip cache and reports each clip's
path, duration, spoken form and word times, plus the lead-in and tail;
nothing here hard-codes either. Word times are kokoro's own, or a labelled
estimate on other backends, aligned here to the written text.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .align import time_words
from .audio import call_gnosia
from .repo import CliError, Repo, rel, write_json
from .topics import SCENE_RE, load_topic, resolve_topic

WORD_SOURCES = ("tts", "none")


def read_narration(d: Path) -> list[dict]:
    """narration.json, checked: [{scene, title, hold?, lines: [non-empty str]}]."""
    f = d / "narration.json"
    try:
        n = json.loads(f.read_text())
    except FileNotFoundError:
        raise CliError(f"{f}: missing")
    except (OSError, ValueError) as e:
        raise CliError(f"{f}: {e}")
    if not isinstance(n, list) or not n:
        raise CliError(f"{f}: expected a non-empty list of scenes")
    seen = set()
    for i, s in enumerate(n):
        if not isinstance(s, dict) or not isinstance(s.get("scene"), str) or not SCENE_RE.match(s["scene"]):
            raise CliError(f'{f}: scene {i} needs a "scene" name like HB1_Hook')
        if s["scene"] in seen:
            raise CliError(f"{f}: scene {s['scene']} appears twice")
        seen.add(s["scene"])
        if not isinstance(s.get("title"), str) or not s["title"]:
            raise CliError(f'{f}: {s["scene"]} needs a "title" (its chapter title)')
        lines = s.get("lines")
        if not isinstance(lines, list) or not lines or any(not isinstance(l, str) or not l.strip() for l in lines):
            raise CliError(f'{f}: {s["scene"]} needs "lines", a list of non-empty strings')
        hold = s.get("hold", 0)
        if not isinstance(hold, (int, float)) or isinstance(hold, bool) or hold < 0:
            raise CliError(f'{f}: {s["scene"]} "hold" must be a number of seconds, 0 or more')
    return n


def _stage(src: str, dst: Path) -> None:
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        import shutil
        shutil.copyfile(src, dst)


def narrate_topic(repo: Repo, topic: str, *, words: str | None = None, quiet: bool = False) -> dict:
    t = load_topic(repo, topic)
    scenes = read_narration(t.dir)
    mode = words or t.yml.get("words") or "tts"
    if mode == "auto":
        mode = "tts"
    if mode not in WORD_SOURCES:
        raise CliError(f"words: must be one of {', '.join(WORD_SOURCES)}, got {mode}")
    lines = [l for s in scenes for l in s["lines"]]
    started = time.monotonic()
    reply = call_gnosia(repo, "narrate", {"lines": lines, "words": mode}, quiet=quiet)
    got = reply.get("lines") or []
    if len(got) != len(lines):
        raise CliError(f"gnosia-audio narrate answered {len(got)} lines for {len(lines)}")
    if not isinstance(reply.get("leadIn"), (int, float)) or not isinstance(reply.get("tail"), (int, float)):
        raise CliError("gnosia-audio narrate gave no leadIn and tail; the timeline needs both")

    pub = repo.public_dir(topic)
    audio = pub / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    keep = set()
    staged = []
    for l in got:
        name = Path(l["clip"]).name
        _stage(l["clip"], audio / name)
        keep.add(name)
        staged.append({**l, "audio": f"{topic}/audio/{name}"})
    for f in os.listdir(audio):
        if f not in keep:
            (audio / f).unlink()

    it = iter(staged)
    manifest = {
        "backend": reply.get("backend"), "leadIn": reply["leadIn"], "tail": reply["tail"],
        "scenes": [{"scene": s["scene"], "title": s["title"],
                    "lines": [{k: l.get(k) for k in ("text", "spoken", "audio", "duration")}
                              for l in (next(it) for _ in s["lines"])]} for s in scenes],
    }
    matched = total = 0
    words_file = None
    if mode != "none":
        k = 0
        out_lines = []
        for s in scenes:
            for index, text in enumerate(s["lines"]):
                l = staged[k]
                k += 1
                src = l.get("words")
                timed = time_words(text, src["words"], l["duration"]) if src else {"words": [], "matched": 0}
                matched += timed["matched"]
                total += len(timed["words"])
                out_lines.append({"scene": s["scene"], "index": index, "text": text,
                                  "source": (src or {}).get("source", "none"), "duration": l["duration"],
                                  "words": timed["words"]})
        words_file = {"lines": out_lines}
    write_json(pub / "narration.manifest.json", manifest)
    wp = pub / "words.json"
    if words_file:
        write_json(wp, words_file)
    elif wp.exists():
        wp.unlink()
    narrated = sum(manifest["leadIn"] + l["duration"] + manifest["tail"] for s in manifest["scenes"] for l in s["lines"])
    return {
        "topic": topic, "backend": reply.get("backend"), "voice": reply.get("voice"), "cache": reply.get("cache"),
        "synthesised": reply.get("synthesised"), "scenes": len(scenes), "lines": len(lines),
        "narratedSeconds": round(narrated, 2),
        "words": {"mode": mode, "adapter": f"gnosia-audio {reply.get('version')} ({reply.get('backend')})" if mode != "none" else "none",
                  "source": words_file["lines"][0]["source"] if words_file else "none", "matched": matched, "total": total},
        "manifest": rel(repo.home, pub / "narration.manifest.json"),
        "wordsFile": rel(repo.home, wp) if words_file else None,
        "seconds": round(time.monotonic() - started, 1),
    }


def narrated_state(repo: Repo, topic: str) -> dict:
    """What a previous `narrate` left, checked against narration.json without
    voicing anything (build --no-narrate, still --timing narration). Refuses,
    naming the fix, when the manifest is missing or malformed, when a scene,
    title, line count or line text changed (a `hold` edit is not stale: only
    the render reads it), when a clip is not staged, or when words.json exists
    but describes other lines."""
    t = load_topic(repo, topic)
    scenes = read_narration(t.dir)
    pub = repo.public_dir(topic)

    def fail(why: str) -> CliError:
        return CliError(f"{topic}: {why}; run `blenderkc narrate {topic}` first")

    def stored(f: Path, name: str, optional: bool):
        try:
            text = f.read_text()
        except FileNotFoundError:
            if optional:
                return None
            raise fail(f"no narration yet ({name} is missing under the data root)")
        try:
            return json.loads(text)
        except ValueError as e:
            raise fail(f"{name} is not valid JSON ({e})")

    m = stored(pub / "narration.manifest.json", "narration.manifest.json", False)
    if not isinstance(m, dict) or not isinstance(m.get("scenes"), list) \
            or not isinstance(m.get("leadIn"), (int, float)) or not isinstance(m.get("tail"), (int, float)):
        raise fail("narration.manifest.json is not a manifest this blenderkc wrote (it needs scenes, leadIn and tail)")

    def stale(detail: str) -> CliError:
        return fail(f"the narration is stale: narration.json changed since the last narrate ({detail})")

    if len(m["scenes"]) != len(scenes):
        raise stale(f"{len(scenes)} scenes now, {len(m['scenes'])} narrated")
    for i, s in enumerate(scenes):
        ms = m["scenes"][i]
        if ms.get("scene") != s["scene"]:
            raise stale(f"scene {i + 1} is {s['scene']} now, {ms.get('scene')} when narrated")
        if ms.get("title") != s["title"]:
            raise stale(f"the title of {s['scene']}")
        if not isinstance(ms.get("lines"), list) or len(ms["lines"]) != len(s["lines"]):
            raise stale(f"the number of lines in {s['scene']}")
        for j, text in enumerate(s["lines"]):
            if ms["lines"][j].get("text") != text:
                raise stale(f"{s['scene']} line {j + 1}")
    for s in m["scenes"]:
        for j, l in enumerate(s["lines"]):
            if not isinstance(l.get("audio"), str) or not isinstance(l.get("duration"), (int, float)):
                raise fail(f"{s['scene']} line {j + 1} has no clip in the manifest")
            if not (repo.public / l["audio"]).is_file():
                raise fail(f"the clip for {s['scene']} line {j + 1} is missing ({l['audio']} under the data root's public/)")
    w = stored(pub / "words.json", "words.json", True)
    if w is not None:
        exp = [(s["scene"], i, text) for s in scenes for i, text in enumerate(s["lines"])]
        got = w.get("lines") if isinstance(w, dict) else None
        if not isinstance(got, list) or [(x.get("scene"), x.get("index"), x.get("text")) for x in got] != exp:
            raise fail("words.json does not match narration.json (the last narrate did not finish, or the text changed since)")
    narrated = sum(m["leadIn"] + l["duration"] + m["tail"] for s in m["scenes"] for l in s["lines"])
    return {
        "topic": topic, "backend": m.get("backend"), "voice": None, "cache": None, "synthesised": None,
        "scenes": len(scenes), "lines": sum(len(s["lines"]) for s in scenes), "narratedSeconds": round(narrated, 2),
        "words": {"mode": None, "adapter": None, "source": (w["lines"][0]["source"] if w and w["lines"] else "none"),
                  "matched": None, "total": None},
        "manifest": rel(repo.home, pub / "narration.manifest.json"),
        "wordsFile": rel(repo.home, pub / "words.json") if w else None, "seconds": 0,
    }


def format_narration(s: dict) -> str:
    pct = round(100 * s["words"]["matched"] / s["words"]["total"]) if s["words"]["total"] else 0
    return "\n".join([
        f"narrated {s['topic']}: {s['scenes']} scenes, {s['lines']} lines, {s['narratedSeconds']}s with lead-ins and tails (backend {s['backend']})",
        f"  clips    {s['lines']} in {s['cache']}, {s['synthesised']} newly voiced",
        f"  words    {s['words']['source']} via {s['words']['adapter']}; {s['words']['matched']}/{s['words']['total']} written words matched a timed token ({pct}%)",
        f"  wrote    {s['manifest']}{', ' + s['wordsFile'] if s['wordsFile'] else ''} under the data root",
    ])


def main(args, repo: Repo) -> int:
    topic = resolve_topic(repo, args.topic)
    s = narrate_topic(repo, topic, words=args.words, quiet=args.json)
    print(json.dumps(s, indent=1) if args.json else format_narration(s))
    return 0
