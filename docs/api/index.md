# API Reference

The agentobs API surface is organised by module. All public symbols are
exported at the top-level package under `agentobs`.

## Modules

- [event](event.md)
- [types](types.md)
- [signing](signing.md)
- [redact](redact.md)
- [compliance](compliance.md)
- [export](export.md)
- [stream](stream.md)
- [validate](validate.md)
- [normalizer](normalizer.md)
- [migrate](migrate.md)
- [consumer](consumer.md)
- [governance](governance.md)
- [deprecations](deprecations.md)
- [integrations](integrations.md)
- [trace](trace.md)
- [debug](debug.md)
- [metrics](metrics.md)
- [store](store.md)
- [hooks](hooks.md)
- [testing](testing.md)
- [auto](auto.md)
- [ulid](ulid.md)
- [exceptions](exceptions.md)
- [models](models.md)
- [cache](cache.md)
- [lint](lint.md)

## Module summary

| Module | Responsibility |
|--------|---------------|
| `agentobs.event` | `Event` envelope and serialisation |
| `agentobs.types` | `EventType` enum, `SpanErrorCategory`, custom type validation |
| `agentobs.signing` | HMAC signing, `AuditStream`, chain verification |
| `agentobs.redact` | `Redactable`, `RedactionPolicy`, PII helpers |
| `agentobs.compliance` | Compatibility checks, isolation, chain integrity, scope verification |
| `agentobs.export` | OTLP, Webhook, JSONL, Datadog, and Grafana Loki export backends |
| `agentobs.stream` | `EventStream` multiplexer with Kafka support |
| `agentobs.validate` | JSON Schema validation helpers (version-aware: v1.0 + v2.0) |
| `agentobs.normalizer` | `ProviderNormalizer` protocol and `GenericNormalizer` fallback |
| `agentobs.migrate` | `MigrationResult`, `SunsetPolicy`, `DeprecationRecord`, `v2_migration_roadmap()` |
| `agentobs.consumer` | `ConsumerRegistry`, `ConsumerRecord`, `IncompatibleSchemaError` |
| `agentobs.governance` | `EventGovernancePolicy`, `GovernanceViolationError`, `GovernanceWarning` |
| `agentobs.deprecations` | `DeprecationRegistry`, `DeprecationNotice`, `warn_if_deprecated()` |
| `agentobs.integrations` | `LLMSchemaCallbackHandler` (LangChain), `LLMSchemaEventHandler` (LlamaIndex), `AgentOBSCrewAIHandler` (CrewAI), OpenAI `patch()` |
| `agentobs._trace` | `Trace` dataclass and `start_trace()` high-level entry point |
| `agentobs.debug` | `print_tree()`, `summary()`, `visualize()` debug utilities |
| `agentobs.metrics` | `aggregate()`, `MetricsSummary`, `LatencyStats`, per-metric helpers |
| `agentobs._store` | `TraceStore` ring buffer; `get_trace()`, `list_tool_calls()`, `list_llm_calls()` |
| `agentobs._hooks` | `HookRegistry`, `hooks` singleton, sync and async span lifecycle callbacks (`on_llm_call`, `on_tool_call`, `on_agent_start`, `on_agent_end` and `*_async` variants) |
| `agentobs.testing` | `MockExporter`, `capture_events()` context manager, `assert_event_schema_valid()`, `trace_store()` — test utilities with no real exporters required |
| `agentobs.auto` | `setup()` / `teardown()` — auto-detect and patch every installed LLM integration |
| `agentobs.ulid` | ULID generation and helpers |
| `agentobs.exceptions` | Package-level exception hierarchy |
| `agentobs.models` | Shared Pydantic base models |
| `agentobs.cache` | `SemanticCache`, `@cached` decorator, `InMemoryBackend`, `SQLiteBackend`, `RedisBackend`, `CacheEntry`, `CacheBackendError` |
| `agentobs.lint` | `run_checks()`, `LintError`, AO001–AO005 checks, `AgentOBSChecker` flake8 plugin, `python -m agentobs.lint` CLI |
