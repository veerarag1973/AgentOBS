"""Retry and fallback engine — llm-retry (Tool 5).

Public API
----------
* :func:`retry` — exponential-backoff decorator (sync + async)
* :class:`FallbackChain` — ordered provider fallback list
* :class:`CircuitBreaker` — per-provider CLOSED / OPEN / HALF_OPEN state machine
* :class:`CostAwareRouter` — cost + latency budget-aware provider routing
* :class:`AllProvidersFailedError` — raised when every FallbackChain provider fails
* :class:`CircuitOpenError` — raised when CircuitBreaker is in OPEN state
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import random
import threading
import time
from enum import Enum
from typing import Any, Callable, TypeVar

__all__ = [
    "retry",
    "FallbackChain",
    "CircuitBreaker",
    "CircuitState",
    "CostAwareRouter",
    "AllProvidersFailedError",
    "CircuitOpenError",
]

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class AllProvidersFailedError(Exception):
    """Raised by :class:`FallbackChain` when every provider raises an exception.

    Attributes
    ----------
    errors:
        Ordered list of exceptions, one per provider that was tried.
    """

    def __init__(
        self,
        errors: list[BaseException],
        message: str = "All providers failed",
    ) -> None:
        self.errors = list(errors)
        super().__init__(f"{message}: {len(self.errors)} provider(s) attempted")


class CircuitOpenError(Exception):
    """Raised by :class:`CircuitBreaker` when the circuit is in OPEN state.

    Attributes
    ----------
    failure_count:
        Number of consecutive failures that tripped the circuit.
    recovery_timeout:
        Seconds until the circuit will attempt a probe (HALF_OPEN).
    """

    def __init__(self, failure_count: int, recovery_timeout: float) -> None:
        self.failure_count = failure_count
        self.recovery_timeout = recovery_timeout
        super().__init__(
            f"Circuit is OPEN after {failure_count} consecutive failure(s); "
            f"recovery in {recovery_timeout:.1f}s"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

#: Exception class names that are retried by default when ``on=None``.
_DEFAULT_RETRYABLE: frozenset[str] = frozenset(
    {
        "RateLimitError",
        "Timeout",
        "TimeoutError",
        "APIStatusError",
        "ReadTimeout",
        "ConnectTimeout",
        "ConnectionError",
        "ServiceUnavailableError",
    }
)


def _is_retryable(exc: BaseException, on_patterns: list[str] | None) -> bool:
    """Return ``True`` if *exc* should trigger a retry.

    HTTP status-code-bearing exceptions with ``status_code`` or ``status`` in
    ``{429, 500, 502, 503, 504}`` are always retried regardless of *on_patterns*.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int) and status in (429, 500, 502, 503, 504):
        return True
    exc_name = type(exc).__name__
    if on_patterns is None:
        return exc_name in _DEFAULT_RETRYABLE
    return exc_name in on_patterns


def _sleep(delay: float) -> None:
    """Thin wrapper around :func:`time.sleep` to allow test-time patching."""
    time.sleep(delay)  # pragma: no cover


def _compute_delay(
    attempt: int,
    base_delay: float,
    backoff: float,
    jitter: bool,
) -> float:
    """Compute wait time for *attempt* (0-indexed, where 0 = first retry)."""
    delay = base_delay * (backoff**attempt)
    if jitter:
        delay *= random.uniform(0.5, 1.5)
    return delay


# ---------------------------------------------------------------------------
# @retry() decorator
# ---------------------------------------------------------------------------


