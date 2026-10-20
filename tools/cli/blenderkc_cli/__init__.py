"""blenderkc: the command line for narrated Blender (Cycles) explainers.

Standard library only, on purpose, like manimkc's CLI. It shells out to the
checkout's `.venv` (bpy, Pillow, gnosia-audio), to ffmpeg, and to rsync and
ssh for `build --host`, so it keeps working when one of those is broken and
can say which one.

    python3 tools/cli/blenderkc [--root <checkout>] <verb> [options]
"""

__version__ = "0.1.0"
