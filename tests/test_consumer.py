"""Tests for tracium.consumer (ConsumerRegistry API)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tracium.consumer import (
    ConsumerRecord,
    ConsumerRegistry,
    IncompatibleSchemaError,
    _parse_version,
    assert_compatible,
    get_registry,
    register_consumer,
)

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _clear_global_registry() -> Generator[None, None, None]:
    """Reset the global registry before and after every test."""
    get_registry().clear()
    yield
    get_registry().clear()


# ---------------------------------------------------------------------------
# ConsumerRegistry.register
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_returns_record(self) -> None:
        registry = ConsumerRegistry()
        record = registry.register(
            "my-tool",
            namespaces=["trace", "eval"],
            schema_version="1.0",
        )
        assert isinstance(record, ConsumerRecord)
        assert record.tool_name == "my-tool"
        assert record.namespaces == ("trace", "eval")
        assert record.schema_version == "1.0"

    def test_register_with_contact_and_metadata(self) -> None:
        registry = ConsumerRegistry()
        record = registry.register(
            "tool",
            namespaces=["trace"],
            schema_version="1.0",
            contact="team@example.com",
            metadata={"region": "us-east-1"},
        )
        assert record.contact == "team@example.com"
        assert record.metadata["region"] == "us-east-1"

    def test_register_empty_tool_name_raises(self) -> None:
        registry = ConsumerRegistry()
        with pytest.raises(ValueError, match="tool_name"):
            registry.register("", namespaces=["trace"], schema_version="1.0")

    def test_register_empty_namespaces_raises(self) -> None:
        registry = ConsumerRegistry()
        with pytest.raises(ValueError, match="namespaces"):
            registry.register("tool", namespaces=[], schema_version="1.0")

    def test_register_invalid_version_raises(self) -> None:
        registry = ConsumerRegistry()
        with pytest.raises(ValueError, match="MAJOR.MINOR"):
            registry.register("tool", namespaces=["trace"], schema_version="bad")

    def test_register_multiple(self) -> None:
        registry = ConsumerRegistry()
        registry.register("a", namespaces=["trace"], schema_version="1.0")
        registry.register("b", namespaces=["eval"], schema_version="1.0")
        assert len(registry) == 2


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


class TestQuerying:
    def test_all_returns_snapshot(self) -> None:
        registry = ConsumerRegistry()
        registry.register("a", namespaces=["trace"], schema_version="1.0")
        result = registry.all()
        assert len(result) == 1
        assert result[0].tool_name == "a"

    def test_by_namespace_filters(self) -> None:
        registry = ConsumerRegistry()
        registry.register("a", namespaces=["trace", "eval"], schema_version="1.0")
        registry.register("b", namespaces=["cost"], schema_version="1.0")
        result = registry.by_namespace("trace")
        assert len(result) == 1
        assert result[0].tool_name == "a"

    def test_by_tool_found(self) -> None:
        registry = ConsumerRegistry()
        registry.register("tool-x", namespaces=["trace"], schema_version="1.0")
        record = registry.by_tool("tool-x")
        assert record is not None
        assert record.tool_name == "tool-x"

    def test_by_tool_found_second_record(self) -> None:
        """Loop must skip first record before finding the second (covers branch)."""
        registry = ConsumerRegistry()
        registry.register("tool-a", namespaces=["trace"], schema_version="1.0")
        registry.register("tool-b", namespaces=["eval"], schema_version="1.0")
        record = registry.by_tool("tool-b")
        assert record is not None
        assert record.tool_name == "tool-b"

    def test_by_tool_not_found(self) -> None:
        registry = ConsumerRegistry()
        assert registry.by_tool("does-not-exist") is None


# ---------------------------------------------------------------------------
# Compatibility checking
# ---------------------------------------------------------------------------


class TestCompatibilityChecking:
    def test_compatible_same_version(self) -> None:
        registry = ConsumerRegistry()
        registry.register("tool", namespaces=["trace"], schema_version="1.0")
        assert registry.check_compatible("1.0") == []

    def test_compatible_newer_installed(self) -> None:
        registry = ConsumerRegistry()
        registry.register("tool", namespaces=["trace"], schema_version="1.0")
        assert registry.check_compatible("1.1") == []

    def test_incompatible_consumer_newer(self) -> None:
        registry = ConsumerRegistry()
        registry.register("tool", namespaces=["trace"], schema_version="1.2")
        incompatible = registry.check_compatible("1.0")
        assert len(incompatible) == 1
        assert incompatible[0][0] == "tool"

    def test_incompatible_major_mismatch(self) -> None:
        registry = ConsumerRegistry()
        registry.register("tool", namespaces=["trace"], schema_version="2.0")
        incompatible = registry.check_compatible("1.0")
        assert len(incompatible) == 1

    def test_assert_compatible_raises(self) -> None:
        registry = ConsumerRegistry()
        registry.register("tool", namespaces=["trace"], schema_version="2.0")
        with pytest.raises(IncompatibleSchemaError) as exc_info:
            registry.assert_compatible("1.0")
        assert "tool" in str(exc_info.value)
        assert exc_info.value.incompatible[0][0] == "tool"

    def test_assert_compatible_passes(self) -> None:
        registry = ConsumerRegistry()
        registry.register("tool", namespaces=["trace"], schema_version="1.0")
        # Should not raise.
        registry.assert_compatible("1.0")

    def test_check_compatible_invalid_version_raises(self) -> None:
        registry = ConsumerRegistry()
        with pytest.raises(ValueError, match="MAJOR.MINOR"):
            registry.check_compatible("bad-version")


# ---------------------------------------------------------------------------
# clear / len
# ---------------------------------------------------------------------------


class TestClearLen:
    def test_len_after_register(self) -> None:
        registry = ConsumerRegistry()
        assert len(registry) == 0
        registry.register("a", namespaces=["trace"], schema_version="1.0")
        assert len(registry) == 1

    def test_clear(self) -> None:
        registry = ConsumerRegistry()
        registry.register("a", namespaces=["trace"], schema_version="1.0")
        registry.clear()
        assert len(registry) == 0


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestModuleLevelHelpers:
    def test_register_consumer_and_assert(self) -> None:
        register_consumer("tool", namespaces=["trace"], schema_version="1.0")
        assert_compatible("1.0")  # should not raise

    def test_get_registry_is_singleton(self) -> None:
        registry = get_registry()
        assert get_registry() is registry


# ---------------------------------------------------------------------------
# IncompatibleSchemaError
# ---------------------------------------------------------------------------


class TestIncompatibleSchemaError:
    def test_message_contains_all_tools(self) -> None:
        err = IncompatibleSchemaError([("tool-a", "2.0"), ("tool-b", "3.0")])
        msg = str(err)
        assert "tool-a" in msg
        assert "tool-b" in msg

    def test_incompatible_attribute(self) -> None:
        pairs = [("tool-a", "2.0")]
        err = IncompatibleSchemaError(pairs)
        assert err.incompatible == pairs


# ---------------------------------------------------------------------------
# _parse_version helper
# ---------------------------------------------------------------------------


class TestParseVersion:
    def test_valid_versions(self) -> None:
        assert _parse_version("1.0") == (1, 0)
        assert _parse_version("2.11") == (2, 11)

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_version("bad")

    def test_no_minor_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_version("1")
