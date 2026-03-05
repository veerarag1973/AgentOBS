# llm.guard — Safety Classifier

> **Auto-documented module:** `tracium.namespaces.guard`

## Field reference

| Field | Type | Description |
|-------|------|-------------|
| `classifier` | `str` | Classifier identifier (e.g. `"openai-moderation"`, `"llama-guard-2"`). |
| `direction` | `str` | `"input"` or `"output"` — which side of the model was classified. |
| `action` | `str` | Result: `"blocked"`, `"passed"`, `"flagged"`, `"modified"`, or `"escalated"`. |
| `score` | `float` | Classifier confidence score. |
| `score_min` | `float \| None` | Minimum of the scoring scale. |
| `score_max` | `float \| None` | Maximum of the scoring scale. |
| `threshold` | `float \| None` | Block threshold applied. |
| `categories` | `list[str]` | All harm categories evaluated by the classifier. |
| `triggered_categories` | `list[str]` | Categories that exceeded the block threshold. |
| `latency_ms` | `float \| None` | Classifier latency in milliseconds. |
| `policy_id` | `str \| None` | Policy identifier that applied this guard. |
| `span_id` | `str \| None` | Parent span identifier. |
| `content_hash` | `str \| None` | SHA-256 hash of the classified content (64 hex chars). |

## Example

```python
from tracium.namespaces.guard import GuardPayload

payload = GuardPayload(
    classifier="llama-guard-2",
    direction="input",
    action="blocked",
    score=0.91,
    categories=["violence", "self-harm"],
    triggered_categories=["self-harm"],
    threshold=0.8,
)
```
