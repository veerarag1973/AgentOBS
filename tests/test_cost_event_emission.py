"""Tests for agentobs.cost.emit_cost_event(), emit_cost_attributed(),
and auto_emit_cost config integration."""

from __future__ import annotations

import pytest

import agentobs
from agentobs._span import Span
from agentobs.cost import emit_cost_attributed, emit_cost_event
from agentobs.namespaces.trace import CostBreakdown, ModelInfo, TokenUsage
from agentobs.testing import MockExporter, capture_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_span(
    model: str = "gpt-4o",
    cost: CostBreakdown | None = None,
    token_usage: TokenUsage | None = None,
) -> Span:
    span = Span(name="test-span", model=model)
    span.span_id = "abcdef1234567890"
    span.trace_id = "abcdef1234567890abcdef1234567890"
    if cost is not None:
        span.cost = cost
    if token_usage is not None:
        span.token_usage = token_usage
    span.end()
    return span


def _cost(total_usd: float = 0.005) -> CostBreakdown:
    return CostBreakdown(
        input_cost_usd=total_usd,
        output_cost_usd=0.0,
        total_cost_usd=total_usd,
        pricing_date="2026-03-04",
    )


def _usage(input_tokens: int = 500, output_tokens: int = 200) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


# ---------------------------------------------------------------------------
# emit_cost_event()
# ---------------------------------------------------------------------------


class TestEmitCostEvent:
    def test_emits_cost_token_recorded_event(self):
        span = _make_span(cost=_cost(), token_usage=_usage())
        mock = MockExporter()
        with mock.installed():
            emit_cost_event(span)
        events = mock.filter_by_type("llm.cost.token.recorded")
        assert len(events) == 1

    def test_event_payload_contains_cost(self):
        cost = _cost(total_usd=0.00750)
        span = _make_span(cost=cost, token_usage=_usage())
        mock = MockExporter()
        with mock.installed():
            emit_cost_event(span)
        event = mock.filter_by_type("llm.cost.token.recorded")[0]
        assert event.payload["cost"]["total_cost_usd"] == pytest.approx(0.00750)

    def test_event_payload_contains_token_usage(self):
        span = _make_span(cost=_cost(), token_usage=_usage(input_tokens=300, output_tokens=150))
        mock = MockExporter()
        with mock.installed():
            emit_cost_event(span)
        event = mock.filter_by_type("llm.cost.token.recorded")[0]
        assert event.payload["token_usage"]["input_tokens"] == 300
        assert event.payload["token_usage"]["output_tokens"] == 150

    def test_event_payload_contains_model(self):
        span = _make_span(model="gpt-4o-mini", cost=_cost(), token_usage=_usage())
        mock = MockExporter()
        with mock.installed():
            emit_cost_event(span)
        event = mock.filter_by_type("llm.cost.token.recorded")[0]
        assert "gpt-4o-mini" in str(event.payload["model"])

    def test_event_span_id_set(self):
        span = _make_span(cost=_cost(), token_usage=_usage())
        mock = MockExporter()
        with mock.installed():
            emit_cost_event(span)
        event = mock.filter_by_type("llm.cost.token.recorded")[0]
        assert event.span_id == span.span_id

    def test_noop_when_cost_is_none(self):
        span = _make_span(cost=None)
        mock = MockExporter()
        with mock.installed():
            emit_cost_event(span)
        assert mock.filter_by_type("llm.cost.token.recorded") == []

    def test_fallback_token_usage_when_span_has_none(self):
        """emit_cost_event should not raise when span.token_usage is None."""
        span = _make_span(cost=_cost(), token_usage=None)
        mock = MockExporter()
        with mock.installed():
            emit_cost_event(span)  # must not raise
        events = mock.filter_by_type("llm.cost.token.recorded")
        assert len(events) == 1
        assert events[0].payload["token_usage"]["input_tokens"] == 0

    def test_fallback_model_info_when_span_model_is_none(self):
        """emit_cost_event should not raise when span.model is None."""
        span = Span(name="no-model-span")
        span.span_id = "abcd1234abcd1234"
        span.trace_id = "abcd1234abcd1234abcd1234abcd1234"
        span.cost = _cost()
        span.end()
        mock = MockExporter()
        with mock.installed():
            emit_cost_event(span)  # must not raise
        events = mock.filter_by_type("llm.cost.token.recorded")
        assert len(events) == 1

    def test_override_token_usage_kwarg(self):
        span = _make_span(cost=_cost(), token_usage=_usage(input_tokens=1))
        override_usage = TokenUsage(input_tokens=999, output_tokens=888, total_tokens=1887)
        mock = MockExporter()
        with mock.installed():
            emit_cost_event(span, token_usage=override_usage)
        event = mock.filter_by_type("llm.cost.token.recorded")[0]
        assert event.payload["token_usage"]["input_tokens"] == 999

    def test_override_model_info_kwarg(self):
        span = _make_span(cost=_cost(), token_usage=_usage())
        override_model = ModelInfo(system="anthropic", name="claude-3-5-sonnet")
        mock = MockExporter()
        with mock.installed():
            emit_cost_event(span, model_info=override_model)
        event = mock.filter_by_type("llm.cost.token.recorded")[0]
        assert "claude-3-5-sonnet" in str(event.payload["model"])

    def test_agent_run_id_propagated(self):
        span = _make_span(cost=_cost(), token_usage=_usage())
        span.agent_run_id = "01HXYZ00000000AGENTRUN"
        mock = MockExporter()
        with mock.installed():
            emit_cost_event(span)
        event = mock.filter_by_type("llm.cost.token.recorded")[0]
        assert event.payload.get("agent_run_id") == "01HXYZ00000000AGENTRUN"


