"""Render one scene's frames.

    <venv python> -m blenderkc.render --topic <dir> --scene <SceneName>
        [--timing <scene-timing.json>] [--frames <a> <b>] --out <dir>
        [--res 100] [--samples 16] [--device auto|METAL|OPTIX|CUDA|CPU]
        [--overlays-only] [--save-blend <file>]

Resets Blender, imports <topic>/shots/<SceneName>.py and calls build(shot),
then writes <out>/f_0001.png ... (1-based frame numbers within the scene,
frame f at t = (f - 1) / fps), skipping frames that already exist, and
<out>/shot.json: every anchor's screen position per frame (pixels at 100%) and
the overlay records, for blenderkc.overlay. --overlays-only writes shot.json
and renders nothing. Without --timing the scene is timed from narration.json
by estimate (15.2 characters a second).
"""
import argparse
import importlib.util
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bpy  # noqa: E402
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402

from blenderkc import timing as T  # noqa: E402
from blenderkc.points import point_of  # noqa: E402
from blenderkc.project import to_pixels  # noqa: E402
from blenderkc.shot import Shot  # noqa: E402

LOOK = "AgX - Medium High Contrast"
SEED = 0


def _parse(argv):
    ap = argparse.ArgumentParser(prog="python -m blenderkc.render", description=__doc__.split("\n\n")[0])
    ap.add_argument("--topic", required=True, help="topic folder (holds narration.json and shots/)")
    ap.add_argument("--scene", required=True)
    ap.add_argument("--timing", help="scene timing JSON from the pipeline; default: estimate from narration.json")
    ap.add_argument("--frames", type=int, nargs=2, metavar=("A", "B"), help="1-based inclusive range; default all")
    ap.add_argument("--out", required=True)
    ap.add_argument("--res", type=int, default=100, help="resolution percentage of 1920x1080")
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--device", default="auto", choices=["auto", "METAL", "OPTIX", "CUDA", "HIP", "ONEAPI", "CPU"])
    ap.add_argument("--overlays-only", action="store_true", help="write shot.json, render no frames")
    ap.add_argument("--save-blend", help="also save the built scene as a .blend (debugging)")
    return ap.parse_args(argv)


def _shots_dir(topic):
    """narrated.shots from topic.yml (a plain `shots: <dir>` line), else shots/."""
    p = os.path.join(topic, "topic.yml")
    if os.path.exists(p):
        with open(p) as fh:
            lines = fh.read().splitlines()
        for raw in lines:
            s = raw.strip()
            if s.startswith("shots:"):
                v = s.split(":", 1)[1].split("#")[0].strip().strip("'\"")
                if v:
                    return os.path.join(topic, v)
    return os.path.join(topic, "shots")


def _load_timing(args):
    if args.timing:
        return T.Timing.load(args.timing)
    nar = os.path.join(args.topic, "narration.json")
    if not os.path.exists(nar):
        raise SystemExit(f"render: no --timing and no {nar}")
    return T.Timing(T.timing_from_narration(nar, args.scene))


def _reset_scene(timing, res):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for h in list(bpy.app.handlers.frame_change_pre):
        bpy.app.handlers.frame_change_pre.remove(h)
    scene = bpy.context.scene
    scene.render.fps = timing.fps
    scene.render.fps_base = 1.0
    scene.frame_start, scene.frame_end = 1, timing.frames
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    scene.render.resolution_percentage = res
    return scene


