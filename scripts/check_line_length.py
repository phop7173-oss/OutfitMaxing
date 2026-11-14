#!/usr/bin/env python3
"""Fail if a Python file under backend/ has a line longer than the ruff limit (100).

Ruff is not installable on Termux, so this stdlib script gives the same E501 signal locally.
It counts characters; ruff counts display width and ignores a few special cases (such as a
line that is only a long URL), so CI's ruff remains the authority.
"""

import sys
from pathlib import Path

LIMIT = 100  # keep in sync with [tool.ruff] line-length in backend/pyproject.toml
SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules"}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    problems: list[str] = []
    for path in sorted((root / "backend").rglob("*.py")):
        if SKIP_DIRS & set(path.parts):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if len(line) > LIMIT:
                problems.append(f"{path.relative_to(root)}:{number}: {len(line)} > {LIMIT}")
    if problems:
        print("Lines over the limit (ruff E501 would fail CI):")
        print("\n".join(problems))
        return 1
    print(f"OK: no Python line over {LIMIT} characters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
