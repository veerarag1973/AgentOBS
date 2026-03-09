"""Tests for agentobs.integrations.openai and agentobs.integrations._pricing.

All tests run without the real ``openai`` package installed — the OpenAI SDK
is simulated via lightweight mock objects and sys.modules injection.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers — mock OpenAI response objects
# ---------------------------------------------------------------------------


def _make_usage(
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    total_tokens: int = 150,
    cached_tokens: int | None = None,
    reasoning_tokens: int | None = None,
) -> Any:
    """Build a mock OpenAI ``CompletionUsage`` object."""
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = total_tokens

    ptd = MagicMock()
    ptd.cached_tokens = cached_tokens
    usage.prompt_tokens_details = ptd if cached_tokens is not None else None

    ctd = MagicMock()
    ctd.reasoning_tokens = reasoning_tokens
    usage.completion_tokens_details = ctd if reasoning_tokens is not None else None

    return usage


def _make_response(  # noqa: PLR0913
    model: str = "gpt-4o",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    total_tokens: int = 150,
    cached_tokens: int | None = None,
    reasoning_tokens: int | None = None,
) -> Any:
    """Build a mock ``ChatCompletion`` response object."""
    resp = MagicMock()
    resp.model = model
    resp.usage = _make_usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )
    return resp


# ---------------------------------------------------------------------------
# Pricing module tests
# ---------------------------------------------------------------------------


class TestPricingTable:
    def test_known_models_present(self) -> None:
        from agentobs.integrations._pricing import OPENAI_PRICING  # noqa: PLC0415

        for model in ("gpt-4o", "gpt-4o-mini", "o1", "o3-mini", "gpt-3.5-turbo"):
            assert model in OPENAI_PRICING, f"{model} missing from pricing table"

    def test_every_entry_has_input_output(self) -> None:
        from agentobs.integrations._pricing import OPENAI_PRICING  # noqa: PLC0415

        for model, p in OPENAI_PRICING.items():
            assert "input" in p, f"{model}: missing 'input'"
            assert "output" in p, f"{model}: missing 'output'"

    def test_get_pricing_exact_match(self) -> None:
        from agentobs.integrations._pricing import get_pricing  # noqa: PLC0415

        p = get_pricing("gpt-4o")
        assert p is not None
        assert p["input"] == pytest.approx(2.50)
        assert p["output"] == pytest.approx(10.00)

    def test_get_pricing_unknown_returns_none(self) -> None:
        from agentobs.integrations._pricing import get_pricing  # noqa: PLC0415

        assert get_pricing("nonexistent-model-xyz") is None

    def test_get_pricing_cached_input(self) -> None:
        from agentobs.integrations._pricing import get_pricing  # noqa: PLC0415

        p = get_pricing("gpt-4o-mini")
        assert p is not None
        assert "cached_input" in p
        assert p["cached_input"] == pytest.approx(0.075)

    def test_get_pricing_o1_has_reasoning(self) -> None:
        from agentobs.integrations._pricing import get_pricing  # noqa: PLC0415

        p = get_pricing("o1")
        assert p is not None
        assert "reasoning" in p

    def test_get_pricing_embedding_zero_output(self) -> None:
        from agentobs.integrations._pricing import get_pricing  # noqa: PLC0415

        p = get_pricing("text-embedding-3-small")
        assert p is not None
        assert p["output"] == pytest.approx(0.0)

    def test_list_models_returns_sorted_list(self) -> None:
        from agentobs.integrations._pricing import list_models  # noqa: PLC0415

        models = list_models()
        assert isinstance(models, list)
        assert models == sorted(models)
        assert "gpt-4o" in models

    def test_pricing_date_is_set(self) -> None:
        from agentobs.integrations._pricing import PRICING_DATE  # noqa: PLC0415

        assert len(PRICING_DATE) == 10  # YYYY-MM-DD
        assert PRICING_DATE.startswith("20")

    def test_all_prices_non_negative(self) -> None:
        from agentobs.integrations._pricing import OPENAI_PRICING  # noqa: PLC0415

        for model, p in OPENAI_PRICING.items():
            for field, val in p.items():
                assert val >= 0, f"{model}.{field} is negative"


# ---------------------------------------------------------------------------
# normalize_response tests
# ---------------------------------------------------------------------------


class TestNormalizeResponse:
    def test_basic_usage(self) -> None:
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415
        from agentobs.namespaces.trace import GenAISystem  # noqa: PLC0415

        resp = _make_response(model="gpt-4o", prompt_tokens=100, completion_tokens=50, total_tokens=150)  # noqa: E501
        token_usage, model_info, cost = normalize_response(resp)

        assert token_usage.input_tokens == 100
        assert token_usage.output_tokens == 50
        assert token_usage.total_tokens == 150
        assert token_usage.cached_tokens is None
        assert token_usage.reasoning_tokens is None

        assert model_info.name == "gpt-4o"
        assert model_info.system == GenAISystem.OPENAI

        assert cost.input_cost_usd > 0
        assert cost.output_cost_usd > 0
        assert cost.total_cost_usd > 0

    def test_with_cached_tokens(self) -> None:
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = _make_response(
            model="gpt-4o",
            prompt_tokens=200,
            completion_tokens=50,
            total_tokens=250,
            cached_tokens=100,
        )
        token_usage, _, cost = normalize_response(resp)

        assert token_usage.cached_tokens == 100
        assert cost.cached_discount_usd > 0  # 100 cached @ cheaper rate → discount

    def test_with_reasoning_tokens_o1(self) -> None:
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = _make_response(
            model="o1",
            prompt_tokens=50,
            completion_tokens=200,
            total_tokens=250,
            reasoning_tokens=150,
        )
        token_usage, _, cost = normalize_response(resp)

        assert token_usage.reasoning_tokens == 150
        assert cost.reasoning_cost_usd > 0

    def test_unknown_model_zero_cost(self) -> None:
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = _make_response(model="unknown-future-model-v99")
        _, _, cost = normalize_response(resp)

        assert cost.total_cost_usd == pytest.approx(0.0)
        assert cost.input_cost_usd == pytest.approx(0.0)

    def test_null_usage_gives_zero_tokens(self) -> None:
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = MagicMock()
        resp.model = "gpt-4o"
        resp.usage = None
        token_usage, _model_info, cost = normalize_response(resp)

        assert token_usage.input_tokens == 0
        assert token_usage.output_tokens == 0
        assert cost.total_cost_usd == pytest.approx(0.0)

    def test_missing_model_uses_unknown(self) -> None:
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = MagicMock()
        resp.model = None
        resp.usage = None
        _, model_info, _ = normalize_response(resp)

        assert model_info.name == "unknown"

    def test_gpt4o_mini_cost_calculation(self) -> None:
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = _make_response(
            model="gpt-4o-mini",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            total_tokens=2_000_000,
        )
        _, _, cost = normalize_response(resp)

        # 1M input @ $0.15 + 1M output @ $0.60 = $0.75
        assert abs(cost.input_cost_usd - 0.15) < 1e-6
        assert abs(cost.output_cost_usd - 0.60) < 1e-6
        assert abs(cost.total_cost_usd - 0.75) < 1e-6

    def test_gpt4o_cost_calculation(self) -> None:
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = _make_response(
            model="gpt-4o",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            total_tokens=2_000_000,
        )
        _, _, cost = normalize_response(resp)

        # 1M input @ $2.50 + 1M output @ $10.00 = $12.50
        assert abs(cost.input_cost_usd - 2.50) < 1e-6
        assert abs(cost.output_cost_usd - 10.00) < 1e-6
        assert abs(cost.total_cost_usd - 12.50) < 1e-6

    def test_cost_breakdown_is_valid_dataclass(self) -> None:
        """CostBreakdown validates total = input + output + reasoning - discount."""
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415
        from agentobs.namespaces.trace import CostBreakdown  # noqa: PLC0415

        resp = _make_response(
            model="gpt-4o",
            prompt_tokens=500,
            completion_tokens=250,
            total_tokens=750,
            cached_tokens=100,
        )
        _, _, cost = normalize_response(resp)

        # Re-validate by round-tripping through from_dict
        cost2 = CostBreakdown.from_dict(cost.to_dict())
        assert abs(cost2.total_cost_usd - cost.total_cost_usd) < 1e-9

    def test_o3_mini_cost(self) -> None:
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = _make_response(
            model="o3-mini",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            total_tokens=2_000_000,
        )
        _, _, cost = normalize_response(resp)

        # o3-mini: $1.10 input + $4.40 output = $5.50
        assert abs(cost.input_cost_usd - 1.10) < 1e-6
        assert abs(cost.output_cost_usd - 4.40) < 1e-6


# ---------------------------------------------------------------------------
# _compute_cost tests
# ---------------------------------------------------------------------------


class TestComputeCost:
    def test_zero_tokens_zero_cost(self) -> None:
        from agentobs.integrations.openai import _compute_cost  # noqa: PLC0415

        cost = _compute_cost("gpt-4o", 0, 0, None, None)
        assert cost.total_cost_usd == pytest.approx(0.0)

    def test_unknown_model_zero_cost(self) -> None:
        from agentobs.integrations.openai import _compute_cost  # noqa: PLC0415

        cost = _compute_cost("my-internal-llm", 1000, 500, None, None)
        assert cost.total_cost_usd == pytest.approx(0.0)

    def test_cached_discount_reduces_cost(self) -> None:
        from agentobs.integrations.openai import _compute_cost  # noqa: PLC0415

        # Without cache discount
        cost_no_cache = _compute_cost("gpt-4o", 1000, 500, None, None)
        # With 500 cached tokens (half input)
        cost_cached = _compute_cost("gpt-4o", 1000, 500, 500, None)

        assert cost_cached.cached_discount_usd > 0
        assert cost_cached.total_cost_usd < cost_no_cache.total_cost_usd

    def test_reasoning_tokens_separate_rate(self) -> None:
        from agentobs.integrations.openai import _compute_cost  # noqa: PLC0415

        cost = _compute_cost("o1", 100, 200, None, 150)
        assert cost.reasoning_cost_usd > 0
        # reasoning_tokens=150 of 200 completion → 50 regular output
        # total should be less than full output at regular rate

    def test_non_negative_total(self) -> None:
        """Even with large cached discount, total is clamped to >= 0."""
        from agentobs.integrations.openai import _compute_cost  # noqa: PLC0415

        # This edge case shouldn't arise with real data, but guard it anyway
        cost = _compute_cost("gpt-4o", 10, 5, 10, None)
        assert cost.total_cost_usd >= 0.0

    def test_pricing_date_attached(self) -> None:
        from agentobs.integrations._pricing import PRICING_DATE  # noqa: PLC0415
        from agentobs.integrations.openai import _compute_cost  # noqa: PLC0415

        cost = _compute_cost("gpt-4o", 1000, 500, None, None)
        assert cost.pricing_date == PRICING_DATE


# ---------------------------------------------------------------------------
# patch / unpatch / is_patched tests (via mocked openai module)
# ---------------------------------------------------------------------------


def _build_mock_openai() -> types.ModuleType:
    """Construct a minimal fake openai package in sys.modules."""
    openai_mod = types.ModuleType("openai")

    # Use real functions so functools.wraps can copy __name__ / __doc__ safely
    def _sync_create(*args: Any, **kwargs: Any) -> Any:
        return _make_response()

    async def _async_create(*args: Any, **kwargs: Any) -> Any:  # NOSONAR
        return _make_response()

    # Completions class (sync)
    completions_cls = MagicMock()
    completions_cls.create = _sync_create

    # AsyncCompletions class
    async_completions_cls = MagicMock()
    async_completions_cls.create = _async_create

    # Put the class under openai.resources.chat.completions
    resources_mod = types.ModuleType("openai.resources")
    chat_mod = types.ModuleType("openai.resources.chat")
    completions_mod = types.ModuleType("openai.resources.chat.completions")
    completions_mod.Completions = completions_cls  # type: ignore[attr-defined]
    completions_mod.AsyncCompletions = async_completions_cls  # type: ignore[attr-defined]

    sys.modules["openai"] = openai_mod
    sys.modules["openai.resources"] = resources_mod
    sys.modules["openai.resources.chat"] = chat_mod
    sys.modules["openai.resources.chat.completions"] = completions_mod

    return openai_mod


def _uninstall_mock_openai() -> None:
    for key in list(sys.modules):
        if key == "openai" or key.startswith("openai."):
            del sys.modules[key]


class TestPatchLifecycle:
    def setup_method(self) -> None:
        # Remove real or leftover mock openai from sys.modules
        for key in list(sys.modules):
            if key == "openai" or key.startswith("openai."):
                del sys.modules[key]

    def teardown_method(self) -> None:
        for key in list(sys.modules):
            if key == "openai" or key.startswith("openai."):
                del sys.modules[key]

    def test_is_patched_false_when_not_installed(self) -> None:
        from agentobs.integrations.openai import is_patched  # noqa: PLC0415

        # No openai in sys.modules → returns False (not raises)
        assert is_patched() is False

    def test_patch_raises_without_openai(self) -> None:
        from agentobs.integrations.openai import patch  # noqa: PLC0415

        with pytest.raises(ImportError, match="openai"):
            patch()

    def test_unpatch_noop_without_openai(self) -> None:
        from agentobs.integrations.openai import unpatch  # noqa: PLC0415

        with pytest.raises(ImportError, match="openai"):
            unpatch()

    def test_patch_sets_flag(self) -> None:
        from agentobs.integrations.openai import is_patched, patch  # noqa: PLC0415

        _build_mock_openai()
        try:
            patch()
            assert is_patched() is True
        finally:
            _uninstall_mock_openai()

    def test_patch_idempotent(self) -> None:
        from agentobs.integrations.openai import is_patched, patch  # noqa: PLC0415

        _build_mock_openai()
        try:
            patch()
            patch()  # second call must not raise or double-wrap
            assert is_patched() is True
        finally:
            _uninstall_mock_openai()

    def test_unpatch_removes_flag(self) -> None:
        from agentobs.integrations.openai import is_patched, patch, unpatch  # noqa: PLC0415

        _build_mock_openai()
        try:
            patch()
            assert is_patched() is True
            unpatch()
            assert is_patched() is False
        finally:
            _uninstall_mock_openai()

    def test_unpatch_noop_when_not_patched(self) -> None:
        """unpatch() when already unpatched should not raise."""
        from agentobs.integrations.openai import unpatch  # noqa: PLC0415

        _build_mock_openai()
        try:
            unpatch()  # should not raise
        finally:
            _uninstall_mock_openai()


# ---------------------------------------------------------------------------
# _auto_populate_span tests
# ---------------------------------------------------------------------------


class TestAutoPopulateSpan:
    def test_populates_active_span(self) -> None:
        from agentobs._span import SpanContextManager  # noqa: PLC0415
        from agentobs.integrations.openai import _auto_populate_span  # noqa: PLC0415

        with SpanContextManager("test-span") as span:
            assert span.token_usage is None
            assert span.cost is None

            resp = _make_response(model="gpt-4o-mini", prompt_tokens=100, completion_tokens=30, total_tokens=130)  # noqa: E501
            _auto_populate_span(resp)

            assert span.token_usage is not None
            assert span.token_usage.input_tokens == 100
            assert span.token_usage.output_tokens == 30
            assert span.cost is not None
            assert span.cost.total_cost_usd > 0

    def test_does_not_overwrite_manual_token_usage(self) -> None:
        from agentobs._span import SpanContextManager  # noqa: PLC0415
        from agentobs.integrations.openai import _auto_populate_span  # noqa: PLC0415
        from agentobs.namespaces.trace import TokenUsage  # noqa: PLC0415

        manual_usage = TokenUsage(input_tokens=999, output_tokens=1, total_tokens=1000)

        with SpanContextManager("test-span") as span:
            span.token_usage = manual_usage
            _auto_populate_span(_make_response())
            # Should not have been overwritten
            assert span.token_usage is manual_usage
            assert span.token_usage.input_tokens == 999

    def test_no_active_span_is_noop(self) -> None:
        from agentobs._span import _span_stack  # noqa: PLC0415
        from agentobs.integrations.openai import _auto_populate_span  # noqa: PLC0415

        # Ensure stack is empty
        stack = _span_stack()
        assert len(stack) == 0

        # Must not raise
        _auto_populate_span(_make_response())

    def test_sets_model_name_if_not_set(self) -> None:
        from agentobs._span import SpanContextManager  # noqa: PLC0415
        from agentobs.integrations.openai import _auto_populate_span  # noqa: PLC0415

        with SpanContextManager("test-span") as span:
            assert span.model is None
            _auto_populate_span(_make_response(model="gpt-4o"))
            assert span.model == "gpt-4o"

    def test_does_not_overwrite_existing_model(self) -> None:
        from agentobs._span import SpanContextManager  # noqa: PLC0415
        from agentobs.integrations.openai import _auto_populate_span  # noqa: PLC0415

        with SpanContextManager("test-span", model="my-custom-model") as span:
            _auto_populate_span(_make_response(model="gpt-4o"))
            assert span.model == "my-custom-model"

    def test_malformed_response_does_not_raise(self) -> None:
        from agentobs._span import SpanContextManager  # noqa: PLC0415
        from agentobs.integrations.openai import _auto_populate_span  # noqa: PLC0415

        with SpanContextManager("test-span") as span:  # noqa: F841
            _auto_populate_span("not-a-response-object")  # should swallow error


# ---------------------------------------------------------------------------
# Integration: end-to-end with captured span output
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_span_payload_contains_token_data(self) -> None:
        """After _auto_populate_span, to_span_payload includes token_usage."""
        from agentobs._span import Span, _now_ns, _span_id, _trace_id  # noqa: PLC0415

        span = Span(
            name="e2e-test",
            span_id=_span_id(),
            trace_id=_trace_id(),
            start_ns=_now_ns(),
        )
        # Simulate the wrapper setting data
        resp = _make_response(model="gpt-4o-mini", prompt_tokens=200, completion_tokens=80, total_tokens=280)  # noqa: E501
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415
        token_usage, model_info, cost = normalize_response(resp)
        span.token_usage = token_usage
        span.cost = cost
        span.model = model_info.name
        span.end()

        payload = span.to_span_payload()
        assert payload.token_usage is not None
        assert payload.token_usage.input_tokens == 200
        assert payload.cost is not None
        assert payload.cost.total_cost_usd > 0
        assert payload.model is not None
        assert payload.model.name == "gpt-4o-mini"

    def test_normalize_then_validate_cost_breakdown(self) -> None:
        """CostBreakdown from normalize_response passes its own __post_init__."""
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        # This exercises the __post_init__ validator in CostBreakdown
        for model in ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini", "gpt-3.5-turbo"]:
            resp = _make_response(
                model=model,
                prompt_tokens=500,
                completion_tokens=250,
                total_tokens=750,
            )
            _, _, cost = normalize_response(resp)
            # If __post_init__ didn't raise, the math is consistent
            assert cost.total_cost_usd >= 0

    def test_with_cached_tokens_cost_is_less(self) -> None:
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        base = _make_response("gpt-4o", 1000, 500, 1500)
        cached = _make_response("gpt-4o", 1000, 500, 1500, cached_tokens=500)

        _, _, cost_base = normalize_response(base)
        _, _, cost_cached = normalize_response(cached)

        assert cost_cached.total_cost_usd < cost_base.total_cost_usd
        assert cost_cached.cached_discount_usd > 0


# ---------------------------------------------------------------------------
# Coverage gap-filler tests
# ---------------------------------------------------------------------------


class TestPricingFallback:
    def test_get_pricing_strips_version_suffix(self) -> None:
        """get_pricing should match 'gpt-4o-mini' for 'gpt-4o-mini-2099-01-01'."""
        from agentobs.integrations._pricing import OPENAI_PRICING, get_pricing  # noqa: PLC0415

        # Add a fake model with a version suffix to the table temporarily
        OPENAI_PRICING["test-base-model"] = {"input": 1.0, "output": 2.0}
        try:
            # This should fall back to "test-base-model" via prefix stripping
            result = get_pricing("test-base-model-2099-01-01")
            assert result is not None
            assert result["input"] == pytest.approx(1.0)
        finally:
            del OPENAI_PRICING["test-base-model"]

    def test_get_pricing_version_strip_no_match(self) -> None:
        """Stripping prefixes that still don't match returns None."""
        from agentobs.integrations._pricing import get_pricing  # noqa: PLC0415

        assert get_pricing("completely-unknown-xyz-2099-01-01") is None


