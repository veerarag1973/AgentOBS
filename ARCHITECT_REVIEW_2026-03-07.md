# AgentOBS SDK — Architect Review
**Date:** 2026-03-07  
**Reviewer:** Architect hat (self-review against 8 developer-friendliness principles)  
**Version reviewed:** 2.0.0  
**Target version:** 2.1.0 (all recommendations below)

---

## Scoring Summary

| # | Principle | Score | Top Concern |
|---|-----------|-------|-------------|
| 1 | Standard adherence | ★★★★★ | `SpanPayload` not frozen; `v1_to_v2()` raises `NotImplementedError` publicly |
| 2 | Documentation & tutorials | ★★★★ | Missing custom exporter walkthrough; async emit path undocumented |
| 3 | Minimal surface area | ★★★★ | 116 top-level exports; global `TraceStore` coupling in tests |
| 4 | Fast time-to-first-value | ★★★★★ | Integrations require an explicit side-effect import to activate |
| 5 | Performance & security | ★★★★★ | Sync emit path blocks the event loop for async callers |
| 6 | Extensibility & integrations | ★★★★★ | Hooks are sync-only; no `agentobs.testing` harness; inconsistent `unpatch()` |
| 7 | Backward compatibility | ★★★★ | Mutable `SpanPayload` is a long-term schema risk |
| 8 | Diagnostics & reliability | ★★★★ | No e2e health check; no exporter retry; `TraceStore` hard to isolate in tests |

---

## Principle 1 — Strict Adherence to the Standard ★★★★★

**Strengths:**
- All 116 exports map directly to RFC-0001 sections.
- 36 canonical `EventType` members match RFC Appendix B exactly.
- `SCHEMA_VERSION = "2.0"` is a `Final` constant; readers accept both `"1.0"` and `"2.0"`.
- Docstrings cite RFC sections throughout.

**Gaps & Recommendations:**

| ID | Gap | Recommendation | Status |
|----|-----|----------------|--------|
| P1-1 | `SpanPayload`, `AgentStepPayload`, `AgentRunPayload` are mutable dataclasses — fields can be overwritten post-construction, silently invalidating schema guarantees | Change `@dataclass` → `@dataclass(frozen=True, eq=True)` on all three payload classes. Prevents `payload.error = "foo"` after the span is recorded. | ✅ Implemented in 2.1.0 |
| P1-2 | `v1_to_v2()` is exported in `__all__` but raises `NotImplementedError` — callers crash silently | Remove from `__all__`; emit `DeprecationWarning` via `warnings.warn()` pointing to `v2_migration_roadmap()` before raising | ✅ Implemented in 2.1.0 |

---

## Principle 2 — Excellent Documentation and Tutorials ★★★★

**Strengths:**
- All 116+ public symbols have docstrings.
- RFC sections cited in docstrings.
- Type annotations complete on all core modules.

**Gaps & Recommendations:**

| ID | Gap | Recommendation | Status |
|----|-----|----------------|--------|
| P2-1 | No custom exporter walkthrough | Create `docs/user_guide/custom_exporters.md` with a minimal 20-line end-to-end example | ✅ Implemented in 2.1.0 |
| P2-2 | Async emit pattern undocumented — exporters expose `export_batch()` as `async def` but the dispatch path calls `exporter.export()` synchronously | Document the pattern: use `EventStream` for async exporters; add a "sync vs async" section to tracing guide | ✅ Implemented in 2.1.0 |
| P2-3 | Sampling determinism buried | Add a "Deterministic Sampling" callout box to `docs/user_guide/tracing.md` explaining the `trace_id[:8]` bucketing | ✅ Implemented in 2.1.0 |
| P2-4 | Integration side-effect import required — no doc explains why `import agentobs.integrations.openai` is needed | Add "How patching works" note to each integration guide page | Pending — add to integration guide |

---

## Principle 3 — Minimal Surface Area ★★★★

**Strengths:**
- Zero mandatory dependencies.
- Optional extras correctly gated via `pyproject.toml` extras.
- Lean dispatch pipeline (sample → redact → sign → export → store).

**Gaps & Recommendations:**

