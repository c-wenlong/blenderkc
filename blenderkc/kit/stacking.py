"""Stacking and bonding: the parts sections 9 to 12 of the HBM video need.

The dies here are drawn as the original's frames draw them in the light
studio: wide, thin, polished silicon plates (lavender grey) on rows of
microbumps, seen side on so the gaps between them show.

    st = stacking.silicon_stack("st", (0, 0, 0.5), n=8)
    st.drop_in(shot, 1.0, step=0.15)            dies fall into place one by one
    st.reflow(shot, 3.0, 3.6)                    every solder cap melts at once
    st.fill_gaps(shot, 5.0, 7.0, stacking.underfill())   epoxy creeps into each gap
    st.bend(shot, 2.0, 2.3, angle=8, top=3)      the top dies flex under a press
    heat = stacking.heat_material("die_heat", z_mid, half)  dies that glow hot
    shot.tween(stacking.heat_value(heat), "default_value", 1.0, 2, 3)

Also: pedestal, press_pad, crack, mould_cavity, bond_head, glow_ring,
wafer_disc, wafer_rack, dimm, trend_line, animate_grid.
"""
import math

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import geo, rigs
from . import materials as M
from .geo import Part, box, cylinder


def light_studio(shot, scale=0.8, **kw):
    """The grey studio for these sets: rigs.grey_studio with its lights kept
    out of the camera and its backdrop matte, so no soft highlight of a
    light floats on the wall behind a close-up. Returns the Rig."""
    r = rigs.grey_studio(shot, scale=scale, **kw)
    for light in r.lights.values():
        light.visible_camera = False
    # the rim light sits close to the wall and leaves a bright pool on it in
    # every close-up: move it in front of the wall and aim it at the subject
    rim = r.lights["rim"]
    rim.location = (3.2 * scale, 1.2 * scale, 3.0 * scale)
    aim = Vector((0.0, 0.0, 0.8 * scale)) - rim.location
    rim.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()
    m = M.backdrop("stacking_backdrop", color=(0.42, 0.42, 0.43), rough=0.85)
    m.node_tree.nodes["Principled BSDF"].inputs["Specular IOR Level"].default_value = 0.08
    r.floor.data.materials.clear()
    r.floor.data.materials.append(m)
    return r


# --- materials ----------------------------------------------------------

def silicon(name="stack_silicon"):
    """Polished silicon die, as the original draws it in the light studio."""
    return M.principled(name, (0.40, 0.40, 0.52), metallic=0.55, rough=0.12, coat=0.4, coat_rough=0.04)


def underfill(name="mr_muf", tint=(0.10, 0.085, 0.07), density=6.0):
    """Liquid epoxy moulding compound: dark, translucent while it flows.
    stacking.cure(shot, mat, start, end) turns it solid and matte."""
    return M.epoxy(name, tint=tint, rough=0.08, density=density)


def film(name="ncf_film", tint=(0.95, 0.62, 0.16), density=2.0):
    """Non-conductive film: thin, amber, translucent."""
    return M.epoxy(name, tint=tint, rough=0.1, density=density)


def cure(shot, mat, start, end, color=(0.035, 0.032, 0.03), rough=0.55, ease="inout"):
    """A translucent resin sets: transmission goes out, it turns matte."""
    b = mat.node_tree.nodes["Principled BSDF"]
    shot.tween(b.inputs["Transmission Weight"], "default_value", 0.0, start, end, ease)
    shot.tween(b.inputs["Roughness"], "default_value", rough, start, end, ease)
    shot.tween(b.inputs["Base Color"], "default_value", (*color, 1.0), start, end, ease)
    return mat


def warm_steel(name="bond_head_steel"):
    """Brushed steel that can glow warm (a heated tool)."""
    m = M.steel(name)
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Emission Color"].default_value = (1.0, 0.35, 0.08, 1.0)
    b.inputs["Emission Strength"].default_value = 0.0
    return m


