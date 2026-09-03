#!/usr/bin/env python3
"""Normalise Python source indentation to clean 4-space multiples.

Used to undo the write-tool's spurious extra leading space that produces
IndentationErrors. Only leading whitespace is touched; line content is
preserved exactly.
"""
from __future__ import annotations

import sys


def normalise(text: str) -> str:
    out = []
    for line in text.split("\n"):
        indent = len(line) - len(line.lstrip(" "))
        snap = (indent // 4) * 4
        out.append(" " * snap + line.lstrip(" "))
    return "\n".join(out)


def main() -> int:
    for path in sys.argv[1:]:
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        fixed = normalise(src)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(fixed)
        # Quick syntax sanity check.
        try:
            compile(fixed, path, "exec")
            print(f"OK  {path}")
        except SyntaxError as exc:
            print(f"BAD {path}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
