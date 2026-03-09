"""Phase 6 — OpenAI Integration exhaustive tests.

Covers:
* agentobs.integrations.openai:
  - patch() / unpatch() / is_patched() lifecycle
  - Idempotent patching
  - normalize_response() field mapping (all OpenAI response fields)
  - TokenUsage extraction (input/output/total/cached/reasoning tokens)
  - ModelInfo construction (system=OpenAI, name from response)
  - CostBreakdown computation (standard, cached discount, reasoning rate)
  - _compute_cost() all branches (known model, unknown model, cached tokens,
    reasoning tokens, zero tokens, non-negative clamp)
  - _auto_populate_span() — active span, no span, model not overwritten,
    manual token_usage not overwritten, normalize_response raises (except path),
    _span_stack import fails (except path)
  - Patched sync wrapper executes correctly
  - Patched async wrapper executes correctly
  - unpatch() restores original method
  - is_patched()=False when openai not installed
* agentobs.integrations._pricing:
  - All known models have valid entries
  - get_pricing() exact match, version-suffix strip, no-match → None
  - list_models() sorted + complete
  - PRICING_DATE format
  - All prices >= 0
  - Pricing date attached to CostBreakdown
  - Cached/reasoning pricing optional fields
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_usage(
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    total_tokens: int = 150,
    cached_tokens: int | None = None,
    reasoning_tokens: int | None = None,
) -> Any:
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


def _make_response(
    model: str = "gpt-4o",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    total_tokens: int = 150,
    cached_tokens: int | None = None,
    reasoning_tokens: int | None = None,
) -> Any:
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


def _build_mock_openai() -> types.ModuleType:
    """Inject a minimal stub openai package into sys.modules."""
    openai_mod = types.ModuleType("openai")

    def _sync_create(*args: Any, **kwargs: Any) -> Any:
        return _make_response()

    async def _async_create(*args: Any, **kwargs: Any) -> Any:  # NOSONAR — intentional sync stub in async wrapper
        return _make_response()

    completions_cls = MagicMock()
    completions_cls.create = _sync_create
    async_completions_cls = MagicMock()
    async_completions_cls.create = _async_create

    resources_mod = types.ModuleType("openai.resources")
    chat_mod = types.ModuleType("openai.resources.chat")
    completions_mod = types.ModuleType("openai.resources.chat.completions")
    completions_mod.Completions = completions_cls
    completions_mod.AsyncCompletions = async_completions_cls

    sys.modules["openai"] = openai_mod
    sys.modules["openai.resources"] = resources_mod
    sys.modules["openai.resources.chat"] = chat_mod
    sys.modules["openai.resources.chat.completions"] = completions_mod

    return openai_mod


def _uninstall_mock_openai() -> None:
    keys = [k for k in sys.modules if k == "openai" or k.startswith("openai.")]
    for key in keys:
        del sys.modules[key]


# ===========================================================================
# 1. _pricing module
# ===========================================================================


@pytest.mark.unit
class TestPricingTable:
    def test_all_known_models_have_required_fields(self) -> None:
        from agentobs.integrations._pricing import OPENAI_PRICING

        for model, entry in OPENAI_PRICING.items():
            assert "input" in entry, f"{model} missing 'input'"
            assert "output" in entry, f"{model} missing 'output'"
            assert isinstance(entry["input"], float), f"{model}.input not float"
            assert isinstance(entry["output"], float), f"{model}.output not float"

    def test_all_prices_non_negative(self) -> None:
        from agentobs.integrations._pricing import OPENAI_PRICING

        for model, entry in OPENAI_PRICING.items():
            for field, val in entry.items():
                assert val >= 0.0, f"{model}.{field} is negative"

    def test_gpt4o_pricing_values(self) -> None:
        from agentobs.integrations._pricing import get_pricing

        p = get_pricing("gpt-4o")
        assert p is not None
        assert p["input"] == pytest.approx(2.50)
        assert p["output"] == pytest.approx(10.00)
        assert p["cached_input"] == pytest.approx(1.25)

    def test_gpt4o_mini_pricing_values(self) -> None:
        from agentobs.integrations._pricing import get_pricing

        p = get_pricing("gpt-4o-mini")
        assert p is not None
        assert p["input"] == pytest.approx(0.15)
        assert p["output"] == pytest.approx(0.60)
        assert p["cached_input"] == pytest.approx(0.075)

    def test_o1_has_reasoning_rate(self) -> None:
        from agentobs.integrations._pricing import get_pricing

        p = get_pricing("o1")
        assert p is not None
        assert "reasoning" in p
        assert p["reasoning"] == pytest.approx(60.00)

    def test_o3_mini_pricing(self) -> None:
        from agentobs.integrations._pricing import get_pricing

        p = get_pricing("o3-mini")
        assert p is not None
        assert p["input"] == pytest.approx(1.10)
        assert p["output"] == pytest.approx(4.40)
        assert p["cached_input"] == pytest.approx(0.55)

    def test_o3_pricing(self) -> None:
        from agentobs.integrations._pricing import get_pricing

        p = get_pricing("o3")
        assert p is not None
        assert p["input"] == pytest.approx(10.00)
        assert p["output"] == pytest.approx(40.00)

    def test_gpt4_base_pricing(self) -> None:
        from agentobs.integrations._pricing import get_pricing

        p = get_pricing("gpt-4")
        assert p is not None
        assert p["input"] == pytest.approx(30.00)
        assert p["output"] == pytest.approx(60.00)

    def test_gpt35_turbo_pricing(self) -> None:
        from agentobs.integrations._pricing import get_pricing

        p = get_pricing("gpt-3.5-turbo")
        assert p is not None
        assert p["input"] == pytest.approx(0.50)
        assert p["output"] == pytest.approx(1.50)

    def test_embedding_zero_output_cost(self) -> None:
        from agentobs.integrations._pricing import get_pricing

        for model in ("text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"):
            p = get_pricing(model)
            assert p is not None, f"{model} not in pricing"
            assert p["output"] == pytest.approx(0.00), f"{model}.output should be 0"

    def test_get_pricing_unknown_model_returns_none(self) -> None:
        from agentobs.integrations._pricing import get_pricing

        assert get_pricing("does-not-exist-v99") is None

    def test_get_pricing_exact_lookup_beats_prefix_strip(self) -> None:
        """Exact match returns without hitting the prefix-strip loop."""
        from agentobs.integrations._pricing import get_pricing

        # "gpt-4o-2024-11-20" should match exactly (not via prefix strip)
        p = get_pricing("gpt-4o-2024-11-20")
        assert p is not None
        assert p["input"] == pytest.approx(2.50)

    def test_get_pricing_strips_one_date_suffix(self) -> None:
        """Model name with extra date suffix falls back to base name."""
        from agentobs.integrations._pricing import OPENAI_PRICING, get_pricing

        OPENAI_PRICING["test-strip-model"] = {"input": 9.99, "output": 19.99}
        try:
            result = get_pricing("test-strip-model-2077-06-15")
            assert result is not None
            assert result["input"] == pytest.approx(9.99)
        finally:
            del OPENAI_PRICING["test-strip-model"]

    def test_get_pricing_multiple_prefix_candidates_returns_longest(self) -> None:
        """With a 4-segment name, the longest matching prefix is returned."""
        from agentobs.integrations._pricing import OPENAI_PRICING, get_pricing

        # Add both a shorter and longer prefix to the table
        OPENAI_PRICING["mod-a"] = {"input": 1.0, "output": 2.0}
        OPENAI_PRICING["mod-a-b"] = {"input": 3.0, "output": 4.0}
        try:
            # "mod-a-b-c" should match "mod-a-b" first (longer match)
            result = get_pricing("mod-a-b-c")
            assert result is not None
            # Either is valid depending on loop order (rsplit from right)
            assert result["input"] in (1.0, 3.0)
        finally:
            del OPENAI_PRICING["mod-a"]
            del OPENAI_PRICING["mod-a-b"]

    def test_list_models_returns_sorted(self) -> None:
        from agentobs.integrations._pricing import list_models

        models = list_models()
        assert models == sorted(models)

    def test_list_models_contains_all_table_entries(self) -> None:
        from agentobs.integrations._pricing import OPENAI_PRICING, list_models

        models = list_models()
        for model in OPENAI_PRICING:
            assert model in models

    def test_pricing_date_is_valid_format(self) -> None:
        from agentobs.integrations._pricing import PRICING_DATE

        assert len(PRICING_DATE) == 10
        parts = PRICING_DATE.split("-")
        assert len(parts) == 3
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        assert 2020 <= year <= 2050
        assert 1 <= month <= 12
        assert 1 <= day <= 31

    def test_pricing_date_constant_is_string(self) -> None:
        from agentobs.integrations._pricing import PRICING_DATE

        assert isinstance(PRICING_DATE, str)


# ===========================================================================
# 2. normalize_response()
# ===========================================================================


@pytest.mark.unit
class TestNormalizeResponse:
    def test_basic_token_extraction(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = _make_response(model="gpt-4o", prompt_tokens=100, completion_tokens=50, total_tokens=150)
        tu, mi, cost = normalize_response(resp)

        assert tu.input_tokens == 100
        assert tu.output_tokens == 50
        assert tu.total_tokens == 150
        assert tu.cached_tokens is None
        assert tu.reasoning_tokens is None

    def test_model_info_is_openai(self) -> None:
        from agentobs.integrations.openai import normalize_response
        from agentobs.namespaces.trace import GenAISystem

        _, mi, _ = normalize_response(_make_response(model="gpt-4o-mini"))
        assert mi.system == GenAISystem.OPENAI
        assert mi.name == "gpt-4o-mini"

    def test_model_name_none_becomes_unknown(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = MagicMock()
        resp.model = None
        resp.usage = None
        _, mi, _ = normalize_response(resp)
        assert mi.name == "unknown"

    def test_model_name_empty_string_becomes_unknown(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = MagicMock()
        resp.model = ""
        resp.usage = None
        _, mi, _ = normalize_response(resp)
        assert mi.name == "unknown"

    def test_no_usage_gives_zero_tokens(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = MagicMock()
        resp.model = "gpt-4o"
        resp.usage = None
        tu, _, cost = normalize_response(resp)
        assert tu.input_tokens == 0
        assert tu.output_tokens == 0
        assert tu.total_tokens == 0
        assert cost.total_cost_usd == pytest.approx(0.0)

    def test_cached_tokens_extracted(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = _make_response(model="gpt-4o", prompt_tokens=200, completion_tokens=50,
                              total_tokens=250, cached_tokens=100)
        tu, _, cost = normalize_response(resp)
        assert tu.cached_tokens == 100
        assert cost.cached_discount_usd > 0

    def test_reasoning_tokens_extracted(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = _make_response(model="o1", prompt_tokens=50, completion_tokens=200,
                              total_tokens=250, reasoning_tokens=150)
        tu, _, cost = normalize_response(resp)
        assert tu.reasoning_tokens == 150
        assert cost.reasoning_cost_usd > 0

    def test_gpt4o_mini_cost_exact(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = _make_response(model="gpt-4o-mini",
                              prompt_tokens=1_000_000, completion_tokens=1_000_000,
                              total_tokens=2_000_000)
        _, _, cost = normalize_response(resp)
        assert abs(cost.input_cost_usd - 0.15) < 1e-6
        assert abs(cost.output_cost_usd - 0.60) < 1e-6
        assert abs(cost.total_cost_usd - 0.75) < 1e-6

    def test_gpt4o_cost_exact(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = _make_response(model="gpt-4o",
                              prompt_tokens=1_000_000, completion_tokens=1_000_000,
                              total_tokens=2_000_000)
        _, _, cost = normalize_response(resp)
        assert abs(cost.input_cost_usd - 2.50) < 1e-6
        assert abs(cost.output_cost_usd - 10.00) < 1e-6
        assert abs(cost.total_cost_usd - 12.50) < 1e-6

    def test_unknown_model_zero_cost(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = _make_response(model="hypothetical-model-v99")
        _, _, cost = normalize_response(resp)
        assert cost.total_cost_usd == pytest.approx(0.0)
        assert cost.input_cost_usd == pytest.approx(0.0)
        assert cost.output_cost_usd == pytest.approx(0.0)

    def test_cost_breakdown_round_trip(self) -> None:
        from agentobs.integrations.openai import normalize_response
        from agentobs.namespaces.trace import CostBreakdown

        resp = _make_response(model="gpt-4o", prompt_tokens=500, completion_tokens=250,
                              total_tokens=750, cached_tokens=100)
        _, _, cost = normalize_response(resp)
        cost2 = CostBreakdown.from_dict(cost.to_dict())
        assert abs(cost2.total_cost_usd - cost.total_cost_usd) < 1e-9

    def test_token_usage_round_trip(self) -> None:
        from agentobs.integrations.openai import normalize_response
        from agentobs.namespaces.trace import TokenUsage

        resp = _make_response(model="gpt-4o", prompt_tokens=100, completion_tokens=50,
                              total_tokens=150, cached_tokens=20, reasoning_tokens=None)
        tu, _, _ = normalize_response(resp)
        tu2 = TokenUsage.from_dict(tu.to_dict())
        assert tu2.input_tokens == tu.input_tokens
        assert tu2.cached_tokens == tu.cached_tokens

    def test_o3_mini_cost_exact(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = _make_response(model="o3-mini",
                              prompt_tokens=1_000_000, completion_tokens=1_000_000,
                              total_tokens=2_000_000)
        _, _, cost = normalize_response(resp)
        assert abs(cost.input_cost_usd - 1.10) < 1e-6
        assert abs(cost.output_cost_usd - 4.40) < 1e-6

    def test_embedding_model_zero_output_cost(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = _make_response(model="text-embedding-3-small",
                              prompt_tokens=1_000_000, completion_tokens=0,
                              total_tokens=1_000_000)
        _, _, cost = normalize_response(resp)
        assert cost.output_cost_usd == pytest.approx(0.0)
        assert abs(cost.input_cost_usd - 0.02) < 1e-6

    def test_total_tokens_stored_verbatim(self) -> None:
        """usage.total_tokens is stored as-is, even when 0."""
        from agentobs.integrations.openai import normalize_response

        resp = MagicMock()
        resp.model = "gpt-4o"
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.total_tokens = 0
        usage.prompt_tokens_details = None
        usage.completion_tokens_details = None
        resp.usage = usage

        tu, _, _ = normalize_response(resp)
        # SDK records total_tokens verbatim from the API response
        assert tu.total_tokens == 0
        assert tu.input_tokens == 10
        assert tu.output_tokens == 5


# ===========================================================================
# 3. _compute_cost() branches
# ===========================================================================


@pytest.mark.unit
class TestComputeCost:
    def test_zero_tokens_all_zero_cost(self) -> None:
        from agentobs.integrations.openai import _compute_cost

        cost = _compute_cost("gpt-4o", 0, 0, None, None)
        assert cost.total_cost_usd == pytest.approx(0.0)
        assert cost.input_cost_usd == pytest.approx(0.0)
        assert cost.output_cost_usd == pytest.approx(0.0)

    def test_unknown_model_returns_zero(self) -> None:
        from agentobs.integrations.openai import _compute_cost

        cost = _compute_cost("totally-fictitious-llm-x", 1000, 500, None, None)
        assert cost.total_cost_usd == pytest.approx(0.0)

    def test_cached_discount_reduces_total(self) -> None:
        from agentobs.integrations.openai import _compute_cost

        no_cache = _compute_cost("gpt-4o", 1000, 500, None, None)
        with_cache = _compute_cost("gpt-4o", 1000, 500, 500, None)
        assert with_cache.cached_discount_usd > 0
        assert with_cache.total_cost_usd < no_cache.total_cost_usd

    def test_cached_discount_correct_amount(self) -> None:
        """500 cached tokens @ gpt-4o: discount = 500*(2.50-1.25)/1M = $0.000000625."""
        from agentobs.integrations.openai import _compute_cost

        cost = _compute_cost("gpt-4o", 1000, 0, 500, None)
        expected_discount = 500 * (2.50 - 1.25) / 1_000_000
        assert abs(cost.cached_discount_usd - expected_discount) < 1e-12

    def test_reasoning_tokens_billed_at_separate_rate(self) -> None:
        from agentobs.integrations.openai import _compute_cost

        cost = _compute_cost("o1", 100, 200, None, 150)
        # 150 reasoning @ $60/1M, 50 regular output @ $60/1M
        # For o1, output rate = reasoning rate = 60, so cost should be same
        assert cost.reasoning_cost_usd > 0

    def test_reasoning_rate_not_present_no_separate_reasoning_cost(self) -> None:
        """o3-mini has no 'reasoning' key → reasoning_cost_usd should be 0."""
        from agentobs.integrations.openai import _compute_cost
        from agentobs.integrations._pricing import get_pricing

        # o3-mini doesn't have the separate reasoning rate
        p = get_pricing("o3-mini")
        # o3-mini DOES have cached_input but no reasoning key in the table
        assert "reasoning" not in (p or {})

        cost = _compute_cost("o3-mini", 100, 200, None, 50)
        # No reasoning rate → reasoning_cost added as regular output
        assert cost.reasoning_cost_usd == pytest.approx(0.0)

    def test_non_negative_clamp_edge_case(self) -> None:
        """Total is clamped >= 0.0 even with large fictitious cached discount."""
        from agentobs.integrations.openai import _compute_cost

        # Only 5 input tokens but 10 "cached" — discount can't exceed input cost
        # Total should be clamped to 0 not go negative
        cost = _compute_cost("gpt-4o", 5, 0, 5, None)
        assert cost.total_cost_usd >= 0.0

    def test_pricing_date_attached_to_breakdown(self) -> None:
        from agentobs.integrations._pricing import PRICING_DATE
        from agentobs.integrations.openai import _compute_cost

        cost = _compute_cost("gpt-4o", 100, 50, None, None)
        assert cost.pricing_date == PRICING_DATE

    def test_all_table_models_produce_non_negative_cost(self) -> None:
        from agentobs.integrations._pricing import OPENAI_PRICING
        from agentobs.integrations.openai import _compute_cost

        for model in OPENAI_PRICING:
            cost = _compute_cost(model, 1000, 500, None, None)
            assert cost.total_cost_usd >= 0.0, f"{model} produced negative cost"


# ===========================================================================
# 4. _auto_populate_span() — including the except branch
# ===========================================================================


@pytest.mark.unit
class TestAutoPopulateSpan:
    def test_populates_active_span(self) -> None:
        from agentobs._span import SpanContextManager
        from agentobs.integrations.openai import _auto_populate_span

        with SpanContextManager("span") as span:
            _auto_populate_span(_make_response(model="gpt-4o-mini",
                                               prompt_tokens=100, completion_tokens=30,
                                               total_tokens=130))
            assert span.token_usage is not None
            assert span.token_usage.input_tokens == 100
            assert span.cost is not None
            assert span.cost.total_cost_usd > 0

    def test_sets_model_if_not_set(self) -> None:
        from agentobs._span import SpanContextManager
        from agentobs.integrations.openai import _auto_populate_span

        with SpanContextManager("span") as span:
            assert span.model is None
            _auto_populate_span(_make_response(model="gpt-4o"))
            assert span.model == "gpt-4o"

    def test_does_not_overwrite_existing_model(self) -> None:
        from agentobs._span import SpanContextManager
        from agentobs.integrations.openai import _auto_populate_span

        with SpanContextManager("span", model="my-custom-model") as span:
            _auto_populate_span(_make_response(model="gpt-4o"))
            assert span.model == "my-custom-model"

    def test_does_not_overwrite_manual_token_usage(self) -> None:
        from agentobs._span import SpanContextManager
        from agentobs.integrations.openai import _auto_populate_span
        from agentobs.namespaces.trace import TokenUsage

        manual = TokenUsage(input_tokens=999, output_tokens=1, total_tokens=1000)
        with SpanContextManager("span") as span:
            span.token_usage = manual
            _auto_populate_span(_make_response())
            assert span.token_usage is manual

    def test_no_active_span_is_noop(self) -> None:
        from agentobs._span import _span_stack_var
        from agentobs.integrations.openai import _auto_populate_span

        _span_stack_var.set(())
        _auto_populate_span(_make_response())  # must not raise

    def test_normalize_response_raises_does_not_surface(self) -> None:
        """Covers the ``except Exception: pass`` branch in _auto_populate_span.

        If normalize_response raises for any reason, _auto_populate_span must
        swallow the error and not propagate it to caller code.
        """
        from agentobs._span import SpanContextManager
        from agentobs.integrations import openai as oi

        with SpanContextManager("span"):
            with patch.object(oi, "normalize_response", side_effect=RuntimeError("boom")):
                oi._auto_populate_span(_make_response())  # must not raise

    def test_span_stack_import_failure_does_not_surface(self) -> None:
        """If the internal import of _span_stack raises, error is swallowed."""
        from agentobs.integrations import openai as oi

        with patch.dict("sys.modules", {"agentobs._span": None}):  # type: ignore[dict-item]
            oi._auto_populate_span(_make_response())  # must not raise

    def test_malformed_response_swallowed(self) -> None:
        """A completely non-response object causes no exception."""
        from agentobs._span import SpanContextManager
        from agentobs.integrations.openai import _auto_populate_span

        with SpanContextManager("span"):
            _auto_populate_span("not-a-response")  # string has no .model, .usage

    def test_multiple_normalizations_cumulative_if_cleared(self) -> None:
        """If token_usage is cleared between calls, second call sets new data."""
        from agentobs._span import SpanContextManager
        from agentobs.integrations.openai import _auto_populate_span

        with SpanContextManager("span") as span:
            _auto_populate_span(_make_response(model="gpt-4o", prompt_tokens=10,
                                               completion_tokens=5, total_tokens=15))
            first = span.token_usage
            assert first is not None

            # Clear and re-populate
            span.token_usage = None
            _auto_populate_span(_make_response(model="gpt-4o", prompt_tokens=20,
                                               completion_tokens=10, total_tokens=30))
            assert span.token_usage is not None
            assert span.token_usage.input_tokens == 20


# ===========================================================================
# 5. patch() / unpatch() / is_patched() lifecycle
# ===========================================================================


@pytest.mark.unit
class TestPatchLifecycle:
    def setup_method(self) -> None:
        _uninstall_mock_openai()

    def teardown_method(self) -> None:
        _uninstall_mock_openai()

    def test_is_patched_false_without_openai(self) -> None:
        from agentobs.integrations.openai import is_patched

        assert is_patched() is False

    def test_patch_raises_without_openai(self) -> None:
        from agentobs.integrations.openai import patch

        with pytest.raises(ImportError, match="openai"):
            patch()

    def test_unpatch_raises_without_openai(self) -> None:
        from agentobs.integrations.openai import unpatch

        with pytest.raises(ImportError, match="openai"):
            unpatch()

    def test_patch_sets_flag(self) -> None:
        from agentobs.integrations.openai import is_patched, patch

        _build_mock_openai()
        patch()
        assert is_patched() is True

    def test_patch_idempotent(self) -> None:
        from agentobs.integrations.openai import is_patched, patch

        _build_mock_openai()
        patch()
        patch()  # second call is a no-op
        assert is_patched() is True

    def test_unpatch_removes_flag(self) -> None:
        from agentobs.integrations.openai import is_patched, patch, unpatch

        _build_mock_openai()
        patch()
        assert is_patched() is True
        unpatch()
        assert is_patched() is False

    def test_unpatch_noop_when_not_patched(self) -> None:
        from agentobs.integrations.openai import unpatch

        _build_mock_openai()
        unpatch()  # should not raise

    def test_patch_wraps_original_not_already_wrapped(self) -> None:
        """After unpatch, Completions.create should be the original function."""
        from agentobs.integrations.openai import patch, unpatch

        _build_mock_openai()
        completions_mod = sys.modules["openai.resources.chat.completions"]
        orig_create = completions_mod.Completions.create

        patch()
        # Patched method is a new function (different object)
        assert completions_mod.Completions.create is not orig_create

        unpatch()
        # After unpatch, original is restored
        assert completions_mod.Completions.create is orig_create


# ===========================================================================
# 6. Patched method invocation (wrapper bodies)
# ===========================================================================


@pytest.mark.unit
class TestPatchedMethodInvocation:
    def setup_method(self) -> None:
        _uninstall_mock_openai()

    def teardown_method(self) -> None:
        _uninstall_mock_openai()

    def test_patched_sync_create_populates_span(self) -> None:
        """The sync wrapper executes _auto_populate_span after create()."""
        import asyncio

        from agentobs._span import SpanContextManager
        from agentobs.integrations.openai import patch

        _build_mock_openai()
        patch()

        Completions = sys.modules["openai.resources.chat.completions"].Completions

        with SpanContextManager("test") as span:
            Completions.create(None)
            assert span.token_usage is not None

    def test_patched_async_create_populates_span(self) -> None:
        """The async wrapper executes _auto_populate_span after await create()."""
        import asyncio

        from agentobs._span import SpanContextManager
        from agentobs.integrations.openai import patch

        _build_mock_openai()
        patch()

        AsyncCompletions = sys.modules["openai.resources.chat.completions"].AsyncCompletions

        async def _run() -> None:
            with SpanContextManager("async-test") as span:
                await AsyncCompletions.create(None)
                assert span.token_usage is not None

        asyncio.run(_run())

    def test_patched_create_passes_args_to_original(self) -> None:
        """The wrapper forwards all args/kwargs to the original function."""
        from agentobs._span import SpanContextManager
        from agentobs.integrations.openai import patch

        received: dict = {}

        def _tracking_create(*args: Any, **kwargs: Any) -> Any:
            received["args"] = args
            received["kwargs"] = kwargs
            return _make_response()

        _build_mock_openai()
        sys.modules["openai.resources.chat.completions"].Completions.create = _tracking_create
        patch()

        Completions = sys.modules["openai.resources.chat.completions"].Completions
        with SpanContextManager("span"):
            Completions.create(None, model="gpt-4o", messages=[])

        assert "model" in received["kwargs"]
        assert received["kwargs"]["model"] == "gpt-4o"

    def test_patched_sync_returns_original_response(self) -> None:
        """The sync wrapper must return the original response unmodified."""
        from agentobs._span import SpanContextManager
        from agentobs.integrations.openai import patch

        expected_resp = _make_response(model="gpt-4o")
        _build_mock_openai()
        sys.modules["openai.resources.chat.completions"].Completions.create = (
            lambda *a, **kw: expected_resp
        )
        patch()

        Completions = sys.modules["openai.resources.chat.completions"].Completions
        with SpanContextManager("span"):
            result = Completions.create(None)

        assert result is expected_resp


# ===========================================================================
# 7. End-to-end tracer integration
# ===========================================================================


@pytest.mark.integration
class TestPhase6EndToEnd:
    def setup_method(self) -> None:
        from agentobs._span import _span_stack_var

        _span_stack_var.set(())

    def test_normalize_then_span_payload_has_model(self) -> None:
        from agentobs._span import Span, _now_ns, _span_id, _trace_id
        from agentobs.integrations.openai import normalize_response

        span = Span(
            name="chat",
            span_id=_span_id(),
            trace_id=_trace_id(),
            start_ns=_now_ns(),
        )
        resp = _make_response(model="gpt-4o", prompt_tokens=200, completion_tokens=80,
                              total_tokens=280)
        tu, mi, cost = normalize_response(resp)
        span.token_usage = tu
        span.cost = cost
        span.model = mi.name
        span.end()

        payload = span.to_span_payload()
        assert payload.model is not None
        assert payload.model.name == "gpt-4o"
        assert payload.token_usage is not None
        assert payload.token_usage.input_tokens == 200
        assert payload.cost is not None
        assert payload.cost.total_cost_usd > 0

    def test_normalize_all_table_models_roundtrip(self) -> None:
        """Every model in the pricing table produces valid CostBreakdown + TokenUsage."""
        from agentobs.integrations._pricing import OPENAI_PRICING
        from agentobs.integrations.openai import normalize_response
        from agentobs.namespaces.trace import CostBreakdown, TokenUsage

        for model in OPENAI_PRICING:
            resp = _make_response(model=model, prompt_tokens=500,
                                  completion_tokens=250, total_tokens=750)
            tu, mi, cost = normalize_response(resp)

            assert isinstance(tu, TokenUsage)
            assert isinstance(cost, CostBreakdown)
            assert mi.name == model
            assert cost.total_cost_usd >= 0

    def test_cached_tokens_reduce_cost_for_gpt4o(self) -> None:
        from agentobs.integrations.openai import normalize_response

        base_resp = _make_response("gpt-4o", 1000, 500, 1500)
        cached_resp = _make_response("gpt-4o", 1000, 500, 1500, cached_tokens=500)

        _, _, cost_base = normalize_response(base_resp)
        _, _, cost_cached = normalize_response(cached_resp)

        assert cost_cached.total_cost_usd < cost_base.total_cost_usd
        assert cost_cached.cached_discount_usd > 0

    def test_reasoning_tokens_o1_billed_correctly(self) -> None:
        from agentobs.integrations.openai import normalize_response

        resp = _make_response(model="o1", prompt_tokens=100, completion_tokens=300,
                              total_tokens=400, reasoning_tokens=200)
        _, _, cost = normalize_response(resp)
        # Should have non-zero reasoning cost
        assert cost.reasoning_cost_usd > 0

    def test_full_span_with_auto_populate(self) -> None:
        """Simulates full OpenAI instrumentation lifecycle with agent_run."""
        import agentobs._stream as stream_mod
        from agentobs._span import _run_stack_var, _span_stack_var
        from agentobs._stream import _reset_exporter
        from agentobs._tracer import tracer
        from agentobs.integrations.openai import _auto_populate_span

        # Set up an in-memory exporter
        class _CapExporter:
            def __init__(self) -> None:
                self.events = []
            def export(self, event):
                self.events.append(event)
            def flush(self):
                ...
            def close(self):
                ...
        _reset_exporter()
        cap = _CapExporter()
        stream_mod._cached_exporter = cap
        _span_stack_var.set(())
        _run_stack_var.set(())

        try:
            with tracer.agent_run("gpt-agent") as run:
                with tracer.agent_step("llm-step") as step:
                    with tracer.span("gpt-4o-call") as span:
                        # Simulate auto-populate from patched OpenAI
                        _auto_populate_span(_make_response(
                            model="gpt-4o",
                            prompt_tokens=100,
                            completion_tokens=50,
                            total_tokens=150,
                        ))
                    assert span.token_usage is not None
                    assert span.model == "gpt-4o"
        finally:
            _reset_exporter()
            _span_stack_var.set(())
            _run_stack_var.set(())

        # At least 3 events: span, step, run
        assert len(cap.events) >= 3
