"""Tests for agentobs.testing.captured_spans fixture and assert_span_emitted.

The captured_spans fixture is imported here so pytest picks it up as a
plugin-level fixture for this module.
"""

from __future__ import annotations

import pytest

# Re-export the fixture so pytest can inject it into test functions in this module.
from agentobs.testing import captured_spans, assert_span_emitted  # noqa: F401
from agentobs import tracer
from agentobs.trace import trace


# ---------------------------------------------------------------------------
# captured_spans fixture — basic behaviour
# ---------------------------------------------------------------------------


def test_captured_spans_starts_empty(captured_spans: list) -> None:
    assert captured_spans == []


def test_captured_spans_captures_one_span(captured_spans: list) -> None:
    with tracer.span("fixture-span"):
        pass

    assert len(captured_spans) == 1
    assert captured_spans[0].name == "fixture-span"


def test_captured_spans_captures_multiple_spans(captured_spans: list) -> None:
    with tracer.span("span-a"):
        pass
    with tracer.span("span-b"):
        pass

    names = [s.name for s in captured_spans]
    assert "span-a" in names
    assert "span-b" in names


def test_captured_spans_captures_decorator_spans(captured_spans: list) -> None:
    @trace(name="deco-captured")
    def fn() -> None:
        pass

    fn()
    assert any(s.name == "deco-captured" for s in captured_spans)


def test_captured_spans_accumulates_in_order(captured_spans: list) -> None:
    with tracer.span("first"):
        pass
    with tracer.span("second"):
        pass
    with tracer.span("third"):
        pass

    assert [s.name for s in captured_spans] == ["first", "second", "third"]


def test_captured_spans_captures_nested_spans(captured_spans: list) -> None:
    with tracer.span("outer"):
        with tracer.span("inner"):
            pass

    names = [s.name for s in captured_spans]
    assert "inner" in names
    assert "outer" in names


def test_captured_spans_includes_span_attributes(captured_spans: list) -> None:
    with tracer.span("attr-span", model="gpt-4o") as span:
        span.set_attribute("custom_key", "custom_val")

    assert len(captured_spans) == 1
    s = captured_spans[0]
    assert s.model == "gpt-4o"
    assert s.attributes.get("custom_key") == "custom_val"


def test_captured_spans_captures_error_spans(captured_spans: list) -> None:
    @trace(name="err-captured")
    def boom() -> None:
        raise RuntimeError("test error")

    with pytest.raises(RuntimeError):
        boom()

    s = next(x for x in captured_spans if x.name == "err-captured")
    assert s.status == "error"
    assert "test error" in s.error


def test_captured_spans_isolated_between_tests_a(captured_spans: list) -> None:
    # This test and test_captured_spans_isolated_between_tests_b should each
    # see an empty fixture at start — no cross-contamination.
    assert len(captured_spans) == 0
    with tracer.span("isolation-a"):
        pass
    assert len(captured_spans) == 1


def test_captured_spans_isolated_between_tests_b(captured_spans: list) -> None:
    assert len(captured_spans) == 0
    with tracer.span("isolation-b"):
        pass
    assert len(captured_spans) == 1


# ---------------------------------------------------------------------------
# assert_span_emitted — success cases
# ---------------------------------------------------------------------------


def test_assert_span_emitted_by_name(captured_spans: list) -> None:
    with tracer.span("target-span"):
        pass

    assert_span_emitted(captured_spans, name="target-span")


def test_assert_span_emitted_by_name_and_model(captured_spans: list) -> None:
    with tracer.span("model-span", model="gpt-4o"):
        pass

    assert_span_emitted(captured_spans, name="model-span", model="gpt-4o")


def test_assert_span_emitted_by_status(captured_spans: list) -> None:
    with tracer.span("ok-span"):
        pass

    assert_span_emitted(captured_spans, name="ok-span", status="ok")


def test_assert_span_emitted_returns_span(captured_spans: list) -> None:
    with tracer.span("return-span", model="claude-3") as s:
        s.set_attribute("x", 1)

    result = assert_span_emitted(captured_spans, name="return-span")
    assert result.name == "return-span"
    assert result.model == "claude-3"


# ---------------------------------------------------------------------------
# assert_span_emitted — failure cases
# ---------------------------------------------------------------------------


def test_assert_span_emitted_raises_on_missing_name() -> None:
    with pytest.raises(AssertionError, match="no_such_span"):
        assert_span_emitted([], name="no_such_span")


def test_assert_span_emitted_raises_on_wrong_model(captured_spans: list) -> None:
    with tracer.span("model-span2", model="gpt-4o"):
        pass

    with pytest.raises(AssertionError, match="model-span2"):
        assert_span_emitted(captured_spans, name="model-span2", model="claude-3")


def test_assert_span_emitted_raises_on_wrong_status(captured_spans: list) -> None:
    with tracer.span("status-span"):
        pass

    with pytest.raises(AssertionError):
        assert_span_emitted(captured_spans, name="status-span", status="error")


def test_assert_span_emitted_reports_available_spans() -> None:
    with pytest.raises(AssertionError, match="Got:"):
        assert_span_emitted([], name="missing")
