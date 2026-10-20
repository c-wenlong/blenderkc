"""The TSV cross-section (SET TSV): a silicon block cut through its vias, with
every stage of making a through-silicon via as an animatable value.

    sec = tsv.tsv_section("tsv", loc=(0, 0, 1.8))
    sec.etch_to(shot, 1.0, a, b)          # the first Bosch scoop grows from nothing
    sec.coat(shot, a, b)                  # polymer over every surface of the hole
    sec.punch(shot, a, b)                 # ions clear the floor's polymer
    sec.etch_to(shot, 2.0, a, b)          # the next scoop opens below the floor
    sec.bosch(shot, a, b, cycles=28)      # many cycles, etch / coat / punch each
    sec.strip_polymer(shot, a, b)
    sec.strip_resist(shot, a, b)
    sec.deposit(shot, "liner", a, b)      # then "barrier", "seed"
    sec.plate(shot, a, b)                 # copper grows from the floor up
    sec.thin(shot, a, b, to)              # grind the back (flip the root first)
    sec.reveal(shot, a, b, depth)         # every layer cut flat at depth: the copper ends

The hole is a stack of scoops ("scallops"). Scoop i is centred at depth
i x `pitch`, reaches `core + rho` across (wider than the resist opening: the
undercut) and `rho` up and down, and grows out of the floor the scoop before
it left. Its growth is `sec.s.etch - i` clamped to 0..1, so
`sec.s.etch = 2.5` is two full scoops and half a third; neighbours overlap,
leaving the faint ridges of a Bosch etch. Every layer that lines the hole (polymer, oxide
liner, Ta barrier, Cu seed, the copper fill) is a thin shell rebuilt each
frame from the current profile, with its cut edge drawn on the section face.

Anything here can also be tweened directly: shot.tween(sec.s, "liner", 1, a, b)
(the fields of TSVState), or shot.tween(coat, "amount", ...) on a coat.

Coordinates: loc is the centre of the wafer's top surface, the cut face is
toward -y at y = -size[1] / 2, holes sit on the cut face at x in `holes`.
Points: "rim", "wall", "floor" (moves with the etch), "surface", "resist",
"section", "bottom", "copper".
"""
import math

import bpy
from mathutils import Vector

from . import geo
from . import materials as M
from .geo import Part, box, cylinder

POLYMER = (0.25, 0.75, 0.35)


class TSVState:
    """The values a shot tweens: shot.tween(sec.s, "etch", 3.0, a, b)."""

    def __init__(self):
        self.etch = 0.0      # scoops etched (fractional: the last one is growing)
        self.open = 1.0      # resist opening, 0 (closed) to 1 (r_open)
        self.liner = 0.0     # 0..1 of each layer's full thickness
        self.barrier = 0.0
        self.seed = 0.0
        self.fill = 0.0      # copper plating, 0 (none) to 1 (full to the top)
        self.clip = -1.0     # cut every layer off below this depth (< 0: no clip)
        self.visible = 1.0   # below 0.5 the whole section is hidden (a cut to another set)


class Coat:
    """One polymer coat: it covers the hole above depth `lo` (z = -lo)."""

    def __init__(self, lo):
        self.amount = 0.0
        self.lo = lo


def _smooth(u):
    u = 0.0 if u < 0.0 else 1.0 if u > 1.0 else u
    return u * u * (3.0 - 2.0 * u)


def scoops(etch, core, rho, pitch):
    """[(centre z, rho_i)] for every scoop that has started, top first. A
    full scoop is half an ellipse each side of its centre (-i x pitch), rho
    across beyond `core` and rho tall; a growing one starts as a point on
    the floor the scoop before it left, and grows to that."""
    out = []
    i = 0
    while etch - i > 1e-5:
        g = min(1.0, etch - i)
        prev = 0.0 if i == 0 else -(i - 1) * pitch - rho
        out.append((prev + (-i * pitch - prev) * g, rho * g))
        i += 1
    return out


def wall_radius(z, sc, core):
    """The hole's radius at height z (z <= 0), or None outside every scoop."""
    best = None
    for zc, r in sc:
        if r > 0 and zc - r - 1e-9 <= z <= zc + r + 1e-9:
            u = (z - zc) / r
            v = core * min(1.0, r / 0.02) + r * math.sqrt(max(0.0, 1.0 - u * u))
            if best is None or v > best:
                best = v
    return best


