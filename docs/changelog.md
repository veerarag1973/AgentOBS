# Changelog

All notable changes to AgentOBS are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/) and
this project adheres to [Semantic Versioning](https://semver.org/).

---

## Unreleased

*(No unreleased changes.)*

---

## 1.0.0 (current) — 2026-03-04

**Phase 10 — CLI Tooling**

- **`tracium validate EVENTS_JSONL`** — schema-validates every event in a
  JSONL file; prints per-line errors.
- **`tracium audit-chain EVENTS_JSONL`** — verifies HMAC signing-chain
  integrity; reads `TRACIUM_SIGNING_KEY` from the environment.
- **`tracium inspect EVENT_ID EVENTS_JSONL`** — pretty-prints a single event
  looked up by `event_id`.
- **`tracium stats EVENTS_JSONL`** — prints a summary of event counts, token
  totals, estimated cost, and timestamp range.

**Phase 11 — Security & Privacy Pipeline**

- **Auto-redaction via `configure()`** — passing `redaction_policy=` to
  `configure()` wires `RedactionPolicy.apply()` into the `_dispatch()` path;
  every emitted span/event is redacted before being handed to the exporter.
- **Auto-signing via `configure()`** — passing `signing_key=` to
  `configure()` wires HMAC-SHA256 signing into the dispatch path; every event
  is signed and chained to the previous one automatically.
- **Pipeline order guaranteed** — redaction always runs before signing, so
  each signature covers the already-redacted payload.
- **`_reset_exporter()` closes file handles** — calling `_reset_exporter()`
  now flushes and closes any open `SyncJSONLExporter` file handle and clears
  the HMAC chain state, preventing `ResourceWarning` in tests and on shutdown.
- **`examples/`** — four runnable sample scripts: `openai_chat.py`,
  `agent_workflow.py`, `langchain_chain.py`, `secure_pipeline.py`.
- **Version**: `0.2.0` → `1.0.0`; coverage threshold: `99 %` → `90 %`.

---

## 0.1.0 — 2026-03-04

### Changed

- **Package renamed** from `llm-toolkit-schema` to `tracium` — PyPI distribution is `agentobs` (`pip install agentobs`), import name is `tracium`. The old package name is a deprecated shim that re-exports from `tracium` and emits a `DeprecationWarning`.
- **Schema version** bumped to `2.0` (SpanForge Observability Standard RFC-0001 v2.0).
- **36 canonical `EventType` values** registered (RFC-0001 Appendix B).
- **11 namespace payload modules** ship 42 v2.0 dataclasses under `tracium.namespaces.*`.
- **`TokenUsage`** fields renamed: `prompt_tokens` → `input_tokens`, `completion_tokens` → `output_tokens`, `total` → `total_tokens`.
- **`ModelInfo`** field change: `provider` (plain string) replaced by `system` (`GenAISystem` enum, OTel `gen_ai.system` aligned).
- **`SpanPayload`** replaces `SpanCompletedPayload` / `TracePayload`. New sibling payloads: `AgentStepPayload`, `AgentRunPayload`.
- **`CacheHitPayload`** replaces `CachePayload`; `CostTokenRecordedPayload` replaces `CostPayload`; `EvalScoreRecordedPayload` replaces `EvalPayload`; `FenceValidatedPayload` replaces `FencePayload`; `PromptRenderedPayload` replaces `PromptPayload`; `RedactPiiDetectedPayload` replaces `RedactPayload`; `TemplateRegisteredPayload` replaces `TemplatePayload`; `DiffComputedPayload` replaces `DiffPayload`.
- **`tracium.namespaces.audit`** — new module: `AuditKeyRotatedPayload`, `AuditChainVerifiedPayload`, `AuditChainTamperedPayload`.

---

## 1.1.2 — 2026-03-15

### Added

- **`OTelBridgeExporter`** (`tracium.export.otel_bridge`) — exports
  events through any configured OpenTelemetry `TracerProvider`. Requires the
  `[otel]` extra (`opentelemetry-sdk>=1.24`). Unlike `OTLPExporter`, this
  bridge uses the SDK's span lifecycle so all registered `SpanProcessor`
  instances (sampling, batching, auto-instrumentation hooks) fire normally.
- **`make_traceparent(trace_id, span_id, *, sampled=True)`**
  (`tracium.export.otlp`) — constructs a W3C TraceContext
  `traceparent` header string (RFC 9429).
- **`extract_trace_context(headers)`** (`tracium.export.otlp`) —
  parses `traceparent` / `tracestate` headers and returns a dict of
  `{trace_id, span_id, sampled[, tracestate]}`.
- **`gen_ai.*` semantic convention attributes** (GenAI semconv 1.27+) —
  `to_otlp_span()` now emits `gen_ai.system`, `gen_ai.request.model`,
  `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`,
  `gen_ai.operation.name`, and `gen_ai.response.finish_reasons` from the
  corresponding `payload.*` fields, enabling native LLM dashboards in Grafana,
  Honeycomb, and Dynatrace.

### Fixed

- **`deployment.environment.name`** — `ResourceAttributes.to_otlp()` now
  emits the semconv 1.21+ key `deployment.environment.name` instead of the
  legacy `deployment.environment`.
- **`spanKind`** — `to_otlp_span()` now sets `kind: 3` (CLIENT) as required
  by the OTLP specification.
- **`traceFlags`** — `to_otlp_span()` now sets `traceFlags: 1` (sampled) on
  every span context.
- **`endTimeUnixNano`** — computed correctly as
  `startTimeUnixNano + payload.duration_ms × 1 000 000`; previously omitted.
- **`status.code` / `status.message`** — `payload.status` values `"error"` and
  `"timeout"` now map to OTLP `STATUS_CODE_ERROR` (2); `"ok"` maps to
  `STATUS_CODE_OK` (1). Previously the status block was always empty.

---

## 1.1.1 — 2026-03-15

### Fixed

- **`Event.payload`** now returns a read-only `MappingProxyType` — mutating
  the returned object no longer silently corrupts event state.
- **`EventGovernancePolicy(strict_unknown=True)`** now correctly raises
  `GovernanceViolationError` for unregistered event types (was a no-op
  previously); docstring corrected to match actual behaviour.
- **`_cli.py`** — broad `except Exception` replaced with typed
  `(DeserializationError, SchemaValidationError, KeyError, TypeError)`,
  preventing silent swallowing of unexpected errors.
- **`stream.py`** — broad `except Exception` in `EventStream.from_file` and
  `EventStream.from_kafka` replaced with `(LLMSchemaError, ValueError)`.
- **`validate.py`** — checksum regex tightened to `^sha256:[0-9a-f]{64}$`
  and signature regex to `^hmac-sha256:[0-9a-f]{64}$`, aligning with the
  prefixes actually produced by `signing.py` (bare 64-hex patterns accepted
  invalid values).
- **`export/datadog.py`**:
  - Fallback span/trace IDs are now deterministic SHA-256 derivations of the
    event ID instead of Python `hash()` (non-reproducible across processes).
  - Span start timestamp uses `event.timestamp` rather than wall-clock time.
  - `dd_site` is validated as a hostname (no scheme/path).
  - `agent_url` is validated as an `http://` or `https://` URL.
- **`export/otlp.py`** — `export_batch` now chunks the event list by
  `batch_size` and issues one request per chunk; previously the parameter
  was accepted but never applied.  URL scheme validated on construction.
- **`export/webhook.py`** — URL scheme validated on construction (`http://`
  or `https://` only).
- **`export/grafana.py`** — URL scheme validated on construction.
- **`redact.py`** — `_has_redactable` / `_count_redactable` use the
  `collections.abc.Mapping` ABC instead of `dict`, so payloads built from
  `MappingProxyType` or other mapping types are handled correctly.

### Added

- **`GuardPolicy`** (`tracium.namespaces.guard`) — runtime
  input/output guardrail enforcement with configurable fail-open / fail-closed
  mode and callable checker injection.
- **`FencePolicy`** (`tracium.namespaces.fence`) — structured-output
  validation driver with retry-sequence loop and `max_retries` limit.
- **`TemplatePolicy`** (`tracium.namespaces.template`) — variable
  presence checking and output validation for prompt-template workflows.
- **`iter_file(path)`** (`tracium.stream`) — synchronous generator
  that streams events from an NDJSON file without buffering the entire file.
- **`aiter_file(path)`** (`tracium.stream`) — async-generator
  equivalent of `iter_file`.

---

## 1.1.0 — 2026-03-01

### Added

**Phase 7 — Enterprise Export Backends**

- **`DatadogExporter`** (`tracium.export.datadog`) — async exporter
  that sends events as Datadog APM trace spans (via the local Agent) and as
  Datadog metrics series (via the public API). No `ddtrace` dependency.
- **`DatadogResourceAttributes`** — frozen dataclass with `service`, `env`,
  `version`, and `extra` fields; `.to_tags()` for tag-string serialisation.
- **`GrafanaLokiExporter`** (`tracium.export.grafana`) — async
  exporter that pushes events to Grafana Loki via the `/loki/api/v1/push`
  HTTP endpoint. Supports multi-tenant deployments via `X-Scope-OrgID`.
- **`ConsumerRegistry`** / **`ConsumerRecord`** (`tracium.consumer`)
  — thread-safe registry for declaring schema-namespace dependencies at startup.
  `assert_compatible()` raises `IncompatibleSchemaError` on version mismatches.
- **`EventGovernancePolicy`** (`tracium.governance`) — data-class
  policy with blocked types, deprecated-type warnings, and arbitrary custom
  rule callbacks. Module-level `set_global_policy()` / `check_event()`.
- **`GovernanceViolationError`**, **`GovernanceWarning`** — governance
  exception and warning types.

**Phase 8 — Ecosystem Integrations & Kafka**

- **`EventStream.from_kafka()`** — classmethod constructor that drains a Kafka
  topic into an `EventStream`. Requires optional extra `kafka`.
- **`DeprecationRegistry`** / **`DeprecationNotice`**
  (`tracium.deprecations`) — structured per-event-type deprecation
  tracking with `warn_if_deprecated()` and `list_deprecated()`.
- **`LLMSchemaCallbackHandler`** (`tracium.integrations.langchain`)
  — LangChain `BaseCallbackHandler` that emits `llm.trace.*` events for all LLM
  and tool invocations. Requires optional extra `langchain`.
- **`LLMSchemaEventHandler`** (`tracium.integrations.llamaindex`)
  — LlamaIndex callback event handler. Requires optional extra `llamaindex`.

**Phase 9 — v2 Migration Framework**

- **`SunsetPolicy`** (`tracium.migrate`) — `Enum` classifying
  removal urgency: `NEXT_MAJOR`, `NEXT_MINOR`, `LONG_TERM`, `UNSCHEDULED`.
- **`DeprecationRecord`** (`tracium.migrate`) — frozen dataclass
  capturing `event_type`, `since`, `sunset`, `sunset_policy`, `replacement`,
  `migration_notes`, and `field_renames` for structured migration guidance.
- **`v2_migration_roadmap()`** — returns all 9 deprecation records for event
  types that will change in v2.0, sorted by `event_type`.
- **CLI: `list-deprecated`** — prints all deprecation notices from the global
  registry.
- **CLI: `migration-roadmap [--json]`** — prints the v2 migration roadmap in
  human-readable or JSON form.
- **CLI: `check-consumers`** — lists all registered consumers and their
  compatibility status against the installed schema version.

### Changed

- Version: `1.0.1` → `1.1.0`
- `export/__init__.py` now re-exports `DatadogExporter`,
  `DatadogResourceAttributes`, and `GrafanaLokiExporter`.
- Top-level `tracium` package re-exports all Phase 7/8/9 public
  symbols.

### Optional extras added

| Extra | Enables |
|-------|---------|
| `kafka` | `EventStream.from_kafka()` via `kafka-python>=2.0` |
| `langchain` | `LLMSchemaCallbackHandler` via `langchain-core>=0.2` |
| `llamaindex` | `LLMSchemaEventHandler` via `llama-index-core>=0.10` |
| `datadog` | `DatadogExporter` (stdlib-only transport; extra reserved for future `ddtrace` integration) |
| `all` | All optional extras in one install target |

---

## 1.0.1 — 2026-03-01

### Changed

- **Python package renamed** from `llm_schema` to `tracium`.
  The import path is now `import tracium` (or
  `from tracium import ...`).
  The distribution name `tracium` and all runtime behaviour are
  unchanged. This is the canonical, permanently stable import name.
- Version: `1.0.0` → `1.0.1`

---

## 1.0.0 — 2026-03-01

**General Availability release.** The public API is now stable and covered
by semantic versioning guarantees.

### Added

- **Compliance package** (`tracium.compliance`) — programmatic v1.0
  compatibility checklist (CHK-1 through CHK-5), multi-tenant isolation
  verification, and audit chain integrity suite. All checks are callable
  without a pytest dependency.
- **`test_compatibility()`** — applies the five-point adoption checklist to
  any sequence of events. Powers the new `tracium check-compat` CLI command.
- **`verify_tenant_isolation()` / `verify_events_scoped()`** — detect
  cross-tenant data leakage in multi-org deployments.
- **`verify_chain_integrity()`** — wraps `verify_chain()` with gap,
  tamper, and timestamp-monotonicity diagnostics.
- **`tracium check-compat`** CLI sub-command — reads a JSON file of
  serialised events and prints compatibility violations.
- **`tracium.migrate`** — `MigrationResult` dataclass and
  `v1_to_v2()` scaffold (raises `NotImplementedError`; full implementation
  ships in Phase 9).
- Performance benchmark test suite (`tests/test_benchmarks.py`,
  `@pytest.mark.perf`) validating all NFR targets.

### Changed

- Version: `0.5.0` → `1.0.0`
- PyPI classifier: `Development Status :: 3 - Alpha` →
  `Development Status :: 5 - Production/Stable`

---

## 0.5.0 — 2026-02-22

### Added

- **Namespace payload dataclasses** for all 10 reserved namespaces
  (`llm.trace.*`, `llm.cost.*`, `llm.cache.*`, `llm.diff.*`,
  `llm.eval.*`, `llm.fence.*`, `llm.guard.*`, `llm.prompt.*`,
  `llm.redact.*`, `llm.template.*`). The `llm.trace` payload is
  **FROZEN** at v1 — no breaking changes permitted.
- **`schemas/v1.0/schema.json`** — published JSON Schema for the event envelope.
- **`validate_event()`** — validates an event against the JSON Schema with an
  optional `jsonschema` backend; falls back to structural stdlib checks.

---

## 0.4.0 — 2026-02-15

### Added

- **`OTLPExporter`** — async OTLP/HTTP JSON exporter with retry, gzip
  compression, and configurable resource attributes.
- **`WebhookExporter`** — async HTTP webhook exporter with configurable
  headers, retry backoff, and timeout.
- **`JSONLExporter`** — synchronous JSONL file exporter with optional
  per-event gzip compression.
- **`EventStream`** — in-process event router with type filters, org/team
  scoping, sampling, and fan-out to multiple exporters.

---

## 0.3.0 — 2026-02-08

### Added

- **`sign()` / `verify()`** — HMAC-SHA256 event signing and verification
  (`sha256:` payload checksum + `hmac-sha256:` chain signature).
- **`verify_chain()`** — batch chain verification with gap detection and
  tampered-event identification.
- **`AuditStream`** — sequential event stream that signs and links every
  appended event via `prev_id`.
- **Key rotation** — `AuditStream.rotate_key()` emits a signed rotation
  event and switches the active HMAC key.
- **`assert_verified()`** — strict raising variant of `verify()`.

---

## 0.2.0 — 2026-02-01

### Added

- **PII redaction framework** — `Redactable`, `Sensitivity`,
  `RedactionPolicy`, `RedactionResult`, `contains_pii()`,
  `assert_redacted()`.
- **Pydantic v2 model layer** — `tracium.models.EventModel` with
  `from_event()` / `to_event()` round-trip and `model_json_schema()`.

---

## 0.1.0 — 2026-01-25

### Added

- **Core `Event` dataclass** — frozen, validated, zero external dependencies.
- **`EventType` enum** — exhaustive registry of all 50+ first-party event types
  across 10 namespaces plus audit types.
- **ULID utilities** — `generate()`, `validate()`, `extract_timestamp_ms()`.
- **`Tags`** dataclass — arbitrary `str → str` metadata.
- **JSON serialisation** — `Event.to_dict()`, `Event.to_json()`,
  `Event.from_dict()`, `Event.from_json()`.
- **`Event.validate()`** — full structural validation of all fields.
- **`is_registered()`**, **`validate_custom()`**, **`namespace_of()`** —
  event-type introspection helpers.
- **Domain exceptions hierarchy** — `LLMSchemaError` base with
  `SchemaValidationError`, `ULIDError`, `SerializationError`,
  `DeserializationError`, `EventTypeError`.
