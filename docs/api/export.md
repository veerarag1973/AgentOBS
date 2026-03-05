# tracium.export

Export backends for delivering tracium events to external systems.

All exporters implement the `Exporter` protocol from `tracium.stream`
(async `export_batch` method) and satisfy the `EventStream.drain()` / `route()` API.

See the [Export User Guide](../user_guide/export.md) for usage examples.

---

## `tracium.export.jsonl` — JSONL File Exporter

### `JSONLExporter`

```python
class JSONLExporter(
    path: Union[str, Path],
    mode: str = "a",
    encoding: str = "utf-8",
)
```

Async exporter that appends events as newline-delimited JSON.

- `path="-"` writes to stdout (useful for log pipelines).
- Async-safe: an `asyncio.Lock` serialises concurrent appends.
- Acts as an async context manager: `async with JSONLExporter(...) as e:`.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str \| Path` | — | File path or `"-"` for stdout. |
| `mode` | `str` | `"a"` | File open mode: `"a"` (append) or `"w"` (truncate). |
| `encoding` | `str` | `"utf-8"` | File encoding. |

**Raises:** `OSError` — if the file cannot be opened or written.

**Example:**

```python
from tracium.export.jsonl import JSONLExporter

async with JSONLExporter("events.jsonl") as exporter:
    for event in events:
        await exporter.export(event)
```

#### Methods

##### `async export(event: Event) -> None`

Append a single event as one JSON line.

**Raises:** `RuntimeError` — if the exporter has been closed. `OSError` — if the write fails.

##### `async export_batch(events: Sequence[Event]) -> int`

Append multiple events, one JSON line each.

**Returns:** `int` — number of events written.

**Raises:** `RuntimeError` — if the exporter has been closed. `OSError` — if the write fails.

##### `flush() -> None`

Flush internal write buffers to the OS. Safe to call before the file is opened.

##### `close() -> None`

Flush and close the underlying file handle. Idempotent. Does not close stdout.

---

## `tracium.export.otlp` — OTLP Exporter

### `ResourceAttributes`

```python
@dataclass(frozen=True)
class ResourceAttributes(
    service_name: str,
    deployment_environment: str = "production",
    extra: Dict[str, str] = field(default_factory=dict),
)
```

OTel resource attributes attached to every exported OTLP payload.

**Attributes:**

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `service_name` | `str` | — | Value for the `service.name` resource attribute. |
| `deployment_environment` | `str` | `"production"` | Value for `deployment.environment.name` (semconv 1.21+). |
| `extra` | `Dict[str, str]` | `{}` | Additional arbitrary resource attributes. |

#### Methods

##### `to_otlp() -> List[Dict[str, Any]]`

Return a list of OTLP `KeyValue` dicts for the resource.

---

### `OTLPExporter`

```python
class OTLPExporter(
    endpoint: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    resource_attrs: Optional[ResourceAttributes] = None,
    timeout: float = 5.0,
    batch_size: int = 500,
)
```

Async exporter that serialises events to OTLP/JSON and HTTP-POSTs to a collector.

- Events **with** `trace_id` → OTLP spans (`resourceSpans`).
- Events **without** `trace_id` → OTLP log records (`resourceLogs`).
- No `opentelemetry-sdk` dependency — stdlib-only HTTP transport.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `endpoint` | `str` | — | Full OTLP HTTP URL, e.g. `"http://otel-collector:4318/v1/traces"`. |
| `headers` | `Dict[str, str] \| None` | `None` | Optional extra HTTP request headers (e.g. API keys). |
| `resource_attrs` | `ResourceAttributes \| None` | `None` | OTel resource attributes. Defaults to `service_name="Tracium"`. |
| `timeout` | `float` | `5.0` | HTTP request timeout in seconds. |
| `batch_size` | `int` | `500` | Maximum events per `export_batch` call. |

**Example:**

```python
from tracium.export.otlp import OTLPExporter, ResourceAttributes

