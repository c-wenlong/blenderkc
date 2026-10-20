"""Easing curves over u in [0, 1]. Pure Python, no bpy.

A shot names an ease by string ("inout", "out", ...) or passes any callable
f(u) -> u'. Every curve maps 0 to 0 and 1 to 1.
"""
import math


def clamp01(u):
    return 0.0 if u < 0 else 1.0 if u > 1 else u


def linear(u):
    return clamp01(u)


def ease_in(u):
    u = clamp01(u)
    return u * u * u


def ease_out(u):
    u = 1.0 - clamp01(u)
    return 1.0 - u * u * u


def ease_in_out(u):
    """Cubic in-out: the default for camera moves."""
    u = clamp01(u)
    return 4 * u * u * u if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def smooth(u):
    """Smootherstep (Perlin): zero velocity and acceleration at both ends."""
    u = clamp01(u)
    return u * u * u * (u * (u * 6 - 15) + 10)


def expo_in_out(u):
    u = clamp01(u)
    if u in (0.0, 1.0):
        return u
    return 2 ** (20 * u - 10) / 2 if u < 0.5 else (2 - 2 ** (-20 * u + 10)) / 2


def back_out(u, s=1.4):
    """Overshoots a little and settles: for parts that pop into place."""
    u = clamp01(u) - 1
    return 1 + u * u * ((s + 1) * u + s)


def step(u):
    return 1.0 if u >= 1 else 0.0


def sine_in_out(u):
    return -(math.cos(math.pi * clamp01(u)) - 1) / 2


EASES = {
    "linear": linear,
    "in": ease_in,
    "out": ease_out,
    "inout": ease_in_out,
    "smooth": smooth,
    "expo": expo_in_out,
    "back": back_out,
    "step": step,
    "sine": sine_in_out,
}


def get(ease):
    """An ease by name or a callable, else a ValueError naming the choices."""
    if callable(ease):
        return ease
    if ease is None:
        return ease_in_out
    try:
        return EASES[ease]
    except KeyError:
        raise ValueError(f"unknown ease {ease!r}; use one of {', '.join(EASES)} or a function") from None


def progress(t, start, end, ease="inout"):
    """Eased progress 0..1 of time t through [start, end]."""
    if end <= start:
        return 1.0 if t >= start else 0.0
    return get(ease)((t - start) / (end - start))


def lerp(a, b, u):
    """Interpolate numbers or equal-length sequences."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * u
    a, b = tuple(a), tuple(b)
    if len(a) != len(b):
        raise ValueError(f"cannot interpolate {a!r} to {b!r}: lengths differ")
    return tuple(x + (y - x) * u for x, y in zip(a, b))
