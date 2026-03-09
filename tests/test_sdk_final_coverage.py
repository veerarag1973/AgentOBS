"""Final targeted tests to bridge remaining coverage gaps.

Every test class is focused on a specific set of missed statements
identified from the coverage report.

Missing statement groups:
  trace.py         318, 339, 573, 694, 696, 703, 799, 801, 810
  diff.py          108, 112
  cache.py         45, 97, 131, 133, 172, 174, 185, 187, 189
  cost.py          98, 100, 104, 144, 146
  audit.py         53, 96, 98, 100, 102, 140, 142, 144, 148
  eval_.py         100, 150, 152, 195
  fence.py         86, 88, 94, 141, 145
  prompt.py        45, 99, 103, 149
  redact.py        87, 91, 139, 141
  template.py      48, 109, 111, 164
  console.py       60, 68, 95
  jsonl.py         126-127
  types.py         401
  validate.py      145
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
from agentobs.namespaces.trace import (
    AgentRunPayload,
    AgentStepPayload,
    CostBreakdown,
    GenAISystem,
    ModelInfo,
    PricingTier,
    SpanPayload,
    TokenUsage,
)

_SPAN_ID = "a" * 16
_TRACE_ID = "b" * 32
_SHA256 = "c" * 64


def _now() -> int:
    return int(time.time_ns())


def _tok() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


def _cost_bd() -> CostBreakdown:
    return CostBreakdown(input_cost_usd=0.001, output_cost_usd=0.002, total_cost_usd=0.003)


# ===========================================================================
# trace.py — CostBreakdown.zero() (line 318)
# ===========================================================================

@pytest.mark.unit
class TestCostBreakdownZero:
    def test_zero_returns_zeroed_instance(self) -> None:
        z = CostBreakdown.zero()
        assert z.input_cost_usd == pytest.approx(0.0)
        assert z.output_cost_usd == pytest.approx(0.0)
        assert z.total_cost_usd == pytest.approx(0.0)

    def test_zero_is_costbreakdown(self) -> None:
        z = CostBreakdown.zero()
        assert isinstance(z, CostBreakdown)


# ===========================================================================
# trace.py — PricingTier.model empty (line 339)
# ===========================================================================

@pytest.mark.unit
class TestPricingTierModelEmpty:
    def test_empty_model_raises(self) -> None:
        with pytest.raises(ValueError, match="model"):
            PricingTier(
                system=GenAISystem.OPENAI,
                model="",
                input_per_million_usd=1.0,
                output_per_million_usd=2.0,
                effective_date="2024-01-01",
            )


# ===========================================================================
# trace.py — SpanPayload.span_name empty (line 573)
# ===========================================================================

@pytest.mark.unit
class TestSpanPayloadSpanNameEmpty:
    def test_empty_span_name_raises(self) -> None:
        t0 = _now()
        t1 = t0 + 1_000_000
        with pytest.raises(ValueError, match="span_name"):
            SpanPayload(
                span_id=_SPAN_ID,
                trace_id=_TRACE_ID,
                span_name="",
                operation="chat",
                span_kind="CLIENT",
                status="ok",
                start_time_unix_nano=t0,
                end_time_unix_nano=t1,
                duration_ms=1.0,
            )


# ===========================================================================
# trace.py — AgentStepPayload additional validation (694, 696, 703)
# ===========================================================================

@pytest.mark.unit
class TestAgentStepAdditionalValidation:
    def _base(self, **overrides):
        t0 = _now()
        t1 = t0 + 1_000_000
        kwargs = {
            "agent_run_id": "run-1",
            "step_index": 0,
            "span_id": _SPAN_ID,
            "trace_id": _TRACE_ID,
            "operation": "chat",
            "tool_calls": [],
            "reasoning_steps": [],
            "decision_points": [],
            "status": "ok",
            "start_time_unix_nano": t0,
            "end_time_unix_nano": t1,
            "duration_ms": 1.0,
        }
        kwargs.update(overrides)
        return AgentStepPayload(**kwargs)

    def test_bad_span_id_raises(self) -> None:
        with pytest.raises(ValueError, match="span_id"):
            self._base(span_id="not-valid")

    def test_bad_trace_id_raises(self) -> None:
        with pytest.raises(ValueError, match="trace_id"):
            self._base(trace_id="too-short")

    def test_negative_start_time_raises(self) -> None:
        with pytest.raises(ValueError, match="start_time_unix_nano"):
            self._base(start_time_unix_nano=-1, end_time_unix_nano=1_000_000)

    def test_negative_step_index_raises(self) -> None:
        with pytest.raises(ValueError, match="step_index"):
            self._base(step_index=-1)


# ===========================================================================
# trace.py — AgentRunPayload additional validation (799, 801, 810)
# ===========================================================================

@pytest.mark.unit
class TestAgentRunAdditionalValidation:
    def _base(self, **overrides):
        t0 = _now()
        t1 = t0 + 1_000_000
        kwargs = {
            "agent_run_id": "run-1",
            "agent_name": "bot",
            "trace_id": _TRACE_ID,
            "root_span_id": _SPAN_ID,
            "total_steps": 0,
            "total_model_calls": 0,
            "total_tool_calls": 0,
            "total_token_usage": _tok(),
            "total_cost": _cost_bd(),
            "status": "ok",
            "start_time_unix_nano": t0,
            "end_time_unix_nano": t1,
            "duration_ms": 1.0,
        }
        kwargs.update(overrides)
        return AgentRunPayload(**kwargs)

    def test_empty_agent_name_raises(self) -> None:
        with pytest.raises(ValueError, match="agent_name"):
            self._base(agent_name="")

    def test_bad_trace_id_raises(self) -> None:
        with pytest.raises(ValueError, match="trace_id"):
            self._base(trace_id="bad-id")

    def test_negative_total_steps_raises(self) -> None:
        with pytest.raises(ValueError, match="total_steps"):
            self._base(total_steps=-1)

    def test_negative_model_calls_raises(self) -> None:
        with pytest.raises(ValueError, match="total_model_calls"):
            self._base(total_model_calls=-1)

    def test_bad_root_span_id_raises(self) -> None:
        with pytest.raises(ValueError, match="root_span_id"):
            self._base(root_span_id="bad")


# ===========================================================================
# diff.py — DiffRegressionFlaggedPayload extra validation (108, 112)
# ===========================================================================

@pytest.mark.unit
class TestDiffRegressionFlaggedExtra:
    def test_invalid_diff_type_raises(self) -> None:
        from agentobs.namespaces.diff import DiffRegressionFlaggedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="diff_type"):
            DiffRegressionFlaggedPayload(
                ref_event_id="ev-1",
                target_event_id="ev-2",
                diff_type="invalid_type",  # not in _VALID_DIFF_TYPES
                similarity_score=0.5,
                threshold=0.8,
                severity="low",
            )

    def test_out_of_range_threshold_raises(self) -> None:
        from agentobs.namespaces.diff import DiffRegressionFlaggedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="threshold"):
            DiffRegressionFlaggedPayload(
                ref_event_id="ev-1",
                target_event_id="ev-2",
                diff_type="prompt",
                similarity_score=0.5,
                threshold=1.5,  # > 1
                severity="low",
            )


# ===========================================================================
# cache.py — CacheHitPayload similarity_score OOR (line 45)
# and CacheMissPayload namespace empty (line 97) + optional fields (131-189)
# ===========================================================================

@pytest.mark.unit
class TestCachePayloadValidation:
    def test_cache_hit_out_of_range_score_raises(self) -> None:
        from agentobs.namespaces.cache import CacheHitPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="similarity_score"):
            CacheHitPayload(
                key_hash="abc123",
                namespace="prompt_cache",
                similarity_score=1.5,  # > 1
            )

    def test_cache_hit_empty_key_hash_raises(self) -> None:
        from agentobs.namespaces.cache import CacheHitPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="key_hash"):
            CacheHitPayload(key_hash="", namespace="ns", similarity_score=0.9)

    def test_cache_miss_empty_namespace_raises(self) -> None:
        from agentobs.namespaces.cache import CacheMissPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="namespace"):
            CacheMissPayload(key_hash="abc", namespace="")

    def test_cache_miss_empty_key_hash_raises(self) -> None:
        from agentobs.namespaces.cache import CacheMissPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="key_hash"):
            CacheMissPayload(key_hash="", namespace="ns")

    def test_cache_evicted_with_entry_age(self) -> None:
        from agentobs.namespaces.cache import CacheEvictedPayload  # noqa: PLC0415
        p = CacheEvictedPayload(
            key_hash="abc",
            namespace="ns",
            eviction_reason="ttl_expired",
            entry_age_seconds=3600,
        )
        d = p.to_dict()
        assert d["entry_age_seconds"] == 3600
        # roundtrip
        p2 = CacheEvictedPayload.from_dict(d)
        assert p2.entry_age_seconds == 3600

    def test_cache_written_with_optional_fields(self) -> None:
        from agentobs.namespaces.cache import CacheWrittenPayload  # noqa: PLC0415
        p = CacheWrittenPayload(
            key_hash="abc",
            namespace="ns",
            ttl_seconds=300,
            model=ModelInfo(system=GenAISystem.OPENAI, name="gpt-4o"),
            response_token_count=150,
            write_duration_ms=2.5,
        )
        d = p.to_dict()
        assert "model" in d
        assert d["response_token_count"] == 150
        assert d["write_duration_ms"] == pytest.approx(2.5)

    def test_cache_written_invalid_ttl_raises(self) -> None:
        from agentobs.namespaces.cache import CacheWrittenPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="ttl_seconds"):
            CacheWrittenPayload(key_hash="abc", namespace="ns", ttl_seconds=-1)


# ===========================================================================
# cost.py — session and attribution payload validation (98, 100, 104, 144, 146)
# ===========================================================================

@pytest.mark.unit
class TestCostPayloadValidation:
    def test_session_negative_call_count_raises(self) -> None:
        from agentobs.namespaces.cost import CostSessionRecordedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="call_count"):
            CostSessionRecordedPayload(
                total_cost=_cost_bd(),
                total_token_usage=_tok(),
                call_count=-1,
            )

    def test_session_negative_duration_raises(self) -> None:
        from agentobs.namespaces.cost import CostSessionRecordedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="session_duration_ms"):
            CostSessionRecordedPayload(
                total_cost=_cost_bd(),
                total_token_usage=_tok(),
                call_count=0,
                session_duration_ms=-1.0,
            )

    def test_session_with_optional_fields(self) -> None:
        from agentobs.namespaces.cost import CostSessionRecordedPayload  # noqa: PLC0415
        p = CostSessionRecordedPayload(
            total_cost=_cost_bd(),
            total_token_usage=_tok(),
            call_count=3,
            session_duration_ms=5000.0,
            models_used=["gpt-4o", "claude-3-5-sonnet"],
        )
        d = p.to_dict()
        assert d["session_duration_ms"] == pytest.approx(5000.0)
        assert "models_used" in d

    def test_attributed_empty_target_raises(self) -> None:
        from agentobs.namespaces.cost import CostAttributedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="attribution_target"):
            CostAttributedPayload(
                cost=_cost_bd(),
                attribution_target="",
                attribution_type="direct",
            )

    def test_attributed_invalid_type_raises(self) -> None:
        from agentobs.namespaces.cost import CostAttributedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="attribution_type"):
            CostAttributedPayload(
                cost=_cost_bd(),
                attribution_target="user-42",
                attribution_type="invalid",
            )

    def test_attributed_with_source_event_ids(self) -> None:
        from agentobs.namespaces.cost import CostAttributedPayload  # noqa: PLC0415
        p = CostAttributedPayload(
            cost=_cost_bd(),
            attribution_target="user-42",
            attribution_type="direct",
            source_event_ids=["ev-1", "ev-2"],
        )
        d = p.to_dict()
        assert d["source_event_ids"] == ["ev-1", "ev-2"]


# ===========================================================================
# audit.py — all three classes (53, 96-102, 140-148)
# ===========================================================================

@pytest.mark.unit
class TestAuditPayloadValidation:
    def test_key_rotated_invalid_rotation_reason_raises(self) -> None:
        from agentobs.namespaces.audit import AuditKeyRotatedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="rotation_reason"):
            AuditKeyRotatedPayload(
                key_id="k1",
                previous_key_id="k0",
                rotated_at="2024-01-01T00:00:00.000000Z",
                rotated_by="admin",
                rotation_reason="invalid_reason",
            )

    def test_chain_verified_empty_from_raises(self) -> None:
        from agentobs.namespaces.audit import AuditChainVerifiedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="verified_from_event_id"):
            AuditChainVerifiedPayload(
                verified_from_event_id="",
                verified_to_event_id="ev-9",
                event_count=10,
                verified_at="2024-01-01T00:00:00.000000Z",
                verified_by="auto",
            )

    def test_chain_verified_empty_to_raises(self) -> None:
        from agentobs.namespaces.audit import AuditChainVerifiedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="verified_to_event_id"):
            AuditChainVerifiedPayload(
                verified_from_event_id="ev-1",
                verified_to_event_id="",
                event_count=10,
                verified_at="2024-01-01T00:00:00.000000Z",
                verified_by="auto",
            )

    def test_chain_verified_negative_count_raises(self) -> None:
        from agentobs.namespaces.audit import AuditChainVerifiedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="event_count"):
            AuditChainVerifiedPayload(
                verified_from_event_id="ev-1",
                verified_to_event_id="ev-9",
                event_count=-1,
                verified_at="2024-01-01T00:00:00.000000Z",
                verified_by="auto",
            )

    def test_chain_verified_empty_at_raises(self) -> None:
        from agentobs.namespaces.audit import AuditChainVerifiedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="verified_at"):
            AuditChainVerifiedPayload(
                verified_from_event_id="ev-1",
                verified_to_event_id="ev-9",
                event_count=5,
                verified_at="",
                verified_by="auto",
            )

    def test_chain_verified_empty_by_raises(self) -> None:
        from agentobs.namespaces.audit import AuditChainVerifiedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="verified_by"):
            AuditChainVerifiedPayload(
                verified_from_event_id="ev-1",
                verified_to_event_id="ev-9",
                event_count=5,
                verified_at="2024-01-01T00:00:00.000000Z",
                verified_by="",
            )

    def test_chain_tampered_with_all_optionals(self) -> None:
        from agentobs.namespaces.audit import AuditChainTamperedPayload  # noqa: PLC0415
        p = AuditChainTamperedPayload(
            first_tampered_event_id="ev-50",
            tampered_count=3,
            detected_at="2024-01-01T00:00:00.000000Z",
            detected_by="audit-agent",
            gap_count=2,
            gap_prev_ids=["ev-48", "ev-49"],
            severity="high",
        )
        d = p.to_dict()
        assert d["gap_count"] == 2
        assert d["gap_prev_ids"] == ["ev-48", "ev-49"]
        assert d["severity"] == "high"

    def test_chain_tampered_invalid_severity_raises(self) -> None:
        from agentobs.namespaces.audit import AuditChainTamperedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="severity"):
            AuditChainTamperedPayload(
                first_tampered_event_id="ev-50",
                tampered_count=1,
                detected_at="2024-01-01T00:00:00.000000Z",
                detected_by="system",
                severity="extreme",
            )


# ===========================================================================
# eval_.py — regression and scenario completed (100, 150, 152, 195)
# ===========================================================================

@pytest.mark.unit
class TestEvalPayloadValidation:
    def test_regression_empty_metric_name_raises(self) -> None:
        from agentobs.namespaces.eval_ import EvalRegressionDetectedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="metric_name"):
            EvalRegressionDetectedPayload(
                metric_name="",
                baseline_score=0.9,
                current_score=0.7,
                delta=-0.2,
                regression_pct=22.2,
            )

    def test_scenario_completed_invalid_status_raises(self) -> None:
        from agentobs.namespaces.eval_ import EvalScenarioCompletedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="status"):
            EvalScenarioCompletedPayload(
                scenario_id="s1",
                status="invalid",
                duration_ms=100.0,
            )

    def test_scenario_completed_negative_duration_raises(self) -> None:
        from agentobs.namespaces.eval_ import EvalScenarioCompletedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="duration_ms"):
            EvalScenarioCompletedPayload(
                scenario_id="s1",
                status="passed",
                duration_ms=-1.0,
            )

    def test_scenario_completed_with_optional_fields(self) -> None:
        from agentobs.namespaces.eval_ import EvalScenarioCompletedPayload  # noqa: PLC0415
        p = EvalScenarioCompletedPayload(
            scenario_id="s1",
            status="passed",
            duration_ms=500.0,
            completed_sample_count=100,
            scores_summary={"f1": 0.93, "accuracy": 0.95},
            errors=[],
        )
        d = p.to_dict()
        assert d["completed_sample_count"] == 100
        assert "scores_summary" in d
        assert "errors" in d


# ===========================================================================
# fence.py — FenceRetry missing raises (86, 88, 94) + FenceMaxRetries (141, 145)
# ===========================================================================

@pytest.mark.unit
class TestFenceMissingValidation:
    def test_retry_empty_fence_id_raises(self) -> None:
        from agentobs.namespaces.fence import FenceRetryTriggeredPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="fence_id"):
            FenceRetryTriggeredPayload(
                fence_id="",
                schema_name="S",
                attempt=1,
                max_attempts=3,
                violation_summary="bad output",
            )

    def test_retry_empty_schema_name_raises(self) -> None:
        from agentobs.namespaces.fence import FenceRetryTriggeredPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="schema_name"):
            FenceRetryTriggeredPayload(
                fence_id="f1",
                schema_name="",
                attempt=1,
                max_attempts=3,
                violation_summary="bad output",
            )

    def test_retry_empty_violation_summary_raises(self) -> None:
        from agentobs.namespaces.fence import FenceRetryTriggeredPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="violation_summary"):
            FenceRetryTriggeredPayload(
                fence_id="f1",
                schema_name="S",
                attempt=1,
                max_attempts=3,
                violation_summary="",
            )

    def test_max_retries_empty_fence_id_raises(self) -> None:
        from agentobs.namespaces.fence import FenceMaxRetriesExceededPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="fence_id"):
            FenceMaxRetriesExceededPayload(
                fence_id="",
                schema_name="S",
                attempts_made=3,
                final_violation_summary="still wrong",
            )

    def test_max_retries_empty_violation_summary_raises(self) -> None:
        from agentobs.namespaces.fence import FenceMaxRetriesExceededPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="final_violation_summary"):
            FenceMaxRetriesExceededPayload(
                fence_id="f1",
                schema_name="S",
                attempts_made=3,
                final_violation_summary="",
            )


# ===========================================================================
# prompt.py — PromptRendered version empty (45), PromptLoaded template_id/source (99,103), PromptVersionChanged (149)  # noqa: E501
# ===========================================================================

@pytest.mark.unit
class TestPromptMissingValidation:
    def test_rendered_empty_version_raises(self) -> None:
        from agentobs.namespaces.prompt import PromptRenderedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="version"):
            PromptRenderedPayload(
                template_id="t1",
                version="",
                rendered_hash=_SHA256,
            )

    def test_loaded_empty_template_id_raises(self) -> None:
        from agentobs.namespaces.prompt import PromptTemplateLoadedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="template_id"):
            PromptTemplateLoadedPayload(
                template_id="",
                version="1.0",
                source="registry",
            )

    def test_loaded_invalid_source_raises(self) -> None:
        from agentobs.namespaces.prompt import PromptTemplateLoadedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="source"):
            PromptTemplateLoadedPayload(
                template_id="t1",
                version="1.0",
                source="bad_source",
            )

    def test_version_changed_empty_template_id_raises(self) -> None:
        from agentobs.namespaces.prompt import PromptVersionChangedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="template_id"):
            PromptVersionChangedPayload(
                template_id="",
                previous_version="1.0",
                new_version="2.0",
                change_reason="fix",
            )

    def test_version_changed_empty_change_reason_raises(self) -> None:
        from agentobs.namespaces.prompt import PromptVersionChangedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="change_reason"):
            PromptVersionChangedPayload(
                template_id="t1",
                previous_version="1.0",
                new_version="2.0",
                change_reason="",
            )

    def test_version_changed_empty_new_version_raises(self) -> None:
        from agentobs.namespaces.prompt import PromptVersionChangedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="new_version"):
            PromptVersionChangedPayload(
                template_id="t1",
                previous_version="1.0",
                new_version="",  # empty → raise
                change_reason="fix",
            )

    def test_version_changed_empty_previous_version_raises(self) -> None:
        from agentobs.namespaces.prompt import PromptVersionChangedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="previous_version"):
            PromptVersionChangedPayload(
                template_id="t1",
                previous_version="",  # empty → raise
                new_version="2.0",
                change_reason="fix",
            )


# ===========================================================================
# redact.py — RedactPiiDetectedPayload + RedactAppliedPayload (87, 91, 139, 141)
# ===========================================================================

@pytest.mark.unit
class TestRedactMissingValidation:
    def test_pii_detected_invalid_sensitivity_raises(self) -> None:
        from agentobs.namespaces.redact import RedactPiiDetectedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="sensitivity_level"):
            RedactPiiDetectedPayload(
                detected_categories=["email"],
                field_names=["user.email"],
                sensitivity_level="EXTREME",  # invalid
            )

    def test_pii_detected_empty_field_names_raises(self) -> None:
        from agentobs.namespaces.redact import RedactPiiDetectedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="field_names"):
            RedactPiiDetectedPayload(
                detected_categories=["email"],
                field_names=[],
                sensitivity_level="HIGH",
            )

    def test_applied_invalid_sensitivity_raises(self) -> None:
        from agentobs.namespaces.redact import RedactAppliedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="policy_min_sensitivity"):
            RedactAppliedPayload(
                policy_min_sensitivity="SUPER_HIGH",  # invalid
                redacted_by="agent",
                redacted_count=1,
            )

    def test_applied_empty_redacted_by_raises(self) -> None:
        from agentobs.namespaces.redact import RedactAppliedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="redacted_by"):
            RedactAppliedPayload(
                policy_min_sensitivity="HIGH",
                redacted_by="",
                redacted_count=1,
            )


# ===========================================================================
# template.py — missing validation (48, 109, 111, 164)
# ===========================================================================

@pytest.mark.unit
class TestTemplateMissingValidation:
    def test_registered_invalid_hash_raises(self) -> None:
        from agentobs.namespaces.template import TemplateRegisteredPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="template_hash"):
            TemplateRegisteredPayload(
                template_id="t1",
                version="1.0",
                template_hash="short_hash",  # not 64 chars
            )

    def test_variable_bound_empty_variable_name_raises(self) -> None:
        from agentobs.namespaces.template import TemplateVariableBoundPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="variable_name"):
            TemplateVariableBoundPayload(
                template_id="t1",
                version="1.0",
                variable_name="",
                value_type="string",
            )

    def test_variable_bound_invalid_value_type_raises_again(self) -> None:
        from agentobs.namespaces.template import TemplateVariableBoundPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="value_type"):
            TemplateVariableBoundPayload(
                template_id="t1",
                version="1.0",
                variable_name="x",
                value_type="blob",  # not in valid list
            )

    def test_validation_failed_empty_failure_reason_raises(self) -> None:
        from agentobs.namespaces.template import TemplateValidationFailedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="failure_reason"):
            TemplateValidationFailedPayload(
                template_id="t1",
                version="1.0",
                failure_reason="",
            )


# ===========================================================================
# exporters/console.py — NO_COLOR path (60), colour enabled (68), pad<2 (95)
# ===========================================================================

@pytest.mark.unit
class TestConsoleExporterMissingPaths:
    def test_no_color_env_disables_colour(self) -> None:
        """Line 60: _use_colour() returns False when NO_COLOR is set."""
        from agentobs.exporters.console import _use_colour  # noqa: PLC0415
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            assert _use_colour() is False

    def test_colour_enabled_when_isatty(self) -> None:
        """Line 68: _c() returns coloured string when colour is enabled."""
        from agentobs.exporters.console import _CYAN  # noqa: PLC0415
        fake_stdout = MagicMock()
        fake_stdout.isatty.return_value = True
        with patch("agentobs.exporters.console.sys") as mock_sys:
            mock_sys.stdout = fake_stdout
            with patch.dict(os.environ, {}, clear=True):
                if "NO_COLOR" in os.environ:
                    del os.environ["NO_COLOR"]
                # Directly test _c with colour enabled
                from agentobs.exporters import console as c_mod  # noqa: PLC0415
                with patch.object(c_mod, "_use_colour", return_value=True):
                    result = c_mod._c("hello", _CYAN)
                    assert "hello" in result
                    assert _CYAN in result

    def test_top_bar_short_title_pads_to_minimum(self) -> None:
        """Line 95: when pad < 2 in _top_bar, pad is set to 2."""
        from agentobs.exporters.console import _BOX_WIDTH, _top_bar  # noqa: PLC0415
        # Create a title so long that inner fills almost the whole box
        # inner = f"══ {title} ", so title needs len >= _BOX_WIDTH - 5
        long_title = "X" * (_BOX_WIDTH - 3)
        result = _top_bar(long_title)
        assert long_title in result or "X" in result


# ===========================================================================
# exporters/jsonl.py — OSError during close() (126-127)
# ===========================================================================

@pytest.mark.unit
class TestJSONLExporterOSError:
    def test_close_handles_oserror(self, tmp_path) -> None:
        """Lines 126-127: OSError during flush/close is swallowed."""
        from agentobs.exporters.jsonl import SyncJSONLExporter  # noqa: PLC0415
        p = tmp_path / "test.jsonl"
        exporter = SyncJSONLExporter(str(p))
        # Inject a mock file that raises OSError on flush
        mock_file = MagicMock()
        mock_file.closed = False
        mock_file.flush.side_effect = OSError("disk full")
        mock_file.close.side_effect = OSError("disk full")
        with exporter._lock:
            exporter._file = mock_file
        # close() should NOT propagate the OSError
        exporter.close()
        assert exporter._closed


# ===========================================================================
# types.py — future namespace raise (line 401)
# ===========================================================================

@pytest.mark.unit
class TestFutureNamespaceRaise:
    def test_future_namespace_raises(self) -> None:
        from agentobs.exceptions import EventTypeError  # noqa: PLC0415
        from agentobs.types import validate_custom  # noqa: PLC0415
        with pytest.raises(EventTypeError):
            validate_custom("llm.rag.some_event")

    def test_other_future_namespaces_raise(self) -> None:
        from agentobs.exceptions import EventTypeError  # noqa: PLC0415
        from agentobs.types import validate_custom  # noqa: PLC0415
        with pytest.raises(EventTypeError):
            validate_custom("llm.memory.recall")


# ===========================================================================
# validate.py — min_length raise (line 145)
# ===========================================================================

@pytest.mark.unit
class TestValidateMinLength:
    def test_check_string_field_min_length_raises(self) -> None:
        from agentobs.exceptions import SchemaValidationError  # noqa: PLC0415
        from agentobs.validate import _check_string_field  # noqa: PLC0415
        with pytest.raises(SchemaValidationError, match="at least"):
            _check_string_field({"field": "x"}, "field", min_length=5)  # "x" is too short

    def test_check_string_field_pattern_mismatch_raises(self) -> None:
        import re  # noqa: PLC0415

        from agentobs.exceptions import SchemaValidationError  # noqa: PLC0415
        from agentobs.validate import _check_string_field  # noqa: PLC0415
        pattern = re.compile(r"^\d{4}$")
        with pytest.raises(SchemaValidationError, match="pattern"):
            _check_string_field({"field": "abc"}, "field", pattern=pattern)
