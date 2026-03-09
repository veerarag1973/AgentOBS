"""In-memory event stream with filtering and routing.

:class:`EventStream` is an ordered, immutable sequence of
:class:`~agentobs.event.Event` objects with a fluent API for filtering and
routing to export backends.

Usage examples
--------------
**Build from a list**::

    stream = EventStream([event1, event2, event3])

**Filter**::

    errors = stream.filter(lambda e: "error" in e.payload)
    llm_trace = stream.filter_by_type("llm.trace.span.completed")

**Route to an exporter**::

    exporter = JSONLExporter("errors.jsonl")
    await stream.route(exporter, lambda e: e.event_type.startswith("llm.error"))

**Drain to an exporter (export all)**::

    await stream.drain(exporter)

**Load from a JSONL file**::

    stream = EventStream.from_file("audit.jsonl")

**Load from an asyncio.Queue**::

    stream = await EventStream.from_async_queue(queue)
"""

from __future__ import annotations

import asyncio
import queue as stdlib_queue
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Protocol,
    runtime_checkable,
)

from agentobs.event import Event

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator, Sequence

__all__ = ["EventStream", "Exporter", "aiter_file", "iter_file"]


# ---------------------------------------------------------------------------
# Exporter protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Exporter(Protocol):
    """Structural protocol for exporters accepted by :class:`EventStream`.

    Any object with an async ``export_batch`` method satisfies this protocol.
    All built-in exporters (:class:`~agentobs.export.otlp.OTLPExporter`,
    :class:`~agentobs.export.webhook.WebhookExporter`,
    :class:`~agentobs.export.jsonl.JSONLExporter`) implement it.
    """

    async def export_batch(self, events: Sequence[Event]) -> Any:  # noqa: ANN401
        """Export a sequence of events."""
        ...


# ---------------------------------------------------------------------------
# EventStream
# ---------------------------------------------------------------------------


