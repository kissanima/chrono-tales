#!/usr/bin/env python3
"""Extract the field schema from every `tabedit/*.gbc` table editor.

Each editor declares its columns as parallel tuples:
    m_szItemName = ("field", "field", ...)     column display names
    m_szExplain  = ("...", ...)                per-column help text
    m_szType     = (1, 256, 3840, ...)         editor widget/type codes
    m_szFlip     = (0, ("索引", ",", "值", ",")) sub-field layouts

`m_nTagNO` / `m_nKeyMode` identify the table family.  Emitting this as JSON gives
us the column order needed to label the varbin property streams.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TAB = os.path.join(ROOT, "extracted", "sysnet", "tabedit")
OUT = os.path.join(HERE, "table_schemas.json")

STR = r'"((?:[^"\\]|\\.)*)"'


def grab(text, key):
    """Return the raw parenthesised body of `key=(...)`, respecting nesting."""
    m = re.search(rf"\b{key}\s*=\s*\(", text)
    if not m:
        return None
    i = m.end() - 1
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[i + 1:j]
    return None


def split_top(body):
    """Split a tuple body on top-level commas."""
    out, depth, cur, instr = [], 0, [], False
    i = 0
    while i < len(body):
        c = body[i]
        if instr:
            cur.append(c)
            if c == "\\":
                cur.append(body[i + 1])
                i += 2
                continue
            if c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
                cur.append(c)
            elif c == "(":
                depth += 1
                cur.append(c)
            elif c == ")":
                depth -= 1
                cur.append(c)
            elif c == "," and depth == 0:
                out.append("".join(cur).strip())
                cur = []
            else:
                cur.append(c)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\r", "\r").replace("\\n", "\n")
    return s


def strip_comments(text):
    """Remove /* ... */ and // ... comments, preserving string literals."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def main():
    schemas = {}
    for p in sorted(glob.glob(os.path.join(TAB, "*.gbc"))):
        raw = open(p, "rb").read()
        text = strip_comments(raw.decode("gbk", "replace"))
        names = grab(text, "m_szItemName")
        if not names:
            continue
        cols = [unquote(x) for x in split_top(names)]
        types_b = grab(text, "m_szType")
        types = [int(x) for x in split_top(types_b)] if types_b else []
        expl_b = grab(text, "m_szExplain")
        expl = [unquote(x) for x in split_top(expl_b)] if expl_b else []
        tagno = re.search(r"m_nTagNO\s*=\s*(-?\d+)", text)
        keymode = re.search(r"m_nKeyMode\s*=\s*(-?\d+)", text)
        edname = re.search(rf'#define\s+\w*EDITNAME\s+{STR}', text)

        schemas[os.path.basename(p)] = {
            "editor_title": unquote(edname.group(1)) if edname else None,
            "tag_no": int(tagno.group(1)) if tagno else None,
            "key_mode": int(keymode.group(1)) if keymode else None,
            "columns": cols,
            "types": types,
            "explain": expl,
        }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(schemas, f, ensure_ascii=False, indent=1)

    print(f"{len(schemas)} schemas -> {OUT}\n")
    for name, s in schemas.items():
        print(f"{name:26s} tag={s['tag_no']} key={s['key_mode']} "
              f"cols={len(s['columns']):3d}  {s['editor_title']}")
        if len(s["columns"]) != len(s["types"]):
            print(f"   !! column/type count mismatch: "
                  f"{len(s['columns'])} vs {len(s['types'])}")


if __name__ == "__main__":
    main()