| ID | Gap | Recommendation | Status |
|----|-----|----------------|--------|
| P3-1 | 116 top-level exports includes 27 namespace payload classes rarely needed by app developers | Group namespace payloads under `agentobs.payloads` sub-namespace and re-export only the most common ones top-level. Document "advanced import" pattern. | Future (3.0 — would be breaking) |
| P3-2 | `get_store()` is globally coupled — difficult to get a fresh store per test without resetting global state | Add `trace_store()` context manager to `_store.py` that creates an isolated `TraceStore` for the duration of the block | ✅ Implemented in 2.1.0 |
| P3-3 | `governance.py` and `consumer.py` always raise on violations — no warning-only mode | Add `strict=False` parameter to `EventGovernancePolicy` and `registry.assert_compatible()` that downgrades violations to `warnings.warn()` | Future (follow-up PR) |

---

## Principle 4 — Fast Time-to-First-Value ★★★★★

**Strengths:**
- Default `exporter="console"` — zero setup required.
- 5-line minimal example works immediately.
- Env-var overrides allow container config without code changes.

**Gaps & Recommendations:**

| ID | Gap | Recommendation | Status |
|----|-----|----------------|--------|
| P4-1 | Integrations require explicit side-effect import (`import agentobs.integrations.openai`) | Add `agentobs/auto.py` with `setup()` that auto-discovers and patches all installed libraries (OpenAI, LangChain, Anthropic, etc.) | ✅ Implemented in 2.1.0 |
| P4-2 | Quickstart leads with `configure()` before the "wow moment" | Update `docs/quickstart.md` to show the 5-line `Event(...)` example first | ✅ Implemented in 2.1.0 |

---

## Principle 5 — High Performance and Strong Security ★★★★★

**Strengths:**
- < 1 ms per event (no signing), < 5 ms (with HMAC) — NFR benchmarks pass.
- `hmac.compare_digest()` for constant-time verification.
- Secrets never appear in `__repr__`, exceptions, or logs.
- Key rotation supported via `AuditStream.rotate_key()`.
- PII redaction is recursive and policy-driven.
- SSRF risk mitigated in `OTLPExporter`.

**Gaps & Recommendations:**

| ID | Gap | Recommendation | Status |
|----|-----|----------------|--------|
| P5-1 | The emit path calls `exporter.export()` (sync) even when the exporter is async. In long-running async apps this blocks the event loop | Add async emit path: when `asyncio.get_running_loop()` is active, emit via `loop.call_soon_threadsafe()` or schedule on the loop | Future (requires exporter protocol update) |
| P5-2 | Signing keys are raw base64 strings — no NIST key derivation | Document that keys must be cryptographically random ≥ 32 bytes; add optional `derive_key(passphrase, salt)` helper using PBKDF2-HMAC-SHA256 | Future (follow-up PR) |
| P5-3 | No per-event nonce — audit chain security anchor is ordering only | Future defence-in-depth: add optional `nonce` field to Event envelope in a future schema revision | Future (requires RFC revision) |
| P5-4 | Export errors only emit to `warnings.warn()` — silently swallowed in production log frameworks | Add structured export error counter + emit errors to `logging.getLogger("agentobs")` | ✅ Implemented in 2.1.0 |

---

## Principle 6 — Easy Extensibility and Integrations ★★★★★

**Strengths:**
- `Exporter` protocol is `@runtime_checkable` — duck-typed, no inheritance required.
- 6 built-in exporters; 7 integrations.
- Hook system isolates errors (hook failures → `warnings.warn()`).
- Consumer registry enables proactive schema compatibility checks.

**Gaps & Recommendations:**

| ID | Gap | Recommendation | Status |
|----|-----|----------------|--------|
| P6-1 | Hooks execute synchronously — async agent's thread blocked by any I/O in hook | Add `AsyncHookFn` type and `on_llm_call_async`, `on_tool_call_async`, `on_agent_start_async`, `on_agent_end_async` to `HookRegistry` | ✅ Implemented in 2.1.0 |
| P6-2 | `unpatch()` inconsistently implemented — missing in `crewai`, `langchain`, `llamaindex` | Add `unpatch()` and `is_patched()` to all three integrations | ✅ Implemented in 2.1.0 |
| P6-3 | No `agentobs.testing` module — custom integration authors have no validated test harness | Create `agentobs/testing.py` with `MockExporter`, `capture_events()` context manager, `assert_event_schema_valid()`, `trace_store()` | ✅ Implemented in 2.1.0 |

