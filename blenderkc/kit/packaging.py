"""How an ordinary chip is packaged: a bare die, the organic substrate it is
flipped onto, the substrate's fan-out in cross section, and the subtractive
process that makes the substrate's copper wiring (laminate, photoresist,
mask, UV, develop, etch, laser vias).

    from blenderkc.kit import packaging as pk
    die = pk.bare_die("die", (0, 0, 0.8))           # pads along the centre
    die.crack(shot, 9.9, 10.6, 13.8, 14.6)          # a crack runs and heals
    sub = pk.package_substrate("sub", (0, 0, 0.3))
    sec = pk.fanout_section("sec", (0, 0, 0.4))      # cut open, seen from -y
    sec.light_layers(shot, 2.0); sec.send_signals(shot, 5.9, 7.4)
    board = pk.laminate_board("board", (0, 0, 0.6))  # core, copper, resist
    board.roll_resist(shot, 1, 3); board.expose(shot, 7.9, 9.5); ...

Every animated look is a "knob": a Value node in a material, tweened with
`knob(mat, name)` -> socket (shot.tween(sock, "default_value", to, a, b)).
Sizes are scene units; the defaults suit grey_studio(scale=0.6).
"""
import math
import random

import bpy
from mathutils import Vector

from . import geo
from . import materials as M
from . import textures
from .geo import Part, box, cylinder

# --- node helpers --------------------------------------------------------


def knob(mat, name):
    """The output socket of a material's Value node `name`, to tween."""
    node = mat.node_tree.nodes.get(name)
    if node is None:
        raise KeyError(f"material {mat.name} has no knob {name!r}")
    return node.outputs[0]


def turn(shot, mat, name, to, start, end, ease="inout"):
    """Tween a material knob from its value at `start` to `to`."""
    shot.tween(knob(mat, name), "default_value", float(to), start, end, ease)


def _value(nt, name, v):
    n = nt.nodes.new("ShaderNodeValue")
    n.name = n.label = name
    n.outputs[0].default_value = float(v)
    return n.outputs[0]


def _math(nt, op, a, b=None, clamp=False):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    n.use_clamp = clamp
    for i, s in enumerate((a, b)):
        if s is None:
            continue
        if isinstance(s, (int, float)):
            n.inputs[i].default_value = float(s)
        else:
            nt.links.new(s, n.inputs[i])
    return n.outputs[0]


def _ramp(nt, x, lo, hi):
    """Smoothstep of x from lo to hi (0..1)."""
    n = nt.nodes.new("ShaderNodeMapRange")
    n.interpolation_type = "SMOOTHSTEP"
    n.inputs["From Min"].default_value = lo
    n.inputs["From Max"].default_value = hi
    nt.links.new(x, n.inputs["Value"])
    return n.outputs["Result"]


def _ramp_s(nt, x, lo, hi):
    """Smoothstep with socket bounds."""
    n = nt.nodes.new("ShaderNodeMapRange")
    n.interpolation_type = "SMOOTHSTEP"
    for key, v in (("From Min", lo), ("From Max", hi)):
        if isinstance(v, (int, float)):
            n.inputs[key].default_value = v
        else:
            nt.links.new(v, n.inputs[key])
    nt.links.new(x, n.inputs["Value"])
    return n.outputs["Result"]


def _mix_rgb(nt, fac, a, b):
    n = nt.nodes.new("ShaderNodeMix")
    n.data_type = "RGBA"
    for key, v in (("A", a), ("B", b)):
        if isinstance(v, tuple):
            n.inputs[key].default_value = (*v[:3], 1.0)
        else:
            nt.links.new(v, n.inputs[key])
    if isinstance(fac, (int, float)):
        n.inputs["Factor"].default_value = fac
    else:
        nt.links.new(fac, n.inputs["Factor"])
    return n.outputs["Result"]


def _generated(nt):
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Generated"], sep.inputs["Vector"])
    return tc, sep


def _noise(nt, vec, scale, detail=3.0):
    n = nt.nodes.new("ShaderNodeTexNoise")
    n.inputs["Scale"].default_value = scale
    n.inputs["Detail"].default_value = detail
    nt.links.new(vec, n.inputs["Vector"])
    return n.outputs["Fac"]


def _pattern(nt, img, vec):
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Cubic"
    tex.extension = "REPEAT"
    nt.links.new(vec, tex.inputs["Vector"])
    return tex.outputs["Color"]


def _bsdf(mat):
    return mat.node_tree.nodes["Principled BSDF"]


# --- the circuit pattern ---------------------------------------------------


def circuit_image(seed=21, size=1024, grow=3):
    """The PCB routing pattern of kit.textures, its traces thickened by
    `grow` pixels (white wiring on black), as a packed Blender image. One
    pattern serves the mask, the resist and the etched copper, so they line
    up exactly."""
    name = f"bkc_circuit_{seed}_{size}_{grow}"
    im = bpy.data.images.get(name)
    if im:
        return im
    from PIL import ImageFilter
    import numpy as np

    pil = textures._draw_traces(seed, size)
    if grow > 1:
        pil = pil.filter(ImageFilter.MaxFilter(grow if grow % 2 else grow + 1))
    pil = pil.filter(ImageFilter.GaussianBlur(0.8))
    a = np.asarray(pil, dtype=np.float32)[::-1] / 255.0
    rgba = np.empty((size, size, 4), dtype=np.float32)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = a
    rgba[..., 3] = 1.0
    im = bpy.data.images.new(name, size, size, alpha=False, float_buffer=False)
    im.colorspace_settings.name = "Non-Color"
    im.pixels.foreach_set(rgba.ravel())
    im.pack()
    return im


def _pattern_vec(nt, tiles=1.0, offset=(0.0, 0.0)):
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (tiles, tiles, 1.0)
    mp.inputs["Location"].default_value = (offset[0], offset[1], 0.0)
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    return mp.outputs["Vector"], tc


# --- materials ------------------------------------------------------------


