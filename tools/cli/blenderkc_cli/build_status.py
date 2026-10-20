"""`blenderkc build-status <topic> [--host h] [--json]`: how the detached
render job of a topic is going: scenes done, the current scene and frame,
the seconds a frame, and an ETA, plus which plates are already here.

With --host it asks the host with one cheap ssh call (a Tailscale login, if
the host wants one, is printed and the call fails rather than waits);
without, it reads this machine's own data root."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import hostjob, remote
from .repo import Repo
from .topics import resolve_topic


def report(repo: Repo, topic: str, host: str | None) -> dict:
    if host:
        st = remote.fetch_status(remote.Link(host, patience_s=0), repo, host, topic)
    else:
        st = hostjob.status(repo, topic)
    for r in st.get("scenes", []):
        r["here"] = (repo.home / r["plate"]).is_file()
    st["here"] = sum(1 for r in st.get("scenes", []) if r["here"])
    st["host"] = host
    if st.get("etaSeconds") is not None:
        st["etaAt"] = (datetime.now() + timedelta(seconds=st["etaSeconds"])).astimezone().isoformat(timespec="minutes")
    return st


def _local(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%a %d %b %H:%M")
    except ValueError:
        return iso


def main(args, repo: Repo) -> int:
    topic = resolve_topic(repo, args.topic)
    st = report(repo, topic, args.host)
    if args.json:
        print(json.dumps(st, indent=1))
        return 0
    where = f"on {args.host}" if args.host else "here"
    if st["state"] == "none":
        print(f"{topic}: no render job {where}")
        return 0
    pid = f" (pid {st['pid']})" if st.get("pid") else ""
    L = [f"{topic} {where}: {st['state']}{pid}"
         + (f", started {_local(st['startedAt'])}" if st.get("startedAt") else "")
         + (f", restarted {st['restarts']} times" if st.get("restarts") else "")]
    L.append(f"  plates  {st['done']} of {st['total']} done on the {'host' if args.host else 'render machine'}, {st['here']} copied here")
    cur = st.get("current")
    if cur:
        L.append(f"  now     {cur['scene']}, " + ("every frame rendered, encoding its plate" if cur["frame"] >= cur["frames"]
                                                   else f"frame {cur['frame']} of {cur['frames']}"))
    if st.get("secondsPerFrame"):
        L.append(f"  speed   {st['secondsPerFrame']} s a frame")
    if st.get("etaSeconds") is not None:
        at = datetime.fromisoformat(st["etaAt"]).strftime("%a %d %b %H:%M")
        L.append(f"  left    {st['framesLeft']} frames, about {remote.human_seconds(st['etaSeconds'])} (around {at})")
    if st.get("error"):
        L.append(f"  error   {st['error']}")
    if st.get("logTail"):
        L.append("  its log ends:")
        L += [f"    {line}" for line in st["logTail"][-10:]]
    if st["state"] in ("died", "failed", "stopped"):
        L.append(f"  run `blenderkc build {topic}{' --host ' + args.host if args.host else ''}` to start it again; it resumes from its frames")
    print("\n".join(L))
    return 0
