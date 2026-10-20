"""Studios: floor or backdrop, world light and area lights.

dark_studio: the approved reference look. A dark charcoal tiled floor, the
bundled CC0 "interior" HDRI desaturated at low strength, and a key, a cool rim
and a warm fill. Product shots of boards, packages and stacks.

grey_studio: a light grey seamless backdrop (floor curving into a wall) under
big soft lights. Cross sections, wafers, process steps.

Both take `scale` (1 = the reference, where a GPU package is ~7 units wide):
light positions and sizes scale with it and power with its square, so a 0.5
unit wafer section can sit in a studio sized for it.
"""
import math
import os

import bmesh
import bpy

from . import geo
from . import materials as M


class Rig:
    def __init__(self, name):
        self.name = name
        self.lights = {}
        self.floor = None
        self.world = None
        self.hdri = None

    def light(self, key):
        return self.lights[key]


def hdri_path(name="interior"):
    return os.path.join(bpy.utils.system_resource("DATAFILES"), "studiolights", "world", f"{name}.exr")


def world(hdri="interior", strength=0.16, saturation=0.15, rotation=110.0, color=None):
    """World lighting from a bundled HDRI (or a flat colour when hdri is None)."""
    w = bpy.data.worlds.new("world")
    bpy.context.scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    bg = nt.nodes["Background"]
    bg.inputs["Strength"].default_value = strength
    if hdri is None:
        bg.inputs["Color"].default_value = (*(color or (0.05, 0.05, 0.05)), 1)
        return w
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(hdri_path(hdri), check_existing=True)
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Rotation"].default_value[2] = math.radians(rotation)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
    hsv = nt.nodes.new("ShaderNodeHueSaturation")
    hsv.inputs["Saturation"].default_value = saturation
    nt.links.new(env.outputs["Color"], hsv.inputs["Color"])
    nt.links.new(hsv.outputs["Color"], bg.inputs["Color"])
    return w


def area(name, loc, rot, size, power, color=(1, 1, 1), spread=180.0):
    d = bpy.data.lights.new(name, "AREA")
    d.shape = "RECTANGLE"
    d.size, d.size_y = size
    d.energy = power
    d.color = color
    d.spread = math.radians(spread)
    o = bpy.data.objects.new(name, d)
    o.location = loc
    o.rotation_euler = [math.radians(r) for r in rot]
    return geo.link(o)


def _s(v, k):
    return tuple(x * k for x in v)


def dark_studio(shot=None, scale=1.0, floor=True, hdri_strength=0.16, key=700.0, rim=500.0, fill=120.0,
                tile_scale=3.0):
    """The reference rig. Returns a Rig with lights "key", "rim", "fill"."""
    r = Rig("dark_studio")
    k2 = scale * scale
    if floor:
        r.floor = geo.plane("floor", 400 * scale, (0, 0, 0), M.floor_tiles(scale=tile_scale / scale))
    r.world = world("interior", hdri_strength, 0.15, 110.0)
    r.lights["key"] = area("key", _s((2.0, -3.0, 7.0), scale), (25, 0, 20), _s((10, 6), scale), key * k2)
    r.lights["rim"] = area("rim", _s((-4.0, 6.0, 3.0), scale), (-70, 0, 200), _s((5, 1), scale), rim * k2,
                           (0.75, 0.85, 1.0))
    r.lights["fill"] = area("fill", _s((-6.0, -4.0, 2.5), scale), (70, 0, -55), _s((3, 3), scale), fill * k2,
                            (1.0, 0.9, 0.8))
    return r


def cyclorama(name, width, depth, height, radius, mat):
    """A seamless backdrop: floor from y=-depth to 0, a quarter round of
    `radius`, then a wall up to `height`, `width` wide in x, centred on x=0."""
    prof = [(-depth, 0.0), (0.0, 0.0)]
    steps = 16
    for i in range(steps + 1):
        a = (math.pi / 2) * i / steps
        if i:
            prof.append((radius * math.sin(a), radius - radius * math.cos(a)))
    prof.append((radius, height))
    bm = bmesh.new()
    rows = []
    for x in (-width / 2, width / 2):
        rows.append([bm.verts.new((x, y, z)) for (y, z) in prof])
    for i in range(len(prof) - 1):
        bm.faces.new((rows[0][i], rows[1][i], rows[1][i + 1], rows[0][i + 1]))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.shade_smooth()
    o = bpy.data.objects.new(name, me)
    me.materials.append(mat)
    return geo.link(o)


def grey_studio(shot=None, scale=1.0, color=(0.42, 0.42, 0.43), tiles=0.0, hdri_strength=0.55, key=420.0,
                top=260.0, rim=160.0, wall_distance=6.0, rim_on_backdrop=False):
    """Light grey seamless studio. The wall stands `wall_distance` (x scale)
    behind the origin along +y. Returns a Rig with lights "key", "top", "rim".

    The rim light sits less than a unit (x scale) above the backdrop's curve,
    so on the backdrop it is a large bright blob behind the subject, top
    right in most framings. By default it is light-linked off the backdrop
    (it still lights and glints on everything else); rim_on_backdrop=True
    keeps the old look."""
    r = Rig("grey_studio")
    k2 = scale * scale
    mat = M.backdrop(color=color, tiles=tiles / scale if tiles else 0.0)
    cyc = cyclorama("backdrop", 80 * scale, 40 * scale, 30 * scale, 6 * scale, mat)
    cyc.location = (0, wall_distance * scale - 6 * scale, 0)
    r.floor = cyc
    r.world = world("interior", hdri_strength, 0.25, 110.0)
    r.lights["key"] = area("key", _s((-3.0, -5.0, 6.0), scale), (45, 0, -30), _s((8, 8), scale), key * k2)
    r.lights["top"] = area("top", _s((1.0, 0.0, 8.0), scale), (0, 0, 0), _s((10, 6), scale), top * k2)
    r.lights["rim"] = area("rim", _s((4.0, 4.0, 3.0), scale), (-65, 0, 140), _s((6, 2), scale), rim * k2,
                           (0.92, 0.95, 1.0))
    if not rim_on_backdrop:
        keep_off_backdrop(r, "rim")
    return r


def keep_off_backdrop(rig, key="rim"):
    """Light-link one of the rig's lights off its floor or backdrop, so the
    light does not land on it; everything else keeps the light. Safe to call
    twice, and a receiver collection the light already has keeps its other
    entries as they were."""
    light = rig.lights[key]
    coll = light.light_linking.receiver_collection
    if coll is None:
        coll = bpy.data.collections.new(f"{light.name}_receivers")
        light.light_linking.receiver_collection = coll
    if rig.floor.name not in coll.objects:
        coll.objects.link(rig.floor)
    # collection_objects holds one entry per object, in the order of coll.objects
    i = list(coll.objects).index(rig.floor)
    coll.collection_objects[i].light_linking.link_state = "EXCLUDE"
    return light
