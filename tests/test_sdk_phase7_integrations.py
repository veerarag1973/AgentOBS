"""Tests for agentobs Phase 7 provider integrations.

Covers:
* agentobs.integrations.anthropic  — Claude response normalizer
* agentobs.integrations.ollama     — Ollama local model normalizer
* agentobs.integrations.groq       — Groq API normalizer
* agentobs.integrations.together   — Together AI normalizer

All tests run without the real provider SDKs installed — each SDK is simulated
via lightweight mock objects and sys.modules injection.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentobs.namespaces.trace import CostBreakdown, GenAISystem

# ===========================================================================
# Shared helpers
# ===========================================================================


def _make_usage(
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    total_tokens: int = 150,
) -> Any:
    """Build a mock OpenAI/Groq/Together-style CompletionUsage object."""
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = total_tokens
    return usage


def _make_openai_style_response(
    model: str = "llama3-70b-8192",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    total_tokens: int = 150,
) -> Any:
    resp = MagicMock()
    resp.model = model
    resp.usage = _make_usage(prompt_tokens, completion_tokens, total_tokens)
    return resp


# ===========================================================================
# ─── Anthropic ──────────────────────────────────────────────────────────────
# ===========================================================================


def _inject_fake_anthropic() -> None:
    """Register a minimal fake anthropic package in sys.modules."""
    if "anthropic" in sys.modules:
        return
    mod = types.ModuleType("anthropic")
    mod._agentobs_patched = False  # type: ignore[attr-defined]
    resources = types.ModuleType("anthropic.resources")
    messages_mod = types.ModuleType("anthropic.resources.messages")

    class Messages:
        def create(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            return MagicMock()

    class AsyncMessages:
        async def create(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover  # NOSONAR
            return MagicMock()

    messages_mod.Messages = Messages  # type: ignore[attr-defined]
    messages_mod.AsyncMessages = AsyncMessages  # type: ignore[attr-defined]

    mod.resources = resources  # type: ignore[attr-defined]
    resources.messages = messages_mod  # type: ignore[attr-defined]

    sys.modules["anthropic"] = mod
    sys.modules["anthropic.resources"] = resources
    sys.modules["anthropic.resources.messages"] = messages_mod


def _remove_fake_anthropic() -> None:
    for key in list(sys.modules):
        if key == "anthropic" or key.startswith("anthropic."):
            del sys.modules[key]


def _make_anthropic_usage(
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_input_tokens: int | None = None,
) -> Any:
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    if cache_read_input_tokens is not None:
        usage.cache_read_input_tokens = cache_read_input_tokens
    else:
        usage.cache_read_input_tokens = None
    return usage


def _make_anthropic_response(
    model: str = "claude-3-5-sonnet-20241022",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_input_tokens: int | None = None,
) -> Any:
    resp = MagicMock()
    resp.model = model
    resp.usage = _make_anthropic_usage(input_tokens, output_tokens, cache_read_input_tokens)
    return resp


class TestAnthropicPricingTable:
    def test_known_models_present(self) -> None:
        from agentobs.integrations.anthropic import ANTHROPIC_PRICING  # noqa: PLC0415

        for model in (
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
            "claude-3-haiku-20240307",
            "claude-2.1",
        ):
            assert model in ANTHROPIC_PRICING, f"{model} missing from pricing table"

    def test_every_entry_has_input_output(self) -> None:
        from agentobs.integrations.anthropic import ANTHROPIC_PRICING  # noqa: PLC0415

        for model, p in ANTHROPIC_PRICING.items():
            assert "input" in p, f"{model}: missing 'input'"
            assert "output" in p, f"{model}: missing 'output'"

    def test_all_prices_non_negative(self) -> None:
        from agentobs.integrations.anthropic import ANTHROPIC_PRICING  # noqa: PLC0415

        for model, p in ANTHROPIC_PRICING.items():
            for field, val in p.items():
                assert val >= 0, f"anthropic {model}.{field} is negative"

    def test_pricing_date_format(self) -> None:
        from agentobs.integrations.anthropic import PRICING_DATE  # noqa: PLC0415

        assert len(PRICING_DATE) == 10
        assert PRICING_DATE[:2] == "20"

    def test_list_models_sorted(self) -> None:
        from agentobs.integrations.anthropic import list_models  # noqa: PLC0415

        models = list_models()
        assert models == sorted(models)
        assert "claude-3-5-sonnet-20241022" in models

    def test_opus_more_expensive_than_haiku(self) -> None:
        from agentobs.integrations.anthropic import ANTHROPIC_PRICING  # noqa: PLC0415

        opus = ANTHROPIC_PRICING["claude-3-opus-20240229"]
        haiku = ANTHROPIC_PRICING["claude-3-haiku-20240307"]
        assert opus["input"] > haiku["input"]
        assert opus["output"] > haiku["output"]


class TestAnthropicNormalizeResponse:
    def test_basic_usage(self) -> None:
        from agentobs.integrations.anthropic import normalize_response  # noqa: PLC0415

        resp = _make_anthropic_response(
            model="claude-3-5-sonnet-20241022",
            input_tokens=100,
            output_tokens=50,
        )
        token_usage, model_info, cost = normalize_response(resp)

        assert token_usage.input_tokens == 100
        assert token_usage.output_tokens == 50
        assert token_usage.total_tokens == 150
        assert token_usage.cached_tokens is None

        assert model_info.name == "claude-3-5-sonnet-20241022"
        assert model_info.system == GenAISystem.ANTHROPIC

        assert cost.input_cost_usd > 0
        assert cost.output_cost_usd > 0
        assert cost.total_cost_usd > 0

    def test_cache_read_tokens_populated(self) -> None:
        from agentobs.integrations.anthropic import normalize_response  # noqa: PLC0415

        resp = _make_anthropic_response(
            model="claude-3-5-sonnet-20241022",
            input_tokens=200,
            output_tokens=50,
            cache_read_input_tokens=100,
        )
        token_usage, _, _ = normalize_response(resp)
        assert token_usage.cached_tokens == 100

    def test_unknown_model_zero_cost(self) -> None:
        from agentobs.integrations.anthropic import normalize_response  # noqa: PLC0415

        resp = _make_anthropic_response(model="claude-99-ultra-fictional")
        _, _, cost = normalize_response(resp)
        assert cost == CostBreakdown.zero()

    def test_cost_math_correct(self) -> None:
        """100k input + 50k output of claude-3-5-haiku-20241022."""
        from agentobs.integrations.anthropic import normalize_response  # noqa: PLC0415

        resp = _make_anthropic_response(
            model="claude-3-5-haiku-20241022",
            input_tokens=100_000,
            output_tokens=50_000,
        )
        _, _, cost = normalize_response(resp)
        expected_input = 100_000 * 0.80 / 1_000_000.0   # $0.08
        expected_output = 50_000 * 4.00 / 1_000_000.0   # $0.20
        assert abs(cost.input_cost_usd - expected_input) < 1e-9
        assert abs(cost.output_cost_usd - expected_output) < 1e-9
        assert abs(cost.total_cost_usd - (expected_input + expected_output)) < 1e-6

    def test_no_usage_field_gives_zero_tokens(self) -> None:
        from agentobs.integrations.anthropic import normalize_response  # noqa: PLC0415

        resp = MagicMock()
        resp.model = "claude-3-haiku-20240307"
        resp.usage = None
        token_usage, _, _ = normalize_response(resp)
        assert token_usage.input_tokens == 0
        assert token_usage.output_tokens == 0
        assert token_usage.total_tokens == 0

    def test_model_info_system_is_anthropic(self) -> None:
        from agentobs.integrations.anthropic import normalize_response  # noqa: PLC0415

        resp = _make_anthropic_response()
        _, model_info, _ = normalize_response(resp)
        assert model_info.system == GenAISystem.ANTHROPIC

    def test_cost_breakdown_total_matches_formula(self) -> None:
        from agentobs.integrations.anthropic import normalize_response  # noqa: PLC0415

        resp = _make_anthropic_response(
            model="claude-3-opus-20240229",
            input_tokens=1000,
            output_tokens=500,
        )
        _, _, cost = normalize_response(resp)
        assert abs(
            cost.total_cost_usd
            - (cost.input_cost_usd + cost.output_cost_usd + cost.reasoning_cost_usd - cost.cached_discount_usd)  # noqa: E501
        ) < 1e-6


class TestAnthropicPatchUnpatch:
    def setup_method(self) -> None:
        _remove_fake_anthropic()
        _inject_fake_anthropic()

    def teardown_method(self) -> None:
        _remove_fake_anthropic()
        # Reset patch state in module
        import importlib  # noqa: PLC0415
        if "agentobs.integrations.anthropic" in sys.modules:
            importlib.reload(sys.modules["agentobs.integrations.anthropic"])

    def test_is_patched_false_initially(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.anthropic")
        assert not mod.is_patched()

    def test_patch_sets_flag(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.anthropic")
        mod.patch()
        assert mod.is_patched()

    def test_patch_idempotent(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.anthropic")
        mod.patch()
        mod.patch()  # second call must not raise
        assert mod.is_patched()

    def test_unpatch_clears_flag(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.anthropic")
        mod.patch()
        mod.unpatch()
        assert not mod.is_patched()

    def test_unpatch_noop_when_not_patched(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.anthropic")
        mod.unpatch()  # must not raise

    def test_import_error_without_anthropic(self) -> None:
        _remove_fake_anthropic()
        with pytest.raises(ImportError, match="anthropic"):
            import importlib  # noqa: PLC0415
            mod = importlib.import_module("agentobs.integrations.anthropic")
            mod._require_anthropic()

    def test_is_patched_false_when_package_missing(self) -> None:
        _remove_fake_anthropic()
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.anthropic")
        assert not mod.is_patched()


# ===========================================================================
# ─── Ollama ─────────────────────────────────────────────────────────────────
# ===========================================================================


def _inject_fake_ollama() -> None:
    if "ollama" in sys.modules:
        return
    mod = types.ModuleType("ollama")

    def chat(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        return {}

    class Client:
        def chat(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            return {}

    class AsyncClient:
        async def chat(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover  # NOSONAR
            return {}

    mod.chat = chat  # type: ignore[attr-defined]
    mod.Client = Client  # type: ignore[attr-defined]
    mod.AsyncClient = AsyncClient  # type: ignore[attr-defined]
    sys.modules["ollama"] = mod


def _remove_fake_ollama() -> None:
    for key in list(sys.modules):
        if key == "ollama" or key.startswith("ollama."):
            del sys.modules[key]


def _make_ollama_response(
    model: str = "llama3",
    prompt_eval_count: int = 100,
    eval_count: int = 50,
) -> Any:
    resp = MagicMock()
    resp.model = model
    resp.prompt_eval_count = prompt_eval_count
    resp.eval_count = eval_count
    return resp


class TestOllamaNormalizeResponse:
    def test_basic_object_response(self) -> None:
        from agentobs.integrations.ollama import normalize_response  # noqa: PLC0415

        resp = _make_ollama_response(model="llama3", prompt_eval_count=80, eval_count=40)
        token_usage, model_info, cost = normalize_response(resp)

        assert token_usage.input_tokens == 80
        assert token_usage.output_tokens == 40
        assert token_usage.total_tokens == 120

        assert model_info.name == "llama3"
        assert model_info.system == GenAISystem.OLLAMA

        # Ollama → always zero cost
        assert cost == CostBreakdown.zero()

    def test_dict_response(self) -> None:
        from agentobs.integrations.ollama import normalize_response  # noqa: PLC0415

        resp = {"model": "mistral", "prompt_eval_count": 50, "eval_count": 30}
        token_usage, model_info, cost = normalize_response(resp)

        assert token_usage.input_tokens == 50
        assert token_usage.output_tokens == 30
        assert token_usage.total_tokens == 80
        assert model_info.name == "mistral"
        assert cost == CostBreakdown.zero()

    def test_cost_is_always_zero(self) -> None:
        from agentobs.integrations.ollama import normalize_response  # noqa: PLC0415

        resp = _make_ollama_response(model="phi3", prompt_eval_count=999, eval_count=999)
        _, _, cost = normalize_response(resp)
        assert cost == CostBreakdown.zero()
        assert cost.total_cost_usd == pytest.approx(0.0)

    def test_missing_fields_default_zero(self) -> None:
        """Response with no token count fields → zeros, not exceptions."""
        from agentobs.integrations.ollama import normalize_response  # noqa: PLC0415

        resp = MagicMock()
        resp.model = "codellama"
        resp.prompt_eval_count = None
        resp.eval_count = None
        token_usage, _, _ = normalize_response(resp)
        assert token_usage.input_tokens == 0
        assert token_usage.output_tokens == 0
        assert token_usage.total_tokens == 0

    def test_model_info_system_is_ollama(self) -> None:
        from agentobs.integrations.ollama import normalize_response  # noqa: PLC0415

        resp = _make_ollama_response(model="phi3")
        _, model_info, _ = normalize_response(resp)
        assert model_info.system == GenAISystem.OLLAMA

    def test_unknown_model_still_works(self) -> None:
        from agentobs.integrations.ollama import normalize_response  # noqa: PLC0415

        resp = _make_ollama_response(model="my-custom-gguf-model")
        _token_usage, model_info, cost = normalize_response(resp)
        assert model_info.name == "my-custom-gguf-model"
        assert cost == CostBreakdown.zero()


class TestOllamaPatchUnpatch:
    def setup_method(self) -> None:
        _remove_fake_ollama()
        _inject_fake_ollama()

    def teardown_method(self) -> None:
        _remove_fake_ollama()
        import importlib  # noqa: PLC0415
        if "agentobs.integrations.ollama" in sys.modules:
            importlib.reload(sys.modules["agentobs.integrations.ollama"])

    def test_is_patched_false_initially(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.ollama")
        assert not mod.is_patched()

    def test_patch_sets_flag(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.ollama")
        mod.patch()
        assert mod.is_patched()

    def test_patch_idempotent(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.ollama")
        mod.patch()
        mod.patch()
        assert mod.is_patched()

    def test_unpatch_clears_flag(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.ollama")
        mod.patch()
        mod.unpatch()
        assert not mod.is_patched()

    def test_unpatch_noop_when_not_patched(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.ollama")
        mod.unpatch()  # must not raise

    def test_import_error_without_ollama(self) -> None:
        _remove_fake_ollama()
        with pytest.raises(ImportError, match="ollama"):
            import importlib  # noqa: PLC0415
            mod = importlib.import_module("agentobs.integrations.ollama")
            mod._require_ollama()

    def test_is_patched_false_when_package_missing(self) -> None:
        _remove_fake_ollama()
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.ollama")
        assert not mod.is_patched()


# ===========================================================================
# ─── Groq ───────────────────────────────────────────────────────────────────
# ===========================================================================


def _inject_fake_groq() -> None:
    if "groq" in sys.modules:
        return
    mod = types.ModuleType("groq")
    mod._agentobs_patched = False  # type: ignore[attr-defined]

    resources = types.ModuleType("groq.resources")
    chat_mod = types.ModuleType("groq.resources.chat")
    completions_mod = types.ModuleType("groq.resources.chat.completions")

    class Completions:
        def create(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            return MagicMock()

    class AsyncCompletions:
        async def create(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover  # NOSONAR
            return MagicMock()

    completions_mod.Completions = Completions  # type: ignore[attr-defined]
    completions_mod.AsyncCompletions = AsyncCompletions  # type: ignore[attr-defined]

    mod.resources = resources  # type: ignore[attr-defined]
    resources.chat = chat_mod  # type: ignore[attr-defined]
    chat_mod.completions = completions_mod  # type: ignore[attr-defined]

    sys.modules["groq"] = mod
    sys.modules["groq.resources"] = resources
    sys.modules["groq.resources.chat"] = chat_mod
    sys.modules["groq.resources.chat.completions"] = completions_mod


def _remove_fake_groq() -> None:
    for key in list(sys.modules):
        if key == "groq" or key.startswith("groq."):
            del sys.modules[key]


class TestGroqPricingTable:
    def test_known_models_present(self) -> None:
        from agentobs.integrations.groq import GROQ_PRICING  # noqa: PLC0415

        for model in (
            "llama3-70b-8192",
            "llama3-8b-8192",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ):
            assert model in GROQ_PRICING, f"{model} missing from Groq pricing table"

    def test_every_entry_has_input_output(self) -> None:
        from agentobs.integrations.groq import GROQ_PRICING  # noqa: PLC0415

        for model, p in GROQ_PRICING.items():
            assert "input" in p, f"groq {model}: missing 'input'"
            assert "output" in p, f"groq {model}: missing 'output'"

    def test_all_prices_non_negative(self) -> None:
        from agentobs.integrations.groq import GROQ_PRICING  # noqa: PLC0415

        for model, p in GROQ_PRICING.items():
            for field, val in p.items():
                assert val >= 0, f"groq {model}.{field} is negative"

    def test_list_models_sorted(self) -> None:
        from agentobs.integrations.groq import list_models  # noqa: PLC0415

        models = list_models()
        assert models == sorted(models)

    def test_pricing_date_format(self) -> None:
        from agentobs.integrations.groq import PRICING_DATE  # noqa: PLC0415

        assert len(PRICING_DATE) == 10
        assert PRICING_DATE[:2] == "20"

    def test_llama3_70b_cheaper_than_llama31_405b(self) -> None:
        from agentobs.integrations.groq import GROQ_PRICING  # noqa: PLC0415

        llama3 = GROQ_PRICING["llama3-70b-8192"]
        llama405b = GROQ_PRICING["llama-3.1-405b-reasoning"]
        assert llama3["input"] < llama405b["input"]


class TestGroqNormalizeResponse:
    def test_basic_usage(self) -> None:
        from agentobs.integrations.groq import normalize_response  # noqa: PLC0415

        resp = _make_openai_style_response(
            model="llama3-70b-8192",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        token_usage, model_info, cost = normalize_response(resp)

        assert token_usage.input_tokens == 100
        assert token_usage.output_tokens == 50
        assert token_usage.total_tokens == 150

        assert model_info.name == "llama3-70b-8192"
        assert model_info.system == GenAISystem.GROQ

        assert cost.input_cost_usd > 0
        assert cost.output_cost_usd > 0

    def test_unknown_model_zero_cost(self) -> None:
        from agentobs.integrations.groq import normalize_response  # noqa: PLC0415

        resp = _make_openai_style_response(model="unknown-groq-model-xyz123")
        _, _, cost = normalize_response(resp)
        assert cost == CostBreakdown.zero()

    def test_cost_math_correct(self) -> None:
        """100k input + 50k output of mixtral-8x7b-32768."""
        from agentobs.integrations.groq import normalize_response  # noqa: PLC0415

        resp = _make_openai_style_response(
            model="mixtral-8x7b-32768",
            prompt_tokens=100_000,
            completion_tokens=50_000,
            total_tokens=150_000,
        )
        _, _, cost = normalize_response(resp)
        # $0.24/M for both input and output
        expected_input = 100_000 * 0.24 / 1_000_000.0
        expected_output = 50_000 * 0.24 / 1_000_000.0
        assert abs(cost.input_cost_usd - expected_input) < 1e-9
        assert abs(cost.output_cost_usd - expected_output) < 1e-9

    def test_cost_breakdown_total_matches_formula(self) -> None:
        from agentobs.integrations.groq import normalize_response  # noqa: PLC0415

        resp = _make_openai_style_response(model="llama3-70b-8192")
        _, _, cost = normalize_response(resp)
        assert abs(
            cost.total_cost_usd
            - (cost.input_cost_usd + cost.output_cost_usd + cost.reasoning_cost_usd - cost.cached_discount_usd)  # noqa: E501
        ) < 1e-6

    def test_no_usage_field_gives_zero_tokens(self) -> None:
        from agentobs.integrations.groq import normalize_response  # noqa: PLC0415

        resp = MagicMock()
        resp.model = "gemma2-9b-it"
        resp.usage = None
        token_usage, _, _ = normalize_response(resp)
        assert token_usage.input_tokens == 0
        assert token_usage.output_tokens == 0

    def test_model_info_system_is_groq(self) -> None:
        from agentobs.integrations.groq import normalize_response  # noqa: PLC0415

        resp = _make_openai_style_response()
        _, model_info, _ = normalize_response(resp)
        assert model_info.system == GenAISystem.GROQ


class TestGroqGetDurationMs:
    def test_returns_ms_when_total_time_present(self) -> None:
        from agentobs.integrations.groq import get_duration_ms  # noqa: PLC0415

        resp = MagicMock()
        resp.usage = MagicMock()
        resp.usage.total_time = 0.250  # 250ms

        result = get_duration_ms(resp)
        assert result is not None
        assert abs(result - 250.0) < 1e-6

    def test_returns_none_when_usage_missing(self) -> None:
        from agentobs.integrations.groq import get_duration_ms  # noqa: PLC0415

        resp = MagicMock()
        resp.usage = None
        assert get_duration_ms(resp) is None

    def test_returns_none_when_total_time_missing(self) -> None:
        from agentobs.integrations.groq import get_duration_ms  # noqa: PLC0415

        resp = MagicMock()
        resp.usage = MagicMock()
        resp.usage.total_time = None
        assert get_duration_ms(resp) is None

    def test_sub_millisecond_precision(self) -> None:
        from agentobs.integrations.groq import get_duration_ms  # noqa: PLC0415

        resp = MagicMock()
        resp.usage = MagicMock()
        resp.usage.total_time = 0.0005  # 0.5ms

        result = get_duration_ms(resp)
        assert result is not None
        assert result < 1.0
        assert result > 0.0


class TestGroqPatchUnpatch:
    def setup_method(self) -> None:
        _remove_fake_groq()
        _inject_fake_groq()

    def teardown_method(self) -> None:
        _remove_fake_groq()
        import importlib  # noqa: PLC0415
        if "agentobs.integrations.groq" in sys.modules:
            importlib.reload(sys.modules["agentobs.integrations.groq"])

    def test_is_patched_false_initially(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.groq")
        assert not mod.is_patched()

    def test_patch_sets_flag(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.groq")
        mod.patch()
        assert mod.is_patched()

    def test_patch_idempotent(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.groq")
        mod.patch()
        mod.patch()
        assert mod.is_patched()

    def test_unpatch_clears_flag(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.groq")
        mod.patch()
        mod.unpatch()
        assert not mod.is_patched()

    def test_unpatch_noop_when_not_patched(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.groq")
        mod.unpatch()

    def test_import_error_without_groq(self) -> None:
        _remove_fake_groq()
        with pytest.raises(ImportError, match="groq"):
            import importlib  # noqa: PLC0415
            mod = importlib.import_module("agentobs.integrations.groq")
            mod._require_groq()

    def test_is_patched_false_when_package_missing(self) -> None:
        _remove_fake_groq()
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.groq")
        assert not mod.is_patched()


# ===========================================================================
# ─── Together AI ─────────────────────────────────────────────────────────────
# ===========================================================================


def _inject_fake_together() -> None:
    if "together" in sys.modules:
        return
    mod = types.ModuleType("together")
    mod._agentobs_patched = False  # type: ignore[attr-defined]

    resources = types.ModuleType("together.resources")
    chat_mod = types.ModuleType("together.resources.chat")
    completions_mod = types.ModuleType("together.resources.chat.completions")

    class Completions:
        def create(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            return MagicMock()

    class AsyncCompletions:
        async def create(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover  # NOSONAR
            return MagicMock()

    completions_mod.Completions = Completions  # type: ignore[attr-defined]
    completions_mod.AsyncCompletions = AsyncCompletions  # type: ignore[attr-defined]

    mod.resources = resources  # type: ignore[attr-defined]
    resources.chat = chat_mod  # type: ignore[attr-defined]
    chat_mod.completions = completions_mod  # type: ignore[attr-defined]

    sys.modules["together"] = mod
    sys.modules["together.resources"] = resources
    sys.modules["together.resources.chat"] = chat_mod
    sys.modules["together.resources.chat.completions"] = completions_mod


def _remove_fake_together() -> None:
    for key in list(sys.modules):
        if key == "together" or key.startswith("together."):
            del sys.modules[key]


class TestTogetherPricingTable:
    def test_known_models_present(self) -> None:
        from agentobs.integrations.together import TOGETHER_PRICING  # noqa: PLC0415

        for model in (
            "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            "meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "mistralai/Mixtral-8x7B-Instruct-v0.1",
        ):
            assert model in TOGETHER_PRICING, f"{model} missing from Together pricing table"

    def test_every_entry_has_input_output(self) -> None:
        from agentobs.integrations.together import TOGETHER_PRICING  # noqa: PLC0415

        for model, p in TOGETHER_PRICING.items():
            assert "input" in p, f"together {model}: missing 'input'"
            assert "output" in p, f"together {model}: missing 'output'"

    def test_all_prices_non_negative(self) -> None:
        from agentobs.integrations.together import TOGETHER_PRICING  # noqa: PLC0415

        for model, p in TOGETHER_PRICING.items():
            for field, val in p.items():
                assert val >= 0, f"together {model}.{field} is negative"

    def test_list_models_sorted(self) -> None:
        from agentobs.integrations.together import list_models  # noqa: PLC0415

        models = list_models()
        assert models == sorted(models)

    def test_llama3_1_405b_more_expensive_than_8b(self) -> None:
        from agentobs.integrations.together import TOGETHER_PRICING  # noqa: PLC0415

        big = TOGETHER_PRICING["meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo"]
        small = TOGETHER_PRICING["meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"]
        assert big["input"] > small["input"]


class TestTogetherNormalizeModelName:
    def test_strips_org_prefix(self) -> None:
        from agentobs.integrations.together import normalize_model_name  # noqa: PLC0415

        assert normalize_model_name("meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo") == "Meta-Llama-3.1-8B-Instruct-Turbo"  # noqa: E501

    def test_strips_qwen_prefix(self) -> None:
        from agentobs.integrations.together import normalize_model_name  # noqa: PLC0415

        assert normalize_model_name("Qwen/Qwen2.5-72B-Instruct-Turbo") == "Qwen2.5-72B-Instruct-Turbo"  # noqa: E501

    def test_no_slash_unchanged(self) -> None:
        from agentobs.integrations.together import normalize_model_name  # noqa: PLC0415

        assert normalize_model_name("gpt-4o") == "gpt-4o"

    def test_empty_string_unchanged(self) -> None:
        from agentobs.integrations.together import normalize_model_name  # noqa: PLC0415

        assert normalize_model_name("") == ""

    def test_only_org_slash_gives_empty(self) -> None:
        from agentobs.integrations.together import normalize_model_name  # noqa: PLC0415

        # "org/" → empty string after the slash
        assert normalize_model_name("org/") == ""

    def test_multiple_slashes_only_first_stripped(self) -> None:
        from agentobs.integrations.together import normalize_model_name  # noqa: PLC0415

        result = normalize_model_name("org/sub/model-name")
        assert result == "sub/model-name"


class TestTogetherNormalizeResponse:
    def test_basic_usage(self) -> None:
        from agentobs.integrations.together import normalize_response  # noqa: PLC0415

        resp = _make_openai_style_response(
            model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
        )
        token_usage, model_info, cost = normalize_response(resp)

        assert token_usage.input_tokens == 200
        assert token_usage.output_tokens == 100
        assert token_usage.total_tokens == 300

        # ModelInfo stores the full org/model identifier
        assert model_info.name == "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
        assert model_info.system == GenAISystem.TOGETHER_AI

        assert cost.input_cost_usd > 0
        assert cost.output_cost_usd > 0

    def test_unknown_model_zero_cost(self) -> None:
        from agentobs.integrations.together import normalize_response  # noqa: PLC0415

        resp = _make_openai_style_response(model="org/totally-fictional-model-12345")
        _, _, cost = normalize_response(resp)
        assert cost == CostBreakdown.zero()

    def test_cost_math_correct(self) -> None:
        """100k input + 50k output of Meta-Llama-3.1-8B-Instruct-Turbo ($0.18/M both)."""
        from agentobs.integrations.together import normalize_response  # noqa: PLC0415

        resp = _make_openai_style_response(
            model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            prompt_tokens=100_000,
            completion_tokens=50_000,
            total_tokens=150_000,
        )
        _, _, cost = normalize_response(resp)
        expected_input = 100_000 * 0.18 / 1_000_000.0
        expected_output = 50_000 * 0.18 / 1_000_000.0
        assert abs(cost.input_cost_usd - expected_input) < 1e-9
        assert abs(cost.output_cost_usd - expected_output) < 1e-9

    def test_full_identifier_preserved_in_model_info(self) -> None:
        from agentobs.integrations.together import normalize_response  # noqa: PLC0415

        full_name = "meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo"
        resp = _make_openai_style_response(model=full_name)
        _, model_info, _ = normalize_response(resp)
        assert model_info.name == full_name

    def test_model_info_system_is_together_ai(self) -> None:
        from agentobs.integrations.together import normalize_response  # noqa: PLC0415

        resp = _make_openai_style_response(model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo")
        _, model_info, _ = normalize_response(resp)
        assert model_info.system == GenAISystem.TOGETHER_AI

    def test_no_usage_field_gives_zero_tokens(self) -> None:
        from agentobs.integrations.together import normalize_response  # noqa: PLC0415

        resp = MagicMock()
        resp.model = "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
        resp.usage = None
        token_usage, _, _ = normalize_response(resp)
        assert token_usage.input_tokens == 0
        assert token_usage.output_tokens == 0

    def test_cost_breakdown_total_matches_formula(self) -> None:
        from agentobs.integrations.together import normalize_response  # noqa: PLC0415

        resp = _make_openai_style_response(model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
        _, _, cost = normalize_response(resp)
        assert abs(
            cost.total_cost_usd
            - (cost.input_cost_usd + cost.output_cost_usd + cost.reasoning_cost_usd - cost.cached_discount_usd)  # noqa: E501
        ) < 1e-6

    def test_deepseek_r1_asymmetric_pricing(self) -> None:
        """DeepSeek-R1 has different input vs output pricing."""
        from agentobs.integrations.together import (  # noqa: PLC0415
            TOGETHER_PRICING,
            normalize_response,
        )

        r1 = TOGETHER_PRICING["deepseek-ai/DeepSeek-R1"]
        assert r1["input"] != r1["output"]

        resp = _make_openai_style_response(
            model="deepseek-ai/DeepSeek-R1",
            prompt_tokens=1000,
            completion_tokens=1000,
            total_tokens=2000,
        )
        _, _, cost = normalize_response(resp)
        assert cost.input_cost_usd != cost.output_cost_usd  # asymmetric rates


class TestTogetherPatchUnpatch:
    def setup_method(self) -> None:
        _remove_fake_together()
        _inject_fake_together()

    def teardown_method(self) -> None:
        _remove_fake_together()
        import importlib  # noqa: PLC0415
        if "agentobs.integrations.together" in sys.modules:
            importlib.reload(sys.modules["agentobs.integrations.together"])

    def test_is_patched_false_initially(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.together")
        assert not mod.is_patched()

    def test_patch_sets_flag(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.together")
        mod.patch()
        assert mod.is_patched()

    def test_patch_idempotent(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.together")
        mod.patch()
        mod.patch()
        assert mod.is_patched()

    def test_unpatch_clears_flag(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.together")
        mod.patch()
        mod.unpatch()
        assert not mod.is_patched()

    def test_unpatch_noop_when_not_patched(self) -> None:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.together")
        mod.unpatch()

    def test_import_error_without_together(self) -> None:
        _remove_fake_together()
        with pytest.raises(ImportError, match="together"):
            import importlib  # noqa: PLC0415
            mod = importlib.import_module("agentobs.integrations.together")
            mod._require_together()

    def test_is_patched_false_when_package_missing(self) -> None:
        _remove_fake_together()
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("agentobs.integrations.together")
        assert not mod.is_patched()


# ===========================================================================
# ─── Integrations __init__ ───────────────────────────────────────────────────
# ===========================================================================


class TestIntegrationsInit:
    def test_all_providers_in_dunder_all(self) -> None:
        from agentobs.integrations import __all__ as _all  # noqa: PLC0415

        for provider in ("openai", "anthropic", "ollama", "groq", "together"):
            assert provider in _all, f"{provider} missing from agentobs.integrations.__all__"
