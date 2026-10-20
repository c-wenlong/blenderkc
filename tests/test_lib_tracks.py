"""Easing and tracks (blenderkc.ease, blenderkc.tracks): pure Python."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blenderkc import ease as E  # noqa: E402
from blenderkc.tracks import Tracks, hermite, split_path  # noqa: E402


class Thing:
    def __init__(self):
        self.location = [0.0, 0.0, 0.0]
        self.value = 1.0


class EaseTest(unittest.TestCase):
    def test_every_ease_runs_zero_to_one(self):
        for name, f in E.EASES.items():
            if name == "step":
                continue
            self.assertAlmostEqual(f(0.0), 0.0, places=6, msg=name)
            self.assertAlmostEqual(f(1.0), 1.0, places=6, msg=name)
        self.assertAlmostEqual(E.ease_in_out(0.5), 0.5)
        self.assertEqual(E.progress(5, 0, 10, "linear"), 0.5)
        self.assertEqual(E.progress(-1, 0, 10), 0.0)
        with self.assertRaises(ValueError):
            E.get("bouncy")


class TracksTest(unittest.TestCase):
    def test_tween_holds_before_and_after(self):
        tr, o = Tracks(), Thing()
        tr.tween(o, "value", 3.0, 1.0, 2.0, "linear")
        for t, want in ((0.0, 1.0), (1.0, 1.0), (1.5, 2.0), (2.0, 3.0), (9.0, 3.0)):
            tr.apply(t)
            self.assertAlmostEqual(o.value, want)

    def test_chained_tweens_start_where_the_last_ended(self):
        tr, o = Tracks(), Thing()
        tr.tween(o, "location", (1, 0, 0), 0, 1, "linear")
        tr.tween(o, "location", (1, 2, 0), 2, 3, "linear")
        tr.apply(1.5)
        self.assertEqual(tuple(o.location), (1, 0, 0))
        tr.apply(2.5)
        self.assertEqual(tuple(o.location), (1, 1, 0))

    def test_component_paths(self):
        self.assertEqual(split_path("location.z"), ("location", 2))
        self.assertEqual(split_path("location[1]"), ("location", 1))
        self.assertEqual(split_path("default_value"), ("default_value", None))
        tr, o = Tracks(), Thing()
        tr.tween(o, "location.z", 4.0, 0, 1, "linear")
        tr.apply(0.5)
        self.assertEqual(o.location, [0.0, 0.0, 2.0])

    def test_set_steps(self):
        tr, o = Tracks(), Thing()
        tr.set(o, "value", 7.0, at=2.0)
        tr.apply(1.99)
        self.assertEqual(o.value, 1.0)
        tr.apply(2.0)
        self.assertEqual(o.value, 7.0)

    def test_spline_passes_through_keys_and_is_flat_at_the_ends(self):
        keys = [(0.0, (0.0, 0.0)), (1.0, (2.0, 1.0)), (3.0, (2.0, 4.0))]
        for t, v in keys:
            self.assertEqual(hermite(keys, t), v)
        a, b = hermite(keys, 0.001), hermite(keys, 0.0)
        self.assertLess(abs(a[0] - b[0]), 1e-4)  # zero velocity at the start
        # x turns at the middle key (0 -> 2 -> 2): flat there, no overshoot
        self.assertLessEqual(max(hermite(keys, t / 100)[0] for t in range(301)), 2.0 + 1e-9)
        tr, o = Tracks(), Thing()
        tr.spline(o, "value", [(0, 0.0), (1, 5.0), (2, 6.0)])
        tr.apply(1.0)
        self.assertAlmostEqual(o.value, 5.0)

    def test_spline_never_overshoots_uneven_keys(self):
        keys = [(0.0, 0.0), (1.0, 100.0), (2.0, 101.0)]
        peak = max(hermite(keys, t / 1000) for t in range(2001))
        self.assertLessEqual(peak, 101.0 + 1e-9)
        self.assertEqual(hermite(keys, 1.0), 100.0)

    def test_spline_repeated_times_keep_the_last_value(self):
        tr, o = Tracks(), Thing()
        tr.spline(o, "value", [(0, 0.0), (1, 100.0), (1, 101.0), (2, 101.0)])
        for t in (0.5, 1.0, 1.5):
            tr.apply(t)
        tr.apply(1.0)
        self.assertEqual(o.value, 101.0)

    def test_callbacks_run_after_tracks(self):
        tr, o = Tracks(), Thing()
        tr.tween(o, "value", 3.0, 0, 1, "linear")
        seen = []
        tr.callbacks.append(lambda t: seen.append((t, o.value)))
        tr.apply(1.0)
        self.assertEqual(seen, [(1.0, 3.0)])


if __name__ == "__main__":
    unittest.main()