def retry(
    fn: F | None = None,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff: float = 2.0,
    jitter: bool = False,
    on: list[str] | None = None,
) -> F | Callable[[F], F]:
    """Exponential-backoff retry decorator for sync and async callables.

    Parameters
    ----------
    max_attempts:
        Total number of call attempts (including the first).  Must be ≥ 1.
    base_delay:
        Seconds to wait before the **first** retry.
    backoff:
        Multiplicative factor applied to *base_delay* on each subsequent
        attempt.  ``backoff=2.0`` → delays of ``1 s, 2 s, 4 s, …``
    jitter:
        When ``True``, multiply each computed delay by a uniform random
        factor in ``[0.5, 1.5]`` to spread retries across clients.
    on:
        List of exception **class names** (strings) that should trigger a
        retry.  When ``None`` the built-in :data:`_DEFAULT_RETRYABLE` set is
        used.  Exceptions with ``status_code`` in ``{429, 500, 502, 503,
        504}`` are always retried regardless of this list.

    Usage::

        @retry(max_attempts=3, base_delay=1.0, backoff=2.0)
        def call_api(prompt: str) -> str: ...

        # Bare decorator — uses all defaults
        @retry
        def call_api(prompt: str) -> str: ...
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be ≥ 1, got {max_attempts!r}")

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exc: BaseException | None = None
                for attempt in range(max_attempts):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as exc:
                        last_exc = exc
                        if not _is_retryable(exc, on):
                            raise
                        if attempt < max_attempts - 1:
                            delay = _compute_delay(attempt, base_delay, backoff, jitter)
                            await asyncio.sleep(delay)
                assert last_exc is not None
                raise last_exc

            return async_wrapper  # type: ignore[return-value]

        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exc: BaseException | None = None
                for attempt in range(max_attempts):
                    try:
                        return func(*args, **kwargs)
                    except Exception as exc:
                        last_exc = exc
                        if not _is_retryable(exc, on):
                            raise
                        if attempt < max_attempts - 1:
                            delay = _compute_delay(attempt, base_delay, backoff, jitter)
                            _sleep(delay)
                assert last_exc is not None
                raise last_exc

            return sync_wrapper  # type: ignore[return-value]

    if fn is not None:
        # Called as @retry (bare, without parentheses)
        return decorator(fn)
    return decorator


# ---------------------------------------------------------------------------
# FallbackChain
# ---------------------------------------------------------------------------


class FallbackChain:
    """Try providers in order; advance to the next on any exception.

    Parameters
    ----------
    providers:
        Ordered list of callables.  Each is invoked with the same positional
        and keyword arguments passed to the chain.

    Raises
    ------
    AllProvidersFailedError
        When every provider raises an exception.
    ValueError
        If *providers* is empty.

    Usage::

        chain = FallbackChain([call_openai, call_anthropic, call_local])
        result = chain("my prompt")

        # Async via acall():
        result = await chain.acall("my prompt")
    """

    def __init__(self, providers: list[Callable[..., Any]]) -> None:
        if not providers:
            raise ValueError("FallbackChain requires at least one provider")
        self._providers = list(providers)

    @property
    def providers(self) -> list[Callable[..., Any]]:
        """Read-only snapshot of the provider list."""
        return list(self._providers)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Try each provider in order; raise :class:`AllProvidersFailedError` if all fail."""
        errors: list[BaseException] = []
        for provider in self._providers:
            try:
                return provider(*args, **kwargs)
            except Exception as exc:
                errors.append(exc)
        raise AllProvidersFailedError(errors)

    async def acall(self, *args: Any, **kwargs: Any) -> Any:
        """Async variant — awaits providers that are coroutine functions."""
        errors: list[BaseException] = []
        for provider in self._providers:
            try:
                if inspect.iscoroutinefunction(provider):
                    return await provider(*args, **kwargs)
                return provider(*args, **kwargs)
            except Exception as exc:
                errors.append(exc)
        raise AllProvidersFailedError(errors)

    def __repr__(self) -> str:
        names = [getattr(p, "__name__", repr(p)) for p in self._providers]
        return f"FallbackChain([{', '.join(names)}])"


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class CircuitState(Enum):
    """Possible states of a :class:`CircuitBreaker`."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-provider circuit breaker with CLOSED → OPEN → HALF_OPEN state machine.

    * **CLOSED** — normal operation; all calls pass through.
    * **OPEN** — provider is considered unavailable; raises
      :class:`CircuitOpenError` immediately without invoking the function.
    * **HALF_OPEN** — recovery probe: a single call is allowed.  Success
      resets to CLOSED; failure re-opens the circuit.

    The instance is usable as a **decorator** or via the :meth:`call` method::

        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)

        @breaker
        def call_openai(prompt: str) -> str: ...

        # Or directly:
        result = breaker.call(call_openai, "my prompt")

    Parameters
    ----------
    failure_threshold:
        Number of consecutive failures required to trip the circuit OPEN.
    recovery_timeout:
        Seconds to wait in OPEN state before transitioning to HALF_OPEN and
        permitting one probe call.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """Current circuit state (thread-safe snapshot)."""
        return self._state

    @property
    def failure_count(self) -> int:
        """Current consecutive failure count."""
        return self._failure_count

    # ------------------------------------------------------------------
    # Internal state machine (must be called with self._lock held)
    # ------------------------------------------------------------------

    def _check_and_maybe_transition(self) -> None:
        """Enforce current state; may transition OPEN → HALF_OPEN.

        Raises
        ------
        CircuitOpenError
            When the circuit is OPEN and the recovery window has not elapsed.
        """
        if self._state is CircuitState.OPEN:
            elapsed = time.monotonic() - (self._last_failure_time or 0.0)
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
            else:
                raise CircuitOpenError(self._failure_count, self.recovery_timeout)

    def _record_success(self) -> None:
        """Transitions to CLOSED and resets the failure counter."""
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def _record_failure(self) -> None:
        """Increment failure count; opens circuit if threshold is met."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if (
            self._state is CircuitState.HALF_OPEN
            or self._failure_count >= self.failure_threshold
        ):
            self._state = CircuitState.OPEN

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def __call__(self, fn: F) -> F:
        """Wrap *fn* with circuit-breaker protection (decorator form)."""
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with self._lock:
                    self._check_and_maybe_transition()
                try:
                    result = await fn(*args, **kwargs)
                except Exception:
                    with self._lock:
                        self._record_failure()
                    raise
                else:
                    with self._lock:
                        self._record_success()
                    return result

            return async_wrapper  # type: ignore[return-value]

        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                with self._lock:
                    self._check_and_maybe_transition()
                try:
                    result = fn(*args, **kwargs)
                except Exception:
                    with self._lock:
                        self._record_failure()
                    raise
                else:
                    with self._lock:
                        self._record_success()
                    return result

            return sync_wrapper  # type: ignore[return-value]

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Invoke *fn* under circuit-breaker protection (non-decorator form).

        Raises
        ------
        CircuitOpenError
            When the circuit is OPEN and the recovery window has not elapsed.
        """
        with self._lock:
            self._check_and_maybe_transition()
        try:
            result = fn(*args, **kwargs)
        except Exception:
            with self._lock:
                self._record_failure()
            raise
        else:
            with self._lock:
                self._record_success()
            return result

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED state with zero failure count."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(state={self._state.value!r}, "
            f"failures={self._failure_count}/{self.failure_threshold})"
        )


# ---------------------------------------------------------------------------
# CostAwareRouter
# ---------------------------------------------------------------------------


class CostAwareRouter:
    """Select the cheapest provider that meets a latency budget.

    Parameters
    ----------
    providers:
        Mapping of ``provider_name → (cost_per_token, p95_latency_ms)``.
    latency_budget_ms:
        Maximum acceptable p95 latency in milliseconds.  Providers whose
        ``p95_latency_ms`` exceeds this value are excluded.  Defaults to
        ``float("inf")`` (no constraint).

    Raises
    ------
    AllProvidersFailedError
        When no providers fall within the latency budget.
    ValueError
        If *providers* is empty.

    Usage::

        router = CostAwareRouter(
            providers={
                "gpt-4o":         (0.0025, 800.0),
                "claude-3-haiku": (0.0003, 500.0),
                "llama-local":    (0.0,    150.0),
            },
            latency_budget_ms=600.0,
        )
        name = router.select()            # "llama-local" (cheapest, ≤600ms)
        result = router.route(fn_map, "my prompt")
    """

    def __init__(
        self,
        providers: dict[str, tuple[float, float]],
        latency_budget_ms: float = float("inf"),
    ) -> None:
        if not providers:
            raise ValueError("CostAwareRouter requires at least one provider")
        self._providers: dict[str, tuple[float, float]] = dict(providers)
        self.latency_budget_ms = latency_budget_ms

    @property
    def providers(self) -> dict[str, tuple[float, float]]:
        """Read-only snapshot of the provider table."""
        return dict(self._providers)

    def select(self) -> str:
        """Return the name of the cheapest provider within the latency budget.

        Raises
        ------
        AllProvidersFailedError
            When no provider meets the latency budget.
        """
        candidates: dict[str, float] = {
            name: cost
            for name, (cost, latency) in self._providers.items()
            if latency <= self.latency_budget_ms
        }
        if not candidates:
            raise AllProvidersFailedError(
                [],
                f"No providers within latency budget of {self.latency_budget_ms:.0f}ms",
            )
        return min(candidates, key=lambda k: candidates[k])

    def route(
        self,
        fn_map: dict[str, Callable[..., Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Select the cheapest in-budget provider and invoke its function.

        Parameters
        ----------
        fn_map:
            Mapping of ``provider_name → callable`` (same keys as *providers*).
        """
        name = self.select()
        return fn_map[name](*args, **kwargs)

    def update_latency(self, provider: str, new_p95_ms: float) -> None:
        """Update the observed p95 latency for *provider* (e.g. from live telemetry).

        Raises
        ------
        KeyError
            If *provider* is not in the routing table.
        """
        if provider not in self._providers:
            raise KeyError(f"Unknown provider: {provider!r}")
        cost, _ = self._providers[provider]
        self._providers[provider] = (cost, new_p95_ms)

    def __repr__(self) -> str:
        return (
            f"CostAwareRouter(providers={list(self._providers)!r}, "
            f"latency_budget_ms={self.latency_budget_ms})"
        )
