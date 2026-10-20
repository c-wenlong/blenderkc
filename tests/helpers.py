"""Synthetic checkouts for the CLI tests: the real CLI, the stand-in library
(tests/stub/blenderkc) and the stand-in gnosia-audio, in a temp folder with
its own data root. No bpy, no network, no model."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
sys.path.insert(0, str(REPO / "tools" / "cli"))


class Sandbox:
    def __init__(self, library: bool = True):
        self.tmp = Path(tempfile.mkdtemp(prefix="bkc-test-")).resolve()
        self.root = self.tmp / "checkout"
        self.home = self.tmp / "home"
        (self.root / "tools").mkdir(parents=True)
        shutil.copytree(REPO / "tools" / "cli", self.root / "tools" / "cli",
                        ignore=shutil.ignore_patterns("__pycache__"))
        for f in ("requirements.txt", "requirements-narration.txt"):
            shutil.copyfile(REPO / f, self.root / f)
        if library:
            shutil.copytree(TESTS / "stub" / "blenderkc", self.root / "blenderkc")
        self.home.mkdir()
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("BLENDERKC_", "GNOSIA_", "MANIMKC_"))},
            "BLENDERKC_HOME": str(self.home),
            "BLENDERKC_PYTHON": sys.executable,
            "GNOSIA_AUDIO_BIN": str(TESTS / "fake_gnosia_audio.py"),
            "FAKE_GNOSIA_CACHE": str(self.tmp / "clips"),
            "STUB_RENDER_LOG": str(self.tmp / "render.log"),
        }

    def run(self, *args, env=None, input=None):
        r = subprocess.run([sys.executable, str(self.root / "tools" / "cli" / "blenderkc"), "--root", str(self.root), *args],
                           capture_output=True, text=True, env={**self.env, **(env or {})}, input=input)
        return r

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def topic(self, path="computer-architecture/gpu-architecture/hbm-test", scenes=None, prefix="HT", label=False):
        scenes = scenes or [{"scene": f"{prefix}1_Hook", "title": "Hook", "lines": ["Memory sits beside the GPU.", "Two lines here."]},
                            {"scene": f"{prefix}2_Wires", "title": "Wires", "hold": 0.5, "lines": ["Bandwidth is width times rate."]}]
        self.write(f"topics/{path}/topic.yml", "\n".join([
            'title: "HBM test"', "topic: gpu-architecture", "concepts: []", "tags: [hbm]", "prerequisites: []",
            "materials: []", f"prefix: {prefix}", "narrated:", "  shots: shots/", "  output: hbm-test.mp4", ""]))
        self.write(f"topics/{path}/narration.json", json.dumps(scenes, indent=1))
        for s in scenes:
            body = "def build(shot):\n    pass\n"
            if label:
                body = "def build(shot):\n    shot.label('x', start=0, end=1)\n"
            self.write(f"topics/{path}/shots/{s['scene']}.py", body)
        return path

    def repo(self):
        """The checkout as the CLI sees it, with this sandbox's data root."""
        from blenderkc_cli import repo as repo_mod
        old = os.environ.get("BLENDERKC_HOME")
        os.environ["BLENDERKC_HOME"] = str(self.home)
        try:
            return repo_mod.Repo(self.root)
        finally:
            if old is None:
                del os.environ["BLENDERKC_HOME"]
            else:
                os.environ["BLENDERKC_HOME"] = old

    def renders(self):
        p = self.tmp / "render.log"
        return p.read_text().splitlines() if p.exists() else []

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