def die_material(name="die_cells", color=(0.42, 0.55, 0.06), banks=(4, 6)):
    """A DRAM die seen up close: banks of memory cells (fine rows) in a grid,
    with darker periphery between them, yellow-green and glossy. Knob "lit"
    (0..1) brightens the cell arrays."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    tc, sep = _generated(nt)
    # banks: a grid over the generated 0..1 square, with gaps
    gx = _math(nt, "FRACT", _math(nt, "MULTIPLY", sep.outputs["X"], banks[0]))
    gy = _math(nt, "FRACT", _math(nt, "MULTIPLY", sep.outputs["Y"], banks[1]))
    inx = _math(nt, "MULTIPLY", _ramp(nt, gx, 0.025, 0.04), _ramp(nt, _math(nt, "SUBTRACT", 1.0, gx), 0.025, 0.04))
    iny = _math(nt, "MULTIPLY", _ramp(nt, gy, 0.02, 0.032), _ramp(nt, _math(nt, "SUBTRACT", 1.0, gy), 0.02, 0.032))
    bank = _math(nt, "MULTIPLY", inx, iny)
    # fine cell rows inside a bank
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = "Y"
    wave.inputs["Scale"].default_value = 70.0
    wave.inputs["Distortion"].default_value = 0.0
    nt.links.new(tc.outputs["Generated"], wave.inputs["Vector"])
    rows = _math(nt, "MULTIPLY", wave.outputs["Fac"], 0.35)
    cell = _mix_rgb(nt, rows, color, tuple(c * 0.78 for c in color))
    gap = (0.25, 0.3, 0.12)
    col = _mix_rgb(nt, bank, gap, cell)
    # only the top face carries circuits; the sides and the back are bare silicon
    nsep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Normal"], nsep.inputs["Vector"])
    face = _ramp(nt, nsep.outputs["Z"], 0.6, 0.8)
    col = _mix_rgb(nt, face, (0.2, 0.2, 0.24), col)
    bank = _math(nt, "MULTIPLY", bank, face)
    nt.links.new(col, b.inputs["Base Color"])
    nt.links.new(col, b.inputs["Emission Color"])
    lit = _value(nt, "lit", 0.0)
    nt.links.new(_math(nt, "MULTIPLY", _math(nt, "MULTIPLY", lit, bank), 0.8), b.inputs["Emission Strength"])
    M._set(b, metallic=0.3, rough=0.2, coat=0.7, coat_rough=0.04)
    return m


def process_studio(shot=None, scale=0.6, tile=0.12, dark=0.05, light=0.066, key=300.0, top=170.0, rim=150.0,
                   hdri_strength=0.3):
    """The grey studio of the original's process shots: rigs.grey_studio's
    seamless sweep, but in mid-dark grey with small tiles (`tile` units),
    a little darker overall so glossy parts and glows read."""
    from . import rigs

    r = rigs.grey_studio(shot, scale=scale, key=key, top=top, rim=rim, hdri_strength=hdri_strength)
    floor = M.floor_tiles("process_floor", scale=1.0 / (tile * 2), dark=dark, light=light)
    r.floor.data.materials[0] = floor
    return r


def glint_metal(name, color=(0.86, 0.86, 0.88), rough=0.2, glow=(1.0, 0.95, 0.75)):
    """A bright metal with a knob "glint" (emission strength)."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    M._set(b, color=color, metallic=1.0, rough=rough, emission=glow)
    nt.links.new(_value(nt, "glint", 0.0), b.inputs["Emission Strength"])
    return m


def laminate(name="laminate", color=(0.55, 0.6, 0.5)):
    """The pressed board: glass cloth set in pale green-grey resin."""
    return M.glass_fibre(name, color=color, weave=22.0, resin=(0.78, 0.84, 0.7))


def glow_copper(name, glow=(1.0, 0.5, 0.18)):
    """Copper with a knob "lit" for its emission (a copper layer lighting up)."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    M._set(b, color=(0.93, 0.52, 0.36), metallic=1.0, rough=0.25, emission=glow)
    nt.links.new(_math(nt, "MULTIPLY", _value(nt, "lit", 0.0), 2.2), b.inputs["Emission Strength"])
    return m


def ghost(name, color, metallic=0.0, rough=0.4, coat=0.0, glow=(1.0, 0.6, 0.15)):
    """A plain surface with knobs "alpha" (1 opaque) and "lit" (emission)."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    M._set(b, color=color, metallic=metallic, rough=rough, coat=coat, emission=glow)
    nt.links.new(_value(nt, "alpha", 1.0), b.inputs["Alpha"])
    nt.links.new(_math(nt, "MULTIPLY", _value(nt, "lit", 0.0), 6.0), b.inputs["Emission Strength"])
    return m


