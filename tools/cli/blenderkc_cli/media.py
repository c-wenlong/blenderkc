"""ffmpeg and ffprobe: plates, clips, the narration track, mastering, checks
and contact sheets. Every encode tags BT.709 and converts with it explicitly,
so a frame does not shift colour on its way PNG -> plate -> PNG -> clip.

    plate   one scene's raw 3D frames, H.264 4:4:4 at CRF 14 (49.6 dB PSNR
            against CRF 10, half its size: about 16 Mbit/s for the test
            shot, which matters over a relayed link to the render host),
            x264 held to 4 threads (0.9 GB on the host, measured); what a
            label edit re-composites on without rendering 3D again
    clip    one finished scene: overlays composited, H.264 4:2:0 High, CRF 17,
            the settings every scene shares so the concat is a stream copy
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import wave
from pathlib import Path

from .repo import CliError, which

LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
TAGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
TO_YUV = "scale=out_color_matrix=bt709:out_range=tv"
# 4:2:0 needs even sides; a draft at an odd percentage (41% is 787x442) loses
# its last column or row. Plates are 4:4:4 and keep every pixel.
EVEN = "crop=trunc(iw/2)*2:trunc(ih/2)*2"
TO_RGB = "scale=in_color_matrix=bt709:in_range=tv,format=rgb24"
PLATE_ARGS = ["-c:v", "libx264", "-threads", "4", "-preset", "medium", "-crf", "14", "-pix_fmt", "yuv444p", *TAGS]
CLIP_ARGS = ["-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-profile:v", "high",
             "-g", "60", *TAGS]


def require_ffmpeg() -> None:
    missing = [c for c in ("ffmpeg", "ffprobe") if not which(c)]
    if missing:
        raise CliError(f"{' and '.join(missing)} not on PATH; `blenderkc doctor` says more")


def ffmpeg(args: list[str]) -> None:
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise CliError(f"ffmpeg failed: {r.stderr[-1200:]}")


def frame_pattern(d: Path) -> str:
    return str(d / "f_%04d.png")


def frame_name(n: int) -> str:
    return f"f_{n:04d}.png"


def encode_frames(frames_dir: Path, out: Path, *, fps: int, start: int, count: int, plate: bool) -> None:
    """f_<start>.png .. f_<start+count-1>.png -> one H.264 file, written whole."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.{os.getpid()}.partial.mp4")
    ffmpeg(["-framerate", str(fps), "-start_number", str(start), "-i", frame_pattern(frames_dir),
            "-frames:v", str(count), "-vf", TO_YUV if plate else f"{EVEN},{TO_YUV}", *(PLATE_ARGS if plate else CLIP_ARGS),
            "-r", str(fps), "-an", "-movflags", "+faststart", str(tmp)])
    os.replace(tmp, out)