def heat_material(name, z_mid, half, base=None, hot=(1.0, 0.12, 0.03), warm=(1.0, 0.5, 0.1), strength=4.0,
                  spread=1.0):
    """Silicon whose emission is a heat map: hottest at world height z_mid,
    falling to nothing `half` above and below, scaled by a value node "heat"
    (0 = cold). heat_value(mat) is the socket to tween; "spread" (another
    value node, heat_spread(mat)) above 1 widens the hot band."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    color = base or (0.40, 0.40, 0.52)
    m, nt, b = M.node_mat(name)
    M._set(b, color=color, metallic=0.55, rough=0.12, coat=0.4, coat_rough=0.04)
    heat = nt.nodes.new("ShaderNodeValue")
    heat.name = heat.label = "heat"
    heat.outputs[0].default_value = 0.0
    sp = nt.nodes.new("ShaderNodeValue")
    sp.name = sp.label = "spread"
    sp.outputs[0].default_value = spread
    geo_n = nt.nodes.new("ShaderNodeNewGeometry")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(geo_n.outputs["Position"], sep.inputs["Vector"])

    def op(kind, a, bb=None):
        n = nt.nodes.new("ShaderNodeMath")
        n.operation = kind
        for i, s in enumerate((a, bb)):
            if s is None:
                continue
            if isinstance(s, (int, float)):
                n.inputs[i].default_value = s
            else:
                nt.links.new(s, n.inputs[i])
        return n.outputs[0]

    u = op("DIVIDE", op("SUBTRACT", sep.outputs["Z"], z_mid), op("MULTIPLY", sp.outputs[0], half))
    bell = op("MAXIMUM", op("SUBTRACT", 1.0, op("POWER", op("ABSOLUTE", u), 2.0)), 0.0)
    level = op("MULTIPLY", bell, heat.outputs[0])
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["A"].default_value = (*warm, 1)
    mix.inputs["B"].default_value = (*hot, 1)
    nt.links.new(op("MINIMUM", level, 1.0), mix.inputs["Factor"])
    nt.links.new(mix.outputs["Result"], b.inputs["Emission Color"])
    nt.links.new(op("MULTIPLY", level, strength), b.inputs["Emission Strength"])
    return m


def heat_value(mat):
    return mat.node_tree.nodes["heat"].outputs[0]


def heat_spread(mat):
    return mat.node_tree.nodes["spread"].outputs[0]


def glow_copper(name="glow_copper", glow=(1.0, 0.42, 0.1)):
    """Copper (pillars, pads) that can glow: tween M.emission_socket(mat)."""
    return M.principled(name, (0.93, 0.52, 0.36), metallic=1.0, rough=0.22, emission=glow, strength=0.0)


# --- slabs that can bend -------------------------------------------------

def slab(name, size, loc=(0, 0, 0), mat=None, bevel_width=0.006, cuts=0, parent=None):
    """A box like geo.box; cuts > 0 subdivides it along x so it can bend."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    if cuts:
        edges = [e for e in bm.edges if abs((e.verts[0].co - e.verts[1].co).normalized().x) > 0.99]
        bmesh.ops.subdivide_edges(bm, edges=edges, cuts=cuts, use_grid_fill=True)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if bevel_width:
        me.shade_smooth()
    o = bpy.data.objects.new(name, me)
    o.location = loc
    if mat is not None:
        me.materials.append(mat)
    geo.link(o, parent)
    if bevel_width:
        geo.bevel(o, min(bevel_width, min(size) * 0.45), 2)
    return o


def _bend_mod(obj, origin=None):
    """The bend modifier of obj (made on first use). origin: an object whose
    space the bend happens in (default obj's own), so geometry attached to a
    die can bend exactly with it."""
    m = obj.modifiers.get("bend")
    if m is None:
        m = obj.modifiers.new("bend", "SIMPLE_DEFORM")
        m.deform_method = "BEND"
        m.deform_axis = "Y"
        m.angle = 0.0
        if origin is not None:
            m.origin = origin
    return m


# --- the stack ----------------------------------------------------------

