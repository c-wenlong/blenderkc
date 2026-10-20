"""The shot camera: a camera that tracks a look-at empty, with depth of field
focused on a second empty. Every move is a tween over seconds.

    cam = shot.camera                      # made on first use
    cam.set(pos=(-7.5, -9, 6.5), look=(0.3, 0, 0.2))
    cam.move(0, 2, pos=(-0.6, -6.2, 3.2), look=(1.2, -0.4, 0.35))
    cam.orbit(2, 6, degrees=40)            # about the look point
    cam.push_in(6, 8, 0.25)                # a quarter of the way to the look point
    cam.rack_focus(7, 8, to=some_object)
"""
import math

import bpy
from mathutils import Vector

from .points import point_of

DEFAULT_POS = (-7.5, -9.0, 6.5)
DEFAULT_LOOK = (0.3, 0.0, 0.2)


class CameraRig:
    def __init__(self, shot, pos=DEFAULT_POS, look=DEFAULT_LOOK, lens=50.0, fstop=2.2, dof=True):
        self.shot = shot
        scene = bpy.context.scene
        data = bpy.data.cameras.new("shot_cam")
        data.lens = lens
        data.sensor_width = 36.0
        data.clip_start = 0.01
        data.clip_end = 500.0
        data.dof.use_dof = dof
        data.dof.aperture_fstop = fstop
        data.dof.aperture_blades = 7
        self.data = data
        self.obj = bpy.data.objects.new("shot_cam", data)
        scene.collection.objects.link(self.obj)
        scene.camera = self.obj
        self.target = bpy.data.objects.new("cam_look", None)
        self.focus = bpy.data.objects.new("cam_focus", None)
        for e in (self.target, self.focus):
            e.empty_display_size = 0.2
            scene.collection.objects.link(e)
        data.dof.focus_object = self.focus
        c = self.obj.constraints.new("TRACK_TO")
        c.target = self.target
        c.track_axis, c.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
        self.obj.location = pos
        self.target.location = look
        self.focus.location = look
        self._drift = None
        self._follows = 0
        # tracks capture these as their initial values
        tr = shot.tracks
        for o in (self.obj, self.target, self.focus):
            tr.track(o, "location")
        tr.track(self.data, "lens")
        tr.track(self.data.dof, "aperture_fstop")

    # -- state at a time ------------------------------------------------
    def pos_at(self, t):
        return Vector(self.shot.tracks.value(self.obj, "location", t))

    def look_at_time(self, t):
        """Where the camera looks at time t, follow() included."""
        if self._follows:
            return self._evaluated(self.target, t)
        return Vector(self.shot.tracks.value(self.target, "location", t))

    def _evaluated(self, obj, t):
        # a follow constraint moves the empty: set the tracks to time t, read
        # the result, then put every tracked value back exactly as it was.
        # on_frame callbacks are not run: they may hold state, or be the ones
        # asking. So a subject moved only by a callback is not seen here;
        # move followed subjects with tweens, or give the camera explicit points.
        tr = self.shot.tracks
        snap = tr.snapshot()
        tr.apply(t, callbacks=False)
        bpy.context.view_layer.update()
        p = obj.matrix_world.translation.copy()
        tr.restore(snap)
        bpy.context.view_layer.update()
        return p

    # -- instant --------------------------------------------------------
    def set(self, pos=None, look=None, lens=None, fstop=None, focus=None, at=0.0):
        """Jump to a framing at time `at` (a cut)."""
        tr = self.shot.tracks
        if pos is not None:
            tr.set(self.obj, "location", tuple(point_of(pos)), at)
        if look is not None:
            tr.set(self.target, "location", tuple(point_of(look)), at)
            if focus is None:
                tr.set(self.focus, "location", tuple(point_of(look)), at)
        if focus is not None:
            tr.set(self.focus, "location", tuple(point_of(focus)), at)
        if lens is not None:
            tr.set(self.data, "lens", float(lens), at)
        if fstop is not None:
            tr.set(self.data.dof, "aperture_fstop", float(fstop), at)
        return self

    cut = set

    # -- moves ----------------------------------------------------------
    def move(self, start, end, pos=None, look=None, lens=None, fstop=None, focus=None, ease="inout"):
        """Tween any of position, look point, lens (mm), f-stop and focus point.
        Focus follows the look point unless `focus` is given. An object or
        part.point() is read where it is when the move is scheduled; to keep
        a moving subject framed use follow()."""
        tr = self.shot.tracks
        if pos is not None:
            tr.tween(self.obj, "location", tuple(point_of(pos)), start, end, ease)
        if look is not None:
            tr.tween(self.target, "location", tuple(point_of(look)), start, end, ease)
            if focus is None:
                tr.tween(self.focus, "location", tuple(point_of(look)), start, end, ease)
        if focus is not None:
            tr.tween(self.focus, "location", tuple(point_of(focus)), start, end, ease)
        if lens is not None:
            tr.tween(self.data, "lens", float(lens), start, end, ease)
        if fstop is not None:
            tr.tween(self.data.dof, "aperture_fstop", float(fstop), start, end, ease)
        return self

    def through(self, keys, ease=None):
        """Glide through framings [(t, pos, look), ...] without stopping at
        the middle ones (a smooth cubic, flat at the ends). Focus follows look."""
        tr = self.shot.tracks
        tr.spline(self.obj, "location", [(k[0], tuple(point_of(k[1]))) for k in keys])
        looks = [(k[0], tuple(point_of(k[2]))) for k in keys]
        tr.spline(self.target, "location", looks)
        tr.spline(self.focus, "location", looks)
        return self

    def look_at(self, start, end, point, ease="inout", focus=True):
        return self.move(start, end, look=point, focus=None if focus else self.focus_at(start), ease=ease)

    def focus_at(self, t):
        if self._follows:
            return self._evaluated(self.focus, t)
        return Vector(self.shot.tracks.value(self.focus, "location", t))

    def rack_focus(self, start, end, to, ease="inout"):
        """Pull focus to a point or object without moving the camera."""
        self.shot.tracks.tween(self.focus, "location", tuple(point_of(to)), start, end, ease)
        return self

    def orbit(self, start, end, degrees, center=None, rise=0.0, radius=None, ease="inout"):
        """Swing round the vertical axis through `center` (default: the look
        point at `start`), optionally rising by `rise` and ending at `radius`."""
        p0 = self.pos_at(start)
        c = Vector(point_of(center)) if center is not None else self.look_at_time(start)
        off = p0 - c
        r0 = math.hypot(off.x, off.y)
        a0 = math.atan2(off.y, off.x)
        r1 = r0 if radius is None else float(radius)
        da = math.radians(degrees)

        def fn(u):
            a = a0 + da * u
            r = r0 + (r1 - r0) * u
            return (c.x + r * math.cos(a), c.y + r * math.sin(a), p0.z + rise * u)

        self.shot.tracks.path(self.obj, "location", fn, start, end, ease)
        return self

    def push_in(self, start, end, amount=0.3, ease="inout"):
        """Move toward the look point by `amount` of the distance (negative pulls out)."""
        p0, c = self.pos_at(start), self.look_at_time(start)
        return self.move(start, end, pos=p0 + (c - p0) * amount, ease=ease)

    def pull_out(self, start, end, amount=0.3, ease="inout"):
        return self.push_in(start, end, -amount, ease)

    def dolly(self, start, end, distance, ease="inout"):
        """Travel along the view direction by `distance`, look point and all."""
        p0, c = self.pos_at(start), self.look_at_time(start)
        d = (c - p0).normalized() * distance
        return self.move(start, end, pos=p0 + d, look=c + d, ease=ease)

    def truck(self, start, end, offset, ease="inout"):
        """Slide camera and look point together by a world offset."""
        o = Vector(offset)
        return self.move(start, end, pos=self.pos_at(start) + o, look=self.look_at_time(start) + o, ease=ease)

    def zoom(self, start, end, lens, ease="inout"):
        return self.move(start, end, lens=lens, ease=ease)

    def follow(self, start, end, subject, blend=0.6, focus=True, ease="inout"):
        """Keep looking at (and focused on) a subject that moves, from start
        to end (None: to the scene's end), blending in and out over `blend`
        seconds. move(), set() and through() read a point once, when they are
        scheduled; follow() reads it every frame."""
        target = self._follow_target(subject)
        if end is not None:
            blend = min(blend, max(0.0, (end - start) / 2))  # both fades fit inside the follow
        self._follows += 1
        tr = self.shot.tracks
        for e in (self.target, self.focus) if focus else (self.target,):
            c = e.constraints.new("COPY_LOCATION")
            c.target = target
            c.influence = 0.0
            tr.tween(c, "influence", 1.0, start, start + blend, ease, frm=0.0)
            if end is not None:
                tr.tween(c, "influence", 0.0, end - blend, end, ease)
        return self

    def _follow_target(self, subject):
        from .points import AnchorRef

        if isinstance(subject, AnchorRef):
            e = bpy.data.objects.new(f"follow_{subject.name or subject.obj.name}", None)
            bpy.context.scene.collection.objects.link(e)
            e.parent = subject.obj
            e.location = subject.offset
            return e
        if hasattr(subject, "root") and not isinstance(subject, bpy.types.ID):
            return subject.root
        if isinstance(subject, bpy.types.Object):
            return subject
        raise TypeError("follow() needs an object, a part or part.point(...); a fixed point does not move")

    def drift(self, amount=0.03, period=7.0):
        """A slow float on top of every move, so a held shot still breathes."""
        if self._drift is None:
            self.shot.tracks.callbacks.append(self._apply_drift)
        self._drift = (float(amount), float(period))
        return self

    def _apply_drift(self, t):
        a, p = self._drift
        w = 2 * math.pi / p
        off = Vector((math.sin(w * t) * a, math.sin(w * t * 0.73 + 1.3) * a * 0.6, math.sin(w * t * 0.51 + 2.1) * a * 0.4))
        self.obj.location = self.pos_at(t) + off

    # -- framing --------------------------------------------------------
    def frame_box(self, lo, hi, direction=(-0.55, -0.7, 0.45), fill=0.8, at=0.0, lens=None):
        """A camera position that frames the box lo..hi from `direction`,
        returned (pos, look) for set() or move(). Uses `lens`, else the lens
        scheduled for time `at`."""
        lo, hi = Vector(lo), Vector(hi)
        c = (lo + hi) / 2
        radius = (hi - lo).length / 2
        aspect = bpy.context.scene.render.resolution_y / bpy.context.scene.render.resolution_x
        lens = float(lens) if lens is not None else float(self.shot.tracks.value(self.data, "lens", at))
        half = math.atan((self.data.sensor_width / 2) / lens)
        half = min(half, math.atan(math.tan(half) * aspect))
        dist = radius / math.sin(half) / fill
        d = Vector(direction).normalized()
        return c + d * dist, c

