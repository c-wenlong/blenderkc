"""Smoke render: python -m blenderkc.render on a tiny scene (needs bpy)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOPIC = os.path.join(ROOT, "tests", "fixtures", "smoke")

try:
    import bpy  # noqa: F401
    HAVE_BPY = True
except ImportError:
    HAVE_BPY = False


def load(path):
    with open(path) as f:
        return json.load(f)


def run(*args):
    return subprocess.run([sys.executable, "-m", "blenderkc.render", "--topic", TOPIC, "--scene", "SM1_Cube", *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=600)


@unittest.skipUnless(HAVE_BPY, "bpy not importable")
class RenderSmokeTest(unittest.TestCase):
    def test_one_tiny_frame_then_resume_skips_it(self):
        with tempfile.TemporaryDirectory() as out:
            r = run("--frames", "1", "1", "--out", out, "--res", "5", "--samples", "1", "--device", "CPU")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:] + r.stdout[-2000:])
            png = os.path.join(out, "f_0001.png")
            self.assertTrue(os.path.getsize(png) > 0)
            doc = load(os.path.join(out, "shot.json"))
            self.assertEqual((doc["width"], doc["height"], doc["fps"]), (1920, 1080, 30))
            x, y, vis = doc["anchors"]["a_a_cube"][0]
            self.assertEqual(vis, 1)
            self.assertAlmostEqual(x, 960, delta=60)  # the camera looks at the cube
            self.assertEqual(doc["overlays"][0]["kind"], "label")
            r2 = run("--frames", "1", "1", "--out", out, "--res", "5", "--samples", "1", "--device", "CPU")
            self.assertIn("skipped 1", r2.stdout)

    def test_overlays_only_projects_every_frame(self):
        with tempfile.TemporaryDirectory() as out:
            r = run("--out", out, "--overlays-only")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            doc = load(os.path.join(out, "shot.json"))
            track = doc["anchors"]["a_a_cube"]
            self.assertEqual(len(track), doc["frames"])
            self.assertTrue(all(p is not None for p in track))
            self.assertNotEqual(track[0][:2], track[-1][:2])  # the orbit moves it on screen
            self.assertEqual([f for f in os.listdir(out) if f.endswith(".png")], [])


if __name__ == "__main__":
    unittest.main()
