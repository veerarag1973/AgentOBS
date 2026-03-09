"""Tests for agentobs.deprecations (DeprecationRegistry)."""

from __future__ import annotations

import warnings
from collections.abc import Generator

import pytest

from agentobs.deprecations import (
    DeprecationNotice,
    DeprecationRegistry,
    get_deprecation_notice,
    get_registry,
    list_deprecated,
    mark_deprecated,
    warn_if_deprecated,
)


@pytest.fixture(autouse=True)
def _clear_global_registry() -> Generator[None, None, None]:
    """Reset the global registry before and after every test."""
    get_registry().clear()
    yield
    get_registry().clear()


# ---------------------------------------------------------------------------
# DeprecationNotice.format_message
# ---------------------------------------------------------------------------


class TestDeprecationNotice:
    def test_format_message_with_replacement(self) -> None:
        notice = DeprecationNotice(
            event_type="old.type",
            since="1.0",
            sunset="2.0",
            replacement="new.type",
        )
        msg = notice.format_message()
        assert "old.type" in msg
        assert "1.0" in msg
        assert "2.0" in msg
        assert "new.type" in msg

    def test_format_message_without_replacement(self) -> None:
        notice = DeprecationNotice(event_type="old.type", since="1.0", sunset="2.0")
        msg = notice.format_message()
        assert "old.type" in msg
        assert "replacement" not in msg.lower()

    def test_format_message_with_notes(self) -> None:
        notice = DeprecationNotice(
            event_type="t",
            since="1.0",
            sunset="2.0",
            notes="Please update ASAP.",
        )
        msg = notice.format_message()
        assert "Please update ASAP." in msg

    def test_immutable(self) -> None:
        notice = DeprecationNotice(event_type="t", since="1.0", sunset="2.0")
        with pytest.raises((AttributeError, TypeError)):
            notice.event_type = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DeprecationRegistry.mark_deprecated
# ---------------------------------------------------------------------------


class TestMarkDeprecated:
    def test_mark_and_get(self) -> None:
        registry = DeprecationRegistry()
        notice = registry.mark_deprecated(
            "old.type", since="1.0", sunset="2.0", replacement="new.type"
        )
        assert registry.get("old.type") is notice

    def test_mark_updates_existing(self) -> None:
        registry = DeprecationRegistry()
        registry.mark_deprecated("old.type", since="1.0", sunset="2.0")
        updated = registry.mark_deprecated(
            "old.type", since="1.0", sunset="3.0", replacement="new.type"
        )
        assert registry.get("old.type") is updated
        assert registry.get("old.type").sunset == "3.0"

    def test_empty_event_type_raises(self) -> None:
        registry = DeprecationRegistry()
        with pytest.raises(ValueError, match="event_type"):
            registry.mark_deprecated("", since="1.0", sunset="2.0")

    def test_empty_since_raises(self) -> None:
        registry = DeprecationRegistry()
        with pytest.raises(ValueError, match="since"):
            registry.mark_deprecated("t", since="", sunset="2.0")

    def test_empty_sunset_raises(self) -> None:
        registry = DeprecationRegistry()
        with pytest.raises(ValueError, match="sunset"):
            registry.mark_deprecated("t", since="1.0", sunset="")


# ---------------------------------------------------------------------------
# DeprecationRegistry.get / is_deprecated
# ---------------------------------------------------------------------------


class TestGetIsDeprecated:
    def test_get_returns_none_if_absent(self) -> None:
        registry = DeprecationRegistry()
        assert registry.get("missing.type") is None

    def test_is_deprecated_true(self) -> None:
        registry = DeprecationRegistry()
        registry.mark_deprecated("t", since="1.0", sunset="2.0")
        assert registry.is_deprecated("t") is True

    def test_is_deprecated_false(self) -> None:
        registry = DeprecationRegistry()
        assert registry.is_deprecated("t") is False


# ---------------------------------------------------------------------------
# warn_if_deprecated
# ---------------------------------------------------------------------------


class TestWarnIfDeprecated:
    def test_warns_for_deprecated_type(self) -> None:
        registry = DeprecationRegistry()
        registry.mark_deprecated("old.type", since="1.0", sunset="2.0")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registry.warn_if_deprecated("old.type")
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)

    def test_no_warn_for_non_deprecated_type(self) -> None:
        registry = DeprecationRegistry()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registry.warn_if_deprecated("normal.type")
        assert len(w) == 0


# ---------------------------------------------------------------------------
# list_all / remove / clear
# ---------------------------------------------------------------------------


class TestListRemoveClear:
    def test_list_all_sorted(self) -> None:
        registry = DeprecationRegistry()
        registry.mark_deprecated("z.type", since="1.0", sunset="2.0")
        registry.mark_deprecated("a.type", since="1.0", sunset="2.0")
        notices = registry.list_all()
        assert [n.event_type for n in notices] == ["a.type", "z.type"]

    def test_remove_existing(self) -> None:
        registry = DeprecationRegistry()
        registry.mark_deprecated("t", since="1.0", sunset="2.0")
        removed = registry.remove("t")
        assert removed is True
        assert registry.get("t") is None

    def test_remove_non_existing(self) -> None:
        registry = DeprecationRegistry()
        assert registry.remove("does-not-exist") is False

    def test_clear(self) -> None:
        registry = DeprecationRegistry()
        registry.mark_deprecated("t", since="1.0", sunset="2.0")
        registry.clear()
        assert len(registry) == 0

    def test_len(self) -> None:
        registry = DeprecationRegistry()
        assert len(registry) == 0
        registry.mark_deprecated("a", since="1.0", sunset="2.0")
        assert len(registry) == 1


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestModuleLevelHelpers:
    def test_mark_deprecated_global(self) -> None:
        notice = mark_deprecated("my.type", since="1.0", sunset="2.0")
        assert get_deprecation_notice("my.type") is notice

    def test_warn_if_deprecated_global(self) -> None:
        mark_deprecated("my.type", since="1.0", sunset="2.0")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_deprecated("my.type")
        assert len(w) == 1

    def test_list_deprecated_global(self) -> None:
        mark_deprecated("a.type", since="1.0", sunset="2.0")
        mark_deprecated("b.type", since="1.0", sunset="2.0")
        notices = list_deprecated()
        event_types = [n.event_type for n in notices]
        assert "a.type" in event_types
        assert "b.type" in event_types

    def test_get_registry_is_singleton(self) -> None:
        assert get_registry() is get_registry()
