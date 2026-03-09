"""Tests for agentobs.cost.CostTracker and CostRecord."""

from __future__ import annotations

import pytest

from agentobs.cost import CostRecord, CostTracker, cost_summary


# ---------------------------------------------------------------------------
# CostRecord
# ---------------------------------------------------------------------------


class TestCostRecord:
    def test_basic_fields(self):
        r = CostRecord(
            model="gpt-4o",
            input_tokens=500,
            output_tokens=200,
            total_usd=0.003250,
            input_cost_usd=0.00125,
            output_cost_usd=0.00200,
        )
        assert r.model == "gpt-4o"
        assert r.input_tokens == 500
        assert r.output_tokens == 200
        assert r.total_usd == pytest.approx(0.003250)
        assert r.input_cost_usd == pytest.approx(0.00125)
        assert r.output_cost_usd == pytest.approx(0.00200)

    def test_default_tags_empty(self):
        r = CostRecord(model="gpt-4o-mini", input_tokens=100, output_tokens=50, total_usd=0.0)
        assert r.tags == {}
        assert r.span_id is None
        assert r.agent_run_id is None

    def test_to_dict_minimal(self):
        r = CostRecord(model="gpt-4o", input_tokens=10, output_tokens=5, total_usd=0.001)
        d = r.to_dict()
        assert d["model"] == "gpt-4o"
        assert d["input_tokens"] == 10
        assert d["output_tokens"] == 5
        assert d["total_usd"] == pytest.approx(0.001)
        assert "tags" not in d  # empty tags omitted
        assert "span_id" not in d
        assert "agent_run_id" not in d

    def test_to_dict_full(self):
        r = CostRecord(
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
            total_usd=0.005,
            input_cost_usd=0.0025,
            output_cost_usd=0.0025,
            tags={"env": "prod", "team": "ai"},
            span_id="abc123",
            agent_run_id="01HXYZ",
        )
        d = r.to_dict()
        assert d["input_cost_usd"] == pytest.approx(0.0025)
        assert d["output_cost_usd"] == pytest.approx(0.0025)
        assert d["tags"] == {"env": "prod", "team": "ai"}
        assert d["span_id"] == "abc123"
        assert d["agent_run_id"] == "01HXYZ"

    def test_immutable(self):
        r = CostRecord(model="gpt-4o", input_tokens=1, output_tokens=1, total_usd=0.0)
        with pytest.raises(Exception):  # frozen=True raises FrozenInstanceError
            r.model = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CostTracker — basic recording
# ---------------------------------------------------------------------------


class TestCostTrackerBasic:
    def test_empty_tracker(self):
        t = CostTracker()
        assert t.total_usd == pytest.approx(0.0)
        assert t.call_count == 0
        assert t.total_input_tokens == 0
        assert t.total_output_tokens == 0
        assert t.breakdown_by_model == {}
        assert t.breakdown_by_tag == {}
        assert t.records == []

    def test_record_returns_cost_record(self):
        t = CostTracker()
        r = t.record("gpt-4o-mini", input_tokens=1000, output_tokens=400,
                     total_usd=0.000390)
        assert isinstance(r, CostRecord)
        assert r.model == "gpt-4o-mini"
        assert r.input_tokens == 1000
        assert r.output_tokens == 400
        assert r.total_usd == pytest.approx(0.000390)

    def test_accumulates_totals(self):
        t = CostTracker()
        t.record("gpt-4o", input_tokens=500, output_tokens=200, total_usd=0.0032)
        t.record("gpt-4o", input_tokens=300, output_tokens=100, total_usd=0.0018)
        assert t.total_usd == pytest.approx(0.0050)
        assert t.call_count == 2
        assert t.total_input_tokens == 800
        assert t.total_output_tokens == 300

    def test_records_snapshot(self):
        t = CostTracker()
        t.record("gpt-4o", input_tokens=10, output_tokens=5, total_usd=0.001)
        t.record("gpt-4o-mini", input_tokens=20, output_tokens=10, total_usd=0.0005)
        records = t.records
        assert len(records) == 2
        assert records[0].model == "gpt-4o"
        assert records[1].model == "gpt-4o-mini"

    def test_record_with_tags(self):
        t = CostTracker()
        t.record("gpt-4o", input_tokens=100, output_tokens=50, total_usd=0.001,
                 tags={"env": "prod"}, span_id="abc", agent_run_id="RUN1")
        r = t.records[0]
        assert r.tags == {"env": "prod"}
        assert r.span_id == "abc"
        assert r.agent_run_id == "RUN1"


