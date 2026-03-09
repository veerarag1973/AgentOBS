# Linting & Static Analysis

> **Module:** `agentobs.lint`  
> **Added in:** 1.0.7

`agentobs.lint` catches instrumentation mistakes at *static analysis* time —
before broken or incomplete events ever reach your export pipeline. It ships
as a Python API, a flake8/ruff plugin, and a standalone CLI so it fits
wherever your existing quality tools live.

---

## The five error codes

| Code | Short description | Impact if missed |
|------|-------------------|-----------------|
| AO001 | `Event()` missing required field | Silent schema-invalid events |
| AO002 | Bare `str` for identity field | PII leaks past the redaction pipeline |
| AO003 | Unknown `event_type` string literal | Events silently dropped by consumers |
| AO004 | LLM call outside trace span | LLM calls produce no observability data |
| AO005 | Emit call outside agent context | Orphaned events with no parent trace |

---

## Using `run_checks()` in tests

The cleanest integration is to call `run_checks()` directly inside a pytest
fixture or test so instrumentation quality gates run alongside your normal
test suite:

```python
# tests/test_lint.py
import glob
from agentobs.lint import run_checks

def _all_sources():
    return glob.glob("myapp/**/*.py", recursive=True)

def test_no_lint_errors():
    errors = []
    for path in _all_sources():
        errors.extend(run_checks(open(path).read(), filename=path))

    if errors:
        lines = [f"{e.filename}:{e.line}:{e.col}: {e.code} {e.message}" for e in errors]
        raise AssertionError("AgentOBS lint errors:\n" + "\n".join(lines))
```

---

## Using the CLI

```bash
# Check a single file
python -m agentobs.lint myapp/pipeline.py

# Check a whole directory tree
python -m agentobs.lint myapp/

# Check the current directory
python -m agentobs.lint .
```

Sample output:

```
myapp/pipeline.py:17:1  AO001 Event() is missing required field 'payload'
myapp/pipeline.py:42:12 AO002 actor_id receives a bare str literal; wrap with Redactable()
myapp/pipeline.py:53:5  AO004 LLM provider call outside tracer span context
3 errors in 1 file.
```

Exit codes:

| Code | Meaning |
|------|---------|
| `0` | No errors — clean |
| `1` | One or more AO-errors found |
| `2` | Internal error (bad path, etc.) |

---

## Using the flake8 plugin

Once `agentobs` is installed in your environment, AO-codes appear in
`flake8` and `ruff` output automatically:

```bash
flake8 myapp/
```

```
myapp/pipeline.py:17:1: AO001 Event() is missing required field 'payload'
myapp/pipeline.py:42:12: AO002 actor_id receives a bare str literal; wrap with Redactable()
```

### Inline suppression

Suppress a code on a specific line with the standard `# noqa` comment:

```python
# Suppressing AO002 because actor_id is a system identifier, not user PII:
event = Event(..., actor_id="system-health-monitor")  # noqa: AO002
```

### `.flake8` configuration

Add to your `.flake8` file to ignore a code project-wide (use sparingly):

```ini
[flake8]
extend-ignore = AO003
```

---

## Adding to CI

### GitHub Actions

```yaml
# .github/workflows/quality.yml
- name: AgentOBS lint
  run: python -m agentobs.lint myapp/ src/
```

Make the step a hard failure (it already is — the CLI exits `1` on errors).
Combine with `flake8` to run both in one step:

```yaml
- name: Lint
  run: |
    flake8 myapp/
    python -m agentobs.lint myapp/
```

### Makefile

```makefile
lint:
    ruff check .
    python -m agentobs.lint myapp/
```

### pre-commit hook

```yaml
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: agentobs-lint
      name: AgentOBS instrumentation lint
      language: system
      entry: python -m agentobs.lint
      types: [python]
```

---

## Fixing each error code

### AO001 — Add the missing `Event()` field

```python
# Before  (AO001: payload missing)
event = Event(event_type="llm.trace.span.completed", source="my-app@1.0.0")

# After
event = Event(
    event_type="llm.trace.span.completed",
    source="my-app@1.0.0",
    payload=span.to_dict(),
)
```

### AO002 — Wrap PII fields with `Redactable`

```python
from agentobs import Redactable

# Before  (AO002)
event = Event(..., actor_id="user-99")

# After
event = Event(..., actor_id=Redactable("user-99", sensitivity="HIGH"))
```

### AO003 — Use the `EventType` enum

```python
from agentobs.types import EventType

# Before  (AO003: typo in string)
event = Event(event_type="llm.trase.span.completed", ...)

# After
event = Event(event_type=EventType.SPAN_COMPLETED, ...)
```

### AO004 — Wrap LLM calls in a span

```python
from agentobs import tracer

# Before  (AO004)
response = client.chat.completions.create(model="gpt-4o", messages=[...])

# After
async with tracer.span("call-llm"):
    response = client.chat.completions.create(model="gpt-4o", messages=[...])
```

### AO005 — Emit span events inside an agent context

```python
from agentobs._span import agent_run

# Before  (AO005)
emit_span(my_span)

# After
async with agent_run("my-agent") as run:
    emit_span(my_span)
```

---

## See also

- [API reference — agentobs.lint](../api/lint.md)
- [API reference — agentobs.redact](../api/redact.md) — `Redactable` wrapper (required by AO002)
- [API reference — agentobs.types](../api/types.md) — `EventType` enum (required by AO003)
- [Tracing user guide](tracing.md) — span context managers (required by AO004/AO005)