def front_glow(name, color=(0.25, 1.0, 0.4), base=(0.93, 0.52, 0.36), z_top=1.0, height=1.0, band=0.06,
               strength=1.6):
    """Copper whose glow sweeps down in world z: knob "front" from 0 (nothing
    lit) to 1 (lit from z_top down through `height`), knob "lit" scales it."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    M._set(b, color=base, metallic=1.0, rough=0.25, emission=color)
    geo_n = nt.nodes.new("ShaderNodeNewGeometry")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(geo_n.outputs["Position"], sep.inputs["Vector"])
    front = _value(nt, "front", 0.0)
    # depth below the top, as a fraction of the height (0 at the top)
    depth = _math(nt, "DIVIDE", _math(nt, "SUBTRACT", z_top, sep.outputs["Z"]), height)
    edge = _math(nt, "SUBTRACT", _math(nt, "MULTIPLY", front, 1.0 + band), depth)
    on = _ramp(nt, edge, 0.0, band)
    lit = _value(nt, "lit", 1.0)
    nt.links.new(_math(nt, "MULTIPLY", _math(nt, "MULTIPLY", on, lit), strength), b.inputs["Emission Strength"])
    return m


# --- the bare die ---------------------------------------------------------


class Die(Part):
    """A bare die; root at its centre so it can flip in place.

    die.crack(shot, run, ran, heal, healed)   a hairline crack runs across a
                                              corner, then heals in reverse
    die.glint(shot, start, end, strength)     the pads flash
    die.light(shot, start, end, level)        the cell grid brightens
    """

    def crack(self, shot, start, end, heal_start, heal_end):
        segs = self.crack_segments
        n = len(segs)
        for k, s in enumerate(segs):
            on = start + (end - start) * k / n
            off = heal_end - (heal_end - heal_start) * k / n
            shot.show(s, on, off)
        return self

    def glint(self, shot, start, end, strength=3.0):
        mid = (start + end) / 2
        turn(shot, self.pad_mat, "glint", strength, start, mid, "out")
        turn(shot, self.pad_mat, "glint", 0.0, mid, end, "in")
        return self

    def light(self, shot, start, end, level=0.35):
        turn(shot, self.cell_mat, "lit", level, start, end)
        return self


def _crack_path(rnd, a, b, n):
    pts = [Vector(a)]
    for k in range(1, n):
        u = k / n
        p = Vector(a).lerp(Vector(b), u)
        side = Vector((-(b[1] - a[1]), b[0] - a[0], 0)).normalized()
        pts.append(p + side * rnd.uniform(-0.035, 0.035))
    pts.append(Vector(b))
    return pts


def bare_die(name="die", loc=(0, 0, 0), size=(1.5, 1.9, 0.07), pads_per_row=24, pad=0.034, row_gap=0.07,
             bumps=False, bump_r=0.026, seed=3, parent=None):
    """A bare DRAM die: the cell grid on top and a double row of contact pads
    down its centre (along y), as DRAM dies have. bumps=True puts a solder
    bump on every pad (ready to flip). The root is the die's centre.
    Points: top, pads (the near end of the pad row), corner, edge, face."""
    p = Die(name, loc, parent)
    w, d, t = size
    p.size = size
    p.cell_mat = die_material(f"{name}_cells")
    p.add("body", box(f"{name}_body", size, (0, 0, 0), p.cell_mat, 0.004, parent=p.root))
    p.pad_mat = glint_metal(f"{name}_pads", (0.82, 0.82, 0.85), 0.18)
    span = d * 0.82
    pitch = span / (pads_per_row - 1)
    pts = [(sx * row_gap / 2, -span / 2 + j * pitch, t / 2 + 0.002) for sx in (-1, 1) for j in range(pads_per_row)]
    p.pad_points = pts
    src = geo.source(f"_pad_{name}", lambda n: geo.bake(box(n, (pad, pad * 1.3, 0.004), (0, 0, 0), p.pad_mat, 0.0008)))
    p.add("pads", geo.instance_points(f"{name}_pads", src, pts, parent=p.root))
    if bumps:
        bsrc = geo.source(f"_bump_{name}", lambda n: geo.bake(
            geo.sphere(n, bump_r, (0, 0, 0), M.solder(), segments=14, rings=8), scale=(1, 1, 0.8)))
        p.add("bumps", geo.instance_points(f"{name}_bumps", bsrc, [(x, y, z + bump_r * 0.55) for x, y, z in pts],
                                           parent=p.root))
    # the crack: flat dark segments across the near-right corner, hidden until crack()
    rnd = random.Random(seed)
    a = (w / 2 - 0.42, -d / 2 - 0.001, t / 2 + 0.0012)
    b = (w / 2 + 0.001, -d / 2 + 0.5, t / 2 + 0.0012)
    path = _crack_path(rnd, a, b, 9)
    cm = M.principled(f"{name}_crack", (0.012, 0.014, 0.01), rough=0.6)
    p.crack_segments = []
    for k, (u, v) in enumerate(zip(path, path[1:])):
        dv = v - u
        L = dv.length
        o = box(f"{name}_crack{k}", (L + 0.004, 0.009, 0.001), tuple((u + v) / 2), cm, 0, parent=p.root)
        o.rotation_euler.z = math.atan2(dv.y, dv.x)
        o.hide_render = True
        p.crack_segments.append(o)
    p.mark("top", (0, 0, t / 2))
    p.mark("pads", (row_gap / 2, -span / 2 + 2 * pitch, t / 2))
    p.mark("corner", (w / 2 - 0.15, -d / 2 + 0.2, t / 2))
    p.mark("edge", (w / 2, -d / 2, 0))
    p.mark("face", (0, -d / 2, 0))
    return p


# --- the substrate it is flipped onto ---------------------------------------


def package_substrate(name="substrate", loc=(0, 0, 0), size=(2.6, 3.0), t=0.16, pad_points=(), pad=0.05,
                      parent=None):
    """An organic package substrate: a glass-fibre core (its weave shows on
    the edges) between green solder mask, with gold landing pads on top at
    `pad_points` (x, y). loc is the bottom centre. Points: top, edge, pads."""
    p = Part(name, loc, parent)
    w, d = size
    mt = 0.018
    core_t = t - 2 * mt
    p.add("core", box(f"{name}_core", (w, d, core_t), (0, 0, mt + core_t / 2),
                      M.glass_fibre("substrate_core", color=(0.6, 0.6, 0.52), weave=26.0, resin=(0.82, 0.8, 0.55)),
                      0.006, parent=p.root))
    mask = M.solder_mask("substrate_mask", (0.03, 0.12, 0.06), traces=True, seed=9, scale=5.0)
    p.add("top", box(f"{name}_top", (w, d, mt), (0, 0, t - mt / 2), mask, 0.006, parent=p.root))
    p.add("bottom", box(f"{name}_bottom", (w, d, mt), (0, 0, mt / 2), mask, 0.006, parent=p.root))
    if pad_points:
        src = geo.source(f"_spad_{name}", lambda n: geo.bake(box(n, (pad, pad, 0.006), (0, 0, 0), M.gold(), 0.001)))
        p.add("pads", geo.instance_points(f"{name}_pads", src, [(x, y, t + 0.002) for x, y in pad_points],
                                          parent=p.root))
    p.height = t
    p.mark("top", (0, 0, t))
    p.mark("edge", (w / 2, -d / 2, t / 2))
    p.mark("corner", (-w / 2 + 0.2, -d / 2, t))
    return p


# --- the substrate cut open: fan-out ----------------------------------------


class Section(Part):
    """fanout_section: an organic substrate cut through, a die flipped onto it.

    sec.light_layers(shot, start, step)   the copper layers glow one by one
    sec.send_signals(shot, start, end)    signals run from the die down to the balls
    sec.pop_balls(shot, start, step)      solder balls pop on, a row at a time
    sec.cap(shot, start, end)             a mould cap closes over the die
    """

    def light_layers(self, shot, start, step=0.35, hold=1.2, level=1.0):
        for k, mat in enumerate(reversed(self.layer_mats)):  # top layer first
            t = start + k * step
            turn(shot, mat, "lit", level, t, t + 0.3)
            turn(shot, mat, "lit", 0.0, t + 0.3 + hold, t + 0.9 + hold)
        return self

    def send_signals(self, shot, start, end, fade_to=0.45, fade_at=None, fade=1.0):
        turn(shot, self.route_mat, "front", 1.0, start, end, "linear")
        if fade_at is not None:
            turn(shot, self.route_mat, "lit", fade_to, fade_at, fade_at + fade)
        return self

    def pop_balls(self, shot, start, step=0.18, grow=0.35):
        for k, row in enumerate(self.ball_rows):
            t = start + k * step
            for o in row:
                shot.show(o, t)
                shot.tween(o, "scale", (1.0, 1.0, 1.0), t, t + grow, "back", frm=(0.01, 0.01, 0.01))
        return self

    def cap(self, shot, start, end, drop=1.2):
        o = self["mould"]
        z = o.location.z
        shot.show(o, start)
        shot.tween(o, "location.z", z, start, end, "out", frm=z + drop)
        return self


def fanout_section(name="section", loc=(0, 0, 0), width=2.6, depth=1.5, signals=8, pad_pitch=0.13,
                   ball_pitch=0.3, ball_rows=4, ball_r=0.1, die_size=(1.5, 0.07), parent=None):
    """An organic substrate with a flipped die, cut through at y=0 and seen
    from -y (the body runs from y=0 back to y=depth). Four copper layers in
    build-up film around a glass-fibre core; `signals` routes run from the
    die's bumps (pad_pitch apart) down through microvias, the core's plated
    holes and the layers to balls ball_pitch apart. loc is the bottom centre
    of the cut face. Points: die, layers, routes, balls, top."""
    p = Section(name, loc, parent)
    w = width
    diel = M.resin("section_film", (0.36, 0.4, 0.22))
    core = M.glass_fibre("section_core", color=(0.55, 0.56, 0.5), weave=32.0, resin=(0.82, 0.8, 0.55))
    mask = M.solder_mask("section_mask", (0.03, 0.12, 0.06), traces=False)
    stack = [("mask", 0.03), ("cu", 0.026), ("diel", 0.08), ("cu", 0.026), ("core", 0.2), ("cu", 0.026),
             ("diel", 0.08), ("cu", 0.026), ("mask", 0.03)]
    height = sum(t for _, t in stack)
    p.height = height
    # each copper layer: film fill plus traces in its own glowing copper
    p.layer_mats = []
    xs_top = [(i - (signals - 1) / 2) * pad_pitch for i in range(signals)]
    xs_bot = [(i - (signals - 1) / 2) * ball_pitch for i in range(signals)]
    xs_mid = [(a + b) / 2 for a, b in zip(xs_top, xs_bot)]
    route_mat = front_glow(f"{name}_route", z_top=loc[2] + height + 0.06, height=height + 0.06 + ball_r * 2)
    p.route_mat = route_mat
    layer_z = []
    z = 0.0
    rnd = random.Random(7)
    cu_index = 0
    for kind, t in stack:
        zc = z + t / 2
        if kind == "cu":
            layer_z.append(zc)
            box(f"{name}_film{cu_index}", (w, depth, t), (0, depth / 2, zc), diel, 0, parent=p.root)
            mat = glow_copper(f"{name}_cu{cu_index}")
            p.layer_mats.append(mat)
            # filler traces where no route crosses this layer
            keep = {0: xs_top, 1: xs_top + xs_mid, 2: xs_mid + xs_bot, 3: xs_bot}[cu_index]
            span = {1: list(zip(xs_top, xs_mid)), 2: list(zip(xs_mid, xs_bot))}.get(cu_index, [])
            x = -w / 2 + 0.03
            while x < w / 2 - 0.05:
                tw = rnd.choice([0.05, 0.07, 0.1, 0.16])
                lo, hi = x, x + tw
                busy = any(abs((lo + hi) / 2 - k) < 0.09 for k in keep) or any(
                    min(a, b) - 0.05 < (lo + hi) / 2 < max(a, b) + 0.05 for a, b in span)
                if not busy and rnd.random() < 0.8:
                    box(f"{name}_tr{cu_index}_{x:.2f}", (tw, depth, t * 0.9), ((lo + hi) / 2, depth / 2 - 0.0006, zc),
                        mat, 0.002, parent=p.root)
                x = hi + rnd.choice([0.04, 0.06, 0.09])
            cu_index += 1
        else:
            mat = {"mask": mask, "diel": diel, "core": core}[kind]
            box(f"{name}_{kind}_{z:.3f}", (w, depth, t), (0, depth / 2, zc), mat, 0.002 if kind != "mask" else 0.004,
                parent=p.root)
        z += t
    p.layer_z = layer_z
    # the routes, drawn on the cut face (slightly proud so they read as copper in the cut)
    face_y = -0.001
    seg_d = 0.06  # how far the copper runs back into the body

    def piece(nm, x0, x1, z0, z1):
        cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
        return box(nm, (max(abs(x1 - x0), 0.02), seg_d, max(abs(z1 - z0), 0.02)), (cx, face_y + seg_d / 2, cz),
                   route_mat, 0.002, parent=p.root)

    vw = 0.05
    for i, (xt, xm, xb) in enumerate(zip(xs_top, xs_mid, xs_bot)):
        L1, L2, L3, L4 = layer_z[::-1]  # top layer first
        piece(f"{name}_r{i}_a", xt - vw / 2, xt + vw / 2, L2, height + 0.004)        # pad to layer 2 (microvia)
        piece(f"{name}_r{i}_b", min(xt, xm) - 0.013, max(xt, xm) + 0.013, L2 - 0.012, L2 + 0.012)
        piece(f"{name}_r{i}_c", xm - vw / 2, xm + vw / 2, L3, L2)                     # plated hole through the core
        piece(f"{name}_r{i}_d", min(xm, xb) - 0.013, max(xm, xb) + 0.013, L3 - 0.012, L3 + 0.012)
        piece(f"{name}_r{i}_e", xb - vw / 2 - 0.02, xb + vw / 2 + 0.02, -0.004, L3)   # down to the ball pad
    # the die on its bumps
    dw, dt = die_size
    gap = 0.06
    die_mat = M.principled("section_die", (0.16, 0.165, 0.22), metallic=0.2, rough=0.3, coat=0.4)
    p.add("die", box(f"{name}_die", (dw, depth * 0.66, dt), (0, depth * 0.33, height + gap + dt / 2), die_mat, 0.003,
                     parent=p.root))
    bump_src = geo.source(f"_sbump_{name}", lambda n: geo.bake(geo.sphere(n, gap * 0.55, (0, 0, 0), M.solder(),
                                                                          segments=14, rings=8), scale=(1, 1, 0.9)))
    bump_pts = [(x, y, height + gap / 2) for x in xs_top for y in (0.04, 0.16, 0.28, 0.4, 0.52, 0.64, 0.76, 0.88)]
    p.add("bumps", geo.instance_points(f"{name}_bumps", bump_src, bump_pts, parent=p.root))
    # balls in rows going back from the cut, each its own object so they can pop
    p.ball_rows = []
    for r in range(ball_rows):
        y = 0.13 + r * ball_pitch
        row = []
        for i, x in enumerate(xs_bot):
            o = geo.sphere(f"{name}_ball{r}_{i}", ball_r, (x, y, -ball_r * 0.92), M.solder(), segments=20, rings=12,
                           parent=p.root)
            row.append(o)
        p.ball_rows.append(row)
    # the mould cap (hidden until cap())
    cap_h = 0.28
    mould = box(f"{name}_mould", (dw + 0.5, depth * 0.8, cap_h), (0, depth * 0.4 - 0.001, height + cap_h / 2),
                M.mould("section_mould", (0.02, 0.02, 0.022)), 0.01, parent=p.root)
    mould.hide_render = True
    p.add("mould", mould)
    p.mark("die", (-dw / 2 + 0.1, 0, height + gap + dt))
    p.mark("top", (0, 0, height))
    p.mark("layers", (-w / 2 + 0.1, 0, layer_z[-1]))
    p.mark("layer_low", (-w / 2 + 0.1, 0, layer_z[0]))
    p.mark("routes", (xs_mid[-1], 0, layer_z[2]))
    p.mark("balls", (xs_bot[-1], 0.13, -ball_r))
    p.mark("ball_row", (xs_bot[0], 0.13, -ball_r))
    p.mark("center", (0, depth / 2, height / 2))
    return p


# --- making the laminate ----------------------------------------------------


def cloth_sheet(name="cloth", loc=(0, 0, 0), size=(2.6, 2.6), t=0.03, parent=None):
    """Dry glass-fibre cloth: white fibre bundles in a plain weave."""
    mat = M.glass_fibre("dry_cloth", color=(0.82, 0.82, 0.79), weave=7.0)
    return box(name, (size[0], size[1], t), (loc[0], loc[1], loc[2] + t / 2), mat, 0.004, parent=parent)


def flow_resin(name="resin_flow", tint=(0.98, 0.74, 0.36)):
    """Amber resin spreading across a sheet from its -x edge: knob "flow"
    (0 none, 1 all of it) with a soft wavy front."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    M._set(b, color=tint, rough=0.06, transmission=0.5, ior=1.5, coat=0.5, coat_rough=0.03, subsurface=0.2)
    tc, sep = _generated(nt)
    flow = _value(nt, "flow", 0.0)
    wobble = _math(nt, "MULTIPLY", _math(nt, "SUBTRACT", _noise(nt, tc.outputs["Generated"], 3.0), 0.5), 0.12)
    x = _math(nt, "ADD", sep.outputs["X"], wobble)
    edge = _math(nt, "SUBTRACT", _math(nt, "MULTIPLY", flow, 1.2), x)
    nt.links.new(_ramp(nt, edge, 0.0, 0.03), b.inputs["Alpha"])
    return m


