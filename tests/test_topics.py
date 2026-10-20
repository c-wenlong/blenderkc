"""`topics --json` is a contract with the self-learn catalog, which indexes
manimkc, remotionkc, revideokc and blenderkc with one reader: these tests pin
its shape field by field, the drift report and the data root it reports."""

import json
import shutil
import unittest

from helpers import Sandbox

TOPIC = "computer-architecture/gpu-architecture/hbm-test"


class TopicsJson(unittest.TestCase):
    def setUp(self):
        self.s = Sandbox()

    def tearDown(self):
        self.s.cleanup()

    def test_empty_checkout(self):
        r = self.s.run("topics", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"outRoot": str(self.s.home), "topics": [], "drift": {"orphans": [], "unrendered": []}})

    def test_unrendered_topic_has_manimkcs_shape(self):
        self.s.topic()
        r = self.s.run("topics", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = f"out/{TOPIC}"
        self.assertEqual(json.loads(r.stdout), {
            "outRoot": str(self.s.home),
            "topics": [{
                "path": TOPIC,
                "title": "HBM test",
                "topic": "gpu-architecture",
                "concepts": [],
                "tags": ["hbm"],
                "prerequisites": [],
                "materials": [],
                "prefix": "HT",
                "out_dir": out,
                "sections": [{
                    "section": "narrated",
                    "files": [{"file": "shots/HT1_Hook.py", "scenes": ["HT1_Hook"]},
                              {"file": "shots/HT2_Wires.py", "scenes": ["HT2_Wires"]}],
                    "output": "hbm-test.mp4",
                    "deliverable": {
                        "path": f"{out}/hbm-test.mp4",
                        "exists": False,
                        "duration": None,
                        "sidecars": {
                            "transcript": {"path": f"{out}/hbm-test.transcript.json", "exists": False},
                            "chapters": {"path": f"{out}/hbm-test.chapters.json", "exists": False},
                            "vtt": {"path": f"{out}/hbm-test.vtt", "exists": False},
                        },
                    },
                }],
            }],
            "drift": {"orphans": [], "unrendered": [TOPIC]},
        })

    def test_orphan_fails_check(self):
        self.s.topic()
        orphan = self.s.home / "out" / "gone" / "topic"
        orphan.mkdir(parents=True)
        (orphan / "old.mp4").write_bytes(b"")
        r = self.s.run("topics", "--json", "--check")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stdout)["drift"]["orphans"], [{"path": "out/gone/topic", "videos": ["old.mp4"]}])

    def test_missing_topic_slug_is_a_problem(self):
        self.s.topic()
        yml = self.s.root / "topics" / TOPIC / "topic.yml"
        yml.write_text(yml.read_text().replace("topic: gpu-architecture\n", ""))
        r = self.s.run("topics", "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("has no `topic:`", r.stdout)

    def test_a_block_without_output_is_a_problem(self):
        self.s.topic()
        yml = self.s.root / "topics" / TOPIC / "topic.yml"
        yml.write_text(yml.read_text().replace("  output: hbm-test.mp4\n", ""))
        r = self.s.run("topics", "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("has no `output:`", r.stdout)

    def test_data_root_from_checkout_env(self):
        self.s.topic()
        (self.s.root / ".env").write_text(f"BLENDERKC_HOME='{self.s.tmp / 'elsewhere'}'\n")
        env = {k: v for k, v in self.s.env.items() if k != "BLENDERKC_HOME"}
        import subprocess, sys  # noqa: E401
        r = subprocess.run([sys.executable, str(self.s.root / "tools/cli/blenderkc"), "--root", str(self.s.root), "topics", "--json"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(json.loads(r.stdout)["outRoot"], str((self.s.tmp / "elsewhere").resolve()))

    def test_relative_data_root_refused(self):
        r = self.s.run("topics", env={"BLENDERKC_HOME": "relative/path"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("must be an absolute path", r.stderr)

    def test_topics_tree_from_the_environment(self):
        self.s.topic()
        other = self.s.tmp / "bkc-topics"
        shutil.move(str(self.s.root / "topics"), str(other))
        r = self.s.run("topics", "--json", env={"BLENDERKC_TOPICS": str(other)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([t["path"] for t in json.loads(r.stdout)["topics"]], [TOPIC])
        r = self.s.run("home", "--json", env={"BLENDERKC_TOPICS": str(other)})
        self.assertEqual(json.loads(r.stdout)["topics"]["dir"], str(other.resolve()))

    def test_topics_tree_from_checkout_env(self):
        self.s.topic()
        other = self.s.tmp / "bkc-topics"
        shutil.move(str(self.s.root / "topics"), str(other))
        (self.s.root / ".env").write_text(f"BLENDERKC_TOPICS='{other}'\n")
        r = self.s.run("topics", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([t["path"] for t in json.loads(r.stdout)["topics"]], [TOPIC])

    def test_relative_topics_tree_refused(self):
        r = self.s.run("topics", env={"BLENDERKC_TOPICS": "relative/path"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("must be an absolute path", r.stderr)


if __name__ == "__main__":
    unittest.main()