class EventStream:
    """An immutable, ordered sequence of :class:`~agentobs.event.Event` objects.

    All methods that return a subset (``filter``, ``filter_by_type``,
    ``filter_by_tags``) return a **new** :class:`EventStream` without
    modifying the original.

    Args:
        events: Initial sequence of events.  Defaults to an empty stream.

    Example::

        stream = EventStream([event1, event2, event3])
        filtered = stream.filter_by_type("llm.trace.span.completed")
        await filtered.drain(exporter)
    """

    def __init__(self, events: Iterable[Event] | None = None) -> None:
        self._events: list[Event] = list(events) if events is not None else []

    # ------------------------------------------------------------------
    # Class-method constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        encoding: str = "utf-8",
        skip_errors: bool = False,
    ) -> EventStream:
        """Load events from a JSONL file.

        Each non-empty line is deserialized with
        :meth:`~agentobs.event.Event.from_json`.  Lines that fail to
        deserialize are skipped when ``skip_errors=True``; by default they
        raise :class:`~agentobs.exceptions.DeserializationError`.

        Args:
            path:        Path to a ``.jsonl`` file.
            encoding:    File encoding (default ``"utf-8"``).
            skip_errors: When ``True``, silently skip malformed lines instead
                         of raising.

        Returns:
            A new :class:`EventStream` with the loaded events.

        Raises:
            DeserializationError: On the first malformed line when
                ``skip_errors=False`` (default).
            OSError: If the file cannot be opened.
        """
        from agentobs.exceptions import DeserializationError, LLMSchemaError  # noqa: PLC0415

        events: list[Event] = []
        with Path(str(path)).open(encoding=encoding) as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    events.append(Event.from_json(line))
                except (LLMSchemaError, ValueError) as exc:
                    if skip_errors:
                        continue
                    raise DeserializationError(
                        reason=f"line {lineno}: {exc}",
                        source_hint=str(path),
                    ) from exc
        return cls(events)

    @classmethod
    def from_queue(
        cls,
        q: stdlib_queue.Queue[Event],
        *,
        sentinel: object = None,
    ) -> EventStream:
        """Drain a synchronous :class:`queue.Queue` into an EventStream.

        Reads items from *q* until the queue is empty or a *sentinel* value is
        encountered.  Non-blocking: uses :meth:`queue.Queue.get_nowait` so this
        method returns immediately once the queue is drained.

        Args:
            q:        A :class:`queue.Queue` containing
                      :class:`~agentobs.event.Event` objects.
            sentinel: Stop-value that signals end-of-stream.  The sentinel
                      itself is not added to the stream.  Defaults to ``None``.

        Returns:
            A new :class:`EventStream` with all events drained from the queue.
        """
        events: list[Event] = []
        while True:
            try:
                item = q.get_nowait()
            except stdlib_queue.Empty:
                break
            if item is sentinel:
                break
            events.append(item)
        return cls(events)

    @classmethod
    async def from_async_queue(
        cls,
        q: asyncio.Queue[Event],
        *,
        sentinel: object = None,
    ) -> EventStream:
        """Drain an :class:`asyncio.Queue` into an EventStream.

        Awaits items from *q* until the *sentinel* value is received.  The
        sentinel itself is not added to the stream.

        Args:
            q:        An :class:`asyncio.Queue` containing
                      :class:`~agentobs.event.Event` objects.
            sentinel: Stop-value (default ``None``).

        Returns:
            A new :class:`EventStream` with all events from the queue.
        """
        events: list[Event] = []
        while True:
            item = await q.get()
            if item is sentinel:
                break
            events.append(item)
        return cls(events)

    @classmethod
    async def from_async_iter(
        cls,
        async_iter: "AsyncIterator[Event]",
    ) -> "EventStream":
        """Consume an async iterator into an EventStream.

        Args:
            async_iter: Any :class:`~typing.AsyncIterator` of events.

        Returns:
            A new :class:`EventStream`.
        """
        return cls([event async for event in async_iter])

    @classmethod
    def from_kafka(  # noqa: PLR0913
        cls,
        topic: str,
        bootstrap_servers: str | list[str],
        *,
        group_id: str | None = None,
        sentinel: object = None,
        max_messages: int | None = None,
        poll_timeout_ms: int = 1000,
        skip_errors: bool = False,
    ) -> EventStream:
        """Consume messages from a Kafka topic into an EventStream.

        Each Kafka message value is deserialised as a UTF-8 JSON string and
        parsed with :meth:`~agentobs.event.Event.from_json`.

        Requires ``kafka-python >= 2.0`` to be installed.  Install it with::

            pip install "agentobs[kafka]"

        Consumption stops when:

        * A *sentinel* message value is received (not added to stream).
        * *max_messages* events have been collected (when set).
        * The topic-partition reaches the end-of-partition offset and there
          are no more messages within *poll_timeout_ms* (``StopIteration``
          from the consumer is caught automatically).

        Args:
            topic:             Kafka topic name to consume from.
            bootstrap_servers: Kafka broker address(es),
                               e.g. ``"localhost:9092"`` or
                               ``["broker1:9092", "broker2:9092"]``.
            group_id:          Consumer group ID.  ``None`` creates an
                               anonymous (uncoordinated) consumer.
            sentinel:          Message value (decoded UTF-8 string) that
                               signals end-of-stream.  The sentinel message
                               is not added to the returned stream.
            max_messages:      Maximum number of events to collect.  ``None``
                               means no limit.
            poll_timeout_ms:   Milliseconds to wait for messages in each poll
                               (default 1 000 ms).
            skip_errors:       When ``True``, silently skip messages that fail
                               to deserialise instead of raising.

        Returns:
            A new :class:`EventStream` with all consumed events.

        Raises:
            ImportError: If ``kafka-python`` is not installed.
            DeserializationError: On the first malformed message when
                ``skip_errors=False`` (default).

        Example::

            stream = EventStream.from_kafka(
                "llm-events",
                "localhost:9092",
                group_id="analytics-pipeline",
                max_messages=1000,
            )
        """
        try:
            from kafka import KafkaConsumer  # type: ignore[import-untyped]  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "kafka-python is required for EventStream.from_kafka(). "
                'Install it with: pip install "agentobs[kafka]"'
            ) from exc

        from agentobs.exceptions import DeserializationError, LLMSchemaError  # noqa: PLC0415

        consumer: Any = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            consumer_timeout_ms=poll_timeout_ms,
            value_deserializer=lambda m: m.decode("utf-8"),
            auto_offset_reset="earliest",
            enable_auto_commit=group_id is not None,
        )

        events: list[Event] = []
        try:
            for message in consumer:
                value = message.value
                if value == sentinel:
                    break
                try:
                    events.append(Event.from_json(value))
                except (LLMSchemaError, ValueError) as exc:
                    if skip_errors:
                        continue
                    raise DeserializationError(
                        reason=f"Kafka message offset {message.offset}: {exc}",
                        source_hint=f"topic={topic}",
                    ) from exc
                if max_messages is not None and len(events) >= max_messages:
                    break
        finally:
            consumer.close()

        return cls(events)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def filter(
        self,
        predicate: Callable[[Event], bool],
    ) -> EventStream:
        """Return a new stream containing only events for which *predicate* returns ``True``.

        Args:
            predicate: A callable that accepts an :class:`~agentobs.event.Event`
                       and returns ``True`` to keep the event.

        Returns:
            New :class:`EventStream`.
        """
        return EventStream(e for e in self._events if predicate(e))

    def filter_by_type(self, *event_types: str) -> EventStream:
        """Return a new stream with events matching one of the supplied ``event_type`` strings.

        Args:
            *event_types: One or more event type strings.

        Returns:
            New :class:`EventStream`.
        """
        type_set = frozenset(event_types)
        return EventStream(e for e in self._events if e.event_type in type_set)

    def filter_by_tags(self, **tags: str) -> EventStream:
        """Return a filtered stream keeping only events whose tags include all supplied key-value pairs.

        Args:
            **tags: Tag key=value pairs that must all be present.

        Returns:
            New :class:`EventStream`.
        """  # noqa: E501
        def _matches(event: Event) -> bool:
            if event.tags is None:
                return False
            tag_dict = event.tags.to_dict()
            return all(tag_dict.get(k) == v for k, v in tags.items())

        return EventStream(e for e in self._events if _matches(e))

    # ------------------------------------------------------------------
    # Routing & export
    # ------------------------------------------------------------------

    async def route(
        self,
        exporter: Exporter,
        predicate: Callable[[Event], bool] | None = None,
    ) -> int:
        """Dispatch matching events to *exporter* as a single batch.

        Args:
            exporter:  Any object satisfying the :class:`Exporter` protocol
                       (has an async ``export_batch`` method).
            predicate: Optional filter.  When ``None`` all events are sent.

        Returns:
            Number of events dispatched.
        """
        subset = self._events if predicate is None else [e for e in self._events if predicate(e)]

        if subset:
            await exporter.export_batch(subset)
        return len(subset)

    async def drain(self, exporter: Exporter) -> int:
        """Export all events in this stream to *exporter*.

        Equivalent to ``await stream.route(exporter)``.

        Args:
            exporter: Target exporter.

        Returns:
            Number of events exported.
        """
        return await self.route(exporter)

    # ------------------------------------------------------------------
    # Sequence protocol
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[Event]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __getitem__(self, index: int | slice) -> Event | EventStream:
        result = self._events[index]
        if isinstance(index, slice):
            return EventStream(result)  # type: ignore[arg-type]
        return result  # type: ignore[return-value]

    def __repr__(self) -> str:
        return f"EventStream({len(self._events)} events)"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EventStream):
            return NotImplemented
        return self._events == other._events

    __hash__: None = None  # EventStream is unhashable (mutable container)


