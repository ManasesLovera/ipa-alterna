#!/usr/bin/env python3
"""Validate that every ```mermaid block in the repo's Markdown actually parses.

A diagram that renders as a grey error box on GitHub is worse than no diagram, and
the failure is invisible in review because Markdown itself stays valid. This walks
every tracked ``*.md`` file, extracts each fenced ``mermaid`` block, and runs it
through ``mmdc`` (the mermaid CLI), reporting every failure rather than stopping at
the first.

Exit code is 0 when all blocks parse, 1 otherwise.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

MERMAID_BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
SKIP_DIRS = {"node_modules", ".venv", ".git", "dist", "build", ".next"}


def iter_markdown(root: pathlib.Path):
    """Yield Markdown files, skipping vendored and build directories."""
    for path in root.rglob("*.md"):
        if SKIP_DIRS & set(path.parts):
            continue
        yield path


def validate_block(block: str) -> tuple[bool, str]:
    """Render one mermaid block with mmdc and report success plus any stderr."""
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "diagram.mmd"
        out = pathlib.Path(tmp) / "diagram.svg"
        src.write_text(block, encoding="utf-8")
        result = subprocess.run(
            ["mmdc", "--input", str(src), "--output", str(out)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0, result.stderr.strip()


def main() -> int:
    """Validate every mermaid block and print a summary."""
    root = pathlib.Path(__file__).resolve().parent.parent
    failures: list[str] = []
    checked = 0

    for path in iter_markdown(root):
        for index, block in enumerate(MERMAID_BLOCK.findall(path.read_text(encoding="utf-8"))):
            checked += 1
            ok, error = validate_block(block)
            if not ok:
                rel = path.relative_to(root)
                failures.append(f"{rel} (block {index + 1}): {error[:400]}")

    if failures:
        print(f"{len(failures)} of {checked} mermaid block(s) failed to parse:\n")
        print("\n\n".join(failures))
        return 1

    print(f"all {checked} mermaid block(s) parse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