def profile(etch, core, rho, pitch, k=12, floor_steps=6):
    """The hole's half profile as [(r, z)], from the rim (z = 0) down the wall
    and across the floor to the axis. Empty before any etching."""
    sc = scoops(etch, core, rho, pitch)
    if not sc:
        return []
    zs = {0.0}
    for zc, r in sc:
        for j in range(-k, k + 1):
            zs.add(zc + r * math.sin(0.5 * math.pi * j / k))
    for (z1, r1), (z2, r2) in zip(sc, sc[1:]):  # the ridges where two scoops meet
        if r1 > 0 and r2 > 0 and abs(z1 - z2) > 1e-9:
            zs.add(_ridge(z1, r1, z2, r2))
    bottom = min(zc - r for zc, r in sc)
    zs = sorted((z for z in zs if bottom - 1e-9 <= z <= 1e-9), reverse=True)
    pts = []
    for z in zs:
        r = wall_radius(z, sc, core)
        if r is None:
            continue
        if pts and abs(pts[-1][1] - z) < 1e-6:
            continue
        pts.append((r, z))
    r0 = pts[-1][0]
    for j in range(1, floor_steps + 1):
        pts.append((r0 * (1 - j / floor_steps), bottom))
    return pts


def _ridge(z1, r1, z2, r2):
    """Height where two scoop walls cross (bisection between their centres)."""
    lo, hi = min(z1, z2), max(z1, z2)

    def f(z):
        a = r1 * math.sqrt(max(0.0, 1 - ((z - z1) / r1) ** 2))
        b = r2 * math.sqrt(max(0.0, 1 - ((z - z2) / r2) ** 2))
        return a - b

    flo = f(lo)
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if (f(mid) > 0) == (flo > 0):
            lo, flo = mid, f(mid)
        else:
            hi = mid
    return 0.5 * (lo + hi)


def normals(pts):
    """Unit normals into the hole (toward the axis and up) for a profile."""
    out = []
    n = len(pts)
    for i in range(n):
        a = pts[max(0, i - 1)]
        b = pts[min(n - 1, i + 1)]
        dr, dz = b[0] - a[0], b[1] - a[1]
        nr, nz = dz, -dr
        ln = math.hypot(nr, nz) or 1.0
        out.append((nr / ln, nz / ln))
    return out


def offset(pts, nrm, amounts):
    """Each point moved along its normal by its amount, radius kept >= 0."""
    return [(max(0.0, r + nr * a), z + nz * a) for (r, z), (nr, nz), a in zip(pts, nrm, amounts)]


def clip_profile(pts, depth):
    """Keep the part of a profile above z = -depth, ending on that plane."""
    if depth is None or depth < 0 or not pts:
        return pts
    zc = -depth
    out = [pts[0]] if pts[0][1] >= zc else []
    for a, b in zip(pts, pts[1:]):
        if b[1] >= zc:
            if a[1] < zc:  # coming back up (does not happen for a hole)
                out.append(b)
            else:
                out.append(b)
        elif a[1] >= zc > b[1]:
            u = (a[1] - zc) / (a[1] - b[1])
            out.append((a[0] + (b[0] - a[0]) * u, zc))
            break
    return out


def clip_together(lists, depth):
    """Clip profiles that run point for point (a shell's faces) at
    z = -depth so they end together: the same number of points, each list's
    last point on the plane, so the cap between them is flat."""
    if depth is None or depth < 0 or not lists or not lists[0]:
        return lists
    zc = -depth
    n = min(len(q) for q in lists)
    k = next((i for i in range(n) if any(q[i][1] < zc for q in lists)), None)
    if k is None:
        return [q[:n] for q in lists]
    if k == 0:
        return [[] for _ in lists]
    out = []
    for q in lists:
        head = list(q[:k])
        j = k
        while j < len(q) and q[j][1] >= zc:  # this face crosses later: run on to its own crossing
            j += 1
        if j < len(q):
            a, b = q[j - 1], q[j]
            u = (a[1] - zc) / (a[1] - b[1]) if a[1] != b[1] else 1.0
            head.append((a[0] + (b[0] - a[0]) * u, zc))
        else:
            head.append((q[-1][0], zc))
        out.append(head)
    return out


