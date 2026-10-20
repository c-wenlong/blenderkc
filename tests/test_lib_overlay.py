"""Overlay compositing (blenderkc.overlay), with Pillow."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PIL import Image

    from blenderkc import overlay as O
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def doc(frames=30, overlays=None, anchor=(960.0, 540.0, 1)):
    return {"scene": "T", "fps": 30, "frames": frames, "width": 1920, "height": 1080,
            "anchors": {"a": [list(anchor)] * frames}, "overlays": overlays or []}


@unittest.skipUnless(HAVE_PIL, "Pillow not importable")
class OverlayTest(unittest.TestCase):
    def test_label_draws_at_its_anchor_and_scales_with_the_frame(self):
        d = doc(overlays=[{"kind": "label", "text": "Interposer", "anchor": "a", "start": 0.0, "end": 1.0,
                           "side": "right", "size": 30}])
        for w, h in ((1920, 1080), (960, 540)):
            out = O.composite_frame(Image.new("RGB", (w, h)), d, 15)
            px = out.getpixel((w // 2, h // 2))  # the anchor dot
            self.assertGreater(sum(px), 600, (w, px))
            self.assertEqual(out.getpixel((10, 10)), (0, 0, 0))  # nothing far away

    def test_hidden_anchor_draws_nothing_and_fades_follow_time(self):
        d = doc(overlays=[{"kind": "label", "text": "x", "anchor": "a", "start": 0.0, "end": 1.0}],
                anchor=(960.0, 540.0, 0))
        out = O.composite_frame(Image.new("RGB", (480, 270)), d, 20)
        self.assertEqual(out.getbbox(), None)
        rec = {"start": 1.0, "end": 3.0}
        self.assertEqual(O._alpha_at(rec, 1.0), 0.0)
        self.assertEqual(O._alpha_at(rec, 2.0), 1.0)
        self.assertLess(O._alpha_at(rec, 2.9), 1.0)

    def test_every_kind_renders(self):
        recs = [
            {"kind": "title", "text": "High bandwidth memory", "sub": "Eight dies", "start": 0, "end": 1},
            {"kind": "equation", "text": "Bandwidth = [frequency] × [bus width]", "start": 0, "end": 1},
            {"kind": "glow", "text": "1,152 GB", "start": 0, "end": 1, "color": "green", "box": True,
             "at": [960, 300]},
            {"kind": "glow", "text": "266", "start": 0, "end": 1, "color": [255, 80, 80], "anchor": "a"},
        ]
        out = O.composite_frame(Image.new("RGB", (960, 540)), doc(overlays=recs), 20)
        self.assertIsNotNone(out.getbbox())
        self.assertEqual(O._segments("a [b] c"), [("a ", False), ("b", True), (" c", False)])

    def test_text_is_drawn_in_inter_at_its_size(self):
        # the title's ink must be as wide as Inter says it is, so the shipped
        # font (not Pillow's default) drew it
        text = "High bandwidth memory"
        d = doc(overlays=[{"kind": "title", "text": text, "start": 0, "end": 2, "size": 72}])
        img = O.composite_frame(Image.new("RGB", (1920, 1080)), d, 31)
        band = img.crop((0, 470, 1920, 560)).convert("L").point(lambda v: 255 if v > 200 else 0)
        x0, _, x1, _ = band.getbbox()
        want = O._font("semibold", 72).getlength(text)
        self.assertAlmostEqual(x1 - x0, want, delta=want * 0.06)

    def test_composite_dir_handles_a_chunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "in"), os.path.join(tmp, "out")
            os.makedirs(src)
            d = doc(frames=60, overlays=[{"kind": "title", "text": "T", "start": 1.1, "end": 1.5}])
            with open(os.path.join(src, "shot.json"), "w") as fh:
                json.dump(d, fh)
            for f in range(31, 41):  # only a chunk of the scene is on disk
                Image.new("RGB", (192, 108)).save(os.path.join(src, f"f_{f:04d}.png"))
            done, copied, missing = O.composite_dir(src, dst, first=31, last=50)
            self.assertEqual((done, copied, missing), (7, 3, 10))
            self.assertTrue(os.path.exists(os.path.join(dst, "f_0031.png")))
            self.assertFalse(os.path.exists(os.path.join(dst, "f_0041.png")))

    # -- legibility: ink chosen from the frame, checked with the WCAG ratio

    LEGIBLE = [
        {"kind": "label", "text": "Dummy bumps carry heat", "anchor": "a", "start": 0, "end": 5, "side": "right"},
        {"kind": "title", "text": "One heating step", "sub": "Mass reflow", "start": 0, "end": 5,
         "position": "top", "size": 60},
        {"kind": "equation", "text": "16 × 32 = [512 bits]", "start": 0, "end": 5},
        {"kind": "glow", "text": "Short", "color": "red", "start": 0, "end": 5, "at": [1500, 520]},
        {"kind": "glow", "text": "288 GB", "color": "green", "box": True, "start": 0, "end": 5, "at": [1500, 760]},
    ]

    def _edge_contrast(self, out, rec, part=None):
        """A text colour against what surrounds its glyphs in the composited
        frame: glyph cores are the pixels within a small distance of the ink,
        and the background is the median luminance of a ring 2 to 4 px
        outside them. part: (ink, box), default the record's ink and box."""
        from PIL import ImageChops, ImageFilter
        ink, box = part or (rec["ink"], rec["box"])
        x0, y0, x1, y1 = (int(v) for v in box)
        crop = out.crop((x0 - 8, y0 - 8, x1 + 8, y1 + 8))
        raw = crop.convert("RGB").tobytes()
        px = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
        core = [1 if sum((a - b) ** 2 for a, b in zip(p, ink)) < 28 ** 2 else 0 for p in px]
        near = Image.new("L", crop.size)
        near.putdata([255 * v for v in core])
        inner = near.filter(ImageFilter.MaxFilter(3))
        outer = near.filter(ImageFilter.MaxFilter(9))
        ring = ImageChops.subtract(outer, inner)
        bg = sorted(O.luminance(p) for p, r in zip(px, ring.tobytes()) if r)
        self.assertGreater(sum(core), 20, f"{rec['kind']}: no glyph pixels in {ink}")
        self.assertTrue(bg, rec["kind"])
        return O.contrast(O.luminance(ink), bg[len(bg) // 2])

    def test_text_contrasts_with_bright_dark_and_mid_grey_frames(self):
        frames = {"bright": (205, 205, 208), "dark": (28, 28, 30), "mid grey": (120, 120, 121)}
        d = doc(frames=60, overlays=self.LEGIBLE, anchor=(600.0, 560.0, 1))
        for name, rgb in frames.items():
            report = []
            out = O.composite_frame(Image.new("RGB", (1920, 1080), rgb), d, 40, report=report)
            self.assertEqual(len(report), len(self.LEGIBLE), name)
            for rec in report:
                # every text colour (title and subtitle, equation terms and accents) in its own text
                self.assertGreaterEqual(len(rec["parts"]), 1)
                for part in rec["parts"]:
                    with self.subTest(frame=name, kind=rec["kind"], ink=part[0]):
                        self.assertGreaterEqual(self._edge_contrast(out, rec, part), O.MIN_CONTRAST)
            # on a plainly bright or dark frame the ink alone clears the bar, halo or not
            if name != "mid grey":
                for rec in report:
                    if rec["kind"] in ("label", "title", "equation"):
                        self.assertGreaterEqual(O.contrast(rec["ink"], rgb), O.MIN_CONTRAST, (name, rec))
            pols = {r["polarity"] for r in report if r["kind"] != "glow"}
            if name == "bright":
                self.assertEqual(pols, {"dark"})
            if name == "dark":
                self.assertEqual(pols, {"light"})

    def test_a_badge_fill_follows_the_frame_under_it(self):
        rec = {"kind": "glow", "text": "288 GB", "color": "green", "box": True, "anchor": "a", "start": 0, "end": 5}
        d = doc(frames=60, overlays=[rec])
        state = {}
        for rgb in ((15, 15, 16), (235, 235, 238)):  # first drawn on dark, then moved over bright
            rep = []
            out = O.composite_frame(Image.new("RGB", (1920, 1080), rgb), d, 30, state=state, report=rep)
            with self.subTest(frame=rgb):
                self.assertGreaterEqual(self._edge_contrast(out, rep[0]), O.MIN_CONTRAST)
        self.assertLess(O._badge_fill_alpha((201, 255, 215), 0.01), O._badge_fill_alpha((201, 255, 215), 0.85))

    def test_ink_is_kept_for_an_overlay_once_chosen(self):
        rec = {"kind": "label", "text": "Silicon", "anchor": "a", "start": 0, "end": 5, "side": "right"}
        d = doc(frames=60, overlays=[rec])
        state = {}
        rep = []
        O.composite_frame(Image.new("RGB", (1920, 1080), (220, 220, 222)), d, 30, state=state, report=rep)
        O.composite_frame(Image.new("RGB", (1920, 1080), (20, 20, 22)), d, 31, state=state, report=rep)
        self.assertEqual([r["polarity"] for r in rep], ["dark", "dark"])
        # and still readable on the dark frame, by its halo
        out = O.composite_frame(Image.new("RGB", (1920, 1080), (20, 20, 22)), d, 32, state=state, report=rep)
        self.assertGreaterEqual(self._edge_contrast(out, rep[-1]), O.MIN_CONTRAST)

    def test_state_file_carries_the_ink_across_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "in"), os.path.join(tmp, "out")
            os.makedirs(src)
            state = os.path.join(tmp, "state.json")
            d = doc(frames=60, overlays=[{"kind": "title", "text": "T", "start": 0, "end": 2}])
            with open(os.path.join(src, "shot.json"), "w") as fh:
                json.dump(d, fh)
            Image.new("RGB", (192, 108), (230, 230, 230)).save(os.path.join(src, "f_0010.png"))
            Image.new("RGB", (192, 108), (10, 10, 10)).save(os.path.join(src, "f_0040.png"))
            O.composite_dir(src, dst, first=1, last=30, state_file=state)
            O.composite_dir(src, dst, first=31, last=60, state_file=state)
            with open(state) as fh:
                self.assertEqual(list(json.load(fh).values()), ["dark"])
            # the dark frame got dark ink with a light halo, not white ink
            with Image.open(os.path.join(dst, "f_0040.png")) as im:
                self.assertGreater(max(im.convert("L").tobytes()), 60)

    def test_contrast_ratio_matches_wcag(self):
        self.assertAlmostEqual(O.contrast((255, 255, 255), (0, 0, 0)), 21.0, places=2)
        self.assertAlmostEqual(O.contrast((118, 118, 118), (255, 255, 255)), 4.54, places=2)
        self.assertLessEqual(O.luminance(O._deepen((96, 255, 150))), O.DEEP_LUM)


if __name__ == "__main__":
    unittest.main()
