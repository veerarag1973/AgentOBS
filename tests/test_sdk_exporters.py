"""Tests for agentobs.exporters — SyncJSONLExporter and SyncConsoleExporter.

Phase 5 SDK coverage target.
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

import pytest

from agentobs.event import Event
from agentobs.exporters import SyncConsoleExporter, SyncJSONLExporter
from agentobs.exporters.console import (
    _format_cost,
    _format_duration,
    _format_event,
    _format_tokens,
    _get,
    _status_colour,
)
from agentobs.types import EventType

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(**kw) -> Event:
    defaults = {
        "event_type": EventType.TRACE_SPAN_COMPLETED,
        "source": "test-service@1.0.0",
        "payload": {
            "span_id": "a" * 16,
            "trace_id": "b" * 32,
            "span_name": "test-span",
            "status": "ok",
            "duration_ms": 42.5,
        },
    }
    defaults.update(kw)
    return Event(**defaults)


# ===========================================================================
# SyncJSONLExporter
# ===========================================================================


@pytest.mark.unit
class TestSyncJSONLExporter:
    def test_exports_valid_json_line(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        exp = SyncJSONLExporter(str(path))
        try:
            event = _make_event()
            exp.export(event)
        finally:
            exp.close()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event_type"] == "llm.trace.span.completed"

    def test_multiple_events_produce_multiple_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "multi.jsonl"
        exp = SyncJSONLExporter(str(path))
        try:
            for _ in range(5):
                exp.export(_make_event())
        finally:
            exp.close()
        lines = [l for l in path.read_text().strip().split("\n") if l]  # noqa: E741
        assert len(lines) == 5
        for line in lines:
            json.loads(line)  # all valid JSON

    def test_append_mode_default(self, tmp_path: Path) -> None:
        path = tmp_path / "append.jsonl"
        # First exporter writes 2 events
        exp1 = SyncJSONLExporter(str(path))
        try:
            exp1.export(_make_event())
            exp1.export(_make_event())
        finally:
            exp1.close()
        # Second exporter appends 1 more
        exp2 = SyncJSONLExporter(str(path), mode="a")
        try:
            exp2.export(_make_event())
        finally:
            exp2.close()
        lines = [l for l in path.read_text().strip().split("\n") if l]  # noqa: E741
        assert len(lines) == 3

    def test_write_mode_truncates(self, tmp_path: Path) -> None:
        path = tmp_path / "overwrite.jsonl"
        # Write initial content
        path.write_text('{"old": true}\n')
        exp = SyncJSONLExporter(str(path), mode="w")
        try:
            exp.export(_make_event())
        finally:
            exp.close()
        lines = [l for l in path.read_text().strip().split("\n") if l]  # noqa: E741
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert "old" not in parsed

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="mode"):
            SyncJSONLExporter("/tmp/test.jsonl", mode="r")  # noqa: S108  # NOSONAR

    def test_export_after_close_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "closed.jsonl"
        exp = SyncJSONLExporter(str(path))
        exp.close()
        with pytest.raises(RuntimeError, match="closed"):
            exp.export(_make_event())

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "idem.jsonl"
        exp = SyncJSONLExporter(str(path))
        exp.export(_make_event())
        exp.close()
        exp.close()  # second close should not raise

    def test_context_manager_auto_closes(self, tmp_path: Path) -> None:
        path = tmp_path / "ctx.jsonl"
        with SyncJSONLExporter(str(path)) as exp:
            exp.export(_make_event())
        assert exp._closed

    def test_flush_after_write(self, tmp_path: Path) -> None:
        path = tmp_path / "flush.jsonl"
        exp = SyncJSONLExporter(str(path))
        try:
            exp.export(_make_event())
            exp.flush()  # should not raise
        finally:
            exp.close()

    def test_repr_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "repr.jsonl"
        exp = SyncJSONLExporter(str(path))
        exp.close()
        r = repr(exp)
        assert "closed" in r

    def test_repr_open(self, tmp_path: Path) -> None:
        path = tmp_path / "repr_open.jsonl"
        exp = SyncJSONLExporter(str(path))
        try:
            r = repr(exp)
            assert "open" in r
        finally:
            exp.close()

    def test_stdout_mode(self, capsys: pytest.CaptureFixture) -> None:
        exp = SyncJSONLExporter("-")
        exp.export(_make_event())
        # stdout was written to, not closed (it is sys.stdout)
        captured = capsys.readouterr()
        assert "llm.trace.span.completed" in captured.out

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        nested_path = tmp_path / "sub" / "dir" / "events.jsonl"
        exp = SyncJSONLExporter(str(nested_path))
        try:
            exp.export(_make_event())
        finally:
            exp.close()
        assert nested_path.exists()

    def test_thread_safe_concurrent_writes(self, tmp_path: Path) -> None:
        path = tmp_path / "concurrent.jsonl"
        exp = SyncJSONLExporter(str(path))
        errors = []

        def write_events() -> None:
            try:
                for _ in range(10):
                    exp.export(_make_event())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_events) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        exp.close()
        assert not errors
        lines = [l for l in path.read_text().strip().split("\n") if l]  # noqa: E741
        assert len(lines) == 50

    def test_event_id_in_exported_json(self, tmp_path: Path) -> None:
        path = tmp_path / "id.jsonl"
        event = _make_event()
        with SyncJSONLExporter(str(path)) as exp:
            exp.export(event)
        parsed = json.loads(path.read_text().strip())
        assert parsed["event_id"] == event.event_id

    def test_flush_when_no_file_open(self, tmp_path: Path) -> None:
        path = tmp_path / "nofile.jsonl"
        exp = SyncJSONLExporter(str(path))
        exp.flush()  # file not opened yet — should not raise
        exp.close()

    def test_path_object_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "pathobj.jsonl"
        exp = SyncJSONLExporter(path)  # Path object, not str
        try:
            exp.export(_make_event())
        finally:
            exp.close()
        assert path.exists()


# ===========================================================================
# SyncConsoleExporter
# ===========================================================================


@pytest.mark.unit
class TestSyncConsoleExporter:
    def test_export_writes_to_stdout(self, capsys: pytest.CaptureFixture) -> None:
        exp = SyncConsoleExporter()
        exp.export(_make_event())
        captured = capsys.readouterr()
        assert "test-span" in captured.out

    def test_export_event_type_in_output(self, capsys: pytest.CaptureFixture) -> None:
        exp = SyncConsoleExporter()
        exp.export(_make_event())
        captured = capsys.readouterr()
        assert "trace" in captured.out

    def test_flush_does_not_raise(self) -> None:
        exp = SyncConsoleExporter()
        exp.flush()  # should not raise

    def test_close_does_not_raise(self) -> None:
        exp = SyncConsoleExporter()
        exp.close()  # no-op

    def test_repr(self) -> None:
        exp = SyncConsoleExporter()
        assert "SyncConsoleExporter" in repr(exp)

    def test_error_event_in_output(self, capsys: pytest.CaptureFixture) -> None:
        exp = SyncConsoleExporter()
        event = _make_event(
            payload={
                "span_name": "error-span",
                "status": "error",
                "error": "something failed",
                "duration_ms": 10.0,
            }
        )
        exp.export(event)
        captured = capsys.readouterr()
        assert "error-span" in captured.out
        assert "error" in captured.out.lower()

    def test_event_with_token_usage(self, capsys: pytest.CaptureFixture) -> None:
        exp = SyncConsoleExporter()
        event = _make_event(
            payload={
                "span_name": "token-span",
                "status": "ok",
                "duration_ms": 50.0,
                "token_usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            }
        )
        exp.export(event)
        captured = capsys.readouterr()
        assert "100" in captured.out

    def test_event_with_cost(self, capsys: pytest.CaptureFixture) -> None:
        exp = SyncConsoleExporter()
        event = _make_event(
            payload={
                "span_name": "costly-span",
                "status": "ok",
                "duration_ms": 30.0,
                "cost": {"total_cost_usd": 0.001234, "currency": "USD"},
            }
        )
        exp.export(event)
        captured = capsys.readouterr()
        assert "0.00123" in captured.out

    def test_agent_event_renders_agent_name(self, capsys: pytest.CaptureFixture) -> None:
        exp = SyncConsoleExporter()
        event = _make_event(
            event_type=EventType.TRACE_AGENT_COMPLETED,
            payload={
                "agent_name": "my-research-agent",
                "status": "ok",
                "total_steps": 3,
                "duration_ms": 1200.0,
            }
        )
        exp.export(event)
        captured = capsys.readouterr()
        assert "my-research-agent" in captured.out
        assert "3" in captured.out

    def test_agent_step_renders_step(self, capsys: pytest.CaptureFixture) -> None:
        exp = SyncConsoleExporter()
        event = _make_event(
            event_type=EventType.TRACE_AGENT_STEP,
            payload={
                "step_name": "web-lookup",
                "status": "ok",
                "step_index": 1,
                "duration_ms": 200.0,
            }
        )
        exp.export(event)
        captured = capsys.readouterr()
        assert "web-lookup" in captured.out


# ===========================================================================
# Console exporter helper functions
# ===========================================================================


@pytest.mark.unit
class TestConsoleHelpers:
    def test_get_nested_value(self) -> None:
        payload = {"model": {"name": "gpt-4o"}}
        assert _get(payload, "model", "name") == "gpt-4o"

    def test_get_missing_key_returns_default(self) -> None:
        assert _get({}, "model", "name") == ""
        assert _get({}, "nonexistent", default="N/A") == "N/A"

    def test_get_none_value_returns_default(self) -> None:
        payload = {"key": None}
        assert _get(payload, "key") == ""

    def test_get_non_dict_intermediate(self) -> None:
        payload = {"model": "not-a-dict"}
        assert _get(payload, "model", "name") == ""

    def test_format_tokens_with_usage(self) -> None:
        payload = {"token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
        result = _format_tokens(payload)
        assert result is not None
        assert "10" in result
        assert "5" in result
        assert "15" in result

    def test_format_tokens_no_usage(self) -> None:
        assert _format_tokens({}) is None
        assert _format_tokens({"token_usage": None}) is None
        assert _format_tokens({"token_usage": "invalid"}) is None

    def test_format_cost_usd(self) -> None:
        payload = {"cost": {"total_cost_usd": 0.00123, "currency": "USD"}}
        result = _format_cost(payload)
        assert result is not None
        assert "$" in result
        assert "0.00123" in result

    def test_format_cost_non_usd_currency(self) -> None:
        payload = {"cost": {"total_cost_usd": 1.0, "currency": "EUR"}}
        result = _format_cost(payload)
        assert result is not None
        assert "EUR" in result

    def test_format_cost_no_cost(self) -> None:
        assert _format_cost({}) is None
        assert _format_cost({"cost": None}) is None
        assert _format_cost({"cost": {"no_total": 0}}) is None

    def test_format_duration_ms(self) -> None:
        payload = {"duration_ms": 142.35}
        result = _format_duration(payload)
        assert result is not None
        assert "142" in result
        assert "ms" in result

    def test_format_duration_no_value(self) -> None:
        assert _format_duration({}) is None
        assert _format_duration({"duration_ms": None}) is None

    def test_status_colour_ok(self) -> None:
        from agentobs.exporters.console import _GREEN  # noqa: PLC0415
        assert _status_colour("ok") == _GREEN

    def test_status_colour_error(self) -> None:
        from agentobs.exporters.console import _RED  # noqa: PLC0415
        assert _status_colour("error") == _RED

    def test_status_colour_timeout(self) -> None:
        from agentobs.exporters.console import _RED  # noqa: PLC0415
        assert _status_colour("timeout") == _RED

    def test_status_colour_unknown(self) -> None:
        from agentobs.exporters.console import _YELLOW  # noqa: PLC0415
        assert _status_colour("pending") == _YELLOW

    def test_format_event_returns_string(self) -> None:
        event = _make_event()
        result = _format_event(event)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_event_contains_event_id(self) -> None:
        event = _make_event()
        result = _format_event(event)
        assert event.event_id in result

    def test_format_event_span_with_trace_info(self) -> None:
        event = _make_event()
        result = _format_event(event)
        assert "event_id" in result
        assert "event_typ" in result

    def test_format_event_no_duration(self) -> None:
        event = _make_event(payload={"span_name": "nodur", "status": "ok"})
        result = _format_event(event)
        assert "nodur" in result

    def test_format_event_total_steps_included(self) -> None:
        event = _make_event(
            event_type=EventType.TRACE_AGENT_COMPLETED,
            payload={"agent_name": "bot", "status": "ok", "total_steps": 7},
        )
        result = _format_event(event)
        assert "7" in result

    def test_format_event_step_index_included(self) -> None:
        event = _make_event(
            event_type=EventType.TRACE_AGENT_STEP,
            payload={"step_name": "s", "status": "ok", "step_index": 3},
        )
        result = _format_event(event)
        assert "3" in result


# ===========================================================================
# Exporters __init__ re-exports
# ===========================================================================


@pytest.mark.unit
class TestExportersInit:
    def test_sync_jsonl_exporter_importable(self) -> None:
        from agentobs.exporters import SyncJSONLExporter as J  # noqa: PLC0415
        assert J is SyncJSONLExporter

    def test_sync_console_exporter_importable(self) -> None:
        from agentobs.exporters import SyncConsoleExporter as C  # noqa: PLC0415
        assert C is SyncConsoleExporter
