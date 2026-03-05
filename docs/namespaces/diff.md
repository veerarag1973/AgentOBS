# llm.diff — Prompt/Response Delta

> **Auto-documented module:** `tracium.namespaces.diff`

The `llm.diff.*` namespace records computed differences between two events,
allowing regression detection and prompt-drift analysis (RFC-0001 §6).

## Payload classes

| Class | Event type | Description |
|-------|-----------|-------------|
| `DiffComputedPayload` | `llm.diff.computed` | A diff was computed between two events |
| `DiffRegressionFlaggedPayload` | `llm.diff.regression.flagged` | A diff exceeded a regression threshold |

---

## `DiffComputedPayload` — key fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ref_event_id` | `str` | ✓ | ULID of the reference (baseline) event |
| `target_event_id` | `str` | ✓ | ULID of the target event being compared |
| `diff_type` | `str` | ✓ | One of `"prompt"`, `"response"`, `"template"`, `"token_usage"`, `"cost"` |
| `similarity_score` | `float` | ✓ | Semantic similarity in `[0.0, 1.0]` |
| `added_tokens` | `int \| None` | — | Tokens added relative to the reference |
| `removed_tokens` | `int \| None` | — | Tokens removed relative to the reference |
| `diff_algorithm` | `str \| None` | — | Algorithm used. One of `"embedding_cosine"`, `"levenshtein"`, `"token_edit"`, `"lcs"`, `"semantic_embedding"` |
| `ref_content_hash` | `str \| None` | — | SHA-256 of the reference content |
| `target_content_hash` | `str \| None` | — | SHA-256 of the target content |
| `computation_duration_ms` | `float \| None` | — | Diff computation latency |

---

## Example

```python
from tracium import Event, EventType
from tracium.namespaces.diff import DiffComputedPayload

payload = DiffComputedPayload(
    ref_event_id="01HXABC0000000000000000000",
    target_event_id="01HXDEF0000000000000000000",
    diff_type="prompt",
    similarity_score=0.92,
    added_tokens=15,
    removed_tokens=8,
    diff_algorithm="embedding_cosine",
)

event = Event(
    event_type=EventType.DIFF_COMPUTED,
    source="my-app@1.0.0",
    org_id="org_01HX",
    payload=payload.to_dict(),
)
```
