# agentobs.testing

First-class test utilities for writing unit tests against your AI pipeline
without connecting to real exporters or an external observability backend.

---

## `MockExporter`

```python
class MockExporter:
    events: list[Event]
    export_count: int
    ...
```

A synchronous exporter that collects every exported event in memory.
Optionally raises on export to test error-handling paths.

```python
from agentobs.testing import MockExporter
from agentobs import configure

exporter = MockExporter()
configure(exporter=exporter)

# run your agent code here ...

assert exporter.export_count == 3
assert exporter.events[0].event_type == "llm.trace.span.completed"
```

### Constructor

```python
MockExporter(
    raise_on_export: type[Exception] | None = None,
    max_events: int | None = None,
)
```

| Parameter | Description |
|-----------|-------------|
| `raise_on_export` | If set to an `Exception` subclass, `export()` raises that type on every call. |
| `max_events` | If set, raises `RuntimeError` after more than `max_events` calls. |

### Methods

| Method | Description |
|--------|-------------|
| `export(event)` | Store `event`; raise if `raise_on_export` is set. |
| `export_batch(events)` | Async batch export (stores all events). |
| `clear()` | Reset `events` list and `export_count` counter. |
| `filter_by_type(event_type)` | Return only events matching `event_type`. |
| `installed()` | Context manager: install this exporter as the active exporter for the duration of the `with` block; restore previous exporter on exit. |

### `MockExporter.installed()` context manager

```python
with MockExporter().installed() as mock:
    run_my_agent()
    span_events = mock.filter_by_type("llm.trace.span.completed")
    assert len(span_events) == 1
```

---

## `capture_events()`

```python
@contextmanager
def capture_events() -> Generator[list[Event], None, None]:
```

Context manager that installs a fresh `MockExporter` and yields the live
event list. Restores the previous exporter on exit.

```python
from agentobs.testing import capture_events

with capture_events() as events:
    run_my_agent()

assert any(e.event_type == "llm.trace.span.completed" for e in events)
```

---

## `assert_event_schema_valid()`

```python
def assert_event_schema_valid(event: Event) -> None:
```

Assert that `event` passes JSON Schema validation against the published
AgentOBS v2.0 schema. Raises `AssertionError` with a descriptive message
on failure.

```python
from agentobs.testing import assert_event_schema_valid
from agentobs import Event, EventType

event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="test@1.0.0",
    payload={"span_name": "test", "status": "ok"},
)
assert_event_schema_valid(event)   # passes silently
```

---

## `trace_store()`

```python
@contextmanager
def trace_store(max_traces: int = 100) -> Generator[TraceStore, None, None]:
```

Context manager that installs a fresh, isolated `TraceStore` for the duration
of a `with` block and restores the previous singleton on exit. Useful for
isolating trace storage between test cases.

```python
from agentobs.testing import trace_store

def test_trace_recorded():
    with trace_store() as store:
        run_my_agent(trace_id="abc123")
        trace_events = store.get_trace("abc123")
        assert trace_events is not None
```

---

## Re-exports

```python
import agentobs.testing as testing

testing.MockExporter
testing.capture_events
testing.assert_event_schema_valid
testing.trace_store
```