def press_platen(name, loc, size=(3.4, 3.4, 0.35), parent=None):
    """A press platen: brushed steel with a knob "lit" (a warm glow of heat)."""
    m = bpy.data.materials.get("press_steel")
    if m is None:
        m, nt, b = M.node_mat("press_steel")
        M._set(b, color=(0.7, 0.71, 0.74), metallic=1.0, rough=0.38, emission=(1.0, 0.3, 0.05))
        nt.links.new(_math(nt, "MULTIPLY", _value(nt, "lit", 0.0), 0.07), b.inputs["Emission Strength"])
    return box(name, size, loc, m, 0.03, parent=parent)


class Board(Part):
    """laminate_board: core, copper foil both faces, photoresist on top.

    roll_resist(shot, start, end)    the film rolls on from -y (with a roller)
    expose(shot, start, end)         the film glows violet in the pattern
    harden(shot, start, end)         lit film turns darker teal
    develop(shot, start, end)        unlit film washes away
    etch(shot, start, end)           bare copper dissolves
    strip(shot, start, end)          the film comes off, wiring remains
    """

    def roll_resist(self, shot, start, end):
        turn(shot, self.resist_mat, "roll", 1.0, start, end, "linear")
        r = self.roller
        w, d = self.size
        y0, y1 = -d / 2 - 0.06, d / 2 + 0.06
        shot.show(r, start - 0.4, end + 0.6)
        shot.tween(r, "location.y", y1, start, end, "linear", frm=y0)
        shot.tween(r, "rotation_euler.x", (y1 - y0) / self.roller_r, start, end, "linear", frm=0.0)
        return self

    def expose(self, shot, start, end, level=1.0):
        turn(shot, self.resist_mat, "expose", level, start, start + 0.5)
        turn(shot, self.resist_mat, "expose", 0.0, end - 0.5, end)
        return self

    def harden(self, shot, start, end):
        turn(shot, self.resist_mat, "hard", 1.0, start, end)
        return self

    def develop(self, shot, start, end):
        turn(shot, self.resist_mat, "wash", 1.0, start, end, "linear")
        return self

    def etch(self, shot, start, end):
        turn(shot, self.copper_mat, "etch", 1.0, start, end, "linear")
        return self

    def strip(self, shot, start, end):
        turn(shot, self.resist_mat, "strip", 1.0, start, end, "linear")
        return self