class TestPatchedMethodInvocation:
    """Test that the wrapper bodies (lines 95-97, 110-112) actually execute."""

    def setup_method(self) -> None:
        for key in list(sys.modules):
            if key == "openai" or key.startswith("openai."):
                del sys.modules[key]

    def teardown_method(self) -> None:
        for key in list(sys.modules):
            if key == "openai" or key.startswith("openai."):
                del sys.modules[key]

    def test_patched_sync_create_populates_span(self) -> None:
        """Calling the patched Completions.create executes the wrapper body."""
        from agentobs._span import SpanContextManager  # noqa: PLC0415
        from agentobs.integrations.openai import patch  # noqa: PLC0415

        _build_mock_openai()
        patch()

        completions_mod = sys.modules["openai.resources.chat.completions"]
        Completions = completions_mod.Completions  # noqa: N806

        with SpanContextManager("test") as span:
            # Call the patched create — simulates an actual API call
            Completions.create(None)  # None as self
            assert span.token_usage is not None

    def test_patched_async_create_populates_span(self) -> None:
        """Calling the patched AsyncCompletions.create executes the async wrapper."""
        from agentobs._span import SpanContextManager  # noqa: PLC0415
        from agentobs.integrations.openai import patch  # noqa: PLC0415

        _build_mock_openai()
        patch()

        completions_mod = sys.modules["openai.resources.chat.completions"]
        AsyncCompletions = completions_mod.AsyncCompletions  # noqa: N806

        async def _run() -> None:
            async with SpanContextManager("async-test") as span:
                await AsyncCompletions.create(None)
                assert span.token_usage is not None

        asyncio.run(_run())