# ---------------------------------------------------------------------------
# emit_cost_attributed()
# ---------------------------------------------------------------------------


class TestEmitCostAttributed:
    def test_emits_attributed_event(self):
        mock = MockExporter()
        with mock.installed():
            emit_cost_attributed(
                attribution_target="team:search",
                total_usd=1.25,
                attribution_type="direct",
            )
        events = mock.filter_by_type("llm.cost.attributed")
        assert len(events) == 1

    def test_payload_attribution_target(self):
        mock = MockExporter()
        with mock.installed():
            emit_cost_attributed("org:acme", total_usd=0.50, attribution_type="manual")
        event = mock.filter_by_type("llm.cost.attributed")[0]
        assert event.payload["attribution_target"] == "org:acme"

    def test_payload_attribution_type(self):
        mock = MockExporter()
        with mock.installed():
            emit_cost_attributed("user:alice", total_usd=2.00, attribution_type="proportional")
        event = mock.filter_by_type("llm.cost.attributed")[0]
        assert event.payload["attribution_type"] == "proportional"

    def test_payload_cost_total_usd(self):
        mock = MockExporter()
        with mock.installed():
            emit_cost_attributed("team:infra", total_usd=3.14, attribution_type="estimated")
        event = mock.filter_by_type("llm.cost.attributed")[0]
        assert event.payload["cost"]["total_cost_usd"] == pytest.approx(3.14)

    def test_source_event_ids_propagated(self):
        mock = MockExporter()
        with mock.installed():
            emit_cost_attributed(
                "org:x",
                total_usd=0.10,
                attribution_type="direct",
                source_event_ids=["ev1", "ev2"],
            )
        event = mock.filter_by_type("llm.cost.attributed")[0]
        assert event.payload.get("source_event_ids") == ["ev1", "ev2"]

    def test_default_attribution_type_is_direct(self):
        mock = MockExporter()
        with mock.installed():
            emit_cost_attributed("env:prod", total_usd=0.05)
        event = mock.filter_by_type("llm.cost.attributed")[0]
        assert event.payload["attribution_type"] == "direct"

    def test_invalid_attribution_type_raises(self):
        mock = MockExporter()
        with mock.installed():
            with pytest.raises(ValueError):
                emit_cost_attributed("env:prod", total_usd=0.05, attribution_type="invalid")


# ---------------------------------------------------------------------------
# auto_emit_cost config integration
# ---------------------------------------------------------------------------


