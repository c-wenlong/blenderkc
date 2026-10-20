"""Wiring: single wires that glow on their own, the voltage on a wire drawn as
a lit trace above it, sample marks, and grids (bumps, balls) that light in a
sweep.

    from blenderkc.kit import wiring
    w = wiring.wire("data", (-2, 0, 0.1), (2, 0, 0.1))            # one wire, its own material
    wiring.pulses(shot, w.material, 1.0, 4.0, speed=2.0)          # pulses run down it
    wiring.flicker(shot, w.material, 2.0, 4.0, level=0.6)         # crosstalk noise on it
    tr = wiring.trace("v", (-2, 0, 0.6), length=4, bits=[0, 1, 0, 1, 0], period=0.8)
    shot.tween(tr.state, "edge", 0.3, 2.0, 3.0)                   # square edges become ramps
    m = wiring.sweep_material("bumps", wiring.COPPER)             # lights where the sweep has passed
    shot.tween(wiring.value(m, "sweep"), "default_value", 2.0, 1.0, 2.0, frm=-2.0)

Everything animates through tweens on value nodes or on a trace's `state`
(a plain object: shot.tween(trace.state, "period", ...) works like any
property), and a trace redraws itself every frame from that state.
"""
import bisect
import math
import random

import bpy
from mathutils import Vector

from . import geo
from . import materials as M
from .geo import Part, cylinder

GOLD = ((1.0, 0.76, 0.33), 1.0, 0.3)
COPPER = ((0.95, 0.55, 0.30), 1.0, 0.25)
GREEN = (0.25, 1.0, 0.4)
RED = (1.0, 0.12, 0.05)


def value(mat, key):
    """The output socket of a material's named value node, to tween."""
    return mat.node_tree.nodes[key].outputs[0]


def _op(nt, kind, a, b=None, clamp=False):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = kind
    n.use_clamp = clamp
    for i, s in enumerate((a, b)):
        if s is None:
            continue
        if isinstance(s, (int, float)):
            n.inputs[i].default_value = s
        else:
            nt.links.new(s, n.inputs[i])
    return n.outputs[0]


def _values(nt, pairs):
    out = {}
    for key, v in pairs:
        n = nt.nodes.new("ShaderNodeValue")
        n.name = n.label = key
        n.outputs[0].default_value = v
        out[key] = n.outputs[0]
    return out


