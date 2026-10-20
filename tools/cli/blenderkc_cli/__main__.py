"""Entry point: `python3 tools/cli/blenderkc ...` (the shim) or
`python3 tools/cli/blenderkc_cli ...`. Global --root goes before the verb."""

from __future__ import annotations

import argparse
import sys

if __package__ in (None, ""):
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "blenderkc_cli"  # noqa: A001

from . import __version__
from .repo import CliError, Repo, find_root

VERBS = {
    "doctor": "check the venv (bpy, Pillow), the library, ffmpeg, gnosia-audio, and with --host the render host",
    "home": "where generated data lives ($BLENDERKC_HOME), every path under it, and the host layout",
    "api": "the rules, then every blenderkc/ export with its doc and a real usage line from topics/",
    "new": "scaffold a topic: topic.yml with a free prefix, narration.json, shots/ stubs that render",
    "narrate": "voice narration.json with gnosia-audio, stage the clips, write the manifest and word timings",
    "still": "render chosen instants at half resolution with few samples, here, tiled into a sheet",
    "build": "narrate, render (here or --host), composite, encode, join, master, sidecars, sheet",
    "build-status": "how a topic's detached render job is going: scenes done, the frame, seconds a frame, ETA",
    "sidecars": "write the transcript, chapters and VTT for a deliverable built with --no-narrate",
    "sheet": "a contact sheet of the built deliverable, to look at",
    "topics": "every topic.yml as data (manimkc's report shape), with what is rendered and what drifted",
    "render-job": "internal: run a render job (what build runs here)",
    "host-job": "internal: start, run or report the detached render job on a build --host host",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="blenderkc", description="Narrated Blender (Cycles) explainers, one folder per topic.")
    p.add_argument("--root", help="the checkout (default: $BLENDERKC_ROOT, then walk up from cwd)")
    p.add_argument("--version", action="version", version=f"blenderkc {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<verb>")
    sub.required = True
    v = {k: sub.add_parser(k, help=h, description=h) for k, h in VERBS.items()}

    v["doctor"].add_argument("--host", help="also check this ssh host for build --host")
    for k in ("doctor", "home", "api", "new", "narrate", "still", "build", "build-status", "sidecars", "sheet", "topics",
              "render-job"):
        v[k].add_argument("--json", action="store_true", help="the report as JSON")
    v["api"].add_argument("names", nargs="*", help="only these exports (a name, Class.method or module)")
    n = v["new"]
    n.add_argument("path", help="domain/[subdomain/]slug in kebab-case")
    n.add_argument("--topic", required=True, help="the catalog topic slug (required)")
    n.add_argument("--title")
    n.add_argument("--prefix", help="scene-name prefix (default: a free one from the slug)")
    n.add_argument("--scenes", help="comma-separated scene names, default intro,idea,close")
    v["narrate"].add_argument("topic")
    v["narrate"].add_argument("--words", choices=("auto", "tts", "none"), help="word timings: the voice's own (default) or none")

    for k in ("still", "build"):
        v[k].add_argument("--res", type=int, help="percent of 1920x1080 (still: 50, build: 100)")
        v[k].add_argument("--samples", type=int, help="Cycles samples (still: 4, build: 16)")
        v[k].add_argument("--device", choices=("auto", "METAL", "OPTIX", "CUDA", "CPU"), help="default auto")
        v[k].add_argument("--scenes", help="comma-separated scene names (default: all)")
    s = v["still"]
    s.add_argument("topic")
    s.add_argument("moments", nargs="*", help="Scene, Scene:12.5s, Scene:120 (frame) or 95.2s (whole video)")
    s.add_argument("--timing", default="auto", choices=("auto", "narrate", "narration", "estimate"),
                   help="auto: narrate if gnosia-audio is installed, else the narration on disk, else the estimate")
    b = v["build"]
    b.add_argument("topic")
    b.add_argument("--host", help="render the 3D on this ssh host (e.g. home); everything else stays here")
    b.add_argument("--no-narrate", action="store_true", help="use the narration `narrate` left; no sidecars: run `sidecars` after")
    b.add_argument("--keep-frames", action="store_true", help="keep each scene's PNG frames after its plate is made")
    b.add_argument("--reuse-plates", action="store_true",
                   help="reuse a scene's newest plate of the right length even when its key changed (a library edit you know changes no pixels)")
    b.add_argument("--retry-hours", type=float,
                   help="with --host: how long a dropped connection is retried before the build stops (default 24, or BLENDERKC_HOST_RETRY_HOURS)")
    bs = v["build-status"]
    bs.add_argument("topic")
    bs.add_argument("--host", help="ask this ssh host (default: this machine's data root)")
    v["sidecars"].add_argument("topic")
    v["sheet"].add_argument("topic")
    v["sheet"].add_argument("-n", type=int, default=12, help="frames, default 12")
    v["topics"].add_argument("--check", action="store_true", help="exit 1 on an orphan render or a problem")
    rj = v["render-job"]
    rj.add_argument("--job", required=True, help="a job file, or - for stdin")
    rj.add_argument("--ensure-venv", action="store_true", help="create or update the venv with uv first")
    rj.add_argument("--venv", help="the venv to render with (default: the checkout's .venv)")
    hj = v["host-job"]
    hj.add_argument("action", choices=("start", "run", "status"))
    hj.add_argument("--topic", help="the topic path (run, status)")
    hj.add_argument("--job", default="-", help="start: a job file, or - for stdin")
    hj.add_argument("--ensure-venv", action="store_true", help="start: create or update the venv with uv first")
    hj.add_argument("--venv", help="start: the venv to render with (default: the checkout's .venv)")
    return p


def _on_term(signum, frame):
    # SIGTERM unwinds like Ctrl-C, so a renderer or ssh we started is stopped
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    import signal
    signal.signal(signal.SIGTERM, _on_term)
    args = build_parser().parse_args(argv)
    try:
        repo = Repo(find_root(args.root))
        if args.command == "doctor":
            from .doctor import main as cmd
        elif args.command == "home":
            from .home import main as cmd
        elif args.command == "api":
            from .api import main as cmd
        elif args.command == "new":
            from .new import main as cmd
        elif args.command == "narrate":
            from .narrate import main as cmd
        elif args.command == "still":
            from .still import main as cmd
        elif args.command == "build":
            from .build import main as cmd
        elif args.command == "build-status":
            from .build_status import main as cmd
        elif args.command == "sidecars":
            from .build import sidecars_main as cmd
        elif args.command == "sheet":
            from .sheet import main as cmd
        elif args.command == "topics":
            from .topics import main as cmd
        elif args.command == "render-job":
            from .runner import main as cmd
        elif args.command == "host-job":
            from .hostjob import main as cmd
        else:  # pragma: no cover
            return 2
        return cmd(args, repo) or 0
    except CliError as e:
        print(f"blenderkc: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("blenderkc: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
