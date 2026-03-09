"""Tests for FallbackChain — ordered provider fallback."""

from __future__ import annotations

import asyncio

import pytest

from agentobs.retry import AllProvidersFailedError, FallbackChain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(results: list, *, fail_n: int = 0):
    """Return a provider that raises ValueError for the first *fail_n* calls."""
    calls: list[tuple] = []

    def provider(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) <= fail_n:
            raise ValueError(f"Provider failed on call {len(calls)}")
        return results[len(calls) - 1] if results else "ok"

    provider.calls = calls  # type: ignore[attr-defined]
    return provider


# ---------------------------------------------------------------------------
# Sync behaviour
# ---------------------------------------------------------------------------


class TestFallbackChainSync:
    def test_first_provider_succeeds(self) -> None:
        p1 = _make_provider(["result1"])
        p2 = _make_provider(["result2"])
        chain = FallbackChain([p1, p2])
        assert chain("arg") == "result1"
        assert len(p1.calls) == 1  # type: ignore[attr-defined]
        assert len(p2.calls) == 0  # type: ignore[attr-defined]

    def test_falls_back_to_second(self) -> None:
        p1 = _make_provider([], fail_n=1)
        p2 = _make_provider(["fallback_result"])
        chain = FallbackChain([p1, p2])
        assert chain("arg") == "fallback_result"
        assert len(p1.calls) == 1  # type: ignore[attr-defined]
        assert len(p2.calls) == 1  # type: ignore[attr-defined]

    def test_falls_back_all_the_way_to_third(self) -> None:
        p1 = _make_provider([], fail_n=1)
        p2 = _make_provider([], fail_n=1)
        p3 = _make_provider(["final"])
        chain = FallbackChain([p1, p2, p3])
        assert chain() == "final"

    def test_all_fail_raises_all_providers_failed_error(self) -> None:
        p1 = _make_provider([], fail_n=999)
        p2 = _make_provider([], fail_n=999)
        chain = FallbackChain([p1, p2])
        with pytest.raises(AllProvidersFailedError):
            chain()

    def test_error_list_contains_all_exceptions(self) -> None:
        def fail_a():
            raise ValueError("a")

        def fail_b():
            raise RuntimeError("b")

        chain = FallbackChain([fail_a, fail_b])
        with pytest.raises(AllProvidersFailedError) as exc_info:
            chain()

        errs = exc_info.value.errors
        assert len(errs) == 2
        assert isinstance(errs[0], ValueError)
        assert isinstance(errs[1], RuntimeError)

    def test_passes_args_and_kwargs(self) -> None:
        received: list = []

        def capture(*args, **kwargs):
            received.append((args, kwargs))
            return "ok"

        chain = FallbackChain([capture])
        chain(1, 2, key="value")
        assert received == [((1, 2), {"key": "value"})]

    def test_single_provider_success(self) -> None:
        chain = FallbackChain([lambda: 42])
        assert chain() == 42

    def test_single_provider_fail_raises(self) -> None:
        chain = FallbackChain([lambda: (_ for _ in ()).throw(RuntimeError("fail"))])
        with pytest.raises(AllProvidersFailedError):
            chain()


# ---------------------------------------------------------------------------
# Async behaviour
# ---------------------------------------------------------------------------


class TestFallbackChainAsync:
    def test_async_first_provider_succeeds(self) -> None:
        async def p1():
            return "async_result"

        async def p2():
            return "fallback"

        chain = FallbackChain([p1, p2])

        async def run():
            return await chain.acall()

        assert asyncio.run(run()) == "async_result"

    def test_async_falls_back_on_exception(self) -> None:
        async def p1():
            raise ValueError("p1 failed")

        async def p2():
            return "p2_result"

        chain = FallbackChain([p1, p2])

        async def run():
            return await chain.acall()

        assert asyncio.run(run()) == "p2_result"

    def test_async_all_fail_raises(self) -> None:
        async def p1():
            raise ValueError("p1")

        async def p2():
            raise RuntimeError("p2")

        chain = FallbackChain([p1, p2])

        async def run():
            await chain.acall()

        with pytest.raises(AllProvidersFailedError):
            asyncio.run(run())

    def test_async_mixes_sync_and_async_providers(self) -> None:
        async def p1():
            raise ValueError("async fail")

        def p2():
            return "sync_fallback"

        chain = FallbackChain([p1, p2])

        async def run():
            return await chain.acall()

        assert asyncio.run(run()) == "sync_fallback"

    def test_async_passes_args(self) -> None:
        received: list = []

        async def capture(*args, **kwargs):
            received.append((args, kwargs))
            return "ok"

        chain = FallbackChain([capture])

        async def run():
            return await chain.acall(1, key="v")

        asyncio.run(run())
        assert received == [((1,), {"key": "v"})]


# ---------------------------------------------------------------------------
# Construction and repr
# ---------------------------------------------------------------------------


class TestFallbackChainConstruction:
    def test_empty_providers_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            FallbackChain([])

    def test_providers_property_is_copy(self) -> None:
        p = lambda: "ok"  # noqa: E731
        chain = FallbackChain([p])
        snap = chain.providers
        snap.append(lambda: "injected")
        assert len(chain.providers) == 1

    def test_repr_contains_provider_names(self) -> None:
        def provider_a():
            return "a"

        def provider_b():
            return "b"

        chain = FallbackChain([provider_a, provider_b])
        r = repr(chain)
        assert "FallbackChain" in r
        assert "provider_a" in r
        assert "provider_b" in r
