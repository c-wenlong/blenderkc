"""From a plate to a finished scene clip, always on this machine: the 2D half.

    1. `<python> -m blenderkc.render ... --overlays-only` builds the scene and
       writes shot.json (overlays and per-frame anchors), rendering nothing
    2. a scene with no overlays: the plate is transcoded to the clip
    3. otherwise, in chunks of CHUNK frames: decode the chunk to PNGs, run
       `<python> -m blenderkc.overlay --frames <in> --out <out> --from a --to b`,
       encode the chunk; then stream-copy the chunks into the clip

Chunks keep the PNGs on disk to a few GB whatever the scene's length. The
clip is filed under its clip key, so a scene whose shot script, overlay code
and plate are unchanged is never composited twice:

    <home>/cache/clips/<topic>/<Scene>.<key16>.mp4
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import keys, media, runner
from .repo import CliError, Repo

CHUNK = 900


def clip_path(repo: Repo, topic: str, scene: str, key: str) -> Path:
    return repo.cache / "clips" / topic / f"{scene}.{keys.short(key)}.mp4"


def overlay_cmd(python: str, frames_in: Path, frames_out: Path, first: int, last: int,
                state: Path | None = None) -> list[str]:
    """The compositor's command line. `state` keeps each overlay's ink (light
    or dark, chosen from the frame the first time it is drawn) across the
    chunks of one scene, so a label never changes colour at a chunk boundary."""
    cmd = [python, "-m", "blenderkc.overlay", "--frames", str(frames_in), "--out", str(frames_out),
           "--from", str(first), "--to", str(last)]
    if state is not None:
        cmd += ["--state", str(state)]
    return cmd


def shot_overlays(repo: Repo, topic: str, scene: str, timing: Path, frames: int, settings: dict, work: Path) -> Path:
    """Runs the renderer in --overlays-only mode; returns shot.json's path."""
    python, _ = repo.python()
    d = work / "shot"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    runner.run_renderer(repo, runner.render_cmd(repo, python, repo.topics_dir / topic, scene, timing, 1, frames, d,
                                                settings, overlays_only=True), f"{scene} (overlays)")
    f = d / "shot.json"
    if not f.is_file():
        raise CliError(f"{scene}: the renderer wrote no shot.json in --overlays-only mode")
    return f


def composite_scene(repo: Repo, *, topic: str, scene: str, frames: int, fps: int, plate: Path, clip: Path,
                    timing: Path, settings: dict, work: Path) -> dict:
    python, _ = repo.python()
    work.mkdir(parents=True, exist_ok=True)
    shot = shot_overlays(repo, topic, scene, timing, frames, settings, work)
    overlays = (json.loads(shot.read_text()).get("overlays") or [])
    if not overlays:
        media.transcode_clip(plate, clip, fps=fps)
        return {"overlays": 0, "chunks": 0}
    chunk = int(repo.setting("BLENDERKC_COMPOSE_CHUNK") or CHUNK)  # the tests make it small
    state = work / "overlay-state.json"
    state.unlink(missing_ok=True)  # chosen afresh for this plate
    parts = []
    for i, a in enumerate(range(1, frames + 1, chunk)):
        b = min(frames, a + chunk - 1)
        fin, fout = work / "in", work / "out"
        for d in (fin, fout):
            shutil.rmtree(d, ignore_errors=True)
        media.decode_range(plate, a, b, fin, fps=fps)
        got = runner.missing_frames(fin, a, b)
        if got:
            raise CliError(f"{scene}: decoding frames {a}..{b} of the plate left {len(got)} missing (first {got[0]})")
        shutil.copyfile(shot, fin / "shot.json")
        fout.mkdir()
        r = subprocess.run(overlay_cmd(python, fin, fout, a, b, state), cwd=repo.root, env=runner.lib_env(repo),
                           stdout=sys.stderr, stderr=sys.stderr)
        if r.returncode != 0:
            raise CliError(f"{scene}: blenderkc.overlay failed on frames {a}..{b} (exit {r.returncode})")
        part = work / f"part_{i:03d}.mp4"
        media.encode_frames(fout, part, fps=fps, start=a, count=b - a + 1, plate=False)
        parts.append(part)
        shutil.rmtree(fin, ignore_errors=True)
        shutil.rmtree(fout, ignore_errors=True)
    clip.parent.mkdir(parents=True, exist_ok=True)
    media.concat(parts, clip, work)
    for p in parts:
        p.unlink(missing_ok=True)
    return {"overlays": len(overlays), "chunks": len(parts)}