class _Layer:
    """A shell lining the hole, rebuilt every frame: its inner surface (half
    a revolution, behind the cut face) and its cut edge on the face."""

    def __init__(self, sec, key, mat, hide_eps):
        self.key, self.eps = key, hide_eps
        self.mesh = bpy.data.meshes.new(f"{sec.name}_{key}")
        self.mesh.materials.append(mat)
        self.objs = []
        for hx in sec.holes:
            o = bpy.data.objects.new(f"{sec.name}_{key}{hx:g}", self.mesh)
            o.location = (hx, sec.face, 0.0)
            geo.link(o, sec.root)
            o.hide_render = True
            self.objs.append(o)

    def show(self, on):
        for o in self.objs:
            if o.hide_render == on:
                o.hide_render = not on

    def build(self, outer, inner, seg, surf=None, face_eps=0.0004, cap_top=True, cap_bottom=False):
        """outer/inner: profiles [(r, z)] of the same length (the layer's two
        faces); surf: where to draw the visible inner surface (default inner;
        a layer pulls its empty stretches behind the layer under it)."""
        n = len(inner)
        verts, faces, flat = [], [], []
        surf = surf or inner
        # inner surface, half a revolution behind the face (y >= 0 locally)
        for r, z in surf:
            for j in range(seg + 1):
                a = math.pi * j / seg
                verts.append((r * math.cos(a), r * math.sin(a), z))
        w = seg + 1
        for i in range(n - 1):
            for j in range(seg):
                p = i * w + j
                faces.append((p, p + 1, p + w + 1, p + w))
                flat.append(False)
        # the cut edge on the section face, both sides of the axis
        base = len(verts)
        for (ro, zo), (ri, zi) in zip(outer, inner):
            verts.extend([(ro, -face_eps, zo), (ri, -face_eps, zi), (-ro, -face_eps, zo), (-ri, -face_eps, zi)])
        for i in range(n - 1):
            p, q = base + 4 * i, base + 4 * (i + 1)
            faces.append((p, q, q + 1, p + 1))
            faces.append((p + 2, p + 3, q + 3, q + 2))
            flat.extend([True, True])

        def ring(io, ii):
            (ro, zo), (ri, zi) = outer[io], inner[ii]
            b = len(verts)
            for j in range(seg + 1):
                a = math.pi * j / seg
                c, s = math.cos(a), math.sin(a)
                verts.extend([(ro * c, ro * s, zo), (ri * c, ri * s, zi)])
            for j in range(seg):
                p = b + 2 * j
                faces.append((p, p + 2, p + 3, p + 1))
                flat.append(True)

        if cap_top:
            ring(0, 0)
        if cap_bottom:
            ring(n - 1, n - 1)
        me = self.mesh
        me.clear_geometry()
        me.from_pydata(verts, [], faces)
        me.polygons.foreach_set("use_smooth", [not f for f in flat])
        me.update()


