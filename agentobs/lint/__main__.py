"""agentobs.lint.__main__ — CLI entry point.

Usage::

    python -m agentobs.lint [FILES_OR_DIRS ...]

Exits with code 0 if no issues found, 1 otherwise.

Examples::

    python -m agentobs.lint myapp/
    python -m agentobs.lint agents/pipeline.py agents/tools.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from agentobs.lint._checks import run_checks


def _iter_python_files(paths: list[str]) -> list[Path]:
    result: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            result.extend(sorted(p.rglob("*.py")))
        elif p.is_file() and p.suffix == ".py":
            result.append(p)
        else:
            print(f"agentobs.lint: skipping non-Python path: {raw}", file=sys.stderr)
    return result


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print("Usage: python -m agentobs.lint [FILES_OR_DIRS ...]", file=sys.stderr)
        return 2

    files = _iter_python_files(argv)
    if not files:
        print("agentobs.lint: no Python files found", file=sys.stderr)
        return 2

    total_errors = 0
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"agentobs.lint: cannot read {path}: {exc}", file=sys.stderr)
            continue

        errors = run_checks(source, filename=str(path))
        for err in errors:
            print(err)
            total_errors += 1

    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
