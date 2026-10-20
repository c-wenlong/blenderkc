"""Shot: one scene's clock, animation, camera, anchors and 2D overlays.

A shot script defines `build(shot)`. Everything is in seconds:

    def build(shot):
        rig = kit.rigs.dark_studio(shot)
        pkg = kit.parts.gpu_package()
        shot.camera.set(pos=(-7.5, -9, 6.5), look=(0.3, 0, 0.2))
        t = shot.at("stack")                      # when line 0 says "stack"
        shot.camera.move(t, t + 2, pos=(-0.6, -6.2, 3.2), look=pkg.point("hero"))
        shot.tween(obj, "location.z", 1.0, t, t + 1.5, ease="out")
        shot.label("Silicon interposer", anchor=pkg.point("interposer"), start=t)

Overlays (label, title, equation, glow) are records only: they never touch
bpy, and the pipeline composites them after rendering, so a wording fix does
not re-render 3D. Anchors are projected per frame by render.py.
"""
from . import ease as E
from .timing import Timing
from .tracks import Tracks

WIDTH, HEIGHT = 1920, 1080
FADE = 0.35


class Shot:
    def __init__(self, timing, topic_dir=None, width=WIDTH, height=HEIGHT):
        if not isinstance(timing, Timing):
            timing = Timing(timing)
        self.timing = timing
        self.scene = timing.scene
        self.fps = timing.fps
        self.frames = timing.frames
        self.duration = timing.duration
        self.topic_dir = topic_dir
        self.width, self.height = width, height
        self.tracks = Tracks()
        self.anchors = {}
        self.overlays = []
        self._camera = None

    # -- clock ----------------------------------------------------------
    def frame(self, t):
        """1-based frame at time t (seconds)."""
        return self.timing.frame(t)

    def time(self, frame):
        return self.timing.time(frame)

    def at(self, word, line=0, nth=1):
        """When the nth (from 1) `word` or phrase of `line` starts, in seconds.
        Raises KeyError naming the line when it is not said there."""
        return self.timing.at(word, line, nth)

    def after(self, word, line=0, nth=1):
        """When that word or phrase ends."""
        return self.timing.after(word, line, nth)

    def line(self, i):
        """Line i: .start/.end of its voice, .slot_start/.slot_end, .text."""
        return self.timing.line(i)

    @property
    def lines(self):
        return self.timing.lines

    # -- animation ------------------------------------------------------
    def tween(self, target, path, to, start, end, ease="inout", frm=None):
        """Animate target.path (e.g. "location", "location.z", "scale",
        "default_value" of a node socket) to `to` over [start, end] seconds."""
        self._check_span(start, end)
        return self.tracks.tween(target, path, to, start, end, ease, frm)

    def path(self, target, path, fn, start, end, ease="inout"):
        """Animate along fn(u) for u eased from 0 to 1."""
        self._check_span(start, end)
        return self.tracks.path(target, path, fn, start, end, ease)

    def set(self, target, path, value, at=0.0):
        """Step target.path to value at time `at`."""
        return self.tracks.set(target, path, value, at)

    def on_frame(self, fn):
        """Call fn(t) every frame after the tracks are applied (for anything
        that is not a tween: moving many points, procedural motion)."""
        self.tracks.callbacks.append(fn)
        return fn

    def value(self, target, path, t):
        """A property's animated value at time t."""
        return self.tracks.value(target, path, t)

    def show(self, obj, start=0.0, end=None):
        """Visible (in render) only from start to end. Applies to the object's
        children too, so a part's root hides the whole part."""
        objs = _with_children(obj)
        for o in objs:
            self.tracks.set(o, "hide_render", start > 0, 0.0)
            if start > 0:
                self.tracks.set(o, "hide_render", False, start)
            if end is not None:
                self.tracks.set(o, "hide_render", True, end)
        return obj

    def hide(self, obj, at):
        for o in _with_children(obj):
            self.tracks.set(o, "hide_render", True, at)
        return obj

    def _check_span(self, start, end):
        if end < start:
            raise ValueError(f"{self.scene}: animation ends at {end:.2f}s before it starts at {start:.2f}s")

    def apply(self, t):
        self.tracks.apply(t)

    # -- camera ---------------------------------------------------------
    @property
    def camera(self):
        if self._camera is None:
            from .camera import CameraRig

            self._camera = CameraRig(self)
        return self._camera

    # -- anchors and overlays ------------------------------------------
    def anchor(self, name, target, offset=(0.0, 0.0, 0.0)):
        """Name a 3D point (object, AnchorRef, part, or fixed (x, y, z)) whose
        screen position render.py records every frame. Returns the name."""
        self.anchors[name] = (target, tuple(offset))
        return name

    def _anchor_name(self, anchor, hint):
        if anchor is None or isinstance(anchor, str):
            if isinstance(anchor, str) and anchor not in self.anchors:
                raise KeyError(f"no anchor named {anchor!r}; make it with shot.anchor(name, obj)")
            return anchor
        base = "a_" + "".join(c if c.isalnum() else "_" for c in hint.lower())[:32]
        name, k = base, 2
        while name in self.anchors and self.anchors[name][0] is not anchor:
            name, k = f"{base}_{k}", k + 1
        return self.anchor(name, anchor)

    def _span(self, start, end):
        start = float(start)
        end = self.duration if end is None else float(end)
        if end <= start:
            raise ValueError(f"{self.scene}: overlay ends at {end:.2f}s, not after its start {start:.2f}s")
        return start, end

    def label(self, text, anchor, start=0.0, end=None, side="auto", dx=None, dy=None, size=30):
        """A label with a leader line to a point that follows the 3D anchor.
        side: auto, right, left, below-right, below-left. dx/dy (px at 1080p)
        override where the elbow sits relative to the anchor."""
        start, end = self._span(start, end)
        rec = {"kind": "label", "text": text, "anchor": self._anchor_name(anchor, text), "start": start,
               "end": end, "side": side, "size": size}
        if dx is not None:
            rec["dx"] = dx
        if dy is not None:
            rec["dy"] = dy
        self.overlays.append(rec)
        return rec

    def title(self, text, start=0.0, end=None, sub=None, position="center", size=72):
        """A title card over the picture. position: center, lower, top."""
        start, end = self._span(start, end)
        rec = {"kind": "title", "text": text, "sub": sub, "start": start, "end": end, "position": position, "size": size}
        self.overlays.append(rec)
        return rec

    def equation(self, text, start=0.0, end=None, at=None, anchor=None, size=56, accent=None):
        """A line of maths in plain Unicode ("Bandwidth = frequency × bus width").
        Wrap terms in [brackets] to draw them in the accent colour. at: (x, y)
        pixels at 1080p for its centre (default: centre, lower third)."""
        start, end = self._span(start, end)
        rec = {"kind": "equation", "text": text, "start": start, "end": end, "size": size}
        if anchor is not None:
            rec["anchor"] = self._anchor_name(anchor, text)
        if at is not None:
            rec["at"] = list(at)
        if accent is not None:
            rec["accent"] = list(accent)
        self.overlays.append(rec)
        return rec

    def glow(self, text, start=0.0, end=None, at=None, anchor=None, color="green", size=64, box=False):
        """Glowing text, like a number lit on the board ("1,152 GB", "266").
        color: green, red, amber, blue, white or an (r, g, b) 0-255 triple."""
        start, end = self._span(start, end)
        rec = {"kind": "glow", "text": text, "start": start, "end": end, "size": size, "color": color, "box": box}
        if anchor is not None:
            rec["anchor"] = self._anchor_name(anchor, text)
        if at is not None:
            rec["at"] = list(at)
        self.overlays.append(rec)
        return rec

    def records(self):
        return list(self.overlays)


def _with_children(obj):
    if hasattr(obj, "root") and not hasattr(obj, "children_recursive"):
        obj = obj.root
    out = [obj]
    out.extend(getattr(obj, "children_recursive", []))
    return out


def ease(name="inout"):
    """The easing function by name (see blenderkc.ease)."""
    return E.get(name)
