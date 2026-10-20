"""Annotations in 3D: dimension bars and tinted regions on a cross section.

    from blenderkc.kit import annotate
    bar = annotate.dimension(shot, "wafer_h", (8, -0.7, 1.8), (8, -0.7, -18.9), 1.4, 2.9)
    tint = annotate.tint(shot, "rest", (-22, -18.9), (22, -2.2), y=-0.62, start=11.8, end=12.6)

They are lit geometry (emission), so they sit in the picture with depth of
field and occlusion, where an overlay would float on top of it. Pair a bar
with shot.glow or shot.label for its number.
"""
import math

import bpy
from mathutils import Vector

from . import geo
from . import materials as M

BAR = (0.42, 0.5, 1.0)


def dimension(shot, name, p0, p1, start, end, color=BAR, r=0.02, head=None, strength=6.0, fade=None,
              ease="inout", parent=None):
    """A glowing bar with an arrowhead at each end that draws itself from p0
    to p1 over [start, end], then stays (until `fade`, a time, if given).
    Returns a geo.Part with points "start", "end", "middle"."""
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    length = d.length
    head = head or r * 4.0
    mat = M.emission(f"{name}_glow", color, strength)
    part = geo.Part(name, tuple(p0), parent)
    rot = d.normalized().to_track_quat("Z", "Y").to_euler()
    part.root.rotation_euler = rot
    bar = geo.bake(geo.cylinder(f"{name}_bar", r, 1.0, (0, 0, 0.5), mat, verts=12))
    bar.parent = part.root
    part.add("bar", bar)
    bar.scale = (1.0, 1.0, 1e-4)
    shot.tween(bar, "scale.z", max(1e-4, length - 2 * head * 0.6), start, end, ease, frm=1e-4)
    shot.set(bar, "location.z", head * 0.6, 0.0)
    a = geo.cylinder(f"{name}_head0", head * 0.55, head, (0, 0, head / 2), mat, verts=16, r2=0.0)
    a.parent = part.root
    a.rotation_euler.x = math.pi  # pointing back at p0
    a.location.z = head / 2
    b = geo.cylinder(f"{name}_head1", head * 0.55, head, (0, 0, 0), mat, verts=16, r2=0.0)
    b.parent = part.root
    shot.tween(b, "location.z", length - head / 2, start, end, ease, frm=head / 2)
    for o in (bar, a, b):
        o.visible_shadow = False
        shot.show(o, start, fade)
    part.add("head0", a)
    part.add("head1", b)
    part.mark("start", (0, 0, 0))
    part.mark("end", (0, 0, length))
    part.mark("middle", (0, 0, length / 2))
    return part


def tint(shot, name, lo, hi, y, start, end, color=(0.9, 0.12, 0.08), alpha=0.45, glow=0.6, fade=None,
         parent=None):
    """A translucent coloured pane over the region lo..hi (x, z) of a section
    face at depth y (put it just in front of the face), fading in over
    [start, end] (and out from `fade` if given)."""
    m = bpy.data.materials.new(f"{name}_tint")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.remove(nt.nodes["Principled BSDF"])
    out = nt.nodes["Material Output"]
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1)
    em.inputs["Strength"].default_value = glow
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.inputs[0].default_value = 0.0
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    (x0, z0), (x1, z1) = lo, hi
    verts = [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)]
    o = geo.mesh(name, verts, [(0, 1, 2, 3)], mat=m, parent=parent)
    o.visible_shadow = False
    fac = mix.inputs[0]
    shot.tween(fac, "default_value", alpha, start, end, "inout", frm=0.0)
    if fade is not None:
        shot.tween(fac, "default_value", 0.0, fade, fade + (end - start), "inout")
    return o
