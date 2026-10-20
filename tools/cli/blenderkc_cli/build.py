"""`blenderkc build <topic> [--host home] [--scenes A,B]`: the deliverable.

    1. narrate             cheap when every clip is cached; --no-narrate reads
                           what `narrate` left and refuses when it is stale
    2. plan                each scene's timing file, plate key and clip key
                           (keys.py); a scene whose clip exists is done
    3. render              scenes with neither clip nor plate: a render job
                           (runner.py) here, or one detached job on --host
                           (remote.py, hostjob.py) that survives a dropped
                           connection; rerunning build reattaches to it
    4. composite           overlays onto each plate, one clip per scene
                           (compose.py), always here
    5. assemble            stream-copy the clips, lay the narration clips at
                           their frames, master (-16 LUFS, 48 kHz AAC, moov
                           first), check, publish
    6. sidecars and sheet  transcript, chapters and VTT from gnosia-audio
                           (`sidecars --json`), from the timeline actually
                           rendered; a contact sheet to look at

With --scenes, or at --res below 100, steps 3 and 4 run for those scenes and
step 5 writes a draft, <home>/stills/<topic>/<name>.draft.mp4, and never the
deliverable. A full build writes <home>/out/<topic>/<name>.mp4 and appends a
line to <home>/state/builds.jsonl.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from . import compose, keys, media, remote, runner
from .audio import call_gnosia
from .locks import acquire
from .narrate import narrate_topic, narrated_state, read_narration
from .repo import FPS, CliError, Repo, probe_duration, read_json, rel, sha256_file, write_json
from .timeline import from_disk, scene_timing, sidecar_request
from .topics import load_topic, resolve_topic, sidecar_paths

DEFAULT_SAMPLES = 16


def settings_from(args, *, res: int = 100, samples: int = DEFAULT_SAMPLES) -> dict:
    r = int(args.res) if getattr(args, "res", None) is not None else res
    s = int(args.samples) if getattr(args, "samples", None) is not None else samples
    if not 1 <= r <= 100:
        raise CliError("--res must be a percentage from 1 to 100")
    if s < 1:
        raise CliError("--samples must be 1 or more")
    dev = (getattr(args, "device", None) or "auto")
    if dev not in ("auto", "METAL", "OPTIX", "CUDA", "CPU"):
        raise CliError("--device must be auto, METAL, OPTIX, CUDA or CPU")
    return {"res": r, "samples": s, "device": dev, "fps": FPS}


def pick_scenes(tl: dict, spec: str | None) -> list[str]:
    every = [s["scene"] for s in tl["scenes"]]
    if not spec:
        return every
    want = [x.strip() for x in spec.split(",") if x.strip()]
    unknown = [w for w in want if w not in every]
    if unknown:
        raise CliError(f"no scene {', '.join(unknown)} in narration.json; it has {', '.join(every)}")
    return [s for s in every if s in want]


def sub_timeline(tl: dict, names: list[str]) -> dict:
    """The timeline of some scenes only, back to back from frame 0."""
    at, scenes = 0, []
    for s in tl["scenes"]:
        if s["scene"] in names:
            scenes.append({**s, "from": at})
            at += s["frames"]
    return {**tl, "scenes": scenes, "frames": at}


def frame_size(res: int) -> tuple[int, int]:
    """Blender's output size at a resolution percentage of 1920x1080."""
    return 1920 * res // 100, 1080 * res // 100


