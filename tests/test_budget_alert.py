"""Tests for agentobs.cost.BudgetMonitor and budget_alert()."""

from __future__ import annotations

import pytest

from agentobs.cost import BudgetMonitor, CostTracker, budget_alert


# ---------------------------------------------------------------------------
# BudgetMonitor — constructor validation
# ---------------------------------------------------------------------------


class TestBudgetMonitorValidation:
    def test_zero_threshold_raises(self):
        with pytest.raises(ValueError, match="threshold_usd must be > 0"):
            BudgetMonitor(threshold_usd=0.0, on_exceeded=lambda t: None)

    def test_negative_threshold_raises(self):
        with pytest.raises(ValueError, match="threshold_usd must be > 0"):
            BudgetMonitor(threshold_usd=-1.0, on_exceeded=lambda t: None)

    def test_non_callable_on_exceeded_raises(self):
        with pytest.raises(TypeError, match="on_exceeded must be callable"):
            BudgetMonitor(threshold_usd=1.0, on_exceeded="not-callable")  # type: ignore[arg-type]

    def test_valid_monitor_constructed(self):
        m = BudgetMonitor(threshold_usd=5.00, on_exceeded=lambda t: None)
        assert m.threshold_usd == pytest.approx(5.00)
        assert m._fired is False


# ---------------------------------------------------------------------------
# BudgetMonitor — check() behaviour
# ---------------------------------------------------------------------------


class TestBudgetMonitorCheck:
    def test_fires_when_threshold_reached(self):
        fired = []
        m = BudgetMonitor(
            threshold_usd=0.50,
            on_exceeded=lambda t: fired.append(t.total_usd),
        )
        t = CostTracker()
        t._add_monitor(m)
        t.record("gpt-4o", 100, 50, total_usd=0.49)
        assert fired == []
        t.record("gpt-4o", 100, 50, total_usd=0.02)  # pushes over 0.50
        assert len(fired) == 1

    def test_does_not_fire_twice(self):
        fired = []
        m = BudgetMonitor(
            threshold_usd=0.50,
            on_exceeded=lambda t: fired.append(True),
        )
        t = CostTracker()
        t._add_monitor(m)
        t.record("gpt-4o", 100, 50, total_usd=0.60)
        t.record("gpt-4o", 100, 50, total_usd=0.10)
        t.record("gpt-4o", 100, 50, total_usd=0.20)
        # Callback should have fired exactly once
        assert len(fired) == 1

    def test_fires_again_after_reset(self):
        fired = []
        m = BudgetMonitor(
            threshold_usd=0.50,
            on_exceeded=lambda t: fired.append(True),
        )
        t = CostTracker()
        t._add_monitor(m)
        t.record("gpt-4o", 100, 50, total_usd=0.60)
        assert len(fired) == 1
        t.reset()  # resets _fired state on monitor
        t.record("gpt-4o", 100, 50, total_usd=0.60)
        assert len(fired) == 2

    def test_callback_receives_tracker(self):
        trackers_seen = []
        t = CostTracker()
        m = BudgetMonitor(
            threshold_usd=0.10,
            on_exceeded=lambda tracker: trackers_seen.append(tracker),
        )
        t._add_monitor(m)
        t.record("gpt-4o", 100, 50, total_usd=0.15)
        assert len(trackers_seen) == 1
        assert trackers_seen[0] is t

    def test_check_returns_true_on_fire(self):
        t = CostTracker()
        t.record("gpt-4o", 100, 50, total_usd=0.20)
        m = BudgetMonitor(threshold_usd=0.10, on_exceeded=lambda _: None)
        result = m.check(t)
        assert result is True

    def test_check_returns_false_when_not_exceeded(self):
        t = CostTracker()
        t.record("gpt-4o", 100, 50, total_usd=0.05)
        m = BudgetMonitor(threshold_usd=1.00, on_exceeded=lambda _: None)
        result = m.check(t)
        assert result is False

    def test_check_returns_false_after_already_fired(self):
        fired = []
        m = BudgetMonitor(threshold_usd=0.10, on_exceeded=lambda _: fired.append(1))
        t = CostTracker()
        t._add_monitor(m)
        t.record("gpt-4o", 100, 50, total_usd=0.20)  # fires
        # Directly calling check again should return False (already fired)
        result = m.check(t)
        assert result is False
        assert len(fired) == 1  # still only fired once

    def test_callback_exception_does_not_propagate(self):
        """Callback errors must never break the recording path."""
        def bad_callback(t):
            raise RuntimeError("callback error!")

        tracker = CostTracker()
        m = BudgetMonitor(threshold_usd=0.01, on_exceeded=bad_callback)
        tracker._add_monitor(m)
        # Should not raise
        tracker.record("gpt-4o", 100, 50, total_usd=0.05)

    def test_exact_threshold_fires(self):
        fired = []
        m = BudgetMonitor(threshold_usd=0.50, on_exceeded=lambda _: fired.append(1))
        t = CostTracker()
        t._add_monitor(m)
        t.record("gpt-4o", 100, 50, total_usd=0.50)  # exactly at threshold
        assert len(fired) == 1


