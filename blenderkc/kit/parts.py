"""Parts: parametric, cheap to render, each a geo.Part with named points.

    pkg = parts.gpu_package(stacks_per_side=2, hero=2)    # the reference package
    pkg.hero.explode(shot, 1.8, 3.8)                       # lift the dies apart
    pkg.hero.glow(shot, 3.5, 4.5)                          # light the TSVs
    shot.label("HBM stack", anchor=pkg.point("hero"), start=4)

Sizes are in scene units (a GPU package is 7 x 5). Every part takes `loc`
and `parent`; move a whole part through part.root.
"""
import math

import bpy
from mathutils import Vector

from . import geo
from . import materials as M
from .geo import Part, box, cylinder, grid_array, grid_points


# --- dies and stacks ------------------------------------------------------

def dram_die(name="dram", loc=(0, 0, 0), size=(0.95, 1.25, 0.045), mat=None, parent=None):
    """One bare DRAM die with its cell grid."""
    p = Part(name, loc, parent)
    p.add("die", box(f"{name}_die", size, (0, 0, size[2] / 2), mat or M.dram_die(), 0.003, parent=p.root))
    p.mark("top", (0, 0, size[2]))
    p.mark("edge", (size[0] / 2, -size[1] / 2, size[2] / 2))
    return p


class DieStack(Part):
    """A stack of dies on microbumps: a base (logic) die and n DRAM dies.

    stack.layers      the die objects, bottom first
    stack.explode(shot, start, end, spread=0.16, lift=0.12, stagger=1/15)
    stack.collapse(shot, start, end)
    stack.glow(shot, start, end, strength=6)   TSVs light up (detail=True)
    """

    def explode(self, shot, start, end, spread=0.16, lift=0.12, stagger=1 / 15, ease="inout"):
        for i, (obj, z0) in enumerate(zip(self.layers, self.rest_z)):
            to = z0 + i * spread + (lift if i > 0 else 0.0)
            shot.tween(obj, "location.z", to, start, end + i * stagger, ease)
        return self

    def collapse(self, shot, start, end, ease="inout"):
        for obj, z0 in zip(self.layers, self.rest_z):
            shot.tween(obj, "location.z", z0, start, end, ease)
        return self

    def glow(self, shot, start, end, strength=6.0, ease="inout"):
        if self.tsv_mat is None:
            raise ValueError(f"{self.name} has no TSVs; build it with detail=True")
        shot.tween(M.emission_socket(self.tsv_mat), "default_value", strength, start, end, ease)
        return self


def die_stack(name="hbm", loc=(0, 0, 0), n=8, base=True, w=0.95, d=1.25, layer_t=0.045, gap=0.018,
              die_mat=None, base_mat=None, detail=False, tsv=(2, 14), tsv_pitch=(0.08, 0.0769), tsv_r=0.011,
              bumps=(10, 14), bump_pitch=(0.08, 0.0769), bump_r=0.012, parent=None):
    """An HBM-style stack. loc is the bottom centre. detail=True adds
    microbumps under each die and copper TSVs through it (the hero stack)."""
    p = DieStack(name, loc, parent)
    die_mat = die_mat or M.dram_die()
    base_mat = base_mat or M.logic_die()
    p.tsv_mat = M.tsv_copper(f"tsv_{name}") if detail else None
    bump_mat = M.solder()
    p.layers, p.rest_z, p.tsvs, p.bumps = [], [], [], []
    z = 0.0
    count = n + 1 if base else n
    for i in range(count):
        mat = base_mat if (base and i == 0) else die_mat
        zc = z + gap + layer_t / 2
        lay = box(f"{name}_d{i}", (w, d, layer_t), (0, 0, zc), mat, 0.003, parent=p.root)
        p.layers.append(lay)
        p.rest_z.append(zc)
        if detail:
            bx, by = bumps
            b = cylinder(f"{name}_bump{i}", bump_r, gap, (-(bx - 1) * bump_pitch[0] / 2, -(by - 1) * bump_pitch[1] / 2,
                         -(layer_t / 2 + gap / 2)), bump_mat, verts=8, parent=lay)
            grid_array(b, bx, bump_pitch[0], by, bump_pitch[1])
            p.bumps.append(b)
            tx, ty = tsv
            t = cylinder(f"{name}_tsv{i}", tsv_r, layer_t + 0.004, (-(tx - 1) * tsv_pitch[0] / 2,
                         -(ty - 1) * tsv_pitch[1] / 2, 0), p.tsv_mat, verts=10, parent=lay)
            grid_array(t, tx, tsv_pitch[0], ty, tsv_pitch[1])
            p.tsvs.append(t)
        z = zc + layer_t / 2
    p.height = z
    p.mark("top", (0, 0, z))
    p.mark("base", (w / 2, -d / 2, gap + layer_t / 2))
    p.mark("side", (w / 2, -d / 2, z / 2))
    p.mark("center", (0, 0, z / 2))
    return p


# --- packages -------------------------------------------------------------

