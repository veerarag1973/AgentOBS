"""agentobs.integrations.openai — Auto-instrumentation for the OpenAI Python SDK.

This module monkey-patches the OpenAI client so every
``client.chat.completions.create(...)`` call automatically populates the
active :class:`~agentobs._span.Span` with:

* :class:`~agentobs.namespaces.trace.TokenUsage` (input / output / cached /
  reasoning token counts)
* :class:`~agentobs.namespaces.trace.ModelInfo` (provider = ``openai``, name
  from response)
* :class:`~agentobs.namespaces.trace.CostBreakdown` (computed from the static
  pricing table in :mod:`agentobs.integrations._pricing`)

Usage::

    from agentobs.integrations import openai as openai_integration
    openai_integration.patch()

    import openai
    client = openai.OpenAI()

    import agentobs
    agentobs.configure(exporter="console")

    with agentobs.tracer.span("chat", model="gpt-4o") as span:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello"}],
        )
    # → span.token_usage and span.cost auto-populated on exit

Calling ``patch()`` is **idempotent** — calling it multiple times has no
effect.  Call :func:`unpatch` to restore the original methods.

Install with::

    pip install "agentobs[openai]"
"""

from __future__ import annotations

import functools
from typing import Any

from agentobs.integrations._pricing import PRICING_DATE, get_pricing
from agentobs.namespaces.trace import (
    CostBreakdown,
    GenAISystem,
    ModelInfo,
    TokenUsage,
)

__all__ = [
    "is_patched",
    "normalize_response",
    "patch",
    "unpatch",
]

# Sentinel attribute set on the openai module to prevent double-patching.
_PATCH_FLAG = "_agentobs_patched"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def patch() -> None:
    """Monkey-patch the OpenAI client to auto-instrument all chat completions.

    Wraps both ``openai.resources.chat.completions.Completions.create``
    (sync) and ``AsyncCompletions.create`` (async).  The wrapper calls
    :func:`normalize_response` on the result and, if a span is currently
    active on this thread, updates it with token usage, model info, and cost.

    This function is **idempotent** — safe to call multiple times.

    Raises:
        ImportError: If the ``openai`` package is not installed.
    """
    openai_mod = _require_openai()

    if getattr(openai_mod, _PATCH_FLAG, False):
        return  # already patched

    # --- sync ----------------------------------------------------------------
    from openai.resources.chat.completions import (  # noqa: PLC0415
        Completions,  # type: ignore[import-untyped]
    )

    _orig_sync = Completions.create  # type: ignore[attr-defined]

    @functools.wraps(_orig_sync)
    def _patched_sync(self: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        response = _orig_sync(self, *args, **kwargs)
        _auto_populate_span(response)
        return response

    Completions.create = _patched_sync  # type: ignore[method-assign]
    Completions._agentobs_orig_create = _orig_sync  # type: ignore[attr-defined]

    # --- async ---------------------------------------------------------------
    try:
        from openai.resources.chat.completions import (  # noqa: PLC0415
            AsyncCompletions,  # type: ignore[import-untyped]
        )

        _orig_async = AsyncCompletions.create  # type: ignore[attr-defined]

        @functools.wraps(_orig_async)
        async def _patched_async(self: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            response = await _orig_async(self, *args, **kwargs)
            _auto_populate_span(response)
            return response

        AsyncCompletions.create = _patched_async  # type: ignore[method-assign]
        AsyncCompletions._agentobs_orig_create = _orig_async  # type: ignore[attr-defined]
    except (ImportError, AttributeError):  # pragma: no cover
        pass

    setattr(openai_mod, _PATCH_FLAG, True)


def unpatch() -> None:
    """Restore the original OpenAI methods and remove the patch flag.

    Safe to call even if :func:`patch` was never called.

    Raises:
        ImportError: If the ``openai`` package is not installed.
    """
    openai_mod = _require_openai()

    if not getattr(openai_mod, _PATCH_FLAG, False):
        return  # nothing to do

    try:
        from openai.resources.chat.completions import (  # noqa: PLC0415
            Completions,  # type: ignore[import-untyped]
        )

        Completions.create = Completions._agentobs_orig_create  # type: ignore[attr-defined,method-assign]
        del Completions._agentobs_orig_create  # type: ignore[attr-defined]
    except (ImportError, AttributeError):  # pragma: no cover
        pass

    try:
        from openai.resources.chat.completions import (  # noqa: PLC0415
            AsyncCompletions,  # type: ignore[import-untyped]
        )

        AsyncCompletions.create = AsyncCompletions._agentobs_orig_create  # type: ignore[attr-defined,method-assign]
        del AsyncCompletions._agentobs_orig_create  # type: ignore[attr-defined]
    except (ImportError, AttributeError):  # pragma: no cover
        pass

    try:  # noqa: SIM105
        delattr(openai_mod, _PATCH_FLAG)
    except AttributeError:  # pragma: no cover
        pass


def is_patched() -> bool:
    """Return ``True`` if the OpenAI client has been patched by agentobs.

    Returns ``False`` if the ``openai`` package is not installed.
    """
    try:
        openai_mod = _require_openai()
        return bool(getattr(openai_mod, _PATCH_FLAG, False))
    except ImportError:
        return False


def normalize_response(
    response: Any,  # noqa: ANN401
) -> tuple[TokenUsage, ModelInfo, CostBreakdown]:
    """Extract structured observability data from an OpenAI chat completion.

    Works with both ``openai.types.chat.ChatCompletion`` objects and any
    duck-typed mock with the same attribute structure.

    Args:
        response: An OpenAI ``ChatCompletion`` (or compatible object).

    Returns:
        A 3-tuple of ``(TokenUsage, ModelInfo, CostBreakdown)``.

    Field mapping:

    +--------------------------------------------+---------------------------+
    | OpenAI field                               | AgentOBS field             |
    +============================================+===========================+
    | ``response.model``                         | ``ModelInfo.name``        |
    | ``usage.prompt_tokens``                    | ``TokenUsage.input_tokens``|
    | ``usage.completion_tokens``                | ``TokenUsage.output_tokens``|
    | ``usage.total_tokens``                     | ``TokenUsage.total_tokens``|
    | ``usage.prompt_tokens_details.cached_tokens``     | ``TokenUsage.cached_tokens``|
    | ``usage.completion_tokens_details.reasoning_tokens``| ``TokenUsage.reasoning_tokens``|
    +--------------------------------------------+---------------------------+
    """
    # ------------------------------------------------------------------ usage
    usage = getattr(response, "usage", None)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None

    if usage is not None:
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(
            getattr(usage, "total_tokens", input_tokens + output_tokens) or 0
        )

        # Prompt token details (cached)
        ptd = getattr(usage, "prompt_tokens_details", None)
        if ptd is not None:
            ct = getattr(ptd, "cached_tokens", None)
            if ct is not None:
                cached_tokens = int(ct)

        # Completion token details (reasoning)
        ctd = getattr(usage, "completion_tokens_details", None)
        if ctd is not None:
            rt = getattr(ctd, "reasoning_tokens", None)
            if rt is not None:
                reasoning_tokens = int(rt)

    token_usage = TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )

    # ---------------------------------------------------------------- model
    model_name: str = getattr(response, "model", None) or "unknown"
    model_info = ModelInfo(system=GenAISystem.OPENAI, name=model_name)

    # ----------------------------------------------------------------- cost
    cost = _compute_cost(model_name, input_tokens, output_tokens, cached_tokens, reasoning_tokens)

    return token_usage, model_info, cost


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_openai() -> Any:  # noqa: ANN401
    """Import and return the ``openai`` module, raising ``ImportError`` if absent."""
    try:
        import openai  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "The 'openai' package is required for agentobs OpenAI integration.\n"
            "Install it with: pip install 'agentobs[openai]'"
        ) from exc
    else:
        return openai


