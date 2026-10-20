"""Bond wires: thin gold loops from die edges down to pads, as one curve
object redrawn every frame from a plain state, so a shot can grow them,
make them sag, and let them snap and fall.

    from blenderkc.kit import bond_wires
    bw = bond_wires.bond_wires("bw", pairs, arc=0.12, r=0.003)
    bond_wires.drive(shot, bw)                      # redraw every frame
    shot.tween(bw.state, "shown", 1.0, 2.0, 3.0)    # wires appear in order
    shot.tween(bw.state, "sag", 1.0, 4.0, 5.0)      # loops droop
    shot.tween(bw.state, "fall", 1.0, 6.0, 7.0)     # they break at the die and drop
    shot.tween(bond_wires.socket(bw, "Base Color"), "default_value", (1, 0.1, 0.05, 1), 6, 6.3)

pairs is a list of (start, end) points in the part's local space, start on
the die's edge, end on the pad. Wires show in list order, so put the ones
that should appear first first. Each wire grows from its start as it shows.
"""
import math
import random

import bpy
from mathutils import Vector

from . import materials as M
from .geo import Part


class State:
    """What the wires look like now; tween its attributes."""

    def __init__(self):
        self.shown = 0.0   # fraction of the wires drawn (each grows over its own share)
        self.sag = 0.0     # 0: a crisp loop; 1: drooping below the straight line
        self.fall = 0.0    # 0: attached; 1: every wire lies on its pad's level
        self.arc = 0.12    # loop height above the start, in scene units


class BondWires(Part):
    pass


def bond_wires(name, pairs, arc=0.12, r=0.003, points=12, mat=None, droop=None, seed=7, loc=(0, 0, 0),
               parent=None):
    """Gold wires for `pairs` [(start, end), ...]. droop: how far a fully
    sagged wire hangs below its chord (default: a third of its length)."""
    p = BondWires(name, loc, parent)
    p.state = State()
    p.state.arc = arc
    p.pairs = [(Vector(a), Vector(b)) for a, b in pairs]
    p.npts = points
    p.droop = droop
    rnd = random.Random(seed)
    p.jitter = [(rnd.random(), rnd.uniform(0.85, 1.15), rnd.uniform(-1, 1)) for _ in p.pairs]
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = r
    cu.bevel_resolution = 1
    cu.use_fill_caps = False
    for _ in p.pairs:
        sp = cu.splines.new("POLY")
        sp.points.add(points - 1)
    p.material = mat or M.principled(f"{name}_gold", (1.0, 0.76, 0.33), metallic=1.0, rough=0.28,
                                     emission=(1.0, 0.7, 0.25), strength=0.0)
    cu.materials.append(p.material)
    o = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(o)
    o.parent = p.root
    p.add("wires", o)
    p.curve = cu
    redraw(p)
    if p.pairs:
        a, b = p.pairs[len(p.pairs) // 2]
        p.mark("mid", (a + b) / 2 + Vector((0, 0, arc)))
        p.mark("first", p.pairs[0][0])
    return p


def socket(p, name):
    """A Principled input of the wires' material, to tween ("Base Color", "Emission Strength")."""
    return M.socket(p.material, name)


def _shape(p, i, a, b, grow, sag, fall):
    st = p.state
    n = p.npts
    rj, sj, tw = p.jitter[i]
    chord = b - a
    length = chord.length or 1e-6
    droop = (p.droop if p.droop is not None else length / 3.0) * sj
    # each wire lets go a little after the last, so they rain down
    f = max(0.0, min(1.0, fall * 1.7 - rj * 0.7))
    f = f * f * (3 - 2 * f)
    rest_z = b.z
    out = []
    for k in range(n):
        u = grow * k / (n - 1)
        q = a + chord * u
        bump = math.sin(math.pi * u)
        lift = st.arc * sj * (1.0 - sag) * (bump + 0.6 * math.sin(math.pi * min(1.0, u * 1.6)) * (1 - u))
        q.z += lift - sag * droop * bump
        q.y += sag * 0.04 * tw * bump  # sagging wires wander a little and touch
        # falling: the die end drops to the pad's level, the wire lies down
        lie = Vector((q.x, q.y, rest_z + 0.004))
        lie.x += (a.x - b.x) * 0.15 * (1 - u) * tw
        q = q.lerp(lie, f)
        out.append(q)
    return out


def redraw(p):
    st = p.state
    total = len(p.pairs)
    flat = []
    for i, (a, b) in enumerate(p.pairs):
        # wire i grows over its own slot in `shown`
        g = max(0.0, min(1.0, st.shown * total - i)) if total else 0.0
        if g <= 0.0:
            pts = [a] * p.npts
        else:
            pts = _shape(p, i, a, b, g, st.sag, st.fall)
        for q in pts:
            flat.extend((q.x, q.y, q.z, 1.0))
    # one foreach_set per spline (points of different splines are not contiguous)
    n = p.npts * 4
    for i, sp in enumerate(p.curve.splines):
        sp.points.foreach_set("co", flat[i * n:(i + 1) * n])
    p.curve.update_tag()


def drive(shot, p):
    """Redraw the wires from their state every frame."""
    shot.on_frame(lambda t: redraw(p))
    return p