# ---------------------------------------------------------------------------
# Module-level streaming generators (avoid full in-memory accumulation)
# ---------------------------------------------------------------------------


def iter_file(
    path: str | Path,
    *,
    encoding: str = "utf-8",
    skip_errors: bool = False,
) -> Iterator[Event]:
    """Yield :class:`~agentobs.event.Event` objects from a NDJSON file one at a time.

    Unlike :meth:`EventStream.from_file`, this function is a **generator**;
    each event is parsed and yielded individually so that very large log files
    can be processed with constant memory overhead.

    Args:
        path:         Path to the NDJSON file.
        encoding:     File encoding (default ``"utf-8"``).
        skip_errors:  When ``True``, lines that fail to parse are silently
                      skipped instead of raising.

    Yields:
        Parsed :class:`~agentobs.event.Event` instances.

    Raises:
        DeserializationError: On the first malformed line when
            ``skip_errors=False`` (default).

    Example::

        for event in iter_file("events.ndjson"):
            process(event)
    """
    from agentobs.exceptions import DeserializationError, LLMSchemaError  # noqa: PLC0415

    with Path(path).open(encoding=encoding) as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                yield Event.from_json(line)
            except (LLMSchemaError, ValueError) as exc:
                if skip_errors:
                    continue
                raise DeserializationError(
                    reason=f"Line {lineno}: {exc}",
                    source_hint=str(path),
                ) from exc


async def aiter_file(
    path: str | Path,
    *,
    encoding: str = "utf-8",
    skip_errors: bool = False,
) -> AsyncIterator[Event]:
    """Async generator equivalent of :func:`iter_file`.

    Reads a newline-delimited JSON file line-by-line using
    :func:`asyncio.to_thread` to avoid blocking the event loop on I/O,
    yielding one :class:`~agentobs.event.Event` at a time.

    Args:
        path:         Path to the NDJSON file.
        encoding:     File encoding (default ``"utf-8"``).
        skip_errors:  When ``True``, lines that fail to parse are silently
                      skipped instead of raising.

    Yields:
        Parsed :class:`~agentobs.event.Event` instances.

    Raises:
        DeserializationError: On the first malformed line when
            ``skip_errors=False`` (default).

    Example::

        async for event in aiter_file("events.ndjson"):
            await process(event)
    """
    from agentobs.exceptions import DeserializationError, LLMSchemaError  # noqa: PLC0415

    lines: list[str] = await asyncio.to_thread(
        lambda: Path(path).read_text(encoding=encoding).splitlines()
    )
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            yield Event.from_json(line)
        except (LLMSchemaError, ValueError) as exc:
            if skip_errors:
                continue
            raise DeserializationError(
                reason=f"Line {lineno}: {exc}",
                source_hint=str(path),
            ) from exc
