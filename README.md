<h1 align="center">AgentOBS</h1>

<p align="center">
  <strong>The reference implementation of the AGENTOBS Standard.</strong><br/>
  A lightweight Python SDK that gives your AI applications a common, structured way to record, sign, redact, and export events — with zero mandatory dependencies.
</p>

<p align="center">
  <em>AGENTOBS (RFC-0001) is the open event-schema standard for observability of agentic AI systems.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9%2B-4c8cbf?logo=python&logoColor=white" alt="Python 3.9+"/>
  <a href="https://pypi.org/project/agentobs/"><img src="https://img.shields.io/pypi/v/agentobs?color=4c8cbf&logo=pypi&logoColor=white" alt="PyPI"/></a>
  <a href="https://www.getspanforge.com/standard"><img src="https://img.shields.io/badge/standard-AGENTOBS_RFC--0001-4c8cbf" alt="AGENTOBS RFC-0001"/></a>
  <img src="https://img.shields.io/badge/coverage-97%25-brightgreen" alt="97% test coverage"/>
  <img src="https://img.shields.io/badge/tests-2407%20passing-brightgreen" alt="2407 tests"/>
  <img src="https://img.shields.io/badge/dependencies-zero-brightgreen" alt="Zero dependencies"/>
  <a href="docs/index.md"><img src="https://img.shields.io/badge/docs-local-4c8cbf" alt="Documentation"/></a>
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license"/>
</p>

---

## What is this?