---

## Principle 7 — Backward Compatibility and Versioning ★★★★

**Strengths:**
- Dual schema support (v1/v2): `_ACCEPTED_SCHEMA_VERSIONS = frozenset({"1.0", "2.0"})`.
- `DeprecationRecord` with `SunsetPolicy` enum; machine-readable via CLI.
- `warn_if_deprecated()` for runtime detection.

**Gaps & Recommendations:**

| ID | Gap | Recommendation | Status |
|----|-----|----------------|--------|
| P7-1 | `SpanPayload` not frozen — post-construction mutation bypasses validation (covered in P1-1) | See P1-1 | ✅ Implemented in 2.1.0 |
| P7-2 | `v1_to_v2()` in `__all__` raises `NotImplementedError` (covered in P1-2) | See P1-2 | ✅ Implemented in 2.1.0 |
| P7-3 | No automated sunset enforcement — deprecated types accumulate without CI enforcement | Add `assert_no_sunset_reached()` function that raises if any `DeprecationRecord.sunset` version ≤ current SDK version | ✅ Implemented in 2.1.0 |
| P7-4 | Semantic versioning contract not stated in README or `pyproject.toml` | Add a "Versioning Policy" section to `README.md`: "Minor = backward-compatible; Major = may break public API" | ✅ Implemented in 2.1.0 |

---

## Principle 8 — Built-in Diagnostics and Reliability ★★★★

**Strengths:**
- `print_tree()`, `summary()`, `visualize()` (HTML Gantt).
- In-process `TraceStore` ring buffer with `get_trace()`, `list_llm_calls()`, etc.
- 8 CLI diagnostic commands.
- `NO_COLOR` env var support.

**Gaps & Recommendations:**

| ID | Gap | Recommendation | Status |
|----|-----|----------------|--------|
| P8-1 | No e2e health check — most common user question is "why isn't anything showing up?" | Add `agentobs check` CLI command: validates config → creates test event → exports it → confirms store recorded it | ✅ Implemented in 2.1.0 |
| P8-2 | Export errors only go to `warnings.warn()` — not observable in production logging | Emit export errors to `logging.getLogger("agentobs.export")` at `WARNING` level; track `export_error_count` | ✅ Implemented in 2.1.0 |
| P8-3 | `TraceStore` globally coupled — no per-test isolation without resetting global state | Add `trace_store()` context manager in `agentobs/testing.py` and `_store.py` | ✅ Implemented in 2.1.0 |
| P8-4 | No retry in exporters — transient network failure drops event permanently | Add `max_retries: int = 3` and `retry_delay_s: float = 0.5` to `_stream._dispatch()` for transient `ExportError` | ✅ Implemented in 2.1.0 |

---

## Implementation Priority

### P0 — Critical (schema correctness + trust)
1. **P1-1** Freeze `SpanPayload` / `AgentStepPayload` / `AgentRunPayload`
2. **P1-2** Remove `v1_to_v2` from `__all__` (or implement it)

### P1 — High (developer friction)
3. **P6-3** Add `agentobs/testing.py` (MockExporter + capture_events)
4. **P8-1** Add `agentobs check` CLI health command
5. **P4-1** Add `agentobs/auto.py` integration auto-discovery
6. **P6-1** Add async hook variants
7. **P6-2** Add `unpatch()` to crewai / langchain / llamaindex

### P2 — Medium (reliability + observability)
8. **P8-3 / P3-2** `trace_store()` context manager
9. **P8-4** Exporter retry logic
10. **P5-4 / P8-2** Structured export error logging + counter

### P3 — Documentation
11. **P2-1** Custom exporter guide
12. **P2-2** Async emit pattern documentation
13. **P2-3** Sampling determinism callout
14. **P7-4** Versioning policy in README

---

## Notes for Future Versions

- **3.0:** Consider grouping namespace payload classes under `agentobs.payloads` to reduce top-level export count (breaking change).
- **Future RFC revision:** Per-event nonce, NIST key derivation, async emit protocol.
- **Follow-up PR:** `governance.py` warn-only mode (`strict=False`).
