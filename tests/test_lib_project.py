"""Anchor projection: camera-view coordinates to pixels, and (with bpy) the
whole path from a 3D point to shot.json."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blenderkc.project import to_pixels  # noqa: E402

try:
    import bpy  # noqa: F401
    HAVE_BPY = True
except ImportError:
    HAVE_BPY = False


class ToPixelsTest(unittest.TestCase):
    def test_centre_and_corners(self):
        self.assertEqual(to_pixels((0.5, 0.5, 3.0), 1920, 1080), [960.0, 540.0, 1])
        self.assertEqual(to_pixels((0.0, 1.0, 1.0), 1920, 1080), [0.0, 0.0, 1])  # top-left
        self.assertEqual(to_pixels((1.0, 0.0, 1.0), 1920, 1080), [1920.0, 1080.0, 1])

    def test_behind_or_outside_is_invisible(self):
        self.assertEqual(to_pixels((0.5, 0.5, -1.0), 1920, 1080)[2], 0)
        self.assertEqual(to_pixels((1.2, 0.5, 2.0), 1920, 1080)[2], 0)
        self.assertEqual(to_pixels((1.01, 0.5, 2.0), 1920, 1080)[2], 1)  # within the margin


@unittest.skipUnless(HAVE_BPY, "bpy not importable")
class CameraProjectionTest(unittest.TestCase):
    def test_look_point_lands_mid_frame_and_offsets_go_the_right_way(self):
        from bpy_extras.object_utils import world_to_camera_view
        from mathutils import Vector

        from blenderkc.shot import Shot
        from blenderkc.timing import Timing

        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
        shot = Shot(Timing.fixed(1.0))
        shot.camera.set(pos=(0, -10, 3), look=(0, 0, 0))
        scene.frame_set(1)
        shot.apply(0.0)
        bpy.context.view_layer.update()
        cam = scene.camera
        centre = to_pixels(tuple(world_to_camera_view(scene, cam, Vector((0, 0, 0)))), 1920, 1080)
        self.assertAlmostEqual(centre[0], 960, delta=0.5)
        self.assertAlmostEqual(centre[1], 540, delta=0.5)
        right = to_pixels(tuple(world_to_camera_view(scene, cam, Vector((1, 0, 0)))), 1920, 1080)
        up = to_pixels(tuple(world_to_camera_view(scene, cam, Vector((0, 0, 1)))), 1920, 1080)
        self.assertGreater(right[0], 960)
        self.assertLess(up[1], 540)


if __name__ == "__main__":
    unittest.main()
