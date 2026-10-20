# blenderkc: the contract as built

blenderkc makes narrated explainer videos whose pictures are photoreal 3D
rendered by Blender's Cycles, through `bpy` from PyPI. It is the fourth video
tool beside manimkc, remotionkc and revideokc in self-learn, and it shares
their narration (gnosia-audio), their timing model, their sidecars and their
`topics --json` report, so the catalog indexes it like the others.

This page is the contract between the pieces. Keep it current when a piece
changes. How to write a shot with the kit is [KIT.md](KIT.md).

## The two halves

| Half | Where | Language | Runs on |
| --- | --- | --- | --- |
| The library: `blenderkc/shot.py`, `kit/`, `render.py`, `overlay.py` | `blenderkc/` | Python 3.13 with bpy and Pillow (`.venv`) | the Mac, or the render host |
| The CLI and the pipeline | `tools/cli/blenderkc`, `tools/cli/blenderkc_cli/` | Python 3.10+, standard library only | the Mac; the host runs only `render-job` |

The CLI never imports bpy or Pillow. It calls the library through two
command lines (below), so it still runs, and `doctor` can say what is wrong,
when the venv is broken.

## Layout

```
blenderkc/                 the library shot scripts import
tools/cli/blenderkc        the CLI shim: python3 tools/cli/blenderkc [--root <checkout>] <verb>
tools/cli/blenderkc_cli/   its modules
topics/<domain>/[<sub>/]<slug>/    the default BLENDERKC_TOPICS; it may live
                                 outside the checkout entirely
  topic.yml                title, topic (catalog slug, required), concepts, tags,
                           prerequisites, materials, prefix, narrated: {shots, output}
  narration.json           [{scene, title, hold?, lines: [...]}], scene = <prefix><n>_<Name>
  shots/<Scene>.py         one per scene: def build(shot)
tests/                     stdlib unittest, with a stand-in library and gnosia-audio
requirements.txt           bpy==5.2.2, Pillow (the .venv, Python 3.13)
requirements-narration.txt gnosia-audio, the commit revideokc pins
Makefile                   make deps, make narration-deps, make test
```

## The two roots (and the topics tree)

`BLENDERKC_ROOT` is the checkout. `BLENDERKC_HOME` is the data root: the
environment, then a `BLENDERKC_HOME=` line in the checkout's `.env`, then the
checkout itself, as manimkc and revideokc resolve theirs. It must be
absolute. On the owner's Mac it is `~/.self-learn/blenderkc`.

`BLENDERKC_TOPICS` is the topics tree, resolved the same way: the
environment, then a `BLENDERKC_TOPICS=` line in the checkout's `.env`, then
`<checkout>/topics`, absolute only. Every verb reads topics from it and `new`
writes into it, so the course videos can live in a private repository while
this one is public — the checkout's own `topics/` only has to hold the demo.

```
$BLENDERKC_HOME/
  out/<topic>/<name>.mp4             the deliverable, with .transcript.json, .chapters.json, .vtt
  public/<topic>/                    narration.manifest.json, words.json, audio/<clip>.wav
  stills/<topic>/<run>/              one `still` run: s001_<Scene>_f0042.png ..., stills.sheet.png
  stills/<topic>/latest              a link to the newest run; runs older than a day pruned, ten kept
  stills/<topic>/                    <name>.sheet.png, <name>.draft.mp4
  frames/<topic>/<Scene>/<key16>/    PNG frames while a scene renders
  plates/<topic>/<Scene>.<key16>.mp4 one raw 3D video per scene and plate key
  cache/clips/<topic>/<Scene>.<key16>.mp4   one finished scene per clip key
  cache/build/<topic>/               timing files, composite work, the build lock
  state/builds.jsonl                 one record per build
```

## Narration and timing

`narrate` sends every line to `gnosia-audio narrate --json` (schema 1) with
`--manimkc-root` when there is a manimkc checkout (`MANIMKC_ROOT`, else a
sibling `../manimkc`), so the voice and the clip cache are the series'. It
writes the same manifest and `words.json` revideokc writes.