def resist_material(name, img, tiles=1.0):
    """Photoresist with knobs: roll (coverage from -y), expose (violet glow
    in the pattern), hard (patterned film darkens), wash (unlit film
    dissolves), strip (all of it dissolves)."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    vec, tc = _pattern_vec(nt, tiles)
    pat = _pattern(nt, img, vec)
    patf = _ramp(nt, pat, 0.35, 0.65)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Generated"], sep.inputs["Vector"])
    roll, expose, hard, wash, strip = (_value(nt, k, 0.0) for k in ("roll", "expose", "hard", "wash", "strip"))
    noise = _noise(nt, tc.outputs["Object"], 14.0, 4.0)
    teal = (0.26, 0.62, 0.54)
    dark = (0.08, 0.3, 0.27)
    col = _mix_rgb(nt, _math(nt, "MULTIPLY", hard, patf), teal, dark)
    nt.links.new(col, b.inputs["Base Color"])
    M._set(b, rough=0.32, sheen=0.25, coat=0.4, coat_rough=0.12, emission=(0.55, 0.25, 1.0), transmission=0.15)
    nt.links.new(_math(nt, "MULTIPLY", _math(nt, "MULTIPLY", expose, patf), 3.0), b.inputs["Emission Strength"])
    covered = _ramp(nt, _math(nt, "SUBTRACT", _math(nt, "MULTIPLY", roll, 1.02), sep.outputs["Y"]), 0.0, 0.01)
    # wash: unlit film dissolves where the noise falls under the wash level
    gone = _math(nt, "MULTIPLY", _ramp(nt, _math(nt, "SUBTRACT", _math(nt, "MULTIPLY", wash, 1.3), noise), 0.0, 0.08),
                 _math(nt, "SUBTRACT", 1.0, patf))
    stripped = _ramp(nt, _math(nt, "SUBTRACT", _math(nt, "MULTIPLY", strip, 1.3), noise), 0.0, 0.08)
    alpha = _math(nt, "MULTIPLY", covered, _math(nt, "MULTIPLY", _math(nt, "SUBTRACT", 1.0, gone),
                                                 _math(nt, "SUBTRACT", 1.0, stripped)))
    nt.links.new(alpha, b.inputs["Alpha"])
    return m


def etch_copper_material(name, img, tiles=1.0):
    """Copper foil that etches away outside the pattern: knob "etch"."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    vec, tc = _pattern_vec(nt, tiles)
    patf = _ramp(nt, _pattern(nt, img, vec), 0.35, 0.65)
    etch = _value(nt, "etch", 0.0)
    noise = _noise(nt, tc.outputs["Object"], 10.0, 4.0)
    eaten = _math(nt, "MULTIPLY", _ramp(nt, _math(nt, "SUBTRACT", _math(nt, "MULTIPLY", etch, 1.35), noise), 0.0, 0.1),
                  _math(nt, "SUBTRACT", 1.0, patf))
    # thinning copper darkens a little before it goes
    nt.links.new(_mix_rgb(nt, _math(nt, "MULTIPLY", eaten, 0.0), (0.93, 0.55, 0.38), (0.5, 0.25, 0.15)),
                 b.inputs["Base Color"])
    M._set(b, metallic=1.0, rough=0.24)
    nt.links.new(_math(nt, "SUBTRACT", 1.0, eaten), b.inputs["Alpha"])
    return m


