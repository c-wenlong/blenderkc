"""A scene's narration clock. Pure Python, no bpy, so tests and the CLI can use it.

The timing file (`render.py --timing`) is written by the pipeline:

    {"scene": "HB1_Hook", "fps": 30, "source": "narration" | "estimate",
     "leadIn": 0.2, "tail": 0.4, "hold": 0.6, "frames": 312, "duration": 10.4,
     "lines": [{"index": 0, "text": "...", "start": 0.0, "voice": 0.2,
                "clip": 3.1, "end": 3.7,
                "words": [{"word": "HBM,", "start": 0.0, "end": 0.31}, ...]}]}

Times are seconds from the scene's first frame, except a word's start and end,
which are seconds from its line's clip start (gnosia-audio's words.json
convention). Frame f (1-based) is at t = (f - 1) / fps.

Without narration, `timing_from_narration` estimates: each line's clip is its
length at 15.2 characters a second, the slot is lead-in + clip + tail rounded up
to whole frames, the voice starts round(lead-in x fps) frames into it, lines
play back to back, then round(hold x fps) frames: the pipeline's
tools/cli/blenderkc_cli/timeline.py lays scenes out the same way.
"""
import json
import math
import re

CHARS_PER_SECOND = 15.2
LEAD_IN = 0.2
TAIL = 0.4
FPS = 30


def norm(word):
    """Comparable form of a word: lower case letters, digits, apostrophes and %."""
    return re.sub(r"[^a-z0-9'%]", "", word.lower().replace("‘", "'").replace("’", "'"))


def estimate_clip(text):
    return round(len(text) / CHARS_PER_SECOND, 3)


def estimate_words(text, clip):
    """Spread a line's written words over its clip, weighted by length."""
    words = text.split()
    if not words:
        return []
    total = float(sum(len(w) + 1 for w in words))
    out, t = [], 0.0
    for w in words:
        d = clip * (len(w) + 1) / total
        out.append({"word": w, "start": round(t, 3), "end": round(t + d * len(w) / (len(w) + 1), 3)})
        t += d
    return out


def _scene_entry(narration, scene):
    for s in narration:
        if s.get("scene") == scene:
            return s
    names = ", ".join(s.get("scene", "?") for s in narration)
    raise KeyError(f"no scene {scene} in narration.json; it has {names}")


def timing_from_narration(narration, scene, fps=FPS, words=None, lead_in=LEAD_IN, tail=TAIL):
    """The timing dict for one scene.

    narration: the narration.json list, or a path to it.
    words: an optional words.json dict ({"lines": [{scene, index, text, duration,
    words}]}); a line found there (same scene, index and text) takes its real
    clip duration and word times, every other line is estimated.
    """
    if isinstance(narration, str):
        with open(narration) as f:
            narration = json.load(f)
    if isinstance(words, str):
        with open(words) as f:
            words = json.load(f)
    entry = _scene_entry(narration, scene)
    known = {}
    for w in (words or {}).get("lines", []):
        if w.get("scene") == scene:
            known[(w.get("index"), w.get("text"))] = w
    lines, frame, measured = [], 0, 0
    for i, text in enumerate(entry.get("lines", [])):
        hit = known.get((i, text))
        if hit:
            clip = float(hit["duration"])
            ws = hit.get("words") or estimate_words(text, clip)
            measured += 1
        else:
            clip = estimate_clip(text)
            ws = estimate_words(text, clip)
        slot = math.ceil(round((lead_in + clip + tail) * fps, 6))
        start = frame / fps
        lines.append({
            "index": i, "text": text, "start": round(start, 6),
            "voice": round((frame + round(lead_in * fps)) / fps, 6), "clip": round(clip, 6),
            "end": round((frame + slot) / fps, 6), "words": ws,
        })
        frame += slot
    hold = float(entry.get("hold", 0) or 0)
    frame += round(hold * fps)
    frames = max(frame, 1)
    if lines and measured == len(lines):
        source = "narration"
    elif measured:
        source = "mixed"
    else:
        source = "estimate"
    return {
        "scene": scene, "fps": fps, "source": source, "leadIn": lead_in, "tail": tail,
        "hold": hold, "frames": frames, "duration": frames / fps, "lines": lines,
    }


class Line:
    """One narration line. start/end: when its voice starts and stops (scene
    seconds). slot_start/slot_end: its whole slot, lead-in and tail included."""

    def __init__(self, d, lead_in):
        self.index = d["index"]
        self.text = d.get("text", "")
        self.slot_start = float(d["start"])
        self.start = float(d.get("voice", self.slot_start + lead_in))
        self.clip = float(d.get("clip", 0.0))
        self.end = self.start + self.clip
        self.slot_end = float(d.get("end", self.end))
        self.words = d.get("words") or estimate_words(self.text, self.clip)

    def __repr__(self):
        return f"Line({self.index}, {self.start:.2f}-{self.end:.2f}s, {self.text[:40]!r})"


class Timing:
    """The clock a shot runs on, built from a timing dict."""

    def __init__(self, d):
        self.scene = d.get("scene", "")
        self.fps = int(d.get("fps", FPS))
        self.source = d.get("source", "estimate")
        lead_in = float(d.get("leadIn", LEAD_IN))
        if "frames" in d:
            self.frames = int(d["frames"])
        else:
            self.frames = max(1, math.ceil(float(d["duration"]) * self.fps - 1e-9))
        self.duration = self.frames / self.fps
        self.lines = [Line(x, lead_in) for x in d.get("lines", [])]

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(json.load(f))

    @classmethod
    def fixed(cls, seconds, fps=FPS, scene=""):
        """A clock with no narration: just a length."""
        return cls({"scene": scene, "fps": fps, "duration": seconds, "lines": []})

    def frame(self, t):
        """1-based frame number at time t (seconds), clamped to the scene."""
        return min(self.frames, max(1, int(round(t * self.fps)) + 1))

    def time(self, frame):
        return (frame - 1) / self.fps

    def line(self, i):
        if not 0 <= i < len(self.lines):
            raise IndexError(f"{self.scene} has no line {i}; it has {len(self.lines)} lines")
        return self.lines[i]

    def _find(self, needle, line, nth):
        if nth < 1:
            raise ValueError("nth counts from 1")
        ln = self.line(line)
        parts = [norm(p) for p in needle.split() if norm(p)]
        if not parts:
            raise ValueError(f"cue {needle!r} has no letters or digits")
        words = ln.words
        seen = 0
        for k in range(len(words)):
            if all(k + j < len(words) and norm(words[k + j]["word"]) == p for j, p in enumerate(parts)):
                seen += 1
                if seen == nth:
                    return ln, words[k], words[k + len(parts) - 1]
        said = " ".join(w["word"] for w in words)
        raise KeyError(f'cue "{needle}" (#{nth}) is not in {self.scene} line {line}: {said}')

    def at(self, word, line=0, nth=1):
        """Scene time (s) the nth (1-based) `word` (or phrase) of `line` starts."""
        ln, first, _ = self._find(word, line, nth)
        return ln.start + float(first["start"])

    def after(self, word, line=0, nth=1):
        """Scene time (s) the nth `word` (or phrase) of `line` ends."""
        ln, _, last = self._find(word, line, nth)
        return ln.start + float(last["end"])
