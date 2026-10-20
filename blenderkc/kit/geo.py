"""Geometry helpers: boxes, cylinders, arrays, instancing, and Part.

Everything is built with bpy.data and bmesh (no operators), linked to the
scene's collection, and optionally parented. Units follow the reference shot:
a GPU package is about 7 units across.
"""
import math

import bmesh
import bpy
from mathutils import Matrix, Vector

from ..points import AnchorRef


def link(obj, parent=None):
    bpy.context.scene.collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    return obj


def _finish(name, bm, mat, loc, parent, smooth=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        me.shade_smooth()
    o = bpy.data.objects.new(name, me)
    o.location = loc
    if mat is not None:
        me.materials.append(mat)
    return link(o, parent)


def bevel(obj, width, segments=2, harden=False):
    """Rounded edges. Unhardened normals (the reference's look) curve the
    shading softly across big faces, which catches the key light."""
    m = obj.modifiers.new("bevel", "BEVEL")
    m.width = width
    m.segments = segments
    m.limit_method = "ANGLE"
    m.harden_normals = harden
    return m


def box(name, size, loc=(0, 0, 0), mat=None, bevel_width=0.004, segments=2, parent=None):
    """An axis-aligned box of size (x, y, z) centred on loc. Bevelled boxes
    shade smooth (the reference's soft edges); bevel_width=0 gives a crisp,
    flat-shaded block (cross sections, booleans)."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    o = _finish(name, bm, mat, loc, parent, smooth=bool(bevel_width))
    if bevel_width:
        bevel(o, min(bevel_width, min(size) * 0.45), segments)
    return o


def cylinder(name, r, h, loc=(0, 0, 0), mat=None, verts=16, axis="Z", parent=None, cap=True, r2=None):
    """A cylinder (or cone with r2) of radius r and height h centred on loc."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=cap, cap_tris=False, segments=verts, radius1=r,
                          radius2=r if r2 is None else r2, depth=h)
    if axis == "X":
        bmesh.ops.rotate(bm, verts=bm.verts, matrix=Matrix.Rotation(math.pi / 2, 3, "Y"))
    elif axis == "Y":
        bmesh.ops.rotate(bm, verts=bm.verts, matrix=Matrix.Rotation(math.pi / 2, 3, "X"))
    return _finish(name, bm, mat, loc, parent)


def sphere(name, r, loc=(0, 0, 0), mat=None, segments=16, rings=8, parent=None, half=None):
    """A UV sphere; half="+y" keeps only the y >= 0 half, closed flat (for cut
    faces in cross sections), likewise "-y", "+z", "-z"."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=r)
    if half:
        _halve(bm, half)
    o = _finish(name, bm, mat, loc, parent)
    if half:
        _flat_cut(o, half)
    return o


def _flat_cut(obj, half):
    """Shade the flat cut face flat (smooth shading would bend it)."""
    axis = "xyz".index(half[1])
    for poly in obj.data.polygons:
        if abs(poly.normal[axis]) > 0.999:
            poly.use_smooth = False


def _halve(bm, half):
    sign = 1 if half[0] == "+" else -1
    axis = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[half[1]]
    n = Vector(axis) * sign
    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    res = bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(0, 0, 0), plane_no=n, clear_inner=True)
    edges = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
    if edges:
        bmesh.ops.edgeloop_fill(bm, edges=edges)


def halve_object(obj, half="+y"):
    """Cut a mesh object in half through its origin and close the cut, e.g.
    a via or ball sitting on a cross section's face."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    _halve(bm, half)
    bm.to_mesh(obj.data)
    bm.free()
    _flat_cut(obj, half)
    return obj


def plane(name, size, loc=(0, 0, 0), mat=None, parent=None):
    sx, sy = (size, size) if isinstance(size, (int, float)) else size
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)
    bmesh.ops.scale(bm, vec=(sx, sy, 1), verts=bm.verts)
    return _finish(name, bm, mat, loc, parent, smooth=False)


def mesh(name, verts, faces, loc=(0, 0, 0), mat=None, parent=None, smooth=False):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    me.update()
    if smooth:
        me.shade_smooth()
    o = bpy.data.objects.new(name, me)
    o.location = loc
    if mat is not None:
        me.materials.append(mat)
    return link(o, parent)