def laminate_board(name="board", loc=(0, 0, 0), size=(2.6, 2.6), core_t=0.1, cu_t=0.012, resist_t=0.012, seed=21,
                   roller=True, parent=None):
    """The copper-clad laminate with photoresist, for the subtractive
    process. loc is the bottom centre. Points: top, resist, copper, corner."""
    p = Board(name, loc, parent)
    w, d = size
    p.size = size
    img = circuit_image(seed)
    p.image = img
    z = 0.0
    p.add("copper_bottom", box(f"{name}_cu_b", (w, d, cu_t), (0, 0, cu_t / 2), M.brushed_copper(), 0.002, parent=p.root))
    z += cu_t
    p.add("core", box(f"{name}_core", (w, d, core_t), (0, 0, z + core_t / 2), laminate(), 0.003, parent=p.root))
    z += core_t
    p.copper_mat = etch_copper_material(f"{name}_copper", img)
    p.add("copper", box(f"{name}_cu", (w, d, cu_t), (0, 0, z + cu_t / 2), p.copper_mat, 0.0015, parent=p.root))
    z += cu_t
    p.resist_mat = resist_material(f"{name}_resist", img)
    p.add("resist", box(f"{name}_resist", (w, d, resist_t), (0, 0, z + resist_t / 2 + 0.0004), p.resist_mat, 0.0015,
                        parent=p.root))
    z += resist_t
    p.height = z
    p.roller_r = 0.09
    if roller:
        r = cylinder(f"{name}_roller", p.roller_r, w + 0.3, (0, -d / 2, z + p.roller_r), M.principled(
            "roller_rubber", (0.04, 0.045, 0.05), rough=0.5), verts=40, axis="X", parent=p.root)
        r.hide_render = True
        p.roller = r
    p.mark("top", (0, 0, z))
    p.mark("resist", (-w / 2 + 0.25, -d / 2 + 0.2, z))
    p.mark("copper", (-w / 2 + 0.3, -d / 2, z - resist_t - cu_t / 2))
    p.mark("corner", (w / 2 - 0.2, -d / 2 + 0.2, z))
    p.mark("center", (0, 0, z))
    p.mark("bottom", (0, 0, 0))
    return p


def photomask(name="mask", loc=(0, 0, 0), size=(2.6, 2.6), t=0.02, img=None, frame=0.12, parent=None):
    """A glass photomask: dark chrome everywhere but the circuit pattern,
    which is clear (and glows violet with knob "lit" while UV passes).
    A thin steel frame holds it. loc is its centre."""
    m = bpy.data.materials.get(f"{name}_glass")
    if m is None:
        m, nt, b = M.node_mat(f"{name}_glass")
        vec, tc = _pattern_vec(nt)
        patf = _ramp(nt, _pattern(nt, img or circuit_image(21), vec), 0.35, 0.65)
        chrome = nt.nodes.new("ShaderNodeBsdfPrincipled")
        M._set(chrome, color=(0.05, 0.05, 0.06), metallic=0.9, rough=0.12)
        M._set(b, color=(0.85, 0.9, 1.0), rough=0.02, transmission=1.0, ior=1.45, emission=(0.55, 0.3, 1.0))
        nt.links.new(_math(nt, "MULTIPLY", _value(nt, "lit", 0.0), 1.5), b.inputs["Emission Strength"])
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(patf, mix.inputs["Fac"])
        nt.links.new(chrome.outputs[0], mix.inputs[1])
        nt.links.new(b.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], nt.nodes["Material Output"].inputs["Surface"])
    p = Part(name, loc, parent)
    w, d = size
    p.mat = m
    p.add("glass", box(f"{name}_plate", (w, d, t), (0, 0, 0), m, 0.002, parent=p.root))
    steel = M.aluminium("mask_frame", 0.35)
    for k, (sx, sy, x, y) in enumerate([(w + 2 * frame, frame, 0, d / 2 + frame / 2),
                                         (w + 2 * frame, frame, 0, -d / 2 - frame / 2),
                                         (frame, d, w / 2 + frame / 2, 0), (frame, d, -w / 2 - frame / 2, 0)]):
        box(f"{name}_frame{k}", (sx, sy, t * 2.5), (x, y, 0), steel, 0.006, parent=p.root)
    p.mark("edge", (-w / 2 - frame, -d / 2 - frame, t))
    p.mark("center", (0, 0, 0))
    return p


