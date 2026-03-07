# agentobs.debug

Developer-facing debug utilities for inspecting span trees, computing
summary statistics, and generating HTML Gantt visualisations.

All three functions are also available as methods on the `Trace` object and
are re-exported at the top-level `agentobs` package.

---

## `print_tree`

```python
def print_tree(
    spans: list[SpanPayload] | list[Span],
    *,
    file: Any = None,
) -> None
```

Pretty-print a hierarchical span tree with Unicode box-drawing characters.

**Example output:**

```
— Agent Run: research-agent  [2.4s]
 ├─ LLM Call: gpt-4o  [1.1s]  in=512 out=200 tokens  $0.0031
 ├─ Tool Call: search  [0.4s]  ok
 │   └─ Tool Call: fetch_url  [0.2s]  ok
 └─ LLM Call: gpt-4o  [0.9s]  in=300 out=150 tokens  $0.0021
```

**Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `spans` | `list[SpanPayload \| Span]` | — | Spans to render (can be mixed types) |
| `file` | file-like \| `None` | `None` | Output destination; defaults to `sys.stdout` |

> Set environment variable `NO_COLOR=1` to suppress ANSI colour.

---

## `summary`

```python
def summary(spans: list[SpanPayload] | list[Span]) -> dict[str, Any]
```

Return an aggregated statistics dictionary.

**Return value:**

```python
{
    "trace_id": "01JP...",
    "agent_name": "research-agent",
    "total_duration_ms": 2400.0,
    "span_count": 4,
    "llm_calls": 2,
    "tool_calls": 1,
    "total_input_tokens": 812,
    "total_output_tokens": 350,
    "total_cost_usd": 0.0052,
    "errors": 0,
}
```

---

## `visualize`

```python
def visualize(
    spans: list[SpanPayload] | list[Span],
    output: str = "html",
    *,
    path: str | None = None,
) -> str
```

Generate a self-contained HTML Gantt-timeline page with no external
dependencies.

**Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `spans` | `list` | — | Spans to visualise |
| `output` | `str` | `"html"` | Output format (only `"html"` is supported currently) |
| `path` | `str \| None` | `None` | If provided, write the HTML to this file path |

**Returns** the HTML string (even when `path` is specified).

---

## Re-exports

```python
from agentobs import print_tree, summary, visualize
from agentobs.debug import print_tree, summary, visualize
```