gnosia-audio lives in the same `.venv` as bpy. Measured on 2026-10-09: the
pinned commit installs on Python 3.13 (it pulls `audioop-lts`), `import bpy,
gnosia_audio, PIL` works in one interpreter, and kokoro voiced two lines
(model load 10 s, synthesis 1.7 s). So there is no separate narration venv.

Timeline: each line is lead-in (0.2 s), clip, tail (0.4 s), back to back; a
line's slot is `ceil((leadIn + clip + tail) * 30)` frames, its voice starts
`round(leadIn * 30)` frames in, and a scene's `hold` adds `round(hold * 30)`
frames. The audio track places every clip at exactly those frames. Before
narration exists, `still` estimates: 15.2 characters a second, words spread
over the line by length.

## The scene timing file (`--timing`)

```json
{"scene": "HB1_Hook", "fps": 30, "source": "narration", "leadIn": 0.2, "tail": 0.4,
 "hold": 0.6, "frames": 912, "duration": 30.4,
 "lines": [{"index": 0, "text": "...", "start": 0.0, "voice": 0.2, "clip": 3.75, "end": 4.367,
            "words": [{"word": "HBM,", "start": 0.275, "end": 0.5}]}]}
```

`start`, `voice` and `end` are seconds from the scene's start; word times
are seconds from the clip's start. Frames are 1-based: `f_0001.png` is t = 0.

## The renderer contract

```
<python> -m blenderkc.render --topic <dir> --scene <Scene> --timing <file>
    --frames <a> <b> --out <dir> [--res 100] [--samples 16]
    [--device auto|METAL|OPTIX|CUDA|CPU] [--overlays-only]
```

Writes `<out>/f_0001.png ...` (scene frame numbers), skips frames that exist,
fixed seed, and on NVIDIA the OptiX denoiser (`auto` picks OPTIX on NVIDIA,
METAL on a Mac, else CPU). Every call also writes `<out>/shot.json`:
`{scene, fps, frames, width, height, anchors: {name: [[x, y, visible], ...]},
overlays: [{kind, text, anchor?, start, end, ...}]}`, anchors in pixels at
100%. `--overlays-only` builds the scene and writes shot.json, rendering
nothing. The CLI runs it from the checkout with the checkout on `PYTHONPATH`.

```
<python> -m blenderkc.overlay --frames <dir with f_*.png and shot.json> --out <dir> [--from a --to b]
    [--state <file>]
```

Composites the overlays onto frames a..b (the dir may hold only that range;
names stay scene-absolute), scaling by frame width / shot.json width, so a
50% still gets the same labels.

Ink follows the frame: each overlay samples the rendered frame under its
text and takes white ink with a dark halo on a dark frame, near-black ink
with a light halo on a bright one (coloured terms and lit text are deepened
on bright frames). The choice is made the first frame an overlay is drawn and
kept; `--state` carries it across the chunks of one scene, and the build
starts a fresh state per plate. The halo's strength follows the worst
contrast under the text every frame, so text over a busy or mid-grey patch
still clears 4.5:1 (`tests/test_lib_overlay.py` checks bright, dark and
mid-grey frames).

## The cache

Two keys per scene (`keys.py`):

- The **plate key** covers the 3D picture: the shot script with comment-only
  lines, blank lines and whole-statement overlay calls (`<name>.label(...)`,
  `title`, `equation`, `glow`) taken out; every file under `blenderkc/` but
  `overlay.py` and `fonts/`; the topic's own helper files; the scene timing
  file; res, samples and fps; the bpy pin; a cache version. Not the device.
- The **clip key** adds the shot script as written, `overlay.py` and `fonts/`.

So a label edit re-composites and renders no 3D; a comment edit does
nothing; a library edit renders every scene again. `build --reuse-plates`
takes each scene's newest plate of the right length and size when its key
changed, for a library edit known to change no pixels; a draft's smaller
plate is never taken for a full build.

## The build

1. Narrate (cached clips cost nothing), or `--no-narrate` to use what
   `narrate` left, refused when stale.
2. Plan each scene's timing file and keys. A scene whose clip exists is done.
3. Render the scenes with neither clip nor plate: a render job here, or one
   detached job on `--host` (below). Per scene: render frames (resumable), encode the plate (H.264
   4:4:4, CRF 14, 4 threads), check its frame count, delete the frames
   (`--keep-frames` keeps them).
