# blenderkc

[![CI](https://github.com/c-wenlong/blenderkc/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/c-wenlong/blenderkc/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
![blenderkc CLI: stdlib only](https://img.shields.io/badge/blenderkc%20CLI-stdlib--only-brightgreen.svg)
![Renderer: Blender Cycles](https://img.shields.io/badge/renderer-Blender%20Cycles%20%28bpy%29-orange.svg)

Narrated explainer videos with photoreal 3D, rendered by Blender's Cycles from Python (`bpy` from PyPI). The fourth video tool beside manimkc, remotionkc and revideokc in self-learn: the same CLI verbs, the same `topics --json` report, the same narration through gnosia-audio and the same sidecars, so the self-learn catalog indexes it like the others.

Status: being built. The design is in `docs/DESIGN.md`.

## Install

```
make deps             # .venv: Python 3.13, bpy 5.2.2, Pillow (uv)
make narration-deps   # gnosia-audio into the same .venv
cp .env.example .env  # BLENDERKC_HOME, BLENDERKC_TOPICS for a private topics repo, MANIMKC_ROOT outside self-learn
python3 tools/cli/blenderkc doctor
```

## Use

```
python3 tools/cli/blenderkc new computer-architecture/memory/dram-cells --topic dram
python3 tools/cli/blenderkc still <topic>            # look, at half resolution
python3 tools/cli/blenderkc build <topic> --host home  # 3D on the homeserver, as one detached job
python3 tools/cli/blenderkc build-status <topic> --host home  # scenes done, frame, seconds a frame, ETA
make test
```

A `--host` build survives the connection: the host renders on its own, the
Mac polls and copies plates back, and a dropped ssh (a Tailscale login
check included, whose URL it prints) is retried for up to 24 hours. If the
Mac process stops, run the same build again and it picks the host job up.