class Package(Part):
    @property
    def hero(self):
        return self.stacks[self.hero_index] if self.hero_index is not None else None


def gpu_package(name="gpu_pkg", loc=(0, 0, 0), stacks_per_side=2, hero=None, dies=1, n_dies=8,
                stack_size=(0.95, 1.25), stack_x=1.85, stack_pitch=1.36, die_size=(2.3, 2.9), sub_t=0.12,
                int_t=0.05, substrate_mat=None, mould=False, parent=None):
    """A GPU package with HBM: organic substrate, silicon interposer, the GPU
    die(s) and 2 x stacks_per_side HBM stacks. hero: the index of the stack
    built in detail (microbumps, TSVs), counting the left column bottom to
    top, then the right. mould=True caps the stacks in black mould compound,
    as shipped. The defaults rebuild the reference shot's package.

    Raises ValueError when a stack would reach into the GPU dies: a wider die
    needs a larger stack_x (at least die width / 2 + stack width / 2 + 0.1)."""
    p = Package(name, loc, parent)
    sw, sd = stack_size
    need = die_size[0] / 2 + sw / 2 + 0.1
    if stack_x < need - 1e-9:
        raise ValueError(f"gpu_package {name}: stacks at stack_x={stack_x} overlap the {die_size[0]}-wide GPU "
                         f"die(s); use stack_x >= {need:.3f}")
    ys = [(j - (stacks_per_side - 1) / 2) * stack_pitch for j in range(stacks_per_side)]
    span_y = (max(ys) - min(ys)) + sd
    ix, iy = 2 * (stack_x + sw / 2 + 0.275), max(span_y + 0.99, die_size[1] + 0.7)
    sx_, sy_ = ix + 1.8, iy + 1.4
    p.size = (sx_, sy_)
    p.add("substrate", box(f"{name}_substrate", (sx_, sy_, sub_t), (0, 0, sub_t / 2),
                           substrate_mat or M.solder_mask("substrate", traces=False), 0.02, parent=p.root))
    pad = box(f"{name}_pads", (0.08, 0.08, 0.01), (-(sx_ / 2 - 0.3), -(sy_ / 2 - 0.25), sub_t + 0.005), M.gold(),
              0, parent=p.root)
    grid_array(pad, int((sx_ - 0.6) / 0.2) + 1, 0.2, 2, sy_ - 0.5)
    cap = box(f"{name}_caps", (0.16, 0.08, 0.08), (-(sx_ / 2 - 0.5), -(sy_ / 2 - 0.6), sub_t + 0.04),
              M.plastic("cap", (0.08, 0.08, 0.08), 0.5), 0.01, parent=p.root)
    grid_array(cap, 4, 0.3, 2, sy_ - 1.2)
    p.add("pads", pad)
    p.add("caps", cap)
    p.add("interposer", box(f"{name}_interposer", (ix, iy, int_t), (0, 0, sub_t + int_t / 2), M.interposer(), 0.005,
                            parent=p.root))
    top = sub_t + int_t
    p.top = top
    if dies == 1:
        p.add("gpu", box(f"{name}_gpu", (die_size[0], die_size[1], 0.16), (0, 0, top + 0.08), M.gpu_lid(), 0.01,
                         parent=p.root))
    else:
        h = (die_size[1] - 0.06 * (dies - 1)) / dies
        for k in range(dies):
            y = -die_size[1] / 2 + h / 2 + k * (h + 0.06)
            p.add(f"gpu{k}", box(f"{name}_gpu{k}", (die_size[0], h, 0.16), (0, y, top + 0.08), M.gpu_lid(), 0.01,
                                 parent=p.root))
    p.stacks = []
    p.hero_index = hero
    for side in (-1, 1):
        for y in ys:
            i = len(p.stacks)
            s = die_stack(f"{name}_hbm{i}", (side * stack_x, y, top), n=n_dies, w=sw, d=sd, detail=(i == hero),
                          parent=p.root)
            p.stacks.append(s)
            p.mark(f"stack{i}", (side * stack_x, y, top + s.height))
            if mould:
                p.add(f"mould{i}", box(f"{name}_mould{i}", (sw + 0.02, sd + 0.02, s.height + 0.01),
                                       (side * stack_x, y, top + (s.height + 0.01) / 2), M.mould(), 0.006,
                                       parent=p.root))
    p.mark("die", (0, 0, top + 0.16))
    p.mark("interposer", (-ix / 2 + 0.15, -iy / 2 + 0.1, top))
    p.mark("substrate", (-sx_ / 2 + 0.4, -sy_ / 2 + 0.3, sub_t))
    if hero is not None:
        hs = p.stacks[hero]
        x, y, z = hs.root.location
        p.mark("hero", (x, y, z + hs.height))
    return p


