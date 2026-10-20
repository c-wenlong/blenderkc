"""The small YAML subset a topic.yml uses, read without a YAML library so the
CLI stays standard-library only (a port of revideokc's yaml.mjs). Supported:

    key: scalar                 quoted ("..." or '...') or bare, with an
                                optional trailing ` # comment` on a bare one
    key: [a, "b c", d]          a flow list of scalars
    key:                        followed by an indented block, either
      - item                      a block list of scalars, or
      sub: scalar                 a one-level mapping of scalars or flow lists

Anything else is an error naming the line, so a manifest never half-parses.
"""

from __future__ import annotations

import json
import re


class YamlError(Exception):
    pass


KEY = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")


def scalar(text: str):
    t = text.strip()
    if t == "":
        return None
    if t[0] == '"':
        end = 1
        while end < len(t) and t[end] != '"':
            end += 2 if t[end] == "\\" else 1
        if end >= len(t):
            raise YamlError(f"unterminated quote in {t!r}")
        body = re.sub(r"\\x([0-9A-Fa-f]{2})", r"\\u00\1", t[: end + 1])
        try:
            return json.loads(body)
        except ValueError:
            return t[1:end]
    if t[0] == "'":
        end = 1
        while end < len(t) and not (t[end] == "'" and (end + 1 >= len(t) or t[end + 1] != "'")):
            end += 2 if t[end] == "'" else 1
        if end >= len(t):
            raise YamlError(f"unterminated quote in {t!r}")
        return t[1:end].replace("''", "'")
    m = re.search(r"\s#", t)
    bare = (t[: m.start()] if m else t).strip()
    return None if bare in ("null", "~", "") else bare


def flow_list(text: str) -> list:
    t = text.strip()
    end = t.rfind("]")
    if not t.startswith("[") or end < 0:
        raise YamlError(f"not a flow list: {t}")
    out, cur, quote, escaped = [], "", None, False
    for ch in t[1:end]:
        if quote:
            cur += ch
            if escaped:
                escaped = False
            elif quote == '"' and ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch == ",":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [v for v in (scalar(x) for x in out) if v is not None]


def value(text: str):
    t = text.strip()
    return flow_list(t) if t.startswith("[") else scalar(t)


def _blank(line: str) -> bool:
    s = line.strip()
    return s == "" or s.startswith("#")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def parse_yaml(text: str) -> dict:
    lines = text.split("\n")
    out: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if _blank(line):
            i += 1
            continue
        if _indent(line) != 0:
            raise YamlError(f"line {i + 1}: unexpected indentation")
        m = KEY.match(line)
        if not m:
            raise YamlError(f'line {i + 1}: expected "key: value"')
        key, rest = m.group(1), m.group(2)
        i += 1
        if rest.strip() and not rest.strip().startswith("#"):
            out[key] = value(rest)
            continue
        block = []
        while i < len(lines) and (_blank(lines[i]) or _indent(lines[i]) > 0):
            if not _blank(lines[i]):
                block.append((i + 1, lines[i].strip()))
            i += 1
        if not block:
            out[key] = None
        elif block[0][1].startswith("- ") or block[0][1] == "-":
            items = []
            for n, l in block:
                if not l.startswith("-"):
                    raise YamlError(f"line {n}: mixed list and mapping under {key}")
                v = scalar(l[1:])
                if v is not None:
                    items.append(v)
            out[key] = items
        else:
            mp = {}
            for n, l in block:
                mm = KEY.match(l)
                if not mm:
                    raise YamlError(f'line {n}: expected "key: value" under {key}')
                mp[mm.group(1)] = value(mm.group(2))
            out[key] = mp
    return out


def as_list(v) -> list:
    """A list field in either shape, or [] when absent or null."""
    if v is None:
        return []
    return v if isinstance(v, list) else [v]
