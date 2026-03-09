"""agentobs.lint._flake8 — flake8 plugin for agentobs lint checks.

Register this plugin via ``pyproject.toml``::

    [project.entry-points."flake8.extension"]
    AO = "agentobs.lint._flake8:AgentOBSChecker"

flake8 then calls ``AgentOBSChecker(tree, filename=...).run()`` and collects
``(line, col, message, type)`` tuples.
"""

from __future__ import annotations

import ast
from typing import Generator

from agentobs.lint._checks import run_checks

__all__ = ["AgentOBSChecker"]


class AgentOBSChecker:
    """flake8 plugin that wraps :func:`agentobs.lint.run_checks`."""

    name = "agentobs-lint"
    version = "1.0.8"

    def __init__(self, tree: ast.AST, filename: str = "<unknown>") -> None:
        self.tree = tree
        self.filename = filename

    def run(self) -> Generator[tuple[int, int, str, type], None, None]:
        """Yield ``(line, col, message, type)`` tuples for each lint error."""
        # We need the original source so that run_checks can parse it independently.
        # flake8 provides the source via the file_tokens or lines argument in
        # newer APIs; fall back to unparsing the tree as a best-effort approach.
        try:
            source = ast.unparse(self.tree)
        except Exception:  # pragma: no cover
            return

        errors = run_checks(source, filename=self.filename)
        for err in errors:
            yield (
                err.line,
                err.col,
                f"{err.code} {err.message}",
                type(self),
            )