def _build(args, timing):
    scene = _reset_scene(timing, args.res)
    shot = Shot(timing, topic_dir=os.path.abspath(args.topic))
    path = os.path.join(_shots_dir(args.topic), args.scene + ".py")
    if not os.path.exists(path):
        raise SystemExit(f"render: no shot script {path}")
    spec = importlib.util.spec_from_file_location(f"blenderkc_shot_{args.scene}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "build"):
        raise SystemExit(f"render: {path} defines no build(shot)")
    mod.build(shot)
    if scene.camera is None:
        shot.camera  # a default framing rather than a black frame

    def handler(sc, *_):
        shot.apply(shot.time(sc.frame_current))

    bpy.app.handlers.frame_change_pre.append(handler)
    return scene, shot


def _pick_device(want):
    """(device type, use OptiX denoiser). auto: OPTIX on NVIDIA, METAL on a
    Mac, then CUDA, HIP, oneAPI, else CPU."""
    prefs = bpy.context.preferences.addons["cycles"].preferences
    order = ["OPTIX", "METAL", "CUDA", "HIP", "ONEAPI"] if want == "auto" else [want]
    for kind in order:
        if kind == "CPU":
            break
        try:
            prefs.compute_device_type = kind
        except TypeError:
            continue
        prefs.get_devices()
        devs = [d for d in prefs.devices if d.type == kind]
        if devs:
            for d in prefs.devices:
                d.use = d.type == kind
            return kind
    if want not in ("auto", "CPU"):
        print(f"render: no {want} device; rendering on the CPU", file=sys.stderr)
    prefs.compute_device_type = "NONE"
    return "CPU"


def _configure(scene, args):
    scene.render.engine = "CYCLES"
    kind = _pick_device(args.device)
    c = scene.cycles
    c.device = "CPU" if kind == "CPU" else "GPU"
    c.samples = args.samples
    c.use_adaptive_sampling = True
    c.adaptive_threshold = 0.02
    c.use_denoising = True
    c.denoiser = "OPENIMAGEDENOISE"
    if kind == "OPTIX":
        c.denoiser = "OPTIX"
    elif kind != "CPU" and hasattr(c, "denoising_use_gpu"):
        c.denoising_use_gpu = True
    c.seed = SEED
    c.use_animated_seed = False
    c.max_bounces = 8
    c.transmission_bounces = 8
    c.volume_bounces = 1
    c.caustics_reflective = False
    c.caustics_refractive = False
    scene.render.use_persistent_data = True
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = LOOK
    im = scene.render.image_settings
    im.file_format = "PNG"
    im.color_mode = "RGB"
    im.color_depth = "8"
    im.compression = 15
    return kind


def _frame_range(args, timing):
    a, b = args.frames if args.frames else (1, timing.frames)
    a, b = max(1, a), min(timing.frames, b)
    if a > b:
        raise SystemExit(f"render: empty frame range {args.frames} (scene has {timing.frames} frames)")
    return a, b


def _anchors_for(scene, shot, a, b):
    """{name: {frame: [x, y, visible]}} for frames a..b, pixels at 100%."""
    out = {name: {} for name in shot.anchors}
    if not shot.anchors:
        return out
    w, h = scene.render.resolution_x, scene.render.resolution_y
    for f in range(a, b + 1):
        scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        cam = scene.camera.evaluated_get(dg)
        for name, (target, offset) in shot.anchors.items():
            p = point_of(target, update=False)
            if any(offset):
                p = p + type(p)(offset)
            out[name][f] = to_pixels(tuple(world_to_camera_view(scene, cam, p)), w, h)
    return out


def _write_shot_json(out_dir, scene, shot, anchors):
    """Merge this run's anchors into shot.json under a lock, so chunks of
    one scene rendered at the same time into one --out keep each other's."""
    import fcntl

    with open(os.path.join(out_dir, ".shot.json.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _merge_shot_json(out_dir, scene, shot, anchors)


def _merge_shot_json(out_dir, scene, shot, anchors):
    path = os.path.join(out_dir, "shot.json")
    old = None
    if os.path.exists(path):
        try:
            with open(path) as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            old = None
    n = shot.frames
    merged = {}
    for name, per in anchors.items():
        prev = (old or {}).get("anchors", {}).get(name) if old and old.get("frames") == n else None
        lst = prev if isinstance(prev, list) and len(prev) == n else [None] * n
        for f, v in per.items():
            lst[f - 1] = v
        merged[name] = lst
    doc = {
        "scene": shot.scene, "fps": shot.fps, "frames": n, "duration": shot.duration,
        "width": scene.render.resolution_x, "height": scene.render.resolution_y,
        "timing": shot.timing.source, "anchors": merged, "overlays": shot.records(),
    }
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, path)
    return path


def _render_frames(scene, out_dir, a, b):
    done = skipped = 0
    t_all = time.time()
    for f in range(a, b + 1):
        dst = os.path.join(out_dir, f"f_{f:04d}.png")
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            skipped += 1
            continue
        t0 = time.time()
        scene.frame_set(f)
        tmp = os.path.join(out_dir, f".f_{f:04d}.tmp.png")
        scene.render.filepath = tmp
        bpy.ops.render.render(write_still=True)
        os.replace(tmp, dst)
        done += 1
        print(f"blenderkc: frame {f} {time.time() - t0:.2f}s", flush=True)
    print(f"blenderkc: rendered {done}, skipped {skipped}, {time.time() - t_all:.1f}s", flush=True)


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)
    timing = _load_timing(args)
    os.makedirs(args.out, exist_ok=True)
    scene, shot = _build(args, timing)
    a, b = _frame_range(args, timing)
    kind = _configure(scene, args)
    anchors = _anchors_for(scene, shot, a, b)
    _write_shot_json(args.out, scene, shot, anchors)
    if args.save_blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save_blend))
    if args.overlays_only:
        print(f"blenderkc: wrote {os.path.join(args.out, 'shot.json')}", flush=True)
        return 0
    print(f"blenderkc: {shot.scene} frames {a}-{b} of {timing.frames} on {kind}, "
          f"{args.samples} samples, {args.res}% ({timing.source} timing)", flush=True)
    _render_frames(scene, args.out, a, b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
