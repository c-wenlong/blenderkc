"""Animation as functions of time, evaluated every frame. Pure Python.

A shot never writes keyframes. It adds segments to tracks, one track per
(target, property, index); before each frame the shot evaluates every track and
sets the property. A segment runs from t0 to t1 with an ease, interpolating
from its start value to its end value, or calling fn(u) for a path (an orbit).
Before the first segment a track holds the value the property had when the
track was made; after a segment ends it holds that segment's end value.
"""
from . import ease as E


class Segment:
    __slots__ = ("t0", "t1", "v0", "v1", "ease", "fn")

    def __init__(self, t0, t1, v0, v1, ease="inout", fn=None):
        if t1 < t0:
            raise ValueError(f"segment ends ({t1}) before it starts ({t0})")
        self.t0, self.t1, self.v0, self.v1 = float(t0), float(t1), v0, v1
        self.ease = E.get(ease)
        self.fn = fn

    def value(self, t):
        if t >= self.t1:
            return self.v1
        u = self.ease((t - self.t0) / (self.t1 - self.t0)) if self.t1 > self.t0 else 1.0
        return self.fn(u) if self.fn else E.lerp(self.v0, self.v1, u)


def _get(target, path, index):
    v = getattr(target, path)
    if index is not None:
        return v[index]
    if isinstance(v, (int, float, bool)):
        return v
    try:
        return tuple(v)
    except TypeError:
        return v


def _set(target, path, index, value):
    if index is None:
        setattr(target, path, value)
    else:
        getattr(target, path)[index] = value


AXES = {"x": 0, "y": 1, "z": 2, "w": 3, "r": 0, "g": 1, "b": 2, "a": 3}


def split_path(path, index=None):
    """'location.z' -> ('location', 2); 'location[1]' -> ('location', 1)."""
    if index is None:
        if "[" in path and path.endswith("]"):
            path, i = path[:-1].split("[", 1)
            index = int(i)
        elif "." in path:
            head, tail = path.rsplit(".", 1)
            if tail in AXES:
                path, index = head, AXES[tail]
    return path, index


def target_key(target):
    p = getattr(target, "as_pointer", None)
    return p() if p else id(target)


class Track:
    def __init__(self, target, path, index=None):
        self.target, self.path, self.index = target, path, index
        self.initial = _get(target, path, index)
        self.segments = []

    def value(self, t):
        v = self.initial
        for s in self.segments:
            if t >= s.t1:
                v = s.v1
            elif t >= s.t0:
                v = s.value(t)
        return v

    def add(self, seg):
        self.segments.append(seg)
        self.segments.sort(key=lambda s: (s.t0, s.t1))

    def apply(self, t):
        _set(self.target, self.path, self.index, self.value(t))


class Tracks:
    """Every animated property of a shot."""

    def __init__(self):
        self._tracks = {}
        self.callbacks = []

    def track(self, target, path, index=None):
        path, index = split_path(path, index)
        key = (target_key(target), path, index)
        tr = self._tracks.get(key)
        if tr is None:
            tr = self._tracks[key] = Track(target, path, index)
        return tr

    def value(self, target, path, t, index=None):
        """A property's animated value at t (its current value if not animated)."""
        path, index = split_path(path, index)
        tr = self._tracks.get((target_key(target), path, index))
        return tr.value(t) if tr else _get(target, path, index)

    def tween(self, target, path, to, start, end, ease="inout", frm=None, index=None):
        tr = self.track(target, path, index)
        v0 = tr.value(start) if frm is None else frm
        if not isinstance(to, (int, float)):
            to = tuple(to)
            v0 = tuple(v0)
        tr.add(Segment(start, end, v0, to, ease))
        return tr

    def path(self, target, path, fn, start, end, ease="inout", index=None):
        """A segment that follows fn(u), u eased 0..1 (an orbit, a curve)."""
        tr = self.track(target, path, index)
        tr.add(Segment(start, end, fn(0.0), fn(1.0), ease, fn=fn))
        return tr

    def spline(self, target, path, keys, index=None):
        """Pass smoothly through keys [(t, value), ...]: a cubic through every
        key, flat at the ends and at turning points (Blender's auto-clamped
        Bezier), so a camera can glide through several framings."""
        latest = {}
        for t, v in keys:  # a repeated time keeps its last value
            latest[float(t)] = v if isinstance(v, (int, float)) else tuple(v)
        keys = sorted(latest.items())
        if len(keys) < 2:
            raise ValueError("a spline needs two keys or more")
        t0, tn = keys[0][0], keys[-1][0]
        tr = self.track(target, path, index)
        tr.add(Segment(t0, tn, keys[0][1], keys[-1][1], "linear", fn=lambda u: hermite(keys, t0 + u * (tn - t0))))
        return tr

    def set(self, target, path, value, at=0.0, index=None):
        """Step to `value` at time `at` and hold it."""
        tr = self.track(target, path, index)
        if not isinstance(value, (int, float, bool)):
            value = tuple(value)
        tr.add(Segment(at, at, value, value, "step"))
        return tr

    def apply(self, t, callbacks=True):
        for tr in self._tracks.values():
            tr.apply(t)
        if callbacks:
            for fn in self.callbacks:
                fn(t)

    def snapshot(self):
        """Every animated property's current value, for restore()."""
        return [(tr, _get(tr.target, tr.path, tr.index)) for tr in self._tracks.values()]

    def restore(self, snap):
        for tr, v in snap:
            _set(tr.target, tr.path, tr.index, v)

    def __len__(self):
        return len(self._tracks)


def _tangents(ts, vs):
    n = len(ts)
    out = []
    for i in range(n):
        if i == 0 or i == n - 1:
            out.append(0.0)
            continue
        a, b, c = vs[i - 1], vs[i], vs[i + 1]
        if (b - a) * (c - b) <= 0:
            out.append(0.0)  # a turning point stays flat
            continue
        d0 = (b - a) / (ts[i] - ts[i - 1])
        d1 = (c - b) / (ts[i + 1] - ts[i])
        m = (c - a) / (ts[i + 1] - ts[i - 1])
        lim = 3.0 * min(abs(d0), abs(d1))  # Fritsch-Carlson: no overshoot past a key
        out.append(max(-lim, min(lim, m)))
    return out


def hermite(keys, t):
    """Value at t of the auto-clamped cubic through keys [(t, value), ...]."""
    ts = [k[0] for k in keys]
    if t <= ts[0]:
        return keys[0][1]
    if t >= ts[-1]:
        return keys[-1][1]
    i = max(j for j in range(len(ts) - 1) if ts[j] <= t)
    h = ts[i + 1] - ts[i]
    s = (t - ts[i]) / h
    h00, h10 = 2 * s ** 3 - 3 * s ** 2 + 1, s ** 3 - 2 * s ** 2 + s
    h01, h11 = -2 * s ** 3 + 3 * s ** 2, s ** 3 - s ** 2

    def comp(vals):
        m = _tangents(ts, vals)
        return h00 * vals[i] + h10 * h * m[i] + h01 * vals[i + 1] + h11 * h * m[i + 1]

    first = keys[0][1]
    if isinstance(first, (int, float)):
        return comp([k[1] for k in keys])
    return tuple(comp([k[1][c] for k in keys]) for c in range(len(first)))
