"""Coverage-boost tests targeting optional-field branches in namespace / trace payloads.

Each test instantiates a payload class with ALL optional fields populated,
verifies ``to_dict()`` includes those fields, and optionally round-trips via
``from_dict()``.  Validation-error branches are also exercised.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Namespace payloads
# ---------------------------------------------------------------------------
from agentobs.namespaces.audit import (
    AuditChainTamperedPayload,
    AuditKeyRotatedPayload,
)
from agentobs.namespaces.cache import (
    CacheEvictedPayload,
    CacheHitPayload,
    CacheMissPayload,
)
from agentobs.namespaces.cost import (
    CostAttributedPayload,
    CostSessionRecordedPayload,
    CostTokenRecordedPayload,
)
from agentobs.namespaces.diff import DiffComputedPayload
from agentobs.namespaces.eval_ import (
    EvalRegressionDetectedPayload,
    EvalScenarioCompletedPayload,
    EvalScenarioStartedPayload,
    EvalScoreRecordedPayload,
)
from agentobs.namespaces.fence import (
    FenceMaxRetriesExceededPayload,
    FenceRetryTriggeredPayload,
    FenceValidatedPayload,
)
from agentobs.namespaces.guard import GuardPayload
from agentobs.namespaces.prompt import PromptRenderedPayload, PromptTemplateLoadedPayload
from agentobs.namespaces.redact import RedactPhiDetectedPayload, RedactPiiDetectedPayload
from agentobs.namespaces.template import (
    TemplateRegisteredPayload,
    TemplateVariableBoundPayload,
)

# ---------------------------------------------------------------------------
# trace.py value objects
# ---------------------------------------------------------------------------
from agentobs.namespaces.trace import (
    AgentRunPayload,
    AgentStepPayload,
    CostBreakdown,
    DecisionPoint,
    GenAIOperationName,
    GenAISystem,
    ModelInfo,
    PricingTier,
    ReasoningStep,
    SpanKind,
    SpanPayload,
    TokenUsage,
    ToolCall,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SPAN_ID = "a" * 16
_TRACE_ID = "b" * 32
_SHA256 = "c" * 64


def _token_usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


def _cost() -> CostBreakdown:
    return CostBreakdown(input_cost_usd=0.001, output_cost_usd=0.002, total_cost_usd=0.003)


def _model() -> ModelInfo:
    return ModelInfo(system=GenAISystem.OPENAI, name="gpt-4o")


# ===========================================================================
# TokenUsage — optional fields
# ===========================================================================

@pytest.mark.unit
class TestTokenUsageFull:
    def test_all_optional_fields_in_to_dict(self) -> None:
        tu = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cached_tokens=20,
            cache_creation_tokens=5,
            reasoning_tokens=10,
            image_tokens=3,
        )
        d = tu.to_dict()
        assert d["cached_tokens"] == 20
        assert d["cache_creation_tokens"] == 5
        assert d["reasoning_tokens"] == 10
        assert d["image_tokens"] == 3

    def test_from_dict_roundtrip_with_optionals(self) -> None:
        tu = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cached_tokens=20,
            reasoning_tokens=10,
        )
        tu2 = TokenUsage.from_dict(tu.to_dict())
        assert tu2.cached_tokens == 20
        assert tu2.reasoning_tokens == 10
        assert tu2.image_tokens is None


# ===========================================================================
# ModelInfo — optional fields + unknown system fallback
# ===========================================================================

@pytest.mark.unit
class TestModelInfoFull:
    def test_all_optional_fields_in_to_dict(self) -> None:
        m = ModelInfo(
            system=GenAISystem.OPENAI,
            name="gpt-4o",
            response_model="gpt-4o-2024-08-06",
            version="2024-08-06",
        )
        d = m.to_dict()
        assert d["response_model"] == "gpt-4o-2024-08-06"
        assert d["version"] == "2024-08-06"

    def test_custom_system_name_in_to_dict(self) -> None:
        m = ModelInfo(
            system=GenAISystem.CUSTOM,
            name="llm-enterprise-v2",
            custom_system_name="acme-internal",
        )
        d = m.to_dict()
        assert d["custom_system_name"] == "acme-internal"

    def test_unknown_system_string_roundtrip(self) -> None:
        m = ModelInfo(system="some_unknown_provider", name="model-x")
        m2 = ModelInfo.from_dict(m.to_dict())
        assert m2.system == "some_unknown_provider"

    def test_custom_system_without_name_raises(self) -> None:
        with pytest.raises(ValueError, match="custom_system_name"):
            ModelInfo(system=GenAISystem.CUSTOM, name="x")


# ===========================================================================
# CostBreakdown — optional fields
# ===========================================================================

@pytest.mark.unit
class TestCostBreakdownFull:
    def test_non_zero_reasoning_in_to_dict(self) -> None:
        c = CostBreakdown(
            input_cost_usd=0.001,
            output_cost_usd=0.001,
            reasoning_cost_usd=0.001,
            total_cost_usd=0.003,
        )
        d = c.to_dict()
        assert "reasoning_cost_usd" in d

    def test_cached_discount_in_to_dict(self) -> None:
        c = CostBreakdown(
            input_cost_usd=0.005,
            output_cost_usd=0.002,
            cached_discount_usd=0.001,
            total_cost_usd=0.006,
        )
        d = c.to_dict()
        assert "cached_discount_usd" in d

    def test_non_usd_currency_in_to_dict(self) -> None:
        c = CostBreakdown(
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
            currency="EUR",
        )
        d = c.to_dict()
        assert d["currency"] == "EUR"

    def test_pricing_date_in_to_dict(self) -> None:
        c = CostBreakdown(
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
            pricing_date="2024-01-01",
        )
        d = c.to_dict()
        assert d["pricing_date"] == "2024-01-01"

    def test_invalid_currency_raises(self) -> None:
        with pytest.raises(ValueError, match="currency"):
            CostBreakdown(
                input_cost_usd=0.001,
                output_cost_usd=0.002,
                total_cost_usd=0.003,
                currency="XY",  # only 2 chars
            )

    def test_invalid_pricing_date_raises(self) -> None:
        with pytest.raises(ValueError, match="pricing_date"):
            CostBreakdown(
                input_cost_usd=0.001,
                output_cost_usd=0.002,
                total_cost_usd=0.003,
                pricing_date="01-01-2024",  # wrong format
            )


# ===========================================================================
# PricingTier — optional fields + unknown system
# ===========================================================================

@pytest.mark.unit
class TestPricingTierFull:
    def _base(self, **kw: object) -> PricingTier:
        defaults = {
            "system": GenAISystem.OPENAI,
            "model": "gpt-4o",
            "input_per_million_usd": 5.0,
            "output_per_million_usd": 15.0,
            "effective_date": "2024-01-01",
        }
        defaults.update(kw)
        return PricingTier(**defaults)  # type: ignore[arg-type]

    def test_cached_input_optional_in_to_dict(self) -> None:
        pt = self._base(cached_input_per_million_usd=2.5)
        d = pt.to_dict()
        assert d["cached_input_per_million_usd"] == pytest.approx(2.5)

    def test_reasoning_optional_in_to_dict(self) -> None:
        pt = self._base(reasoning_per_million_usd=10.0)
        d = pt.to_dict()
        assert d["reasoning_per_million_usd"] == pytest.approx(10.0)

    def test_unknown_system_fallback_in_from_dict(self) -> None:
        d = {
            "system": "some_future_provider",
            "model": "m",
            "input_per_million_usd": 1.0,
            "output_per_million_usd": 2.0,
            "effective_date": "2025-01-01",
        }
        pt = PricingTier.from_dict(d)
        assert pt.system == "some_future_provider"


# ===========================================================================
# ToolCall — optional fields
# ===========================================================================

@pytest.mark.unit
class TestToolCallFull:
    def test_all_optional_fields_in_to_dict(self) -> None:
        tc = ToolCall(
            tool_call_id="call-1",
            function_name="search",
            status="success",
            arguments_hash=_SHA256,
            error_type="TimeoutError",
            duration_ms=42.5,
        )
        d = tc.to_dict()
        assert d["arguments_hash"] == _SHA256
        assert d["error_type"] == "TimeoutError"
        assert d["duration_ms"] == pytest.approx(42.5)

    def test_from_dict_roundtrip_with_optionals(self) -> None:
        tc = ToolCall(
            tool_call_id="call-1",
            function_name="search",
            status="success",
            duration_ms=100.0,
        )
        tc2 = ToolCall.from_dict(tc.to_dict())
        assert tc2.duration_ms == pytest.approx(100.0)
        assert tc2.arguments_hash is None

    def test_invalid_arguments_hash_raises(self) -> None:
        with pytest.raises(ValueError, match="arguments_hash"):
            ToolCall(
                tool_call_id="c1",
                function_name="fn",
                status="success",
                arguments_hash="tooshort",
            )

    def test_negative_duration_raises(self) -> None:
        with pytest.raises(ValueError, match="duration_ms"):
            ToolCall(
                tool_call_id="c1",
                function_name="fn",
                status="success",
                duration_ms=-1.0,
            )


# ===========================================================================
# ReasoningStep — optional fields
# ===========================================================================

@pytest.mark.unit
class TestReasoningStepFull:
    def test_all_optional_fields_in_to_dict(self) -> None:
        rs = ReasoningStep(
            step_index=0,
            reasoning_tokens=50,
            duration_ms=12.5,
            content_hash=_SHA256,
        )
        d = rs.to_dict()
        assert d["duration_ms"] == pytest.approx(12.5)
        assert d["content_hash"] == _SHA256

    def test_from_dict_roundtrip_with_optionals(self) -> None:
        rs = ReasoningStep(step_index=0, reasoning_tokens=50, duration_ms=10.0)
        rs2 = ReasoningStep.from_dict(rs.to_dict())
        assert rs2.duration_ms == pytest.approx(10.0)
        assert rs2.content_hash is None

    def test_invalid_content_hash_raises(self) -> None:
        with pytest.raises(ValueError, match="content_hash"):
            ReasoningStep(step_index=0, reasoning_tokens=10, content_hash="bad")


# ===========================================================================
# DecisionPoint — optional rationale
# ===========================================================================

@pytest.mark.unit
class TestDecisionPointFull:
    def test_rationale_in_to_dict(self) -> None:
        dp = DecisionPoint(
            decision_id="dp-1",
            decision_type="tool_selection",
            options_considered=["search", "read"],
            chosen_option="search",
            rationale="Fastest for this query",
        )
        d = dp.to_dict()
        assert d["rationale"] == "Fastest for this query"

    def test_from_dict_roundtrip_with_rationale(self) -> None:
        dp = DecisionPoint(
            decision_id="dp-1",
            decision_type="route_choice",
            options_considered=["a", "b"],
            chosen_option="a",
            rationale="Preferred path",
        )
        dp2 = DecisionPoint.from_dict(dp.to_dict())
        assert dp2.rationale == "Preferred path"


# ===========================================================================
# SpanPayload — optional fields
# ===========================================================================

@pytest.mark.unit
class TestSpanPayloadFull:
    def _now(self) -> int:
        import time  # noqa: PLC0415
        return int(time.time_ns())

    def test_all_optional_fields_in_to_dict(self) -> None:
        t0 = self._now()
        t1 = t0 + 10_000_000
        sp = SpanPayload(
            span_id=_SPAN_ID,
            trace_id=_TRACE_ID,
            span_name="test-span",
            operation=GenAIOperationName.CHAT,
            span_kind=SpanKind.CLIENT,
            status="ok",
            start_time_unix_nano=t0,
            end_time_unix_nano=t1,
            duration_ms=10.0,
            parent_span_id="d" * 16,
            agent_run_id="run-42",
            model=_model(),
            token_usage=_token_usage(),
            cost=_cost(),
            tool_calls=[ToolCall(tool_call_id="c1", function_name="f", status="success")],
            reasoning_steps=[ReasoningStep(step_index=0, reasoning_tokens=5)],
            finish_reason="stop",
            error="oops",
            error_type="ValueError",
            attributes={"key": "val"},
        )
        d = sp.to_dict()
        assert d["parent_span_id"] == "d" * 16
        assert d["agent_run_id"] == "run-42"
        assert "model" in d
        assert "token_usage" in d
        assert "cost" in d
        assert len(d["tool_calls"]) == 1
        assert len(d["reasoning_steps"]) == 1
        assert d["finish_reason"] == "stop"
        assert d["error"] == "oops"
        assert d["error_type"] == "ValueError"
        assert d["attributes"] == {"key": "val"}

    def test_unknown_operation_and_span_kind_roundtrip(self) -> None:
        t0 = self._now()
        t1 = t0 + 5_000_000
        # Supply string values instead of enum
        sp = SpanPayload(
            span_id=_SPAN_ID,
            trace_id=_TRACE_ID,
            span_name="raw-op",
            operation="custom_operation",
            span_kind="CUSTOM_KIND",
            status="ok",
            start_time_unix_nano=t0,
            end_time_unix_nano=t1,
            duration_ms=5.0,
        )
        d = sp.to_dict()
        assert d["operation"] == "custom_operation"
        assert d["span_kind"] == "CUSTOM_KIND"
        # from_dict with unknown values should fall back to raw string
        sp2 = SpanPayload.from_dict(d)
        assert sp2.operation == "custom_operation"
        assert sp2.span_kind == "CUSTOM_KIND"


# ===========================================================================
# AgentStepPayload — optional fields
# ===========================================================================

@pytest.mark.unit
class TestAgentStepPayloadFull:
    def _now(self) -> int:
        import time  # noqa: PLC0415
        return int(time.time_ns())

    def test_all_optional_fields_in_to_dict(self) -> None:
        t0 = self._now()
        t1 = t0 + 5_000_000
        step = AgentStepPayload(
            agent_run_id="run-1",
            step_index=0,
            span_id=_SPAN_ID,
            trace_id=_TRACE_ID,
            operation=GenAIOperationName.INVOKE_AGENT,
            tool_calls=[],
            reasoning_steps=[],
            decision_points=[
                DecisionPoint(
                    decision_id="dp-1",
                    decision_type="tool_selection",
                    options_considered=["a", "b"],
                    chosen_option="a",
                )
            ],
            status="ok",
            start_time_unix_nano=t0,
            end_time_unix_nano=t1,
            duration_ms=5.0,
            parent_span_id="e" * 16,
            model=_model(),
            token_usage=_token_usage(),
            cost=_cost(),
            error="something bad",
            error_type="RuntimeError",
        )
        d = step.to_dict()
        assert d["parent_span_id"] == "e" * 16
        assert "model" in d
        assert "token_usage" in d
        assert "cost" in d
        assert d["error"] == "something bad"
        assert d["error_type"] == "RuntimeError"
        assert len(d["decision_points"]) == 1

    def test_unknown_operation_fallback(self) -> None:
        t0 = self._now()
        t1 = t0 + 1_000_000
        step = AgentStepPayload(
            agent_run_id="run-1",
            step_index=0,
            span_id=_SPAN_ID,
            trace_id=_TRACE_ID,
            operation="custom_op",
            tool_calls=[],
            reasoning_steps=[],
            decision_points=[],
            status="ok",
            start_time_unix_nano=t0,
            end_time_unix_nano=t1,
            duration_ms=1.0,
        )
        step2 = AgentStepPayload.from_dict(step.to_dict())
        assert step2.operation == "custom_op"


# ===========================================================================
# AgentRunPayload — termination_reason optional
# ===========================================================================

@pytest.mark.unit
class TestAgentRunPayloadFull:
    def _now(self) -> int:
        import time  # noqa: PLC0415
        return int(time.time_ns())

    def test_termination_reason_in_to_dict(self) -> None:
        t0 = self._now()
        t1 = t0 + 10_000_000
        run = AgentRunPayload(
            agent_run_id="run-1",
            agent_name="my-agent",
            trace_id=_TRACE_ID,
            root_span_id=_SPAN_ID,
            total_steps=3,
            total_model_calls=3,
            total_tool_calls=1,
            total_token_usage=_token_usage(),
            total_cost=_cost(),
            status="ok",
            start_time_unix_nano=t0,
            end_time_unix_nano=t1,
            duration_ms=10.0,
            termination_reason="goal_reached",
        )
        d = run.to_dict()
        assert d["termination_reason"] == "goal_reached"


# ===========================================================================
# Namespace: audit  # noqa: ERA001
# ===========================================================================

@pytest.mark.unit
class TestAuditNamespaceFull:
    def test_key_rotated_with_all_optionals(self) -> None:
        p = AuditKeyRotatedPayload(
            key_id="k1",
            previous_key_id="k0",
            rotated_at="2024-01-01T00:00:00Z",
            rotated_by="admin",
            rotation_reason="scheduled",
            effective_from_event_id="01AAAAAAAAAAAAAAAAAAAAAAAABB",
        )
        d = p.to_dict()
        assert d["rotation_reason"] == "scheduled"
        assert "effective_from_event_id" in d

    def test_key_rotated_validation_errors(self) -> None:
        with pytest.raises(ValueError):
            AuditKeyRotatedPayload(
                key_id="",
                previous_key_id="k0",
                rotated_at="t",
                rotated_by="admin",
            )
        with pytest.raises(ValueError):
            AuditKeyRotatedPayload(
                key_id="k1",
                previous_key_id="",
                rotated_at="t",
                rotated_by="admin",
            )
        with pytest.raises(ValueError):
            AuditKeyRotatedPayload(
                key_id="k1",
                previous_key_id="k0",
                rotated_at="",
                rotated_by="admin",
            )
        with pytest.raises(ValueError):
            AuditKeyRotatedPayload(
                key_id="k1",
                previous_key_id="k0",
                rotated_at="t",
                rotated_by="",
            )

    def test_chain_tampered_with_all_optionals(self) -> None:
        p = AuditChainTamperedPayload(
            first_tampered_event_id="ev-1",
            tampered_count=2,
            detected_at="2024-01-01T00:00:00Z",
            detected_by="verifier",
            gap_count=1,
            gap_prev_ids=["ev-0"],
            severity="high",
        )
        d = p.to_dict()
        assert d["gap_count"] == 1
        assert d["gap_prev_ids"] == ["ev-0"]
        assert d["severity"] == "high"


# ===========================================================================
# Namespace: cache  # noqa: ERA001
# ===========================================================================

@pytest.mark.unit
class TestCacheNamespaceFull:
    def test_cache_hit_with_all_optionals(self) -> None:
        p = CacheHitPayload(
            key_hash=_SHA256,
            namespace="prompt",
            similarity_score=0.99,
            ttl_remaining_seconds=300,
            cached_model=_model(),
            cost_saved=_cost(),
            tokens_saved=_token_usage(),
            lookup_duration_ms=5.0,
        )
        d = p.to_dict()
        assert "ttl_remaining_seconds" in d
        assert "cached_model" in d
        assert "cost_saved" in d
        assert "tokens_saved" in d
        assert "lookup_duration_ms" in d

    def test_cache_miss_with_all_optionals(self) -> None:
        p = CacheMissPayload(
            key_hash=_SHA256,
            namespace="prompt",
            best_similarity_score=0.7,
            similarity_threshold=0.9,
            lookup_duration_ms=3.5,
        )
        d = p.to_dict()
        assert "best_similarity_score" in d
        assert "similarity_threshold" in d
        assert "lookup_duration_ms" in d

    def test_cache_evicted_with_entry_age(self) -> None:
        p = CacheEvictedPayload(
            key_hash=_SHA256,
            namespace="prompt",
            eviction_reason="ttl_expired",
            entry_age_seconds=3600,
        )
        d = p.to_dict()
        assert "entry_age_seconds" in d


# ===========================================================================
# Namespace: cost  # noqa: ERA001
# ===========================================================================

@pytest.mark.unit
class TestCostNamespaceFull:
    def test_cost_token_recorded_with_all_optionals(self) -> None:
        from agentobs.namespaces.trace import PricingTier  # noqa: PLC0415
        p = CostTokenRecordedPayload(
            cost=_cost(),
            token_usage=_token_usage(),
            model=_model(),
            pricing_tier=PricingTier(
                system=GenAISystem.OPENAI,
                model="gpt-4o",
                input_per_million_usd=5.0,
                output_per_million_usd=15.0,
                effective_date="2024-01-01",
            ),
            span_id=_SPAN_ID,
            agent_run_id="run-1",
        )
        d = p.to_dict()
        assert "pricing_tier" in d
        assert d["span_id"] == _SPAN_ID
        assert d["agent_run_id"] == "run-1"

    def test_cost_session_with_all_optionals(self) -> None:
        p = CostSessionRecordedPayload(
            total_cost=_cost(),
            total_token_usage=_token_usage(),
            call_count=5,
            session_duration_ms=1000.0,
            models_used=["gpt-4o", "gpt-3.5"],
        )
        d = p.to_dict()
        assert "session_duration_ms" in d
        assert d["models_used"] == ["gpt-4o", "gpt-3.5"]

    def test_cost_attributed_with_source_event_ids(self) -> None:
        p = CostAttributedPayload(
            cost=_cost(),
            attribution_target="user:alice",
            attribution_type="direct",
            source_event_ids=["ev-1", "ev-2"],
        )
        d = p.to_dict()
        assert d["source_event_ids"] == ["ev-1", "ev-2"]


# ===========================================================================
# Namespace: diff  # noqa: ERA001
# ===========================================================================

@pytest.mark.unit
class TestDiffNamespaceFull:
    def test_diff_with_all_optionals(self) -> None:
        p = DiffComputedPayload(
            ref_event_id="ev-1",
            target_event_id="ev-2",
            diff_type="prompt",
            similarity_score=0.85,
            added_tokens=12,
            removed_tokens=8,
            diff_algorithm="embedding_cosine",
            ref_content_hash=_SHA256,
            target_content_hash="d" * 64,
            computation_duration_ms=15.0,
        )
        d = p.to_dict()
        assert d["added_tokens"] == 12
        assert d["removed_tokens"] == 8
        assert d["diff_algorithm"] == "embedding_cosine"
        assert "ref_content_hash" in d
        assert "target_content_hash" in d
        assert "computation_duration_ms" in d


# ===========================================================================
# Namespace: eval  # noqa: ERA001
# ===========================================================================

@pytest.mark.unit
class TestEvalNamespaceFull:
    def test_eval_score_with_all_optionals(self) -> None:
        p = EvalScoreRecordedPayload(
            evaluator="human",
            metric_name="faithfulness",
            score=0.95,
            score_min=0.0,
            score_max=1.0,
            threshold=0.8,
            passed=True,
            subject_event_id="ev-1",
            subject_type="response",
            eval_run_id="eval-42",
            rationale="Accurate and grounded",
            model=_model(),
        )
        d = p.to_dict()
        assert d["score_min"] == pytest.approx(0.0)
        assert d["score_max"] == pytest.approx(1.0)
        assert d["threshold"] == pytest.approx(0.8)
        assert d["passed"] is True
        assert "model" in d

    def test_eval_regression_with_all_optionals(self) -> None:
        p = EvalRegressionDetectedPayload(
            metric_name="f1",
            baseline_score=0.9,
            current_score=0.7,
            delta=-0.2,
            regression_pct=22.2,
            severity="high",
            affected_model=_model(),
            eval_run_id="eval-1",
            sample_count=100,
        )
        d = p.to_dict()
        assert d["severity"] == "high"
        assert "affected_model" in d
        assert d["eval_run_id"] == "eval-1"
        assert d["sample_count"] == 100

    def test_eval_scenario_started_with_all_optionals(self) -> None:
        p = EvalScenarioStartedPayload(
            scenario_id="sc-1",
            scenario_name="QA Benchmark",
            evaluator="auto",
            dataset_id="ds-42",
            expected_sample_count=500,
            metrics=["precision", "recall"],
        )
        d = p.to_dict()
        assert d["dataset_id"] == "ds-42"
        assert d["expected_sample_count"] == 500
        assert d["metrics"] == ["precision", "recall"]

    def test_eval_scenario_completed_with_all_optionals(self) -> None:
        p = EvalScenarioCompletedPayload(
            scenario_id="sc-1",
            status="passed",
            duration_ms=5000.0,
            completed_sample_count=500,
            scores_summary={"f1": 0.92},
            errors=["warning: skipped 2 samples"],
        )
        d = p.to_dict()
        assert d["completed_sample_count"] == 500
        assert d["scores_summary"] == {"f1": 0.92}
        assert d["errors"] == ["warning: skipped 2 samples"]


# ===========================================================================
# Namespace: fence  # noqa: ERA001
# ===========================================================================

@pytest.mark.unit
class TestFenceNamespaceFull:
    def test_fence_validated_with_all_optionals(self) -> None:
        p = FenceValidatedPayload(
            fence_id="f1",
            schema_name="ResponseSchema",
            attempt=1,
            output_type="json_schema",
            span_id=_SPAN_ID,
            validation_duration_ms=2.5,
        )
        d = p.to_dict()
        assert d["output_type"] == "json_schema"
        assert d["span_id"] == _SPAN_ID
        assert "validation_duration_ms" in d

    def test_fence_retry_with_optionals(self) -> None:
        p = FenceRetryTriggeredPayload(
            fence_id="f1",
            schema_name="S",
            attempt=1,
            max_attempts=3,
            violation_summary="missing field",
            output_type="json_schema",
            span_id=_SPAN_ID,
        )
        d = p.to_dict()
        assert d["output_type"] == "json_schema"
        assert d["span_id"] == _SPAN_ID

    def test_fence_max_retries_with_total_extra_cost(self) -> None:
        p = FenceMaxRetriesExceededPayload(
            fence_id="f1",
            schema_name="S",
            attempts_made=3,
            final_violation_summary="still invalid",
            output_type="json_schema",
            span_id=_SPAN_ID,
            total_extra_cost=_cost(),
        )
        d = p.to_dict()
        assert d["output_type"] == "json_schema"
        assert d["span_id"] == _SPAN_ID
        assert "total_extra_cost" in d


# ===========================================================================
# Namespace: guard  # noqa: ERA001
# ===========================================================================

@pytest.mark.unit
class TestGuardNamespaceFull:
    def test_guard_with_all_optionals(self) -> None:
        p = GuardPayload(
            classifier="openai-moderation",
            direction="output",
            action="passed",
            score=0.1,
            score_min=0.0,
            score_max=1.0,
            threshold=0.5,
            categories=["hate", "violence"],
            triggered_categories=["violence"],
            span_id=_SPAN_ID,
            latency_ms=12.5,
            policy_id="policy-v1",
            content_hash=_SHA256,
        )
        d = p.to_dict()
        assert d["score_min"] == pytest.approx(0.0)
        assert d["score_max"] == pytest.approx(1.0)
        assert d["threshold"] == pytest.approx(0.5)
        assert d["categories"] == ["hate", "violence"]
        assert d["triggered_categories"] == ["violence"]
        assert d["span_id"] == _SPAN_ID
        assert d["latency_ms"] == pytest.approx(12.5)
        assert d["policy_id"] == "policy-v1"
        assert d["content_hash"] == _SHA256


# ===========================================================================
# Namespace: prompt  # noqa: ERA001
# ===========================================================================

@pytest.mark.unit
class TestPromptNamespaceFull:
    def test_prompt_rendered_with_all_optionals(self) -> None:
        p = PromptRenderedPayload(
            template_id="t1",
            version="1.0",
            rendered_hash=_SHA256,
            variable_count=3,
            variable_names=["name", "context", "style"],
            char_count=500,
            token_estimate=125,
            language="en",
            span_id=_SPAN_ID,
        )
        d = p.to_dict()
        assert d["variable_count"] == 3
        assert d["variable_names"] == ["name", "context", "style"]
        assert d["char_count"] == 500
        assert d["token_estimate"] == 125
        assert d["language"] == "en"
        assert d["span_id"] == _SPAN_ID

    def test_prompt_template_loaded_with_all_optionals(self) -> None:
        p = PromptTemplateLoadedPayload(
            template_id="t1",
            version="1.0",
            source="registry",
            template_hash=_SHA256,
            load_duration_ms=10.0,
            cache_hit=True,
        )
        d = p.to_dict()
        assert "template_hash" in d
        assert "load_duration_ms" in d
        assert d["cache_hit"] is True


# ===========================================================================
# Namespace: redact  # noqa: ERA001
# ===========================================================================

@pytest.mark.unit
class TestRedactNamespaceFull:
    def test_pii_detected_with_all_optionals(self) -> None:
        p = RedactPiiDetectedPayload(
            detected_categories=["email", "phone"],
            field_names=["user.email"],
            sensitivity_level="HIGH",
            detection_count=2,
            detector="presidio",
            subject_event_id="ev-1",
        )
        d = p.to_dict()
        assert d["detection_count"] == 2
        assert d["detector"] == "presidio"
        assert d["subject_event_id"] == "ev-1"

    def test_phi_detected_with_all_optionals(self) -> None:
        p = RedactPhiDetectedPayload(
            detected_categories=["diagnosis"],
            field_names=["record.diagnosis"],
            sensitivity_level="PHI",
            detection_count=1,
            detector="custom-phi",
            subject_event_id="ev-2",
            hipaa_covered=True,
        )
        d = p.to_dict()
        assert d["detection_count"] == 1
        assert d["detector"] == "custom-phi"
        assert d["subject_event_id"] == "ev-2"
        assert d["hipaa_covered"] is True


# ===========================================================================
# Namespace: template  # noqa: ERA001
# ===========================================================================

@pytest.mark.unit
class TestTemplateNamespaceFull:
    def test_template_registered_with_all_optionals(self) -> None:
        p = TemplateRegisteredPayload(
            template_id="t1",
            version="1.0",
            template_hash=_SHA256,
            variable_names=["a", "b"],
            variable_count=2,
            language="en",
            char_count=800,
            registered_by="alice",
            is_active=True,
            tags={"team": "nlp"},
        )
        d = p.to_dict()
        assert d["variable_names"] == ["a", "b"]
        assert d["variable_count"] == 2
        assert d["language"] == "en"
        assert d["char_count"] == 800
        assert d["registered_by"] == "alice"
        assert d["is_active"] is True
        assert d["tags"] == {"team": "nlp"}

    def test_template_variable_bound_with_all_optionals(self) -> None:
        p = TemplateVariableBoundPayload(
            template_id="t1",
            version="1.0",
            variable_name="user_name",
            value_type="string",
            value_length=12,
            value_hash=_SHA256,
            is_sensitive=False,
            span_id=_SPAN_ID,
        )
        d = p.to_dict()
        assert d["value_type"] == "string"
        assert d["value_length"] == 12
        assert d["value_hash"] == _SHA256
        assert d["is_sensitive"] is False
        assert d["span_id"] == _SPAN_ID


# ===========================================================================
# integrations/__init__.py — trivial import coverage
# ===========================================================================

@pytest.mark.unit
class TestIntegrationsInit:
    def test_integrations_package_importable(self) -> None:
        try:  # noqa: SIM105
            import agentobs.integrations  # noqa: F401, PLC0415
        except ImportError:
            pass  # optional dependency not installed — still covers the import lines
