"""Tests for agentobs/stream.py — EventStream.

Coverage targets
----------------
* Construction: from list, empty, iterable.
* ``from_file``: normal, blank lines, skip_errors=True/False.
* ``from_queue``: drain, sentinel, empty queue.
* ``from_async_queue``: sentinel-terminated.
* ``from_async_iter``: full drain.
* ``filter``: predicate keeps/drops events, empty result.
* ``filter_by_type``: single, multiple, none matching.
* ``filter_by_tags``: all match, partial mismatch, no tags on event.
* ``route``: with predicate, no predicate, empty dispatch.
* ``drain``: exports all events.
* Sequence protocol: ``__iter__``, ``__len__``, ``__getitem__`` (int + slice).
* ``__repr__``, ``__eq__``.
* ``Exporter`` protocol structural check.
"""

from __future__ import annotations

import asyncio
import queue as stdlib_queue
from typing import TYPE_CHECKING

import pytest

from agentobs.event import Event, Tags
from agentobs.exceptions import DeserializationError
from agentobs.stream import EventStream, Exporter

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    event_type: str = "llm.trace.span.completed",
    org_id: str | None = None,
    tags: Tags | None = None,
) -> Event:
    return Event(
        event_type=event_type,
        source="test-tool@1.0.0",
        payload={"status": "ok"},
        org_id=org_id,
        tags=tags,
    )


class _MockExporter:
    """A simple in-memory exporter for testing route/drain."""

    def __init__(self) -> None:
        self.received: list[Event] = []

    async def export_batch(self, events: Sequence[Event]) -> int:
        self.received.extend(events)
        return len(events)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestEventStreamConstruction:
    def test_empty_by_default(self) -> None:
        stream = EventStream()
        assert len(stream) == 0

    def test_from_list(self) -> None:
        events = [_make_event() for _ in range(3)]
        stream = EventStream(events)
        assert len(stream) == 3

    def test_from_generator(self) -> None:
        events = (_make_event() for _ in range(4))
        stream = EventStream(events)
        assert len(stream) == 4

    def test_events_preserved_in_order(self) -> None:
        events = [_make_event() for _ in range(5)]
        stream = EventStream(events)
        for original, stored in zip(events, stream):
            assert original.event_id == stored.event_id


# ---------------------------------------------------------------------------
# from_file
# ---------------------------------------------------------------------------


