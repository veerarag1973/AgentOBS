# User Guide

This guide covers all major features of agentobs in depth.
Start with [Events](events.md) if you are new to the library, then proceed to
whichever features your use case requires.

## Contents

- [Events](events.md)
- [Tracing API](tracing.md) — `Trace`, `start_trace()`, async spans, `add_event()`, error categories
- [HMAC Signing & Audit Chains](signing.md)
- [PII Redaction](redaction.md)
- [Compliance & Tenant Isolation](compliance.md)
- [Export Backends & EventStream](export.md)
- [Governance, Consumer Registry & Deprecations](governance.md)
- [Migration Guide](migration.md)
- [Debugging & Visualization](debugging.md) — `print_tree()`, `summary()`, `visualize()`, sampling
- [Metrics & Analytics](metrics.md) — `metrics.aggregate()`, `MetricsSummary`, `TraceStore`
- [Semantic Cache](cache.md) — `SemanticCache`, `@cached`, `InMemoryBackend`, `SQLiteBackend`, `RedisBackend`
- [Linting & Static Analysis](linting.md) — `run_checks()`, AO001–AO005, flake8 plugin, CI integration
