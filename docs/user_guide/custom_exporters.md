# Writing a Custom Exporter

AgentOBS is designed so that **any object with an `export(event)` method**
can act as an exporter.  This page walks through building one from scratch,
testing it, and registering it with the SDK.

---

## The `SyncExporter` protocol

The SDK expects a synchronous exporter that satisfies:

```python
class SyncExporter(Protocol):
    def export(self, event: Event) -> None: ...
```

That's it.  No base class, no ABC, no registration — pure duck typing.

An optional `close()` method is called by the SDK when the config is reset
(e.g. after a subsequent `configure()` call), so implement it if you hold
resources like file handles or network connections.

---

## Minimal example

```python
import json
from agentobs.event import Event

class PrintExporter:
    """Prints every event to stdout as pretty JSON — useful for debugging."""

    def export(self, event: Event) -> None:
        print(json.dumps(event.to_dict(), indent=2))
```

Register it using `MockExporter.installed()` or by calling
`agentobs._stream._reset_exporter()` after assigning to
`agentobs._stream._cached_exporter`.
For tests, the recommended approach is `MockExporter.installed()`
(see [Testing your exporter](#testing-your-exporter) below)::

```python
mock = PrintExporter()
import agentobs._stream as _stream
_stream._cached_exporter = mock
```

> **Note** \u2014 for production use, implement the `SyncExporter` protocol
> (any object with an `export(event)` method) and configure the SDK to use
> the appropriate built-in backend, or wrap your exporter in a stream via
> `agentobs.stream.EventStream`.

---

## Sending events to an HTTP endpoint

```python
import json
import urllib.request
from agentobs.event import Event
from agentobs.exceptions import ExportError


class MyHTTPExporter:
    """POST each event as JSON to *url*."""

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self._url = url
        self._timeout = timeout

    def export(self, event: Event) -> None:
        body = json.dumps(event.to_dict()).encode()
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                if resp.status >= 400:
                    raise ExportError(f"HTTP {resp.status} from {self._url}")
        except OSError as exc:
            raise ExportError(str(exc)) from exc
```

---

## Batching events

For high-throughput use cases, collect events in a list and flush in bulk.
The SDK calls `export()` once per event, but your exporter can buffer
internally:

```python
import threading
from agentobs.event import Event


class BatchExporter:
    def __init__(self, flush_size: int = 50) -> None:
        self._buf: list[Event] = []
        self._lock = threading.Lock()
        self._flush_size = flush_size

    def export(self, event: Event) -> None:
        with self._lock:
            self._buf.append(event)
            if len(self._buf) >= self._flush_size:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def close(self) -> None:
        self.flush()

    def _flush_locked(self) -> None:
        if not self._buf:
            return
        self._send_batch(list(self._buf))
        self._buf.clear()

    def _send_batch(self, events: list[Event]) -> None:
        # Replace with your real network call.
        for event in events:
            print(event.to_json())
```

---

## Testing your exporter

Use `agentobs.testing.MockExporter` as a reference, or write a simple
unit test using `agentobs.testing.capture_events`:

```python
from agentobs import configure
from agentobs.testing import MockExporter, capture_events

def test_my_exporter_receives_span(tracer):
    mock = MockExporter()
    with mock.installed():
        with tracer.span("my-op"):
            pass  # emits a span.completed event

    assert len(mock.events) == 1
    assert mock.events[0].payload["span_name"] == "my-op"
```

Or use the higher-level context manager:

```python
from agentobs.testing import capture_events

def test_span_emits_event(tracer):
    with capture_events() as events:
        with tracer.span("hello"):
            pass

    assert events[0].payload["status"] == "ok"
```

---

## Error handling

Raise `agentobs.exceptions.ExportError` for recoverable failures.  The SDK
will apply the configured `on_export_error` policy (`"warn"` by default) and
will automatically **retry** up to `export_max_retries` times (default: 3)
with exponential back-off before calling the error handler.

```python
from agentobs.exceptions import ExportError

class FlakyExporter:
    def export(self, event: Event) -> None:
        if not self._connected():
            raise ExportError("connection lost — will be retried by the SDK")
        ...
```

Configure retry behaviour:

```python
agentobs.configure(export_max_retries=5)
```

---

## Full API surface

| Config key | Type | Default | Description |
|---|---|---|---|
| `exporter` | `str` | `"console"` | Built-in exporter name (`"console"`, `"jsonl"`) |
| `on_export_error` | `str` | `"warn"` | `"warn"` \| `"raise"` \| `"drop"` |
| `export_max_retries` | `int` | `3` | Retry attempts (on `ExportError`) before calling the error handler |

See also: [Export Backends](export.md) for the built-in exporters.