class TestFromFile:
    def test_round_trip_single_event(self, tmp_path: Path) -> None:
        event = _make_event()
        path = tmp_path / "single.jsonl"
        path.write_text(event.to_json() + "\n", encoding="utf-8")

        stream = EventStream.from_file(path)
        assert len(stream) == 1
        assert stream[0].event_id == event.event_id

    def test_round_trip_multiple_events(self, tmp_path: Path) -> None:
        events = [_make_event() for _ in range(10)]
        path = tmp_path / "multi.jsonl"
        path.write_text("\n".join(e.to_json() for e in events) + "\n", encoding="utf-8")

        stream = EventStream.from_file(path)
        assert len(stream) == 10
        for original, loaded in zip(events, stream):
            assert original.event_id == loaded.event_id

    def test_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        event = _make_event()
        path = tmp_path / "blanks.jsonl"
        path.write_text(f"\n{event.to_json()}\n\n\n", encoding="utf-8")

        stream = EventStream.from_file(path)
        assert len(stream) == 1

    def test_empty_file_returns_empty_stream(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")

        stream = EventStream.from_file(path)
        assert len(stream) == 0

    def test_malformed_line_raises_deserialization_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text('{"not": "an event"}\n', encoding="utf-8")

        with pytest.raises(DeserializationError):
            EventStream.from_file(path)

    def test_skip_errors_true_skips_malformed_lines(self, tmp_path: Path) -> None:
        good_event = _make_event()
        path = tmp_path / "mixed.jsonl"
        path.write_text(
            'not-json\n' + good_event.to_json() + '\n{"bad": true}\n',
            encoding="utf-8",
        )

        stream = EventStream.from_file(path, skip_errors=True)
        assert len(stream) == 1
        assert stream[0].event_id == good_event.event_id

    def test_skip_errors_false_raises_on_first_bad_line(self, tmp_path: Path) -> None:
        path = tmp_path / "bad2.jsonl"
        path.write_text("INVALID\n", encoding="utf-8")

        with pytest.raises(DeserializationError) as exc_info:
            EventStream.from_file(path, skip_errors=False)

        assert str(path) in exc_info.value.source_hint

    def test_from_file_string_path(self, tmp_path: Path) -> None:
        event = _make_event()
        path = tmp_path / "str_path.jsonl"
        path.write_text(event.to_json() + "\n", encoding="utf-8")

        stream = EventStream.from_file(str(path))
        assert len(stream) == 1


# ---------------------------------------------------------------------------
# from_queue (sync)  # noqa: ERA001
# ---------------------------------------------------------------------------


class TestFromQueue:
    def test_drains_all_events(self) -> None:
        events = [_make_event() for _ in range(5)]
        q: stdlib_queue.Queue = stdlib_queue.Queue()
        for e in events:
            q.put(e)

        stream = EventStream.from_queue(q)
        assert len(stream) == 5

    def test_empty_queue_returns_empty_stream(self) -> None:
        q: stdlib_queue.Queue = stdlib_queue.Queue()
        stream = EventStream.from_queue(q)
        assert len(stream) == 0

    def test_sentinel_stops_early(self) -> None:
        events = [_make_event() for _ in range(3)]
        SENTINEL = object()  # noqa: N806
        q: stdlib_queue.Queue = stdlib_queue.Queue()
        q.put(events[0])
        q.put(events[1])
        q.put(SENTINEL)
        q.put(events[2])  # Should not be consumed

        stream = EventStream.from_queue(q, sentinel=SENTINEL)
        assert len(stream) == 2
        assert not q.empty()  # events[2] still in queue

    def test_sentinel_not_included_in_stream(self) -> None:
        SENTINEL = None  # noqa: N806
        q: stdlib_queue.Queue = stdlib_queue.Queue()
        q.put(_make_event())
        q.put(None)  # sentinel

        stream = EventStream.from_queue(q, sentinel=SENTINEL)
        assert len(stream) == 1


# ---------------------------------------------------------------------------
# from_async_queue
# ---------------------------------------------------------------------------


class TestFromAsyncQueue:
    def test_drains_until_default_sentinel(self) -> None:
        events = [_make_event() for _ in range(4)]

        async def _run() -> EventStream:
            q: asyncio.Queue = asyncio.Queue()
            for e in events:
                await q.put(e)
            await q.put(None)  # default sentinel
            return await EventStream.from_async_queue(q)

        stream = asyncio.run(_run())
        assert len(stream) == 4

    def test_custom_sentinel(self) -> None:
        DONE = object()  # noqa: N806

        async def _run() -> EventStream:
            q: asyncio.Queue = asyncio.Queue()
            q.put_nowait(_make_event())
            q.put_nowait(DONE)
            return await EventStream.from_async_queue(q, sentinel=DONE)

        stream = asyncio.run(_run())
        assert len(stream) == 1

    def test_empty_via_immediate_sentinel(self) -> None:
        async def _run() -> EventStream:
            q: asyncio.Queue = asyncio.Queue()
            q.put_nowait(None)
            return await EventStream.from_async_queue(q)

        stream = asyncio.run(_run())
        assert len(stream) == 0


# ---------------------------------------------------------------------------
# from_async_iter
# ---------------------------------------------------------------------------


class TestFromAsyncIter:
    def test_drains_async_iterator(self) -> None:
        events = [_make_event() for _ in range(5)]

        async def _aiter():
            for e in events:
                yield e

        async def _run() -> EventStream:
            return await EventStream.from_async_iter(_aiter())

        stream = asyncio.run(_run())
        assert len(stream) == 5

    def test_empty_async_iterator(self) -> None:
        async def _empty():
            return
            yield  # make it a generator

        async def _run() -> EventStream:
            return await EventStream.from_async_iter(_empty())

        stream = asyncio.run(_run())
        assert len(stream) == 0


# ---------------------------------------------------------------------------
# filter
# ---------------------------------------------------------------------------


class TestFilter:
    def test_filter_keeps_matching_events(self) -> None:
        events = [_make_event("llm.trace.span.completed") for _ in range(3)]
        events.append(_make_event("llm.cost.recorded"))
        stream = EventStream(events)

        filtered = stream.filter(lambda e: e.event_type == "llm.cost.recorded")
        assert len(filtered) == 1

    def test_filter_empty_result(self) -> None:
        events = [_make_event() for _ in range(3)]
        stream = EventStream(events)

        filtered = stream.filter(lambda e: False)
        assert len(filtered) == 0

    def test_filter_returns_new_stream(self) -> None:
        events = [_make_event() for _ in range(3)]
        stream = EventStream(events)
        filtered = stream.filter(lambda e: True)
        assert filtered is not stream

    def test_filter_does_not_mutate_original(self) -> None:
        events = [_make_event() for _ in range(5)]
        stream = EventStream(events)
        stream.filter(lambda e: False)
        assert len(stream) == 5

    def test_filter_preserves_order(self) -> None:
        events = [_make_event() for _ in range(10)]
        stream = EventStream(events)
        filtered = stream.filter(lambda e: True)
        for orig, filt in zip(events, filtered):
            assert orig.event_id == filt.event_id


# ---------------------------------------------------------------------------
# filter_by_type
# ---------------------------------------------------------------------------


class TestFilterByType:
    def test_single_type_filter(self) -> None:
        events = [
            _make_event("llm.trace.span.completed"),
            _make_event("llm.cost.recorded"),
        ]
        stream = EventStream(events)
        filtered = stream.filter_by_type("llm.cost.recorded")
        assert len(filtered) == 1
        assert filtered[0].event_type == "llm.cost.recorded"

    def test_multiple_types_filter(self) -> None:
        events = [
            _make_event("llm.trace.span.completed"),
            _make_event("llm.cost.recorded"),
            _make_event("llm.eval.scenario.started"),
        ]
        stream = EventStream(events)
        filtered = stream.filter_by_type(
            "llm.trace.span.completed", "llm.eval.scenario.started"
        )
        assert len(filtered) == 2

    def test_no_match_returns_empty(self) -> None:
        events = [_make_event("llm.trace.span.completed")]
        stream = EventStream(events)
        filtered = stream.filter_by_type("llm.cost.recorded")
        assert len(filtered) == 0


# ---------------------------------------------------------------------------
# filter_by_tags
# ---------------------------------------------------------------------------


class TestFilterByTags:
    def test_matching_single_tag(self) -> None:
        matching = _make_event(tags=Tags(env="prod"))
        not_matching = _make_event(tags=Tags(env="dev"))
        stream = EventStream([matching, not_matching])

        filtered = stream.filter_by_tags(env="prod")
        assert len(filtered) == 1
        assert filtered[0].event_id == matching.event_id

    def test_matching_multiple_tags_all_required(self) -> None:
        match_both = _make_event(tags=Tags(env="prod", region="eu"))
        match_one = _make_event(tags=Tags(env="prod"))
        stream = EventStream([match_both, match_one])

        filtered = stream.filter_by_tags(env="prod", region="eu")
        assert len(filtered) == 1

    def test_event_without_tags_not_matched(self) -> None:
        no_tags = _make_event()  # tags=None
        stream = EventStream([no_tags])

        filtered = stream.filter_by_tags(env="prod")
        assert len(filtered) == 0

    def test_tag_value_mismatch_not_matched(self) -> None:
        event = _make_event(tags=Tags(env="staging"))
        stream = EventStream([event])

        filtered = stream.filter_by_tags(env="prod")
        assert len(filtered) == 0

    def test_no_tag_criteria_matches_events_with_tags(self) -> None:
        # filter_by_tags() with zero kwargs: events with a Tags object pass
        # (all(... for k,v in {}.items()) is vacuously True); events with
        # tags=None return False (tags is None guard).
        events = [
            _make_event(tags=Tags(env="prod")),
            _make_event(tags=Tags(env="dev")),
            _make_event(),  # tags=None — does NOT match
        ]
        stream = EventStream(events)
        filtered = stream.filter_by_tags()
        # Only the 2 events with Tags objects match.
        assert len(filtered) == 2


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------


class TestRoute:
    def test_route_no_predicate_sends_all(self) -> None:
        events = [_make_event() for _ in range(4)]
        stream = EventStream(events)
        exporter = _MockExporter()

        count = asyncio.run(stream.route(exporter))
        assert count == 4
        assert len(exporter.received) == 4

    def test_route_with_predicate_filters(self) -> None:
        events = [_make_event("llm.trace.span.completed")] * 3
        events += [_make_event("llm.cost.recorded")]
        stream = EventStream(events)
        exporter = _MockExporter()

        count = asyncio.run(
            stream.route(exporter, lambda e: e.event_type == "llm.cost.recorded")
        )
        assert count == 1
        assert exporter.received[0].event_type == "llm.cost.recorded"

    def test_route_empty_match_does_not_call_exporter(self) -> None:
        exporter = _MockExporter()
        stream = EventStream([_make_event()])

        count = asyncio.run(stream.route(exporter, lambda e: False))
        assert count == 0
        assert len(exporter.received) == 0

    def test_route_empty_stream(self) -> None:
        exporter = _MockExporter()
        stream = EventStream()

        count = asyncio.run(stream.route(exporter))
        assert count == 0


# ---------------------------------------------------------------------------
# drain
# ---------------------------------------------------------------------------


class TestDrain:
    def test_drain_exports_all_events(self) -> None:
        events = [_make_event() for _ in range(6)]
        stream = EventStream(events)
        exporter = _MockExporter()

        count = asyncio.run(stream.drain(exporter))
        assert count == 6
        assert len(exporter.received) == 6

    def test_drain_empty_stream(self) -> None:
        exporter = _MockExporter()
        stream = EventStream()

        count = asyncio.run(stream.drain(exporter))
        assert count == 0

    def test_drain_preserves_event_order(self) -> None:
        events = [_make_event() for _ in range(5)]
        stream = EventStream(events)
        exporter = _MockExporter()

        asyncio.run(stream.drain(exporter))
        for original, received in zip(events, exporter.received):
            assert original.event_id == received.event_id


# ---------------------------------------------------------------------------
# Sequence protocol
# ---------------------------------------------------------------------------


class TestSequenceProtocol:
    def test_len(self) -> None:
        stream = EventStream([_make_event() for _ in range(7)])
        assert len(stream) == 7

    def test_iter(self) -> None:
        events = [_make_event() for _ in range(3)]
        stream = EventStream(events)
        iterated = list(stream)
        assert [e.event_id for e in iterated] == [e.event_id for e in events]

    def test_getitem_int(self) -> None:
        events = [_make_event() for _ in range(3)]
        stream = EventStream(events)
        assert stream[0].event_id == events[0].event_id
        assert stream[-1].event_id == events[-1].event_id

    def test_getitem_slice_returns_event_stream(self) -> None:
        events = [_make_event() for _ in range(5)]
        stream = EventStream(events)
        sliced = stream[1:3]
        assert isinstance(sliced, EventStream)
        assert len(sliced) == 2
        assert sliced[0].event_id == events[1].event_id

    def test_getitem_out_of_range_raises(self) -> None:
        stream = EventStream([_make_event()])
        with pytest.raises(IndexError):
            _ = stream[99]


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------


class TestRepr:
    def test_repr_contains_event_count(self) -> None:
        stream = EventStream([_make_event() for _ in range(3)])
        assert "3" in repr(stream)

    def test_repr_empty_stream(self) -> None:
        stream = EventStream()
        assert "0" in repr(stream)


# ---------------------------------------------------------------------------
# __eq__
# ---------------------------------------------------------------------------


class TestEquality:
    def test_equal_streams(self) -> None:
        events = [_make_event() for _ in range(3)]
        assert EventStream(events) == EventStream(events)

    def test_unequal_streams(self) -> None:
        e1 = _make_event()
        e2 = _make_event()
        assert EventStream([e1]) != EventStream([e2])

    def test_not_equal_to_non_stream(self) -> None:
        stream = EventStream()
        result = stream.__eq__("not a stream")
        assert result is NotImplemented

    def test_empty_streams_equal(self) -> None:
        assert EventStream() == EventStream()


# ---------------------------------------------------------------------------
# Exporter protocol
# ---------------------------------------------------------------------------


class TestExporterProtocol:
    def test_mock_exporter_satisfies_protocol(self) -> None:
        assert isinstance(_MockExporter(), Exporter)

    def test_object_without_export_batch_does_not_satisfy_protocol(self) -> None:
        class NotAnExporter:
            ...
        assert not isinstance(NotAnExporter(), Exporter)