def empty(name, loc=(0, 0, 0), parent=None, size=0.2):
    e = bpy.data.objects.new(name, None)
    e.empty_display_size = size
    e.location = loc
    return link(e, parent)


def grid_array(obj, nx, dx, ny=1, dy=0.0):
    """Repeat obj nx times along +x (step dx) and ny times along +y (step dy)."""
    if nx > 1:
        a = obj.modifiers.new("ax", "ARRAY")
        a.use_relative_offset, a.use_constant_offset = False, True
        a.count, a.constant_offset_displace = nx, (dx, 0, 0)
    if ny > 1:
        b = obj.modifiers.new("ay", "ARRAY")
        b.use_relative_offset, b.use_constant_offset = False, True
        b.count, b.constant_offset_displace = ny, (0, dy, 0)
    return obj


def _instancer_group(src):
    """A node group instancing `src` on every point of its geometry; one group
    per source, with the object set on the node (no modifier inputs, whose
    API changed in Blender 5)."""
    name = f"bkc_instance_{src.name}"
    ng = bpy.data.node_groups.get(name)
    if ng:
        return ng
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = ng.nodes
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    info = n.new("GeometryNodeObjectInfo")
    info.transform_space = "ORIGINAL"
    info.inputs["Object"].default_value = src
    inst = n.new("GeometryNodeInstanceOnPoints")
    ng.links.new(gi.outputs[0], inst.inputs["Points"])
    ng.links.new(info.outputs["Geometry"], inst.inputs["Instance"])
    ng.links.new(inst.outputs["Instances"], go.inputs[0])
    return ng


def instance_points(name, src, points, loc=(0, 0, 0), parent=None):
    """One instance of object `src` at each local point: cheap to render in
    any number. `src` is hidden from render; edit it to change them all."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(p) for p in points], [], [])
    o = bpy.data.objects.new(name, me)
    o.location = loc
    link(o, parent)
    mod = o.modifiers.new("instances", "NODES")
    mod.node_group = _instancer_group(src)
    src.hide_render = True
    return o


def grid_points(nx, ny, dx, dy, origin=(0, 0, 0), centered=True):
    ox, oy, oz = (tuple(origin) + (0.0,))[:3]
    if centered:
        ox -= (nx - 1) * dx / 2
        oy -= (ny - 1) * dy / 2
    return [(ox + i * dx, oy + j * dy, oz) for j in range(ny) for i in range(nx)]


def instance_grid(name, src, nx, ny, dx, dy, loc=(0, 0, 0), parent=None, centered=True):
    return instance_points(name, src, grid_points(nx, ny, dx, dy, centered=centered), loc, parent)


def bake(obj, scale=None):
    """Move an object's location (and an optional scale) into its mesh, so an
    instancer (which ignores the source's transform) sees it."""
    m = Matrix.Translation(obj.location)
    if scale is not None:
        m = m @ Matrix.Diagonal((*scale, 1.0))
    obj.data.transform(m)
    obj.location = (0, 0, 0)
    return obj


def source(name, maker):
    """A hidden source object for instancing, made once per scene."""
    o = bpy.data.objects.get(name)
    if o is None:
        o = maker(name)
        o.hide_render = True
        o.location = (0, 0, -1000)
    return o


class Part:
    """A built thing: a root empty, its named objects, and named points.

    part.root           move or tween it to move the whole part
    part["die"]         a named object
    part.point("top")   an AnchorRef for anchors, looks and focus
    """

    def __init__(self, name, loc=(0, 0, 0), parent=None):
        self.name = name
        self.root = empty(name, loc, parent, size=0.5)
        self.objs = {}
        self.points = {}
        self.data = {}

    def add(self, key, obj):
        self.objs[key] = obj
        if obj.parent is None:
            obj.parent = self.root
        return obj

    def __getitem__(self, key):
        return self.objs[key]

    def mark(self, name, local, obj=None):
        """Name a point in the root's (or obj's) local space."""
        self.points[name] = (obj or self.root, Vector(local))
        return self

    def point(self, name):
        if name not in self.points:
            raise KeyError(f"{self.name} has no point {name!r}; it has {', '.join(self.points) or 'none'}")
        obj, local = self.points[name]
        return AnchorRef(obj, local, f"{self.name}.{name}")

    @property
    def location(self):
        return self.root.location