4. Composite, always here: `--overlays-only` for shot.json, then the plate
   decoded in chunks of 900 frames, `blenderkc.overlay`, encoded (H.264 4:2:0
   High, CRF 17, BT.709), chunks joined. No overlays: the plate is transcoded.
5. Join the clips by stream copy (the build assembles the narration from hard
   links it made when it started, so a `narrate` meanwhile cannot remove its
   clips), check the frame count against the timeline,
   lay the narration, master (loudnorm to -16 LUFS, AAC 192k at 48 kHz, moov
   first), check audio and faststart, write the sidecars with `gnosia-audio
   sidecars --json` from the timeline used, publish, cut a contact sheet,
   append to `state/builds.jsonl`.

`--scenes A,B` or `--res` below 100 make a draft at
`stills/<topic>/<name>.draft.mp4` and leave the deliverable alone.

## `--host`

The host (`home`: Ubuntu, GTX 1070, 7.7 GB shared RAM) cannot clone the
private repository, so the checkout is copied:

```
~/.cache/blenderkc/src/<name>-<digest>/  a content-addressed snapshot of the checkout
                                 (minus .git, venvs, .env, data) plus the topics
                                 tree at <snapshot>/topics/, pushed by its own
                                 rsync so BLENDERKC_TOPICS outside the checkout
                                 is copied too; unused ones pruned after two
                                 days, newest five kept
~/.cache/blenderkc/venv-<hash>/  bpy and Pillow by uv, Python 3.13; <hash> is requirements.txt's
~/.cache/blenderkc/home/         the host's data root: frames while rendering, plates
~/.cache/blenderkc/home/cache/hostjob/<topic>/
                                 the detached job: job.json, pid, status.json, log
                                 (log.1 the previous job's), code (the snapshot it runs from)
```

`BLENDERKC_HOST_BASE` puts all of it under another folder of the host's home
(for a trial run beside a real one); the default is `.cache/blenderkc`.

A render on the homeserver takes up to a day, longer than one ssh connection
can be trusted: the server's Tailscale SSH is in check mode, and on
2026-10-10 a check that wanted a browser login timed a connection out and
stopped a build after 9 of 54 scenes. So the render is one detached job on
the host, and the Mac only watches it:

1. `build --host home` makes the folders, rsyncs the checkout into its
   snapshot and the topics tree into `<snapshot>/topics/`, and sends the job (every scene still needing a plate, with its
   timing and plate key) in one ssh call: `host-job start --job -`, which
   makes the venv if missing, writes the job down and starts
   `host-job run` in a session of its own, stdin and output off the ssh
   channel, so the call returns at once and a dropped connection cannot
   reach it. One job per topic: a start while one runs attaches to it when
   it already covers every scene asked for (same plate keys), and is refused
   otherwise, naming its pid. The start also refuses while an older build's
   per-scene `render-job` holds the topic's render lock.
2. The host job renders the scenes in order with `runner.run_job`: frames
   resumable, each render inside `systemd-run --user --scope -q -p
   MemoryMax=3G` when that works (`BLENDERKC_MEMORY_MAX` changes the cap),
   the plate encoded and checked, the frames deleted. It writes
   `status.json` at each scene and plate (a plate counts as ready only once
   this run has checked it, so one being made again never reads as ready),
   and touches its snapshot's `.used` every hour so a prune never takes it.
   Before rendering, under the topic's render lock, it stops any renderer
   still writing this topic's frames: a runner killed outright leaves
   Blender running, and two would write the same frames.
3. The Mac polls `host-job status` every minute (`BLENDERKC_POLL_S`), a
   small JSON reply, and rsyncs each plate back as soon as the host has
   checked it. A job that died (state `died`: its pid gone; or `failed`,
   `stopped`) is started again and resumes from the frames on disk; one
   that stops three times without a new frame stops the build with its log.
4. With every plate here, the build composites and finishes here, as before.