class SiliconStack(Part):
    """Dies on microbumps, side on. stack.layers (bottom first), stack.rest_z,
    stack.caps[i] (the solder caps under die i), stack.fills (made by
    fill_gaps), stack.thermal (made by add_thermal)."""

    def drop_in(self, shot, start, step=0.15, height=0.8, dur=0.45, first=1, ease="out"):
        """Dies from `first` up fall into place one after another."""
        for k, i in enumerate(range(first, len(self.layers))):
            t = start + k * step
            obj = self.layers[i]
            shot.show(obj, t)
            shot.tween(obj, "location.z", self.rest_z[i], t, t + dur, ease, frm=self.rest_z[i] + height + k * 0.05)
        return self

    def reflow(self, shot, start, end, squash=0.35, ease="inout"):
        """Every solder cap melts and flattens at once; each die sinks by the
        height its caps lost, so nothing floats or sinks into its neighbour."""
        loss = self.cap_h * squash
        for i, cap in enumerate(self.caps):
            if cap is None:
                continue
            shot.tween(cap, "scale.z", 1.0 - squash, start, end, ease)
        for i, obj in enumerate(self.layers):
            if i:
                self.rest_z[i] -= i * loss
                shot.tween(obj, "location.z", self.rest_z[i], start, end, ease)
        self.gap -= loss
        self.height -= (len(self.layers) - 1) * loss
        self.mark("top", (0, 0, self.height))
        return self

    def gap_z(self, i):
        """World-local z of the middle of the gap under die i (i >= 1)."""
        return self.rest_z[i] - self.t / 2 - self.gap / 2

    def fill_gaps(self, shot, start, end, mat, stagger=0.12, side=-1, envelope=0.0, ease="inout"):
        """Resin creeps into every gap from one side (side=-1: from -x). With
        envelope > 0 it also wraps the stack in a block that much wider, the
        moulded shape. Returns the fill objects."""
        w, d = self.size
        objs = []
        n = len(self.layers)
        for i in range(1, n):
            g = self.gap * 0.999
            o = box(f"{self.name}_fill{i}", (w, d * 0.995, g), (0, 0, 0), mat, 0, parent=self.root)
            objs.append(o)
            self._grow_x(shot, o, w, side, self.gap_z(i), start + (i - 1) * stagger, end + (i - 1) * stagger, ease)
        if envelope:
            e = envelope
            h = self.height + e - self.rest_z[0] + self.t / 2
            o = box(f"{self.name}_envelope", (w + 2 * e, d + 2 * e, h), (0, 0, 0), mat, 0.01, parent=self.root)
            objs.append(o)
            self._grow_x(shot, o, w + 2 * e, side, self.rest_z[0] - self.t / 2 + h / 2, start, end + (n - 2) * stagger,
                         ease)
        self.fills.extend(objs)
        return objs

    def _grow_x(self, shot, o, w, side, z, start, end, ease):
        # origin on the starting edge so scale.x grows it across
        o.data.transform(Matrix.Translation((-side * w / 2, 0, 0)))
        o.location = (side * w / 2, 0, z)
        o.scale.x = 0.0005
        shot.show(o, start)
        shot.tween(o, "scale.x", 1.0, start, end, ease, frm=0.0005)

    def bend(self, shot, start, end, angle=6.0, top=3, ease="inout"):
        """The top `top` dies flex by `angle` degrees (0 bends them back).
        Build the stack with cuts > 0 for a smooth bend."""
        for obj in self.layers[-top:]:
            shot.tween(_bend_mod(obj), "angle", math.radians(angle), start, end, ease)
            # things attached to the die (a crack) bend with it, in the die's space; the bumps sit in
            # the central band where a bend about the die's centre moves nothing, so they need none
            for child in obj.children:
                if child.modifiers.get("bend") is not None:
                    shot.tween(child.modifiers["bend"], "angle", math.radians(angle), start, end, ease)
        return self

    def add_thermal(self, xs, rows=None, mat=None):
        """Extra (dummy) bumps that carry only heat: bands of pillars at the x
        positions `xs`, under every die, hidden until shown. Returns them."""
        mat = mat or glow_copper("thermal_copper", glow=(1.0, 0.35, 0.06))
        rows = rows or self.rows
        out = []
        for i in range(1, len(self.layers)):
            pts = [(x, y, 0.0) for x in xs for y in rows]
            o = self._bump_band(f"{self.name}_th{i}", pts, i, mat, solder=False)
            o.hide_render = True
            out.append(o)
            self.mark(f"thermal{i}", (xs[0], -self.size[1] / 2, -self.t / 2 - self.gap / 2), obj=self.layers[i])
        self.thermal = out
        return out

    def _bump_band(self, name, pts, i, pillar_mat, solder=True):
        """Pillars hanging from die i's underside; dummy (solder=False) ones
        run the whole gap, all copper."""
        h = self.pillar_h if solder else self.gap
        src = geo.source(f"_pil_{self.name}_{pillar_mat.name}_{int(solder)}_{self.bump_r:.4f}_{h:.4f}",
                         lambda n: geo.bake(cylinder(n, self.bump_r, h, (0, 0, -h / 2), pillar_mat, verts=14)))
        return geo.instance_points(name, src, pts, (0, 0, -self.t / 2), self.layers[i])