exporter = OTLPExporter(
    endpoint="http://localhost:4318/v1/traces",
    resource_attrs=ResourceAttributes(service_name="llm-trace"),
)
await exporter.export(event)
```

#### Methods

##### `to_otlp_span(event: Event) -> Dict[str, Any]`

Map a single event to an OTLP span dict (pure, no I/O).

The returned dict is fully compliant with the OTel specification:

- `kind: 3` (CLIENT)
- `traceFlags: 1` (sampled)
- `endTimeUnixNano` computed as `startTimeUnixNano + payload.duration_ms × 1 000 000`
- `status.code` / `status.message` mapped from `payload.status` (`"error"`/`"timeout"` → ERROR; `"ok"` → OK)
- `gen_ai.*` attributes (GenAI semconv 1.27+) populated from `payload.model_info`, `payload.token_usage`, and `payload.status`

If the event has no `span_id`, a deterministic synthetic ID is derived.
If the event has no `trace_id`, a zero-filled placeholder is used.

**Returns:** `Dict[str, Any]` — OTLP-compatible span dict.

##### `to_otlp_log(event: Event) -> Dict[str, Any]`

Map a single event to an OTLP log record dict (pure, no I/O).

**Returns:** `Dict[str, Any]` — OTLP-compatible log record dict.

##### `async export(event: Event) -> Dict[str, Any]`

Export a single event as an OTLP payload and HTTP POST it.

Span vs log selection is automatic based on `event.trace_id`.

**Returns:** `Dict[str, Any]` — the OTLP record dict that was sent.

**Raises:** `ExportError` — if the HTTP request fails.

##### `async export_batch(events: Sequence[Event]) -> List[Dict[str, Any]]`

Export a sequence of events, batching spans and log records into separate HTTP requests.

**Returns:** `List[Dict[str, Any]]` — list of OTLP record dicts.

**Raises:** `ExportError` — if any HTTP request fails.

---

### `make_traceparent`

```python
def make_traceparent(
    trace_id: str,
    span_id: str,
    *,
    sampled: bool = True,
) -> str
```

Construct a W3C TraceContext `traceparent` header (RFC 9429).

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `trace_id` | `str` | — | 32-char lowercase hex trace ID. |
| `span_id` | `str` | — | 16-char lowercase hex span ID. |
| `sampled` | `bool` | `True` | When `True`, sets the sampled flag (`-01`); otherwise `-00`. |

**Returns:** `str` — `"00-<trace_id>-<span_id>-01"` (or `-00`).

**Example:**

```python
from tracium.export.otlp import make_traceparent

header = make_traceparent(event.trace_id, event.span_id)
# → "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
```

---

### `extract_trace_context`

```python
def extract_trace_context(
    headers: Mapping[str, str],
) -> Optional[Dict[str, Any]]
```

Parse W3C TraceContext `traceparent` / `tracestate` headers.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `headers` | `Mapping[str, str]` | HTTP headers dict (case-insensitive lookup for `traceparent` / `tracestate`). |

**Returns:** `Dict[str, Any]` with keys `trace_id`, `span_id`, `sampled` (`bool`), and optionally `tracestate` (`str`) — or `None` if no valid `traceparent` header is present.

**Example:**

```python
from tracium.export.otlp import extract_trace_context

ctx = extract_trace_context({"traceparent": "00-abc...def-1234567890abcdef-01"})
# ctx = {"trace_id": "abc...def", "span_id": "1234567890abcdef", "sampled": True}
```

---

## `tracium.export.otel_bridge` — OTel SDK Bridge Exporter

Requires the `[otel]` optional extra: `pip install "tracium[otel]"`.

### `OTelBridgeExporter`

```python
class OTelBridgeExporter(
    tracer_name: str = "agentobs",
    tracer_version: str = "1.0",
)
```

Exports events through any configured OpenTelemetry `TracerProvider`. Unlike
`OTLPExporter` (which speaks the OTLP wire protocol directly), this bridge
delegates the entire span lifecycle to the OTel SDK — sampling, processors,
and all registered `SpanExporter` instances fire as normal.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tracer_name` | `str` | `"agentobs"` | Instrumentation scope name. |
| `tracer_version` | `str` | `"1.0"` | Instrumentation scope version. |

**Example:**

```python
from tracium.export.otel_bridge import OTelBridgeExporter

exporter = OTelBridgeExporter(tracer_name="my-service")
exporter.export(event)                # synchronous (starts + ends span immediately)
await exporter.export_batch(events)   # async batch variant
```

#### Methods

##### `export(event: Event) -> None`

Emit a single span via the active `TracerProvider`.

Sets `SpanKind.CLIENT`, maps `payload.status` to `StatusCode.OK/ERROR`, and
sets all `gen_ai.*` + `llm.*` attributes.

**Raises:** `ImportError` — if `opentelemetry-sdk` is not installed.

##### `async export_batch(events: Sequence[Event]) -> None`

Emit spans for all events via the active `TracerProvider`.

**Raises:** `ImportError` — if `opentelemetry-sdk` is not installed.