**AgentOBS** (``agentobs``) is the **reference implementation of [RFC-0001 AGENTOBS](https://www.getspanforge.com/standard)** — the open event-schema standard for observability of agentic AI systems.

AGENTOBS defines a structured, typed event envelope that every LLM-adjacent instrumentation tool can emit and every observability backend can consume. It covers the full lifecycle: event envelopes, agent span hierarchies, token and cost models, HMAC audit chains, PII redaction, OTLP-compatible export, and schema governance.

> Think of **AgentOBS** as a **universal receipt format** for your AI application.
> Every time your app calls a language model, makes a decision, redacts private data, or checks a guardrail — this library gives that action a consistent, structured record that any tool in your stack can read.

---

## Why use it?

Without a shared schema, every team invents their own log format. With ``agentobs`` (and the AGENTOBS standard it implements), your logs, dashboards, compliance reports, and monitoring tools all speak the same language — automatically.

| Without AgentOBS | With AgentOBS |
|---|---|
| Each service logs events differently | Every event follows the same structure |
| Hard to audit who saw what data | Built-in HMAC signing creates a tamper-proof audit trail |
| PII scattered across logs | First-class PII redaction before data leaves your app |
| Vendor-specific observability | OpenTelemetry-compatible — works with any monitoring stack |
| No way to check compatibility | CLI + programmatic compliance checks in CI |
| Complex integration glue | Zero required dependencies — just ``pip install`` |

---

## Install

```bash
pip install agentobs
```

```python
import agentobs  # distribution name is agentobs, import name is agentobs
```

**Requires Python 3.9 or later.** No other packages are required for core usage.

> **Note:** The PyPI distribution is named `agentobs`. The Python import name remains `agentobs`.

### Optional extras

```bash
pip install "agentobs[jsonschema]"   # strict JSON Schema validation
pip install "agentobs[openai]"       # OpenAI auto-instrumentation (patch/unpatch)
pip install "agentobs[http]"         # Webhook + OTLP export
pip install "agentobs[pydantic]"     # Pydantic v2 model layer
pip install "agentobs[otel]"         # OpenTelemetry SDK integration
pip install "agentobs[kafka]"        # EventStream.from_kafka() via kafka-python
pip install "agentobs[langchain]"    # LangChain callback handler
pip install "agentobs[llamaindex]"   # LlamaIndex event handler
pip install "agentobs[datadog]"      # Datadog APM + metrics exporter
pip install "agentobs[all]"          # everything above
```

---

## Five-minute tour

### 1 — Trace an LLM call with the span API

```python
import agentobs

agentobs.configure(exporter="console", service_name="my-agent")

with agentobs.span("call-llm") as span:
    span.set_model(model="gpt-4o", system="openai")
    result = call_llm(prompt)                          # your LLM call here
    span.set_token_usage(input=512, output=128, total=640)
    span.set_status("ok")
```

The context manager automatically records start/end times, parent-child span relationships, and emits a structured event when it exits.

---

### 1b — Auto-instrument the OpenAI client (zero boilerplate)

```python
from agentobs.integrations import openai as openai_integration
import openai, agentobs

# One-time setup: patch the OpenAI SDK
openai_integration.patch()

agentobs.configure(exporter="console", service_name="my-agent")

client = openai.OpenAI()

with agentobs.tracer.span("chat-gpt4o") as span:
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello"}],
    )
    # span.token_usage, span.cost, and span.model are now populated automatically
```

`patch()` wraps every `client.chat.completions.create()` call (sync and async)
so that `token_usage`, `cost`, and `model` are auto-populated on the active span
from the API response — no per-call boilerplate required.

```python
# Restore original behaviour when you're done
openai_integration.unpatch()
```

---

### 2 — Record a raw event

```python
from agentobs import Event, EventType, Tags

event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="my-app@1.0.0",          # who emitted this
    org_id="org_acme",              # your organisation
    payload={
        "model": "gpt-4o",
        "prompt_tokens": 512,
        "completion_tokens": 128,
        "latency_ms": 340.5,
    },
    tags=Tags(env="production"),
)

event.validate()         # raises if structure is invalid
print(event.to_json())   # compact JSON string, ready to store or ship
```

Every event gets a **ULID** (a time-sortable unique ID) automatically — no need to generate one yourself.

---

### 3 — Redact private information before logging

```python
from agentobs import Event, EventType
from agentobs.redact import Redactable, RedactionPolicy, Sensitivity

policy = RedactionPolicy(min_sensitivity=Sensitivity.PII, redacted_by="policy:gdpr-v1")

# Wrap any string that might contain PII
event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="my-app@1.0.0",
    payload={"prompt": Redactable("Call me at 555-867-5309", Sensitivity.PII)},
)
result = policy.apply(event)
# result.event.payload["prompt"] -> "[REDACTED by policy:gdpr-v1]"
```

``Redactable`` is a string wrapper. You mark fields as sensitive at the point where they are created; the policy decides what to remove before the event is written to any log.

> **Tip — auto-redact every span:** pass `redaction_policy=policy` to
> `agentobs.configure()` and the policy runs automatically inside `_dispatch()`
> before any exporter sees the event.

---

### 4 — Sign events for tamper-proof audit trails

```python
from agentobs.signing import sign, verify_chain, AuditStream

# Sign a single event
signed = sign(event, org_secret="my-org-secret")

# Or build a chain — every event references the one before it,
# so any gap or modification is immediately detectable.
stream = AuditStream(org_secret="my-org-secret")
for e in events:
    stream.append(e)

result = verify_chain(stream.events, org_secret="my-org-secret")
```

This is the same principle used in certificate chains and blockchain — each event's signature covers the previous event's signature, so you cannot alter history without breaking the chain.
> **Tip — auto-sign every span:** pass `signing_key="your-secret"` to
> `agentobs.configure()` and every emitted span is signed and chained
> automatically, with no per-event boilerplate.
---

### 5 — Export to anywhere

```python
from agentobs.stream import EventStream
from agentobs.export.jsonl import JSONLExporter
from agentobs.export.webhook import WebhookExporter
from agentobs.export.otlp import OTLPExporter
from agentobs.export.datadog import DatadogExporter
from agentobs.export.grafana import GrafanaLokiExporter

stream = EventStream(events)

# Write everything to a local file
await stream.drain(JSONLExporter("events.jsonl"))

# Ship to your OpenTelemetry collector
await stream.drain(OTLPExporter("http://otel-collector:4318/v1/traces"))

# Send to Datadog APM (traces + metrics)
await stream.drain(DatadogExporter(
    service="my-app",
    env="production",
    agent_url="http://dd-agent:8126",
    api_key="your-dd-api-key",
))

# Push to Grafana Loki
await stream.drain(GrafanaLokiExporter(
    url="http://loki:3100",
    labels={"app": "my-app", "env": "production"},
))

# Fan-out: guard-blocked events -> Slack webhook
await stream.route(
    WebhookExporter("https://hooks.slack.com/your-webhook"),
    predicate=lambda e: e.event_type == "llm.guard.blocked",
)
```

#### Kafka source

```python
from agentobs.stream import EventStream

# Drain a Kafka topic directly into an EventStream
stream = EventStream.from_kafka(
    topic="llm-events",
    bootstrap_servers="kafka:9092",
    group_id="analytics",
    max_messages=5000,
)
await stream.drain(exporter)
```

---

### 6 — Sync exporters for non-async workflows

```python
from agentobs.exporters.jsonl import SyncJSONLExporter
from agentobs.exporters.console import SyncConsoleExporter

# Log all events to a JSONL file synchronously
exporter = SyncJSONLExporter("events.jsonl")
exporter.export(event)
exporter.close()

# Pretty-print events to the terminal during development
console = SyncConsoleExporter()
console.export(event)
```

---

### 7 — Check compliance and inspect events from the command line

```bash
agentobs check-compat events.json        # v1.0 compatibility checklist
agentobs validate events.jsonl           # JSON Schema validation per event
agentobs audit-chain events.jsonl        # verify HMAC signing chain integrity
agentobs inspect <EVENT_ID> events.jsonl # pretty-print a single event
agentobs stats events.jsonl              # summary: counts, tokens, cost, timestamps
agentobs list-deprecated                 # list all deprecated event types
agentobs migration-roadmap [--json]      # v2 migration roadmap
agentobs check-consumers                 # consumer registry compatibility check
```

```
CHK-1  All required fields present          (500 / 500 events)
CHK-2  Event types valid                    (500 / 500 events)
CHK-3  Source identifiers well-formed       (500 / 500 events)
CHK-5  Event IDs are valid ULIDs            (500 / 500 events)
All checks passed.
```

Drop any of these into your CI pipeline to catch schema drift, signing failures, or schema-breaking migrations before they reach production.

---

## What is inside the box

<table>
<thead>
<tr><th>Module</th><th>What it does</th><th>For whom</th></tr>
</thead>
<tbody>
<tr>
  <td><code>agentobs.event</code></td>
  <td>The core <code>Event</code> envelope — the one structure all tools share</td>
  <td>Everyone</td>
</tr>
<tr>
  <td><code>agentobs.types</code></td>
  <td>All built-in event type strings (trace, cost, cache, eval, guard…)</td>
  <td>Everyone</td>
</tr>
<tr>
  <td><code>agentobs.config</code></td>
  <td><code>configure()</code> and <code>get_config()</code> — global SDK configuration</td>
  <td>Everyone</td>
</tr>
<tr>
  <td><code>agentobs._span</code></td>
  <td>Span, AgentRun, AgentStep context managers — the runtime tracing API</td>
  <td>App developers</td>
</tr>
<tr>
  <td><code>agentobs._cli</code></td>
  <td>8 CLI sub-commands: <code>check-compat</code>, <code>validate</code>, <code>audit-chain</code>, <code>inspect</code>, <code>stats</code>, <code>list-deprecated</code>, <code>migration-roadmap</code>, <code>check-consumers</code></td>
  <td>DevOps / CI teams</td>
</tr>
<tr>
  <td><code>agentobs.redact</code></td>
  <td>PII detection, sensitivity levels, redaction policies</td>
  <td>Data privacy / GDPR teams</td>
</tr>
<tr>
  <td><code>agentobs.signing</code></td>
  <td>HMAC-SHA256 event signing and tamper-evident audit chains</td>
  <td>Security / compliance teams</td>
</tr>
<tr>
  <td><code>agentobs.compliance</code></td>
  <td>Programmatic v2.0 compatibility checks — no pytest required</td>
  <td>Platform / DevOps teams</td>
</tr>
<tr>
  <td><code>agentobs.export</code></td>
  <td>Ship events to files (JSONL), HTTP webhooks, OTLP collectors, Datadog APM, or Grafana Loki</td>
  <td>Infra / observability teams</td>
</tr>
<tr>
  <td><code>agentobs.exporters</code></td>
  <td>Sync exporters — <code>SyncJSONLExporter</code> and <code>SyncConsoleExporter</code> for non-async code</td>
  <td>App developers</td>
</tr>
<tr>
  <td><code>agentobs.stream</code></td>
  <td>Fan-out router — one <code>drain()</code> call reaches multiple backends; Kafka source via <code>from_kafka()</code></td>
  <td>Platform engineers</td>
</tr>
<tr>
  <td><code>agentobs.validate</code></td>
  <td>JSON Schema validation against the published v2.0 schema</td>
  <td>All teams</td>
</tr>
<tr>
  <td><code>agentobs.consumer</code></td>
  <td>Declare schema-namespace dependencies; fail fast at startup if version requirements are not met</td>
  <td>Platform / integration teams</td>
</tr>
<tr>
  <td><code>agentobs.governance</code></td>
  <td>Policy-based event gating — block prohibited types, warn on deprecated usage, enforce custom rules</td>
  <td>Platform / compliance teams</td>
</tr>
<tr>
  <td><code>agentobs.deprecations</code></td>
  <td>Register and surface per-event-type deprecation notices at runtime</td>
  <td>Library maintainers</td>
</tr>
<tr>
  <td><code>agentobs.integrations</code></td>
  <td>Plug-in adapters for OpenAI (auto-instrumentation via <code>patch()</code>), LangChain, LlamaIndex, Anthropic, Groq, Ollama, and Together. <code>agentobs.integrations._pricing</code> ships a static USD/1M-token pricing table for all current OpenAI models.</td>
  <td>App developers</td>
</tr>
<tr>
  <td><code>agentobs.namespaces</code></td>
  <td>Typed payload dataclasses for all 10 built-in event namespaces</td>
  <td>Tool authors</td>
</tr>
<tr>
  <td><code>agentobs.models</code></td>
  <td>Optional Pydantic v2 models for teams that prefer validated schemas</td>
  <td>API / backend teams</td>
</tr>
</tbody>
</table>

---

## Event namespaces

Every event carries a ``payload`` — a dictionary whose shape is defined by the event's **namespace**. The ten built-in namespaces cover everything from raw model traces to safety guardrails:

| Namespace prefix | Dataclass | What it records |
|---|---|---|
| ``llm.trace.*`` | ``SpanPayload``, ``AgentRunPayload``, ``AgentStepPayload`` | Model call — tokens, latency, finish reason **(frozen v2)** |
| ``llm.cost.*`` | ``CostPayload`` | Per-call cost in USD |
| ``llm.cache.*`` | ``CachePayload`` | Cache hit/miss, backend, TTL |
| ``llm.eval.*`` | ``EvalScenarioPayload`` | Scores, labels, evaluator identity |
| ``llm.guard.*`` | ``GuardPayload`` | Safety classifier output, block decisions |
| ``llm.fence.*`` | ``FencePayload`` | Topic constraints, allow/block lists |
| ``llm.prompt.*`` | ``PromptPayload`` | Prompt template version, rendered text |
| ``llm.redact.*`` | ``RedactPayload`` | PII audit record — what was found and removed |
| ``llm.diff.*`` | ``DiffPayload`` | Prompt/response delta between two events |
| ``llm.template.*`` | ``TemplatePayload`` | Template registry metadata |

```python
from agentobs.namespaces.trace import SpanPayload
from agentobs import Event

payload = SpanPayload(
    span_name="call-llm",
    span_id="abc123",
    trace_id="def456",
    start_time_ns=1_000_000_000,
    end_time_ns=1_340_000_000,
    status="ok",
)

event = Event(
    event_type="llm.trace.span.completed",
    source="my-app@1.0.0",
    payload=payload.to_dict(),
)
```

---

## Quality standards

- **2 407 tests** — unit, integration, property-based (Hypothesis), and performance benchmarks
- **97 % line and branch coverage** — measured with ``pytest-cov``
- **Zero required dependencies** — the entire core runs on Python's standard library alone
- **Typed** — full ``py.typed`` marker; works with mypy and pyright out of the box
- **Frozen v2 trace schema** — ``llm.trace.*`` payload fields will never break between minor releases

---

## Project structure

```
agentobs/
├── __init__.py       <- Public API surface (start here)
├── event.py          <- The Event envelope
├── types.py          <- EventType enum
├── config.py         <- configure() / get_config() / AgentOBSConfig
├── _span.py          <- Span, AgentRun, AgentStep context managers
├── _tracer.py        <- Tracer — top-level tracing entry point
├── _stream.py        <- Internal dispatch: redact → sign → export
├── _cli.py           <- CLI entry-point (8 sub-commands)
├── signing.py        <- HMAC signing & audit chains
├── redact.py         <- PII redaction
├── validate.py       <- JSON Schema validation
├── consumer.py       <- Consumer registry & schema-version compatibility
├── governance.py     <- Event governance policies
├── deprecations.py   <- Per-event-type deprecation tracking
├── compliance/       <- Compatibility checklist suite
├── export/
│   ├── jsonl.py      <- Local file export (async)
│   ├── webhook.py    <- HTTP POST export
│   ├── otlp.py       <- OpenTelemetry export
│   ├── datadog.py    <- Datadog APM traces + metrics
│   └── grafana.py    <- Grafana Loki export
├── exporters/
│   ├── jsonl.py      <- SyncJSONLExporter
│   └── console.py    <- SyncConsoleExporter
├── stream.py         <- EventStream fan-out router (+ Kafka source)
├── integrations/
│   ├── langchain.py  <- LangChain callback handler
│   ├── llamaindex.py <- LlamaIndex event handler
│   ├── openai.py     <- OpenAI tracing wrapper
│   └── ...           (anthropic, groq, ollama, together)
├── namespaces/       <- Typed payload dataclasses
│   ├── trace.py        (SpanPayload, AgentRunPayload, AgentStepPayload — frozen v2)
│   ├── cost.py
│   ├── cache.py
│   └── ...
├── models.py         <- Optional Pydantic v2 models
└── migrate.py        <- Schema migration helpers
examples/             <- Runnable sample scripts
├── openai_chat.py    <- OpenAI + JSONL export
├── agent_workflow.py <- Multi-step agent + console exporter
├── langchain_chain.py<- LangChain callback handler
└── secure_pipeline.py<- HMAC signing + PII redaction together
```

---

## Development setup

```bash
git clone https://github.com/veerarag1973/agentobs.git
cd agentobs

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -e ".[dev]"
pytest                          # run all 2 407 tests
```

<details>
<summary><strong>Code quality commands</strong></summary>

```bash
ruff check .                  # linting
ruff format .                 # auto-format
mypy agentobs                  # type checking
pytest --cov                  # tests + coverage report (>=90% required)
```

</details>

<details>
<summary><strong>Build the docs locally</strong></summary>

```bash
pip install -e ".[docs]"
cd docs
sphinx-build -b html . _build/html   # open _build/html/index.html
```

</details>

---

## Compatibility and versioning

``agentobs`` implements **RFC-0001 AGENTOBS** (Observability Schema Standard for Agentic AI Systems). The current schema version is **2.0**.

This project follows [Semantic Versioning](https://semver.org/):

- **Patch** releases (``1.0.x``) — bug fixes only, fully backwards-compatible
- **Minor** releases (``1.x.0``) — new features, backwards-compatible
- **Major** releases (``x.0.0``) — breaking changes, announced in advance

The ``llm.trace.*`` namespace payload schema is **additionally frozen at v2**: even a major release will not remove or rename fields from ``SpanPayload``, ``AgentRunPayload``, or ``AgentStepPayload``.

---

## Changelog

See [docs/changelog.md](docs/changelog.md) for the full version history.

---

## Contributing

Contributions are welcome! Please read the [Contributing Guide](docs/contributing.md) first, then open an issue or pull request.

Key rules:
- All new code must maintain **>= 90 % test coverage**
- Follow the existing **Google-style docstrings**
- Run ``ruff`` and ``mypy`` before submitting

---

## License

[MIT](LICENSE) — free for personal and commercial use.

---

<p align="center">
  Made with care for the AI observability community.<br/>
  <a href="docs/index.md">Docs</a> ·
  <a href="docs/quickstart.md">Quickstart</a> ·
  <a href="docs/api/index.md">API Reference</a> ·
  <a href="https://github.com/veerarag1973/agentobs/issues">Report a bug</a>
</p>