class TSVSection(Part):
    """See the module docstring."""

    LAYERS = ("polymer", "liner", "barrier", "seed")

    def bottom_at(self, shot, t):
        """Depth (positive) of the hole's floor at time t."""
        sc = scoops(shot.value(self.s, "etch", t), self.core, self.rho, self.pitch)
        return -min((zd - r for zd, r in sc), default=0.0)

    # -- etching ---------------------------------------------------------
    def etch_to(self, shot, n, start, end, ease="out"):
        """Etch until `n` scoops (fractional allowed) exist."""
        shot.tween(self.s, "etch", float(n), start, end, ease)
        return self

    def coat(self, shot, start, end, amount=1.0, ease="inout"):
        """A polymer coat over every surface the hole has at `start`."""
        c = Coat(self.bottom_at(shot, start) + 0.02)
        self.coats.append(c)
        shot.tween(c, "amount", amount, start, end, ease, frm=0.0)
        return c

    def punch(self, shot, start, end, coat=None, ease="inout"):
        """Ions clear the floor: the newest coat (or `coat`) recedes from the
        bottom of the hole to where the floor turns into wall."""
        c = coat or self.coats[-1]
        b = self.bottom_at(shot, start)
        clear = b - self.rho * 0.55
        shot.tween(c, "lo", clear, start, end, ease, frm=b + 0.02)
        return c

    def strip_polymer(self, shot, start, end, ease="inout"):
        for c in self.coats:
            shot.tween(c, "amount", 0.0, start, end, ease)
        return self

    def bosch(self, shot, start, end, cycles, ease="linear"):
        """`cycles` more etch / coat / punch cycles from start to end."""
        n0 = shot.value(self.s, "etch", start)
        step = (end - start) / cycles
        for k in range(cycles):
            t = start + k * step
            self.etch_to(shot, math.floor(n0 + 1e-6) + k + 1, t, t + step * 0.55, "inout")
            self.coat(shot, t + step * 0.55, t + step * 0.8)
            self.punch(shot, t + step * 0.8, t + step)
        return self

    # -- resist ----------------------------------------------------------
    def open_resist(self, shot, start, end, ease="inout"):
        shot.tween(self.s, "open", 1.0, start, end, ease)
        return self

    def strip_resist(self, shot, start, end, ease="in"):
        if self.resist is None:
            return self
        shot.tween(self.resist, "location.z", self.resist.location.z + 0.6, start, end, ease)
        self.resist_gone = end
        return self

    def hide(self, shot, start, end=None):
        """Hide the whole section from start (to end, if given): a cut to
        another set in the same script."""
        shot.set(self.s, "visible", 0.0, start)
        if end is not None:
            shot.set(self.s, "visible", 1.0, end)
        return self

    # -- lining and filling ---------------------------------------------
    def deposit(self, shot, which, start, end, ease="inout"):
        """Grow "liner" (SiO2), "barrier" (Ta/TaN) or "seed" (Cu) to full."""
        if which not in ("liner", "barrier", "seed"):
            raise KeyError(f"deposit what? {which!r}: liner, barrier or seed")
        shot.tween(self.s, which, 1.0, start, end, ease)
        return self

    def plate(self, shot, start, end, ease="linear"):
        """Copper grows inward from walls and floor, the floor faster, until
        the hole is full."""
        shot.tween(self.s, "fill", 1.0, start, end, ease)
        return self

    # -- the back side ----------------------------------------------------
    def thin(self, shot, start, end, to, ease="inout"):
        """Grind the block from its bottom face until it is `to` deep."""
        shot.tween(self.block, "scale.z", to / self.size[2], start, end, ease)
        return self

    def reveal(self, shot, start, end, depth, ease="inout"):
        """Cut every layer off at `depth` (the polished copper ends)."""
        shot.tween(self.s, "clip", depth, start, end, ease, frm=self.max_depth + 0.5)
        return self

    # -- per frame ---------------------------------------------------------
    def _update(self, t):
        s = self.s
        shown = s.visible >= 0.5
        if self.block.hide_render == shown:
            self.block.hide_render = not shown
        if self.resist is not None:
            gone = not shown or (self.resist_gone is not None and t >= self.resist_gone)
            if self.resist.hide_render != gone:
                self.resist.hide_render = gone
        if not shown:
            for lay in list(self.layers.values()) + [self.copper]:
                lay.show(False)
            self._key = None
            return
        key = (round(s.etch, 6), round(s.open, 6), round(s.liner, 6), round(s.barrier, 6), round(s.seed, 6),
               round(s.fill, 6), round(s.clip, 6),
               tuple((round(c.amount, 6), round(c.lo, 6)) for c in self.coats))
        if key == self._key:
            return  # nothing moved: keep the meshes (and the boolean's cache)
        self._key = key
        p = profile(s.etch, self.core, self.rho, self.pitch)
        self._update_cutter(p)
        self._update_resist()
        clip = s.clip if s.clip >= 0 else None
        if not p:
            for lay in self.layers.values():
                lay.show(False)
            self.copper.show(False)
            self.floor_pt.location = (self.holes[0], self.face, 0.0)
            return
        nrm = normals(p)
        zs = [z for _, z in p]
        poly = []
        for z in zs:
            v = 0.0
            for c in self.coats:
                if c.amount > 0:
                    v = max(v, c.amount * _smooth((z + c.lo) / 0.012))
            poly.append(self.thick["polymer"] * v)
        depth = max(1e-6, -zs[-1])
        th = {
            "polymer": poly,
            "liner": [self.thick["liner"] * s.liner] * len(p),
            "barrier": [self.thick["barrier"] * s.barrier * (1.0 - 0.45 * (-z / depth)) for z in zs],
            "seed": [self.thick["seed"] * s.seed * (1.0 - 0.3 * (-z / depth)) for z in zs],
        }
        acc = [0.0] * len(p)
        for key in self.LAYERS:
            t_k = th[key]
            lay = self.layers[key]
            if max(t_k) < 1e-5:
                lay.show(False)
            else:
                outer = offset(p, nrm, acc)
                inner = offset(p, nrm, [a + x for a, x in zip(acc, t_k)])
                surf = offset(p, nrm, [a + x if x > lay.eps else a - lay.eps for a, x in zip(acc, t_k)])
                if clip is not None:
                    outer, inner, surf = clip_together([outer, inner, surf], clip)
                if len(outer) > 1:
                    lay.build(outer, inner, self.seg, surf=surf, cap_bottom=clip is not None)
                    lay.show(True)
                else:
                    lay.show(False)
            acc = [a + x for a, x in zip(acc, t_k)]
        self._update_copper(p, nrm, acc, clip)
        self.floor_pt.location = (self.holes[0], self.face, zs[-1] + acc[-1])

    def _update_cutter(self, p):
        me = self.cutter_mesh
        me.clear_geometry()
        if not p:
            self.mod.show_render = self.mod.show_viewport = False
            return
        self.mod.show_render = self.mod.show_viewport = True
        seg = self.seg * 2
        ring = [(p[0][0], 0.2)] + [q for q in p if q[0] > 1e-4]
        verts, faces = [], []
        for r, z in ring:
            for j in range(seg):
                a = 2 * math.pi * j / seg
                verts.append((r * math.cos(a), r * math.sin(a), z))
        for i in range(len(ring) - 1):  # outward normals: down the wall, round it the other way
            for j in range(seg):
                a, b = i * seg + j, i * seg + (j + 1) % seg
                faces.append((a, a + seg, b + seg, b))
        faces.append(tuple(range(seg)))  # the top, facing up
        apex = len(verts)
        verts.append((0.0, 0.0, p[-1][1]))
        last = (len(ring) - 1) * seg
        for j in range(seg):  # the floor's centre, facing down
            faces.append((last + j, apex, last + (j + 1) % seg))
        me.from_pydata(verts, [], faces)
        me.update()

    def _update_resist(self):
        if self.resist is None:
            return
        o = max(0.0, min(1.0, self.s.open))
        on = o > 0.01
        self.resist_mod.show_render = self.resist_mod.show_viewport = on
        for op in self.openers:
            op.scale = (o, o, 1.0) if on else (1.0, 1.0, 1.0)

    def _update_copper(self, p, nrm, acc, clip):
        f = max(0.0, min(1.0, self.s.fill))
        if f <= 1e-4:
            self.copper.show(False)
            return
        outer = offset(p, nrm, acc)
        bottom = outer[-1][1]
        span = -bottom
        m = self.core + self.rho  # the rising front is a round meniscus, about a radius tall
        zf = bottom - 0.02 + f * (span + m + 0.04)
        side = self.thick["side"] * min(1.0, f * 3.0)
        inner = []
        for (r, z) in outer:
            d = -z / span if span > 0 else 0.0
            ri = max(0.0, r - side * (0.55 + 0.45 * d))
            u = max(0.0, min(1.0, (z - zf) / m))
            ri *= math.sqrt(max(0.0, 1.0 - (1.0 - u) ** 2))
            inner.append((ri, z))
        # a finer row count up the wall so the front reads smoothly
        outer2, inner2 = _resample(outer, inner, 0.012)
        if clip is not None:
            outer2, inner2 = clip_together([outer2, inner2], clip)
        if len(outer2) < 2:
            self.copper.show(False)
            return
        self.copper.build(outer2, inner2, self.seg, cap_bottom=clip is not None)
        self.copper.show(True)


