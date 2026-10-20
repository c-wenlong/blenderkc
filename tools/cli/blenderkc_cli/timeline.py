"""The narration timeline, the same model as manimkc's `voiceover()` and
revideokc's lib/timeline.ts: every line is `leadIn` seconds of silence, the
clip, then `tail` seconds, lines play back to back, and a scene lasts as long
as its lines plus its `hold`. So the narration decides the video's length.

Frames, as revideokc counts them: a line's slot is ceil((leadIn + clip +
tail) * fps) frames, its voice starts round(leadIn * fps) frames into the
slot, and a scene's hold adds round(hold * fps) frames at its end. The audio
track (media.narration_track) places each clip at exactly those frames, so
picture and voice cannot drift apart.

Two sources: the narration on disk (narration.manifest.json and words.json
under the data root's public/<topic>/), or, before any narration exists, an
estimate at 15.2 characters a second with gnosia-audio's lead-in 0.2 s and
tail 0.4 s, and word times spread over each line by word length.

Each scene is handed to the renderer as a scene timing file (`--timing`),
the shape agreed with blenderkc/shot.py:

    {scene, fps, source, leadIn, tail, hold, frames, duration,
     lines: [{index, text, start, voice, clip, end, words: [{word, start, end}]}]}

start, voice and end are seconds from the scene's start (frame 1 is t = 0);
word times are seconds from the clip's start, as in words.json.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .audio import gnosia_bin, runnable
from .narrate import narrate_topic, narrated_state, read_narration
from .repo import FPS, CliError, Repo, read_json

ESTIMATE_CPS = 15.2
ESTIMATE_LEAD_IN = 0.2
ESTIMATE_TAIL = 0.4
TIMING_MODES = ("auto", "narrate", "narration", "estimate")


def estimate_words(text: str, clip: float) -> list[dict]:
    """Written words spread over the clip by length (plus one for the gap)."""
    words = text.split()
    total = sum(len(w) + 1 for w in words) or 1
    out, t = [], 0.0
    for w in words:
        d = clip * (len(w) + 1) / total
        out.append({"word": w, "start": round(t, 3), "end": round(t + d * len(w) / (len(w) + 1), 3)})
        t += d
    return out


def estimate_manifest(narration: list[dict]) -> tuple[dict, dict]:
    """A manifest and words file as `narrate` would write them, from the text alone."""
    m = {"backend": "estimate", "leadIn": ESTIMATE_LEAD_IN, "tail": ESTIMATE_TAIL, "scenes": []}
    wl = []
    for s in narration:
        lines = []
        for i, text in enumerate(s["lines"]):
            clip = round(len(text) / ESTIMATE_CPS, 3)
            lines.append({"text": text, "spoken": text, "audio": None, "duration": clip})
            wl.append({"scene": s["scene"], "index": i, "text": text, "source": "estimate", "duration": clip,
                       "words": estimate_words(text, clip)})
        m["scenes"].append({"scene": s["scene"], "title": s["title"], "lines": lines})
    return m, {"lines": wl}


def lay_out(manifest: dict, narration: list[dict], words: dict | None, fps: int = FPS, source: str = "narration") -> dict:
    """The frame-accurate timeline of a topic."""
    holds = {s["scene"]: float(s.get("hold") or 0) for s in narration}
    by_line = {(w["scene"], w["index"], w["text"]): w for w in (words or {}).get("lines", [])}
    lead, tail = manifest["leadIn"], manifest["tail"]
    at = 0
    scenes = []
    for s in manifest["scenes"]:
        t = 0
        lines = []
        for i, l in enumerate(s["lines"]):
            slot = math.ceil(round((lead + l["duration"] + tail) * fps, 6))
            w = by_line.get((s["scene"], i, l["text"]))
            lines.append({"index": i, "text": l["text"], "audio": l.get("audio"), "duration": l["duration"],
                          "from": t, "audioFrom": t + round(lead * fps), "slot": slot,
                          "words": (w or {}).get("words", []), "wordsSource": (w or {}).get("source", "none")})
            t += slot
        hold = holds.get(s["scene"], 0.0)
        frames = t + round(hold * fps)
        scenes.append({"scene": s["scene"], "title": s["title"], "from": at, "frames": frames, "hold": hold, "lines": lines})
        at += frames
    return {"fps": fps, "frames": at, "leadIn": lead, "tail": tail, "source": source, "scenes": scenes}


def scene_named(tl: dict, name: str) -> dict:
    for s in tl["scenes"]:
        if s["scene"] == name:
            return s
    raise CliError(f"no scene {name}; the timeline has {', '.join(s['scene'] for s in tl['scenes'])}")


def scene_timing(tl: dict, scene: dict) -> dict:
    """The renderer's --timing file for one scene."""
    fps = tl["fps"]
    r = lambda x: round(x, 6)  # noqa: E731
    return {
        "scene": scene["scene"], "fps": fps, "source": tl["source"], "leadIn": tl["leadIn"], "tail": tl["tail"],
        "hold": scene["hold"], "frames": scene["frames"], "duration": r(scene["frames"] / fps),
        "lines": [{"index": l["index"], "text": l["text"], "start": r(l["from"] / fps), "voice": r(l["audioFrom"] / fps),
                   "clip": l["duration"], "end": r((l["from"] + l["slot"]) / fps), "words": l["words"]}
                  for l in scene["lines"]],
    }


