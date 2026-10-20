"""Process-room pieces for the wafer scenes (SET TSV): the etch chamber with
its RF coil, drifting particles (fluorine radicals, SiF4 leaving), and a fix
for the grey studio's rim light.

    from blenderkc.kit import process
    rig = kit.rigs.grey_studio(shot, scale=0.4)
    ch = process.chamber("chamber", (0, 0, 0), r=2.4, h=3.2)
    process.glow_coil(shot, ch, 2.0, 2.6, strength=8)
    process.drift(shot, "radicals", (0, 0, 2.8), (2.4, 1.0), 1.0, 5.0, count=80, rise=-0.6)
"""
import math
import random

import bmesh
import bpy

from . import geo
from . import materials as M
from .geo import Part


def no_backdrop_glint(rig, key="rim"):
    """Keep one of the rig's lights off the backdrop (light linking), so its
    reflection does not sit on the curved wall as a bright blob behind a
    close subject. Everything else keeps that light. grey_studio does this
    for its rim light by default now; this stays for other lights and for
    the scripts that already call it."""
    from .rigs import keep_off_backdrop

    return keep_off_backdrop(rig, key)


def _helix(name, R, r, z0, z1, turns, mat, seg=24):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = r
    cu.bevel_resolution = 3
    sp = cu.splines.new("POLY")
    n = int(turns * seg) + 1
    sp.points.add(n - 1)
    co = []
    for i in range(n):
        a = 2 * math.pi * i / seg
        co.extend((R * math.cos(a), R * math.sin(a), z0 + (z1 - z0) * i / (n - 1), 1.0))
    sp.points.foreach_set("co", co)
    o = bpy.data.objects.new(name, cu)
    cu.materials.append(mat)
    return geo.link(o)


def chamber(name="chamber", loc=(0, 0, 0), r=2.4, h=3.2, wall=0.04, coil_turns=4.5, open_front=True,
            parent=None):
    """A glass process chamber: a cylinder of radius r and height h standing
    on loc, cut open toward -y (open_front) so the camera sees in, a steel
    base and lid ring, and a copper RF coil wound round its top third.
    Points: coil, inside, rim. The coil's material is ch.coil_mat."""
    p = Part(name, loc, parent)
    # see-through by alpha, not refraction: a clean, cheap glass that only catches highlights
    glass = M.principled(f"{name}_glass", (0.85, 0.9, 0.95), rough=0.05, coat=1.0, coat_rough=0.02, alpha=0.12)
    steel = M.steel()
    bm = bmesh.new()
    seg = 64
    a0, a1 = (math.radians(-60), math.radians(240)) if open_front else (0.0, 2 * math.pi)
    rows = []
    for rr in (r, r - wall):
        ring = []
        for i in range(seg + 1):
            a = a0 + (a1 - a0) * i / seg
            ring.append((bm.verts.new((rr * math.cos(a), rr * math.sin(a), 0.0)),
                         bm.verts.new((rr * math.cos(a), rr * math.sin(a), h))))
        rows.append(ring)
    out, inn = rows
    for i in range(seg):
        bm.faces.new((out[i][0], out[i + 1][0], out[i + 1][1], out[i][1]))
        bm.faces.new((inn[i][1], inn[i + 1][1], inn[i + 1][0], inn[i][0]))
        bm.faces.new((out[i][1], out[i + 1][1], inn[i + 1][1], inn[i][1]))
        bm.faces.new((inn[i][0], inn[i + 1][0], out[i + 1][0], out[i][0]))
    for k in (0, seg):
        bm.faces.new((out[k][0], out[k][1], inn[k][1], inn[k][0]))
    me = bpy.data.meshes.new(f"{name}_wall")
    bm.to_mesh(me)
    bm.free()
    me.shade_smooth()
    wall_o = bpy.data.objects.new(f"{name}_wall", me)
    me.materials.append(glass)
    geo.link(wall_o, p.root)
    p.add("wall", wall_o)
    base = geo.cylinder(f"{name}_base", r + 0.12, 0.16, (0, 0, -0.08), steel, verts=64, parent=p.root)
    p.add("base", base)
    p.coil_mat = M.principled(f"{name}_coil", (0.95, 0.55, 0.30), metallic=1.0, rough=0.25,
                              emission=(1.0, 0.32, 0.06), strength=0.0)
    coil = _helix(f"{name}_coil", r + 0.09, 0.045, h * 0.62, h * 0.94, coil_turns, p.coil_mat)
    coil.parent = p.root
    p.add("coil", coil)
    p.mark("coil", (r + 0.09, 0, h * 0.78))
    p.mark("inside", (0, 0, h * 0.6))
    p.mark("rim", (0, -r, h))
    p.r, p.h = r, h
    return p