def bga_chip(name="gddr", loc=(0, 0, 0), size=(1.2, 1.4, 0.12), balls=(10, 12), pitch=0.1, ball_r=0.035,
             body_mat=None, parent=None, substrate_t=0.03):
    """A BGA memory chip (GDDR): mould body on a thin substrate, solder balls
    underneath. loc is the bottom centre of the balls."""
    p = Part(name, loc, parent)
    z = ball_r * 1.6
    p.add("substrate", box(f"{name}_sub", (size[0], size[1], substrate_t), (0, 0, z + substrate_t / 2),
                           M.solder_mask("chip_substrate", (0.03, 0.06, 0.035), traces=False), 0.004, parent=p.root))
    p.add("body", box(f"{name}_body", (size[0] - 0.04, size[1] - 0.04, size[2]),
                      (0, 0, z + substrate_t + size[2] / 2), body_mat or M.mould(), 0.012, parent=p.root))
    src = geo.source(f"_ball_{ball_r:.3f}", lambda n: geo.sphere(n, ball_r, mat=M.solder(), segments=10, rings=6))
    nx, ny = balls
    nx = min(nx, int((size[0] - 0.1) / pitch) + 1)
    ny = min(ny, int((size[1] - 0.1) / pitch) + 1)
    p.add("balls", geo.instance_points(f"{name}_balls", src, grid_points(nx, ny, pitch, pitch, (0, 0, ball_r)),
                                       parent=p.root))
    h = z + substrate_t + size[2]
    p.height = h
    p.mark("top", (0, 0, h))
    p.mark("edge", (size[0] / 2, 0, h / 2))
    p.mark("balls", (size[0] / 2 - 0.05, -size[1] / 2 + 0.05, ball_r))
    return p


def solder_balls(name, nx, ny, pitch, r, loc=(0, 0, 0), mat=None, parent=None):
    """A grid of solder balls (instanced: any count is cheap)."""
    src = geo.source(f"_ball_{name}", lambda n: geo.sphere(n, r, mat=mat or M.solder(), segments=12, rings=8))
    return geo.instance_points(name, src, grid_points(nx, ny, pitch, pitch, (0, 0, r)), loc, parent)


def microbumps(name, nx, ny, pitch, r=0.012, h=0.02, loc=(0, 0, 0), parent=None, cap=True):
    """Copper pillars with solder caps, instanced. loc is the grid's centre at
    the pillars' feet."""
    def make(n):
        pillar = geo.bake(cylinder(n, r, h * 0.6, (0, 0, h * 0.3), M.copper(), verts=12))
        if cap:
            tip = geo.bake(geo.sphere(n + "_cap", r * 1.05, (0, 0, 0), M.solder(), segments=12, rings=6),
                           scale=(1, 1, 0.75))
            tip.parent = pillar
            tip.hide_render = True
        return pillar

    src = geo.source(f"_mb_{name}", make)
    obj = geo.instance_points(name, src, grid_points(nx, ny, pitch, pitch), loc, parent)
    if cap:  # the caps ride on the returned object
        tip = src.children[0]
        geo.instance_points(name + "_caps", tip, grid_points(nx, ny, pitch, pitch, (0, 0, h * 0.6)), parent=obj)
    return obj



def _scatter(rnd, n, lo, hi, avoid=()):
    pts = []
    tries = 0
    while len(pts) < n and tries < n * 30:
        tries += 1
        x, y = rnd.uniform(lo[0], hi[0]), rnd.uniform(lo[1], hi[1])
        if any(a[0] <= x <= a[2] and a[1] <= y <= a[3] for a in avoid):
            continue
        pts.append((x, y))
    return pts


def _clusters(rnd, n, lo, hi, avoid=(), pitch=(0.2, 0.14)):
    """Passives in tidy little rows, the way boards place them: n clusters of
    2 to 8 by 1 to 3 parts, kept out of the `avoid` rectangles."""
    pts = []
    for cx, cy in _scatter(rnd, n, lo, hi, avoid):
        nx, ny = rnd.randint(2, 8), rnd.randint(1, 3)
        if rnd.random() < 0.5:
            pts.extend((cx + i * pitch[0], cy + j * pitch[1] * 2.2) for i in range(nx) for j in range(ny))
        else:
            pts.extend((cx + j * pitch[1] * 2.2, cy + i * pitch[0]) for i in range(nx) for j in range(ny))
    return [q for q in pts if not any(a[0] <= q[0] <= a[2] and a[1] <= q[1] <= a[3] for a in avoid)]


