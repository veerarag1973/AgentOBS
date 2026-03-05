# tracium.stream

In-memory event stream with filtering, routing, and export capabilities.

`EventStream` is an ordered, immutable sequence of `Event` objects with a
fluent API for filtering and routing to export backends.

See the [Export User Guide](../user_guide/export.md) for usage examples.

---

## `Exporter` *(Protocol)*

```python
@runtime_checkable
class Exporter(Protocol)
```

Structural protocol for exporters accepted by `EventStream`.

Any object with an async `export_batch` method satisfies this protocol. All
built-in exporters (`JSONLExporter`, `OTLPExporter`, `WebhookExporter`) implement it.

### Methods

#### `async export_batch(events: Sequence[Event]) -> Any`

Export a sequence of events.

---

## `EventStream`

```python
class EventStream(events: Optional[Iterable[Event]] = None)
```

An immutable, ordered sequence of `Event` objects.

All filter methods return a **new** `EventStream` without modifying the original.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `events` | `Iterable[Event] \| None` | Initial events. Defaults to an empty stream. |

**Example:**

```python
from tracium.stream import EventStream

stream = EventStream([event1, event2, event3])
filtered = stream.filter_by_type("llm.trace.span.completed")
await filtered.drain(exporter)
```

### Class method constructors

#### `from_file(path, *, encoding="utf-8", skip_errors=False) -> EventStream` *(classmethod)*

Load events from a JSONL file.

Each non-empty line is deserialized with `Event.from_json()`.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str \| Path` | - | Path to a `.jsonl` file. |
| `encoding` | `str` | `"utf-8"` | File encoding. |
| `skip_errors` | `bool` | `False` | When `True`, silently skip malformed lines instead of raising. |

**Returns:** `EventStream`

**Raises:** `DeserializationError` - on the first malformed line when `skip_errors=False`. `OSError` - if the file cannot be opened.

---

#### `from_queue(q, *, sentinel=None) -> EventStream` *(classmethod)*

Drain a synchronous `queue.Queue` into an `EventStream`.

Non-blocking: uses `get_nowait()` so this returns immediately once the queue is drained.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | `queue.Queue[Event]` | - | Queue of `Event` objects. |
| `sentinel` | `object` | `None` | Stop-value that signals end-of-stream. Not added to the stream. |

**Returns:** `EventStream`

---

#### `async from_async_queue(q, *, sentinel=None) -> EventStream` *(classmethod)*

Drain an `asyncio.Queue` into an `EventStream`.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | `asyncio.Queue[Event]` | - | Async queue of `Event` objects. |
| `sentinel` | `object` | `None` | Stop-value. Not added to the stream. |

**Returns:** `EventStream`

---

#### `async from_async_iter(aiter) -> EventStream` *(classmethod)*

Consume an async iterator into an `EventStream`.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `aiter` | `AsyncIterator[Event]` | Any async iterator of events. |

**Returns:** `EventStream`

---

#### `from_kafka(topic, bootstrap_servers, *, group_id=None, sentinel=None, max_messages=None, poll_timeout_ms=1000, skip_errors=False) -> EventStream` *(classmethod)*

Drain a Kafka topic into an `EventStream`.

Requires `kafka-python>=2.0`. Raises `ImportError` with an installation hint
if `kafka-python` is not installed.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `topic` | `str` | - | Kafka topic name to consume. |
| `bootstrap_servers` | `str \| List[str]` | - | Kafka broker address(es), e.g. `"localhost:9092"`. |
| `group_id` | `str \| None` | `None` | Consumer group ID. `None` = no group (earliest offset). |
| `sentinel` | `object` | `None` | Stop-value that signals end-of-stream. Not added to the stream. |
| `max_messages` | `int \| None` | `None` | Maximum number of messages to consume before stopping. `None` = drain until sentinel or topic exhaustion. |
| `poll_timeout_ms` | `int` | `1000` | Kafka poll timeout in milliseconds. |
| `skip_errors` | `bool` | `False` | When `True`, silently skip messages that cannot be deserialised. |

**Returns:** `EventStream`

**Raises:**
- `ImportError` - if `kafka-python` is not installed.
- `DeserializationError` - on a malformed message when `skip_errors=False`.

**Example:**

```python
from tracium.stream import EventStream

