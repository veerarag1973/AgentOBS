"""Tests for agentobs/export/jsonl.py — JSONLExporter.

Coverage targets
----------------
* Write/append/read-back round-trip.
* Overwrite (``mode="w"``) truncates existing content.
* Stdout mode (``path="-"``).
* Async context manager (``async with``).
* Concurrent appends serialised by asyncio.Lock.
* ``export_batch`` returns correct count.
* ``flush`` and ``close`` are idempotent.
* ``close`` prevents further writes (RuntimeError).
* Invalid mode raises ValueError.
* ``__repr__``.
"""

from __future__ import annotations

import asyncio
import io
import sys
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from agentobs.event import Event
from agentobs.export.jsonl import JSONLExporter

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: str = "llm.trace.span.completed") -> Event:
    return Event(
        event_type=event_type,
        source="test-tool@1.0.0",
        payload={"status": "ok"},
    )


def _read_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Mode validation
# ---------------------------------------------------------------------------


class TestModeValidation:
    def test_invalid_mode_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="mode"):
            JSONLExporter("/tmp/test.jsonl", mode="r")  # noqa: S108  # NOSONAR

    def test_append_mode_accepted(self) -> None:
        exp = JSONLExporter("/tmp/test.jsonl", mode="a")  # noqa: S108  # NOSONAR
        assert exp._mode == "a"

    def test_write_mode_accepted(self) -> None:
        exp = JSONLExporter("/tmp/test.jsonl", mode="w")  # noqa: S108  # NOSONAR
        assert exp._mode == "w"


# ---------------------------------------------------------------------------
# Single export to file
# ---------------------------------------------------------------------------


