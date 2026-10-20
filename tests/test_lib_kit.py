"""Kit behaviour that needs bpy: the camera following a moving part, and the
wafer section's per-hole openings and thinning."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import bpy
    HAVE_BPY = True
except ImportError:
    HAVE_BPY = False


def fresh_shot(seconds=2.0):
    from blenderkc.shot import Shot
    from blenderkc.timing import Timing

    bpy.ops.wm.read_factory_settings(use_empty=True)
    return Shot(Timing.fixed(seconds))


def at(shot, t):
    bpy.context.scene.frame_set(shot.frame(t))
    shot.apply(t)
    bpy.context.view_layer.update()


@unittest.skipUnless(HAVE_BPY, "bpy not importable")
class KitTest(unittest.TestCase):
    def test_follow_keeps_a_moving_part_in_view(self):
        from blenderkc.kit import geo

        shot = fresh_shot()
        part = geo.Part("mover")
        shot.tween(part.root, "location.x", 3.0, 0.0, 2.0, "linear")
        shot.camera.set(pos=(0, -10, 3), look=(0, 0, 0))
        shot.camera.follow(0.0, None, part, blend=0.0)
        at(shot, 1.0)
        look = shot.camera.target.matrix_world.translation
        self.assertAlmostEqual(look.x, 1.5, places=3)
        focus = shot.camera.focus.matrix_world.translation
        self.assertAlmostEqual(focus.x, 1.5, places=3)

    def test_short_follow_ends_and_moves_see_the_followed_look(self):
        from blenderkc.kit import geo

        shot = fresh_shot(4.0)
        part = geo.Part("mover", loc=(3, 0, 0))
        shot.camera.set(pos=(0, -10, 0), look=(0, 0, 0))
        shot.camera.follow(0.5, 1.0, part)  # shorter than two default blends
        at(shot, 0.75)
        self.assertAlmostEqual(shot.camera.target.matrix_world.translation.x, 3.0, places=3)
        at(shot, 2.0)
        self.assertAlmostEqual(shot.camera.target.matrix_world.translation.x, 0.0, places=3)
        self.assertAlmostEqual(shot.camera.look_at_time(0.75).x, 3.0, places=3)
        shot.camera.push_in(0.75, 0.8, 0.5)  # halfway toward the followed point
        self.assertAlmostEqual(shot.camera.pos_at(0.8).x, 1.5, places=3)

    def test_camera_queries_leave_the_scene_as_it_was_and_do_not_recurse(self):
        from blenderkc.kit import geo

        shot = fresh_shot(4.0)
        part = geo.Part("mover")
        shot.tween(part.root, "location.x", 4.0, 0.0, 4.0, "linear")
        shot.camera.set(pos=(0, -10, 0), look=(0, 0, 0))
        shot.camera.follow(0.0, None, part, blend=0.0)
        seen = []
        shot.on_frame(lambda t: seen.append(shot.camera.look_at_time(t).x))
        at(shot, 2.0)  # the callback asks the camera mid-frame: no recursion
        self.assertEqual(len(seen), 1)  # and it ran once for the frame
        self.assertAlmostEqual(seen[-1], 2.0, places=3)
        self.assertAlmostEqual(part.root.location.x, 2.0, places=6)
        shot.camera.focus_at(3.0)  # a query does not move the part
        self.assertAlmostEqual(part.root.location.x, 2.0, places=6)

    def test_beam_groups_get_their_own_material(self):
        from blenderkc.kit import effects

        shot = fresh_shot()
        a = effects.beams(shot, [(0, 0)], 0.0, 1.0)
        bpy.data.materials.new("uv_beam5")
        b = effects.beams(shot, [(1, 0)], 0.5, 1.5)
        c = effects.beams(shot, [(2, 0)], 0.5, 1.5)
        self.assertEqual(len({a.name, b.name, c.name}), 3)

    def test_wafer_openings_sit_on_uneven_holes_and_thinning_is_per_hole(self):
        from blenderkc.kit import parts

        shot = fresh_shot()
        w = parts.wafer_section("w", holes=(-1.0, 0.0, 0.5), scallops=4, depth=0.8)
        openers = w.resist.modifiers["open"].collection.objects
        self.assertEqual(sorted(round(o.location.x, 3) for o in openers), [-1.0, 0.0, 0.5])
        w.fill(shot, 0.1, 0.4)
        w.thin(shot, 0.5, 1.0)
        at(shot, 1.5)
        hidden = [p.hide_render for p in w.plugs]
        self.assertEqual(hidden, [False, False, False, True] * 3)
        self.assertAlmostEqual(w.block.scale.z, (3 + 0.1) * 0.2 / 1.6, places=4)


    def _world_box(self, o):
        from mathutils import Vector

        cs = [o.matrix_world @ Vector(c) for c in o.bound_box]
        return [min(c[i] for c in cs) for i in range(3)], [max(c[i] for c in cs) for i in range(3)]

    def test_server_board_stacks_clear_the_gpu_dies(self):
        import re

        from blenderkc.kit import parts

        fresh_shot()
        parts.server_board()
        bpy.context.view_layer.update()
        objs = bpy.data.objects
        dies = [o for o in objs if re.search(r"_gpu\d+_gpu\d+$", o.name)]
        stacks = [o for o in objs if o.type == "MESH" and re.search(r"_hbm\d+_|_mould\d+$", o.name)]
        self.assertEqual(len(dies), 8)
        self.assertGreater(len(stacks), 32)
        for d in dies:
            (a0, a1) = self._world_box(d)
            for s in stacks:
                (b0, b1) = self._world_box(s)
                hit = all(a0[i] < b1[i] - 1e-4 and b0[i] < a1[i] - 1e-4 for i in range(3))
                self.assertFalse(hit, f"{s.name} reaches into {d.name}")

    def test_gpu_package_refuses_stacks_inside_its_dies(self):
        from blenderkc.kit import parts

        fresh_shot()
        with self.assertRaises(ValueError):
            parts.gpu_package("too_close", dies=2, die_size=(3.0, 5.2), stacks_per_side=4)
        parts.gpu_package("default")  # the reference package still builds

    def test_grey_studio_keeps_its_rim_light_off_the_backdrop(self):
        from blenderkc.kit import process, rigs

        fresh_shot()
        r = rigs.grey_studio(None, scale=0.4)
        process.no_backdrop_glint(r)  # the scripts that already ask for it: no second collection
        coll = r.lights["rim"].light_linking.receiver_collection
        self.assertEqual([o.name for o in coll.objects], [r.floor.name])
        self.assertEqual([i.light_linking.link_state for i in coll.collection_objects], ["EXCLUDE"])
        old = rigs.grey_studio(None, scale=0.4, rim_on_backdrop=True)
        self.assertIsNone(old.lights["rim"].light_linking.receiver_collection)
        # a light already linked to a subject keeps that link; only the backdrop's entry changes
        from blenderkc.kit import geo
        subject = geo.box("subject", (1, 1, 1), (0, 0, 0.5), None, 0)
        own = bpy.data.collections.new("rim_subjects")
        own.objects.link(subject)
        old.lights["rim"].light_linking.receiver_collection = own
        rigs.keep_off_backdrop(old, "rim")
        states = dict(zip([o.name for o in own.objects], [i.light_linking.link_state for i in own.collection_objects]))
        self.assertEqual(states, {"subject": "INCLUDE", old.floor.name: "EXCLUDE"})

if __name__ == "__main__":
    unittest.main()
