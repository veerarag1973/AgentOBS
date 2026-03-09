"""Tests for CircuitBreaker — CLOSED / OPEN / HALF_OPEN state machine."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from agentobs.retry import CircuitBreaker, CircuitOpenError, CircuitState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trip_breaker(breaker: CircuitBreaker, n: int | None = None) -> None:
    """Call breaker with a failing function *n* times (defaults to failure_threshold)."""
    count = n if n is not None else breaker.failure_threshold
    for _ in range(count):
        try:
            breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


class TestCircuitBreakerInitial:
    def test_initial_state_is_closed(self) -> None:
        breaker = CircuitBreaker()
        assert breaker.state is CircuitState.CLOSED

    def test_initial_failure_count_is_zero(self) -> None:
        breaker = CircuitBreaker()
        assert breaker.failure_count == 0

    def test_repr_shows_closed(self) -> None:
        breaker = CircuitBreaker()
        r = repr(breaker)
        assert "closed" in r
        assert "0/" in r


# ---------------------------------------------------------------------------
# CLOSED state — normal operation
# ---------------------------------------------------------------------------


class TestCircuitBreakerClosed:
    def test_allows_successful_calls(self) -> None:
        breaker = CircuitBreaker()
        result = breaker.call(lambda: "ok")
        assert result == "ok"

    def test_success_keeps_state_closed(self) -> None:
        breaker = CircuitBreaker()
        breaker.call(lambda: "ok")
        assert breaker.state is CircuitState.CLOSED

    def test_failure_increments_count(self) -> None:
        breaker = CircuitBreaker(failure_threshold=5)
        try:
            breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        except RuntimeError:
            pass
        assert breaker.failure_count == 1

    def test_failure_below_threshold_stays_closed(self) -> None:
        breaker = CircuitBreaker(failure_threshold=3)
        for _ in range(2):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
            except RuntimeError:
                pass
        assert breaker.state is CircuitState.CLOSED

    def test_exception_propagates(self) -> None:
        breaker = CircuitBreaker()
        with pytest.raises(ValueError, match="propagated"):
            breaker.call(lambda: (_ for _ in ()).throw(ValueError("propagated")))


# ---------------------------------------------------------------------------
# Transition CLOSED → OPEN
# ---------------------------------------------------------------------------


class TestCircuitBreakerTransitionToOpen:
    def test_opens_after_failure_threshold(self) -> None:
        breaker = CircuitBreaker(failure_threshold=3)
        _trip_breaker(breaker)
        assert breaker.state is CircuitState.OPEN

    def test_failure_count_equals_threshold_when_open(self) -> None:
        breaker = CircuitBreaker(failure_threshold=3)
        _trip_breaker(breaker)
        assert breaker.failure_count == 3

    def test_open_raises_circuit_open_error(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1)
        _trip_breaker(breaker, 1)
        with pytest.raises(CircuitOpenError):
            breaker.call(lambda: "should not be called")

    def test_circuit_open_error_attributes(self) -> None:
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        _trip_breaker(breaker, 2)
        with pytest.raises(CircuitOpenError) as exc_info:
            breaker.call(lambda: "blocked")
        err = exc_info.value
        assert err.failure_count == 2
        assert err.recovery_timeout == pytest.approx(30.0)

    def test_open_state_does_not_invoke_function(self) -> None:
        calls = []
        breaker = CircuitBreaker(failure_threshold=1)
        _trip_breaker(breaker, 1)
        with pytest.raises(CircuitOpenError):
            breaker.call(lambda: calls.append(1) or "ok")
        assert calls == []


# ---------------------------------------------------------------------------
# Transition OPEN → HALF_OPEN
# ---------------------------------------------------------------------------


class TestCircuitBreakerHalfOpen:
    def test_transitions_to_half_open_after_recovery_timeout(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)

        with patch("agentobs.retry.time") as mock_time:
            mock_time.monotonic.return_value = 100.0  # time at failure
            _trip_breaker(breaker, 1)
            assert breaker.state is CircuitState.OPEN

            # Advance past recovery window
            mock_time.monotonic.return_value = 115.0  # 15s later > 10s
            result = breaker.call(lambda: "probe_ok")

        assert result == "probe_ok"
        assert breaker.state is CircuitState.CLOSED

    def test_half_open_probe_success_closes_circuit(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=5.0)

        with patch("agentobs.retry.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            _trip_breaker(breaker, 1)

            mock_time.monotonic.return_value = 10.0  # > 5s recovery
            breaker.call(lambda: "ok")

        assert breaker.state is CircuitState.CLOSED
        assert breaker.failure_count == 0

    def test_half_open_probe_failure_reopens_circuit(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=5.0)

        with patch("agentobs.retry.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            _trip_breaker(breaker, 1)
            # Advance into HALF_OPEN window
            mock_time.monotonic.return_value = 10.0
            # Probe fails
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("probe fail")))
            except RuntimeError:
                pass

        assert breaker.state is CircuitState.OPEN

    def test_open_within_recovery_timeout_still_raises(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)

        with patch("agentobs.retry.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            _trip_breaker(breaker, 1)
            # Only 5 seconds elapsed — not yet in recovery window
            mock_time.monotonic.return_value = 5.0
            with pytest.raises(CircuitOpenError):
                breaker.call(lambda: "blocked")


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestCircuitBreakerReset:
    def test_reset_clears_state_to_closed(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1)
        _trip_breaker(breaker, 1)
        assert breaker.state is CircuitState.OPEN
        breaker.reset()
        assert breaker.state is CircuitState.CLOSED

    def test_reset_clears_failure_count(self) -> None:
        breaker = CircuitBreaker(failure_threshold=3)
        _trip_breaker(breaker, 2)
        breaker.reset()
        assert breaker.failure_count == 0

    def test_reset_allows_calls_again(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1)
        _trip_breaker(breaker, 1)
        breaker.reset()
        result = breaker.call(lambda: "after_reset")
        assert result == "after_reset"

    def test_success_before_threshold_resets_count(self) -> None:
        breaker = CircuitBreaker(failure_threshold=5)
        _trip_breaker(breaker, 3)
        assert breaker.failure_count == 3
        breaker.call(lambda: "ok")
        assert breaker.failure_count == 0
        assert breaker.state is CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Decorator form
# ---------------------------------------------------------------------------


class TestCircuitBreakerDecorator:
    def test_sync_decorator_allows_call(self) -> None:
        breaker = CircuitBreaker()

        @breaker
        def fn():
            return "decorated"

        assert fn() == "decorated"

    def test_sync_decorator_trips_on_failures(self) -> None:
        breaker = CircuitBreaker(failure_threshold=2)

        @breaker
        def fn():
            raise ValueError("fail")

        for _ in range(2):
            with pytest.raises(ValueError):
                fn()

        assert breaker.state is CircuitState.OPEN

    def test_sync_decorator_raises_circuit_open_when_tripped(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1)

        @breaker
        def fail_fn():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            fail_fn()

        @breaker
        def ok_fn():
            return "ok"

        with pytest.raises(CircuitOpenError):
            ok_fn()

    def test_async_decorator_allows_call(self) -> None:
        breaker = CircuitBreaker()

        @breaker
        async def fn():
            return "async_decorated"

        assert asyncio.run(fn()) == "async_decorated"

    def test_async_decorator_trips_on_failures(self) -> None:
        breaker = CircuitBreaker(failure_threshold=2)

        @breaker
        async def fn():
            raise ValueError("async fail")

        async def run():
            for _ in range(2):
                try:
                    await fn()
                except ValueError:
                    pass

        asyncio.run(run())
        assert breaker.state is CircuitState.OPEN

    def test_decorator_preserves_function_name(self) -> None:
        breaker = CircuitBreaker()

        @breaker
        def my_special_fn():
            return "ok"

        assert my_special_fn.__name__ == "my_special_fn"


# ---------------------------------------------------------------------------
# CircuitOpenError
# ---------------------------------------------------------------------------


class TestCircuitOpenError:
    def test_attributes(self) -> None:
        err = CircuitOpenError(failure_count=7, recovery_timeout=45.0)
        assert err.failure_count == 7
        assert err.recovery_timeout == pytest.approx(45.0)

    def test_str_contains_failure_count(self) -> None:
        err = CircuitOpenError(failure_count=3, recovery_timeout=30.0)
        assert "3" in str(err)

    def test_is_exception(self) -> None:
        assert isinstance(CircuitOpenError(1, 1.0), Exception)