def silicon_stack(name="stack", loc=(0, 0, 0), n=8, size=(4.0, 3.0), t=0.1, gap=0.07, base=True, mat=None,
                  base_mat=None, cols=None, rows=None, bump_r=0.022, cuts=0, parent=None):
    """n dies on a base die (base=True), each `t` thick, `gap` apart, on
    microbumps: copper pillars with solder caps in a band of columns `cols`
    (x positions, default four round the centre) by `rows` (y positions).
    loc is the bottom centre. Points: top, side, front, gap1..n, die0..n."""
    p = SiliconStack(name, loc, parent)
    w, d = size
    p.size, p.t, p.gap, p.bump_r = size, t, gap, bump_r
    p.pillar_h, p.cap_h = gap * 0.58, gap * 0.42
    cols = cols if cols is not None else [(-1.5 + k) * 0.09 for k in range(4)]
    rows = rows if rows is not None else [-d / 2 + 0.12 + k * (d - 0.24) / 15 for k in range(16)]
    p.cols, p.rows = cols, rows
    mat = mat or silicon()
    base_mat = base_mat or M.logic_die("stack_base")
    pil = M.copper("pillar_copper")
    sol = M.solder("cap_solder", rough=0.12)

    def make_cap(nm):
        # an ellipsoid cap_h tall with its top at the origin, so scale.z flattens it downward
        c = geo.bake(geo.sphere(nm, bump_r * 1.06, (0, 0, 0), sol, segments=14, rings=8),
                     scale=(1, 1, p.cap_h / (2 * bump_r * 1.06)))
        c.data.transform(Matrix.Translation((0, 0, -p.cap_h / 2)))
        return c

    cap_src = geo.source(f"_cap_{name}_{bump_r:.4f}_{p.cap_h:.4f}", make_cap)
    p.layers, p.rest_z, p.caps, p.bumps, p.fills, p.thermal = [], [], [], [], [], []
    z = 0.0
    count = n + 1 if base else n
    pts = [(x, y, 0.0) for x in cols for y in rows]
    for i in range(count):
        if i:
            z += gap
        zc = z + t / 2
        lay = slab(f"{name}_d{i}", (w, d, t), (0, 0, zc), base_mat if (base and i == 0) else mat, 0.006, cuts,
                   parent=p.root)
        p.layers.append(lay)
        p.rest_z.append(zc)
        if i:
            p.bumps.append(p._bump_band(f"{name}_b{i}", pts, i, pil))
            cap = geo.instance_points(f"{name}_c{i}", cap_src, pts, (0, 0, -t / 2 - p.pillar_h), lay)
            p.caps.append(cap)
        else:
            p.caps.append(None)
        z += t
    p.height = z
    p.mark("top", (0, 0, z))
    p.mark("front", (0, -d / 2, z / 2))
    p.mark("side", (w / 2, -d / 2, z / 2))
    for i in range(count):
        p.mark(f"die{i}", (-w / 2 + 0.25, -d / 2, 0), obj=p.layers[i])
        p.mark(f"face{i}", (0.55, -d / 2, 0), obj=p.layers[i])
        if i:
            p.mark(f"gap{i}", (cols[0] if cols else 0.0, -d / 2, -t / 2 - gap / 2), obj=p.layers[i])
    return p