def arc(name, R, r, loc, mat, a0=180.0, a1=360.0, seg=48):
    """A thin tube along a circular arc in the xz plane (degrees from +x,
    counter-clockwise; the default is the lower half): a bowl's outline on a
    cut face."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = r
    cu.bevel_resolution = 2
    sp = cu.splines.new("POLY")
    sp.points.add(seg)
    co = []
    for i in range(seg + 1):
        a = math.radians(a0 + (a1 - a0) * i / seg)
        co.extend((R * math.cos(a), 0.0, R * math.sin(a), 1.0))
    sp.points.foreach_set("co", co)
    o = bpy.data.objects.new(name, cu)
    o.location = loc
    cu.materials.append(mat)
    return geo.link(o)


def glow_coil(shot, ch, start, end, strength=8.0, ease="inout"):
    """The RF coil warms up (its copper glows)."""
    shot.tween(M.socket(ch.coil_mat, "Emission Strength"), "default_value", strength, start, end, ease)
    return ch


def drift(shot, name, loc, area, start, end, count=60, rise=-0.5, height=1.0, wander=0.08, r=0.01,
          color=(1.0, 0.95, 0.6), strength=12.0, seed=5, fade=0.5, parent=None):
    """Particles drifting through a box: `area` (x, y) wide and `height` tall
    centred on loc, moving `rise` units a second (negative: down) with a slow
    sideways wander, wrapping round, shown from start to end with their
    brightness fading in and out over `fade`. Instanced spheres moved every
    frame; returns the points object (its material: o["mat"])."""
    rnd = random.Random(seed)
    mat = M.emission(f"{name}_glow", color, 0.0)
    src = geo.source(f"_drift_{name}", lambda n: geo.sphere(n, r, mat=mat, segments=8, rings=4))
    seeds = [(rnd.uniform(-area[0] / 2, area[0] / 2), rnd.uniform(-area[1] / 2, area[1] / 2), rnd.random(),
              rnd.uniform(0, 2 * math.pi), rnd.uniform(0.7, 1.3)) for _ in range(count)]
    o = geo.instance_points(name, src, [(x, y, 0.0) for x, y, *_ in seeds], loc, parent)
    o.visible_shadow = False
    me = o.data
    far = (0.0, 0.0, 1000.0)

    def update(t):
        co = []
        live = start <= t < end
        for x, y, ph, a, sp in seeds:
            if not live:
                co.extend(far)
                continue
            u = (ph + (t - start) * rise * sp / height) % 1.0
            z = (u - 0.5) * height
            w = wander * math.sin(t * 1.3 * sp + a)
            co.extend((x + w, y + wander * 0.5 * math.cos(t * 1.1 * sp + a), z))
        me.vertices.foreach_set("co", co)
        me.update()

    shot.on_frame(update)
    sock = M.emission_socket(mat)
    shot.tween(sock, "default_value", strength, start, start + fade, frm=0.0)
    if end is not None:
        shot.tween(sock, "default_value", 0.0, max(start + fade, end - fade), end)
    o["mat"] = mat.name
    return o
