"""The TSV cross-section (blenderkc.kit.tsv): the hole profile's geometry, and
the section's layers following its state frame by frame."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import bpy
    HAVE_BPY = True
except ImportError:
    HAVE_BPY = False


def fresh_shot(seconds=4.0):
    from blenderkc.shot import Shot
    from blenderkc.timing import Timing

    bpy.ops.wm.read_factory_settings(use_empty=True)
    return Shot(Timing.fixed(seconds))


@unittest.skipUnless(HAVE_BPY, "bpy not importable")
class ProfileTest(unittest.TestCase):
    def test_no_hole_before_etching(self):
        from blenderkc.kit import tsv

        self.assertEqual(tsv.profile(0.0, 0.025, 0.08, 0.07), [])

    def test_first_scoop_undercuts_and_ends_on_the_axis(self):
        from blenderkc.kit import tsv

        p = tsv.profile(1.0, 0.025, 0.08, 0.07)
        self.assertAlmostEqual(p[0][1], 0.0)
        self.assertAlmostEqual(p[0][0], 0.105, places=6)  # core + rho at the surface
        self.assertAlmostEqual(p[-1][0], 0.0)
        self.assertAlmostEqual(p[-1][1], -0.08, places=6)  # one rho deep
        zs = [z for _, z in p]
        self.assertEqual(zs, sorted(zs, reverse=True))  # down the wall, never back up

    def test_scoops_add_depth_and_leave_ridges(self):
        from blenderkc.kit import tsv

        sc = tsv.scoops(3.0, 0.025, 0.08, 0.07)
        self.assertEqual(len(sc), 3)
        p = tsv.profile(3.0, 0.025, 0.08, 0.07)
        self.assertAlmostEqual(p[-1][1], -(2 * 0.07 + 0.08), places=6)
        ridge = tsv.wall_radius(-0.035, sc, 0.025)  # where scoops 0 and 1 meet
        self.assertLess(ridge, 0.1)
        self.assertGreater(ridge, 0.09)
        self.assertAlmostEqual(tsv.wall_radius(-0.07, sc, 0.025), 0.105, places=6)  # widest at a centre

    def test_half_grown_scoop_is_shallower(self):
        from blenderkc.kit import tsv

        full = tsv.profile(2.0, 0.025, 0.08, 0.07)[-1][1]
        half = tsv.profile(1.5, 0.025, 0.08, 0.07)[-1][1]
        self.assertGreater(half, full)

    def test_clip_ends_on_the_plane(self):
        from blenderkc.kit import tsv

        p = tsv.profile(5.0, 0.025, 0.08, 0.07)
        c = tsv.clip_profile(p, 0.1)
        self.assertAlmostEqual(c[-1][1], -0.1, places=6)
        self.assertTrue(all(z >= -0.1 - 1e-9 for _, z in c))

    def test_shell_faces_clip_together_flat(self):
        from blenderkc.kit import tsv

        p = tsv.profile(1.0, 0.025, 0.08, 0.07)
        nrm = tsv.normals(p)
        outer = tsv.offset(p, nrm, [0.0] * len(p))
        inner = tsv.offset(p, nrm, [0.013] * len(p))  # a full liner: its floor sits higher
        a, b = tsv.clip_together([outer, inner], 0.075)
        self.assertEqual(len(a), len(b))
        self.assertAlmostEqual(a[-1][1], -0.075, places=9)
        self.assertAlmostEqual(b[-1][1], -0.075, places=9)  # a flat cut, not a slope
        self.assertTrue(all(z >= -0.075 - 1e-9 for _, z in a + b))


@unittest.skipUnless(HAVE_BPY, "bpy not importable")
class SectionTest(unittest.TestCase):
    def test_layers_follow_the_state(self):
        from blenderkc.kit import tsv

        shot = fresh_shot()
        sec = tsv.section(shot, "t", (0, 0, 1.0), size=(2.0, 1.0, 3.0))
        shot.set(sec.s, "etch", 6.0, 0.0)
        sec.coat(shot, 0.5, 1.0)
        sec.punch(shot, 1.0, 1.5)
        sec.strip_polymer(shot, 1.6, 1.8)
        sec.deposit(shot, "liner", 2.0, 2.2)
        sec.plate(shot, 3.0, 3.5)

        def shown(lay):
            return not lay.objs[0].hide_render

        shot.apply(0.2)
        self.assertFalse(shown(sec.layers["polymer"]))
        self.assertTrue(sec.mod.show_render)  # the hole is cut
        shot.apply(1.2)
        self.assertTrue(shown(sec.layers["polymer"]))
        shot.apply(2.5)
        self.assertFalse(shown(sec.layers["polymer"]))
        self.assertTrue(shown(sec.layers["liner"]))
        self.assertFalse(shown(sec.copper))
        shot.apply(3.9)
        self.assertTrue(shown(sec.copper))
        open_below_top = [v for v in sec.copper.mesh.vertices if v.co.y > 1e-4 and v.co.z < -0.01]
        self.assertEqual(open_below_top, [])  # full: only the top cap reaches behind the face

    def test_floor_point_moves_with_the_etch(self):
        from blenderkc.kit import tsv

        shot = fresh_shot()
        sec = tsv.section(shot, "t", (0, 0, 1.0), size=(2.0, 1.0, 3.0))
        sec.etch_to(shot, 4.0, 0.0, 2.0, ease="linear")
        shot.apply(1.0)
        z1 = sec.floor_pt.location.z
        shot.apply(2.0)
        self.assertLess(sec.floor_pt.location.z, z1)
        self.assertAlmostEqual(sec.bottom_at(shot, 2.0), 3 * 0.07 + 0.08, places=6)
        shot.apply(0.0)  # back before any etching: the point is on the surface again
        self.assertAlmostEqual(sec.floor_pt.location.z, 0.0)

    def test_each_particle_call_keeps_its_look(self):
        from blenderkc.kit import tsv

        shot = fresh_shot()
        sec = tsv.section(shot, "t", (0, 0, 1.0), size=(2.0, 1.0, 3.0))
        a = tsv.ion_rain(shot, sec, 0.0, 1.0, color=(1, 0, 0))
        b = tsv.ion_rain(shot, sec, 0.0, 1.0, color=(0, 0, 1))
        self.assertNotEqual(a.name, b.name)
        d1 = tsv.debris(shot, (0, 0, 0), (1, 1), 0.0, 1.0, r=0.01)
        d2 = tsv.debris(shot, (0, 0, 0), (1, 1), 0.0, 1.0, r=0.02)
        self.assertNotEqual(d1.name, d2.name)

    def test_ions_stop_on_closed_resist_and_hide_with_the_section(self):
        from blenderkc.kit import tsv

        shot = fresh_shot()
        sec = tsv.section(shot, "t", (0, 0, 1.0), size=(2.0, 1.0, 3.0), opened=False)
        shot.set(sec.s, "etch", 3.0, 0.0)
        ions = tsv.ion_rain(shot, sec, 0.0, 4.0, count=30, spread=0.1, height=0.5)
        sec.hide(shot, 2.0)
        shot.apply(1.0)
        zs = [v.co.z for v in ions.data.vertices]
        self.assertGreaterEqual(min(zs), sec.resist_t - 1e-6)  # nothing falls through closed resist
        shot.apply(2.5)
        self.assertTrue(all(v.co.z > 100 for v in ions.data.vertices))

    def test_hide_and_resist_strip(self):
        from blenderkc.kit import tsv

        shot = fresh_shot()
        sec = tsv.section(shot, "t", (0, 0, 1.0), size=(2.0, 1.0, 3.0))
        shot.set(sec.s, "etch", 2.0, 0.0)
        sec.strip_resist(shot, 1.0, 1.5)
        sec.hide(shot, 2.0, 3.0)
        shot.apply(0.5)
        self.assertFalse(sec.resist.hide_render)
        shot.apply(1.6)
        self.assertTrue(sec.resist.hide_render)
        shot.apply(2.5)
        self.assertTrue(sec.block.hide_render)
        shot.apply(3.5)
        self.assertFalse(sec.block.hide_render)
        self.assertTrue(sec.resist.hide_render)


if __name__ == "__main__":
    unittest.main()