# --- tools and furniture ------------------------------------------------

def pedestal(name="pedestal", loc=(0, 0, 0), r=2.6, h=0.5, color=(0.55, 0.55, 0.57), parent=None):
    """A plain round plinth; loc is its bottom centre. Points: top."""
    p = Part(name, loc, parent)
    o = cylinder(f"{name}_body", r, h, (0, 0, h / 2), M.principled(f"{name}_mat", color, rough=0.45, coat=0.2),
                 verts=64)
    o.parent = p.root
    geo.bevel(o, 0.03, 3)
    p.add("body", o)
    p.height = h
    p.mark("top", (0, 0, h))
    p.mark("front", (0, -r, h))
    return p


def press_pad(name="press", loc=(0, 0, 0), size=(2.4, 2.0, 0.6), parent=None):
    """A plain steel block that presses down; loc is its bottom centre."""
    p = Part(name, loc, parent)
    p.add("block", box(f"{name}_block", size, (0, 0, size[2] / 2), M.steel("press_steel"), 0.03, parent=p.root))
    rod = cylinder(f"{name}_rod", size[0] * 0.12, 6.0, (0, 0, size[2] + 3.0), M.steel("press_steel"), verts=24)
    rod.parent = p.root
    p.mark("bottom", (0, -size[1] / 2, 0))
    return p


def crack(name, die, length=2.6, depth=0.004, width=0.012, zigs=11, seed=3, x0=None, y=None, amp=0.22):
    """A jagged dark crack across the top face of `die` (a slab of known
    size), growing from its left end: tween crack.scale.x 0 to 1. Hidden
    until shown (shot.show)."""
    import random

    rnd = random.Random(seed)
    sx, sy, sz = die.dimensions
    x0 = -length / 2 if x0 is None else x0
    pts = []
    for k in range(zigs + 1):
        u = k / zigs
        pts.append((u * length, rnd.uniform(-amp, amp) * (0.4 + u), 0.0))
    verts, faces = [], []
    for k, (x, y, _) in enumerate(pts):
        w = width * (1.0 - 0.6 * k / zigs)
        verts += [(x, y - w, 0.0), (x, y + w, 0.0)]
        if k:
            a = 2 * (k - 1)
            faces.append((a, a + 2, a + 3, a + 1))
    o = geo.mesh(name, verts, faces, (x0, 0.1 * sy if y is None else y, sz / 2 + depth), M.principled("crack_dark", (0.004, 0.004, 0.005),
                                                                                 rough=0.9), die)
    o.scale.x = 0.0005
    o.hide_render = True
    # bend with the die if it bends: same modifier, in the die's own space
    pivot = geo.empty(f"{name}_pivot", (0, 0, 0), die, size=0.05)
    _bend_mod(o, origin=pivot)
    return o


def mould_cavity(name="mould", loc=(0, 0, 0), inner=(5.0, 3.8, 1.8), wall=0.7, parent=None):
    """A steel mould in two halves, drawn as a cutaway (no front wall) so the
    stack inside stays in view. loc is the cavity's floor centre. Parts:
    left, right (each with half the back wall); points: inside, front."""
    p = Part(name, loc, parent)
    w, d, h = inner
    steel = M.steel("mould_steel")
    for key, s in (("left", -1), ("right", 1)):
        half = Part(f"{name}_{key}", (0, 0, 0), p.root)
        box(f"{name}_{key}_wall", (wall, d + wall, h + wall), (s * (w / 2 + wall / 2), wall / 2, (h + wall) / 2 - wall / 2),
            steel, 0.02, parent=half.root)
        box(f"{name}_{key}_back", (w / 2, wall, h + wall), (s * w / 4, d / 2 + wall / 2, (h + wall) / 2 - wall / 2),
            steel, 0.02, parent=half.root)
        box(f"{name}_{key}_lid", (w / 2 + wall, d + wall, wall), (s * (w / 4 + wall / 2), wall / 2, h + wall / 2),
            steel, 0.02, parent=half.root)
        p.objs[key] = half.root
    p.add("floor", box(f"{name}_floor", (w + 2 * wall, d + wall, wall), (0, wall / 2, -wall / 2), steel, 0.02,
                       parent=p.root))
    p.mark("inside", (0, 0, h / 2))
    p.mark("front", (-w / 2, -d / 2, h * 0.8))
    return p


