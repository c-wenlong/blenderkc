"""`blenderkc sheet <topic> [-n 12]`: a contact sheet of the built
deliverable, n evenly spaced frames four across, to look at in one Read."""

from __future__ import annotations

import json

from . import media
from .repo import CliError, Repo, probe_duration
from .topics import load_topic, resolve_topic


def main(args, repo: Repo) -> int:
    topic = resolve_topic(repo, args.topic)
    t = load_topic(repo, topic)
    t.require_buildable()
    mp4 = t.out_dir / t.block["output"]
    if not mp4.is_file():
        raise CliError(f"{mp4} is not built yet: blenderkc build {topic}")
    n = args.n
    if not 1 <= n <= 64:
        raise CliError("-n must be from 1 to 64")
    duration = probe_duration(mp4)
    if not duration:
        raise CliError(f"ffprobe cannot read {mp4}")
    out = media.video_sheet(mp4, repo.stills_dir(topic) / f"{t.stem}.sheet.png", duration=duration, n=n)
    if args.json:
        print(json.dumps({"topic": topic, "video": str(mp4), "duration": duration, "frames": n,
                          "every": round(duration / n, 2), "sheet": str(out)}, indent=1))
    else:
        print(f"{out}\n  {n} frames of {mp4}, one every {duration / n:.1f}s, four across; Read it and look.")
    return 0