def light_column(name="uv", loc=(0, 0, 0), size=(2.6, 2.6, 2.2), color=(0.55, 0.3, 1.0), parent=None):
    """A soft column of light (UV through a mask): a glowing, see-through box,
    brightest at its foot, fading up. loc is the foot's centre. Knob "lit"
    on its material (obj.active_material)."""
    m = bpy.data.materials.get(f"{name}_light")
    if m is None:
        m = bpy.data.materials.new(f"{name}_light")
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.remove(nt.nodes["Principled BSDF"])
        tc, sep = _generated(nt)
        fade = _math(nt, "POWER", _math(nt, "SUBTRACT", 1.0, sep.outputs["Z"], clamp=True), 2.0)
        # soften the vertical edges too
        ex = _math(nt, "MULTIPLY", _ramp(nt, sep.outputs["X"], 0.0, 0.12),
                   _ramp(nt, _math(nt, "SUBTRACT", 1.0, sep.outputs["X"]), 0.0, 0.12))
        ey = _math(nt, "MULTIPLY", _ramp(nt, sep.outputs["Y"], 0.0, 0.12),
                   _ramp(nt, _math(nt, "SUBTRACT", 1.0, sep.outputs["Y"]), 0.0, 0.12))
        k = _math(nt, "MULTIPLY", _math(nt, "MULTIPLY", fade, _math(nt, "MULTIPLY", ex, ey)), _value(nt, "lit", 0.0))
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = (*color, 1.0)
        nt.links.new(_math(nt, "MULTIPLY", k, 0.6), em.inputs["Strength"])
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        add = nt.nodes.new("ShaderNodeAddShader")
        nt.links.new(tr.outputs[0], add.inputs[0])
        nt.links.new(em.outputs[0], add.inputs[1])
        nt.links.new(add.outputs[0], nt.nodes["Material Output"].inputs["Surface"])
    o = box(name, size, (loc[0], loc[1], loc[2] + size[2] / 2), m, 0, parent=parent)
    o.visible_shadow = False
    return o


# --- etching -------------------------------------------------------------


def etch_tank(name="tank", loc=(0, 0, 0), size=(3.6, 3.4, 1.1), wall=0.05, fill=0.78, parent=None):
    """A shallow glass tank of etchant: glass walls and a pale blue liquid
    filled to `fill` of the height. loc is the bottom centre.
    Points: surface, wall."""
    p = Part(name, loc, parent)
    w, d, h = size
    glass = M.principled("tank_glass", (0.95, 0.97, 1.0), rough=0.02, transmission=1.0, ior=1.45)
    p.add("floor", box(f"{name}_floor", (w, d, wall), (0, 0, wall / 2), glass, 0.01, parent=p.root))
    for k, (sx, sy, x, y) in enumerate([(w, wall, 0, -d / 2 + wall / 2), (w, wall, 0, d / 2 - wall / 2),
                                         (wall, d - 2 * wall, -w / 2 + wall / 2, 0),
                                         (wall, d - 2 * wall, w / 2 - wall / 2, 0)]):
        p.add(f"wall{k}", box(f"{name}_wall{k}", (sx, sy, h), (x, y, h / 2), glass, 0.008, parent=p.root))
    lm = bpy.data.materials.get("etchant")
    if lm is None:
        lm, nt, b = M.node_mat("etchant")
        M._set(b, color=(0.75, 0.9, 1.0), rough=0.03, transmission=1.0, ior=1.33)
        tc = nt.nodes.new("ShaderNodeTexCoord")
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.15
        nt.links.new(_noise(nt, tc.outputs["Object"], 3.0, 2.0), bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
        vol = nt.nodes.new("ShaderNodeVolumeAbsorption")
        vol.inputs["Color"].default_value = (0.55, 0.8, 0.95, 1)
        vol.inputs["Density"].default_value = 0.15
        nt.links.new(vol.outputs[0], nt.nodes["Material Output"].inputs["Volume"])
    lh = (h - wall) * fill
    p.add("liquid", box(f"{name}_liquid", (w - 2 * wall - 0.004, d - 2 * wall - 0.004, lh),
                        (0, 0, wall + lh / 2), lm, 0.004, parent=p.root))
    for o in p.objs.values():  # light passes through (no caustics needed to light the board inside)
        o.visible_shadow = False
    p.surface = wall + lh
    p.mark("surface", (-w / 2 + 0.3, -d / 2, wall + lh))
    p.mark("wall", (w / 2, -d / 2, h * 0.6))
    return p


def patterned_layer(name, loc, size=(2.6, 2.6), t=0.035, seed=31, parent=None):
    """A finished wiring layer: copper traces on build-up film (for the
    stack of layers). loc is the bottom centre."""
    mn = f"wiring_{seed}"
    m = bpy.data.materials.get(mn)
    if m is None:
        m, nt, b = M.node_mat(mn)
        vec, tc = _pattern_vec(nt)
        patf = _ramp(nt, _pattern(nt, circuit_image(seed), vec), 0.35, 0.65)
        cu = nt.nodes.new("ShaderNodeBsdfPrincipled")
        M._set(cu, color=(0.93, 0.55, 0.38), metallic=1.0, rough=0.24)
        M._set(b, color=(0.42, 0.45, 0.38), rough=0.3, coat=0.5, coat_rough=0.08, transmission=0.2)
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(patf, mix.inputs["Fac"])
        nt.links.new(b.outputs[0], mix.inputs[1])
        nt.links.new(cu.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], nt.nodes["Material Output"].inputs["Surface"])
    return box(name, (size[0], size[1], t), (loc[0], loc[1], loc[2] + t / 2), m, 0.003, parent=parent)


