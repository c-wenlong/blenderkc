"""`blenderkc still <topic> [moment ...]`: render chosen instants at half
resolution with few samples, here, overlays included, tiled into one sheet.

A moment is one of:

    HB3_Wires           one still at the end of every line's voice in it
    HB3_Wires:12.5s     12.5 s into that scene
    HB3_Wires:120       frame 120 of that scene (1-based)
    95.2s               95.2 s into the whole video

With none, every scene (or those --scenes names) gets one still at the end of
each line's voice, the moment what the line describes should be on screen.

Timing (--timing): auto narrates when gnosia-audio is installed, else uses the
narration on disk, else the estimate (15.2 characters a second), so shots can
be looked at before any narration exists.

    <home>/stills/<topic>/<run>/s001_<Scene>_f0042.png ...   the stills
    <home>/stills/<topic>/<run>/stills.sheet.png             the sheet, in that order
    <home>/stills/<topic>/latest -> <run>                    the newest run

Each run writes into its own folder and its own scratch directory, so runs on
the same topic at the same time (several agents, two terminals) never delete
each other's stills. Runs older than a day are pruned, the newest ten always
kept.
"""

from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import media, runner
from .build import pick_scenes, settings_from
from .compose import overlay_cmd
from .repo import CliError, Repo, rel
from .timeline import scene_named, scene_timing, topic_timeline
from .topics import load_topic, resolve_topic

RUN_RE = re.compile(r"^\d{8}T\d{6}-\d+$")
KEEP_RUNS = 10
PRUNE_AFTER_S = 24 * 3600


def new_run_id() -> str:
    """A folder name no other run can share: the time, then this process's id."""
    return time.strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"


def prune_runs(topic_dir: Path, keep: str) -> None:
    """Delete run folders older than a day, never the newest KEEP_RUNS or `keep`.

    A run still rendering is younger than a day, so pruning cannot pull a
    folder out from under it.
    """
    # Two runs finishing together may both pick the same old folder, and one
    # removes it while the other looks: a folder that is gone is skipped.
    runs = []
    for d in topic_dir.iterdir():
        if RUN_RE.match(d.name):
            try:
                runs.append((d.stat().st_mtime, d))
            except FileNotFoundError:
                continue
    runs.sort(key=lambda x: x[0], reverse=True)
    now = time.time()
    for mtime, d in runs[KEEP_RUNS:]:
        if d.name != keep and now - mtime > PRUNE_AFTER_S:
            shutil.rmtree(d, ignore_errors=True)


def point_latest(topic_dir: Path, run: str) -> None:
    """Swap the `latest` link to this run in one rename, so it is never missing."""
    tmp = topic_dir / f".latest-{os.getpid()}"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(run, tmp)
    os.replace(tmp, topic_dir / "latest")


def voice_end_frames(scene: dict, fps: int) -> list[tuple[int, int]]:
    """(1-based frame, line index) at the end of each line's voice."""
    out = []
    for l in scene["lines"]:
        f = l["audioFrom"] + max(1, round(l["duration"] * fps))  # 0-based last voiced frame + 1
        out.append((min(max(1, f), scene["frames"]), l["index"]))
    return out


def parse_moments(tl: dict, moments: list[str], scenes: list[str]) -> list[tuple[str, int]]:
    """[(scene, 1-based frame)] in timeline order, without repeats."""
    fps = tl["fps"]
    picks: list[tuple[str, int]] = []
    if not moments:
        for s in tl["scenes"]:
            if s["scene"] in scenes:
                picks += [(s["scene"], f) for f, _ in voice_end_frames(s, fps)]
    for m in moments:
        if re.fullmatch(r"\d+(\.\d+)?s", m):
            t = float(m[:-1])
            f0 = round(t * fps)
            for s in tl["scenes"]:
                if s["from"] <= f0 < s["from"] + s["frames"]:
                    picks.append((s["scene"], f0 - s["from"] + 1))
                    break
            else:
                raise CliError(f"{m} is past the end ({tl['frames'] / fps:.1f}s)")
            continue
        name, _, at = m.partition(":")
        s = scene_named(tl, name)
        if not at:
            picks += [(name, f) for f, _ in voice_end_frames(s, fps)]
        elif re.fullmatch(r"\d+(\.\d+)?s", at):
            picks.append((name, min(s["frames"], round(float(at[:-1]) * fps) + 1)))
        elif at.isdigit() and 1 <= int(at) <= s["frames"]:
            picks.append((name, int(at)))
        else:
            raise CliError(f"{m!r}: after the colon, a time like 12.5s or a frame from 1 to {s['frames']}")
    order = {s["scene"]: i for i, s in enumerate(tl["scenes"])}
    return sorted(set(picks), key=lambda p: (order[p[0]], p[1]))


