"""agentobs.lint._visitor — AST visitor base tracking imports and scope.

``AgentOBSVisitor`` walks a Python AST and maintains:

* **import_aliases** — mapping of local name → canonical symbol for any
  ``from agentobs import X`` or ``import agentobs`` statement found.
* **scope_stack** — list of active scope descriptors pushed by function
  definitions and context managers (agent_run / agent_step / span).
"""

from __future__ import annotations

import ast
from typing import Any

__all__ = ["AgentOBSVisitor", "ScopeKind"]


class ScopeKind:
    FUNCTION = "function"
    AGENT_RUN = "agent_run"
    AGENT_STEP = "agent_step"
    SPAN = "span"
    TRACE = "trace"


class AgentOBSVisitor(ast.NodeVisitor):
    """Base AST visitor that tracks agentobs imports and scope nesting."""

    def __init__(self) -> None:
        # Maps local alias → canonical name, e.g. {"trace": "trace",
        # "emit_span": "emit_span", "Event": "Event"}
        self.import_aliases: dict[str, str] = {}
        # Each entry is a dict with at minimum {"kind": ScopeKind.X}
        self.scope_stack: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Import tracking
    # ------------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "agentobs":
                local = alias.asname or "agentobs"
                self.import_aliases[local] = "agentobs"
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and (
            node.module == "agentobs"
            or node.module.startswith("agentobs.")
        ):
            for alias in node.names:
                local = alias.asname or alias.name
                self.import_aliases[local] = alias.name
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Scope tracking
    # ------------------------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_stack.append({"kind": ScopeKind.FUNCTION, "name": node.name})
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope_stack.append({"kind": ScopeKind.FUNCTION, "name": node.name})
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_With(self, node: ast.With) -> None:
        """Track ``with tracer.agent_run() / agent_step() / span()`` blocks."""
        pushed: list[dict[str, Any]] = []
        for item in node.items:
            kind = self._classify_context_manager(item.context_expr)
            if kind:
                entry: dict[str, Any] = {"kind": kind}
                self.scope_stack.append(entry)
                pushed.append(entry)
        self.generic_visit(node)
        for _ in pushed:
            self.scope_stack.pop()

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        pushed: list[dict[str, Any]] = []
        for item in node.items:
            kind = self._classify_context_manager(item.context_expr)
            if kind:
                entry = {"kind": kind}
                self.scope_stack.append(entry)
                pushed.append(entry)
        self.generic_visit(node)
        for _ in pushed:
            self.scope_stack.pop()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _classify_context_manager(self, node: ast.expr) -> str | None:
        """Return a :class:`ScopeKind` string if *node* is a known agentobs CM."""
        # tracer.agent_run() / tracer.agent_step() / tracer.span()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr == "agent_run":
                return ScopeKind.AGENT_RUN
            if attr == "agent_step":
                return ScopeKind.AGENT_STEP
            if attr == "span":
                return ScopeKind.SPAN
        return None

    def _is_agentobs_name(self, name: str) -> bool:
        """Return ``True`` if *name* was imported from agentobs."""
        return name in self.import_aliases

    def _inside_trace_scope(self) -> bool:
        """Return ``True`` when the current position is inside any trace scope."""
        trace_kinds = {ScopeKind.AGENT_RUN, ScopeKind.AGENT_STEP, ScopeKind.SPAN}
        return any(s["kind"] in trace_kinds for s in self.scope_stack)

    def _inside_agent_scope(self) -> bool:
        """Return ``True`` when inside agent_run or agent_step."""
        agent_kinds = {ScopeKind.AGENT_RUN, ScopeKind.AGENT_STEP}
        return any(s["kind"] in agent_kinds for s in self.scope_stack)