def _passives(name, pts, z, parent, size=(0.09, 0.045, 0.04)):
    """Tiny capacitors (tan) and resistors (black) at pts, instanced."""
    cap = geo.source(f"_mlcc_{size[0]:.2f}", lambda n: geo.bake(box(n, size, (0, 0, size[2] / 2),
                                                                   M.ceramic("mlcc", (0.2, 0.14, 0.085)), 0.008)))
    res = geo.source(f"_res_{size[0]:.2f}", lambda n: geo.bake(box(n, size, (0, 0, size[2] / 2),
                                                                  M.plastic("resistor", (0.015, 0.015, 0.015), 0.4),
                                                                  0.008)))
    a = [(q[0], q[1], z) for k, q in enumerate(pts) if (k // 3) % 3]
    b = [(q[0], q[1], z) for k, q in enumerate(pts) if not (k // 3) % 3]
    geo.instance_points(name + "_r", res, b, parent=parent)
    return geo.instance_points(name, cap, a, parent=parent)


# --- boards ---------------------------------------------------------------

def graphics_card(name="card", loc=(0, 0, 0), size=(26.0, 11.0), gddr=12, seed=3, parent=None, *,
                  layout="columns"):
    """A gaming graphics card's board, cooler off: the GPU in the middle with
    GDDR chips around it, power stages along one end, PCIe fingers on the
    bottom edge and an I/O bracket. Points: gpu, gddr0..n, vrm, fingers, edge.

    layout="columns" (the default): left and right columns, the rest along
    the top. layout="ring": sixteen chips, four a side at a 1.45 pitch,
    card.chips ordered clockwise from the top left (top row left to right,
    right column top to bottom, bottom row right to left, left column bottom
    to top); gddr is ignored."""
    import random

    rnd = random.Random(seed)
    W, D = size
    t = 0.16
    p = Part(name, loc, parent)
    p.add("board", box(f"{name}_board", (W, D, t), (0, 0, t / 2), M.board_black(), 0.03, parent=p.root))
    top = t
    gx, gy = -2.0, 0.6
    sub = 4.4
    p.add("gpu_sub", box(f"{name}_gpu_sub", (sub, sub, 0.1), (gx, gy, top + 0.05),
                         M.solder_mask("gpu_substrate", (0.02, 0.05, 0.04), traces=False), 0.01, parent=p.root))
    for k, (dx, dy, sx, sy) in enumerate([(0, sub / 2 - 0.2, sub, 0.4), (0, -sub / 2 + 0.2, sub, 0.4),
                                          (sub / 2 - 0.2, 0, 0.4, sub - 0.8), (-sub / 2 + 0.2, 0, 0.4, sub - 0.8)]):
        p.add(f"stiffener{k}", box(f"{name}_stiff{k}", (sx, sy, 0.14), (gx + dx, gy + dy, top + 0.17),
                                   M.aluminium("stiffener", 0.38), 0.02, parent=p.root))
    p.add("gpu", box(f"{name}_gpu", (2.3, 2.3, 0.12), (gx, gy, top + 0.16), M.gpu_lid(), 0.015, parent=p.root))
    p.mark("gpu", (gx, gy, top + 0.22))
    # memory chips: left and right columns, then along the top
    chips = []
    if layout == "ring":
        off = [(j - 1.5) * 1.45 for j in range(4)]
        chips += [(gx + o, gy + 3.6, 90) for o in off]
        chips += [(gx + 3.55, gy + o, 0) for o in reversed(off)]
        chips += [(gx + o, gy - 3.6, 90) for o in reversed(off)]
        chips += [(gx - 3.55, gy + o, 0) for o in off]
    elif layout == "columns":
        per_side = max(1, min(4, gddr // 3))
        rest = gddr - 2 * per_side
        for side in (-1, 1):
            for j in range(per_side):
                y = gy + (j - (per_side - 1) / 2) * 1.75
                chips.append((gx + side * 3.55, y, 0))
        for j in range(rest):
            x = gx + (j - (rest - 1) / 2) * 1.6
            chips.append((x, gy + 3.6, 90))
    else:
        raise ValueError(f"graphics_card layout must be 'columns' or 'ring', not {layout!r}")
    p.chips = []
    for i, (x, y, rot) in enumerate(chips):
        c = bga_chip(f"{name}_gddr{i}", (x, y, top), size=(1.2, 1.4, 0.11), parent=p.root)
        c.root.rotation_euler.z = math.radians(rot)
        p.chips.append(c)
        p.mark(f"gddr{i}", (x + (0.6 if rot == 0 else 0), y + (0.7 if rot else 0), top + c.height * 0.6))
    # power stages: chokes and polymer capacitors at the right end
    vx0 = 4.3
    choke = box(f"{name}_chokes", (0.75, 0.75, 0.55), (vx0, -3.6, top + 0.275), M.plastic("choke", (0.05, 0.05,
                0.055), 0.4), 0.05, parent=p.root)
    grid_array(choke, 9, 0.95, 2, 1.0)
    p.add("chokes", choke)
    capsrc = geo.source("_polycap", lambda n: geo.bake(cylinder(n, 0.22, 0.5, (0, 0, 0.25),
                                                                M.aluminium("cap_top", 0.25), verts=20)))
    p.add("caps", geo.instance_points(f"{name}_caps", capsrc, grid_points(9, 1, 0.95, 1, (vx0 + 3.8, -1.2, top),
                                                                           ), parent=p.root))
    mos = box(f"{name}_mosfets", (0.5, 0.6, 0.12), (vx0, -1.95, top + 0.06), M.mould(), 0.02, parent=p.root)
    grid_array(mos, 9, 0.95, 1, 0)
    p.add("mosfets", mos)
    p.mark("vrm", (vx0 + 3.8, -3.1, top + 0.55))
    # small parts scattered on the free board
    avoid = [(gx - 4.6, gy - 4.6, gx + 4.6, gy + 4.6), (vx0 - 0.6, -4.3, vx0 + 8.2, -0.8), (-W / 2, -D / 2, -W / 2 + 1.5, D / 2)]
    pts = _clusters(rnd, 50, (-W / 2 + 1.0, -D / 2 + 1.0), (W / 2 - 0.9, D / 2 - 0.6), avoid)
    _passives(f"{name}_smd", pts, top, p.root)
    ics = _scatter(rnd, 14, (-W / 2 + 2, -D / 2 + 1.5), (W / 2 - 1, D / 2 - 1), avoid)
    for i, (x, y) in enumerate(ics):
        s = rnd.choice([0.4, 0.55, 0.8])
        box(f"{name}_ic{i}", (s, s, 0.08), (x, y, top + 0.04), M.mould(), 0.015, parent=p.root)
    # PCIe fingers on the bottom edge
    fx0 = -9.5
    finger = box(f"{name}_finger", (0.1, 0.7, 0.01), (fx0, -D / 2 - 0.35, t + 0.002), M.gold("finger_gold", 0.25), 0,
                 parent=p.root)
    grid_array(finger, 70, 0.17, 1, 0)
    tab = box(f"{name}_tab", (12.2, 0.75, t), (fx0 + 5.9, -D / 2 - 0.37, t / 2), M.board_black(), 0.01,
              parent=p.root)
    p.add("fingers", finger)
    p.add("tab", tab)
    p.mark("fingers", (fx0 + 6, -D / 2 - 0.4, t))
    # I/O bracket at the left end, standing up
    br = box(f"{name}_bracket", (0.08, D + 0.6, 2.8), (-W / 2 - 0.05, 0, 1.2), M.steel(), 0.01, parent=p.root)
    p.add("bracket", br)
    for i in range(4):
        box(f"{name}_port{i}", (0.6, 1.4, 0.55), (-W / 2 + 0.3, -3.5 + i * 2.1, top + 0.3),
            M.steel(), 0.04, parent=p.root)
    p.mark("edge", (W / 2, -D / 2, t))
    return p


def server_board(name="node", loc=(0, 0, 0), n_gpus=4, stacks_per_side=4, seed=5, parent=None):
    """An AI server node's GPU board: n GPU packages in a row, each with its
    HBM in the package (no memory chips around), connectors along the back,
    power stages in front. Points: gpu0..n, connectors, front."""
    import random

    rnd = random.Random(seed)
    pitch = 11.0
    W = n_gpus * pitch + 2.0
    D = 22.0
    t = 0.2
    p = Part(name, loc, parent)
    p.add("board", box(f"{name}_board", (W, D, t), (0, 0, t / 2), M.solder_mask("node_mask", (0.03, 0.12, 0.055),
                                                                              seed=7, scale=0.6), 0.04,
                       parent=p.root))
    p.gpus = []
    for i in range(n_gpus):
        x = (i - (n_gpus - 1) / 2) * pitch
        # 3.0-wide dies need the stacks out at 2.12 (the CoWoS package's spacing), not the default 1.85
        g = gpu_package(f"{name}_gpu{i}", (x, 1.0, t), stacks_per_side=stacks_per_side, dies=2, die_size=(3.0, 5.2),
                        stack_x=2.12, mould=True, parent=p.root)
        p.gpus.append(g)
        p.mark(f"gpu{i}", (x, 1.0, t + 0.4))
        # power stages in front of each package
        ps = box(f"{name}_ps{i}", (0.55, 0.55, 0.14), (x - 3.6, -6.2, t + 0.07), M.mould(), 0.02, parent=p.root)
        grid_array(ps, 12, 0.65, 2, 0.8)
        cap = box(f"{name}_ind{i}", (0.6, 0.6, 0.45), (x - 3.6, -8.2, t + 0.225),
                  M.plastic("choke", (0.05, 0.05, 0.055), 0.4), 0.04, parent=p.root)
        grid_array(cap, 12, 0.65, 1, 0)
    # connectors along the back edge
    for i in range(n_gpus * 2):
        x = -W / 2 + 2.5 + i * (W - 5) / (n_gpus * 2 - 1)
        box(f"{name}_conn{i}", (3.2, 1.2, 1.1), (x, D / 2 - 1.2, t + 0.55),
            M.plastic("connector", (0.05, 0.16, 0.13), 0.35), 0.06, parent=p.root)
        pins = box(f"{name}_pins{i}", (0.06, 0.25, 0.05), (x - 1.4, D / 2 - 2.0, t + 0.03), M.gold(), 0,
                   parent=p.root)
        grid_array(pins, 24, 0.12, 1, 0)
    p.mark("connectors", (0, D / 2 - 1.2, t + 1.1))
    avoid = [((i - (n_gpus - 1) / 2) * pitch - 5.4, -3.6, (i - (n_gpus - 1) / 2) * pitch + 5.4, 5.6)
             for i in range(n_gpus)]
    _passives(f"{name}_smd", _clusters(rnd, 110, (-W / 2 + 0.8, -D / 2 + 0.8), (W / 2 - 1.5, D / 2 - 2.5), avoid),
              t, p.root)
    p.mark("front", (0, -D / 2, t))
    p.size = (W, D)
    return p


# --- wires ----------------------------------------------------------------

class Wires(Part):
    """Parallel wires; effects.signal_pulses(shot, wires, ...) runs pulses down them."""


def wires(name="bus", start=(-3, 0, 0.1), end=(3, 0, 0.1), n=8, spacing=0.12, r=0.018, mat=None, parent=None):
    """n straight wires from start to end (along x), spaced in y and centred.
    They share one signal material: wires.material."""
    a, b = Vector(start), Vector(end)
    L = (b - a).length
    p = Wires(name, tuple((a + b) / 2), parent)
    d = (b - a).normalized()
    p.root.rotation_euler = d.to_track_quat("X", "Z").to_euler()
    p.material = mat or signal_material(f"{name}_signal")
    p.wires = []
    for i in range(n):
        y = (i - (n - 1) / 2) * spacing
        w = cylinder(f"{name}_w{i}", r, L, (0, y, 0), p.material, verts=12, axis="X", parent=p.root)
        p.wires.append(w)
    p.length = L
    p.mark("start", (-L / 2, 0, 0))
    p.mark("end", (L / 2, 0, 0))
    p.mark("middle", (0, -(n - 1) / 2 * spacing, 0))
    return p


def signal_material(name, base=(1.0, 0.76, 0.33), glow=(0.25, 1.0, 0.4)):
    """Gold wire whose emission is pulses travelling along local +x.
    Animate the value nodes: "phase" (pulse position), "pulse" (pulse
    brightness), "lit" (whole-wire glow); effects.signal_pulses does."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m, nt, b = M.node_mat(name)
    M._set(b, color=base, metallic=1.0, rough=0.3, emission=glow)
    vals = {}
    for key, v in (("phase", 0.0), ("pulse", 0.0), ("lit", 0.0), ("density", 1.0), ("strength", 8.0)):
        n = nt.nodes.new("ShaderNodeValue")
        n.name = n.label = key
        n.outputs[0].default_value = v
        vals[key] = n
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Object"], sep.inputs["Vector"])

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

    u = op("SUBTRACT", op("MULTIPLY", sep.outputs["X"], vals["density"].outputs[0]), vals["phase"].outputs[0])
    f = op("FRACT", u)
    tri = op("SUBTRACT", 1.0, op("MULTIPLY", op("ABSOLUTE", op("SUBTRACT", f, 0.5)), 2.0))
    pulse = op("MULTIPLY", op("POWER", tri, 18.0), vals["pulse"].outputs[0])
    e = op("MULTIPLY", op("ADD", pulse, vals["lit"].outputs[0]), vals["strength"].outputs[0])
    nt.links.new(e, b.inputs["Emission Strength"])
    return m


# --- layered builds and cross sections ------------------------------------

class LayerStack(Part):
    """Slabs stacked bottom first: stack.layers[key], stack.keys.

    stack.explode(shot, start, end, spread)   lift the layers apart
    stack.collapse(shot, start, end)
    stack.build_up(shot, start, step, drop)   layers drop in one by one
    """

    def explode(self, shot, start, end, spread=0.3, stagger=0.0, ease="inout"):
        for i, k in enumerate(self.keys):
            shot.tween(self.layers[k], "location.z", self.rest_z[k] + i * spread, start, end + i * stagger, ease)
        return self

    def collapse(self, shot, start, end, ease="inout"):
        for k in self.keys:
            shot.tween(self.layers[k], "location.z", self.rest_z[k], start, end, ease)
        return self

    def build_up(self, shot, start, step=0.6, drop=1.0, ease="out"):
        for i, k in enumerate(self.keys):
            t = start + i * step
            obj = self.layers[k]
            shot.show(obj, t)
            shot.tween(obj, "location.z", self.rest_z[k], t, t + step * 0.9, ease, frm=self.rest_z[k] + drop)
        return self


def layer_stack(name, layers, size=(4.0, 3.0), loc=(0, 0, 0), bevel_width=0.006, parent=None):
    """Slabs of `size` (x, y): layers = [(key, thickness, material), ...]
    bottom first. Points: each key (its front-left edge, mid height), top."""
    p = LayerStack(name, loc, parent)
    p.layers, p.rest_z, p.keys = {}, {}, []
    z = 0.0
    w, d = size
    for key, th, mat in layers:
        o = box(f"{name}_{key}", (w, d, th), (0, 0, z + th / 2), mat, bevel_width, parent=p.root)
        p.layers[key], p.rest_z[key] = o, z + th / 2
        p.keys.append(key)
        p.mark(key, (-w / 2, -d / 2, th / 2), obj=o)
        z += th
    p.height = z
    p.mark("top", (0, 0, z))
    return p


def substrate_section(name="substrate", loc=(0, 0, 0), size=(6.0, 1.6), copper_layers=4, core_t=0.42, diel_t=0.12,
                      cu_t=0.04, mask_t=0.05, seed=4, balls=True, parent=None):
    """An organic package substrate cut open, seen from -y: a glass-fibre
    core, copper layers (traces as rectangles on the cut face) in build-up
    dielectric, laser vias (copper cones) between layers, a plated
    through-hole in the core, solder mask both sides, solder balls below.
    Points: core, top, via, ball."""
    import random

    rnd = random.Random(seed)
    w, d = size
    p = Part(name, loc, parent)
    face = -d / 2
    diel = M.resin("build_up_film")
    core_mat = M.glass_fibre("core_fr4", weave=30.0, resin=(0.75, 0.8, 0.5))
    mask = M.solder_mask("section_mask", (0.04, 0.13, 0.07), traces=False)
    cu = M.copper()
    per_side = max(1, copper_layers // 2)
    order = ["mask"] + ["cu", "diel"] * (per_side - 1) + ["cu", "core", "cu"] + ["diel", "cu"] * (per_side - 1) + ["mask"]
    th = {"mask": mask_t, "cu": cu_t, "diel": diel_t, "core": core_t}
    via_x = sorted(rnd.uniform(-w / 2 + 0.4, w / 2 - 0.4) for _ in range(3))
    thru_x = rnd.uniform(-w / 4, w / 4)
    z = 0.0
    levels = []
    for i, kind in enumerate(order):
        t = th[kind]
        if kind == "cu":
            box(f"{name}_fill{i}", (w, d, t), (0, 0, z + t / 2), diel, 0, parent=p.root)
            x = -w / 2 + 0.05
            must = via_x + [thru_x]
            while x < w / 2 - 0.1:
                tw = rnd.choice([0.06, 0.08, 0.12, 0.2, 0.35])
                if any(abs(x + tw / 2 - m) < 0.2 for m in must):
                    tw = 0.24
                box(f"{name}_tr{i}_{x:.2f}", (tw, d, t), (x + tw / 2, -0.0008, z + t / 2), cu, 0.002, parent=p.root)
                x += tw + rnd.choice([0.05, 0.07, 0.1, 0.15])
            levels.append(z + t / 2)
        else:
            mat = {"mask": mask, "diel": diel, "core": core_mat}[kind]
            box(f"{name}_{kind}{i}", (w, d, t), (0, 0, z + t / 2), mat, 0.002 if kind != "mask" else 0.006,
                parent=p.root)
            if kind == "diel":
                for vx in via_x:
                    c = cylinder(f"{name}_via{i}_{vx:.2f}", 0.05, t, (vx, face, z + t / 2), cu, verts=20, r2=0.08)
                    geo.halve_object(c, "+y")
                    c.parent = p.root
            if kind == "core":
                c = cylinder(f"{name}_pth", 0.09, t + 2 * cu_t, (thru_x, face, z + t / 2), cu, verts=24)
                geo.halve_object(c, "+y")
                c.parent = p.root
                p.mark("core", (-w / 2, face, z + t / 2))
        z += t
    p.height = z
    p.mark("top", (-w / 2, face, z))
    p.mark("via", (via_x[0], face, z - mask_t - cu_t - diel_t / 2))
    if balls:
        src = geo.source("_ball_section", lambda n: geo.sphere(n, 0.12, mat=M.solder(), segments=20, rings=12))
        pts = [(q[0], 0.0, -0.09) for q in grid_points(int((w - 0.4) / 0.5) + 1, 1, 0.5, 1)]
        p.add("balls", geo.instance_points(f"{name}_balls", src, pts, parent=p.root))
        p.mark("ball", (pts[0][0], face, -0.09))
    return p


class WaferSection(Part):
    """A silicon wafer cut through a TSV, seen from -y.

    wafer.etch(shot, start, end)                 Bosch etch: one scallop per cycle
    wafer.deposit(shot, "liner", start, end)     hole walls turn oxide, then
             "barrier" (Ta/TaN), "seed" (copper seed)
    wafer.fill(shot, start, end)                 copper fills bottom-up
    wafer.strip_resist(shot, start, end)         the photoresist lifts away
    wafer.thin(shot, start, end, keep)           grind the back until the copper shows
    """

    WALLS = {
        "etched": ((0.075, 0.078, 0.115), 0.0, 0.5),
        "liner": ((0.30, 0.62, 0.40), 0.0, 0.3),
        "barrier": ((0.62, 0.64, 0.7), 1.0, 0.45),
        "seed": ((0.85, 0.42, 0.2), 1.0, 0.4),
    }

    def etch(self, shot, start, end, ease="out"):
        n = len(self.cutters)
        step = (end - start) / n
        for i, c in enumerate(self.cutters):
            t = start + i * step
            shot.set(c, "location", c["home"], t)
            shot.tween(c, "scale", tuple(c["scale1"]), t, t + step, ease, frm=tuple(c["scale0"]))
        return self

    def deposit(self, shot, which, start, end, ease="inout"):
        color, metal, rough = self.WALLS[which]
        b = self.wall_mat.node_tree.nodes["Principled BSDF"]
        shot.tween(b.inputs["Base Color"], "default_value", (*color, 1.0), start, end, ease)
        shot.tween(b.inputs["Metallic"], "default_value", metal, start, end, ease)
        shot.tween(b.inputs["Roughness"], "default_value", rough, start, end, ease)
        return self

    def fill(self, shot, start, end):
        n = len(self.plugs)
        step = (end - start) / n
        for i, plug in enumerate(reversed(self.plugs)):
            shot.show(plug, start + i * step)
        return self

    def strip_resist(self, shot, start, end, ease="in"):
        if self.resist is None:
            return self
        shot.tween(self.resist, "location.z", self.resist.location.z + self.size[2], start, end, ease)
        shot.hide(self.resist, end)
        return self

    def thin(self, shot, start, end, keep=None, ease="inout"):
        """Grind the back up to `keep` scallops deep (default: all but the
        last), in every hole at once."""
        keep = self.scallops - 1 if keep is None else keep
        h = self.size[2]
        new_h = (keep + 0.1) * self.pitch
        shot.tween(self.block, "scale.z", new_h / h, start, end, ease)
        for i, plug in enumerate(self.plugs):
            if i % self.scallops >= keep:
                shot.hide(plug, end)
        return self


def wafer_section(name="wafer", loc=(0, 0, 0), size=(3.0, 1.2, 1.6), holes=(0.0,), r=0.1, depth=1.0, scallops=8,
                  resist=True, resist_t=0.06, parent=None):
    """A block of silicon (size x, y, z; top surface at loc's z, cut face
    toward -y) with TSV holes at x positions `holes`, etched `depth` deep in
    `scallops` Bosch cycles. Nothing is etched until wafer.etch()."""
    import bpy

    w, d, h = size
    face = -d / 2
    p = WaferSection(name, loc, parent)
    p.size, p.pitch, p.scallops = size, depth / scallops, scallops
    coll = bpy.data.collections.new(f"{name}_cutters")
    bpy.context.scene.collection.children.link(coll)
    p.wall_mat = M.principled(f"{name}_walls", WaferSection.WALLS["etched"][0], rough=0.45)
    p.block = box(f"{name}_block", (w, d, h), (0, 0, -h / 2), M.silicon_section(), 0, parent=p.root)
    geo.bake(p.block)  # origin at the top surface so thinning raises the bottom
    p.add("block", p.block)
    mod = p.block.modifiers.new("etch", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.operand_type = "COLLECTION"
    mod.collection = coll
    mod.solver = "EXACT"
    if hasattr(mod, "material_mode"):
        mod.material_mode = "TRANSFER"
    p.cutters, p.plugs = [], []
    hz = p.pitch * 0.62
    for hx in holes:
        for i in range(scallops):
            zc = -(i + 0.5) * p.pitch
            c = geo.sphere(f"{name}_cut{hx:.2f}_{i}", r, (hx, face, 50.0), p.wall_mat, segments=24, rings=12)
            c.parent = p.root
            for pc in c.users_collection:
                pc.objects.unlink(c)
            coll.objects.link(c)
            c.hide_render = True
            c.display_type = "WIRE"
            c["home"] = (hx, face, zc)
            c["scale1"] = (1.0, 1.0, hz / r)
            c["scale0"] = (0.55, 0.55, hz / r * 0.55)
            c.scale = (1.0, 1.0, hz / r)
            p.cutters.append(c)
            plug = geo.sphere(f"{name}_cu{hx:.2f}_{i}", r * 0.9, (hx, face - 0.0008, zc), M.principled("tsv_fill", (0.78, 0.36, 0.15), metallic=1.0, rough=0.35),
                              segments=24, rings=12, half="+y", parent=p.root)
            plug.scale = (1.0, 1.0, hz / r)
            plug.hide_render = True
            p.plugs.append(plug)
        p.mark(f"hole{hx:g}", (hx, face, -depth / 2))
    p.resist = None
    if resist:
        res = box(f"{name}_resist", (w, d, resist_t), (0, 0, resist_t / 2), M.photoresist(), 0, parent=p.root)
        m2 = res.modifiers.new("open", "BOOLEAN")
        m2.operation = "DIFFERENCE"
        m2.solver = "EXACT"
        openers = bpy.data.collections.new(f"{name}_openings")
        bpy.context.scene.collection.children.link(openers)
        for hx in holes:  # one opening per hole, at its own x
            opener = cylinder(f"{name}_opening{hx:g}", r, resist_t * 4, (hx, face, resist_t / 2), None, verts=32)
            for pc in opener.users_collection:
                pc.objects.unlink(opener)
            openers.objects.link(opener)
            opener.parent = p.root
            opener.hide_render = True
            opener.display_type = "WIRE"
        m2.operand_type = "COLLECTION"
        m2.collection = openers
        p.resist = res
        p.add("resist", res)
        p.mark("resist", (-w / 2 + 0.1, face, resist_t))
    p.mark("surface", (-w / 2 + 0.1, face, 0.0))
    p.mark("bottom", (0, face, -h))
    return p