---

## `tracium.export.webhook` — Webhook Exporter

### `WebhookExporter`

```python
class WebhookExporter(
    url: str,
    *,
    secret: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    max_retries: int = 3,
    allow_private_addresses: bool = False,
)
```

Async exporter that sends events to an HTTP webhook endpoint.

- Single events are delivered as a JSON-encoded body.
- Batch events are delivered as a JSON array.
- Optional HMAC-SHA256 request signing via `X-AgentOBS-Signature` header.
- Retry logic uses truncated exponential back-off (1s, 2s, 4s … capped at 30s).
- The `secret` is **never** included in repr, logs, or exception messages.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | — | Destination webhook URL. |
| `secret` | `str \| None` | `None` | Optional HMAC-SHA256 signing secret. |
| `headers` | `Dict[str, str] \| None` | `None` | Optional extra HTTP request headers. |
| `timeout` | `float` | `10.0` | Per-request timeout in seconds. |
| `max_retries` | `int` | `3` | Maximum retry attempts on transient failures. |
| `allow_private_addresses` | `bool` | `False` | When `True`, allows posting to private/loopback IP addresses. |

**Example:**

```python
from tracium.export.webhook import WebhookExporter

exporter = WebhookExporter(
    url="https://hooks.example.com/events",
    secret="my-hmac-secret",
)
await exporter.export(event)
```

#### Methods

##### `async export(event: Event) -> None`

Export a single event as a JSON-encoded HTTP POST.

**Raises:** `ExportError` — if all retry attempts fail.

##### `async export_batch(events: Sequence[Event]) -> int`

Export multiple events as a JSON array in a single HTTP POST.

**Returns:** `int` — number of events sent.

**Raises:** `ExportError` — if all retry attempts fail.

> **Auto-documented module:** `tracium.export.webhook`

`WebhookExporter` — POSTs events as JSON to an arbitrary HTTP endpoint.

---

## `tracium.export.datadog` — Datadog APM Exporter

### `DatadogResourceAttributes`

```python
@dataclass(frozen=True)
class DatadogResourceAttributes:
    service: str
    env: str = "production"
    version: str = "0.0.0"
    extra: Dict[str, str] = field(default_factory=dict)
```

Datadog resource attributes attached to every trace span and metric series.

**Attributes:**

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `service` | `str` | — | Datadog service name. |
| `env` | `str` | `"production"` | Deployment environment tag (e.g. `"staging"`, `"production"`). |
| `version` | `str` | `"0.0.0"` | Service version tag. |
| `extra` | `Dict[str, str]` | `{}` | Additional arbitrary Datadog tags. |

#### Methods

##### `to_tags() -> List[str]`

Return a list of `"key:value"` strings for use as Datadog tags.

Always includes `service:<service>`, `env:<env>`, `version:<version>`, and any
keys from `extra`.

---

### `DatadogExporter`

```python
class DatadogExporter(
    service: str,
    env: str = "production",
    *,
    agent_url: str = "http://localhost:8126",
    api_key: Optional[str] = None,
    dd_site: Optional[str] = None,
    timeout: float = 10.0,
    allow_private_addresses: bool = False,
)
```

Async exporter that sends events to the Datadog Agent (traces) and to the
Datadog Metrics API.

- **Traces** — serialised via `to_dd_span()` and sent to `POST {agent_url}/v0.3/traces` using the Datadog Agent wire format.
- **Metrics** — extracted from trace payload numeric fields and sent to `POST https://api.{dd_site}/api/v2/series`.
- `api_key` is required for metric delivery; trace delivery goes to the local agent and does not need a key.
- No `ddtrace` dependency — stdlib-only HTTP transport.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `service` | `str` | — | Datadog service name. |
| `env` | `str` | `"production"` | Deployment environment. |
| `agent_url` | `str` | `"http://localhost:8126"` | Datadog Agent base URL. |
| `api_key` | `str \| None` | `None` | Datadog API key for metrics (optional). Required when exporting metrics. |
| `dd_site` | `str \| None` | `None` | Datadog site for metrics API (e.g. `"datadoghq.com"`). Required when `api_key` is set. |
| `timeout` | `float` | `10.0` | HTTP request timeout in seconds. |
| `allow_private_addresses` | `bool` | `False` | When `True`, allows posting to private/loopback IP addresses. |

**Default metric fields:** `cost_usd`, `token_count`, `input_tokens`,
`output_tokens`, `total_tokens`, `duration_ms`, `score`.

**Example:**