def laser(shot, points, start, every=0.32, flash=0.16, top=3.0, z=0.0, r=0.012, parent=None, via_r=0.035,
          via_parent=None):
    """A red laser pulses down at each (x, y) in turn from `start`, `every`
    seconds apart, and leaves a via (a copper-rimmed hole) where it struck.
    z is the surface it strikes (world, or local to parent/via_parent)."""
    mat = M.emission("laser_red", (1.0, 0.12, 0.08), 0.0)
    sock = M.emission_socket(mat)
    beam = cylinder("laser_beam", r, top, (0, 0, z + top / 2), mat, verts=12, cap=False, parent=parent)
    beam.visible_shadow = False
    spot = geo.sphere("laser_spot", r * 3.5, (0, 0, z), mat, segments=12, rings=6, parent=parent)
    spot.visible_shadow = False
    rim = M.copper("via_rim", 0.2)
    hole = M.principled("via_hole", (0.02, 0.015, 0.012), rough=0.5)
    first = points[0]
    shot.set(beam, "location", (first[0], first[1], z + top / 2), 0.0)
    shot.set(spot, "location", (first[0], first[1], z), 0.0)
    shot.set(sock, "default_value", 0.0, 0.0)
    for k, (x, y) in enumerate(points):
        t = start + k * every
        shot.set(beam, "location", (x, y, z + top / 2), t - 0.02)
        shot.set(spot, "location", (x, y, z), t - 0.02)
        shot.tween(sock, "default_value", 30.0, t, t + flash * 0.3, "out")
        shot.tween(sock, "default_value", 0.0, t + flash * 0.3, t + flash, "in")
        ring = cylinder(f"via_rim{k}", via_r, 0.004, (x, y, z + 0.002), rim, verts=24, parent=via_parent or parent)
        pit = cylinder(f"via_pit{k}", via_r * 0.55, 0.005, (x, y, z + 0.0025), hole, verts=20,
                       parent=via_parent or parent)
        shot.show(ring, t + flash * 0.5)
        shot.show(pit, t + flash * 0.5)
        shot.show(beam, t - 0.02, t + flash + 0.02)
        shot.show(spot, t - 0.02, t + flash + 0.02)
    return beam


# --- helpers for the macro cross section (HB21) ------------------------------


def arrow(name, loc, direction=(1, 0, 0), length=0.12, width=0.03, color=(0.35, 0.95, 1.0), strength=6.0,
          normal=(0, -1, 0), parent=None):
    """A small glowing flat arrow (pointing along `direction`, facing
    `normal`), for showing which way an etchant eats. Knob: its material's
    emission (effects.glow)."""
    d = Vector(direction).normalized()
    n = Vector(normal).normalized()
    s = n.cross(d).normalized()
    hl = length * 0.42
    sw = width * 0.32
    pts2 = [(-length / 2, -sw), (length / 2 - hl, -sw), (length / 2 - hl, -width / 2), (length / 2, 0),
            (length / 2 - hl, width / 2), (length / 2 - hl, sw), (-length / 2, sw)]
    verts = [tuple(d * u + s * v) for u, v in pts2]
    mat = M.emission(f"{name}_glow", color, strength)
    o = geo.mesh(name, verts, [list(range(len(verts)))], loc, mat, parent)
    o.visible_shadow = False
    return o


def conformal_slab(name, x0, x1, y0, y1, bottom, top, mat, n=160, loc=(0, 0, 0), parent=None):
    """A slab from x0 to x1 and y0 to y1 whose underside follows bottom(x)
    and top follows top(x) (local z): a wire or film lying on a wavy surface.
    Flat shaded ends, smooth along x."""
    xs = [x0 + (x1 - x0) * i / n for i in range(n + 1)]
    verts, faces = [], []
    for x in xs:
        b, t = bottom(x), top(x)
        verts += [(x, y0, b), (x, y1, b), (x, y1, t), (x, y0, t)]
    for i in range(n):
        a, c = 4 * i, 4 * (i + 1)
        for k in range(4):
            k2 = (k + 1) % 4
            faces.append((a + k, a + k2, c + k2, c + k))
    faces.append((0, 3, 2, 1))
    m = 4 * n
    faces.append((m, m + 1, m + 2, m + 3))
    o = geo.mesh(name, [(x - loc[0], y - loc[1], z - loc[2]) for x, y, z in verts], faces, loc, mat, parent)
    # the ring runs bottom-front, bottom-back, top-back, top-front, so these faces point outward
    o.data.shade_smooth()
    for p in o.data.polygons:
        if abs(p.normal.x) > 0.9 or abs(p.normal.y) > 0.9:
            p.use_smooth = False
    return o


class UndercutLine:
    """A copper line running along y, seen in cross section on the face
    y=y0, whose sides an isotropic wet etch eats in under its resist: the
    recess at height z is sqrt(R^2 - (h - z)^2), so it is deepest just under
    the resist. etch(shot, start, end, r) grows R from 0 to r."""

    def __init__(self, name, x, width, h, y0, y1, z0, mat, steps=24, parent=None):
        self.x, self.w, self.h, self.z0, self.y0, self.y1 = x, width, h, z0, y0, y1
        self.steps = steps
        verts, faces = self._verts(0.0), []
        ring = 2 * (steps + 1)
        for k in range(ring):
            k2 = (k + 1) % ring
            faces.append((k, k2, ring + k2, ring + k))
        faces.append(tuple(range(ring - 1, -1, -1)))
        faces.append(tuple(range(ring, 2 * ring)))
        self.obj = geo.mesh(name, verts, faces, (0, 0, 0), mat, parent)
        self.R = 0.0
        self.track = []

    def _profile(self, R):
        """Points round the section, left side bottom to top, right side top to bottom."""
        h, half = self.h, self.w / 2
        left, right = [], []
        for i in range(self.steps + 1):
            z = h * i / self.steps
            d = h - z
            rec = math.sqrt(max(R * R - d * d, 0.0))
            hw = max(half - rec, 0.0005)
            left.append((self.x - hw, self.z0 + z))
            right.append((self.x + hw, self.z0 + z))
        return left + right[::-1]

    def _verts(self, R):
        prof = self._profile(R)
        return [(x, self.y0, z) for x, z in prof] + [(x, self.y1, z) for x, z in prof]

    def gone_at(self, R):
        """True when the line has no copper left wider than a hair at R."""
        return all(abs(a[0] - b[0]) < 0.002 for a, b in zip(self._profile(R)[: self.steps + 1],
                                                          self._profile(R)[::-1][: self.steps + 1]))

    def etch(self, shot, start, end, r, ease="inout"):
        from .. import ease as E

        f = E.get(ease)
        me = self.obj.data

        def update(t):
            u = 0.0 if t <= start else 1.0 if t >= end else f((t - start) / (end - start))
            co = []
            for v in self._verts(u * r):
                co.extend(v)
            me.vertices.foreach_set("co", co)
            me.update()

        shot.on_frame(update)
        return self

    def pinch_time(self, start, end, r, ease="inout"):
        """When the top of the line pinches off (R = width / 2)."""
        from .. import ease as E

        f = E.get(ease)
        target = (self.w / 2) / r
        lo, hi = 0.0, 1.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if f(mid) < target:
                lo = mid
            else:
                hi = mid
        return start + (end - start) * hi