def bond_head(name="bond_head", loc=(0, 0, 0), size=(4.2, 3.2, 0.5), parent=None):
    """A thermo-compression bond head: a steel tool block on a shaft that can
    glow warm (bond_head.material). loc is its bottom face centre."""
    p = Part(name, loc, parent)
    m = warm_steel()
    p.material = m
    p.add("tool", box(f"{name}_tool", size, (0, 0, size[2] / 2), m, 0.03, parent=p.root))
    p.add("body", box(f"{name}_body", (size[0] * 0.7, size[1] * 0.7, 0.9), (0, 0, size[2] + 0.45),
                      M.steel("press_steel"), 0.04, parent=p.root))
    shaft = cylinder(f"{name}_shaft", 0.35, 6.0, (0, 0, size[2] + 0.9 + 3.0), M.steel("press_steel"), verts=24)
    shaft.parent = p.root
    p.mark("bottom", (size[0] / 2, -size[1] / 2, 0))
    return p


def torus(name, R, r, loc=(0, 0, 0), mat=None, seg=96, ring=10, parent=None):
    verts, faces = [], []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        for j in range(ring):
            b = 2 * math.pi * j / ring
            verts.append(((R + r * math.cos(b)) * math.cos(a), (R + r * math.cos(b)) * math.sin(a), r * math.sin(b)))
    for i in range(seg):
        for j in range(ring):
            a, b2 = i * ring + j, i * ring + (j + 1) % ring
            c, d2 = ((i + 1) % seg) * ring + (j + 1) % ring, ((i + 1) % seg) * ring + j
            faces.append((a, d2, c, b2))
    return geo.mesh(name, verts, faces, loc, mat, parent, smooth=True)


def glow_ring(name, R, loc=(0, 0, 0), r=0.025, color=(0.25, 1.0, 0.4), parent=None):
    """A thin ring of light (round a pedestal's top); its material starts
    dark: effects.glow(shot, ring.active_material, a, b, strength)."""
    return torus(name, R, r, loc, M.emission(f"{name}_glow", color, 0.0), parent=parent)


# --- wafers and memory modules ------------------------------------------

