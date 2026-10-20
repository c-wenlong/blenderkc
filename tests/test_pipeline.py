"""The verbs end to end on a synthetic checkout: the stand-in renderer and
overlay (tests/stub/blenderkc), the stand-in gnosia-audio, the real ffmpeg.
Skipped without ffmpeg. Nothing here touches the network or bpy."""

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import unittest.mock
import wave

from pathlib import Path

from helpers import HAS_FFMPEG, Sandbox


def red_at(video, frame0):
    """The red channel of the centre pixel of a 0-based frame."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{frame0}),scale=8:8",
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    return r.stdout[(4 * 8 + 4) * 3]


@unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
class Pipeline(unittest.TestCase):
    def setUp(self):
        self.s = Sandbox()
        self.t = self.s.topic()

    def tearDown(self):
        self.s.cleanup()

    def build(self, *extra, env=None):
        r = self.s.run("build", self.t, "--json", *extra, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_build_writes_a_checked_deliverable(self):
        out = self.build()
        self.assertFalse(out["draft"])
        self.assertEqual(out["frames"], 108)
        self.assertTrue(out["faststart"])
        kinds = {s["codec_type"]: s for s in out["streams"]}
        self.assertEqual((kinds["video"]["width"], kinds["video"]["height"], kinds["video"]["r_frame_rate"]), (1920, 1080, "30/1"))
        self.assertEqual(kinds["audio"]["codec_name"], "aac")
        self.assertEqual(kinds["audio"]["sample_rate"], "48000")
        for p in out["sidecars"].values():
            self.assertTrue((self.s.home / p).is_file(), p)
        rec = (self.s.home / "state" / "builds.jsonl").read_text().splitlines()
        self.assertEqual(len(rec), 1)
        # the frames are gone once the plates exist; the plates stay
        self.assertEqual(list((self.s.home / "frames").rglob("*.png")), [])
        self.assertEqual(len(list((self.s.home / "plates").rglob("*.mp4"))), 2)
        topics = json.loads(self.s.run("topics", "--json").stdout)
        d = topics["topics"][0]["sections"][0]["deliverable"]
        self.assertTrue(d["exists"])
        self.assertAlmostEqual(d["duration"], 3.6, delta=0.05)
        self.assertTrue(all(x["exists"] for x in d["sidecars"].values()))

    def test_a_second_build_renders_nothing(self):
        self.build()
        n = len(self.s.renders())
        out = self.build()
        self.assertEqual((out["rendered"], out["composited"]), ([], []))
        self.assertEqual(len(self.s.renders()), n)

    def test_an_overlay_edit_recomposites_without_3d(self):
        shot = self.s.root / "topics" / self.t / "shots" / "HT1_Hook.py"
        shot.write_text("def build(shot):\n    x = 1\n    shot.label('old', start=0, end=1)\n")
        self.build()
        shot.write_text("def build(shot):\n    x = 1\n    # a note\n    shot.label('new',\n               start=0, end=2)\n")
        before = len(self.s.renders())
        out = self.build()
        new = self.s.renders()[before:]
        self.assertEqual(out["rendered"], [])
        self.assertEqual(out["composited"], ["HT1_Hook"])
        self.assertEqual(new, ["HT1_Hook 1 60 overlays 100 16"])

    def test_a_3d_edit_renders_that_scene_only(self):
        self.build()
        shot = self.s.root / "topics" / self.t / "shots" / "HT2_Wires.py"
        shot.write_text("def build(shot):\n    return 'a sphere now'\n")
        out = self.build()
        self.assertEqual(out["rendered"], ["HT2_Wires"])
        self.assertEqual(out["composited"], ["HT2_Wires"])

    def test_chunked_compositing_keeps_every_frame_in_place(self):
        shot = self.s.root / "topics" / self.t / "shots" / "HT1_Hook.py"
        shot.write_text("def build(shot):\n    shot.label('x', start=0, end=1)\n")
        out = self.build(env={"BLENDERKC_COMPOSE_CHUNK": "7"})
        video = out["path"]
        # the stand-in paints frame n (1-based, scene-relative) with red = 7n mod 256. The H.264
        # limited-range YUV round-trip shifts saturated primaries — by ~13 on some ffmpeg builds —
        # so the tolerance is wide; a dropped or repeated chunk is off by a multiple of 7*7=49 and
        # still fails.
        for f0 in (0, 6, 7, 8, 13, 14, 30, 59):
            self.assertAlmostEqual(red_at(video, f0), (7 * (f0 + 1)) % 256, delta=24, msg=f"frame {f0}")
        self.assertAlmostEqual(red_at(video, 60), 7, delta=24)  # the second scene starts again at frame 1

    def test_narration_sits_at_its_frames(self):
        from blenderkc_cli import media, timeline
        from blenderkc_cli.narrate import read_narration
        self.build()
        repo = self.s.repo()
        tl = timeline.from_disk(repo, self.t, read_narration(self.s.root / "topics" / self.t))
        wav = self.s.tmp / "n.wav"
        media.narration_track(tl, repo.public, wav, self.s.tmp / "work")
        with wave.open(str(wav)) as w:
            rate = w.getframerate()
            self.assertEqual(w.getnframes(), round(108 / 30 * rate))
            data = w.readframes(w.getnframes())
        first = next(i for i in range(0, len(data), 2) if data[i:i + 2] != b"\0\0") // 2
        self.assertEqual(first, round(6 / 30 * rate))  # lead-in 0.2 s = 6 frames
        line2 = tl["scenes"][0]["lines"][1]["audioFrom"]
        self.assertNotEqual(data[line2 * rate // 30 * 2:line2 * rate // 30 * 2 + 2], b"\0\0")

    def test_no_narrate_then_sidecars(self):
        self.assertEqual(self.s.run("narrate", self.t).returncode, 0)
        out = self.build("--no-narrate")
        self.assertIsNone(out["sidecars"])
        self.assertTrue((self.s.home / out["timeline"]).is_file())
        r = self.s.run("sidecars", self.t, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        for p in json.loads(r.stdout)["sidecars"].values():
            self.assertTrue((self.s.home / p).is_file())

    def test_no_narrate_refuses_stale_narration(self):
        self.assertEqual(self.s.run("narrate", self.t).returncode, 0)
        n = self.s.root / "topics" / self.t / "narration.json"
        n.write_text(n.read_text().replace("Two lines here.", "Two lines, changed."))
        r = self.s.run("build", self.t, "--no-narrate")
        self.assertEqual(r.returncode, 2)
        self.assertIn("stale", r.stderr)

    def test_reuse_plates_never_takes_a_drafts_plate(self):
        self.build("--res", "50")
        shot = self.s.root / "topics" / self.t / "shots" / "HT1_Hook.py"
        shot.write_text("def build(shot):\n    return 'changed'\n")
        out = self.build("--reuse-plates")
        self.assertIn("HT1_Hook", out["rendered"])
        self.assertEqual(out["reusedPlates"], [])
        self.assertEqual(next(s for s in out["streams"] if s["codec_type"] == "video")["width"], 1920)
        shot.write_text("def build(shot):\n    return 'changed again'\n")
        out = self.build("--reuse-plates")
        self.assertEqual(out["reusedPlates"], ["HT1_Hook"])
        self.assertEqual(out["rendered"], [])

    def test_the_build_assembles_from_its_own_clip_links(self):
        from blenderkc_cli import build as build_mod, media, timeline
        from blenderkc_cli.narrate import read_narration
        self.assertEqual(self.s.run("narrate", self.t).returncode, 0)
        repo = self.s.repo()
        tl = timeline.from_disk(repo, self.t, read_narration(self.s.root / "topics" / self.t))
        snap = build_mod.snapshot_clips(repo, tl, self.s.tmp / "snap")
        for f in (repo.public / self.t / "audio").iterdir():
            f.unlink()  # a narrate run meanwhile removed them
        info = media.narration_track(tl, snap, self.s.tmp / "n.wav", self.s.tmp / "work")
        self.assertAlmostEqual(info["seconds"], 108 / 30, places=3)

    def test_an_odd_draft_size_encodes(self):
        shot = self.s.root / "topics" / self.t / "shots" / "HT2_Wires.py"
        shot.write_text("def build(shot):\n    shot.label('x', start=0, end=1)\n")
        out = self.build("--res", "41")  # 787x442: odd width
        self.assertTrue(out["draft"])
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", out["path"]], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "786,442")

    def test_scenes_makes_a_draft_and_leaves_the_deliverable(self):
        out = self.build("--scenes", "HT2_Wires")
        self.assertTrue(out["draft"])
        self.assertTrue(out["path"].endswith("hbm-test.draft.mp4"))
        self.assertEqual(out["frames"], 48)
        self.assertFalse((self.s.home / "out" / self.t / "hbm-test.mp4").exists())

    def test_render_failure_is_reported_and_resumes(self):
        r = self.s.run("build", self.t, env={"STUB_RENDER_FAIL": "HT2_Wires"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("HT2_Wires", r.stderr)
        out = self.build()
        self.assertEqual(out["rendered"], ["HT2_Wires"])  # HT1's plate survived the failure

    def test_still_with_estimate_and_moments(self):
        r = self.s.run("still", self.t, "--timing", "estimate", "HT2_Wires:0.5s", "HT1_Hook:3", "1s", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["timing"], "estimate")
        self.assertEqual([(x["scene"], x["frame"]) for x in out["stills"]], [("HT1_Hook", 3), ("HT1_Hook", 31), ("HT2_Wires", 16)])
        self.assertTrue(all(l.endswith("50 4") for l in self.s.renders()))
        self.assertTrue(out["sheet"])

    def test_still_runs_do_not_delete_each_other(self):
        first = json.loads(self.s.run("still", self.t, "--timing", "estimate", "HT1_Hook:3", "--json").stdout)
        second = json.loads(self.s.run("still", self.t, "--timing", "estimate", "HT2_Wires:0.5s", "--json").stdout)
        self.assertNotEqual(first["run"], second["run"])
        for out in (first, second):
            self.assertTrue(all(Path(x["file"]).is_file() for x in out["stills"]), out["run"])
            self.assertTrue(Path(out["sheet"]).is_file())
        latest = self.s.home / "stills" / self.t / "latest"
        self.assertEqual(os.readlink(latest), second["run"])
        self.assertFalse((self.s.home / "cache" / "stills" / self.t / first["run"]).exists())

    def test_still_runs_that_overlap_keep_both(self):
        cmd = [sys.executable, str(self.s.root / "tools" / "cli" / "blenderkc"), "--root", str(self.s.root), "still", self.t,
               "--timing", "estimate", "--json"]
        env = {**self.s.env, "STUB_RENDER_SLEEP": "1.5"}
        procs = [subprocess.Popen(cmd + [m], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                 for m in ("HT1_Hook:3", "HT2_Wires:0.5s")]
        outs = []
        for p in procs:
            stdout, stderr = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, stderr)
            outs.append(json.loads(stdout))
        self.assertNotEqual(outs[0]["run"], outs[1]["run"])
        for out in outs:
            self.assertTrue(all(Path(x["file"]).is_file() for x in out["stills"]), out["run"])
            self.assertTrue(Path(out["sheet"]).is_file())
        self.assertEqual(list((self.s.home / "cache" / "stills" / self.t).iterdir()), [])

    def test_pruning_skips_a_folder_another_run_removed(self):
        from blenderkc_cli import still
        d = self.s.tmp / "stills-race"
        d.mkdir()
        old = time.time() - 2 * 24 * 3600
        for i in range(12):
            r = d / f"20260101T0000{i:02d}-{100 + i}"
            r.mkdir()
            os.utime(r, (old + i, old + i))
        real_stat = Path.stat
        gone = d / "20260101T000000-100"

        def racing_stat(self, *a, **k):
            if self == gone and os.path.exists(gone):  # not Path.exists, which calls this stat on older Pythons
                shutil.rmtree(gone)  # the other run got there first
            return real_stat(self, *a, **k)
        with unittest.mock.patch.object(Path, "stat", racing_stat):
            still.prune_runs(d, keep="none")
        self.assertEqual(len(list(d.iterdir())), still.KEEP_RUNS)

    def test_old_still_runs_are_pruned_and_new_ones_kept(self):
        from blenderkc_cli import still
        d = self.s.tmp / "stills-prune"
        d.mkdir()
        old = time.time() - 2 * 24 * 3600
        for i in range(14):
            r = d / f"20260101T0000{i:02d}-{100 + i}"
            r.mkdir()
            os.utime(r, (old + i, old + i))
        fresh = d / "20261009T120000-999"
        fresh.mkdir()
        still.prune_runs(d, keep=fresh.name)
        left = sorted(p.name for p in d.iterdir())
        self.assertIn(fresh.name, left)
        self.assertEqual(len(left), still.KEEP_RUNS)

    def test_new_scaffolds_a_topic_that_stills(self):
        r = self.s.run("new", "computer-architecture/memory/dram-cells", "--topic", "dram", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        made = json.loads(r.stdout)
        self.assertEqual(made["prefix"], "DC")
        self.assertEqual(made["scenes"], ["DC1_Intro", "DC2_Idea", "DC3_Close"])
        again = self.s.run("new", "computer-architecture/memory/dram-cells", "--topic", "dram")
        self.assertEqual(again.returncode, 2)
        bad = self.s.run("new", "computer-architecture/memory/sram", "--topic", "sram", "--scenes", "memory.cells")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("would not be a scene name", bad.stderr)
        self.assertFalse((self.s.root / "topics" / "computer-architecture/memory/sram").exists())
        r = self.s.run("still", "dram-cells", "--timing", "estimate", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_render_job_from_stdin(self):
        """What build --host runs on the host, here: the job on stdin."""
        r = self.s.run("narrate", self.t)
        self.assertEqual(r.returncode, 0)
        job = {"schema": 1, "topic": self.t, "shots": "shots/", "fps": 30,
               "settings": {"res": 10, "samples": 1, "device": "CPU"}, "keepFrames": False,
               "scenes": [{"scene": "HT2_Wires", "frames": 12, "plate": "ab" * 32,
                           "timing": {"scene": "HT2_Wires", "fps": 30, "frames": 12, "duration": 0.4, "lines": []}}]}
        r = self.s.run("render-job", "--job", "-", input=json.dumps(job))
        self.assertEqual(r.returncode, 0, r.stderr)
        reply = json.loads(r.stdout)
        self.assertEqual(reply["plates"][0]["plate"], f"plates/{self.t}/HT2_Wires.{'ab' * 8}.mp4")
        self.assertFalse(reply["plates"][0]["cached"])
        r = self.s.run("render-job", "--job", "-", input=json.dumps(job))
        self.assertTrue(json.loads(r.stdout)["plates"][0]["cached"])

    def test_doctor_names_the_library(self):
        shutil.rmtree(self.s.root / "blenderkc")
        r = self.s.run("doctor", "--json")
        lib = next(c for c in json.loads(r.stdout)["checks"] if c["name"] == "library")
        self.assertEqual(lib["status"], "fail")


if __name__ == "__main__":
    unittest.main()
