"""Timing maths and word lookup (blenderkc.timing): pure Python."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blenderkc import timing as T  # noqa: E402

NARRATION = [
    {"scene": "X1_A", "title": "a", "hold": 0.5,
     "lines": ["High bandwidth memory stacks dies.", "Each stack has eight dies, and each die has vias."]},
    {"scene": "X2_B", "title": "b", "lines": ["Short."]},
]


class EstimateTest(unittest.TestCase):
    def test_slots_are_whole_frames_back_to_back(self):
        d = T.timing_from_narration(NARRATION, "X1_A")
        l0, l1 = d["lines"]
        clip0 = round(len(NARRATION[0]["lines"][0]) / 15.2, 3)
        self.assertAlmostEqual(l0["clip"], clip0)
        slot0 = -(-round((0.2 + clip0 + 0.4) * 30, 6) // 1)
        self.assertAlmostEqual(l0["end"], slot0 / 30, places=5)
        self.assertAlmostEqual(l1["start"], l0["end"], places=6)
        self.assertAlmostEqual(l0["voice"], 0.2, places=6)
        self.assertEqual(d["source"], "estimate")
        total = round(l1["end"] * 30) + round(0.5 * 30)
        self.assertEqual(d["frames"], total)
        self.assertAlmostEqual(d["duration"], total / 30)

    def test_words_cover_the_clip_in_order(self):
        ws = T.estimate_words("one two three", 3.0)
        self.assertEqual([w["word"] for w in ws], ["one", "two", "three"])
        starts = [w["start"] for w in ws]
        self.assertEqual(starts, sorted(starts))
        self.assertLessEqual(ws[-1]["end"], 3.0)
        self.assertEqual(T.estimate_words("", 1.0), [])

    def test_measured_words_replace_the_estimate(self):
        words = {"lines": [{"scene": "X2_B", "index": 0, "text": "Short.", "duration": 1.25,
                            "words": [{"word": "Short.", "start": 0.05, "end": 1.1}]}]}
        d = T.timing_from_narration(NARRATION, "X2_B", words=words)
        self.assertEqual(d["source"], "narration")
        self.assertEqual(d["lines"][0]["clip"], 1.25)
        self.assertAlmostEqual(T.Timing(d).at("short"), 0.2 + 0.05)

    def test_unknown_scene_names_the_scenes(self):
        with self.assertRaises(KeyError) as e:
            T.timing_from_narration(NARRATION, "Nope")
        self.assertIn("X1_A", str(e.exception))


class TimingTest(unittest.TestCase):
    def setUp(self):
        self.t = T.Timing(T.timing_from_narration(NARRATION, "X1_A"))

    def test_frames_and_times(self):
        self.assertEqual(self.t.frame(0), 1)
        self.assertEqual(self.t.frame(1.0), 31)
        self.assertEqual(self.t.time(31), 1.0)
        self.assertEqual(self.t.frame(1e6), self.t.frames)
        self.assertEqual(self.t.frame(-5), 1)

    def test_at_word_phrase_and_nth(self):
        l1 = self.t.line(1)
        each1 = self.t.at("each", line=1)
        each2 = self.t.at("each", line=1, nth=2)
        self.assertGreaterEqual(each1, l1.start)
        self.assertGreater(each2, each1)
        self.assertEqual(self.t.at("eight dies", line=1), self.t.at("eight", line=1))
        self.assertGreater(self.t.after("eight dies", line=1), self.t.at("dies", line=1))
        self.assertAlmostEqual(self.t.at("dies,", line=1), self.t.at("dies", line=1))  # punctuation ignored
        self.assertAlmostEqual(self.t.at("HIGH"), self.t.line(0).start)

    def test_unknown_word_raises_naming_the_line(self):
        with self.assertRaises(KeyError) as e:
            self.t.at("wafer", line=0)
        self.assertIn("High bandwidth", str(e.exception))
        with self.assertRaises(KeyError):
            self.t.at("each", line=1, nth=3)
        with self.assertRaises(IndexError):
            self.t.line(5)
        with self.assertRaises(ValueError):
            self.t.at("each", line=1, nth=0)

    def test_pipeline_file_without_frames_rounds_up(self):
        t = T.Timing({"scene": "s", "fps": 30, "duration": 2.01, "lines": []})
        self.assertEqual(t.frames, 61)
        self.assertEqual(T.Timing.fixed(2.0).frames, 60)

    def test_line_fields(self):
        d = {"scene": "s", "fps": 30, "frames": 90, "leadIn": 0.2, "lines": [
            {"index": 0, "text": "a b", "start": 0.0, "voice": 0.2, "clip": 1.0, "end": 1.6, "words": []}]}
        ln = T.Timing(d).line(0)
        self.assertEqual((ln.slot_start, ln.start, ln.end, ln.slot_end), (0.0, 0.2, 1.2, 1.6))
        self.assertEqual(len(ln.words), 2)  # estimated when the file has none


if __name__ == "__main__":
    unittest.main()
