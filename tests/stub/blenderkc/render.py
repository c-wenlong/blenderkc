"""A stand-in for blenderkc.render with the same command line, standard
library only: the CLI tests (and `build --host` with the renderer stubbed)
run the whole pipeline without bpy. Each frame is a flat colour that moves
with the frame number, written as a real PNG; shot.json carries one label
when the shot script calls `shot.label(...)`, so the overlay path runs too."""

import argparse
import json
import os
import struct
import sys
import zlib


def png(path, w, h, rgb):
    row = b"\x00" + bytes(rgb) * w
    raw = row * h

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--topic", required=True)
    p.add_argument("--scene", required=True)
    p.add_argument("--timing", required=True)
    p.add_argument("--frames", nargs=2, type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--res", type=int, default=100)
    p.add_argument("--samples", type=int, default=16)
    p.add_argument("--device", default="auto")
    p.add_argument("--overlays-only", action="store_true")
    a = p.parse_args(argv)
    timing = json.load(open(a.timing))
    shot = open(os.path.join(a.topic, "shots", a.scene + ".py")).read()
    os.makedirs(a.out, exist_ok=True)
    w, h = 1920 * a.res // 100, 1080 * a.res // 100  # Blender's sizes: odd at 41%
    overlays = [{"kind": "label", "text": "stub", "start": 0, "end": timing["duration"]}] if ".label(" in shot else []
    json.dump({"scene": a.scene, "fps": timing["fps"], "frames": timing["frames"], "width": 1920, "height": 1080,
               "anchors": {}, "overlays": overlays}, open(os.path.join(a.out, "shot.json"), "w"))
    log = os.environ.get("STUB_RENDER_LOG")
    if log:
        with open(log, "a") as f:
            f.write(f"{a.scene} {a.frames[0]} {a.frames[1]} {'overlays' if a.overlays_only else 'frames'} {a.res} {a.samples}\n")
    if a.overlays_only:
        return 0
    if os.environ.get("STUB_RENDER_SLEEP"):  # hold the render open, so two runs overlap
        import time
        time.sleep(float(os.environ["STUB_RENDER_SLEEP"]))
    if os.environ.get("STUB_RENDER_FAIL") == a.scene:
        print("stub: failing on purpose", file=sys.stderr)
        return 3
    pause = float(os.environ.get("STUB_RENDER_FRAME_SLEEP") or 0)  # a frame at a time, so a render can be cut mid-scene
    for n in range(a.frames[0], a.frames[1] + 1):
        f = os.path.join(a.out, f"f_{n:04d}.png")
        if not os.path.exists(f):
            if pause:
                import time
                time.sleep(pause)
            png(f, w, h, ((n * 7) % 256, 80, 160))
    return 0


if __name__ == "__main__":
    sys.exit(main())