class TestAutoEmitCost:
    """Verify that setting auto_emit_cost=True causes cost events to be
    emitted automatically when a span closes with a non-None cost."""

    def setup_method(self):
        agentobs.configure(auto_emit_cost=False)  # ensure clean state

    def teardown_method(self):
        agentobs.configure(auto_emit_cost=False)

    def test_auto_emit_disabled_by_default(self):
        from agentobs.config import get_config
        assert get_config().auto_emit_cost is False

    def test_auto_emit_enabled_via_configure(self):
        agentobs.configure(auto_emit_cost=True)
        from agentobs.config import get_config
        assert get_config().auto_emit_cost is True

    def test_cost_event_emitted_on_span_close_when_enabled(self):
        agentobs.configure(auto_emit_cost=True)
        mock = MockExporter()
        tracer = agentobs.Tracer()
        with mock.installed():
            with tracer.span("my-call", model="gpt-4o") as span:
                span.set_cost(CostBreakdown(input_cost_usd=0.00250, output_cost_usd=0.0, total_cost_usd=0.00250, pricing_date="2026-03-04"))
                span.set_token_usage(
                    TokenUsage(input_tokens=500, output_tokens=200, total_tokens=700)
                )

        cost_events = mock.filter_by_type("llm.cost.token.recorded")
        assert len(cost_events) == 1
        assert cost_events[0].payload["cost"]["total_cost_usd"] == pytest.approx(0.00250)

    def test_no_cost_event_when_span_has_no_cost(self):
        agentobs.configure(auto_emit_cost=True)
        mock = MockExporter()
        tracer = agentobs.Tracer()
        with mock.installed():
            with tracer.span("no-cost-span") as span:
                span.set_attribute("foo", "bar")

        cost_events = mock.filter_by_type("llm.cost.token.recorded")
        assert len(cost_events) == 0

    def test_no_cost_event_when_auto_emit_disabled(self):
        agentobs.configure(auto_emit_cost=False)
        mock = MockExporter()
        tracer = agentobs.Tracer()
        with mock.installed():
            with tracer.span("span-with-cost", model="gpt-4o") as span:
                span.set_cost(CostBreakdown(input_cost_usd=0.001, output_cost_usd=0.0, total_cost_usd=0.001, pricing_date="2026-03-04"))

        cost_events = mock.filter_by_type("llm.cost.token.recorded")
        assert len(cost_events) == 0

    def test_span_event_still_emitted_alongside_cost_event(self):
        """Both the span event AND the cost event should be emitted."""
        agentobs.configure(auto_emit_cost=True)
        mock = MockExporter()
        tracer = agentobs.Tracer()
        with mock.installed():
            with tracer.span("dual-emit", model="gpt-4o-mini") as span:
                span.set_cost(CostBreakdown(input_cost_usd=0.0001, output_cost_usd=0.0, total_cost_usd=0.0001, pricing_date="2026-03-04"))

        span_events = mock.filter_by_type("llm.trace.span.completed")
        cost_events = mock.filter_by_type("llm.cost.token.recorded")
        assert len(span_events) == 1
        assert len(cost_events) == 1

    def test_cost_event_has_correct_span_id(self):
        agentobs.configure(auto_emit_cost=True)
        mock = MockExporter()
        tracer = agentobs.Tracer()
        with mock.installed():
            with tracer.span("span-x", model="gpt-4o") as span:
                span_id = span.span_id
                span.set_cost(CostBreakdown(input_cost_usd=0.002, output_cost_usd=0.0, total_cost_usd=0.002, pricing_date="2026-03-04"))

        cost_events = mock.filter_by_type("llm.cost.token.recorded")
        assert cost_events[0].span_id == span_id


# ---------------------------------------------------------------------------
# auto_emit_cost config fields present in AgentOBSConfig
# ---------------------------------------------------------------------------


class TestCostConfigFields:
    def test_auto_emit_cost_field_exists(self):
        from agentobs.config import AgentOBSConfig
        cfg = AgentOBSConfig()
        assert hasattr(cfg, "auto_emit_cost")
        assert cfg.auto_emit_cost is False

    def test_budget_usd_per_run_field_exists(self):
        from agentobs.config import AgentOBSConfig
        cfg = AgentOBSConfig()
        assert hasattr(cfg, "budget_usd_per_run")
        assert cfg.budget_usd_per_run is None

    def test_budget_usd_per_day_field_exists(self):
        from agentobs.config import AgentOBSConfig
        cfg = AgentOBSConfig()
        assert hasattr(cfg, "budget_usd_per_day")
        assert cfg.budget_usd_per_day is None

    def test_configure_sets_auto_emit_cost(self):
        agentobs.configure(auto_emit_cost=True)
        assert agentobs.get_config().auto_emit_cost is True
        agentobs.configure(auto_emit_cost=False)

    def test_configure_sets_budget_usd_per_run(self):
        agentobs.configure(budget_usd_per_run=5.00)
        assert agentobs.get_config().budget_usd_per_run == pytest.approx(5.00)
        agentobs.configure(budget_usd_per_run=None)

    def test_configure_sets_budget_usd_per_day(self):
        agentobs.configure(budget_usd_per_day=100.0)
        assert agentobs.get_config().budget_usd_per_day == pytest.approx(100.0)
        agentobs.configure(budget_usd_per_day=None)


# ---------------------------------------------------------------------------
# Public API surface test
# ---------------------------------------------------------------------------


class TestPublicAPI:
    def test_cost_tracker_in_agentobs_namespace(self):
        from agentobs import CostTracker
        assert CostTracker is not None

    def test_budget_monitor_in_agentobs_namespace(self):
        from agentobs import BudgetMonitor
        assert BudgetMonitor is not None

    def test_budget_alert_in_agentobs_namespace(self):
        from agentobs import budget_alert
        assert callable(budget_alert)

    def test_emit_cost_event_in_agentobs_namespace(self):
        from agentobs import emit_cost_event
        assert callable(emit_cost_event)

    def test_emit_cost_attributed_in_agentobs_namespace(self):
        from agentobs import emit_cost_attributed
        assert callable(emit_cost_attributed)

    def test_cost_summary_in_agentobs_namespace(self):
        from agentobs import cost_summary
        assert callable(cost_summary)

    def test_cost_record_in_agentobs_namespace(self):
        from agentobs import CostRecord
        assert CostRecord is not None