class TestExportToFile:
    def test_export_single_event(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        event = _make_event()

        async def _run() -> None:
            exp = JSONLExporter(path)
            await exp.export(event)
            exp.close()

        asyncio.run(_run())

        lines = _read_lines(path)
        assert len(lines) == 1
        import json  # noqa: PLC0415
        data = json.loads(lines[0])
        assert data["event_id"] == event.event_id

    def test_export_writes_valid_json_line(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        event = _make_event()

        async def _run() -> None:
            async with JSONLExporter(path) as exp:
                await exp.export(event)

        asyncio.run(_run())

        import json  # noqa: PLC0415
        content = path.read_text(encoding="utf-8")
        # Each line is a valid JSON object
        for line in content.splitlines():
            if line.strip():
                obj = json.loads(line)
                assert "event_id" in obj

    def test_export_appends_multiple_events(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        events = [_make_event() for _ in range(5)]

        async def _run() -> None:
            async with JSONLExporter(path) as exp:
                for event in events:
                    await exp.export(event)

        asyncio.run(_run())

        lines = _read_lines(path)
        assert len(lines) == 5

    def test_append_mode_does_not_overwrite(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        event1 = _make_event()
        event2 = _make_event()

        async def _run() -> None:
            async with JSONLExporter(path, mode="a") as exp:
                await exp.export(event1)

            async with JSONLExporter(path, mode="a") as exp:
                await exp.export(event2)

        asyncio.run(_run())

        lines = _read_lines(path)
        assert len(lines) == 2

    def test_write_mode_overwrites_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        event1 = _make_event()
        event2 = _make_event()

        async def _run() -> None:
            async with JSONLExporter(path, mode="a") as exp:
                await exp.export(event1)

            async with JSONLExporter(path, mode="w") as exp:
                await exp.export(event2)

        asyncio.run(_run())

        lines = _read_lines(path)
        assert len(lines) == 1
        import json  # noqa: PLC0415
        assert json.loads(lines[0])["event_id"] == event2.event_id


# ---------------------------------------------------------------------------
# export_batch
# ---------------------------------------------------------------------------


class TestExportBatch:
    def test_export_batch_writes_all_events(self, tmp_path: Path) -> None:
        path = tmp_path / "batch.jsonl"
        events = [_make_event() for _ in range(10)]

        async def _run() -> int:
            async with JSONLExporter(path) as exp:
                return await exp.export_batch(events)

        count = asyncio.run(_run())
        assert count == 10
        lines = _read_lines(path)
        assert len(lines) == 10

    def test_export_batch_empty_returns_zero(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"

        async def _run() -> int:
            async with JSONLExporter(path) as exp:
                return await exp.export_batch([])

        count = asyncio.run(_run())
        assert count == 0
        # File may not even exist / is empty
        if path.exists():
            assert path.read_text().strip() == ""

    def test_export_batch_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "batch.jsonl"
        events = [_make_event() for _ in range(3)]

        async def _run() -> None:
            async with JSONLExporter(path) as exp:
                await exp.export_batch(events)

        asyncio.run(_run())

        import json  # noqa: PLC0415
        lines = _read_lines(path)
        loaded_ids = [json.loads(line)["event_id"] for line in lines]
        expected_ids = [e.event_id for e in events]
        assert loaded_ids == expected_ids


# ---------------------------------------------------------------------------
# Stdout mode (path="-")
# ---------------------------------------------------------------------------


class TestStdoutMode:
    def test_stdout_mode_writes_to_stdout(self) -> None:
        event = _make_event()

        async def _run() -> None:
            exp = JSONLExporter("-")
            await exp.export(event)
            # Do not close stdout — just flush conceptually.

        captured = io.StringIO()
        with patch("sys.stdout", captured):
            asyncio.run(_run())

        output = captured.getvalue()
        assert event.event_id in output

    def test_close_does_not_close_stdout(self) -> None:
        exp = JSONLExporter("-")
        # Ensure file is lazily opened
        # We don't actually open it (no write call), just ensure close doesn't blow up.
        exp.close()
        assert not sys.stdout.closed


# ---------------------------------------------------------------------------
# flush and close
# ---------------------------------------------------------------------------


class TestFlushAndClose:
    def test_flush_before_any_write_no_error(self, tmp_path: Path) -> None:
        exp = JSONLExporter(tmp_path / "f.jsonl")
        exp.flush()  # No file open yet — should not raise.

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        exp = JSONLExporter(tmp_path / "f.jsonl")

        async def _run() -> None:
            await exp.export(_make_event())

        asyncio.run(_run())
        exp.close()
        exp.close()  # Second close should not raise.

    def test_export_after_close_raises_runtime_error(self, tmp_path: Path) -> None:
        exp = JSONLExporter(tmp_path / "f.jsonl")
        exp.close()

        async def _run() -> None:
            await exp.export(_make_event())

        with pytest.raises(RuntimeError, match="closed"):
            asyncio.run(_run())

    def test_flush_writes_buffered_data(self, tmp_path: Path) -> None:
        path = tmp_path / "flush.jsonl"
        event = _make_event()

        async def _run() -> None:
            exp = JSONLExporter(path)
            await exp.export(event)
            exp.flush()
            exp.close()

        asyncio.run(_run())
        lines = _read_lines(path)
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# Async context manager
# ---------------------------------------------------------------------------


class TestAsyncContextManager:
    def test_context_manager_closes_file_on_exit(self, tmp_path: Path) -> None:
        path = tmp_path / "ctx.jsonl"

        async def _run() -> JSONLExporter:
            async with JSONLExporter(path) as exp:
                await exp.export(_make_event())
            return exp

        exp = asyncio.run(_run())
        assert exp._closed

    def test_context_manager_returns_exporter(self, tmp_path: Path) -> None:
        path = tmp_path / "ctx2.jsonl"

        async def _run() -> None:
            async with JSONLExporter(path) as exp:
                assert isinstance(exp, JSONLExporter)

        asyncio.run(_run())

    def test_context_manager_closes_on_exception(self, tmp_path: Path) -> None:
        path = tmp_path / "ctx3.jsonl"
        exp_ref: list = []

        async def _run() -> None:
            try:
                async with JSONLExporter(path) as exp:
                    exp_ref.append(exp)
                    await exp.export(_make_event())
                    raise ValueError("deliberate")  # noqa: TRY301
            except ValueError:
                ...
        asyncio.run(_run())
        assert exp_ref[0]._closed


# ---------------------------------------------------------------------------
# Concurrent appends (Lock)
# ---------------------------------------------------------------------------


class TestConcurrentAppends:
    def test_concurrent_exports_produce_correct_line_count(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "concurrent.jsonl"
        events = [_make_event() for _ in range(20)]

        async def _run() -> None:
            async with JSONLExporter(path) as exp:
                await asyncio.gather(*(exp.export(e) for e in events))

        asyncio.run(_run())
        lines = _read_lines(path)
        assert len(lines) == 20

    def test_concurrent_batch_exports(self, tmp_path: Path) -> None:
        path = tmp_path / "concurrent_batch.jsonl"
        batch_a = [_make_event() for _ in range(5)]
        batch_b = [_make_event() for _ in range(5)]

        async def _run() -> None:
            async with JSONLExporter(path) as exp:
                await asyncio.gather(
                    exp.export_batch(batch_a),
                    exp.export_batch(batch_b),
                )

        asyncio.run(_run())
        lines = _read_lines(path)
        assert len(lines) == 10


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------


class TestJSONLRepr:
    def test_repr_contains_path(self) -> None:
        exp = JSONLExporter("/some/path/events.jsonl")
        assert "events.jsonl" in repr(exp)

    def test_repr_contains_mode(self) -> None:
        exp = JSONLExporter("/tmp/e.jsonl", mode="w")  # noqa: S108  # NOSONAR
        assert "'w'" in repr(exp)

    def test_repr_stdout(self) -> None:
        exp = JSONLExporter("-")
        assert "-" in repr(exp)