```python
from tracium.export.datadog import DatadogExporter

exporter = DatadogExporter(
    service="llm-gateway",
    env="production",
    agent_url="http://dd-agent:8126",
    api_key="dd-api-key",
)
await exporter.export(event)
await exporter.export_batch(events)
```

#### Methods

##### `to_dd_span(event: Event) -> Dict[str, Any]`

Map a single event to a Datadog trace span dict (pure, no I/O).

The span uses the following meta key convention — all LLM fields are placed
under the `llm.*` prefix inside `meta`:

```
span["meta"]["llm.source"]   → event.source
span["meta"]["llm.org_id"]   → event.org_id
span["meta"]["llm.team_id"]  → event.team_id
...
```

**Returns:** `Dict[str, Any]` — Datadog-compatible span dict.

##### `to_dd_metric_series(event: Event) -> List[Dict[str, Any]]`

Extract numeric payload fields as Datadog metric series descriptors.

Returns one metric series dict per recognised numeric field in
`event.payload`. Fields not in `metric_fields` are ignored.

**Returns:** `List[Dict[str, Any]]` — list of Datadog metric series dicts.

##### `async export(event: Event) -> None`

Send a single event as a Datadog trace span and (if applicable) metric series.

**Raises:** `ExportError("datadog-traces", ...)` or `ExportError("datadog-metrics", ...)` on failure.

##### `async export_batch(events: Sequence[Event]) -> None`

Send multiple events. Each is converted to a span and any metric series.

**Returns:** `None`.

**Raises:** `ExportError` on HTTP failure.

---

## `tracium.export.grafana` — Grafana Loki Exporter

### `GrafanaLokiExporter`

```python
class GrafanaLokiExporter(
    url: str,
    *,
    labels: Optional[Dict[str, str]] = None,
    include_envelope_labels: bool = True,
    tenant_id: Optional[str] = None,
    timeout: float = 10.0,
    allow_private_addresses: bool = False,
)
```

Async exporter that pushes events to a Grafana Loki instance via the
`POST /loki/api/v1/push` HTTP endpoint.

- Each event becomes a single Loki log entry.
- Stream labels default to `event_type` + any labels from `labels`; optionally
  enriched with envelope fields (`event_type`, `org_id`) when
  `include_envelope_labels=True`.
- Dots in `event_type` strings are sanitised to underscores for Loki label
  compatibility (e.g. `llm.trace.span.completed` → `llm_trace_span_completed`).
- Multi-tenant deployments set the `X-Scope-OrgID` header via `tenant_id`.
- No `grafana-client` dependency — stdlib-only HTTP transport.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | — | Loki push URL, e.g. `"http://loki:3100"`. |
| `labels` | `Dict[str, str] \| None` | `None` | Static labels added to every stream. |
| `include_envelope_labels` | `bool` | `True` | When `True`, adds `event_type` and `org_id` from the event envelope as stream labels. |
| `tenant_id` | `str \| None` | `None` | Value of the `X-Scope-OrgID` header for multi-tenant Loki. |
| `timeout` | `float` | `10.0` | HTTP request timeout in seconds. |
| `allow_private_addresses` | `bool` | `False` | When `True`, allows posting to private/loopback IP addresses. |

**Example:**

```python
from tracium.export.grafana import GrafanaLokiExporter

exporter = GrafanaLokiExporter(
    url="http://loki:3100",
    labels={"env": "production", "app": "llm-gateway"},
    tenant_id="my-org",
)
count = await exporter.export_batch(events)
print(f"Pushed {count} events to Loki")
```

#### Methods

##### `event_to_loki_entry(event: Event) -> Dict[str, Any]`

Convert a single event to a Loki push entry dict (pure, no I/O).

```json
{
  "stream": {"event_type": "llm_trace_span_completed", "env": "production"},
  "values": [["1772035200000000000", "{...json...}"]]
}
```

**Returns:** `Dict[str, Any]` — Loki stream entry.

##### `async export_batch(events: Sequence[Event]) -> int`

Push all events to Loki in a single HTTP request.

**Returns:** `int` — number of events pushed.

**Raises:** `ExportError("grafana-loki", ...)` — if the HTTP request fails.

##### `_iso_to_ns(timestamp: str) -> int` *(staticmethod)*

Convert an ISO-8601 timestamp string to UNIX nanoseconds.

---

## JSONL Backend

> **Auto-documented module:** `tracium.export.jsonl`

`JSONLExporter` — writes events as newline-delimited JSON to a local file.