# ---------------------------------------------------------------------------
# CostTracker — pricing lookup
# ---------------------------------------------------------------------------


class TestCostTrackerPricingLookup:
    """When total_usd is not provided, tracker uses the pricing table."""

    def test_gpt4o_mini_pricing(self):
        # gpt-4o-mini: $0.15 / 1M input, $0.60 / 1M output
        t = CostTracker()
        r = t.record("gpt-4o-mini", input_tokens=1_000_000, output_tokens=1_000_000)
        assert r.input_cost_usd == pytest.approx(0.15, rel=1e-4)
        assert r.output_cost_usd == pytest.approx(0.60, rel=1e-4)
        assert r.total_usd == pytest.approx(0.75, rel=1e-4)

    def test_gpt4o_pricing(self):
        # gpt-4o: $2.50 / 1M input, $10.00 / 1M output
        t = CostTracker()
        r = t.record("gpt-4o", input_tokens=1_000_000, output_tokens=1_000_000)
        assert r.input_cost_usd == pytest.approx(2.50, rel=1e-4)
        assert r.output_cost_usd == pytest.approx(10.00, rel=1e-4)
        assert r.total_usd == pytest.approx(12.50, rel=1e-4)

    def test_unknown_model_zero_cost(self):
        # An unknown model name should return 0.0 without raising.
        t = CostTracker()
        r = t.record("unknown-model-xyz", input_tokens=500, output_tokens=200)
        assert r.total_usd == pytest.approx(0.0)
        assert r.input_cost_usd == pytest.approx(0.0)
        assert r.output_cost_usd == pytest.approx(0.0)

    def test_explicit_total_usd_bypasses_pricing(self):
        # When total_usd is provided, pricing table is NOT consulted.
        t = CostTracker()
        r = t.record("gpt-4o", input_tokens=500, output_tokens=200, total_usd=99.99)
        assert r.total_usd == pytest.approx(99.99)


# ---------------------------------------------------------------------------
# CostTracker — breakdown_by_model
# ---------------------------------------------------------------------------


class TestBreakdownByModel:
    def test_single_model(self):
        t = CostTracker()
        t.record("gpt-4o", 100, 50, total_usd=0.01)
        t.record("gpt-4o", 200, 100, total_usd=0.02)
        breakdown = t.breakdown_by_model
        assert breakdown == {"gpt-4o": pytest.approx(0.03)}

    def test_multiple_models_sorted_descending(self):
        t = CostTracker()
        t.record("gpt-4o-mini", 100, 50, total_usd=0.001)
        t.record("gpt-4o", 100, 50, total_usd=0.010)
        t.record("gpt-4-turbo", 100, 50, total_usd=0.100)
        breakdown = t.breakdown_by_model
        models = list(breakdown.keys())
        costs = list(breakdown.values())
        assert models[0] == "gpt-4-turbo"
        assert costs == sorted(costs, reverse=True)

    def test_empty_tracker_empty_breakdown(self):
        t = CostTracker()
        assert t.breakdown_by_model == {}


# ---------------------------------------------------------------------------
# CostTracker — breakdown_by_tag
# ---------------------------------------------------------------------------


class TestBreakdownByTag:
    def test_single_tag_key(self):
        t = CostTracker()
        t.record("gpt-4o", 100, 50, total_usd=0.01, tags={"env": "prod"})
        t.record("gpt-4o", 100, 50, total_usd=0.02, tags={"env": "staging"})
        t.record("gpt-4o", 100, 50, total_usd=0.03, tags={"env": "prod"})
        tag_bd = t.breakdown_by_tag
        assert tag_bd["env"]["prod"] == pytest.approx(0.04)
        assert tag_bd["env"]["staging"] == pytest.approx(0.02)

    def test_multiple_tag_keys(self):
        t = CostTracker()
        t.record("gpt-4o", 100, 50, total_usd=0.01, tags={"env": "prod", "team": "search"})
        t.record("gpt-4o", 100, 50, total_usd=0.02, tags={"env": "prod", "team": "infra"})
        tag_bd = t.breakdown_by_tag
        assert "env" in tag_bd
        assert "team" in tag_bd
        assert tag_bd["env"]["prod"] == pytest.approx(0.03)
        assert tag_bd["team"]["search"] == pytest.approx(0.01)
        assert tag_bd["team"]["infra"] == pytest.approx(0.02)

    def test_no_tags_empty_breakdown(self):
        t = CostTracker()
        t.record("gpt-4o", 100, 50, total_usd=0.01)
        assert t.breakdown_by_tag == {}