Every ssh and rsync call is retried when the connection fails (ssh exit
255, rsync's connection exits, or no answer in its time, which stops the
call's whole process group) with backoff from
15 s to 5 minutes, for up to 24 hours of failing (`--retry-hours`,
`BLENDERKC_HOST_RETRY_HOURS`), instead of failing the build. When ssh's
output carries a Tailscale check URL, the build prints `the host needs a
Tailscale login: <url>` once, then the newest URL every half hour while it
waits. BatchMode stays on: nothing ever waits on a prompt.

Stopping the Mac process (Ctrl-C, SIGTERM, a sleep, a crash) stops only the
watching: the host job keeps rendering, and the same build run again
reattaches. To stop the host job too, stop the build first, then `kill
<pid>` on the host (`build-status` prints the pid); the job stops Blender
and records `stopped`.

`blenderkc build-status <topic> --host home [--json]` reads the job with one
ssh call: state, plates done on the host and copied here, the current scene
and frame, the seconds a frame (the median gap between its newest frames),
and the time left at that speed. It runs from the job's own copy of the
code, else this checkout's, else the newest copy on the host that has
`hostjob.py`, so it needs no upload. Without `--host` it reads this
machine's data root.

- Nothing outside `~/.cache/blenderkc/` (or `BLENDERKC_HOST_BASE`) is
  touched. A detached job lives on after its ssh session only when the
  host's logind keeps a closed session's processes (`KillUserProcesses=no`,
  Ubuntu's default) and the user's systemd manager outlives logins for the
  memory scope (`loginctl enable-linger`; `doctor --host` checks Linger).
  Both hold on `home`, and a trial on 2026-10-10 confirmed it: the job went
  on rendering after the Mac's build was stopped and its session closed.
- Frames are deleted once a plate is checked: 16 minutes of 1080p PNG is
  about 80 GB and the host has 56 GB free.
- The link is a relayed Tailscale path, measured at about 120 KB/s both ways
  on 2026-10-09. Plates are CRF 14 for that reason (a minute is about
  120 MB), and the copy of one overlaps the render of the next (a minute of
  video takes about 90 minutes to render at 16 samples).
- No plate key involves the host, the job or the transport, so switching a
  build between the per-scene runs and the detached job reuses every plate
  and frame (`tests/test_units.py` pins the keys).
- `render-job --job -` still runs one job in the foreground (what a local
  build runs in process), with `BLENDERKC_WATCH_PARENT=1` stopping Blender
  when its parent goes.

Measured on the kit-tour demo: KT1_Package at 16 samples, 2.75 to 3.8 s a
frame on the GTX 1070 with OptiX; an interrupted host render resumed with
"rendering 70 of 163 frames".

## `topics --json`

manimkc's report shape, field for field (`tests/test_topics.py` pins it).
Each section lists one file per narration scene, `shots/<Scene>.py`, with
that scene. Paths under `outRoot`, which is the data root.

## Verbs

| Verb | Does |
| --- | --- |
| `doctor [--host h]` | venv and bpy pin, Pillow, the library, ffmpeg, rsync, ssh, gnosia-audio at the pin and its own doctor, the manimkc checkout; with `--host`, the host's tools, GPU and memory scope |
| `home` | the data root, every path under it, the host layout, what gnosia-audio resolved |
| `api [name]` | the shot rules, then every `blenderkc/` export, read with `ast`, with a usage line from `topics/` |
| `new <path> --topic <slug>` | topic.yml with a free prefix, narration.json, shot stubs |
| `narrate <topic>` | voices, stages clips, writes the manifest and words.json |
| `still <topic> [moments]` | 50%, 4 samples, here, overlays on, one sheet; moments `Scene`, `Scene:12.5s`, `Scene:120`, `95.2s` |
| `build <topic>` | the pipeline above; `--host`, `--retry-hours`, `--scenes`, `--res`, `--samples`, `--device`, `--no-narrate`, `--keep-frames`, `--reuse-plates` |
| `build-status <topic> [--host h]` | the detached render job: state, plates done and here, the current frame, seconds a frame, ETA; `--json` |
| `sidecars <topic>` | sidecars for a `--no-narrate` build, refused for any other video |
| `sheet <topic> [-n 12]` | a contact sheet of the deliverable |
| `topics [--json] [--check]` | the catalog report; `--check` exits 1 on an orphan or a problem |
| `render-job --job <file or ->` | internal: one render job in the foreground (a local build) |
| `host-job start\|run\|status` | internal: the detached render job on a `--host` host |