def wire_material(name, base=GOLD[0], glow=GREEN, noise=RED, rough=0.3):
    """A gold wire whose emission is pulses running along local +x (the
    value nodes of parts.signal_material: phase, pulse, lit, density,
    strength) plus "noise": a second colour (red) for crosstalk."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    M._set(b, color=base, metallic=1.0, rough=rough)
    v = _values(nt, (("phase", 0.0), ("pulse", 0.0), ("lit", 0.0), ("density", 1.0), ("strength", 8.0),
                     ("noise", 0.0)))
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Object"], sep.inputs["Vector"])
    u = _op(nt, "SUBTRACT", _op(nt, "MULTIPLY", sep.outputs["X"], v["density"]), v["phase"])
    f = _op(nt, "FRACT", u)
    tri = _op(nt, "SUBTRACT", 1.0, _op(nt, "MULTIPLY", _op(nt, "ABSOLUTE", _op(nt, "SUBTRACT", f, 0.5)), 2.0))
    pulse = _op(nt, "MULTIPLY", _op(nt, "POWER", tri, 18.0), v["pulse"])
    good = _op(nt, "ADD", pulse, v["lit"])
    total = _op(nt, "ADD", good, v["noise"])
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["A"].default_value = (*glow, 1)
    mix.inputs["B"].default_value = (*noise, 1)
    nt.links.new(_op(nt, "DIVIDE", v["noise"], _op(nt, "ADD", total, 1e-4)), mix.inputs["Factor"])
    nt.links.new(mix.outputs["Result"], b.inputs["Emission Color"])
    nt.links.new(_op(nt, "MULTIPLY", total, v["strength"]), b.inputs["Emission Strength"])
    return m


class Wire(Part):
    """One wire (or a few sharing a material): wire.material, wire.wires."""


def wire(name, start, end, r=0.016, mat=None, n=1, spacing=0.1, parent=None, **mat_kw):
    """n straight wires from start to end (spaced in the horizontal across
    the run), sharing one wire_material. Like parts.wires, but the material
    also takes crosstalk noise, and the root sits at `start`, so tweening
    root "scale.x" from 0 to 1 draws the wires from start to end. Each wire
    is wire.wires[i] at local (L/2, y, 0); tween its "location.y" to fan."""
    a, b = Vector(start), Vector(end)
    L = (b - a).length
    p = Wire(name, tuple(a), parent)
    d = (b - a).normalized()
    p.root.rotation_euler = d.to_track_quat("X", "Z").to_euler()
    p.material = mat or wire_material(f"{name}_signal", **mat_kw)
    p.wires = []
    for i in range(n):
        y = (i - (n - 1) / 2) * spacing
        p.wires.append(cylinder(f"{name}_w{i}", r, L, (L / 2, y, 0), p.material, verts=12, axis="X", parent=p.root))
    p.length = L
    p.mark("start", (0, 0, 0))
    p.mark("end", (L, 0, 0))
    p.mark("middle", (L / 2, 0, 0))
    p.mark("side", (L / 2, -(n - 1) / 2 * spacing - r, 0))
    return p


def pulses(shot, mat, start, end, speed=3.0, spacing=1.5, brightness=1.0, fade=0.3, phase0=None):
    """Pulses run along a wire material from start to end (seconds), `speed`
    units a second, one every `spacing` units. Calls may follow one another
    on the same material (a faster rate later): the phase carries on."""
    shot.set(value(mat, "density"), "default_value", 1.0 / spacing, start)
    ph = value(mat, "phase")
    p0 = shot.value(ph, "default_value", start) if phase0 is None else phase0
    shot.tween(ph, "default_value", p0 + speed * (end - start) / spacing, start, end, "linear", frm=p0)
    shot.tween(value(mat, "pulse"), "default_value", brightness, start, start + fade, "linear")
    if end is not None and fade:
        shot.tween(value(mat, "pulse"), "default_value", 0.0, end - fade, end, "linear")
    return mat


def flicker(shot, mat, start, end, level=0.6, rate=14.0, seed=3, ramp=0.4):
    """Crosstalk: the material's red noise flickers from start to end,
    ramping up over `ramp` seconds, at about `rate` changes a second."""
    rnd = random.Random(seed)
    table = [rnd.random() ** 1.6 for _ in range(4096)]
    sock = value(mat, "noise")

    def update(t):
        if t < start or t > end:
            sock.default_value = 0.0
            return
        env = min(1.0, (t - start) / ramp, (end - t) / ramp) if ramp else 1.0
        k = t * rate
        i = int(k)
        u = k - i
        a, b = table[i % 4096], table[(i + 1) % 4096]
        sock.default_value = level * env * (0.25 + 0.75 * (a + (b - a) * u))

    shot.on_frame(update)
    return mat


# --- the voltage trace ---------------------------------------------------

class TraceState:
    """What a trace draws, all animatable with shot.tween(state, "<name>", ...).

    period  length of one bit along the run (scene units)
    edge    how long a change of level takes, in units along the run
            (0 = square edges; a period shorter than this never settles)
    phase   how far the bit pattern has moved along +x (units)
    reveal  how much of the run is drawn, 0..1 (the trace draws itself)
    amp     height between the lowest and highest level
    """

    def __init__(self, period, edge, amp):
        self.period = period
        self.edge = edge
        self.phase = 0.0
        self.reveal = 1.0
        self.amp = amp


class Trace(Part):
    """A lit line above a wire whose height is the voltage: trace.state to
    animate, trace.material (and rise/fall materials for the edges),
    trace.marks for sample marks. trace.x_of(i) is where bit i's middle is
    (before any phase). Points: start, end."""

    def level_at(self, x):
        return _levels(self, x)


def _bit(trace, i):
    bits = trace.bits
    return bits[i % len(bits)] if trace.repeat else bits[min(max(i, 0), len(bits) - 1)]


def _levels(trace, x_end, step=None):
    """Sample the slew-limited voltage from 0 to x_end: [(x, v, slope)], v in 0..1."""
    s = trace.state
    period = max(s.period, 1e-3)
    step = step or trace.length / trace.resolution
    edge = max(s.edge, 1e-6)
    # start two bits before the run at a bit boundary, settled there
    k0 = math.floor((0.0 - s.phase) / period) - 2
    x = k0 * period + s.phase
    v = float(_bit(trace, k0))
    out = []
    while x <= x_end + 1e-9:
        target = float(_bit(trace, math.floor((x - s.phase) / period)))
        dv = target - v
        lim = step / edge
        move = max(-lim, min(lim, dv))
        if x >= 0.0:
            out.append((x, v, move))
        v += move
        x += step
    return out


def trace(name, loc, length, bits, period=0.8, edge=0.0, amp=0.3, r=0.009, color=(0.92, 0.95, 1.0),
          strength=6.0, repeat=True, resolution=600, edges=False, rise_color=(1.0, 0.35, 0.05),
          fall_color=(0.1, 0.8, 1.0), parent=None):
    """A voltage trace: a lit line `length` long from loc along +x, at loc's
    height for a 0 and amp higher for a 1 (levels between 0 and 1 work too,
    e.g. [0, 0.5, 1] for PAM3). bits repeat when repeat=True. edges=True adds
    two more curves on the same path that only cover rising and falling
    edges, with their own materials (trace.rise, trace.fall), to flash them."""
    p = Trace(name, loc, parent)
    p.state = TraceState(period, edge, amp)
    p.bits = list(bits)
    p.repeat = repeat
    p.length = length
    p.resolution = resolution
    p.material = M.emission(f"{name}_trace", color, strength)
    p.curves = {}

    def make(key, mat):
        cu = bpy.data.curves.new(f"{name}_{key}", "CURVE")
        cu.dimensions = "3D"
        cu.bevel_depth = r
        cu.bevel_resolution = 2
        cu.use_fill_caps = True
        o = bpy.data.objects.new(f"{name}_{key}", cu)
        cu.materials.append(mat)
        geo.link(o, p.root)
        o.visible_shadow = False
        p.curves[key] = o
        return o

    p.add("line", make("line", p.material))
    if edges:
        # the edge curves look like the line until flashed (wiring.flash)
        p.rise = M.emission(f"{name}_rise", color, strength)
        p.fall = M.emission(f"{name}_fall", color, strength)
        p.rise_color, p.fall_color = rise_color, fall_color
        p.add("rise", make("rise", p.rise))
        p.add("fall", make("fall", p.fall))
        # sit the edge curves a hair in front of the line, a little thicker
        for k in ("rise", "fall"):
            p.curves[k].data.bevel_depth = r * 1.35
    p.mark("start", (0, 0, 0))
    p.mark("end", (length, 0, 0))
    p.mark("top", (length / 2, 0, amp))
    p.marks = None
    _redraw(p)
    return p


def _set_splines(cu, runs, s):
    cu.splines.clear()
    for run in runs:
        if len(run) < 2:
            continue
        sp = cu.splines.new("POLY")
        sp.points.add(len(run) - 1)
        co = []
        for x, v in run:
            co.extend((x, 0.0, v * s.amp, 1.0))
        sp.points.foreach_set("co", co)


def _redraw(p):
    s = p.state
    end = p.length * max(0.0, min(1.0, s.reveal))
    if end <= 1e-4:
        for o in p.curves.values():
            o.data.splines.clear()
        if p.marks is not None:  # no trace, no marks either
            lines, dots, count = p.marks
            far = (0.0, 0.0, -1000.0) * count
            for o in (lines, dots):
                o.data.vertices.foreach_set("co", far)
                o.data.update()
        return
    samples = _levels(p, end)
    _set_splines(p.curves["line"].data, [[(x, v) for x, v, _ in samples]], s)
    if "rise" in p.curves:
        for key, sign in (("rise", 1), ("fall", -1)):
            runs, cur = [], []
            for x, v, d in samples:
                if d * sign > 1e-9:
                    cur.append((x, v))
                else:
                    if cur:
                        cur.append((x, v))
                        runs.append(cur)
                    cur = []
            if cur:
                runs.append(cur)
            _set_splines(p.curves[key].data, runs, s)
    if p.marks is not None:
        _place_marks(p, samples)


def flash(shot, mat, color, start, hold, end, strength=10.0):
    """Light an emission material in `color` at `strength` from start (0.25 s
    in), hold until `hold`, and ease back to its own colour and strength by
    `end`: a trace's rise or fall edges, say."""
    em = next(n for n in mat.node_tree.nodes if n.type == "EMISSION")
    c0 = tuple(em.inputs["Color"].default_value)
    s0 = em.inputs["Strength"].default_value
    shot.tween(em.inputs["Color"], "default_value", (*color, 1.0), start, start + 0.25, "out")
    shot.tween(em.inputs["Strength"], "default_value", strength, start, start + 0.25, "out")
    shot.tween(em.inputs["Color"], "default_value", c0, hold, end, "inout")
    shot.tween(em.inputs["Strength"], "default_value", s0, hold, end, "inout")
    return mat