def _resample(outer, inner, step):
    """Insert points so no wall segment is longer than `step` (both lists)."""
    o2, i2 = [outer[0]], [inner[0]]
    for a, b, c, d in zip(outer, outer[1:], inner, inner[1:]):
        n = max(1, int(math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / step)))
        for k in range(1, n + 1):
            u = k / n
            o2.append((a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u))
            i2.append((c[0] + (d[0] - c[0]) * u, c[1] + (d[1] - c[1]) * u))
    return o2, i2


def materials():
    """The section's materials by layer name (cached like every kit material)."""
    return {
        "wall": M.principled("tsv_wall", (0.075, 0.078, 0.115), rough=0.45),
        "polymer": M.principled("tsv_polymer", POLYMER, rough=0.25, coat=0.6, coat_rough=0.05,
                                subsurface=0.2),
        "liner": M.principled("tsv_liner", (0.78, 0.8, 0.84), rough=0.22, coat=0.6, coat_rough=0.05,
                              transmission=0.25, ior=1.46),
        "barrier": M.tantalum("tsv_barrier"),
        "seed": M.principled("tsv_seed", (0.98, 0.58, 0.38), metallic=1.0, rough=0.16),
        "copper": M.principled("tsv_fill", (0.72, 0.36, 0.17), metallic=1.0, rough=0.34,
                               emission=(1.0, 0.45, 0.12), strength=0.0),
    }