class TestNormalizeResponseBranches:
    """Cover the branch gaps in normalize_response (lines 219->223, 226->229)."""

    def test_prompt_tokens_details_none(self) -> None:
        """ptd is None → cached_tokens stays None."""
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = MagicMock()
        resp.model = "gpt-4o"
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 150
        usage.prompt_tokens_details = None   # <-- ptd is None
        usage.completion_tokens_details = None
        resp.usage = usage

        token_usage, _, _ = normalize_response(resp)
        assert token_usage.cached_tokens is None

    def test_completion_tokens_details_none(self) -> None:
        """ctd is None → reasoning_tokens stays None."""
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = MagicMock()
        resp.model = "gpt-4o"
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 150
        usage.prompt_tokens_details = None
        usage.completion_tokens_details = None
        resp.usage = usage

        token_usage, _, _ = normalize_response(resp)
        assert token_usage.reasoning_tokens is None

    def test_cached_tokens_subfield_is_none(self) -> None:
        """ptd is not None but cached_tokens field is None."""
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = MagicMock()
        resp.model = "gpt-4o"
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 150

        ptd = MagicMock()
        ptd.cached_tokens = None  # <-- explicitly None
        usage.prompt_tokens_details = ptd
        usage.completion_tokens_details = None
        resp.usage = usage

        token_usage, _, _ = normalize_response(resp)
        assert token_usage.cached_tokens is None

    def test_reasoning_tokens_subfield_is_none(self) -> None:
        """ctd is not None but reasoning_tokens field is None."""
        from agentobs.integrations.openai import normalize_response  # noqa: PLC0415

        resp = MagicMock()
        resp.model = "gpt-4o"
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 150
        usage.prompt_tokens_details = None

        ctd = MagicMock()
        ctd.reasoning_tokens = None  # <-- explicitly None
        usage.completion_tokens_details = ctd
        resp.usage = usage

        token_usage, _, _ = normalize_response(resp)
        assert token_usage.reasoning_tokens is None