def sidecar_request(out_dir: Path, stem: str, tl: dict, name: str | None = None) -> dict:
    """gnosia-audio's `sidecars` request for a rendered timeline: each scene
    with its own start, its lines relative to it (revideokc's sidecarRequest)."""
    fps = tl["fps"]
    return {
        "outDir": str(out_dir), "name": name or stem, "video": f"{stem}.mp4", "duration": tl["frames"] / fps,
        "scenes": [{"scene": s["scene"], "title": s["title"], "start": s["from"] / fps, "duration": s["frames"] / fps,
                    "lines": [{"start": l["audioFrom"] / fps, "end": l["audioFrom"] / fps + l["duration"], "text": l["text"]}
                              for l in s["lines"]]}
                   for s in tl["scenes"]],
    }


def from_disk(repo: Repo, topic: str, narration: list[dict]) -> dict:
    pub = repo.public_dir(topic)
    m = read_json(pub / "narration.manifest.json")
    w = read_json(pub / "words.json", None)
    return lay_out(m, narration, w, source="narration")


def topic_timeline(repo: Repo, topic: str, mode: str, *, quiet: bool = False) -> tuple[dict, dict | None]:
    """(timeline, narration summary or None) by `mode`:

    narrate    voice with gnosia-audio first (cheap when cached), then use it
    narration  what a previous `narrate` left, refused when missing or stale
    estimate   the text alone, 15.2 characters a second
    auto       narrate when gnosia-audio is installed, else the narration on
               disk when it is current, else the estimate
    """
    if mode not in TIMING_MODES:
        raise CliError(f"--timing must be one of {', '.join(TIMING_MODES)}, got {mode}")
    t_dir = repo.topics_dir / topic
    narration = read_narration(t_dir)
    if mode == "auto":
        if runnable(gnosia_bin(repo)[0]):
            mode = "narrate"
        else:
            try:
                narrated_state(repo, topic)
                mode = "narration"
            except CliError:
                mode = "estimate"
    if mode == "estimate":
        m, w = estimate_manifest(narration)
        return lay_out(m, narration, w, source="estimate"), None
    summary = narrate_topic(repo, topic, quiet=quiet) if mode == "narrate" else narrated_state(repo, topic)
    return from_disk(repo, topic, narration), summary


def write_scene_timings(tl: dict, d: Path, scenes: list[str] | None = None) -> dict[str, Path]:
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for s in tl["scenes"]:
        if scenes is None or s["scene"] in scenes:
            f = d / f"{s['scene']}.json"
            f.write_text(json.dumps(scene_timing(tl, s), indent=1, sort_keys=True) + "\n")
            out[s["scene"]] = f
    return out