def drive(shot, tr):
    """Redraw the trace every frame from its state (call once, after making it)."""
    shot.on_frame(lambda t: _redraw(tr))
    return tr


def sample_marks(tr, count=24, r=0.004, dot_r=0.02, height=None, color=(0.85, 0.9, 1.0), strength=0.0,
                 dot_color=(1.0, 0.85, 0.2)):
    """Dashed-looking vertical lines at the middle of each bit's settled
    part, with a dot where the trace crosses them; they move with the trace.
    Both materials start dark: tr.mark_material, tr.dot_material."""
    h = height or tr.state.amp * 1.6
    tr.mark_material = M.emission(f"{tr.name}_marks", color, strength)
    tr.dot_material = M.emission(f"{tr.name}_dots", dot_color, strength)
    line_src = geo.source(f"_{tr.name}_mark", lambda n: geo.bake(cylinder(n, r, h, (0, 0, h / 2 - h * 0.2),
                                                                           tr.mark_material, verts=8, cap=False)))
    dot_src = geo.source(f"_{tr.name}_dot", lambda n: geo.sphere(n, dot_r, mat=tr.dot_material, segments=12,
                                                                  rings=6))
    tr.marks = (geo.instance_points(f"{tr.name}_marklines", line_src, [(0, 0, -1000)] * count, parent=tr.root),
                geo.instance_points(f"{tr.name}_markdots", dot_src, [(0, 0, -1000)] * count, parent=tr.root),
                count)
    for o in tr.marks[:2]:
        o.visible_shadow = False
    _redraw(tr)
    return tr