def transcode_clip(plate: Path, out: Path, *, fps: int) -> None:
    """A plate with no overlays straight to a clip."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.{os.getpid()}.partial.mp4")
    ffmpeg(["-i", str(plate), "-vf", f"{TO_RGB},{EVEN},{TO_YUV}", *CLIP_ARGS, "-r", str(fps), "-an",
            "-movflags", "+faststart", str(tmp)])
    os.replace(tmp, out)


def decode_range(plate: Path, first: int, last: int, d: Path, *, fps: int) -> None:
    """Frames first..last (1-based) of a plate as f_<n>.png, names scene-absolute.
    The seek lands half a frame before `first`, so float rounding can neither
    skip it nor take the one before."""
    d.mkdir(parents=True, exist_ok=True)
    ss = max(0.0, (first - 1.5) / fps)
    ffmpeg(["-ss", f"{ss:.6f}", "-i", str(plate), "-frames:v", str(last - first + 1), "-vf", TO_RGB,
            "-start_number", str(first), "-compression_level", "1", frame_pattern(d)])


def concat(parts: list[Path], out: Path, work: Path) -> None:
    """Stream-copies same-settings H.264 files into one."""
    work.mkdir(parents=True, exist_ok=True)
    lst = work / f"concat.{os.getpid()}.txt"
    lst.write_text("".join(f"file '{str(p).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n" for p in parts))
    tmp = out.with_name(f".{out.stem}.{os.getpid()}.partial.mp4")
    try:
        ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(tmp)])
        os.replace(tmp, out)
    finally:
        lst.unlink(missing_ok=True)


def count_frames(video: Path) -> int | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                        "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip().split(",")[0])
    except ValueError:
        return None


def probe_streams(f: Path) -> list[dict]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,codec_name,width,height,sample_rate,r_frame_rate,pix_fmt",
                        "-of", "json", str(f)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout).get("streams", [])
    except ValueError:
        return []


def narration_track(tl: dict, public: Path, out: Path, work: Path) -> dict:
    """The narration as one WAV, each clip at its line's voice frame, silence
    between: exactly tl['frames'] / fps seconds long. Clips in another format
    than the first are converted with ffmpeg first."""
    fps = tl["fps"]
    clips = [(s["from"] + l["audioFrom"], l) for s in tl["scenes"] for l in s["lines"]]
    if not clips:
        raise CliError("the timeline has no lines")
    paths = []
    for _, l in clips:
        if not l.get("audio"):
            raise CliError(f"line {l['text'][:40]!r} has no clip; narrate first")
        p = public / l["audio"]
        if not p.is_file():
            raise CliError(f"{p} is missing; run narrate again")
        paths.append(p)
    with wave.open(str(paths[0])) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
    if width != 2:
        rate, ch, width = 48000, 2, 2
    conv = work / "pcm"
    conv.mkdir(parents=True, exist_ok=True)
    total = round(tl["frames"] / fps * rate)
    bpf = ch * width
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as dst:
        dst.setnchannels(ch)
        dst.setsampwidth(width)
        dst.setframerate(rate)
        at = 0
        for (frame, l), p in zip(clips, paths):
            src = p
            with wave.open(str(p)) as w:
                same = (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (rate, ch, width)
            if not same:
                src = conv / p.name
                if not src.exists():
                    ffmpeg(["-i", str(p), "-ar", str(rate), "-ac", str(ch), "-c:a", "pcm_s16le", str(src)])
            start = round(frame / fps * rate)
            if start < at:
                raise CliError(f"clip {p.name} would overlap the one before it")
            dst.writeframes(b"\0" * (start - at) * bpf)
            with wave.open(str(src)) as w:
                data = w.readframes(w.getnframes())
            n = min(len(data) // bpf, total - start)
            dst.writeframes(data[: n * bpf])
            at = start + n
        dst.writeframes(b"\0" * max(0, total - at) * bpf)
    return {"rate": rate, "channels": ch, "seconds": total / rate}


def master(video: Path, narration: Path, out: Path) -> None:
    """Video copied, narration loudness-normalised to -16 LUFS, 48 kHz AAC at
    192k, moov atom first (revideokc's and manimkc's mastering)."""
    ffmpeg(["-i", str(video), "-i", str(narration), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            "-af", LOUDNORM, "-ar", "48000", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])


def top_boxes(f: Path) -> list[str]:
    """Top-level MP4 box types in file order, read from the headers alone."""
    out = []
    size = f.stat().st_size
    at = 0
    with open(f, "rb") as fh:
        while at + 8 <= size and len(out) < 64:
            fh.seek(at)
            head = fh.read(16)
            n = int.from_bytes(head[0:4], "big")
            typ = head[4:8].decode("latin1")
            if n == 1:
                n = int.from_bytes(head[8:16], "big")
            elif n == 0:
                n = size - at
            if n < 8:
                break
            out.append(typ)
            at += n
    return out


def is_faststart(f: Path) -> bool:
    b = top_boxes(f)
    return "moov" in b and "mdat" in b and b.index("moov") < b.index("mdat")


def video_sheet(mp4: Path, out: Path, *, duration: float, n: int = 12, cols: int = 4, width: int = 640) -> Path:
    require_ffmpeg()
    rows = -(-n // cols)
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(["-i", str(mp4), "-vf", f"fps={n / duration},scale={width}:-1,tile={cols}x{rows}", "-frames:v", "1", str(out)])
    return out


def tile(images: list[Path], out: Path, *, cols: int = 4, width: int = 640, work: Path) -> Path | None:
    """Tiles images in the given order into one PNG on a dark ground."""
    if not images:
        return None
    require_ffmpeg()
    work.mkdir(parents=True, exist_ok=True)
    seq = work / f"tile.{os.getpid()}"
    shutil.rmtree(seq, ignore_errors=True)
    seq.mkdir()
    for i, p in enumerate(images, 1):
        os.symlink(p, seq / frame_name(i))
    c = min(cols, len(images))
    rows = -(-len(images) // c)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg(["-framerate", "1", "-i", frame_pattern(seq), "-filter_complex",
                f"scale={width}:-2,tile={c}x{rows}:margin=6:padding=6:color=0x222222", "-frames:v", "1", str(out)])
    finally:
        shutil.rmtree(seq, ignore_errors=True)
    return out
