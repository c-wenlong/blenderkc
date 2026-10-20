"""Aligning the written words of a narration line to timed tokens (a port of
revideokc's align.mjs). The words that come out are always the narration's
own written words ("HBM,", "die's"), each with the time of the token it
matched; a word nothing matched gets its time interpolated between its
neighbours by length. Needleman-Wunsch over words, with two extra moves so one
written word can take two or three tokens ("seventy-one" read as two)."""

from __future__ import annotations

import re


def norm(w: str) -> str:
    w = w.lower().replace("‘", "'").replace("’", "'")
    return re.sub(r"[^a-z0-9'%]", "", w)


def lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return prev[len(b)]


def similar(a: str, b: str) -> bool:
    return a != "" and (a == b or (min(len(a), len(b)) >= 4 and lev(a, b) <= max(len(a), len(b)) // 4))


def clean_tokens(tokens: list[dict]) -> list[dict]:
    return [t for t in tokens if not re.match(r"^\s*\[.*\]\s*$", t["word"]) and norm(t["word"]) != ""]


def align(written: list[str], tokens: list[dict]) -> list:
    n, m, GAP = len(written), len(tokens), -1
    NEG = float("-inf")
    S = [[NEG] * (m + 1) for _ in range(n + 1)]
    P = [[0] * (m + 1) for _ in range(n + 1)]
    S[0][0] = 0
    for i in range(1, n + 1):
        S[i][0], P[i][0] = i * GAP, 1
    for j in range(1, m + 1):
        S[0][j], P[0][j] = j * GAP, 2
    w = [norm(x) for x in written]
    tn = [norm(t["word"]) for t in tokens]
    joined = lambda j, k: "".join(tn[j - k:j])  # noqa: E731
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = S[i - 1][j - 1] + (2 if similar(w[i - 1], joined(j, 1)) else -1)
            move = 0
            if S[i - 1][j] + GAP > best:
                best, move = S[i - 1][j] + GAP, 1
            if S[i][j - 1] + GAP > best:
                best, move = S[i][j - 1] + GAP, 2
            for k in (2, 3):
                if j >= k and similar(w[i - 1], joined(j, k)):
                    v = S[i - 1][j - k] + 2
                    if v > best:
                        best, move = v, 2 + k
            S[i][j], P[i][j] = best, move
    out = [None] * n
    i, j = n, m
    while i > 0 or j > 0:
        move = P[i][j] if i > 0 and j > 0 else (1 if i > 0 else 2)
        if move == 0:
            if similar(w[i - 1], joined(j, 1)):
                out[i - 1] = (j - 1, j - 1)
            i, j = i - 1, j - 1
        elif move >= 4:
            k = move - 2
            out[i - 1] = (j - k, j - 1)
            i, j = i - 1, j - k
        elif move == 1:
            i -= 1
        else:
            j -= 1
    return out


def time_words(text: str, tokens: list[dict], clip: float) -> dict:
    """Written words of `text` timed from tokens ({word, start, end}, seconds
    from the clip start): {words: [{word, start, end}], matched}."""
    written = text.split()
    heard = clean_tokens(tokens)
    mp = align(written, heard)
    words = [{"word": wd, "start": heard[mp[k][0]]["start"], "end": heard[mp[k][1]]["end"]} if mp[k]
             else {"word": wd, "start": None, "end": None} for k, wd in enumerate(written)]
    k = 0
    while k < len(words):
        if words[k]["start"] is not None:
            k += 1
            continue
        e = k
        while e < len(words) and words[e]["start"] is None:
            e += 1
        nxt = words[e]["start"] if e < len(words) else float("inf")
        frm = min(heard[0]["start"] if heard else 0, nxt) if k == 0 else words[k - 1]["end"]
        to = max(frm + 0.2, heard[-1]["end"] if heard else clip) if e == len(words) else words[e]["start"]
        total = sum(len(x["word"]) for x in words[k:e]) or 1
        t = frm
        for q in range(k, e):
            d = (to - frm) * len(words[q]["word"]) / total
            words[q]["start"], words[q]["end"] = t, t + d
            t += d
        k = e
    for q in range(1, len(words)):
        words[q]["start"] = max(words[q]["start"], words[q - 1]["start"] + 0.04)
    for x in words:
        x["end"] = max(x["end"], x["start"])
    r3 = lambda x: round(x * 1000) / 1000  # noqa: E731
    return {"words": [{"word": x["word"], "start": r3(x["start"]), "end": r3(x["end"])} for x in words],
            "matched": sum(1 for x in mp if x)}
