"""`build --host` end to end against a stand-in host: tests/fake_ssh.py runs
each ssh command with sh on this machine, $HOME at a folder of the sandbox,
so rsync, the detached host job, the polling and the copies back all run for
real. The renderer is the stub library. Also Link's retries and the
Tailscale login line, and the status report."""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import HAS_FFMPEG, TESTS, Sandbox

from blenderkc_cli import hostjob, remote  # noqa: E402  (helpers puts tools/cli on the path)
from blenderkc_cli.repo import CliError  # noqa: E402

HAS_RSYNC = bool(shutil.which("rsync"))
BANNER = ("# Tailscale SSH requires an additional check.\n"
          "# To authenticate, visit: https://login.tailscale.com/a/l4e376293b10b4\n")


def wait_for(cond, what, timeout=60.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


@unittest.skipUnless(HAS_FFMPEG and HAS_RSYNC, "needs ffmpeg, ffprobe and rsync")
class DetachedHost(unittest.TestCase):
    def setUp(self):
        self.s = Sandbox()
        self.t = self.s.topic()
        self.hosthome = self.s.tmp / "hosthome"
        req = (self.s.root / "requirements.txt").read_bytes()
        digest = hashlib.sha256(req).hexdigest()
        venv = self.hosthome / ".cache" / "blenderkc" / f"venv-{digest[:12]}"
        (venv / "bin").mkdir(parents=True)
        os.symlink(sys.executable, venv / "bin" / "python")  # a ready venv: the stub renderer needs no bpy
        (venv / ".blenderkc-requirements.sha256").write_text(digest + "\n")
        self.fail_file = self.s.tmp / "ssh-fail"
        self.ssh_log = self.s.tmp / "ssh.log"
        self.env = {"BLENDERKC_SSH": str(TESTS / "fake_ssh.py"), "FAKE_SSH_HOME": str(self.hosthome),
                    "FAKE_SSH_LOG": str(self.ssh_log), "FAKE_SSH_FAIL": str(self.fail_file),
                    "BLENDERKC_POLL_S": "0.2", "BLENDERKC_RETRY_FIRST_S": "0.1"}
        self.jobdir = self.hosthome / ".cache" / "blenderkc" / "home" / "cache" / "hostjob" / self.t

    def tearDown(self):
        pid = self.host_pid()
        if pid:
            kill_group(pid)
        self.s.cleanup()

    def build(self, *extra, env=None, ok=True):
        r = self.s.run("build", self.t, "--host", "fakehost", "--json", *extra, env={**self.env, **(env or {})})
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def popen_build(self, env):
        cmd = [sys.executable, str(self.s.root / "tools" / "cli" / "blenderkc"), "--root", str(self.s.root),
               "build", self.t, "--host", "fakehost", "--json"]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env={**self.s.env, **self.env, **env}, start_new_session=True)

    def host_pid(self):
        try:
            return int((self.jobdir / "pid").read_text())
        except (OSError, ValueError):
            return None

    def host_status(self):
        try:
            return json.loads((self.jobdir / "status.json").read_text())
        except (OSError, ValueError):
            return {}

    def starts(self):
        return [l for l in self.ssh_log.read_text().splitlines() if "host-job start" in l]

    def frames_rendered(self):
        return [l for l in self.s.renders() if l.split()[3] == "frames"]

    def test_one_detached_job_renders_every_scene_and_the_plates_come_back(self):
        out = json.loads(self.build().stdout)
        self.assertEqual(out["rendered"], ["HT1_Hook", "HT2_Wires"])
        self.assertFalse(out["draft"])
        self.assertTrue(Path(out["path"]).is_file())
        self.assertEqual(len(self.starts()), 1)  # one host job for the whole topic, not one ssh per scene
        st = self.host_status()
        self.assertEqual(st["state"], "done")
        self.assertEqual([x["scene"] for x in st["done"]], ["HT1_Hook", "HT2_Wires"])
        self.assertIn("--- started", (self.jobdir / "log").read_text())
        for x in st["done"]:
            self.assertTrue((self.s.home / x["plate"]).is_file(), x["plate"])
        # a second build finds every clip here and never asks the host
        before = len(self.starts())
        out = json.loads(self.build().stdout)
        self.assertEqual(out["rendered"], [])
        self.assertEqual(len(self.starts()), before)

    def test_a_build_run_again_attaches_to_the_running_host_job(self):
        p = self.popen_build({"STUB_RENDER_FRAME_SLEEP": "0.05"})
        try:
            wait_for(lambda: self.host_status().get("state") == "running", "the host job to run")
            pid = self.host_pid()
            os.killpg(p.pid, signal.SIGKILL)  # the Mac's build dies, ssh and all
            p.communicate()
            os.kill(pid, 0)  # the host job did not notice
        finally:
            kill_group(p.pid)
        r = self.build(env={"STUB_RENDER_FRAME_SLEEP": "0.05"})
        self.assertIn("attached to the running host job", r.stderr)
        self.assertEqual(self.host_pid(), pid)
        self.assertEqual(len(self.starts()), 2)
        self.assertEqual(sorted(l.split()[0] for l in self.frames_rendered()), ["HT1_Hook", "HT2_Wires"])  # each once
        self.assertTrue(json.loads(r.stdout)["path"])

    def test_a_host_job_that_died_is_started_again_and_resumes_from_its_frames(self):
        p = self.popen_build({"STUB_RENDER_FRAME_SLEEP": "0.05"})
        try:
            fd = self.hosthome / ".cache" / "blenderkc" / "home" / "frames" / self.t / "HT1_Hook"
            wait_for(lambda: len(list(fd.glob("*/f_*.png"))) >= 10, "ten frames on the host")
            first = self.host_pid()
            kill_group(first)  # the host job and its renderer die (a reboot, an OOM kill)
            stdout, stderr = p.communicate(timeout=120)
        finally:
            kill_group(p.pid)
        self.assertEqual(p.returncode, 0, stderr)
        self.assertIn("the host job died", stderr)
        self.assertNotEqual(self.host_pid(), first)
        log = (self.jobdir / "log").read_text()
        self.assertIn("--- resumed", log)
        resumed = [l for l in log.splitlines() if "HT1_Hook: rendering" in l]
        self.assertEqual(len(resumed), 2, log)
        left = int(resumed[1].split("rendering ")[1].split(" of ")[0])
        total = int(resumed[1].split(" of ")[1].split(" frames")[0])
        self.assertLess(left, total)  # the second run rendered only what was missing
        self.assertTrue(json.loads(stdout)["path"])

    def test_a_job_that_fails_the_same_way_stops_the_build_with_its_log(self):
        r = self.build(env={"STUB_RENDER_FAIL": "HT1_Hook"}, ok=False)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("without a new frame", r.stderr)
        self.assertIn("stub: failing on purpose", r.stderr)
        self.assertEqual(len(self.starts()), 1 + remote.MAX_RESTARTS)

    def test_dropped_connections_are_retried_and_the_tailscale_login_shown_once(self):
        self.fail_file.write_text("3")
        r = self.build()
        self.assertEqual(r.stderr.count("the host needs a Tailscale login: https://login.tailscale.com/a/"), 1, r.stderr)
        self.assertEqual(r.stderr.count("trying again in"), 3)
        self.assertNotIn("# Tailscale SSH requires", r.stderr)
        self.assertEqual(self.fail_file.read_text(), "0")

    def test_a_host_that_never_answers_stops_the_build_after_the_retry_window(self):
        self.fail_file.write_text("100000")
        r = self.build("--retry-hours", "0.0003", ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("has not answered for 0.0003 h", r.stderr)
        self.assertIn("the host needs a Tailscale login", r.stderr)

    def test_bad_retry_and_poll_settings_are_refused_before_the_host_is_touched(self):
        for extra, env, says in ((["--retry-hours", "inf"], {}, "--retry-hours"), (["--retry-hours", "-1"], {}, "--retry-hours"),
                                 ([], {"BLENDERKC_POLL_S": "-5"}, "BLENDERKC_POLL_S"),
                                 ([], {"BLENDERKC_RETRY_FIRST_S": "nan"}, "BLENDERKC_RETRY_FIRST_S")):
            r = self.build(*extra, env=env, ok=False)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn(says, r.stderr)
        self.assertFalse(self.ssh_log.exists())

    def test_a_running_job_with_other_keys_is_never_replaced(self):
        p = self.popen_build({"STUB_RENDER_FRAME_SLEEP": "0.05"})
        try:
            wait_for(lambda: self.host_status().get("state") == "running", "the host job to run")
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
        finally:
            kill_group(p.pid)
        r = self.build("--samples", "3", ok=False)  # other settings: other plate keys
        self.assertEqual(r.returncode, 2)
        self.assertIn(f"a host job of {self.t} is running (pid {self.host_pid()}) without HT1_Hook, HT2_Wires", r.stderr)

    def test_build_status_reads_the_host(self):
        self.build()
        r = self.s.run("build-status", self.t, "--host", "fakehost", "--json", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout)
        self.assertEqual((st["state"], st["done"], st["total"], st["here"]), ("done", 2, 2, 2))
        r = self.s.run("build-status", self.t, "--host", "fakehost", env=self.env)
        self.assertIn("plates  2 of 2 done on the host, 2 copied here", r.stdout)
        (self.jobdir / "code").write_text(str(self.hosthome / "pruned-long-ago") + "\n")  # the job's copy is gone
        r = self.s.run("build-status", self.t, "--host", "fakehost", "--json", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["state"], "done")
        # this checkout changed since its last upload: the newest copy on the host that can read the job answers
        (self.s.root / "topics" / "note.txt").write_text("an edit after the build\n")
        remote._digests.clear()
        r = self.s.run("build-status", self.t, "--host", "fakehost", "--json", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["state"], "done")
        src = self.hosthome / ".cache" / "blenderkc" / "src"
        (copy,) = [d for d in src.iterdir() if d.is_dir()]
        copy.rename(src / "an old copy-000000000000")  # a space in its name: read as one path
        r = self.s.run("build-status", self.t, "--host", "fakehost", "--json", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["state"], "done")
        shutil.rmtree(src)  # no copy at all
        r = self.s.run("build-status", self.t, "--host", "fakehost", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("unreadable", r.stdout)
        self.assertIn("run the build to copy one", r.stdout)
        (self.s.tmp / "empty").mkdir()
        r = self.s.run("build-status", "hbm-test", "--host", "fakehost", env={**self.env, "FAKE_SSH_HOME": str(self.s.tmp / "empty")})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no render job on fakehost", r.stdout)

    def test_topics_outside_the_checkout_are_pushed_to_the_snapshot(self):
        other = self.s.tmp / "bkc-topics"
        shutil.move(str(self.s.root / "topics"), str(other))
        out = json.loads(self.build(env={"BLENDERKC_TOPICS": str(other)}).stdout)
        self.assertEqual(out["rendered"], ["HT1_Hook", "HT2_Wires"])
        src = self.hosthome / ".cache" / "blenderkc" / "src"
        (snap,) = [d for d in src.iterdir() if d.is_dir()]
        self.assertTrue((snap / "topics" / self.t / "topic.yml").is_file())
        self.assertFalse((self.s.root / "topics").exists())


class SnapshotContent(unittest.TestCase):
    """What a build --host pushes, wherever the topics tree lives: the topics
    always land at <snapshot>/topics/ by their own rsync, and the snapshot's
    digest covers them so two divergent trees never share one."""

    def setUp(self):
        self.s = Sandbox()
        self.t = self.s.topic()
        self.repo = self.s.repo()

    def tearDown(self):
        self.s.cleanup()

    def test_the_checkout_push_never_carries_the_topics(self):
        self.assertIn("/topics", remote.excludes(self.repo))
        cmd = remote.push_topics_cmd(self.repo, "h")
        self.assertEqual(cmd[-2:], [f"{self.repo.topics_dir}/", f"h:{remote.remote_code(self.repo)}/topics/"])

    def test_the_digest_covers_the_topics(self):
        d = remote.tree_digest(self.repo)
        (self.s.root / "topics" / self.t / "topic.yml").write_text("changed\n")
        remote._digests.clear()
        self.assertNotEqual(remote.tree_digest(self.repo), d)

    def test_the_digest_and_push_follow_an_external_topics_tree(self):
        other = self.s.tmp / "bkc-topics"
        shutil.move(str(self.s.root / "topics"), str(other))
        os.environ["BLENDERKC_TOPICS"] = str(other)
        try:
            repo = self.s.repo()
        finally:
            del os.environ["BLENDERKC_TOPICS"]
        self.assertEqual(repo.topics_dir, other.resolve())
        self.assertIn("/topics", remote.excludes(repo))  # a stray checkout topics/ is never shipped
        d = remote.tree_digest(repo)
        (other / self.t / "topic.yml").write_text("changed\n")
        remote._digests.clear()
        self.assertNotEqual(remote.tree_digest(repo), d)


class HostJobStatus(unittest.TestCase):
    """hostjob.status from files alone: a job half done, a frame every 3.2 s."""

    def setUp(self):
        self.s = Sandbox()
        self.t = self.s.topic()
        self.repo = self.s.repo()
        self.sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "host-job"])
        d = hostjob.job_dir(self.repo, self.t)
        d.mkdir(parents=True)
        k1, k2 = "a1" * 32, "b2" * 32
        (d / "job.json").write_text(json.dumps({"schema": 1, "topic": self.t, "fps": 30, "settings": {"res": 100, "samples": 16},
                                                "scenes": [{"scene": "HT1_Hook", "frames": 100, "plate": k1, "timing": {}},
                                                           {"scene": "HT2_Wires", "frames": 200, "plate": k2, "timing": {}}]}))
        (d / "pid").write_text(f"{self.sleeper.pid}\n")
        (d / "status.json").write_text(json.dumps({
            "state": "running", "startedAt": "2026-10-10T18:00:00+00:00", "restarts": 1,
            "done": [{"scene": "HT1_Hook", "plate": f"plates/{self.t}/HT1_Hook.{k1[:16]}.mp4", "frames": 100, "cached": False, "seconds": 400}],
            "current": {"scene": "HT2_Wires", "frames": 200}}))
        fd = self.s.home / "frames" / self.t / "HT2_Wires" / k2[:16]
        fd.mkdir(parents=True)
        t0 = time.time() - 100
        for n in range(1, 11):
            f = fd / f"f_{n:04d}.png"
            f.write_bytes(b"x")
            os.utime(f, (t0 + n * 3.2, t0 + n * 3.2))
        self.d = d

    def tearDown(self):
        self.sleeper.kill()
        self.sleeper.wait()
        self.s.cleanup()

    def test_scenes_done_the_frame_seconds_a_frame_and_eta(self):
        st = hostjob.status(self.repo, self.t)
        self.assertEqual((st["state"], st["done"], st["total"]), ("running", 1, 2))
        self.assertEqual(st["current"], {"scene": "HT2_Wires", "frame": 10, "frames": 200})
        self.assertEqual(st["secondsPerFrame"], 3.2)
        self.assertEqual(st["framesLeft"], 190)
        self.assertEqual(st["etaSeconds"], 608)

    def test_a_dead_pid_reads_as_died_with_the_log(self):
        (self.d / "log").write_text("one\ntwo\nblenderkc: the renderer failed\n")
        self.sleeper.kill()
        self.sleeper.wait()
        st = hostjob.status(self.repo, self.t)
        self.assertEqual(st["state"], "died")
        self.assertEqual(st["logTail"][-1], "blenderkc: the renderer failed")

    def test_a_reused_pid_is_not_the_job(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            (self.d / "pid").write_text(f"{other.pid}\n")
            self.assertIsNone(hostjob.runner_pid(self.repo, self.t))
            self.assertEqual(hostjob.status(self.repo, self.t)["state"], "died")
        finally:
            other.kill()
            other.wait()

    def test_the_status_verb_prints_it_for_a_person(self):
        r = self.s.run("build-status", self.t)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("running (pid", r.stdout)
        self.assertIn("plates  1 of 2 done on the render machine, 0 copied here", r.stdout)
        self.assertIn("now     HT2_Wires, frame 10 of 200", r.stdout)
        self.assertIn("speed   3.2 s a frame", r.stdout)
        self.assertIn("left    190 frames, about 10 min", r.stdout)

    def test_an_older_style_render_job_blocks_a_start(self):
        self.sleeper.kill()
        self.sleeper.wait()
        old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "render-job"])
        try:
            lock = self.s.home / "cache" / "jobs" / self.t / ".lock"
            lock.parent.mkdir(parents=True)
            lock.write_text(str(old.pid))
            job = json.loads((self.d / "job.json").read_text())
            with self.assertRaises(CliError) as e:
                hostjob.start(self.repo, job)
            self.assertIn("one ssh job per scene; stop that build first", str(e.exception))
        finally:
            old.kill()
            old.wait()


@unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
class HostJobLifecycle(unittest.TestCase):
    """`host-job start` and `run` on this machine, as the host runs them."""

    def setUp(self):
        self.s = Sandbox()
        self.t = self.s.topic()
        self.assertEqual(self.s.run("narrate", self.t).returncode, 0)
        self.repo = self.s.repo()
        self.d = hostjob.job_dir(self.repo, self.t)
        self.job = {"schema": 1, "topic": self.t, "shots": "shots/", "fps": 30,
                    "settings": {"res": 10, "samples": 1, "device": "CPU"}, "keepFrames": False,
                    "scenes": [{"scene": "HT1_Hook", "frames": 40, "plate": "c3" * 32,
                                "timing": {"scene": "HT1_Hook", "fps": 30, "frames": 40, "duration": 1.33, "lines": []}},
                               {"scene": "HT2_Wires", "frames": 12, "plate": "d4" * 32,
                                "timing": {"scene": "HT2_Wires", "fps": 30, "frames": 12, "duration": 0.4, "lines": []}}]}

    def tearDown(self):
        for pid, cmd in hostjob._processes():
            if str(self.s.tmp) in cmd:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        self.s.cleanup()

    def start(self, env=None):
        r = self.s.run("host-job", "start", "--job", "-", input=json.dumps(self.job), env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def status(self):
        return hostjob.status(self.repo, self.t)

    def renderers(self):
        return [pid for pid, cmd in hostjob._processes() if "blenderkc.render" in cmd and str(self.s.home) in cmd]

    def test_a_renderer_left_by_a_killed_runner_is_stopped_before_the_job_starts_again(self):
        slow = {"STUB_RENDER_FRAME_SLEEP": "0.2"}
        first = self.start(slow)["pid"]
        wait_for(self.renderers, "the renderer to start")
        orphan = self.renderers()[0]
        os.kill(first, signal.SIGKILL)  # the runner alone: its renderer lives on, as Blender would
        wait_for(lambda: self.status()["state"] == "died", "the job to read as died")
        self.assertTrue(hostjob._alive(orphan))
        again = self.start(slow)
        self.assertTrue(again["resumed"])
        wait_for(lambda: not hostjob._alive(orphan) or None, "the orphan to be stopped", timeout=30)
        self.assertIn(f"stopping a renderer left by an earlier job of {self.t} (pid {orphan})", (self.d / "log").read_text())
        wait_for(lambda: self.status()["state"] == "done", "the job to finish", timeout=60)

    def test_only_renderers_of_exactly_this_topic_are_orphans(self):
        frames, topics = self.repo.frames, self.repo.root / "topics"

        def cmd(topic, root=topics):
            return (f"/v/bin/python -m blenderkc.render --topic {root}/{topic} --scene HT1_Hook --timing /t.json "
                    f"--frames 1 40 --out {frames}/{topic}/HT1_Hook/{'c3' * 8} --res 10 --samples 1 --device CPU")
        self.assertTrue(hostjob.is_renderer_of(cmd(self.t), self.repo, self.t))
        self.assertTrue(hostjob.is_renderer_of(cmd(self.t, Path("/old copy/topics")), self.repo, self.t))  # any copy of the code
        self.assertFalse(hostjob.is_renderer_of(cmd(self.t + "/nested"), self.repo, self.t))  # a topic under this one's path
        self.assertFalse(hostjob.is_renderer_of(cmd(self.t + "-2"), self.repo, self.t))
        self.assertFalse(hostjob.is_renderer_of(cmd(self.t).replace(str(frames), "/elsewhere/frames"), self.repo, self.t))

    def test_a_plate_made_again_is_not_ready_until_it_is_checked(self):
        self.start()
        wait_for(lambda: self.status()["state"] == "done", "the first run")
        plate = self.s.home / self.status()["scenes"][0]["plate"]
        plate.unlink()  # lost on the host
        self.start({"STUB_RENDER_FRAME_SLEEP": "0.1"})
        st = wait_for(lambda: (lambda x: x if (x.get("current") or {}).get("scene") == "HT1_Hook" else None)(self.status()),
                      "the plate to be made again")
        self.assertFalse(next(r for r in st["scenes"] if r["scene"] == "HT1_Hook")["done"])
        wait_for(lambda: self.status()["state"] == "done", "the second run", timeout=60)
        self.assertTrue(plate.is_file())


class LinkRetries(unittest.TestCase):
    """remote.Link with a shell standing in for ssh: fails N times, then answers."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.said, self.slept = [], []
        self.now = [0.0]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def link(self, patience=3600.0):
        def sleep(s):
            self.slept.append(s)
            self.now[0] += s
        return remote.Link("home", patience_s=patience, first_wait=15, max_wait=300, say=self.said.append,
                           sleep=sleep, clock=lambda: self.now[0])

    def flaky(self, fails, code=255, banner=True):
        n = self.tmp / "n"
        n.write_text(str(fails))
        body = (f'n=$(cat {n}); if [ "$n" -gt 0 ]; then echo $((n-1)) > {n}; '
                + (f"printf '%s' {json.dumps(BANNER)} >&2; " if banner else "")
                + f'echo "Connection to 100.85.87.87 port 22 timed out" >&2; exit {code}; fi; echo answered')
        return ["sh", "-c", body]

    def test_the_url_is_found_in_tailscales_banner(self):
        self.assertEqual(remote.tailscale_url(BANNER), "https://login.tailscale.com/a/l4e376293b10b4")
        self.assertIsNone(remote.tailscale_url("Connection to 100.85.87.87 port 22 timed out"))

    def test_failures_back_off_then_the_call_answers(self):
        link = self.link()
        self.assertEqual(link.call(self.flaky(11), "status", echo=False).strip(), "answered")
        self.assertEqual(self.slept, [15, 30, 60, 120, 240] + [300] * 6)
        logins = [s for s in self.said if s.startswith("the host needs a Tailscale login")]
        self.assertEqual(logins, ["the host needs a Tailscale login: https://login.tailscale.com/a/l4e376293b10b4"])
        reminders = [s for s in self.said if s.startswith("still waiting for the Tailscale login")]
        self.assertEqual(len(reminders), 1)  # the 11th failure, 1965 s in: half an hour after the first line
        self.assertEqual(self.said[-1], "home answers again")

    def test_a_new_outage_shows_the_url_again(self):
        link = self.link()
        link.call(self.flaky(1), "a", echo=False)
        link.call(self.flaky(1), "b", echo=False)
        self.assertEqual(sum(s.startswith("the host needs a Tailscale login") for s in self.said), 2)

    def test_the_commands_own_failure_is_not_retried(self):
        with self.assertRaises(CliError) as e:
            self.link().call(self.flaky(1, code=2, banner=False), "starting the render job", echo=False)
        self.assertIn("exit 2", str(e.exception))
        self.assertEqual(self.slept, [])

    def test_patience_runs_out(self):
        with self.assertRaises(CliError) as e:
            self.link(patience=100).call(self.flaky(50), "status", echo=False)
        self.assertIn("has not answered for", str(e.exception))
        self.assertEqual(sum(self.slept), 100)

    def test_a_timeout_stops_the_children_holding_its_pipes(self):
        n = self.tmp / "once"
        cmd = ["sh", "-c", f'if [ ! -f {n} ]; then touch {n}; sleep 30 & sleep 30; fi; echo answered']
        t0 = time.monotonic()
        self.assertEqual(self.link().call(cmd, "rsync", timeout=0.5, echo=False).strip(), "answered")
        self.assertLess(time.monotonic() - t0, 4)

    def test_a_hung_call_is_stopped_and_retried(self):
        n = self.tmp / "hung"
        cmd = ["sh", "-c", f'if [ ! -f {n} ]; then touch {n}; exec sleep 30; fi; echo answered']
        t0 = time.monotonic()
        self.assertEqual(self.link().call(cmd, "status", timeout=0.5).strip(), "answered")
        self.assertLess(time.monotonic() - t0, 10)
        self.assertTrue(any("no answer in 0s" in s or "no answer in 1s" in s for s in self.said), self.said)


if __name__ == "__main__":
    unittest.main()