# ---------------------------------------------------------------------------
# budget_alert() factory
# ---------------------------------------------------------------------------


class TestBudgetAlert:
    def test_creates_and_attaches_monitor(self):
        t = CostTracker()
        fired = []
        monitor = budget_alert(
            0.25,
            on_exceeded=lambda tracker: fired.append(tracker.total_usd),
            tracker=t,
        )
        assert isinstance(monitor, BudgetMonitor)
        assert monitor.threshold_usd == pytest.approx(0.25)
        assert monitor in t._monitors

    def test_fires_when_threshold_exceeded(self):
        t = CostTracker()
        fired = []
        budget_alert(0.25, on_exceeded=lambda _: fired.append(True), tracker=t)
        t.record("gpt-4o", 100, 50, total_usd=0.30)
        assert len(fired) == 1

    def test_uses_global_tracker_when_none(self):
        """budget_alert() with tracker=None uses the module-level global tracker."""
        from agentobs.cost import _get_global_tracker
        global_tracker = _get_global_tracker()
        initial_monitors = len(global_tracker._monitors)
        budget_alert(9999.99, on_exceeded=lambda _: None)  # high threshold — won't fire
        assert len(global_tracker._monitors) == initial_monitors + 1

    def test_multiple_monitors_on_same_tracker(self):
        t = CostTracker()
        fired_low = []
        fired_high = []
        budget_alert(0.10, on_exceeded=lambda _: fired_low.append(1), tracker=t)
        budget_alert(0.50, on_exceeded=lambda _: fired_high.append(1), tracker=t)
        t.record("gpt-4o", 100, 50, total_usd=0.20)
        assert len(fired_low) == 1
        assert len(fired_high) == 0
        t.record("gpt-4o", 100, 50, total_usd=0.35)
        assert len(fired_low) == 1  # already fired
        assert len(fired_high) == 1

    def test_returns_monitor(self):
        t = CostTracker()
        result = budget_alert(1.00, on_exceeded=lambda _: None, tracker=t)
        assert isinstance(result, BudgetMonitor)

    def test_monitor_not_fired_before_threshold(self):
        t = CostTracker()
        fired = []
        budget_alert(1.00, on_exceeded=lambda _: fired.append(True), tracker=t)
        t.record("gpt-4o", 100, 50, total_usd=0.99)
        assert fired == []  # not yet exceeded

    def test_budget_alert_fires_on_next_record_after_threshold(self):
        t = CostTracker()
        fired = []
        budget_alert(0.05, on_exceeded=lambda _: fired.append(True), tracker=t)
        t.record("gpt-4o", 100, 50, total_usd=0.04)  # under
        assert fired == []
        t.record("gpt-4o", 100, 50, total_usd=0.02)  # over
        assert fired == [True]
