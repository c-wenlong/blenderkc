"""The silicon interposer: a polished silicon slab whose wiring is etched
and filled like a chip's, the organic substrate it outclasses, a GPU package
that comes apart into substrate, interposer and chips, and the silicon
bridges that replace a full interposer in the largest packages.

    from blenderkc.kit import interposer as ip
    slab = ip.silicon_slab("si", (0, 0, 0.3), (4.6, 4.6, 0.16))
    ip.trenches(shot, slab.mat, 15.0, 17.0); ip.fill(shot, slab.mat, 17.8, 19.6)
    ip.light_sweep(shot, 10.8, 12.6, (-4, 0, 3), (4, 0, 3))   # a glint across it
    pkg = ip.cowos_package("pkg", lift=(0.9, 0.9))             # HB25: start exploded
    pkg.mount(shot, 2.6, 3.4); pkg.signals(shot, 5.9, 9.0); pkg.seat(shot, 10.6, 11.6)

Knobs are Value nodes in materials, tweened with packaging.turn().
"""
import bpy

from . import geo
from . import materials as M
from . import parts
from .geo import Part, box, cylinder
from .packaging import _bsdf, _generated, _math, _mix_rgb, _ramp, _value, ghost, knob, turn  # noqa: F401

LAVENDER = (0.36, 0.35, 0.5)


