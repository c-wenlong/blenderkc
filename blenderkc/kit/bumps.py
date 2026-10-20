"""Thinned dies with TSVs and microbumps, cut through their front row (SET STACK).

    die = bumps.tsv_die("d1", (0, 0, 1.0))                 # a die with a grid of copper TSVs
    die.grow_pillars(shot, a, b); die.grow_caps(shot, a, b) # microbumps on its top
    die.melt(shot, a, b)                                     # the caps flatten and join
    shot.tween(die.root, "location.z", ..., a, b)            # drop it onto the one below

Every die is the same box (size x, y, t) with its cut face toward -y; TSVs
stand on a grid of `nx` x `ny` at `pitch`, and the front row sits on the
cut face, halved, so the section shows copper running through the silicon
like SET TSV. Pillars and caps are instanced (one object per row kind), so a
stack of dies costs little; they grow by scaling their instancer in z.
Materials: die.mats["tsv"] (copper that can glow), ["bump"], ["cap"].
"""
import bpy

from . import geo
from . import materials as M
from .geo import Part, box, cylinder


def _flat_ends(obj):
    """Shade a cylinder's end caps flat: smooth shading bends them into
    dimples, worse still when the instancer squashes them in z."""
    for poly in obj.data.polygons:
        if abs(poly.normal.z) > 0.99:
            poly.use_smooth = False
    return obj


def _halved(name, maker):
    """A source mesh cut to its +y half (for the front row on the cut face)."""
    return geo.source(name, lambda n: geo.halve_object(geo.bake(maker(n)), "+y"))


class TSVDie(Part):
    def grow_pillars(self, shot, start, end, ease="out"):
        for o in self.pillars:
            shot.show(o, start)
            shot.tween(o, "scale.z", 1.0, start, end, ease, frm=0.001)
        return self

    def grow_caps(self, shot, start, end, ease="out"):
        for o in self.caps:
            shot.show(o, start)
            shot.tween(o, "scale.z", 1.0, start, end, ease, frm=0.001)
        return self

    def melt(self, shot, start, end, squash=0.55, ease="inout"):
        """The solder caps slump: their height drops to `squash` of a cap,
        and the die above (if it rests on them) can come down by die.slump
        (set here from `squash`)."""
        self.slump = self.cap_h * (1.0 - squash)
        for o in self.caps:
            shot.tween(o, "scale.z", squash, start, end, ease)
        return self


def bump_gap(pillar_h=0.1, cap_r=0.056):
    """The height a row of microbumps adds between two dies (tsv_die's defaults)."""
    return pillar_h + cap_r * 0.75


def tsv_die(name="die", loc=(0, 0, 0), size=(4.0, 2.4, 0.3), nx=9, ny=6, pitch=0.4, tsv_r=0.06, pillar_r=0.05,
            pillar_h=0.1, cap_r=0.056, mat=None, tsv_mat=None, bumps=True, shown=True, body=True, parent=None):
    """A die, bottom face at loc's z, centred in x and y. bumps=False leaves
    the top bare (the top die of a stack). shown=False hides the pillars and
    caps until grow_pillars()/grow_caps(); True has them grown already.
    body=False builds the bumps alone, standing on loc (a die's bumps onto
    an interposer, say)."""
    w, d, t = size
    p = TSVDie(name, loc, parent)
    face = -d / 2
    mat = mat or M.silicon_section()
    tsv_mat = tsv_mat or M.tsv_copper("stack_tsv")
    p.mats = {"tsv": tsv_mat, "bump": M.copper("bump_copper", rough=0.3), "cap": M.solder()}
    p.size, p.face, p.pitch = size, face, pitch
    if not body:
        t = 0.0
    else:
        p.add("die", box(f"{name}_die", (w, d, t), (0, 0, t / 2), mat, 0.0, parent=p.root))
    xs = [(i - (nx - 1) / 2) * pitch for i in range(nx)]
    ys = [face + j * pitch for j in range(1, ny)]
    front = [(x, face, 0.0) for x in xs]
    back = [(x, y, 0.0) for x in xs for y in ys]
    h = t + 0.006  # the ends show a hair proud of both faces

    def tsv_full(n):
        return _flat_ends(geo.bake(cylinder(n, tsv_r, h, (0, 0, t / 2), tsv_mat, verts=20)))

    def tsv_half(n):
        return _flat_ends(cylinder(n, tsv_r, h, (0, 0, t / 2), tsv_mat, verts=24))

    if body:
        src_full = geo.source(f"_tsv_full_{name}", tsv_full)
        src_half = _halved(f"_tsv_half_{name}", tsv_half)
        p.add("tsv_back", geo.instance_points(f"{name}_tsv_back", src_full, back, parent=p.root))
        p.add("tsv_front", geo.instance_points(f"{name}_tsv_front", src_half,
                                               [(x, y - 0.002, z) for x, y, z in front], parent=p.root))
    p.pillars, p.caps = [], []
    if bumps:
        pm, cm = p.mats["bump"], p.mats["cap"]
        srcs = {
            "pillar_full": geo.source(f"_pillar_full_{name}", lambda n: _flat_ends(geo.bake(
                cylinder(n, pillar_r, pillar_h, (0, 0, pillar_h / 2), pm, verts=20)))),
            "pillar_half": _halved(f"_pillar_half_{name}", lambda n: _flat_ends(cylinder(
                n, pillar_r, pillar_h, (0, 0, pillar_h / 2), pm, verts=24))),
            "cap_full": geo.source(f"_cap_full_{name}", lambda n: geo.bake(
                geo.sphere(n, cap_r, (0, 0, 0), cm, segments=20, rings=10, half="+z"), scale=(1, 1, 0.75))),
            "cap_half": _halved(f"_cap_half_{name}", lambda n: geo.bake(
                geo.sphere(n, cap_r, (0, 0, 0), cm, segments=24, rings=10, half="+z"), scale=(1, 1, 0.75))),
        }
        for kind, pts, yoff in (("full", back, 0.0), ("half", front, -0.002)):
            pil = geo.instance_points(f"{name}_pillars_{kind}", srcs[f"pillar_{kind}"],
                                      [(x, y + yoff, 0.0) for x, y, _ in pts], (0, 0, t), p.root)
            cap = geo.instance_points(f"{name}_caps_{kind}", srcs[f"cap_{kind}"],
                                      [(x, y + yoff, 0.0) for x, y, _ in pts], (0, 0, t + pillar_h), p.root)
            p.pillars.append(pil)
            p.caps.append(cap)
            if not shown:
                for o in (pil, cap):
                    o.scale.z = 0.001
                    o.hide_render = True
    p.height = t + (pillar_h + cap_r * 0.75 if bumps else 0.0)
    p.gap = pillar_h + cap_r * 0.75
    p.cap_h = cap_r * 0.75
    p.slump = p.cap_h * 0.45  # melt() resets it from its squash
    p.mark("top", (0, 0, t))
    p.mark("front", (0, face, t / 2))
    p.mark("tsv", (xs[nx // 2], face, t / 2))
    p.mark("bump", (xs[nx // 2], face, t + pillar_h * 0.5))
    p.mark("cap", (xs[nx // 2], face, t + pillar_h + cap_r * 0.4))
    p.xs = xs
    return p
