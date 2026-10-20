"""Composite a shot's 2D overlays onto its rendered frames, with Pillow.

    <venv python> -m blenderkc.overlay --frames <dir> --out <dir> [--from A --to B] [--shot <shot.json>]
        [--state <file>]

<frames dir> holds f_0001.png ... (any subset: a chunk of a scene is fine) and
shot.json from render.py. Each frame in range that exists is written to --out
under the same name: composited when an overlay is live, otherwise copied.
Positions, line widths and text scale with the frame's width over shot.json's
width, so a 50% still gets the same layout as the final.

Kinds (see Shot): label (leader line to a moving 3D anchor), title, equation
(plain Unicode, [terms] in an accent colour) and glow (lit text). Inter is
shipped in blenderkc/fonts (SIL Open Font License, fonts/OFL.txt), so the Mac
and the render server draw identical text.

Ink follows the frame. Each overlay samples the rendered frame under its text
and takes the ink that contrasts more with it: white with a dark halo on a
dark frame, near-black with a light halo on a bright one (titles and
equations put a soft cloud of the halo's colour behind them, labels draw
their leader and ring in the same ink). The ink is chosen once per overlay,
the first frame it is drawn, and kept (`--state` carries the choice across
the chunks of one scene), so a label never flips colour mid-shot; the halo's
strength is set every frame from the worst contrast under the text, so text
crossing a busy or mid-grey patch stays readable.
"""
import argparse
import json
import os
import re
import shutil
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_FILES = {"regular": "Inter-Regular.ttf", "medium": "Inter-Medium.ttf", "semibold": "Inter-SemiBold.ttf"}
BASE_W = 1920
FADE_IN, FADE_OUT = 0.35, 0.3
COLORS = {
    "green": (96, 255, 150), "red": (255, 96, 96), "amber": (255, 192, 72), "blue": (120, 190, 255),
    "white": (255, 255, 255), "violet": (190, 150, 255),
}
ACCENT = (120, 235, 160)
LIGHT_INK = (255, 255, 255)        # on dark frames
DARK_INK = (20, 22, 26)            # on bright frames
SUB_INK = {"light": (225, 228, 232), "dark": (48, 52, 58)}
HALO = {"light": (0, 0, 0), "dark": (255, 255, 255)}   # the halo is the ink's opposite
DEEP_LUM = 0.04                    # a coloured ink on a bright frame is darkened to this luminance
MIN_CONTRAST = 4.5                 # WCAG AA for text
_fonts = {}


def _font(weight, px):
    key = (weight, int(round(px)))
    f = _fonts.get(key)
    if f is None:
        f = _fonts[key] = ImageFont.truetype(os.path.join(FONTS, FONT_FILES[weight]), max(6, key[1]))
    return f


def _clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _ease_out(u):
    u = _clamp01(u)
    return 1 - (1 - u) ** 3


def _alpha_at(rec, t):
    return _clamp01(min((t - rec["start"]) / FADE_IN, (rec["end"] - t) / FADE_OUT))


def _live(doc, t):
    return [r for r in doc.get("overlays", []) if r["start"] <= t < r["end"]]


def _color_of(c):
    if isinstance(c, str):
        return COLORS.get(c, COLORS["green"])
    return tuple(int(x) for x in c[:3])


# -- contrast ----------------------------------------------------------------

_LIN = [(i / 255) / 12.92 if i / 255 <= 0.04045 else (((i / 255) + 0.055) / 1.055) ** 2.4 for i in range(256)]


def luminance(rgb):
    """WCAG relative luminance of an sRGB colour (0..255 channels)."""
    r, g, b = (int(round(v)) for v in rgb[:3])
    return 0.2126 * _LIN[r] + 0.7152 * _LIN[g] + 0.0722 * _LIN[b]


def contrast(a, b):
    """WCAG contrast ratio of two luminances (or two colours)."""
    la = a if isinstance(a, float) else luminance(a)
    lb = b if isinstance(b, float) else luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _srgb_of(lum):
    """The sRGB grey level (0..255) whose relative luminance is lum."""
    lum = min(1.0, max(0.0, lum))
    v = lum * 12.92 if lum <= 0.0031308 else 1.055 * lum ** (1 / 2.4) - 0.055
    return 255.0 * v


