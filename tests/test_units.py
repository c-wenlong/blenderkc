"""The pure pieces: the YAML subset, word alignment, the timing estimate and
layout, the cache keys, and the rsync and ssh commands build --host runs."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "cli"))

from blenderkc_cli import keys, remote, timeline  # noqa: E402
from blenderkc_cli.__main__ import parse_args  # noqa: E402
from blenderkc_cli.align import time_words  # noqa: E402
from blenderkc_cli.repo import Repo  # noqa: E402
from blenderkc_cli.yaml import YamlError, parse_yaml  # noqa: E402


class Parser(unittest.TestCase):
    def test_still_moments_survive_options_on_old_argparse(self):
        # Python < 3.12.4 leaves positionals after an option as extras;
        # parse_args routes them to still's moments.
        a = parse_args(["--root", "/x", "still", "t", "--timing", "estimate", "HT1_Hook:3", "--json"])
        self.assertEqual(a.command, "still")
        self.assertEqual(a.topic, "t")
        self.assertEqual(a.moments, ["HT1_Hook:3"])
        a = parse_args(["--root", "/x", "still", "t", "HT1_Hook:3", "--timing", "estimate", "--json", "1s"])
        self.assertEqual(sorted(a.moments), ["1s", "HT1_Hook:3"])

    def test_extras_stay_an_error_on_other_verbs(self):
        with self.assertRaises(SystemExit):
            parse_args(["--root", "/x", "topics", "--json", "junk"])


class Yaml(unittest.TestCase):
    def test_subset(self):
        y = parse_yaml('title: "A: b"\ntags: [a, "b c"]\nmaterials:\n  - "x.pdf"   # c\nnarrated:\n  shots: shots/\n  output: v.mp4\n')
        self.assertEqual(y, {"title": "A: b", "tags": ["a", "b c"], "materials": ["x.pdf"],
                             "narrated": {"shots": "shots/", "output": "v.mp4"}})

    def test_refuses_what_it_cannot_read(self):
        with self.assertRaises(YamlError):
            parse_yaml("  indented: 1\n")


class Align(unittest.TestCase):
    def test_written_words_keep_their_punctuation(self):
        tokens = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(["high", "bandwidth", "memory"])]
        r = time_words("High-bandwidth memory, here.", tokens, 2.0)
        self.assertEqual([w["word"] for w in r["words"]], ["High-bandwidth", "memory,", "here."])
        self.assertEqual(r["words"][1]["start"], 1.0)


class Timing(unittest.TestCase):
    NARRATION = [{"scene": "HB1_Hook", "title": "Hook", "lines": ["x" * 152, "y" * 76]},
                 {"scene": "HB2_Wires", "title": "Wires", "hold": 0.6, "lines": ["z" * 38]}]

    def test_estimate_is_15_2_characters_a_second(self):
        m, w = timeline.estimate_manifest(self.NARRATION)
        self.assertEqual((m["leadIn"], m["tail"]), (0.2, 0.4))
        self.assertEqual([l["duration"] for s in m["scenes"] for l in s["lines"]], [10.0, 5.0, 2.5])
        self.assertEqual(w["lines"][0]["source"], "estimate")

    def test_layout_frames(self):
        m, w = timeline.estimate_manifest(self.NARRATION)
        tl = timeline.lay_out(m, self.NARRATION, w, source="estimate")
        a, b = tl["scenes"]
        # slots: ceil((0.2 + 10 + 0.4) * 30) = 318, ceil(5.6 * 30) = 168; hold 0.6 s = 18 frames
        self.assertEqual([l["slot"] for l in a["lines"]], [318, 168])
        self.assertEqual([l["audioFrom"] for l in a["lines"]], [6, 324])
        self.assertEqual((a["from"], a["frames"], b["from"], b["frames"]), (0, 486, 486, 93 + 18))
        self.assertEqual(tl["frames"], 486 + 111)
        st = timeline.scene_timing(tl, b)
        self.assertEqual(st["frames"], 111)
        self.assertEqual(st["lines"][0]["voice"], 0.2)
        self.assertEqual(st["lines"][0]["end"], 3.1)
        self.assertEqual(st["hold"], 0.6)

    def test_estimated_words_fit_the_clip(self):
        ws = timeline.estimate_words("one two three", 1.4)
        self.assertEqual([w["word"] for w in ws], ["one", "two", "three"])
        self.assertLessEqual(ws[-1]["end"], 1.4)
        self.assertTrue(all(a["end"] <= b["start"] for a, b in zip(ws, ws[1:])))


class Keys(unittest.TestCase):
    SRC = ("import bpy\n\n# a comment\ndef build(shot):\n    bpy.ops.mesh.primitive_cube_add()\n"
           "    shot.label('HBM', anchor='x',\n               start=1, end=2)\n    shot.title('Hook', start=0, end=1)\n")

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "blenderkc" / "fonts").mkdir(parents=True)
        (self.d / "blenderkc" / "shot.py").write_text("X = 1\n")
        (self.d / "blenderkc" / "overlay.py").write_text("Y = 1\n")
        (self.d / "blenderkc" / "fonts" / "Inter.ttf").write_bytes(b"font")
        (self.d / "requirements.txt").write_text("bpy==5.2.2\nPillow==12.3.0\n")
        self.t = self.d / "topics" / "a" / "b"
        (self.t / "shots").mkdir(parents=True)
        self.shot = self.t / "shots" / "HB1_Hook.py"
        self.shot.write_text(self.SRC)
        (self.t / "shots" / "HB2_Wires.py").write_text("def build(shot):\n    pass\n")
        (self.t / "narration.json").write_text("[]")
        self.settings = {"res": 100, "samples": 16, "fps": 30, "device": "auto"}
        self.timing = {"scene": "HB1_Hook", "frames": 90}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def keys(self, timing=None, settings=None):
        p = keys.plate_key(self.d, self.t, "HB1_Hook", ["HB1_Hook", "HB2_Wires"], "shots/", timing or self.timing, settings or self.settings)
        return p, keys.clip_key(self.d, self.t, "HB1_Hook", "shots/", p)

    def test_the_keys_are_pinned(self):
        """The plate and clip keys of this fixture, as main computed them on
        2026-10-10 before detached host rendering. A change to how a key is
        computed renders every plate again (a day on the homeserver for HBM),
        so it must be a decision, never a side effect: bump CACHE_VERSION and
        this pin together."""
        self.assertEqual(self.keys(), ("b1cd23c933d6ff627c977619baa98ff760de3c2e07e66a4a337388a0c17d74cb",
                                       "ae46e33f1dd824c37d6e1e44029abaf37fdc13923dd616f8be7aac373403e676"))

    def test_overlay_edit_changes_only_the_clip(self):
        p0, c0 = self.keys()
        self.shot.write_text(self.SRC.replace("'HBM'", "'HBM3E'").replace("'Hook'", "'The hook'"))
        p1, c1 = self.keys()
        self.assertEqual(p0, p1)
        self.assertNotEqual(c0, c1)

    def test_comment_and_blank_edits_change_nothing(self):
        k0 = self.keys()
        self.shot.write_text(self.SRC.replace("# a comment\n", "# another comment\n\n\n"))
        self.assertEqual(k0, self.keys())

    def test_3d_edit_changes_both(self):
        p0, c0 = self.keys()
        self.shot.write_text(self.SRC.replace("primitive_cube_add()", "primitive_uv_sphere_add()"))
        p1, c1 = self.keys()
        self.assertNotEqual(p0, p1)
        self.assertNotEqual(c0, c1)

    def test_an_overlay_call_sharing_a_line_stays_in(self):
        self.shot.write_text("def build(shot):\n    x = 1; shot.label('a')\n")
        p0, _ = self.keys()
        self.shot.write_text("def build(shot):\n    x = 1; shot.label('b')\n")
        p1, _ = self.keys()
        self.assertNotEqual(p0, p1)

    def test_a_kit_glow_is_3d_not_an_overlay(self):
        self.shot.write_text("def build(shot):\n    hero.glow(shot, 3.4, 4.4, strength=6.0)\n")
        p0, _ = self.keys()
        self.shot.write_text("def build(shot):\n    hero.glow(shot, 3.4, 4.4, strength=9.0)\n")
        self.assertNotEqual(p0, self.keys()[0])

    def test_only_builds_own_shot_parameter_is_stripped(self):
        self.shot.write_text("def build(s):\n    s.glow('a', start=1)\n    shot.label('x')\n")
        p0, _ = self.keys()
        self.shot.write_text("def build(s):\n    s.glow('b', start=2)\n    shot.label('x')\n")
        self.assertEqual(p0, self.keys()[0])
        self.shot.write_text("def build(s):\n    s.glow('b', start=2)\n    shot.label('y')\n")
        self.assertNotEqual(p0, self.keys()[0])

    def test_inputs_that_change_pixels(self):
        p0, _ = self.keys()
        self.assertNotEqual(p0, self.keys(timing={**self.timing, "frames": 91})[0])
        self.assertNotEqual(p0, self.keys(settings={**self.settings, "samples": 32})[0])
        self.assertEqual(p0, self.keys(settings={**self.settings, "device": "OPTIX"})[0])
        (self.d / "blenderkc" / "shot.py").write_text("X = 2\n")
        self.assertNotEqual(p0, self.keys()[0])
        (self.d / "requirements.txt").write_text("bpy==5.2.3\n")
        self.assertNotEqual(p0, self.keys()[0])

    def test_plate_encode_is_named_in_the_key(self):
        from blenderkc_cli import media
        a = media.PLATE_ARGS
        named = f"{a[1]} {a[3]} {a[5]} crf{a[7]} {a[9]} bt709"
        self.assertEqual(named, keys.PLATE_ENCODE)

    def test_library_split(self):
        p0, c0 = self.keys()
        (self.d / "blenderkc" / "overlay.py").write_text("Y = 2\n")
        p1, c1 = self.keys()
        self.assertEqual(p0, p1)
        self.assertNotEqual(c0, c1)
        (self.d / "blenderkc" / "fonts" / "Inter.ttf").write_bytes(b"font2")
        self.assertNotEqual(c1, self.keys()[1])

    def test_other_scenes_scripts_are_not_this_scenes_input(self):
        p0, _ = self.keys()
        (self.t / "shots" / "HB2_Wires.py").write_text("def build(shot):\n    return 2\n")
        self.assertEqual(p0, self.keys()[0])
        (self.t / "shots" / "parts.py").write_text("R = 1\n")
        self.assertNotEqual(p0, self.keys()[0])


class Remote(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()).resolve() / "blenderkc"
        (self.d / "tools" / "cli").mkdir(parents=True)
        (self.d / "tools" / "cli" / "blenderkc").write_text("")
        (self.d / "requirements.txt").write_text("bpy==5.2.2\n")

    def repo(self, home):
        import os
        old = os.environ.get("BLENDERKC_HOME")
        os.environ["BLENDERKC_HOME"] = str(home)
        try:
            return Repo(self.d)
        finally:
            if old is None:
                del os.environ["BLENDERKC_HOME"]
            else:
                os.environ["BLENDERKC_HOME"] = old

    def test_push_excludes_venvs_git_env_and_an_inside_data_root(self):
        cmd = remote.push_cmd(self.repo(self.d), "home")
        self.assertEqual(cmd[:3], ["rsync", "-az", "--delete"])
        ex = [cmd[i + 1] for i, c in enumerate(cmd) if c == "--exclude"]
        for e in ("/.git", "/.venv", "/.venv-*", "/.env", "__pycache__", "/out", "/plates", "/frames", "/cache"):
            self.assertIn(e, ex)
        self.assertEqual(cmd[-2], f"{self.d}/")
        self.assertRegex(cmd[-1], r"^home:\.cache/blenderkc/src/blenderkc-[0-9a-f]{12}/$")
        self.assertEqual(cmd[-4], "-e")
        self.assertTrue(cmd[-3].startswith("ssh -o BatchMode=yes"), cmd[-3])

    def test_push_with_an_outside_data_root_excludes_no_data_dirs(self):
        ex = remote.excludes(self.repo(self.d.parent / "data"))
        self.assertNotIn("/out", ex)

    def test_ssh_starts_the_host_job_from_stdin_under_cache(self):
        cmd = remote.start_cmd(self.repo(self.d.parent / "data"), "home")
        self.assertEqual(cmd[0], "ssh")
        self.assertIn("BatchMode=yes", cmd)
        self.assertEqual(cmd[-2], "home")
        script = cmd[-1]
        self.assertRegex(script, r'cd "\$HOME"/\.cache/blenderkc/src/blenderkc-[0-9a-f]{12} && touch \.used')
        self.assertIn('export BLENDERKC_HOME="$HOME"/.cache/blenderkc/home', script)
        self.assertIn("host-job start --job - --ensure-venv --venv", script)
        self.assertRegex(script, r'--venv "\$HOME"/\.cache/blenderkc/venv-[0-9a-f]{12}$')
        self.assertIn(".nix-profile/bin", script)

    def test_host_base_moves_everything_and_refuses_paths_outside_home(self):
        import os
        r = self.repo(self.d.parent / "data")
        os.environ["BLENDERKC_HOST_BASE"] = ".cache/blenderkc-trial"
        try:
            self.assertTrue(remote.remote_code(r).startswith(".cache/blenderkc-trial/src/"))
            self.assertEqual(remote.remote_home(r), ".cache/blenderkc-trial/home")
            self.assertIn(".cache/blenderkc-trial/venv-", remote.start_cmd(r, "home")[-1])
            for bad in ("/etc", "~/x", "../up", ".cache/blender kc"):
                os.environ["BLENDERKC_HOST_BASE"] = bad
                with self.assertRaises(Exception):
                    remote.remote_base(r)
        finally:
            del os.environ["BLENDERKC_HOST_BASE"]

    def test_snapshot_digest_follows_what_is_copied(self):
        r = self.repo(self.d.parent / "data")
        remote._digests.clear()
        d0 = remote.tree_digest(r)
        for junk in (".git/HEAD", ".venv/x", ".venv-n/y", "a/__pycache__/m.pyc", ".env", ".DS_Store"):
            (self.d / junk).parent.mkdir(parents=True, exist_ok=True)
            (self.d / junk).write_text("x")
        remote._digests.clear()
        self.assertEqual(d0, remote.tree_digest(r))
        (self.d / "blenderkc").mkdir()
        (self.d / "blenderkc" / "kit.py").write_text("A = 1\n")
        remote._digests.clear()
        d1 = remote.tree_digest(r)
        self.assertNotEqual(d0, d1)
        # with the data root inside the checkout, its folders are not copied
        inside = self.repo(self.d)
        (self.d / "plates").mkdir()
        (self.d / "plates" / "p.mp4").write_text("x")
        remote._digests.clear()
        self.assertEqual(d1, remote.tree_digest(inside))
        remote._digests.clear()

    def test_prune_touches_only_this_checkouts_old_snapshots(self):
        """Run the prune script for real against a fake ~/.cache/blenderkc/src."""
        import os, subprocess, time  # noqa: E401
        r = self.repo(self.d.parent / "data")
        remote._digests.clear()
        cur = Path(remote.remote_code(r)).name
        home = self.d.parent / "fakehome"
        src = home / ".cache" / "blenderkc" / "src"
        old = time.time() - 3 * 86400
        names = [f"blenderkc-{i:012d}" for i in range(7)]
        for i, n in enumerate([cur, *names, "other-000000000000"]):
            (src / n).mkdir(parents=True)
            if n != names[0]:  # names[0] has no marker at all
                (src / n / ".used").write_text("")
                os.utime(src / n / ".used", (old - i * 60, old - i * 60))
        os.utime(src / cur / ".used", (old - 9999, old - 9999))  # current and stale: still kept
        script = remote.prune_cmd(r, "home")[-1]
        subprocess.run(["sh", "-c", script], env={**os.environ, "HOME": str(home)}, check=True)
        left = sorted(p.name for p in src.iterdir())
        # the current one, another checkout's, and the five most recently used are kept
        self.assertIn(cur, left)
        self.assertIn("other-000000000000", left)
        self.assertNotIn(names[0], left)
        self.assertEqual(len([n for n in left if n.startswith("blenderkc-") and n != cur]), 5)
        remote._digests.clear()

    def test_upload_marks_the_snapshot_used_and_rsync_keeps_the_mark(self):
        r = self.repo(self.d.parent / "data")
        self.assertIn('/.used', remote.excludes(r))
        self.assertRegex(remote.mkdir_cmd(r, "home")[-1], r'touch "\$HOME"/\.cache/blenderkc/src/blenderkc-[0-9a-f]{12}/\.used$')

    def test_pull_takes_only_the_named_plates(self):
        r = self.repo(self.d.parent / "data")
        cmd = remote.pull_cmd(r, "home", "a/b", ["HB1_Hook.0123456789abcdef.mp4"])
        self.assertEqual(cmd[:5], ["rsync", "-a", "--timeout=300", "--include", "HB1_Hook.0123456789abcdef.mp4"])
        self.assertEqual(cmd[5:7], ["--exclude", "*"])
        self.assertEqual(cmd[-2], "home:.cache/blenderkc/home/plates/a/b/")
        self.assertEqual(cmd[-1], f"{self.d.parent / 'data' / 'plates' / 'a/b'}/")

    def test_render_command_matches_the_renderer_contract(self):
        from blenderkc_cli import runner
        r = self.repo(self.d.parent / "data")
        cmd = runner.render_cmd(r, "/py", Path("/t"), "HB1_Hook", Path("/t.json"), 1, 90, Path("/o"),
                                {"res": 100, "samples": 16, "device": "auto"}, prefix=["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=3G"])
        self.assertEqual(cmd, ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=3G", "/py", "-m", "blenderkc.render",
                               "--topic", "/t", "--scene", "HB1_Hook", "--timing", "/t.json", "--frames", "1", "90",
                               "--out", "/o", "--res", "100", "--samples", "16", "--device", "auto"])


if __name__ == "__main__":
    unittest.main()
