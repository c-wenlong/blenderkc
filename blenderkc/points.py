"""Points in the world: a fixed position, an object, or a point fixed to an object.

An AnchorRef is a point in an object's local space; it follows the object as it
moves. Parts hand them out (`part.point("top")`), and anchors, camera looks and
rack focus all accept them. Reading one never writes to bpy.
"""
import bpy
from mathutils import Vector


class AnchorRef:
    __slots__ = ("obj", "offset", "name")

    def __init__(self, obj, offset=(0.0, 0.0, 0.0), name=None):
        self.obj = obj
        self.offset = Vector(offset)
        self.name = name

    def world(self):
        return self.obj.matrix_world @ self.offset

    def __repr__(self):
        return f"AnchorRef({self.obj.name}, {tuple(self.offset)})"


def point_of(x, update=True):
    """World position of a point, object, AnchorRef or anything with .root."""
    if isinstance(x, AnchorRef):
        if update:
            bpy.context.view_layer.update()
        return x.world()
    if hasattr(x, "root") and not isinstance(x, bpy.types.ID):
        x = x.root
    if isinstance(x, bpy.types.Object):
        if update:
            bpy.context.view_layer.update()
        return x.matrix_world.translation.copy()
    return Vector(tuple(x))