def _badge_fill_alpha(ink, under_lum, fill=(8, 14, 10), lo=150 / 255, want=7.0):
    """How opaque a badge's dark fill must be for `ink` to clear `want`:1
    against the fill laid over a background of luminance under_lum; never
    less than lo (the look on a dark frame), at most 0.92."""
    g = _srgb_of(under_lum)
    a = lo
    while a < 0.92:
        mix = tuple(a * f + (1 - a) * g for f in fill)
        if contrast(luminance(ink), luminance(mix)) >= want:
            break
        a += 0.01
    return min(a, 0.92)


def _deepen(color, target=DEEP_LUM):
    """The colour darkened (hue kept) until its luminance is at most target."""
    c = tuple(float(v) for v in color[:3])
    for _ in range(40):
        if luminance(c) <= target:
            break
        c = tuple(v * 0.92 for v in c)
    return tuple(int(round(v)) for v in c)


def _ink(pol, color=None):
    """The ink for a polarity: white or near-black, or a colour as it is on
    dark frames and deepened on bright ones."""
    if color is None:
        return LIGHT_INK if pol == "light" else DARK_INK
    return tuple(color[:3]) if pol == "light" else _deepen(color)


class _Canvas:
    """The layers for one frame: dark and light scrims (soft clouds behind
    titles), dark and light halos, the glow, and the ink on top. A layer is
    filled with its own colour at zero alpha so blurring never fringes it."""

    def __init__(self, base, k, state):
        self.base, self.size, self.k, self.state = base, base.size, k, state
        new = lambda rgb: Image.new("RGBA", base.size, (*rgb, 0))  # noqa: E731
        self.fg = new((0, 0, 0))
        self.glow = new((0, 0, 0))
        self.scrim = {"light": new(HALO["light"]), "dark": new(HALO["dark"])}
        self.halo = {"light": new(HALO["light"]), "dark": new(HALO["dark"])}
        self.d = ImageDraw.Draw(self.fg)
        self.dg = ImageDraw.Draw(self.glow)
        self.dsc = {p: ImageDraw.Draw(im) for p, im in self.scrim.items()}
        self.dh = {p: ImageDraw.Draw(im) for p, im in self.halo.items()}
        self._lum = None

    # what is under a box of the rendered frame
    def lums(self, box):
        """Sorted relative luminances of the frame inside box (clamped),
        sampled on a grid of at most about 64 x 64."""
        W, H = self.size
        x0, y0, x1, y1 = (int(max(0, min(W, v))) if i % 2 == 0 else int(max(0, min(H, v)))
                          for i, v in enumerate(box))
        if x1 - x0 < 1 or y1 - y0 < 1:
            return [0.0]
        if self._lum is None:
            self._lum = self.base.convert("RGB")
        crop = self._lum.crop((x0, y0, x1, y1))
        s = max(1.0, max(crop.size) / 64.0)
        if s > 1:
            crop = crop.resize((max(1, int(crop.size[0] / s)), max(1, int(crop.size[1] / s))), Image.BOX)
        L, b = _LIN, crop.tobytes()
        return sorted(0.2126 * L[b[i]] + 0.7152 * L[b[i + 1]] + 0.0722 * L[b[i + 2]] for i in range(0, len(b), 3))

    def polarity(self, key, box):
        """'light' (white ink) or 'dark' (near-black ink) for an overlay, from
        the median luminance under box the first time it is asked; kept in
        state after that."""
        pol = self.state.get(key)
        if pol is None:
            ls = self.lums(box)
            mid = ls[len(ls) // 2]
            pol = "light" if contrast(luminance(LIGHT_INK), mid) >= contrast(luminance(DARK_INK), mid) else "dark"
            self.state[key] = pol
        return pol

    def halo_strength(self, ink, box):
        """0.5 (a soft shadow) where the ink already contrasts well with
        everything under box, up to 0.95 where some of it does not."""
        ls = self.lums(box)
        lo, hi = ls[len(ls) // 10], ls[(len(ls) * 9) // 10]
        worst = min(contrast(luminance(ink), lo), contrast(luminance(ink), hi))
        return 0.5 + 0.45 * _clamp01((7.0 - worst) / 4.5)

    def compose(self):
        out = self.base.convert("RGBA")
        for p in ("dark", "light"):
            out = _blur_over(out, self.scrim[p], 36 * self.k)
        for p in ("dark", "light"):
            out = _blur_over(out, self.halo[p], 2.6 * self.k)
        if self.glow.getbbox():
            out = _blur_over(out, self.glow, 14 * self.k, twice=True)
        return Image.alpha_composite(out, self.fg).convert("RGB")


def _blur_over(out, layer, radius, twice=False):
    """Composite layer, Gaussian-blurred, over out, blurring only the part of
    the layer that holds something (plus a margin)."""
    bb = layer.getbbox()
    if not bb:
        return out
    m = int(3 * radius) + 2
    W, H = layer.size
    box = (max(0, bb[0] - m), max(0, bb[1] - m), min(W, bb[2] + m), min(H, bb[3] + m))
    g = layer.crop(box).filter(ImageFilter.GaussianBlur(radius))
    region = out.crop(box)
    region = Image.alpha_composite(region, g)
    if twice:
        region = Image.alpha_composite(region, g)
    out.paste(region, box[:2])
    return out


def _anchor(doc, name, frame, sx):
    a = doc.get("anchors", {}).get(name)
    if not a or frame - 1 >= len(a) or a[frame - 1] is None:
        return None
    x, y, vis = a[frame - 1]
    return (x * sx, y * sx, vis)


def _key(rec, i):
    return f"{i}:{rec['kind']}:{rec['text']}:{rec['start']:.3f}"


def _halo_text(c, pol, xy, text, f, a, anchor="ls"):
    """The halo under a run of text: the glyphs with a stroke, in the halo's colour."""
    sw = max(1, round(2.6 * c.k))
    c.dh[pol].text(xy, text, font=f, fill=(*HALO[pol], int(255 * a)), anchor=anchor, stroke_width=sw,
                   stroke_fill=(*HALO[pol], int(255 * a)))


def _pad(box, p):
    return (box[0] - p, box[1] - p, box[2] + p, box[3] + p)


def _draw_label(c, rec, t, frame, doc, sx, key):
    p = _anchor(doc, rec["anchor"], frame, sx)
    if p is None or not p[2]:
        return
    ax, ay, _ = p
    k, (W, H) = c.k, c.size
    a = _alpha_at(rec, t)
    side = rec.get("side", "auto")
    if side == "auto":
        side = ("left" if ax > W * 0.62 else "right") + ("-below" if ay < H * 0.22 else "")
    sgn = -1 if "left" in side else 1
    up = 1 if "below" in side else -1
    dx = rec.get("dx", sgn * 110) * k
    dy = rec.get("dy", up * 80) * k
    sgn = -1 if dx < 0 else 1
    f = _font("medium", rec.get("size", 30) * k)
    tw = f.getlength(rec["text"])
    ex, ey = ax + dx, ay + dy
    # keep the text inside the frame: flip sideways or vertically if it would leave
    if not 0 <= ex + sgn * (tw + 18 * k) <= W:
        dx, sgn = -dx, -sgn
        ex = ax + dx
    if not 40 * k <= ey <= H - 10 * k:
        dy = -dy
        ey = ay + dy
    pts = [(ax, ay), (ex, ey), (ex + sgn * (tw + 18 * k), ey)]
    tx = ex + sgn * 9 * k
    anchor = "ls" if sgn > 0 else "rs"
    xy = (tx, ey - 9 * k)
    tbox = c.d.textbbox(xy, rec["text"], font=f, anchor=anchor)
    pol = c.polarity(key, _pad(tbox, 6 * k))
    ink = _ink(pol)
    # the leader and its ring are lines, not text: judge them with the text's box and their own
    lbox = (min(ax, ex) - 8 * k, min(ay, ey) - 8 * k, max(ax, ex) + 8 * k, max(ay, ey) + 8 * k)
    hs = c.halo_strength(ink, _pad(tbox, 4 * k))
    hl = max(hs, c.halo_strength(ink, lbox))
    grow = _ease_out((t - rec["start"]) / 0.55)
    # leader drawn on along its length
    lens = [((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) ** 0.5 for i in range(2)]
    todo = grow * sum(lens)
    line = [pts[0]]
    for i in range(2):
        if todo <= 0:
            break
        u = min(1.0, todo / lens[i]) if lens[i] else 1.0
        line.append((pts[i][0] + (pts[i + 1][0] - pts[i][0]) * u, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * u))
        todo -= lens[i]
    lw = max(1, round(2 * k))
    hw = lw + max(2, round(3 * k))
    col = (*ink, int(235 * a))
    halo = (*HALO[pol], int(255 * a * hl))
    r = 7 * k
    if line and lens[0]:
        ux, uy = (pts[1][0] - ax) / lens[0], (pts[1][1] - ay) / lens[0]
        if len(line) > 1 and ((line[1][0] - ax) ** 2 + (line[1][1] - ay) ** 2) ** 0.5 > r:
            line[0] = (ax + ux * r, ay + uy * r)
        else:
            line = line[:1]
    if len(line) > 1:
        c.dh[pol].line(line, fill=halo, width=hw, joint="curve")
        c.d.line(line, fill=col, width=lw, joint="curve")
    c.dh[pol].ellipse([ax - r, ay - r, ax + r, ay + r], outline=halo, width=hw)
    c.d.ellipse([ax - r, ay - r, ax + r, ay + r], outline=col, width=lw)
    c.d.ellipse([ax - 2.6 * k, ay - 2.6 * k, ax + 2.6 * k, ay + 2.6 * k], fill=col)
    ta = a * _clamp01((t - rec["start"] - 0.25) / 0.35)
    if ta > 0:
        _halo_text(c, pol, xy, rec["text"], f, ta * hs, anchor)
        c.d.text(xy, rec["text"], font=f, fill=(*ink, int(255 * ta)), anchor=anchor)
    return {"kind": "label", "polarity": pol, "ink": ink, "box": tbox, "parts": [(ink, tbox)]}


def _scrim(c, pol, box, a, strength=110):
    """A soft cloud behind text in the halo's colour (dark behind white ink,
    light behind dark ink), so it reads over a busy frame."""
    c.dsc[pol].ellipse(box, fill=(*HALO[pol], int(strength * a)))


def _draw_title(c, rec, t, frame, doc, sx, key):
    k, (W, H) = c.k, c.size
    a = _alpha_at(rec, t)
    f = _font("semibold", rec.get("size", 72) * k)
    pos = rec.get("position", "center")
    cy = {"center": 0.5, "lower": 0.78, "top": 0.17}.get(pos, 0.5) * H
    cy += (1 - _ease_out((t - rec["start"]) / 0.6)) * 14 * k
    tw = f.getlength(rec["text"])
    sub = rec.get("sub")
    fs = _font("regular", rec.get("size", 72) * 0.44 * k) if sub else None
    h = rec.get("size", 72) * k
    mbox = tbox = c.d.textbbox((W / 2, cy), rec["text"], font=f, anchor="ms")
    sbox = None
    if sub:
        sbox = c.d.textbbox((W / 2, cy + h * 0.62), sub, font=fs, anchor="ms")
        tbox = (min(tbox[0], sbox[0]), tbox[1], max(tbox[2], sbox[2]), sbox[3])
    pol = c.polarity(key, _pad(tbox, 10 * k))
    ink = _ink(pol)
    hs = c.halo_strength(ink, _pad(tbox, 6 * k))
    _scrim(c, pol, (W / 2 - tw * 0.62 - 40 * k, cy - h * 0.9, W / 2 + tw * 0.62 + 40 * k,
                    cy + h * (1.0 if sub else 0.5)), a, 90 + 60 * (hs - 0.5))
    _halo_text(c, pol, (W / 2, cy), rec["text"], f, a * hs, "ms")
    c.d.text((W / 2, cy), rec["text"], font=f, fill=(*ink, int(255 * a)), anchor="ms")
    if sub:
        sa = a * _clamp01((t - rec["start"] - 0.2) / 0.4)
        y2 = cy + h * 0.62
        _halo_text(c, pol, (W / 2, y2), sub, fs, sa * hs, "ms")
        c.d.text((W / 2, y2), sub, font=fs, fill=(*SUB_INK[pol], int(255 * sa)), anchor="ms")
    parts = [(ink, mbox)] + ([(SUB_INK[pol], sbox)] if sub else [])
    return {"kind": "title", "polarity": pol, "ink": ink, "box": tbox, "parts": parts}


def _segments(text):
    out = []
    for part in re.split(r"(\[[^\]]*\])", text):
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            out.append((part[1:-1], True))
        else:
            out.append((part, False))
    return out


def _place(rec, frame, doc, sx, c, default):
    if "anchor" in rec:
        p = _anchor(doc, rec["anchor"], frame, sx)
        if p is None or not p[2]:
            return None
        return (p[0], p[1] - 70 * c.k)
    if "at" in rec:
        return (rec["at"][0] * c.k, rec["at"][1] * c.k)
    return default


def _draw_equation(c, rec, t, frame, doc, sx, key):
    k, (W, H) = c.k, c.size
    a = _alpha_at(rec, t)
    pos = _place(rec, frame, doc, sx, c, (W / 2, H * 0.8))
    if pos is None:
        return
    f = _font("medium", rec.get("size", 56) * k)
    segs = _segments(rec["text"])
    total = sum(f.getlength(s) for s, _ in segs)
    x = pos[0] - total / 2
    y = pos[1] + (1 - _ease_out((t - rec["start"]) / 0.6)) * 10 * k
    h = rec.get("size", 56) * k
    tbox = c.d.textbbox((x, y), rec["text"].replace("[", "").replace("]", ""), font=f, anchor="ls")
    pol = c.polarity(key, _pad(tbox, 10 * k))
    ink = _ink(pol)
    accent = _ink(pol, tuple(rec.get("accent", ACCENT))[:3])
    hs = max(c.halo_strength(ink, _pad(tbox, 6 * k)), c.halo_strength(accent, _pad(tbox, 6 * k)))
    _scrim(c, pol, (x - 40 * k, y - h * 0.95, x + total + 40 * k, y + h * 0.45), a, 85 + 60 * (hs - 0.5))
    parts = []
    for s, hi in segs:
        _halo_text(c, pol, (x, y), s, f, a * hs)
        col = (*accent, int(255 * a)) if hi else (*ink, int(255 * a))
        c.d.text((x, y), s, font=f, fill=col, anchor="ls")
        if s.strip():
            parts.append((accent if hi else ink, c.d.textbbox((x, y), s, font=f, anchor="ls")))
        x += f.getlength(s)
    return {"kind": "equation", "polarity": pol, "ink": ink, "accent": accent, "box": tbox, "parts": parts}


def _draw_glow(c, rec, t, frame, doc, sx, key):
    k, (W, H) = c.k, c.size
    a = _alpha_at(rec, t)
    pos = _place(rec, frame, doc, sx, c, (W / 2, H / 2))
    if pos is None:
        return
    col = _color_of(rec.get("color", "green"))
    f = _font("semibold", rec.get("size", 64) * k)
    tw = f.getlength(rec["text"])
    x, y = pos
    hot = tuple(int(v + (255 - v) * 0.55) for v in col)
    tbox = c.d.textbbox((x, y), rec["text"], font=f, anchor="mm")
    if rec.get("box"):
        # a lit badge: its dark fill is the text's background, so the lit ink is always kept;
        # the fill is made as dense as the brightest part of the frame under it needs, every frame
        h = rec.get("size", 64) * k
        box = (x - tw / 2 - 22 * k, y - h * 0.62, x + tw / 2 + 22 * k, y + h * 0.62)
        ls = c.lums(box)
        fa = _badge_fill_alpha(hot, ls[(len(ls) * 9) // 10])
        c.dg.rounded_rectangle(box, radius=10 * k, outline=(*col, int(255 * a)), width=max(2, round(6 * k)))
        c.d.rounded_rectangle(box, radius=10 * k, fill=(8, 14, 10, int(255 * fa * a)),
                              outline=(*hot, int(255 * a)), width=max(1, round(2.5 * k)))
        c.dg.text((x, y), rec["text"], font=f, fill=(*col, int(255 * a)), anchor="mm")
        c.d.text((x, y), rec["text"], font=f, fill=(*hot, int(255 * a)), anchor="mm")
        return {"kind": "glow", "polarity": "light", "ink": hot, "box": tbox, "parts": [(hot, tbox)]}
    pol = c.polarity(key, _pad(tbox, 10 * k))
    ink = hot if pol == "light" else _deepen(col)
    hs = c.halo_strength(ink, _pad(tbox, 6 * k))
    c.dg.text((x, y), rec["text"], font=f, fill=(*col, int(255 * a * (1.0 if pol == "light" else 0.3))), anchor="mm")
    if pol == "dark" or hs > 0.5:
        _halo_text(c, pol, (x, y), rec["text"], f, a * hs, "mm")
    c.d.text((x, y), rec["text"], font=f, fill=(*ink, int(255 * a)), anchor="mm")
    return {"kind": "glow", "polarity": pol, "ink": ink, "box": tbox, "parts": [(ink, tbox)]}


DRAW = {"label": _draw_label, "title": _draw_title, "equation": _draw_equation, "glow": _draw_glow}


def composite_frame(img, doc, frame, state=None, report=None):
    """Draw every overlay live at `frame` onto a PIL image; returns a new RGB
    image. `state` (a dict, kept by the caller across frames) holds each
    overlay's ink once chosen; `report`, a list, gets one record per overlay
    drawn ({kind, polarity, ink, box, parts}: parts lists each text colour
    with the box of the text drawn in it), for tests."""
    t = (frame - 1) / doc["fps"]
    recs = [(i, r) for i, r in enumerate(doc.get("overlays", [])) if r["start"] <= t < r["end"]]
    if not recs:
        return img.convert("RGB")
    k = img.width / BASE_W
    sx = img.width / float(doc.get("width", BASE_W))
    c = _Canvas(img.convert("RGB"), k, {} if state is None else state)
    for i, r in recs:
        got = DRAW[r["kind"]](c, r, t, frame, doc, sx, _key(r, i))
        if got is not None and report is not None:
            report.append(got)
    return c.compose()


def _load_state(path):
    if path and os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}
    return {}


def _save_state(path, state):
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def composite_dir(frames_dir, out_dir, shot_json=None, first=None, last=None, state_file=None):
    """Composite frames first..last (default: all in shot.json) that exist in
    frames_dir into out_dir. state_file (optional) keeps each overlay's ink
    across calls, so the chunks of one scene agree. Returns (composited,
    copied, missing)."""
    with open(shot_json or os.path.join(frames_dir, "shot.json")) as fh:
        doc = json.load(fh)
    first = first or 1
    last = last or doc["frames"]
    os.makedirs(out_dir, exist_ok=True)
    state = _load_state(state_file)
    done = copied = missing = 0
    same = os.path.abspath(frames_dir) == os.path.abspath(out_dir)
    for f in range(first, last + 1):
        name = f"f_{f:04d}.png"
        src, dst = os.path.join(frames_dir, name), os.path.join(out_dir, name)
        if not os.path.exists(src):
            missing += 1
            continue
        t = (f - 1) / doc["fps"]
        if not _live(doc, t):
            if not same:
                if os.path.exists(dst):
                    os.remove(dst)
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copyfile(src, dst)
            copied += 1
            continue
        with Image.open(src) as im:
            out = composite_frame(im, doc, f, state)
        tmp = dst + ".tmp.png"
        out.save(tmp, compress_level=1)
        os.replace(tmp, dst)
        done += 1
    _save_state(state_file, state)
    return done, copied, missing


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m blenderkc.overlay", description="Composite overlays onto frames.")
    ap.add_argument("--frames", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--shot", help="shot.json (default: <frames>/shot.json)")
    ap.add_argument("--from", dest="first", type=int)
    ap.add_argument("--to", dest="last", type=int)
    ap.add_argument("--state", help="a JSON file keeping each overlay's ink across chunks of one scene")
    a = ap.parse_args(argv)
    done, copied, missing = composite_dir(a.frames, a.out, a.shot, a.first, a.last, a.state)
    note = f", {missing} not in {a.frames}" if missing else ""
    print(f"blenderkc overlay: {done} composited, {copied} copied{note}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
