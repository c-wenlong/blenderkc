"""Procedural images drawn with Pillow and handed to Blender as packed pixels
(no files, so the server needs nothing but the code). Deterministic per seed.

traces_mask(seed): a tileable PCB routing pattern, white traces, pads and vias
on black, used by solder_mask (copper showing through) and copper_traces.
"""
import random

import bpy

SIZE = 1024


def _draw_traces(seed, size):
    from PIL import Image, ImageDraw

    rnd = random.Random(seed)
    img = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(img)

    def seg(p, q, w):
        # draw with wrap-around so the tile repeats without seams
        for ox in (-size, 0, size):
            for oy in (-size, 0, size):
                d.line([(p[0] + ox, p[1] + oy), (q[0] + ox, q[1] + oy)], fill=255, width=w)

    def dot(p, r, ring=False):
        for ox in (-size, 0, size):
            for oy in (-size, 0, size):
                box = [p[0] + ox - r, p[1] + oy - r, p[0] + ox + r, p[1] + oy + r]
                if ring:
                    d.ellipse(box, outline=255, width=max(2, r // 3))
                else:
                    d.ellipse(box, fill=255)

    dirs = [(1, 0), (0, 1), (-1, 0), (0, -1)]
    for _ in range(26):
        n = rnd.choice([3, 4, 6, 8, 10])
        w = rnd.choice([2, 3, 3, 4])
        pitch = w * rnd.choice([3, 4])
        x, y = rnd.uniform(0, size), rnd.uniform(0, size)
        dx, dy = rnd.choice(dirs)
        pts = [[(x - dy * k * pitch, y + dx * k * pitch)] for k in range(n)]
        for _leg in range(rnd.randint(2, 4)):
            run = rnd.uniform(60, 260)
            for k in range(n):
                px, py = pts[k][-1]
                pts[k].append((px + dx * run, py + dy * run))
            # a 45 degree jog, then turn
            turn = rnd.choice([-1, 1])
            ndx, ndy = (-dy * turn, dx * turn)
            for k in range(n):
                px, py = pts[k][-1]
                j = (k * pitch * 0.4142) if turn > 0 else ((n - 1 - k) * pitch * 0.4142)
                diag = 30 + j
                pts[k].append((px + (dx + ndx) * diag * 0.7071, py + (dy + ndy) * diag * 0.7071))
            dx, dy = ndx, ndy
        for k in range(n):
            line = pts[k]
            for a, b in zip(line, line[1:]):
                seg(a, b, w)
            dot(line[0], w + 3)
            dot(line[-1], w + 4, ring=rnd.random() < 0.5)
    for _ in range(60):
        dot((rnd.uniform(0, size), rnd.uniform(0, size)), rnd.choice([4, 5, 7]), ring=True)
    return img


def traces_image(seed=1, size=SIZE):
    """A Blender image of the routing pattern (cached per seed), or None
    without Pillow (materials then skip the traces)."""
    name = f"bkc_traces_{seed}_{size}"
    im = bpy.data.images.get(name)
    if im:
        return im
    try:
        pil = _draw_traces(seed, size)
    except ImportError:
        return None
    import numpy as np

    a = np.asarray(pil, dtype=np.float32)[::-1] / 255.0
    rgba = np.empty((size, size, 4), dtype=np.float32)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = a
    rgba[..., 3] = 1.0
    im = bpy.data.images.new(name, size, size, alpha=False, float_buffer=False)
    im.pixels.foreach_set(rgba.ravel())
    im.pack()
    return im