def find_reusable_plate(repo: Repo, topic: str, scene: str, frames: int, res: int) -> str | None:
    """--reuse-plates: the newest plate of this scene with the right length
    and the size this build renders at, whatever its key; returns its short
    key. A 50% draft's plate is never taken for a full build."""
    d = repo.plates / topic
    if not d.is_dir():
        return None
    want = frame_size(res)
    for p in sorted(d.glob(f"{scene}.*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
        v = next((x for x in media.probe_streams(p) if x.get("codec_type") == "video"), {})
        if (v.get("width"), v.get("height")) == want and media.count_frames(p) == frames:
            return p.name[len(scene) + 1:-len(".mp4")]
    return None


def snapshot_clips(repo: Repo, tl: dict, work: Path) -> Path:
    """Hard links to every clip this build's timeline names, under the work
    folder, laid out as public/ is. A `narrate` or `still` run while this
    build renders may remove clips from public/; the build assembles from its
    own links, so it never loses one."""
    snap = work / "public"
    for s in tl["scenes"]:
        for l in s["lines"]:
            if not l.get("audio"):
                continue
            src, dst = repo.public / l["audio"], snap / l["audio"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.unlink()
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
    return snap


def plan(repo: Repo, topic: str, tl: dict, settings: dict, names: list[str], *, reuse_plates: bool = False) -> list[dict]:
    t = load_topic(repo, topic)
    every = [s["scene"] for s in tl["scenes"]]
    tdir = repo.cache / "build" / topic / "timing"
    tdir.mkdir(parents=True, exist_ok=True)
    out = []
    for s in tl["scenes"]:
        if s["scene"] not in names:
            continue
        script = t.shot_file(s["scene"])
        if not script.is_file():
            raise CliError(f"{topic}: no shot script for {s['scene']} ({rel(repo.root, script)}); `blenderkc new` writes a stub")
        timing = scene_timing(tl, s)
        pkey = keys.plate_key(repo.root, t.dir, s["scene"], every, t.block["shots"], timing, settings)
        plate = runner.plate_path(repo, topic, s["scene"], pkey)
        reused = None
        if reuse_plates and not plate.is_file():
            short = find_reusable_plate(repo, topic, s["scene"], s["frames"], settings["res"])
            if short:
                reused = short
                plate = repo.plates / topic / f"{s['scene']}.{short}.mp4"
        ckey = keys.clip_key(repo.root, t.dir, s["scene"], t.block["shots"], reused or pkey)
        tf = tdir / f"{s['scene']}.{keys.short(pkey)}.json"
        tf.write_text(json.dumps(timing, indent=1, sort_keys=True) + "\n")
        out.append({"scene": s["scene"], "frames": s["frames"], "timing": timing, "timingFile": tf,
                    "plateKey": pkey, "plate": plate, "reused": reused,
                    "clipKey": ckey, "clip": compose.clip_path(repo, topic, s["scene"], ckey)})
    return out


def render_job(topic: str, t, settings: dict, todo: list[dict], keep_frames: bool) -> dict:
    return {"schema": runner.JOB_SCHEMA, "topic": topic, "shots": t.block["shots"], "fps": settings["fps"],
            "settings": {k: settings[k] for k in ("res", "samples", "device")}, "keepFrames": keep_frames,
            "scenes": [{"scene": p["scene"], "frames": p["frames"], "plate": p["plateKey"], "timing": p["timing"]}
                       for p in todo]}


def save_timeline(work: Path, stem: str) -> Path:
    return work / f"{stem}.timeline.json"


def write_sidecars(repo: Repo, out_dir: Path, stem: str, tl: dict, name: str, quiet: bool) -> dict:
    return call_gnosia(repo, "sidecars", sidecar_request(out_dir, stem, tl, name), quiet=quiet, config=False)["paths"]


def assemble(repo: Repo, tl: dict, clips: list[Path], work: Path, tag: str, public: Path) -> Path:
    """Clips + narration -> a mastered, checked candidate in the work folder."""
    video = work / f"video.{tag}.mp4"
    media.concat(clips, video, work)
    n = media.count_frames(video)
    if n != tl["frames"]:
        raise CliError(f"the joined clips have {n} frames, the timeline {tl['frames']}")
    wav = work / f"narration.{tag}.wav"
    media.narration_track(tl, public, wav, work)
    cand = work / f"mastered.{tag}.mp4"
    media.master(video, wav, cand)
    video.unlink(missing_ok=True)
    wav.unlink(missing_ok=True)
    streams = media.probe_streams(cand)
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise CliError(f"{cand} has no audio stream")
    if not media.is_faststart(cand):
        raise CliError(f"{cand} has its moov atom after the media data")
    return cand


def build(repo: Repo, args) -> dict:
    topic = resolve_topic(repo, args.topic)
    t = load_topic(repo, topic)
    t.require_buildable()
    media.require_ffmpeg()
    settings = settings_from(args)
    quiet = bool(args.json)
    started = time.monotonic()
    release = acquire(repo.cache / "build" / topic / ".lock")
    phases: dict[str, float] = {}
    public: Path | None = None
    clock = [time.monotonic()]

    def phase(name: str) -> None:
        now = time.monotonic()
        phases[name] = round(phases.get(name, 0) + now - clock[0], 1)
        clock[0] = now

    try:
        narration = narrated_state(repo, topic) if args.no_narrate else narrate_topic(repo, topic, quiet=quiet)
        runner.log(f"{'narration on disk' if args.no_narrate else 'narrated'}: {narration['lines']} lines, {narration['narratedSeconds']}s")
        tl = from_disk(repo, topic, read_narration(t.dir))
        work = repo.cache / "build" / topic
        tag = f"{os.getpid()}-{int(time.time())}"
        public = work / f"snapshot.{tag}" / "public"  # set first, so a failed snapshot is cleaned up too
        snapshot_clips(repo, tl, public.parent)
        phase("narrate")
        names = pick_scenes(tl, args.scenes)
        draft = names != [s["scene"] for s in tl["scenes"]] or settings["res"] != 100
        steps = plan(repo, topic, tl, settings, names, reuse_plates=args.reuse_plates)

        todo = [p for p in steps if not p["clip"].is_file() and not p["plate"].is_file()]
        render_reply = None
        if todo:
            job = render_job(topic, t, settings, todo, args.keep_frames)
            render_reply = (remote.render_remote(repo, args.host, job, retry_hours=getattr(args, "retry_hours", None))
                            if args.host else runner.run_job(repo, job))
            missing = [p["scene"] for p in todo if not p["plate"].is_file()]
            if missing:
                raise CliError(f"no plate came back for {', '.join(missing)}")
        phase("render")
        composed = []
        for i, p in enumerate(steps, 1):
            if p["clip"].is_file():
                continue
            runner.log(f"[{i}/{len(steps)}] {p['scene']}: compositing overlays onto the plate")
            info = compose.composite_scene(repo, topic=topic, scene=p["scene"], frames=p["frames"], fps=settings["fps"],
                                           plate=p["plate"], clip=p["clip"], timing=p["timingFile"], settings=settings,
                                           work=repo.cache / "build" / topic / "compose" / p["scene"])
            composed.append({"scene": p["scene"], **info})
        phase("composite")

        stem = t.stem
        use = sub_timeline(tl, names) if draft else tl
        cand = assemble(repo, use, [p["clip"] for p in steps], work, tag, public)
        phase("assemble")
        base = {"topic": topic, "frames": use["frames"], "fps": use["fps"], "settings": settings, "host": args.host,
                "rendered": [x["scene"] for x in (render_reply or {}).get("plates", []) if not x.get("cached")],
                "composited": [c["scene"] for c in composed], "reusedPlates": [p["scene"] for p in steps if p["reused"]],
                "phases": phases}
        if draft:
            out = repo.stills_dir(topic) / f"{stem}.draft.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            os.replace(cand, out)
            return {**base, "draft": True, "scenes": names, "path": str(out), "duration": probe_duration(out),
                    "sidecars": None, "totalSeconds": round(time.monotonic() - started, 1)}

        written = None if args.no_narrate else write_sidecars(repo, work, stem, tl, f"{stem}.{tag}", quiet)
        phase("sidecars")
        if not written:
            tmp = work / f"{stem}.{tag}.timeline.json"
            write_json(tmp, {"topic": topic, "stem": stem, "builtAt": datetime.now(timezone.utc).isoformat(),
                             "video": {"sha256": sha256_file(cand)}, "timeline": tl})
            os.replace(tmp, save_timeline(work, stem))
        t.out_dir.mkdir(parents=True, exist_ok=True)
        mp4 = t.out_dir / t.block["output"]
        os.replace(cand, mp4)
        side = sidecar_paths(t.out_dir, stem)
        if written:
            for k, p in side.items():
                os.replace(written[k], p)
            save_timeline(work, stem).unlink(missing_ok=True)
        else:
            for p in side.values():
                p.unlink(missing_ok=True)
        duration = probe_duration(mp4)
        sheet = media.video_sheet(mp4, repo.stills_dir(topic) / f"{stem}.sheet.png", duration=duration or tl["frames"] / tl["fps"])
        record = {"at": datetime.now(timezone.utc).isoformat(), **base, "output": rel(repo.home, mp4),
                  "duration": duration, "faststart": media.is_faststart(mp4), "words": narration["words"]["source"],
                  "totalSeconds": round(time.monotonic() - started, 1)}
        repo.state.mkdir(parents=True, exist_ok=True)
        with open(repo.state / "builds.jsonl", "a") as fh:
            fh.write(json.dumps(record) + "\n")
        return {**record, "draft": False, "path": str(mp4), "streams": media.probe_streams(mp4), "sheet": str(sheet),
                "sidecars": {k: rel(repo.home, p) for k, p in side.items()} if written else None,
                **({} if written else {"timeline": rel(repo.home, save_timeline(work, stem))})}
    finally:
        if public is not None and public.parent.exists():
            shutil.rmtree(public.parent, ignore_errors=True)
        release()


def main(args, repo: Repo) -> int:
    r = build(repo, args)
    if args.json:
        print(json.dumps(r, indent=1, default=str))
        return 0
    L = [f"{'draft' if r['draft'] else 'built'} {r['path']}",
         f"  {r['duration']}s, {r['frames']} frames at {r['fps']} fps; {r['settings']['res']}%, {r['settings']['samples']} samples"
         f"{' on ' + r['host'] if r['host'] else ''}",
         f"  rendered {', '.join(r['rendered']) or 'nothing (all cached)'}; composited {', '.join(r['composited']) or 'nothing (all cached)'}",
         f"  seconds: {', '.join(f'{k} {v}' for k, v in r['phases'].items())}"]
    if r["reusedPlates"]:
        L.append(f"  reused older plates for {', '.join(r['reusedPlates'])} (--reuse-plates)")
    if r["draft"]:
        L.append("  a draft: the deliverable was not touched (it needs every scene at --res 100)")
    else:
        v = next((s for s in r["streams"] if s.get("codec_type") == "video"), {})
        a = next((s for s in r["streams"] if s.get("codec_type") == "audio"), {})
        L.append(f"  {v.get('codec_name')} {v.get('width')}x{v.get('height')}, {a.get('codec_name')} {a.get('sample_rate')} Hz, faststart {'yes' if r['faststart'] else 'NO'}")
        L.append(f"  sidecars {', '.join(r['sidecars'].values())}" if r["sidecars"]
                 else f"  sidecars not written (--no-narrate); run `blenderkc sidecars {r['topic']}` where gnosia-audio runs")
        L.append(f"  sheet {r['sheet']}")
    L.append(f"  {r['totalSeconds']}s in all. Look at it before calling it done.")
    print("\n".join(L))
    return 0


def sidecars_main(args, repo: Repo) -> int:
    """`blenderkc sidecars <topic>`: the transcript, chapters and VTT for a
    deliverable built with --no-narrate, from the timeline that build kept,
    refused for any video but the one it was rendered for."""
    topic = resolve_topic(repo, args.topic)
    t = load_topic(repo, topic)
    t.require_buildable()
    release = acquire(repo.cache / "build" / topic / ".lock")
    try:
        work = repo.cache / "build" / topic
        stem = t.stem
        saved = read_json(save_timeline(work, stem), None)
        if not saved or not saved.get("timeline"):
            raise CliError(f"{topic}: no saved timeline to write sidecars from; run `blenderkc build {topic} --no-narrate` first (a plain build writes its own sidecars)")
        mp4 = t.out_dir / t.block["output"]
        if not mp4.is_file() or sha256_file(mp4) != (saved.get("video") or {}).get("sha256"):
            raise CliError(f"{topic}: {rel(repo.home, mp4)} is not the video the saved timeline was rendered for; build it again")
        tag = f"{os.getpid()}-{int(time.time())}"
        written = write_sidecars(repo, work, stem, saved["timeline"], f"{stem}.{tag}", bool(args.json))
        side = sidecar_paths(t.out_dir, stem)
        for k, p in side.items():
            os.replace(written[k], p)
        save_timeline(work, stem).unlink(missing_ok=True)
        out = {"topic": topic, "sidecars": {k: rel(repo.home, p) for k, p in side.items()}}
        print(json.dumps(out, indent=1) if args.json else f"wrote {', '.join(out['sidecars'].values())}")
        return 0
    finally:
        release()
