# Documentation Index

> **AgentOBS** (`agentobs`) — The reference implementation of the [AGENTOBS Standard](https://www.getspanforge.com/standard) (RFC-0001), the open event-schema standard for observability of agentic AI systems.  
> Current release: **2.0.0** — [Changelog](changelog.md) · [![PyPI](https://img.shields.io/pypi/v/agentobs?color=4c8cbf&logo=pypi&logoColor=white)](https://pypi.org/project/agentobs/)

This index links to every documentation page in this folder.

---

## Getting Started

| Page | Description |
|------|-------------|
| [Quickstart](quickstart.md) | Create your first event, sign a chain, and export — in 5 minutes |
| [Installation](installation.md) | Install from PyPI, optional extras, and dev setup |

---

## User Guide

| Page | Description |
|------|-------------|
| [User Guide](user_guide/index.md) | Overview of all user guide topics |
| [Events](user_guide/events.md) | Event envelope, event types, serialisation, validation, ULIDs |
| [Tracing API](user_guide/tracing.md) | `Trace`, `start_trace()`, async context managers, `span.add_event()`, error categories, timeout deadline |
| [HMAC Signing & Audit Chains](user_guide/signing.md) | Sign events, build tamper-evident chains, detect tampering |
| [PII Redaction](user_guide/redaction.md) | Sensitivity levels, redaction policies, PII detection |
| [Compliance & Tenant Isolation](user_guide/compliance.md) | Compatibility checklist, chain integrity, tenant isolation |
| [Export Backends & EventStream](user_guide/export.md) | JSONL, Webhook, OTLP, Datadog, Grafana Loki exporters; EventStream; Kafka source |
| [Governance, Consumer Registry & Deprecations](user_guide/governance.md) | Block/warn event types, declare schema dependencies, track deprecations |
| [Migration Guide](user_guide/migration.md) | v2 migration roadmap, deprecation records, `v1_to_v2()` scaffold |
| [Debugging & Visualization](user_guide/debugging.md) | `print_tree()`, `summary()`, `visualize()`, and sampling controls |
| [Metrics & Analytics](user_guide/metrics.md) | `metrics.aggregate()`, `MetricsSummary`, `TraceStore`, `get_trace()` |

---

## API Reference

| Page | Module |
|------|--------|
| [API Reference](api/index.md) | Module summary and full listing |
| [event](api/event.md) | `agentobs.event` — Event envelope and serialisation |
| [types](api/types.md) | `agentobs.types` — EventType enum, custom type validation |
| [signing](api/signing.md) | `agentobs.signing` — HMAC signing and AuditStream |
| [redact](api/redact.md) | `agentobs.redact` — Redactable, RedactionPolicy, PII helpers |
| [compliance](api/compliance.md) | `agentobs.compliance` — Compatibility and isolation checks |
| [export](api/export.md) | `agentobs.export` — OTLP, Webhook, JSONL, Datadog, Grafana Loki backends |
| [stream](api/stream.md) | `agentobs.stream` — EventStream multiplexer with Kafka support |
| [validate](api/validate.md) | `agentobs.validate` — JSON Schema validation |
| [migrate](api/migrate.md) | `agentobs.migrate` — Migration scaffold, `SunsetPolicy`, `v2_migration_roadmap()` |
| [consumer](api/consumer.md) | `agentobs.consumer` — ConsumerRegistry, IncompatibleSchemaError |
| [governance](api/governance.md) | `agentobs.governance` — EventGovernancePolicy, GovernanceViolationError |
| [deprecations](api/deprecations.md) | `agentobs.deprecations` — DeprecationRegistry, warn_if_deprecated() |
| [integrations](api/integrations.md) | `agentobs.integrations` — LangChain, LlamaIndex, OpenAI, CrewAI adapters |
| [trace](api/trace.md) | `agentobs._trace` — `Trace` class and `start_trace()` |
| [debug](api/debug.md) | `agentobs.debug` — `print_tree()`, `summary()`, `visualize()` |
| [metrics](api/metrics.md) | `agentobs.metrics` — `aggregate()`, `MetricsSummary`, `LatencyStats` |
| [store](api/store.md) | `agentobs._store` — `TraceStore` and MCP trace access functions |
| [hooks](api/hooks.md) | `agentobs._hooks` — `HookRegistry`, `hooks` singleton |
| [ulid](api/ulid.md) | `agentobs.ulid` — ULID generation and helpers |
| [exceptions](api/exceptions.md) | `agentobs.exceptions` — Exception hierarchy |
| [models](api/models.md) | `agentobs.models` — Pydantic v2 model layer |

---

## Namespace Payload Catalogue

| Page | Namespace | Purpose |
|------|-----------|----------|
| [Namespace index](namespaces/index.md) | — | Overview and quick-reference table |
| [trace](namespaces/trace.md) | `llm.trace.*` | Model inputs, outputs, latency, token counts  |
| [cost](namespaces/cost.md) | `llm.cost.*` | Per-event cost estimates and budget tracking |
| [cache](namespaces/cache.md) | `llm.cache.*` | Cache hit/miss, key, TTL, backend metadata |
| [diff](namespaces/diff.md) | `llm.diff.*` | Prompt/response delta between two events |
| [eval](namespaces/eval.md) | `llm.eval.*` | Scoring, grading, and human-feedback payloads |
| [fence](namespaces/fence.md) | `llm.fence.*` | Perimeter checks, topic constraints, allow/block lists |
| [guard](namespaces/guard.md) | `llm.guard.*` | Safety classifier outputs and block decisions |
| [prompt](namespaces/prompt.md) | `llm.prompt.*` | Prompt versioning, template rendering, variable sets |
| [redact_ns](namespaces/redact_ns.md) | `llm.redact.*` | PII detection and redaction audit records |
| [template](namespaces/template.md) | `llm.template.*` | Template registry metadata and render snapshots |
| [audit](namespaces/audit.md) | `llm.audit.*` | HMAC audit chain events |

---

## Command-Line Interface

| Page | Description |
|------|-------------|
| [CLI](cli.md) | `agentobs` command reference: `check-compat`, `validate`, `audit-chain`, `inspect`, `stats`, `list-deprecated`, `migration-roadmap`, `check-consumers` |

---

## Development

| Page | Description |
|------|-------------|
| [Contributing](contributing.md) | Dev setup, code standards, PR checklist |
| [Changelog](changelog.md) | Version history and release notes |
