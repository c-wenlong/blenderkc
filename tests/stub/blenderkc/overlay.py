"""A stand-in for blenderkc.overlay with the same command line: copies each
frame of the range to --out (where the real one draws the overlays)."""

import argparse
import os
import shutil
import sys


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--frames", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--from", dest="first", type=int)
    p.add_argument("--to", dest="last", type=int)
    p.add_argument("--state")
    a = p.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    for name in sorted(os.listdir(a.frames)):
        if not (name.startswith("f_") and name.endswith(".png")):
            continue
        n = int(name[2:-4])
        if (a.first and n < a.first) or (a.last and n > a.last):
            continue
        shutil.copyfile(os.path.join(a.frames, name), os.path.join(a.out, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