def line_silicon(name="si_lines", pitch=0.05, width=0.4, color=LAVENDER, along="Y", size=1.0, trench=0.0,
                 fill=0.0):
    """Polished silicon with a field of parallel lines (running along local
    `along`, `pitch` units apart, `width` of the pitch wide). Knobs:
    "trench" 0..1 (the lines are etched: dark grooves), "fill" 0..1 (copper
    fills them, sweeping across), "glow" 0..1 (lines lit, sweeping across:
    a count), "glint" (sheen of the whole surface). `size` is the slab's
    extent across the lines, so pitch is in scene units."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    tc, sep = _generated(nt)
    across = sep.outputs["X"] if along == "Y" else sep.outputs["Y"]
    sweep_axis = sep.outputs["Y"] if along == "Y" else sep.outputs["X"]
    u = _math(nt, "FRACT", _math(nt, "MULTIPLY", across, size / pitch))
    half = width / 2
    line = _math(nt, "MULTIPLY", _ramp(nt, u, 0.5 - half - 0.04, 0.5 - half),
                 _ramp(nt, _math(nt, "SUBTRACT", 1.0, u), 0.5 - half - 0.04, 0.5 - half))
    # only the top face carries lines
    nsep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Normal"], nsep.inputs["Vector"])
    line = _math(nt, "MULTIPLY", line, _ramp(nt, nsep.outputs["Z"], 0.6, 0.8))
    k_trench, k_fill, k_glow = _value(nt, "trench", trench), _value(nt, "fill", fill), _value(nt, "glow", 0.0)
    filled = _ramp(nt, _math(nt, "SUBTRACT", _math(nt, "MULTIPLY", k_fill, 1.15), sweep_axis), 0.0, 0.1)
    lit = _ramp(nt, _math(nt, "SUBTRACT", _math(nt, "MULTIPLY", k_glow, 1.15), across), 0.0, 0.02)
    groove = _math(nt, "MULTIPLY", _math(nt, "MULTIPLY", line, k_trench), _math(nt, "SUBTRACT", 1.0, filled))
    cu_amt = _math(nt, "MULTIPLY", line, filled)
    col = _mix_rgb(nt, groove, color, (0.06, 0.06, 0.09))
    nt.links.new(col, b.inputs["Base Color"])
    M._set(b, metallic=0.55, rough=0.12, coat=0.4, coat_rough=0.02, emission=(0.3, 1.0, 0.5))
    nt.links.new(_math(nt, "MULTIPLY", _math(nt, "MULTIPLY", cu_amt, lit), 2.5), b.inputs["Emission Strength"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.6
    bump.inputs["Distance"].default_value = 0.002
    nt.links.new(_math(nt, "MULTIPLY", groove, -1.0), bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    cu = nt.nodes.new("ShaderNodeBsdfPrincipled")
    M._set(cu, color=(0.95, 0.56, 0.38), metallic=1.0, rough=0.18, emission=(0.3, 1.0, 0.5))
    nt.links.new(_math(nt, "MULTIPLY", lit, 2.5), cu.inputs["Emission Strength"])
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(cu_amt, mix.inputs["Fac"])
    nt.links.new(b.outputs[0], mix.inputs[1])
    nt.links.new(cu.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], nt.nodes["Material Output"].inputs["Surface"])
    return m


def silicon_slab(name="silicon", loc=(0, 0, 0), size=(4.6, 4.6, 0.16), pitch=0.05, width=0.4, trench=0.0,
                 fill=0.0, parent=None):
    """A polished silicon slab with a line field ready to etch and fill.
    loc is the bottom centre. slab.mat is its material (knobs above).
    Points: top, edge, corner."""
    p = Part(name, loc, parent)
    w, d, t = size
    p.mat = line_silicon(f"{name}_surface", pitch, width, size=w, trench=trench, fill=fill)
    p.add("slab", box(f"{name}_slab", size, (0, 0, t / 2), p.mat, 0.01, parent=p.root))
    p.size = size
    p.mark("top", (0, 0, t))
    p.mark("edge", (w / 2, -d / 2, t / 2))
    p.mark("corner", (-w / 2 + 0.3, -d / 2 + 0.3, t))
    p.mark("near", (0, -d / 2 + 0.2, t))
    return p


def polished_look(rig, world_degrees=206.0):
    """Tune rigs.dark_studio for a mirror-polished subject: turn the world
    so its window, not a small lamp, is what the surface reflects, and drop
    the fill light, whose hot spot sits in the middle of a mirror."""
    import math

    world = bpy.context.scene.world
    for n in world.node_tree.nodes:
        if n.type == "MAPPING":
            n.inputs["Rotation"].default_value[2] = math.radians(world_degrees)
    rig.light("fill").hide_render = True
    return rig


def no_horizon():
    """Hide the world from the camera (it still lights the scene), so a low
    camera past the floor's far edge sees black, not the HDRI's room."""
    bpy.context.scene.world.cycles_visibility.camera = False


def trenches(shot, mat, start, end):
    turn(shot, mat, "trench", 1.0, start, end)


def fill(shot, mat, start, end):
    turn(shot, mat, "fill", 1.0, start, end, "linear")


def light_sweep(shot, start, end, frm, to, size=(0.6, 9.0), strength=40.0, name="sweep"):
    """A long soft light bar, invisible to the camera, that travels from
    `frm` to `to` (world points) between start and end: its reflection
    glides across a polished surface. Faces down."""
    mat = M.emission(f"{name}_light", (1.0, 1.0, 1.0), strength)
    o = geo.plane(name, size, frm, mat)
    o.rotation_euler.x = 3.14159265  # emit downward
    o.visible_camera = False
    o.visible_shadow = False
    shot.show(o, max(0.0, start - 0.01), end + 0.01)
    shot.tween(o, "location", tuple(to), start, end, "inout", frm=tuple(frm))
    return o


def organic_piece(name="organic", loc=(0, 0, 0), size=(2.4, 3.0, 0.14), lines=5, line_w=0.24, parent=None):
    """A piece of organic substrate with a few broad copper lines along y.
    piece.line_mats: one glowing copper per line (knob "lit")."""
    from .packaging import glow_copper, laminate

    p = Part(name, loc, parent)
    w, d, t = size
    p.add("body", box(f"{name}_body", size, (0, 0, t / 2), laminate(), 0.006, parent=p.root))
    pitch = w / lines
    p.line_mats = []
    for i in range(lines):
        x = -w / 2 + pitch * (i + 0.5)
        mat = glow_copper(f"{name}_line{i}")
        p.line_mats.append(mat)
        box(f"{name}_line{i}", (line_w, d, 0.02), (x, 0, t + 0.01), mat, 0.004, parent=p.root)
    p.mark("line", (-w / 2 + pitch * 1.5, -d / 2 + 0.15, t + 0.02))
    p.mark("top", (0, 0, t))
    return p


# --- the package: substrate, interposer, chips -----------------------------


class Cowos(Part):
    """cowos_package(): a GPU package in parts that can come apart.

    pkg.mount(shot, start, end)       the dies and stacks drop onto the interposer
    pkg.seat(shot, start, end)        the interposer drops onto the substrate
    pkg.ghost_chips(shot, a, b, to)   dies and stacks fade to `to` alpha (see beneath)
    pkg.signals(shot, start, end)     short wires between stacks and dies light with pulses
    pkg.power(shot, start, end)       thick paths through the substrate light up
    """

    def mount(self, shot, start, end, stagger=0.06):
        for k, o in enumerate(self.chip_roots):
            z = self.rest_z[o.name]
            shot.tween(o, "location.z", z, start + stagger * k, end + stagger * k, "out")
        return self

    def seat(self, shot, start, end):
        shot.tween(self.upper, "location.z", 0.0, start, end, "inout")
        return self

    def ghost_chips(self, shot, start, end, to=0.25, stack_floor=0.45):
        """Fade the dies and mould shells to `to`; the DRAM dies inside the
        stacks fade too, but no further than `stack_floor`, so the stacks
        still read while what lies under them shows through."""
        for mat in self.chip_mats:
            turn(shot, mat, "alpha", to, start, end)
        for mat in self.stack_mats:
            turn(shot, mat, "alpha", max(to, stack_floor), start, end)
        return self

    def signals(self, shot, start, end, level=0.0):
        """Pulses on the short wires; the wires are shown only meanwhile
        (they lie under the chips, seen when ghost_chips() fades them)."""
        from . import effects

        for bus in self.buses:
            shot.show(bus.root, start - 0.6, end + 0.6)
            effects.signal_pulses(shot, bus, start, end, speed=0.9, spacing=0.35)
            if level:
                effects.light_up(shot, bus, start, start + 0.6, level)
        return self

    def power(self, shot, start, end, fade=None):
        turn(shot, self.power_mat, "lit", 1.0, start, end)
        if fade:
            turn(shot, self.power_mat, "lit", 0.0, fade[0], fade[1])
        return self


def cowos_package(name="pkg", loc=(0, 0, 0), stacks_per_side=4, die_size=(3.0, 5.2), stack_x=2.12,
                  lift=(0.0, 0.0), buses=True, balls=True, power_paths=True, parent=None):
    """A Blackwell-class package: two GPU dies, stacks_per_side HBM stacks
    on each side (moulded), all on a silicon interposer on an organic
    substrate, built on kit.parts.gpu_package with materials that can fade
    and glow. lift=(interposer, chips) starts them raised that far (an
    exploded view) for mount() and seat(). Points: die, stack, interposer,
    substrate, bridge0.., balls."""
    sub_mat = ghost(f"{name}_substrate_mat", (0.035, 0.12, 0.06), rough=0.3, coat=0.8, glow=(0.3, 1.0, 0.5))
    base = parts.gpu_package(name, loc, stacks_per_side=stacks_per_side, dies=2, die_size=die_size, stack_x=stack_x,
                             mould=True, substrate_mat=sub_mat, parent=parent)
    p = Cowos(name + "_view", loc, parent)
    bpy.data.objects.remove(p.root)  # the package's own root is the Part's root
    p.root = base.root
    p.base = base
    p.die_size, p.stack_x, p.stack_w = die_size, stack_x, 0.95
    p.objs = base.objs
    p.points = base.points
    p.stacks = base.stacks
    root = base.root
    top = base.top
    p.top = top
    p.sub_mat = sub_mat
    p.int_mat = ghost(f"{name}_interposer_mat", (0.36, 0.36, 0.46), metallic=0.7, rough=0.16, coat=0.3,
                      glow=(1.0, 0.62, 0.15))
    base["interposer"].data.materials[0] = p.int_mat
    die_mat = ghost(f"{name}_die_mat", (0.45, 0.46, 0.5), metallic=1.0, rough=0.38)
    mould_mat = ghost(f"{name}_mould_mat", (0.018, 0.018, 0.02), rough=0.5)
    p.chip_mats = [die_mat, mould_mat]
    # the parts above the substrate ride on `upper`; the chips on it can drop separately
    p.upper = geo.empty(f"{name}_upper", (0, 0, lift[0]), root)
    ix = base["interposer"]
    ix.parent = p.upper
    p.chip_roots, p.rest_z = [], {}
    for k in ("gpu0", "gpu1"):
        o = base[k]
        o.data.materials[0] = die_mat
        o.parent = p.upper
        p.chip_roots.append(o)
    p.stack_mats = []
    for i, s in enumerate(base.stacks):
        mo = base[f"mould{i}"]
        mo.data.materials[0] = mould_mat
        for layer in s.layers:  # package-local copies of the die materials, with an alpha knob
            src = layer.data.materials[0]
            local = bpy.data.materials.get(f"{name}_{src.name}")
            if local is None:
                local = src.copy()
                local.name = f"{name}_{src.name}"
                nt = local.node_tree
                nt.links.new(_value(nt, "alpha", 1.0), _bsdf(local).inputs["Alpha"])
            if local not in p.stack_mats:  # cached from an earlier package of this name too
                p.stack_mats.append(local)
            layer.data.materials[0] = local
        # the mould and its dies move as one: make the stack ride on the mould
        sx, sy, sz = s.root.location
        mx, my, mz = mo.location
        s.root.parent = mo
        s.root.location = (sx - mx, sy - my, sz - mz)
        mo.parent = p.upper
        p.chip_roots.append(mo)
    for o in p.chip_roots:
        p.rest_z[o.name] = o.location.z
        o.location.z += lift[1]
    # short wires on the interposer from under each stack to under the die
    p.buses = []
    sw, sd = 0.95, 1.25
    if buses:
        for i, s in enumerate(base.stacks):
            side = -1 if i < stacks_per_side else 1
            y = base.points[f"stack{i}"][1].y
            x_in = side * (die_size[0] / 2 - 0.35)
            x_out = side * (stack_x - sw / 2 + 0.3)
            bus = parts.wires(f"{name}_bus{i}", start=(x_in, y, top + 0.004), end=(x_out, y, top + 0.004), n=22,
                              spacing=sd / 23, r=0.006, mat=parts.signal_material(f"{name}_signal"),
                              parent=p.upper)
            p.buses.append(bus)
    # power: thick copper paths down through the substrate to the balls
    p.power_mat = ghost(f"{name}_power_mat", (0.93, 0.55, 0.38), metallic=1.0, rough=0.25, glow=(1.0, 0.55, 0.12))
    sub_t = base["substrate"].dimensions.z
    if power_paths:
        for k, (x, y) in enumerate([(-1.2, -1.8), (-0.4, 0.6), (0.5, -0.9), (1.3, 1.9), (-1.1, 2.2), (0.9, -2.5),
                                    (-2.6, -0.4), (2.6, 0.5), (0.0, 2.9), (-0.1, -2.9)]):
            c = cylinder(f"{name}_power{k}", 0.1, sub_t + 0.02, (x, y, sub_t / 2), p.power_mat, verts=20, parent=root)
            if k == 6:  # a front path, outside the dies, for a label
                p.points["power"] = (c, _v(0.0, 0.0, sub_t / 2))
    if balls:
        sx_, sy_ = base.size
        nx, ny = int((sx_ - 0.3) / 0.25), int((sy_ - 0.3) / 0.25)
        p.add("balls", parts.solder_balls(f"{name}_balls", nx, ny, 0.25, 0.075, (0, 0, -0.15), parent=root))
    # anchor points that ride with the parts
    p.points["gpu"] = (base["gpu0"], _v(0.0, 0.0, 0.08))
    p.points["hbm"] = (base["mould0"], _v(0.0, -0.4, base["mould0"].dimensions.z / 2))
    p.points["interposer_edge"] = (ix, _v(-ix.dimensions.x / 2 + 0.2, -ix.dimensions.y / 2, 0.0))
    p.points["substrate_edge"] = (base["substrate"], _v(-base.size[0] / 2 + 0.3, -base.size[1] / 2, 0.0))
    p.points["substrate_right"] = (base["substrate"], _v(base.size[0] / 2, -base.size[1] / 2 + 0.6, 0.0))
    p.points["balls"] = (root, _v(-base.size[0] / 2 + 0.3, -base.size[1] / 2 + 0.3, -0.08))
    return p


def _v(x, y, z):
    from mathutils import Vector

    return Vector((x, y, z))


def bridges(pkg, shot=None, size=(0.62, 1.0), name="bridge"):
    """Silicon bridges set into the interposer where each HBM stack meets a
    die, as in packages that skip a full silicon interposer. Returns
    (objects, material); the material has knobs "lit" and "alpha"."""
    mat = ghost(f"{name}_si", LAVENDER, metallic=0.55, rough=0.12, coat=0.4, glow=(0.75, 0.7, 1.0))
    knob(mat, "alpha").default_value = 0.0
    out = []
    base = pkg.base
    ix = base["interposer"]
    t = ix.dimensions.z
    n = len(base.stacks) // 2
    for i, s in enumerate(base.stacks):
        side = -1 if i < n else 1
        y = base.points[f"stack{i}"][1].y
        # centred on the gap between the die's edge and the stack's inner edge
        x = side * (pkg.die_size[0] / 2 + pkg.stack_x - pkg.stack_w / 2) / 2
        o = box(f"{name}{i}", (size[0], size[1], t + 0.006), (x, y, ix.location.z), mat, 0.004, parent=pkg.upper)
        out.append(o)
        pkg.points[f"bridge{i}"] = (o, _v(0.0, -size[1] / 2, (t + 0.006) / 2))
    return out, mat