def wafer_grid(name="wafer_grid", color=(0.5, 0.5, 0.66), cells=(0.26, 0.32)):
    """A patterned 300 mm wafer: polished silicon with a grid of dies."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = M._n(nt, "ShaderNodeMapping", Scale=(1.0 / cells[0], 1.0 / cells[1], 1.0))
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    brick = M._n(nt, "ShaderNodeTexBrick", _offset=0.0, Scale=1.0, **{
        "Mortar Size": 0.04, "Brick Width": 1.0, "Row Height": 1.0, "Color1": (*color, 1),
        "Color2": (*(c * 0.9 for c in color), 1), "Mortar": (0.62, 0.64, 0.72, 1)})
    nt.links.new(mp.outputs["Vector"], brick.inputs["Vector"])
    nt.links.new(brick.outputs["Color"], b.inputs["Base Color"])
    # a faint rainbow sheen, as patterned wafers have
    M._set(b, metallic=0.35, rough=0.16, coat=0.5, coat_rough=0.03, film=320.0)
    return m


def wafer_disc(name="wafer", loc=(0, 0, 0), r=3.0, t=0.035, rot=(0, 0, 0), parent=None):
    """A full 300 mm wafer (r units) with a die grid on its face, a flat
    edge ring. loc is its centre; rot in degrees (90, 0, 0 stands it up)."""
    p = Part(name, loc, parent)
    p.root.rotation_euler = [math.radians(a) for a in rot]
    o = cylinder(f"{name}_disc", r, t, (0, 0, 0), wafer_grid(), verts=96)
    o.parent = p.root
    for poly in o.data.polygons:  # flat faces, or smooth shading domes them like a lens
        if abs(poly.normal.z) > 0.99:
            poly.use_smooth = False
    edge = M.principled("wafer_edge", (0.55, 0.56, 0.62), metallic=0.7, rough=0.25)
    ring = torus(f"{name}_edge", r, t / 2, (0, 0, 0), edge, seg=96, ring=8, parent=p.root)
    p.add("disc", o)
    p.add("edge", ring)
    p.mark("top", (0, r, 0))
    p.mark("center", (0, 0, t / 2))
    return p


def wafer_rack(name="rack", loc=(0, 0, 0), length=14.0, depth=1.2, slots=12, pitch=1.0, parent=None):
    """A long aluminium cassette base with slots that wafers stand in.
    loc is its bottom centre; slots run along x."""
    p = Part(name, loc, parent)
    al = M.aluminium("rack_al", 0.35)
    p.add("base", box(f"{name}_base", (length, depth, 0.35), (0, 0, 0.175), al, 0.04, parent=p.root))
    for s in (-1, 1):
        p.add(f"rail{s}", box(f"{name}_rail{s}", (length, 0.18, 0.6), (0, s * (depth / 2 - 0.09), 0.65), al, 0.03,
                              parent=p.root))
    tooth = box(f"{name}_tooth", (0.08, depth - 0.36, 0.25), (-(slots - 1) * pitch / 2 - pitch / 2, 0, 0.47), al,
                0.02, parent=p.root)
    geo.grid_array(tooth, slots + 1, pitch)
    p.mark("top", (0, 0, 0.6))
    return p


def dimm(name="dimm", loc=(0, 0, 0), size=(3.3, 0.78, 0.03), chips=8, rot=(0, 0, 0), parent=None):
    """An ordinary memory module (DIMM) lying flat: green board, blank black
    chips, gold fingers along its long edge. loc is the board's bottom centre."""
    p = Part(name, loc, parent)
    p.root.rotation_euler = [math.radians(a) for a in rot]
    w, d, t = size
    p.add("board", box(f"{name}_pcb", (w, d, t), (0, 0, t / 2), M.solder_mask("dimm_mask", (0.02, 0.11, 0.045),
                                                                           traces=True, seed=9, scale=3.0),
                       0.004, parent=p.root))
    cw = w / (chips + 1.2)
    chip = box(f"{name}_chip", (cw * 0.82, d * 0.42, 0.035), (-w / 2 + cw * 0.85, 0.05 * d, t + 0.0175), M.mould(),
               0.005, parent=p.root)
    geo.grid_array(chip, chips, cw * 1.03)
    p.add("chips", chip)
    fing = box(f"{name}_finger", (0.022, 0.09, 0.004), (-w / 2 + 0.1, -d / 2 + 0.05, t + 0.002),
               M.gold("finger_gold", 0.25), 0, parent=p.root)
    geo.grid_array(fing, int((w - 0.2) / 0.04), 0.04)
    p.add("fingers", fing)
    p.height = t + 0.035
    p.mark("top", (0, 0, p.height))
    return p


# --- lines and grids ----------------------------------------------------