def tsv_section(name="tsv", loc=(0, 0, 0), size=(3.0, 1.2, 1.6), holes=(0.0,), r_open=0.08, core=0.025,
                rho=0.08, pitch=0.07, max_scoops=28, resist=True, resist_t=0.06, opened=True, grid=None,
                thick=None, seg=24, block_mat=None, parent=None):
    """Build the section. size (x, y, z) of the silicon block (top at loc's z,
    cut face toward -y); holes: x positions of the vias on the cut face;
    r_open: the resist opening; core, rho, pitch: the scoop shape (wall radius
    core + rho, one scoop deeper every pitch); grid=(nx, ny, dx, dy): extra
    openings (and holes) behind the face, for top views; opened: start with
    the resist already patterned (False: closed, then open_resist()); thick:
    layer thicknesses {polymer, liner, barrier, seed, side}."""
    w, d, h = size
    p = TSVSection(name, loc, parent)
    p.size, p.face = size, -d / 2
    p.holes = tuple(holes)
    p.core, p.rho, p.pitch, p.seg = core, rho, pitch, seg
    p.r_open, p.resist_t = r_open, resist_t
    p.max_depth = (max_scoops - 1) * pitch + rho
    p.s = TSVState()
    p._key = None
    p.resist_gone = None
    p.s.open = 1.0 if opened else 0.0
    p.coats = []
    p.thick = {"polymer": 0.011, "liner": 0.013, "barrier": 0.007, "seed": 0.006, "side": 0.03}
    p.thick.update(thick or {})
    mats = materials()
    p.mats = mats

    # the block, with one revolved cutter per hole (all sharing one mesh)
    p.block = box(f"{name}_block", (w, d, h), (0, 0, -h / 2), block_mat or M.silicon_section(), 0, parent=p.root)
    geo.bake(p.block)
    p.add("block", p.block)
    coll = bpy.data.collections.new(f"{name}_cutters")
    bpy.context.scene.collection.children.link(coll)
    p.cutter_mesh = bpy.data.meshes.new(f"{name}_cutter")
    p.cutter_mesh.materials.append(mats["wall"])
    spots = [(hx, p.face) for hx in p.holes]
    if grid:
        nx, ny, dx, dy = grid
        for iy in range(1, ny + 1):
            for ix in range(nx):
                spots.append(((ix - (nx - 1) / 2) * dx, p.face + iy * dy))
    for k, (x, y) in enumerate(spots):
        c = bpy.data.objects.new(f"{name}_cut{k}", p.cutter_mesh)
        c.location = (x, y, 0.0)
        coll.objects.link(c)
        c.parent = p.root
        c.hide_render = True
        c.display_type = "WIRE"
    p.mod = p.block.modifiers.new("etch", "BOOLEAN")
    p.mod.operation = "DIFFERENCE"
    p.mod.operand_type = "COLLECTION"
    p.mod.collection = coll
    p.mod.solver = "EXACT"
    if hasattr(p.mod, "material_mode"):
        p.mod.material_mode = "TRANSFER"

    # the layers, each pulled a little further behind the one before when empty
    p.layers = {key: _Layer(p, key, mats[key], 0.0006 * (i + 1)) for i, key in enumerate(TSVSection.LAYERS)}
    p.copper = _Layer(p, "copper", mats["copper"], 0.0)

    # the photoresist, with an opening per hole
    p.resist, p.openers = None, []
    if resist:
        res = box(f"{name}_resist", (w, d, resist_t), (0, 0, resist_t / 2), M.photoresist(), 0, parent=p.root)
        m2 = res.modifiers.new("open", "BOOLEAN")
        m2.operation = "DIFFERENCE"
        m2.solver = "EXACT"
        openers = bpy.data.collections.new(f"{name}_openings")
        bpy.context.scene.collection.children.link(openers)
        for k, (x, y) in enumerate(spots):
            op = cylinder(f"{name}_opening{k}", r_open, resist_t * 4, (x, y, resist_t / 2), None, verts=32)
            for pc in op.users_collection:
                pc.objects.unlink(op)
            openers.objects.link(op)
            op.parent = p.root
            op.hide_render = True
            op.display_type = "WIRE"
            p.openers.append(op)
        m2.operand_type = "COLLECTION"
        m2.collection = openers
        p.resist, p.resist_mod = res, m2
        p.add("resist", res)
        p.mark("resist", (-w / 2 + 0.1, p.face, resist_t))

    hx = p.holes[0]
    p.floor_pt = geo.empty(f"{name}_floor_pt", (hx, p.face, 0.0), p.root, size=0.05)
    p.points["floor"] = (p.floor_pt, Vector((0, 0, 0)))
    p.mark("rim", (hx + core + rho, p.face, 0.0))
    p.mark("wall", (hx - core - rho, p.face, -rho * 0.5))
    p.mark("copper", (hx, p.face, -p.max_depth * 0.4))
    p.mark("surface", (-w / 2 + 0.1, p.face, 0.0))
    p.mark("section", (hx + 0.6, p.face, -min(h, 1.0) * 0.5))
    p.mark("bottom", (0, p.face, -h))
    p._update(0.0)
    return p


def attach(shot, sec):
    """Rebuild the section every frame (tsv_section's parts follow sec.s)."""
    shot.on_frame(sec._update)
    return sec


def section(shot, *args, **kwargs):
    """tsv_section(...) already attached to shot's clock: the usual call."""
    return attach(shot, tsv_section(*args, **kwargs))


# --- the things around it ---------------------------------------------------

def _fresh(base):
    """A number that makes base<n> a new object name in this scene."""
    k = 0
    while f"{base}{k}" in bpy.data.objects:
        k += 1
    return k


def cheap_volumes(step_rate=16.0, max_steps=48):
    """Coarser volume steps for a plasma seen close up through its thin edge
    (call after effects.plasma): measured on HB35, 31 s down to 6 s a still
    at 50%, with no visible change."""
    c = bpy.context.scene.cycles
    c.volume_step_rate = max(c.volume_step_rate, step_rate)
    c.volume_max_steps = min(c.volume_max_steps, max_steps)