# ---------------------------------------------------------------------------
# CostTracker — reset
# ---------------------------------------------------------------------------


class TestCostTrackerReset:
    def test_reset_clears_records(self):
        t = CostTracker()
        t.record("gpt-4o", 100, 50, total_usd=0.01)
        t.record("gpt-4o-mini", 200, 100, total_usd=0.005)
        assert t.call_count == 2
        t.reset()
        assert t.call_count == 0
        assert t.total_usd == pytest.approx(0.0)
        assert t.records == []

    def test_reset_clears_breakdown(self):
        t = CostTracker()
        t.record("gpt-4o", 100, 50, total_usd=0.01, tags={"env": "prod"})
        t.reset()
        assert t.breakdown_by_model == {}
        assert t.breakdown_by_tag == {}

    def test_can_record_after_reset(self):
        t = CostTracker()
        t.record("gpt-4o", 100, 50, total_usd=0.01)
        t.reset()
        t.record("gpt-4o-mini", 200, 100, total_usd=0.005)
        assert t.call_count == 1
        assert t.total_usd == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# CostTracker — to_dict
# ---------------------------------------------------------------------------


class TestCostTrackerToDict:
    def test_to_dict_structure(self):
        t = CostTracker()
        t.record("gpt-4o", 100, 50, total_usd=0.01)
        t.record("gpt-4o-mini", 200, 100, total_usd=0.005)
        d = t.to_dict()
        assert d["total_usd"] == pytest.approx(0.015)
        assert d["call_count"] == 2
        assert d["total_input_tokens"] == 300
        assert d["total_output_tokens"] == 150
        assert "breakdown_by_model" in d
        assert "records" in d
        assert len(d["records"]) == 2

    def test_to_dict_empty(self):
        t = CostTracker()
        d = t.to_dict()
        assert d["total_usd"] == pytest.approx(0.0)
        assert d["call_count"] == 0
        assert d["records"] == []


# ---------------------------------------------------------------------------
# CostTracker — validation
# ---------------------------------------------------------------------------


class TestCostTrackerValidation:
    def test_empty_model_raises(self):
        t = CostTracker()
        with pytest.raises(ValueError, match="model must be a non-empty string"):
            t.record("", 100, 50, total_usd=0.01)

    def test_non_string_model_raises(self):
        t = CostTracker()
        with pytest.raises(ValueError):
            t.record(None, 100, 50, total_usd=0.01)  # type: ignore[arg-type]

    def test_negative_input_tokens_raises(self):
        t = CostTracker()
        with pytest.raises(ValueError, match="input_tokens"):
            t.record("gpt-4o", -1, 50, total_usd=0.01)

    def test_negative_output_tokens_raises(self):
        t = CostTracker()
        with pytest.raises(ValueError, match="output_tokens"):
            t.record("gpt-4o", 100, -1, total_usd=0.01)


# ---------------------------------------------------------------------------
# cost_summary()
# ---------------------------------------------------------------------------


class TestCostSummary:
    def test_summary_shows_totals(self):
        t = CostTracker()
        t.record("gpt-4o", 1000, 500, total_usd=0.00750)
        t.record("gpt-4o-mini", 2000, 800, total_usd=0.00078)
        output = cost_summary(t)
        assert "Total calls        : 2" in output
        assert "gpt-4o" in output
        assert "gpt-4o-mini" in output
        # Total cost in USD should appear
        assert "$" in output

    def test_summary_empty_tracker(self):
        t = CostTracker()
        output = cost_summary(t)
        assert "No calls recorded." in output

    def test_summary_includes_tag_breakdown_when_tags_present(self):
        t = CostTracker()
        t.record("gpt-4o", 100, 50, total_usd=0.01, tags={"team": "search"})
        output = cost_summary(t)
        assert "team=search" in output

    def test_summary_returns_string(self):
        t = CostTracker()
        result = cost_summary(t)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_summary_uses_global_tracker_when_none(self):
        # Just ensure it doesn't raise when tracker=None
        output = cost_summary(None)
        assert isinstance(output, str)

    def test_summary_multimodel_sorted_descending(self):
        t = CostTracker()
        t.record("gpt-4o-mini", 100, 50, total_usd=0.001)
        t.record("gpt-4o", 100, 50, total_usd=0.010)
        output = cost_summary(t)
        # gpt-4o should appear before gpt-4o-mini (higher cost)
        idx_4o = output.index("gpt-4o\n") if "gpt-4o\n" in output else output.index("gpt-4o")
        idx_mini = output.index("gpt-4o-mini")
        assert idx_4o < idx_mini
