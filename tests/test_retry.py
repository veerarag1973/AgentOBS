"""Tests for agentobs.retry — @retry() decorator and CostAwareRouter."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from agentobs.retry import (
    AllProvidersFailedError,
    CostAwareRouter,
    _compute_delay,
    _is_retryable,
    retry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class RateLimitError(Exception):
    """Simulates a provider RateLimitError."""


class Timeout(Exception):
    """Simulates a Timeout error (matches 'Timeout' in _DEFAULT_RETRYABLE)."""


class _APIStatusError(Exception):
    """Simulates an HTTP-status-bearing exception."""

    def __init__(self, msg: str, status_code: int) -> None:
        super().__init__(msg)
        self.status_code = status_code


class _NonRetryableError(Exception):
    """An exception that should NOT be retried by default."""


# ---------------------------------------------------------------------------
# Tests for _is_retryable
# ---------------------------------------------------------------------------


class TestIsRetryable:
    def test_rate_limit_error_retried_by_default(self) -> None:
        assert _is_retryable(RateLimitError("limit"), None)

    def test_timeout_error_retried_by_default(self) -> None:
        assert _is_retryable(Timeout("timeout"), None)

    def test_non_retryable_not_retried_by_default(self) -> None:
        assert not _is_retryable(_NonRetryableError("nope"), None)

    def test_http_429_always_retried(self) -> None:
        assert _is_retryable(_APIStatusError("rate limit", 429), None)

    def test_http_500_always_retried(self) -> None:
        assert _is_retryable(_APIStatusError("server error", 500), None)

    def test_http_503_always_retried(self) -> None:
        assert _is_retryable(_APIStatusError("unavailable", 503), None)

    def test_http_400_not_retried(self) -> None:
        assert not _is_retryable(_APIStatusError("bad request", 400), None)

    def test_custom_on_patterns_match(self) -> None:
        assert _is_retryable(_NonRetryableError("err"), ["_NonRetryableError"])

    def test_custom_on_patterns_no_match(self) -> None:
        assert not _is_retryable(RateLimitError("limit"), ["SomeOtherError"])

    def test_status_attribute_overrides_on_patterns(self) -> None:
        # status_code=429 always retried, even when on=["SomeOtherError"]
        assert _is_retryable(_APIStatusError("rate", 429), ["SomeOtherError"])


# ---------------------------------------------------------------------------
# Tests for _compute_delay
# ---------------------------------------------------------------------------


class TestComputeDelay:
    def test_first_attempt_base_delay(self) -> None:
        assert _compute_delay(0, 1.0, 2.0, False) == pytest.approx(1.0)

    def test_exponential_backoff(self) -> None:
        assert _compute_delay(1, 1.0, 2.0, False) == pytest.approx(2.0)
        assert _compute_delay(2, 1.0, 2.0, False) == pytest.approx(4.0)
        assert _compute_delay(3, 1.0, 2.0, False) == pytest.approx(8.0)

    def test_custom_base_delay(self) -> None:
        assert _compute_delay(0, 0.5, 2.0, False) == pytest.approx(0.5)
        assert _compute_delay(1, 0.5, 2.0, False) == pytest.approx(1.0)

    def test_jitter_within_bounds(self) -> None:
        for _ in range(50):
            delay = _compute_delay(0, 1.0, 2.0, True)
            assert 0.5 <= delay <= 1.5


# ---------------------------------------------------------------------------
# Tests for @retry() decorator — sync
# ---------------------------------------------------------------------------


class TestRetrySync:
    def test_success_on_first_attempt(self) -> None:
        calls = []

        @retry(max_attempts=3, base_delay=0.0)
        def fn() -> str:
            calls.append(1)
            return "ok"

        assert fn() == "ok"
        assert len(calls) == 1

    def test_success_on_third_attempt(self) -> None:
        calls = []

        with patch("agentobs.retry._sleep"):

            @retry(max_attempts=3, base_delay=0.01, on=["RateLimitError"])
            def fn() -> str:
                calls.append(1)
                if len(calls) < 3:
                    raise RateLimitError("limit")
                return "ok"

            assert fn() == "ok"
            assert len(calls) == 3

    def test_raises_after_max_attempts_exhausted(self) -> None:
        with patch("agentobs.retry._sleep"):

            @retry(max_attempts=3, base_delay=0.01, on=["RateLimitError"])
            def fn() -> str:
                raise RateLimitError("limit")

            with pytest.raises(RateLimitError):
                fn()

    def test_non_retryable_raises_immediately(self) -> None:
        calls = []

        @retry(max_attempts=5, base_delay=0.0)
        def fn() -> str:
            calls.append(1)
            raise _NonRetryableError("nope")

        with pytest.raises(_NonRetryableError):
            fn()
        assert len(calls) == 1

    def test_backoff_timing_delays(self) -> None:
        sleep_calls: list[float] = []

        with patch("agentobs.retry._sleep", side_effect=lambda d: sleep_calls.append(d)):

            @retry(max_attempts=3, base_delay=1.0, backoff=2.0, on=["RateLimitError"])
            def fn() -> None:
                raise RateLimitError("limit")

            with pytest.raises(RateLimitError):
                fn()

        assert len(sleep_calls) == 2  # 3 attempts → 2 sleeps
        assert sleep_calls[0] == pytest.approx(1.0)  # attempt 0
        assert sleep_calls[1] == pytest.approx(2.0)  # attempt 1

    def test_no_sleep_on_final_attempt(self) -> None:
        sleep_calls: list[float] = []

        with patch("agentobs.retry._sleep", side_effect=lambda d: sleep_calls.append(d)):

            @retry(max_attempts=2, base_delay=1.0, on=["RateLimitError"])
            def fn() -> None:
                raise RateLimitError("limit")

            with pytest.raises(RateLimitError):
                fn()

        assert len(sleep_calls) == 1

    def test_http_429_retried_automatically(self) -> None:
        calls = []

        with patch("agentobs.retry._sleep"):

            @retry(max_attempts=2, base_delay=0.01)
            def fn() -> str:
                calls.append(1)
                if len(calls) < 2:
                    raise _APIStatusError("rate limit", 429)
                return "ok"

            assert fn() == "ok"
            assert len(calls) == 2

    def test_custom_on_patterns(self) -> None:
        calls = []

        with patch("agentobs.retry._sleep"):

            @retry(max_attempts=3, base_delay=0.01, on=["_NonRetryableError"])
            def fn() -> str:
                calls.append(1)
                if len(calls) < 3:
                    raise _NonRetryableError("custom")
                return "done"

            assert fn() == "done"
            assert len(calls) == 3

    def test_without_parentheses(self) -> None:
        """@retry (bare) should use all defaults."""

        @retry
        def fn() -> str:
            return "bare"

        assert fn() == "bare"

    def test_invalid_max_attempts_raises(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            retry(max_attempts=0)

    def test_functools_wraps_preserves_name(self) -> None:
        @retry(max_attempts=1)
        def my_function() -> None:
            pass

        assert my_function.__name__ == "my_function"

    def test_attempt_count_matches_max_attempts(self) -> None:
        calls = []

        with patch("agentobs.retry._sleep"):

            @retry(max_attempts=4, base_delay=0.01, on=["RateLimitError"])
            def fn() -> None:
                calls.append(1)
                raise RateLimitError("limit")

            with pytest.raises(RateLimitError):
                fn()

        assert len(calls) == 4

    def test_raises_original_exception_not_wrapped(self) -> None:
        original = RateLimitError("original message")

        with patch("agentobs.retry._sleep"):

            @retry(max_attempts=2, base_delay=0.01, on=["RateLimitError"])
            def fn() -> None:
                raise original

            with pytest.raises(RateLimitError) as exc_info:
                fn()

        assert exc_info.value is original


# ---------------------------------------------------------------------------
# Tests for @retry() decorator — async
# ---------------------------------------------------------------------------


class TestRetryAsync:
    def test_async_success_on_first_attempt(self) -> None:
        calls = []

        @retry(max_attempts=3, base_delay=0.0)
        async def fn() -> str:
            calls.append(1)
            return "ok"

        assert asyncio.run(fn()) == "ok"
        assert len(calls) == 1

    def test_async_success_on_third_attempt(self) -> None:
        calls = []

        async def run() -> str:
            with patch("asyncio.sleep"):

                @retry(max_attempts=3, base_delay=0.01, on=["RateLimitError"])
                async def fn() -> str:
                    calls.append(1)
                    if len(calls) < 3:
                        raise RateLimitError("limit")
                    return "async_ok"

                return await fn()

        assert asyncio.run(run()) == "async_ok"
        assert len(calls) == 3

    def test_async_raises_after_max_attempts(self) -> None:
        async def run() -> None:
            with patch("asyncio.sleep"):

                @retry(max_attempts=2, base_delay=0.01, on=["RateLimitError"])
                async def fn() -> None:
                    raise RateLimitError("limit")

                await fn()

        with pytest.raises(RateLimitError):
            asyncio.run(run())

    def test_async_non_retryable_raises_immediately(self) -> None:
        calls = []

        @retry(max_attempts=5, base_delay=0.0)
        async def fn() -> None:
            calls.append(1)
            raise _NonRetryableError("nope")

        with pytest.raises(_NonRetryableError):
            asyncio.run(fn())
        assert len(calls) == 1

    def test_async_functools_wraps(self) -> None:
        @retry(max_attempts=1)
        async def my_async_fn() -> None:
            pass

        assert my_async_fn.__name__ == "my_async_fn"


# ---------------------------------------------------------------------------
# Tests for CostAwareRouter
# ---------------------------------------------------------------------------


class TestCostAwareRouter:
    _PROVIDERS: dict[str, tuple[float, float]] = {
        "gpt-4o": (0.0025, 800.0),
        "claude-3-haiku": (0.0003, 500.0),
        "llama-local": (0.0, 150.0),
    }

    def test_selects_cheapest_within_budget(self) -> None:
        router = CostAwareRouter(self._PROVIDERS, latency_budget_ms=600.0)
        # gpt-4o excluded (800ms > 600ms); cheapest of remaining = llama-local (free)
        assert router.select() == "llama-local"

    def test_selects_cheapest_with_no_budget_constraint(self) -> None:
        router = CostAwareRouter(self._PROVIDERS)  # latency_budget_ms=inf
        assert router.select() == "llama-local"

    def test_selects_within_tight_budget(self) -> None:
        router = CostAwareRouter(self._PROVIDERS, latency_budget_ms=200.0)
        # Only llama-local (150ms) fits
        assert router.select() == "llama-local"

    def test_no_providers_within_budget_raises(self) -> None:
        router = CostAwareRouter(self._PROVIDERS, latency_budget_ms=100.0)
        with pytest.raises(AllProvidersFailedError, match="latency budget"):
            router.select()

    def test_route_calls_selected_provider(self) -> None:
        router = CostAwareRouter(self._PROVIDERS, latency_budget_ms=200.0)
        mock = MagicMock(return_value="result")
        fn_map = {"llama-local": mock, "gpt-4o": MagicMock(), "claude-3-haiku": MagicMock()}
        result = router.route(fn_map, "my prompt")
        assert result == "result"
        mock.assert_called_once_with("my prompt")

    def test_update_latency_changes_routing(self) -> None:
        router = CostAwareRouter(self._PROVIDERS, latency_budget_ms=600.0)
        router.update_latency("llama-local", 700.0)  # now exceeds budget
        # llama-local excluded; cheapest remaining = claude-3-haiku
        assert router.select() == "claude-3-haiku"

    def test_update_latency_unknown_provider_raises(self) -> None:
        router = CostAwareRouter(self._PROVIDERS)
        with pytest.raises(KeyError, match="unknown_provider"):
            router.update_latency("unknown_provider", 100.0)

    def test_empty_providers_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            CostAwareRouter({})

    def test_providers_property_is_copy(self) -> None:
        router = CostAwareRouter(self._PROVIDERS)
        snap = router.providers
        snap["injected"] = (0.0, 0.0)
        assert "injected" not in router.providers

    def test_repr(self) -> None:
        router = CostAwareRouter({"provider-a": (0.001, 100.0)}, latency_budget_ms=200.0)
        r = repr(router)
        assert "CostAwareRouter" in r
        assert "200.0" in r


# ---------------------------------------------------------------------------
# Tests for AllProvidersFailedError
# ---------------------------------------------------------------------------


class TestAllProvidersFailedError:
    def test_stores_errors_list(self) -> None:
        e1 = ValueError("p1 failed")
        e2 = RuntimeError("p2 failed")
        err = AllProvidersFailedError([e1, e2])
        assert err.errors == [e1, e2]

    def test_str_contains_count(self) -> None:
        err = AllProvidersFailedError([ValueError(), RuntimeError()])
        assert "2" in str(err)

    def test_custom_message(self) -> None:
        err = AllProvidersFailedError([], message="Custom failure")
        assert "Custom failure" in str(err)

    def test_is_exception(self) -> None:
        assert isinstance(AllProvidersFailedError([]), Exception)


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


class TestPublicAPI:
    def test_retry_importable_from_agentobs(self) -> None:
        import agentobs

        assert hasattr(agentobs, "retry")

    def test_fallback_chain_importable_from_agentobs(self) -> None:
        import agentobs

        assert hasattr(agentobs, "FallbackChain")

    def test_circuit_breaker_importable_from_agentobs(self) -> None:
        import agentobs

        assert hasattr(agentobs, "CircuitBreaker")

    def test_cost_aware_router_importable_from_agentobs(self) -> None:
        import agentobs

        assert hasattr(agentobs, "CostAwareRouter")

    def test_all_providers_failed_importable(self) -> None:
        import agentobs

        assert hasattr(agentobs, "AllProvidersFailedError")

    def test_circuit_open_error_importable(self) -> None:
        import agentobs

        assert hasattr(agentobs, "CircuitOpenError")

    def test_all_in___all__(self) -> None:
        from agentobs.retry import __all__ as retry_all

        for name in [
            "retry",
            "FallbackChain",
            "CircuitBreaker",
            "CircuitState",
            "CostAwareRouter",
            "AllProvidersFailedError",
            "CircuitOpenError",
        ]:
            assert name in retry_all, f"{name!r} missing from retry.__all__"
