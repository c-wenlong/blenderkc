"""`blenderkc home`: where generated data lives, who said so, and every path
under it, plus where `build --host` puts things and what gnosia-audio
resolved. There is no --home flag, on purpose (manimkc's call): the data root
is a property of the machine, not of one command."""

from __future__ import annotations

import json

from . import remote
from .audio import gnosia_bin, try_gnosia
from .repo import HOME_ENV, Repo, is_inside

WHAT = {
    "out": "deliverables: out/<topic>/<name>.mp4 and its sidecars",
    "public": "narration: <topic>/narration.manifest.json, words.json, audio/",
    "stills": "stills, stills.sheet.png, <name>.sheet.png, <name>.draft.mp4",
    "frames": "PNG frames while a scene renders (removed once its plate is made)",
    "plates": "one raw 3D video per scene and plate key",
    "cache": "clips/ per scene and clip key, build/ work, stills/ work, jobs/",
    "state": "builds.jsonl, one record per build",
}


def narration_report(repo: Repo) -> dict:
    binary, source = gnosia_bin(repo)
    r = try_gnosia(repo, "home")
    if "error" in r:
        return {"bin": binary, "binSource": source, "error": r["error"]}
    keep = ("schema", "version", "checkout", "dataRoot", "cache", "backend", "voice", "rate", "leadIn", "tail", "sources")
    return {"bin": binary, "binSource": source, **{k: r["reply"].get(k) for k in keep}}


def home_report(repo: Repo) -> dict:
    mk, mk_src = repo.manimkc_root()
    return {
        "root": str(repo.root), "home": str(repo.home), "source": repo.home_source,
        "external": not is_inside(repo.home, repo.root),
        "topics": {"dir": str(repo.topics_dir), "source": repo.topics_source,
                   "inCheckout": is_inside(repo.topics_dir, repo.root)},
        "paths": {k: str(getattr(repo, k)) for k in WHAT},
        "host": {"code": f"~/{remote.remote_code(repo)}", "home": f"~/{remote.remote_home(repo)}"},
        "manimkc": {"root": str(mk), "source": mk_src},
        "narration": narration_report(repo),
    }


def main(args, repo: Repo) -> int:
    r = home_report(repo)
    if args.json:
        print(json.dumps(r, indent=1))
        return 0
    L = [f"checkout   {r['root']}", f"data root  {r['home']}",
         f"           from {r['source']}; {'outside the checkout, so it survives a relayout' if r['external'] else 'inside the checkout: deleting or moving the checkout takes it'}", ""]
    for k, p in r["paths"].items():
        L.append(f"  {k:<8} {p}\n           {WHAT[k]}")
    L += ["", f"topics     {r['topics']['dir']} ({r['topics']['source']})",
          f"--host     this checkout is copied to {r['host']['code']}/, its data root is {r['host']['home']}/",
          f"manimkc    {r['manimkc']['root']} ({r['manimkc']['source']})"]
    n = r["narration"]
    if "error" in n:
        L += [f"narration  {n['bin']} ({n['binSource']})", f"           cannot answer: {n['error']}"]
    else:
        L += [f"narration  gnosia-audio {n['version']} at {n['bin']} ({n['binSource']})",
              f"           clips    {n['cache']}",
              f"           voice    {n['backend']} {n['voice']}, rate {n['rate']}; lead-in {n['leadIn']}s, tail {n['tail']}s"]
    if not r["external"]:
        L += ["", f"To keep generated data out of the checkout: echo '{HOME_ENV}=~/.self-learn/blenderkc' >> .env"]
    print("\n".join(L))
    return 0