def _place_marks(tr, samples):
    s = tr.state
    lines, dots, count = tr.marks
    period = max(s.period, 1e-3)
    end = tr.length * max(0.0, min(1.0, s.reveal))
    k = math.floor(-s.phase / period) - 1
    xs = [q[0] for q in samples]
    lco, dco = [], []
    while len(lco) < 3 * count:
        x0 = k * period + s.phase
        k += 1
        # the middle of the settled part: after the edge, halfway to the next bit
        x = x0 + min(s.edge, period) + max(0.0, period - s.edge) / 2
        if x < 0:
            continue
        if x > end or s.edge >= period * 0.98:
            break
        i = bisect.bisect_right(xs, x) - 1   # the redraw's own samples, not a second pass
        v = samples[max(i, 0)][1] if samples else 0.0
        lco.extend((x, 0.0, 0.0))
        dco.extend((x, 0.0, v * s.amp))
    far = (0.0, 0.0, -1000.0) * (count - len(lco) // 3)
    lines.data.vertices.foreach_set("co", (lco + list(far))[:3 * count])
    dots.data.vertices.foreach_set("co", (dco + list(far))[:3 * count])
    lines.data.update()
    dots.data.update()


# --- grids that light in a sweep -----------------------------------------

def sweep_material(name, look=COPPER, glow=(1.0, 0.55, 0.2), axis=(1.0, 0.0, 0.0), width=0.3, strength=6.0):
    """A metal (look = (color, metallic, rough)) that lights where a sweep
    has passed: value nodes "sweep" (position along `axis`, world units),
    "band" (brightness of the moving front), "hold" (brightness behind it),
    "width" (the front's width) and "strength". Tween "sweep" across."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    color, metal, rough = look
    m, nt, b = M.node_mat(name)
    M._set(b, color=color, metallic=metal, rough=rough, emission=glow)
    v = _values(nt, (("sweep", -1e4), ("band", 1.0), ("hold", 0.25), ("width", width), ("strength", strength)))
    g = nt.nodes.new("ShaderNodeNewGeometry")
    dot = nt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = Vector(axis).normalized()
    nt.links.new(g.outputs["Position"], dot.inputs[0])
    d = _op(nt, "SUBTRACT", v["sweep"], dot.outputs["Value"])  # > 0 behind the front
    passed = _op(nt, "DIVIDE", d, v["width"])
    behind = _op(nt, "MINIMUM", _op(nt, "MAXIMUM", passed, 0.0), 1.0)
    front = _op(nt, "MAXIMUM", _op(nt, "SUBTRACT", 1.0, _op(nt, "ABSOLUTE", passed)), 0.0)
    e = _op(nt, "ADD", _op(nt, "MULTIPLY", behind, v["hold"]), _op(nt, "MULTIPLY", front, v["band"]))
    nt.links.new(_op(nt, "MULTIPLY", e, v["strength"]), b.inputs["Emission Strength"])
    return m


def sweep(shot, mat, start, end, frm, to, ease="linear"):
    """Move a sweep material's front from `frm` to `to` (world units along its axis)."""
    shot.tween(value(mat, "sweep"), "default_value", to, start, end, ease, frm=frm)
    return mat


def ball_grid(name, blocks, pitch, r, mat=None, loc=(0, 0, 0), parent=None, segments=10, rings=6):
    """Solder balls in rectangular blocks [(nx, ny, cx, cy), ...] (block
    centres in local units), instanced: a BGA footprint with a gap between
    its halves. Returns (object, count)."""
    pts = []
    for nx, ny, cx, cy in blocks:
        pts.extend(geo.grid_points(nx, ny, pitch, pitch, (cx, cy, 0)))
    src = geo.source(f"_ballg_{name}", lambda n: geo.bake(geo.sphere(n, r, (0, 0, -r * 0.75), mat or M.solder(),
                                                                     segments=segments, rings=rings)))
    return geo.instance_points(name, src, pts, loc, parent), len(pts)


def bump_field(name, groups, pitch, r=0.012, h=0.02, loc=(0, 0, 0), parent=None, down=False):
    """Microbumps (copper pillars with solder caps) on a grid, split into
    groups that each have their own pillar material: groups is
    {key: (material, [(i, j), ...])} with (i, j) grid indices centred on loc.
    down=True hangs them below loc (the underside of a die). Returns
    {key: object}."""
    out = {}
    sgn = -1.0 if down else 1.0
    cap = M.solder()
    for key, (mat, cells) in groups.items():
        def make(n, mat=mat):
            pillar = geo.bake(cylinder(n, r, h * 0.6, (0, 0, sgn * h * 0.3), mat, verts=10))
            return pillar

        src = geo.source(f"_bf_{name}_{key}", make)
        pts = [(i * pitch, j * pitch, 0.0) for i, j in cells]
        out[key] = geo.instance_points(f"{name}_{key}", src, pts, loc, parent)
        tip = geo.source(f"_bf_{name}_cap", lambda n: geo.bake(geo.sphere(n, r * 1.05, (0, 0, 0), cap, segments=10,
                                                                           rings=6), scale=(1, 1, 0.7)))
        geo.instance_points(f"{name}_{key}_caps", tip, [(x, y, sgn * h * 0.62) for x, y, _ in pts], parent=out[key])
    return out


# --- flat ribbons of many wires ------------------------------------------

def fan(name, start, end, n, w0, w1, strip=None, mat=None, parent=None, **mat_kw):
    """n flat wires as thin strips (one mesh, any n is cheap) from a segment
    `w0` wide centred on `start` to one `w1` wide centred on `end`: a ribbon
    that spreads (w1 > w0) or gathers. Strips lie flat in the run's plane
    and share one wire_material (pulses run from start to end). The root
    sits at `start`: tween its "scale.x" from 0 to 1 to draw it on. strip:
    each strip's width (default: half the pitch at the narrow end)."""
    a, b = Vector(start), Vector(end)
    L = (b - a).length
    p = Wire(name, tuple(a), parent)
    d = (b - a).normalized()
    p.root.rotation_euler = d.to_track_quat("X", "Z").to_euler()
    p.material = mat or wire_material(f"{name}_signal", **mat_kw)
    sw = strip or 0.5 * min(w0, w1) / max(n, 1)
    verts, faces = [], []
    for i in range(n):
        u = (i + 0.5) / n - 0.5
        y0, y1 = u * w0, u * w1
        k = len(verts)
        verts += [(0, y0 - sw / 2, 0), (L, y1 - sw / 2, 0), (L, y1 + sw / 2, 0), (0, y0 + sw / 2, 0)]
        faces.append((k, k + 1, k + 2, k + 3))
    p.sheet = geo.mesh(f"{name}_strips", verts, faces, mat=p.material, parent=p.root)
    p.wires = [p.sheet]
    p.length = L
    p.mark("start", (0, 0, 0))
    p.mark("end", (L, 0, 0))
    p.mark("middle", (L / 2, 0, 0))
    p.mark("side", (L * 0.6, -w1 * 0.3 - w0 * 0.2, 0))
    return p