def ion_rain(shot, sec, start, end, count=40, spread=0.5, height=1.4, speed=2.4, length=0.04, r=0.0016,
             color=(0.55, 0.8, 1.0), strength=14.0, seed=7):
    """Ions as short bright streaks falling straight down onto the section,
    over an x band `spread` wide around the first hole. Those over the
    opening fall to the hole's floor (as it is at each frame), the rest stop
    on the resist (or the surface)."""
    import random

    rnd = random.Random(seed)
    k = _fresh(f"{sec.name}_ions")
    mat = M.emission(f"{sec.name}_ion{k}", color, strength)  # each call keeps its own look
    src = geo.source(f"_ionstreak_{sec.name}{k}", lambda n: geo.bake(
        geo.cylinder(n, r, length, (0, 0, length / 2), mat, verts=6)))
    hx = sec.holes[0]
    face = sec.face
    seeds = [(rnd.uniform(-spread / 2, spread / 2), rnd.uniform(0.0, 0.25), rnd.random()) for _ in range(count)]
    o = geo.instance_points(f"{sec.name}_ions{k}", src, [(0, 0, 0)] * count, parent=sec.root)
    o.visible_shadow = False
    me = o.data

    def update(t):
        co = []
        floor = sec.floor_pt.location.z
        res = sec.resist
        resist_on = res is not None and not res.hide_render
        top = res.location.z + sec.resist_t / 2 if resist_on else 0.0
        # through the resist only where it is open, and into the hole only if there is one
        gap = sec.r_open * max(0.0, min(1.0, sec.s.open)) if resist_on else sec.core + sec.rho
        gap = min(gap, sec.core + sec.rho * 0.6) if floor < -1e-4 else 0.0
        for x, dy, ph in seeds:
            if not (start <= t < end) or sec.s.visible < 0.5:
                co.extend((0.0, 0.0, 1000.0))
                continue
            inside = abs(x) < gap * 0.9
            stop = floor if inside else top
            fall = max(1e-3, height - stop)
            z = height - (((t - start) * speed / fall + ph) % 1.0) * fall
            co.extend((hx + x, face + 0.01 + (dy * 0.25 if inside else dy), z))
        me.vertices.foreach_set("co", co)
        me.update()

    shot.on_frame(update)
    return o


def transistors(sec, xs=(0.3, 0.45, 0.6, 0.75), name="fet"):
    """The device layer: a gate on the top surface and a well in the cut face
    at +-x for each x in xs, beside the first hole (parented to the section).
    Returns (gate material, well material); tween their "Emission Strength"
    (materials.socket) to flash them red."""
    gate = M.principled(f"{name}_gate", (0.55, 0.5, 0.42), metallic=0.6, rough=0.35, emission=(1.0, 0.08, 0.04),
                        strength=0.0)
    well = M.principled(f"{name}_well", (0.2, 0.21, 0.3), rough=0.45, emission=(1.0, 0.08, 0.04), strength=0.0)
    hx, face = sec.holes[0], sec.face
    for sx in (-1, 1):
        for k, x in enumerate(xs):
            box(f"{name}_gate{sx}{k}", (0.05, 0.25, 0.025), (hx + sx * x, face + 0.125, 0.0125), gate, 0.003,
                parent=sec.root)
            box(f"{name}_well{sx}{k}", (0.11, 0.02, 0.022), (hx + sx * x, face - 0.004, -0.011), well, 0.0,
                parent=sec.root)
    return gate, well


