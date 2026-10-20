"""Dies with TSVs and microbumps (blenderkc.kit.bumps), 3D dimension bars and
tint panes (blenderkc.kit.annotate), and the TSV section's extras."""
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
class BumpsTest(unittest.TestCase):
    def test_front_row_sits_on_the_cut_face(self):
        from blenderkc.kit import bumps

        fresh_shot()
        die = bumps.tsv_die("d", (0, 0, 0), (4.0, 2.4, 0.3))
        front = die.pillars[1]  # the halved row
        ys = {round(v.co.y, 4) for v in front.data.vertices}
        self.assertEqual(ys, {round(die.face - 0.002, 4)})
        back = {round(v.co.y, 4) for v in die.pillars[0].data.vertices}
        self.assertNotIn(round(die.face - 0.002, 4), back)  # no row twice

    def test_bumps_grow_from_hidden_and_slump(self):
        from blenderkc.kit import bumps

        shot = fresh_shot()
        die = bumps.tsv_die("d", (0, 0, 0), shown=False)
        die.grow_pillars(shot, 1.0, 1.5)
        die.grow_caps(shot, 2.0, 2.5)
        die.melt(shot, 3.0, 3.5)
        shot.apply(0.5)
        self.assertTrue(all(o.hide_render for o in die.pillars + die.caps))
        shot.apply(1.7)
        self.assertFalse(any(o.hide_render for o in die.pillars))
        self.assertTrue(all(o.hide_render for o in die.caps))
        shot.apply(3.9)
        self.assertAlmostEqual(die.caps[0].scale.z, 0.55, places=6)
        self.assertAlmostEqual(die.slump, die.cap_h * 0.45)
        die.melt(shot, 3.5, 3.8, squash=0.3)  # the drop follows the squash asked for
        self.assertAlmostEqual(die.slump, die.cap_h * 0.7)
        self.assertAlmostEqual(bumps.bump_gap(), die.gap)

    def test_bumps_only(self):
        from blenderkc.kit import bumps

        fresh_shot()
        d = bumps.tsv_die("u", (0, 0, 0.4), body=False)
        self.assertNotIn("die", d.objs)
        self.assertAlmostEqual(d.pillars[0].location.z, 0.0)


@unittest.skipUnless(HAVE_BPY, "bpy not importable")
class AnnotateTest(unittest.TestCase):
    def test_bar_draws_from_start_to_end(self):
        from blenderkc.kit import annotate

        shot = fresh_shot()
        bar = annotate.dimension(shot, "h", (1, 0, 2), (1, 0, -1), 1.0, 2.0, r=0.02, fade=3.0)
        shot.apply(0.5)
        self.assertTrue(bar["bar"].hide_render)
        shot.apply(2.5)
        bpy.context.view_layer.update()
        tip = bar["head1"].matrix_world.translation
        self.assertAlmostEqual(tip.z, -1 + bar["head1"].dimensions.z / 2, places=3)
        self.assertAlmostEqual(bar.point("middle").world().z, 0.5, places=4)
        shot.apply(3.5)
        self.assertTrue(bar["bar"].hide_render)

    def test_tint_fades_in(self):
        from blenderkc.kit import annotate

        shot = fresh_shot()
        pane = annotate.tint(shot, "t", (-1, -1), (1, 0), -0.5, 1.0, 2.0, alpha=0.4)
        fac = pane.active_material.node_tree.nodes["Mix Shader"].inputs[0]
        shot.apply(0.5)
        self.assertAlmostEqual(fac.default_value, 0.0)
        shot.apply(2.5)
        self.assertAlmostEqual(fac.default_value, 0.4)


@unittest.skipUnless(HAVE_BPY, "bpy not importable")
class TSVExtrasTest(unittest.TestCase):
    def test_bath_dip_submerges_the_wafer(self):
        from blenderkc.kit import tsv

        fresh_shot()
        bath = tsv.plating_bath("b", (0, 0, 0))
        h = bath["holder"]
        liquid = bath["liquid"]
        top = liquid.location.z + liquid.dimensions.z / 2
        r = bath["wafer"].dimensions.x / 2
        self.assertLess(h.location.z - bath.dip + r, top)  # all of it under the surface
        self.assertGreater(h.location.z - r, top)           # and all of it above, before

    def test_transistors_sit_on_the_surface(self):
        from blenderkc.kit import tsv

        shot = fresh_shot()
        sec = tsv.section(shot, "t", (0, 0, 1.0), size=(3.0, 1.2, 2.0), resist=False)
        gate, well = tsv.transistors(sec)
        gates = [o for o in bpy.data.objects if o.name.startswith("fet_gate")]
        self.assertEqual(len(gates), 8)
        self.assertTrue(all(o.parent is sec.root for o in gates))
        self.assertIn("Emission Strength", gate.node_tree.nodes["Principled BSDF"].inputs)


if __name__ == "__main__":
    unittest.main()
