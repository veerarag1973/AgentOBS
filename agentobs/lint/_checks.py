"""agentobs.lint._checks — static analysis checks for AgentOBS SDK usage.

Five lint rules:

* **AO001** — ``Event()`` call missing a required field: ``event_type``,
  ``source``, or ``payload``.
* **AO002** — Bare ``str`` literal passed to a ``Redactable``-typed field
  (``actor_id``, ``session_id``, ``user_id``).
* **AO003** — ``event_type`` keyword argument is a string literal that is not
  a registered ``EventType`` value.
* **AO004** — LLM provider API call (e.g. ``openai.chat.completions.create``)
  inside a function that has no active trace context manager.
* **AO005** — ``emit_*`` helper called outside any ``agent_run`` /
  ``agent_step`` context manager.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from agentobs.lint._visitor import AgentOBSVisitor, ScopeKind

__all__ = ["LintError", "run_checks"]

# ---------------------------------------------------------------------------
# Registered EventType wire values — kept in sync with agentobs.types.
# (Inline to avoid importing agentobs at linter startup.)
# ---------------------------------------------------------------------------
_VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "llm.trace.span.started",
        "llm.trace.span.completed",
        "llm.trace.span.failed",
        "llm.trace.agent.step",
        "llm.trace.agent.completed",
        "llm.trace.reasoning.step",
        "llm.cost.token.recorded",
        "llm.cost.session.recorded",
        "llm.cost.attributed",
        "llm.cache.hit",
        "llm.cache.miss",
        "llm.cache.evicted",
        "llm.cache.written",
        "llm.eval.score.recorded",
        "llm.eval.regression.detected",
        "llm.eval.scenario.started",
        "llm.eval.scenario.completed",
        "llm.guard.input.blocked",
        "llm.guard.input.passed",
        "llm.guard.output.blocked",
        "llm.guard.output.passed",
        "llm.fence.validated",
        "llm.fence.retry.triggered",
        "llm.fence.max_retries.exceeded",
        "llm.prompt.rendered",
        "llm.prompt.template.loaded",
        "llm.prompt.version.changed",
        "llm.redact.pii.detected",
        "llm.redact.phi.detected",
        "llm.redact.applied",
        "llm.diff.computed",
        "llm.diff.regression.flagged",
        "llm.template.registered",
        "llm.template.variable.bound",
        "llm.template.validation.failed",
        "llm.audit.key.rotated",
    }
)

# Keyword arguments that must receive a Redactable, not a bare string.
_REDACTABLE_KWARGS: frozenset[str] = frozenset(
    {"actor_id", "session_id", "user_id"}
)

# Call attribute chains that indicate an LLM provider call.
# Stored in inner-to-outer order to match _attr_chain() output.
# E.g. ``openai.chat.completions.create(...)`` → attrs = ["create","completions","chat",...]
_LLM_CALL_PATTERNS: list[tuple[str, ...]] = [
    # openai: client.chat.completions.create(...)
    ("create", "completions", "chat"),
    # openai streaming
    ("stream", "completions", "chat"),
    # openai legacy: client.completions.create(...)
    ("create", "completions"),
    # anthropic: client.messages.create(...)
    ("create", "messages"),
    # anthropic streaming
    ("stream", "messages"),
    # cohere / groq / together: client.chat.create(...)
    ("create", "chat"),
    # generic: model.generate(...)
    ("generate",),
]

# emit_* helpers that must be called inside an agent scope.
_EMIT_AGENT_FNS: frozenset[str] = frozenset(
    {
        "emit_span",
        "emit_agent_step",
        "emit_agent_run",
        "emit_agent_completed",
    }
)


# ---------------------------------------------------------------------------
# LintError
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LintError:
    """A single lint finding."""

    code: str
    message: str
    filename: str
    line: int
    col: int

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}:{self.col}: {self.code} {self.message}"


# ---------------------------------------------------------------------------
# Unified checker visitor
# ---------------------------------------------------------------------------


class _Checker(AgentOBSVisitor):
    """Walk the AST and collect all AO*** errors."""

    def __init__(self, filename: str) -> None:
        super().__init__()
        self.filename = filename
        self.errors: list[LintError] = []

    # ------------------------------------------------------------------
    # AO001 — Event() missing required fields
    # ------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        # Dispatch to individual check handlers
        self._check_ao001(node)
        self._check_ao002(node)
        self._check_ao003(node)
        self._check_ao004(node)
        self._check_ao005(node)
        self.generic_visit(node)

    def _check_ao001(self, node: ast.Call) -> None:
        """AO001 — ``Event()`` called without one of the three required kwargs."""
        if not self._is_call_to(node, "Event"):
            return
        provided = {kw.arg for kw in node.keywords if kw.arg is not None}
        for field in ("event_type", "source", "payload"):
            if field not in provided:
                self.errors.append(
                    LintError(
                        code="AO001",
                        message=(
                            f"Event() is missing required keyword argument '{field}'"
                        ),
                        filename=self.filename,
                        line=node.lineno,
                        col=node.col_offset,
                    )
                )

    # ------------------------------------------------------------------
    # AO002 — bare str literal for actor_id / session_id / user_id
    # ------------------------------------------------------------------

    def _check_ao002(self, node: ast.Call) -> None:
        """AO002 — bare str literal passed where Redactable expected."""
        for kw in node.keywords:
            if kw.arg not in _REDACTABLE_KWARGS:
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                self.errors.append(
                    LintError(
                        code="AO002",
                        message=(
                            f"'{kw.arg}' received a bare str literal; "
                            f"wrap with Redactable() to acknowledge PII handling"
                        ),
                        filename=self.filename,
                        line=kw.value.lineno,
                        col=kw.value.col_offset,
                    )
                )

    # ------------------------------------------------------------------
    # AO003 — event_type str literal not in EventType registry
    # ------------------------------------------------------------------

    def _check_ao003(self, node: ast.Call) -> None:
        """AO003 — event_type kwarg is a str literal not in EventType."""
        for kw in node.keywords:
            if kw.arg != "event_type":
                continue
            if not (
                isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
            ):
                continue
            value: str = kw.value.value
            if value not in _VALID_EVENT_TYPES:
                self.errors.append(
                    LintError(
                        code="AO003",
                        message=(
                            f"event_type string '{value}' is not a registered "
                            f"EventType; use EventType.<MEMBER> instead"
                        ),
                        filename=self.filename,
                        line=kw.value.lineno,
                        col=kw.value.col_offset,
                    )
                )

    # ------------------------------------------------------------------
    # AO004 — LLM provider call outside trace context
    # ------------------------------------------------------------------

    def _check_ao004(self, node: ast.Call) -> None:
        """AO004 — model API call without an enclosing trace context manager."""
        if not self._is_llm_call(node):
            return
        if self._inside_trace_scope():
            return
        # Only flag if we are inside a function (otherwise it's module-level test code)
        if not any(s["kind"] == ScopeKind.FUNCTION for s in self.scope_stack):
            return
        self.errors.append(
            LintError(
                code="AO004",
                message=(
                    "LLM provider call appears outside a trace context "
                    "(with tracer.span() / agent_run() / agent_step())"
                ),
                filename=self.filename,
                line=node.lineno,
                col=node.col_offset,
            )
        )

    # ------------------------------------------------------------------
    # AO005 — emit_* called outside agent context
    # ------------------------------------------------------------------

    def _check_ao005(self, node: ast.Call) -> None:
        """AO005 — emit_span/emit_agent_* called without agent_run/step context."""
        if not self._is_emit_call(node):
            return
        if self._inside_agent_scope():
            return
        # Only flag inside functions
        if not any(s["kind"] == ScopeKind.FUNCTION for s in self.scope_stack):
            return
        fn_name = self._call_name(node)
        self.errors.append(
            LintError(
                code="AO005",
                message=(
                    f"'{fn_name}' called outside agent_run() / agent_step() "
                    "context; the event will have no parent agent span"
                ),
                filename=self.filename,
                line=node.lineno,
                col=node.col_offset,
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_call_to(self, node: ast.Call, name: str) -> bool:
        """Return True if *node* is a call to a name imported from agentobs."""
        if isinstance(node.func, ast.Name) and node.func.id == name:
            return self._is_agentobs_name(name)
        if isinstance(node.func, ast.Attribute) and node.func.attr == name:
            return True
        return False

    def _is_llm_call(self, node: ast.Call) -> bool:
        """Return True if the call looks like a known LLM provider API call."""
        attrs = self._attr_chain(node.func)
        if not attrs:
            return False
        # attrs are innermost → outermost, e.g. ["create", "completions", "chat", "client"]
        # We check if any suffix of attrs (reversed back to call order) matches a pattern.
        for pattern in _LLM_CALL_PATTERNS:
            if len(attrs) >= len(pattern) and attrs[: len(pattern)] == list(pattern):
                return True
        return False

    def _is_emit_call(self, node: ast.Call) -> bool:
        """Return True if the call is to a agentobs emit_* function."""
        fn = self._call_name(node)
        if fn in _EMIT_AGENT_FNS and self._is_agentobs_name(fn):
            return True
        # Also catch obj.emit_span() style
        if isinstance(node.func, ast.Attribute) and node.func.attr in _EMIT_AGENT_FNS:
            return True
        return False

    @staticmethod
    def _attr_chain(node: ast.expr) -> list[str]:
        """Return attribute chain in call order (innermost attr first).

        E.g. ``a.b.c()`` → ``["c", "b", "a"]``.
        """
        parts: list[str] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return parts

    @staticmethod
    def _call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_checks(source: str, filename: str = "<string>") -> list[LintError]:
    """Parse *source* and return a list of :class:`LintError` findings.

    Parameters
    ----------
    source:
        Python source code to analyse.
    filename:
        Path string used in error messages (default ``"<string>"``).

    Returns
    -------
    list[LintError]
        Findings in source-order.  Empty list means no issues found.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [
            LintError(
                code="AO000",
                message=f"SyntaxError: {exc.msg}",
                filename=filename,
                line=exc.lineno or 0,
                col=exc.offset or 0,
            )
        ]

    checker = _Checker(filename)
    checker.visit(tree)
    return sorted(checker.errors, key=lambda e: (e.line, e.col, e.code))