def trend_line(name, points, radius=0.04, color=(1.0, 0.32, 0.12), strength=6.0, parent=None):
    """A glowing line through `points` that draws itself: tween
    line.data.bevel_factor_end from 0 to 1 (trend_grow does)."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    sp.points.add(len(points) - 1)
    for p, xyz in zip(sp.points, points):
        p.co = (*xyz, 1.0)
    cu.bevel_depth = radius
    cu.bevel_resolution = 4
    cu.use_fill_caps = True
    cu.bevel_factor_mapping_end = "SPLINE"
    cu.bevel_factor_end = 0.0
    o = bpy.data.objects.new(name, cu)
    cu.materials.append(M.emission(f"{name}_glow", color, strength))
    return geo.link(o, parent)


def trend_grow(shot, line, start, end, ease="inout"):
    shot.tween(line.data, "bevel_factor_end", 1.0, start, end, ease, frm=0.0)
    return line


def animate_grid(shot, obj, points, fn):
    """Move an instance_points object's points every frame: fn(t) returns a
    scale factor (or an (sx, sy) pair) applied to the base `points` about the
    origin, e.g. a microbump grid packing closer."""
    me = obj.data
    base = [tuple(p) for p in points]

    def update(t):
        k = fn(t)
        sx, sy = (k, k) if isinstance(k, (int, float)) else k
        co = []
        for x, y, z in base:
            co.extend((x * sx, y * sy, z))
        me.vertices.foreach_set("co", co)
        me.update()

    shot.on_frame(update)
    return obj


def lit_interposer(obj, name=None, color=(0.25, 1.0, 0.4), lines=60.0):
    """Give one interposer object its own material whose copper wiring can
    glow: fine lines along x (die to stacks). Returns the material; tween
    M.emission_socket(mat) from 0 to light it."""
    name = name or f"{obj.name}_lit"
    m = bpy.data.materials.get(name)
    if m is None:
        m, nt, b = M.node_mat(name)
        M._set(b, color=(0.30, 0.30, 0.34), metallic=0.8, rough=0.18)
        tc = nt.nodes.new("ShaderNodeTexCoord")
        wave = M._n(nt, "ShaderNodeTexWave", _wave_type="BANDS", _bands_direction="Y", Scale=lines, Distortion=0.0,
                    Detail=0.0)
        nt.links.new(tc.outputs["Object"], wave.inputs["Vector"])
        ramp = M._n(nt, "ShaderNodeMapRange", **{"From Min": 0.82, "From Max": 0.95})
        nt.links.new(wave.outputs["Fac"], ramp.inputs["Value"])
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.inputs["A"].default_value = (0, 0, 0, 1)
        mix.inputs["B"].default_value = (*color, 1)
        nt.links.new(ramp.outputs["Result"], mix.inputs["Factor"])
        nt.links.new(mix.outputs["Result"], b.inputs["Emission Color"])
        b.inputs["Emission Strength"].default_value = 0.0
    obj.data.materials.clear()
    obj.data.materials.append(m)
    return m


def bonding_pair(name="pair", loc=(0, 0, 0), n=8, size=(2.2, 1.7), t=0.09, gap=0.1, spacing=3.4, parent=None):
    """Two stacks of n dies side by side for comparing joins: on the left
    microbumps in underfill (gap tall), on the right hybrid bonded (dies
    touching). Returns a Part with .micro and .hybrid (the two stacks) and
    the points micro_top and hybrid_top."""
    p = Part(name, loc, parent)
    fill = M.epoxy("pair_underfill", tint=(0.92, 0.7, 0.3), density=1.5)
    micro = silicon_stack(f"{name}_mb", (-spacing / 2, 0, 0), n=n, size=size, t=t, gap=gap,
                          cols=[-0.07, 0.0, 0.07], rows=[-size[1] / 2 + 0.12 + k * 0.13 for k in range(int((size[1] - 0.24) / 0.13) + 1)],
                          bump_r=0.02, parent=p.root)
    for i in range(1, len(micro.layers)):
        box(f"{name}_mbfill{i}", (size[0], size[1] * 0.995, micro.gap * 0.999), (0, 0, micro.gap_z(i)), fill, 0,
            parent=micro.root)
    hyb = silicon_stack(f"{name}_hb", (spacing / 2, 0, 0), n=n, size=size, t=t, gap=0.004, cols=[], rows=[],
                        parent=p.root)
    p.micro, p.hybrid = micro, hyb
    p.mark("micro_top", (-spacing / 2, 0, micro.height))
    p.mark("hybrid_top", (spacing / 2, 0, hyb.height))
    return p
