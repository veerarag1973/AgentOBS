"""agentobs.lint — Static analysis for AgentOBS SDK usage.

Run from the command line::

    python -m agentobs.lint myapp/

Or as a flake8 plugin::

    flake8 --select AO myapp/

Error codes
-----------
AO001  ``Event()`` missing required field (``event_type``, ``source``, or ``payload``)
AO002  Bare ``str`` literal passed where ``Redactable`` is expected
AO003  Unregistered event type string literal
AO004  Model call inside function without active trace context
AO005  ``emit_*()`` called outside ``agent_run()`` / ``agent_step()`` context
"""

from __future__ import annotations

from agentobs.lint._checks import LintError, run_checks

__all__ = [
    "LintError",
    "run_checks",
]
