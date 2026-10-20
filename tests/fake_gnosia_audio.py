#!/usr/bin/env python3
"""A stand-in for gnosia-audio's JSON command (schema 1), standard library
only: `narrate` writes silent WAV clips (0.1 s per word) into
$FAKE_GNOSIA_CACHE with estimated word times, `sidecars` writes the three
files, `doctor` and `home` answer. No network, no model."""
import hashlib
import json
import os
import sys
import wave

cmd = sys.argv[1]
req = json.loads(sys.stdin.read() or "null")
cache = os.environ.get("FAKE_GNOSIA_CACHE", "/tmp/fake-gnosia")
base = {"schema": 1, "version": "0.0.0-fake", "backend": "fake", "voice": "none", "rate": 170,
        "cache": cache, "leadIn": 0.2, "tail": 0.4}
if cmd == "narrate":
    os.makedirs(cache, exist_ok=True)
    out, made = [], 0
    for text in req["lines"]:
        key = hashlib.sha256(text.encode()).hexdigest()[:16]
        clip = os.path.join(cache, key + ".wav")
        words = text.split()
        dur = round(0.1 * len(words), 3)
        if not os.path.exists(clip):
            made += 1
            with wave.open(clip, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
                w.writeframes(b"\x01\x00" * int(24000 * dur))
        ws = [{"word": x, "start": round(i * 0.1, 3), "end": round(i * 0.1 + 0.09, 3)} for i, x in enumerate(words)]
        out.append({"text": text, "spoken": text, "clip": clip, "duration": dur,
                    "words": {"source": "estimate", "duration": dur, "words": ws}})
    print(json.dumps({**base, "words": req.get("words"), "synthesised": made, "timed": len(out), "lines": out}))
elif cmd == "sidecars":
    d, name = req["outDir"], req["name"]
    os.makedirs(d, exist_ok=True)
    paths = {"transcript": os.path.join(d, name + ".transcript.json"),
             "chapters": os.path.join(d, name + ".chapters.json"), "vtt": os.path.join(d, name + ".vtt")}
    json.dump({"video": req["video"], "duration": req["duration"], "chapters": req["scenes"], "segments": []}, open(paths["transcript"], "w"))
    json.dump([{"scene": s["scene"], "title": s["title"], "start": s["start"]} for s in req["scenes"]], open(paths["chapters"], "w"))
    open(paths["vtt"], "w").write("WEBVTT\n")
    print(json.dumps({**base, "paths": paths}))
elif cmd == "doctor":
    print(json.dumps({**base, "ok": True, "checks": [{"name": "fake", "ok": True, "detail": "stand-in"}]}))
elif cmd == "home":
    print(json.dumps({**base, "checkout": None, "dataRoot": None, "sources": {}}))
else:
    print(json.dumps({**base, "error": {"kind": "usage", "message": f"unknown command {cmd}"}}))
    sys.exit(2)