def _compute_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int | None,
    reasoning_tokens: int | None,
) -> CostBreakdown:
    """Compute :class:`~agentobs.namespaces.trace.CostBreakdown` from token counts.

    Uses the static pricing table.  Falls back to :meth:`CostBreakdown.zero`
    for unknown models.

    Args:
        model_name:      Model name string (as returned by the API).
        input_tokens:    Total input tokens (including any cached tokens).
        output_tokens:   Output / completion tokens.
        cached_tokens:   Subset of input tokens served from the prompt cache.
        reasoning_tokens: Reasoning tokens (o1/o3 models).

    Returns:
        A :class:`~agentobs.namespaces.trace.CostBreakdown` instance.
    """
    pricing = get_pricing(model_name)
    if pricing is None:
        return CostBreakdown.zero()

    input_rate = pricing["input"]   # $/1M tokens
    output_rate = pricing["output"]

    # Full-price input cost (we'll deduct the cached discount separately)
    input_cost = input_tokens * input_rate / 1_000_000.0
    output_cost = output_tokens * output_rate / 1_000_000.0

    # Cached discount: tokens served from cache are billed at cached_input rate
    cached_discount = 0.0
    cached_rate = pricing.get("cached_input")
    if cached_tokens and cached_rate is not None:
        # We already charged these at full input_rate; reduce by the difference
        cached_discount = cached_tokens * (input_rate - cached_rate) / 1_000_000.0
        cached_discount = max(0.0, cached_discount)

    # Reasoning cost: reasoning tokens in o1/o3 are billed at the output rate
    # (already included in output_tokens from the API, so reasoning_cost_usd = 0
    # unless the model has a separate reasoning rate)
    reasoning_cost = 0.0
    reasoning_rate = pricing.get("reasoning")
    if reasoning_tokens and reasoning_rate is not None:
        # Some models bill reasoning tokens at a rate that may differ from the
        # output rate (future-proofing).  For o1, reasoning_rate == output_rate
        # so this branch is arithmetically a no-op, but the code path is kept
        # for models where they diverge.
        # Reasoning tokens are already counted within output_tokens by the API,
        # so we rebill them separately and remove them from regular output cost.
        regular_output = output_tokens - reasoning_tokens
        regular_output = max(0, regular_output)
        output_cost = regular_output * output_rate / 1_000_000.0
        reasoning_cost = reasoning_tokens * reasoning_rate / 1_000_000.0

    total = input_cost + output_cost + reasoning_cost - cached_discount
    total = max(0.0, total)

    return CostBreakdown(
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        total_cost_usd=total,
        cached_discount_usd=cached_discount,
        reasoning_cost_usd=reasoning_cost,
        pricing_date=PRICING_DATE,
    )


def _auto_populate_span(response: Any) -> None:  # noqa: ANN401
    """If there is an active span on this thread, populate it from *response*.

    Silently does nothing if:

    * There is no active span.
    * ``normalize_response`` raises (malformed response).
    * The span already has ``token_usage`` set (don't overwrite manual data).
    """
    try:
        from agentobs._span import _span_stack  # noqa: PLC0415

        stack = _span_stack()
        if not stack:
            return
        span = stack[-1]

        # Don't overwrite data that the user set manually.
        if span.token_usage is not None:
            return

        token_usage, model_info, cost = normalize_response(response)
        span.token_usage = token_usage
        span.cost = cost

        # Update the model string if not already set
        if span.model is None:
            span.model = model_info.name

    except Exception:  # noqa: S110
        # Never let instrumentation errors surface in user code.
        pass