def plating_bath(name="bath", loc=(0, 0, 0), size=(4.0, 2.0, 2.6), liquid=(0.08, 0.42, 0.95), fill=0.85,
                 wafer_r=1.0, parent=None):
    """An electroplating tank: glass walls, blue copper sulfate solution
    (`fill` of the height), copper anode plates at both ends, and a wafer
    held upright on a clamp arm above it. loc is the tank's bottom centre.
    bath["holder"] is the wafer's root: tween its location.z down by
    bath.dip (to submerge the wafer). Points: "liquid", "wafer", "anode"."""
    w, d, h = size
    p = Part(name, loc, parent)
    glass = M.principled("bath_glass", (0.95, 0.97, 1.0), rough=0.03, transmission=1.0, ior=1.45)
    t = 0.04
    for k, (sz, at) in enumerate((((w, t, h), (0, -d / 2, h / 2)), ((w, t, h), (0, d / 2, h / 2)),
                                  ((t, d, h), (-w / 2, 0, h / 2)), ((t, d, h), (w / 2, 0, h / 2)),
                                  ((w, d, t), (0, 0, t / 2)))):
        p.add(f"glass{k}", box(f"{name}_glass{k}", sz, at, glass, 0.01, parent=p.root))
    sol = M.principled("cuso4", liquid, rough=0.04, transmission=1.0, ior=1.33)
    nt = sol.node_tree
    vol = nt.nodes.new("ShaderNodeVolumeAbsorption")
    vol.inputs["Color"].default_value = (*liquid, 1)
    vol.inputs["Density"].default_value = 0.9
    nt.links.new(vol.outputs[0], nt.nodes["Material Output"].inputs["Volume"])
    lh = h * fill
    p.add("liquid", box(f"{name}_liquid", (w - 2.2 * t, d - 2.2 * t, lh - t), (0, 0, t + (lh - t) / 2), sol, 0.0,
                        parent=p.root))
    for sx in (-1, 1):
        p.add(f"anode{sx}", box(f"{name}_anode{sx}", (0.06, d * 0.7, h * 0.75), (sx * (w / 2 - 0.25), 0, h * 0.45),
                                M.brushed_copper(), 0.01, parent=p.root))
    holder = geo.empty(f"{name}_holder", (0, 0, h + wafer_r + 0.4), p.root, 0.3)
    p.add("holder", holder)
    disc = cylinder(f"{name}_wafer", wafer_r, 0.03, (0, 0, 0), M.wafer(), verts=96, axis="Y", parent=holder)
    p.add("wafer", disc)
    clamp = M.steel()
    p.add("arm", box(f"{name}_arm", (0.08, 0.08, 2.4), (0, 0, wafer_r + 1.2), clamp, 0.01, parent=holder))
    p.add("jaw", box(f"{name}_jaw", (0.5, 0.12, 0.18), (0, 0, wafer_r - 0.02), clamp, 0.01, parent=holder))
    p.dip = h + wafer_r + 0.4 - (lh * 0.5)  # how far down the holder goes to submerge the wafer
    p.mark("liquid", (w * 0.3, -d / 2, lh * 0.6))
    p.mark("wafer", (0, 0, 0), obj=holder)
    p.mark("anode", (w / 2 - 0.25, -d * 0.35, h * 0.6))
    return p


def grinding_wheel(name="wheel", loc=(0, 0, 0), r=2.2, h=0.35, parent=None):
    """A grinding wheel: a steel hub and a grey abrasive rim, axis vertical.
    Spin it with shot.tween(wheel.root, "rotation_euler.z", ...)."""
    p = Part(name, loc, parent)
    grit = M.principled("abrasive", (0.32, 0.32, 0.31), rough=0.85)
    p.add("rim", cylinder(f"{name}_rim", r, h * 0.45, (0, 0, h * 0.225), grit, verts=96, parent=p.root))
    p.add("hub", cylinder(f"{name}_hub", r * 0.97, h * 0.55, (0, 0, h * 0.45 + h * 0.275), M.steel(), verts=96,
                          parent=p.root))
    p.add("spindle", cylinder(f"{name}_spindle", r * 0.18, 3.0, (0, 0, h + 1.5), M.steel(), verts=48,
                              parent=p.root))
    # radial marks so the spin can be seen
    for k in range(12):
        a = 2 * math.pi * k / 12
        b = box(f"{name}_mark{k}", (r * 0.6, 0.03, 0.01), (math.cos(a) * r * 0.55, math.sin(a) * r * 0.55,
                h + 0.004), M.principled("wheel_mark", (0.2, 0.2, 0.22), metallic=1.0, rough=0.4), 0,
                parent=p.root)
        b.rotation_euler.z = a
    p.mark("face", (0, 0, 0))
    p.mark("edge", (r, 0, h * 0.2))
    return p


def debris(shot, loc, area, start, end, count=60, r=0.006, color=(1.0, 0.85, 0.6), strength=10.0, seed=3,
           speed=1.6, parent=None):
    """Fine grinding debris flicking sideways out of a contact line at loc,
    over an x span area[0], from start to end."""
    import random

    rnd = random.Random(seed)
    k = _fresh("debris")
    mat = M.emission(f"debris_glow{k}", color, strength)  # each call keeps its own look
    src = geo.source(f"_debris{k}", lambda n: geo.sphere(n, r, mat=mat, segments=6, rings=3))
    seeds = [(rnd.uniform(-area[0] / 2, area[0] / 2), rnd.uniform(-1, 1), rnd.uniform(0.4, 1.0), rnd.random())
             for _ in range(count)]
    o = geo.instance_points(f"debris{k}", src, [(0, 0, 0)] * count, loc, parent)
    o.visible_shadow = False
    me = o.data

    def update(t):
        co = []
        for x, dirn, v, ph in seeds:
            if not (start <= t < end):
                co.extend((0.0, 0.0, 1000.0))
                continue
            u = ((t - start) * speed + ph) % 1.0
            co.extend((x + dirn * u * v * 0.5, -u * v * 0.25, 0.04 + u * 0.25 - u * u * 0.4))
        me.vertices.foreach_set("co", co)
        me.update()

    shot.on_frame(update)
    return o