def main(args, repo: Repo) -> int:
    topic = resolve_topic(repo, args.topic)
    t = load_topic(repo, topic)
    settings = settings_from(args, res=50, samples=4)
    started = time.monotonic()
    tl, _ = topic_timeline(repo, topic, args.timing, quiet=True)
    names = pick_scenes(tl, args.scenes)
    picks = parse_moments(tl, args.moments, names)
    if not picks:
        raise CliError("no moments to render")
    python, src = repo.python()
    if not Path(python).exists():
        raise CliError(f"no Python at {python} ({src}): make deps")
    run = new_run_id()
    topic_dir = repo.stills_dir(topic)
    out_dir = topic_dir / run
    out_dir.mkdir(parents=True)
    work = repo.cache / "stills" / topic / run
    try:
        return _render_stills(args, repo, t, topic, tl, picks, python, settings, started, topic_dir, out_dir, run, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _render_stills(args, repo, t, topic, tl, picks, python, settings, started, topic_dir, out_dir, run, work):
    stills = []
    by_scene: dict[str, list[int]] = {}
    for sc, f in picks:
        by_scene.setdefault(sc, []).append(f)
    n = 0
    for sc, frames in by_scene.items():
        s = scene_named(tl, sc)
        if not t.shot_file(sc).is_file():
            raise CliError(f"{topic}: no shot script for {sc} ({rel(repo.root, t.shot_file(sc))})")
        raw, comp = work / sc / "raw", work / sc / "comp"
        raw.mkdir(parents=True)
        comp.mkdir(parents=True)
        tf = work / sc / "timing.json"
        tf.write_text(json.dumps(scene_timing(tl, s), indent=1, sort_keys=True) + "\n")
        for f in frames:
            runner.log(f"{sc}: frame {f} of {s['frames']}")
            runner.run_renderer(repo, runner.render_cmd(repo, python, t.dir, sc, tf, f, f, raw, settings), f"{sc} frame {f}")
        r = subprocess.run(overlay_cmd(python, raw, comp, min(frames), max(frames)), cwd=repo.root,
                           env=runner.lib_env(repo), stdout=sys.stderr, stderr=sys.stderr)
        if r.returncode != 0:
            raise CliError(f"{sc}: blenderkc.overlay failed (exit {r.returncode})")
        for f in frames:
            src_png = comp / media.frame_name(f)
            if not src_png.is_file():
                raise CliError(f"{sc}: no composited frame {f}")
            n += 1
            dst = out_dir / f"s{n:03d}_{sc}_f{f:04d}.png"
            shutil.copyfile(src_png, dst)
            line = next((l for l in reversed(s["lines"]) if f - 1 >= l["from"]), s["lines"][0])
            stills.append({"scene": sc, "frame": f, "seconds": round((s["from"] + f - 1) / tl["fps"], 2),
                           "file": str(dst), "line": {"index": line["index"], "text": line["text"]}})
    sheet = media.tile([Path(x["file"]) for x in stills], out_dir / "stills.sheet.png",
                       cols=4 if len(stills) > 9 else 3, work=work)
    point_latest(topic_dir, run)
    prune_runs(topic_dir, keep=run)
    out = {"topic": topic, "run": run, "timing": tl["source"], "settings": settings, "stills": stills,
           "sheet": str(sheet) if sheet else None, "seconds": round(time.monotonic() - started, 1)}
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    L = [f"{topic}: {len(stills)} stills at {settings['res']}%, {settings['samples']} samples, timing from {tl['source']}, in {out['seconds']}s"]
    for i, x in enumerate(stills, 1):
        text = x["line"]["text"]
        L.append(f"  {i:>3}. {x['scene']} f{x['frame']:<5} {x['seconds']:>8.2f}s  [{x['line']['index']}] {text[:69] + '…' if len(text) > 70 else text}")
    L += ["", f"sheet  {out['sheet']}", "       tiles in the order above, left to right; Read it and look before going on"]
    print("\n".join(L))
    return 0