stream = EventStream.from_kafka(
    topic="llm-events",
    bootstrap_servers="localhost:9092",
    group_id="analytics-consumer",
    max_messages=1000,
)
await stream.drain(exporter)
```

---

### Filtering methods

#### `filter(predicate: Callable[[Event], bool]) -> EventStream`

Return a new stream containing only events for which `predicate` returns `True`.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `predicate` | `Callable[[Event], bool]` | A callable that returns `True` to keep the event. |

**Returns:** New `EventStream`.

---

#### `filter_by_type(*event_types: str) -> EventStream`

Return a new stream containing only events whose `event_type` matches one of
the supplied strings (exact match).

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `*event_types` | `str` | One or more event type strings. |

**Returns:** New `EventStream`.

---

#### `filter_by_tags(**tags: str) -> EventStream`

Return a new stream keeping only events whose tags include **all** supplied
key-value pairs.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `**tags` | `str` | Tag key=value pairs that must all be present on the event. |

**Returns:** New `EventStream`.

---

### Export methods

#### `async route(exporter: Exporter, predicate=None) -> int`

Dispatch matching events to `exporter` as a single batch.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `exporter` | `Exporter` | - | Any object with an async `export_batch` method. |
| `predicate` | `Callable[[Event], bool] \| None` | `None` | Optional filter. When `None`, all events are sent. |

**Returns:** `int` - number of events dispatched.

---

#### `async drain(exporter: Exporter) -> int`

Export all events in this stream to `exporter`.

Equivalent to `await stream.route(exporter)`.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `exporter` | `Exporter` | Target exporter. |

**Returns:** `int` - number of events exported.

---

### Sequence interface

`EventStream` supports the standard sequence protocol:

| Method / Operation | Description |
|-------------------|-------------|
| `len(stream)` | Number of events. |
| `stream[i]` | Get event at index `i`. Returns `Event`. |
| `stream[i:j]` | Get a slice. Returns a new `EventStream`. |
| `for event in stream` | Iterate over events. |
| `stream == other` | Equality comparison with another `EventStream`. |

---

## Module-level helpers

### `iter_file(path, *, encoding="utf-8", skip_errors=False) -> Iterator[Event]`

Yield `Event` objects from a NDJSON file one at a time (constant memory overhead).

Unlike `EventStream.from_file()`, this is a **generator** - events are parsed
and yielded individually without loading the entire file into memory.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str \| Path` | - | Path to the NDJSON file. |
| `encoding` | `str` | `"utf-8"` | File encoding. |
| `skip_errors` | `bool` | `False` | When `True`, silently skip malformed lines. |

**Yields:** `Event`

**Raises:** `DeserializationError` - on the first malformed line when `skip_errors=False`.

**Example:**

```python
from tracium.stream import iter_file

for event in iter_file("events.ndjson"):
    process(event)
```

---

### `async aiter_file(path, *, encoding="utf-8", skip_errors=False) -> AsyncIterator[Event]`

Async generator equivalent of `iter_file`.

Reads the file via `asyncio.to_thread` to avoid blocking the event loop on I/O.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str \| Path` | - | Path to the NDJSON file. |
| `encoding` | `str` | `"utf-8"` | File encoding. |
| `skip_errors` | `bool` | `False` | When `True`, silently skip malformed lines. |

**Yields:** `Event`

**Raises:** `DeserializationError` - on the first malformed line when `skip_errors=False`.

**Example:**

```python
from tracium.stream import aiter_file

async for event in aiter_file("events.ndjson"):
    await process(event)
```
