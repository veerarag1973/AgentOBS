# agentobs.lint — SDK Instrumentation Linter

> **Module:** `agentobs.lint`  
> **Added in:** 1.0.7

`agentobs.lint` is a static analysis tool that inspects Python source files
for common AgentOBS instrumentation mistakes *before* the code runs. It
ships as:

1. A **Python API** — call `run_checks()` from test suites or CI scripts.
2. A **flake8 / ruff plugin** — AO-codes appear inline in your editor and
   linting output with no extra configuration.
3. A **CLI** — `python -m agentobs.lint myapp/` for one-shot sweeps.

---

## Quick example

```python
from agentobs.lint import run_checks

errors = run_checks(
    source=open("myapp/pipeline.py").read(),
    filename="myapp/pipeline.py",
)

for err in errors:
    print(f"{err.filename}:{err.line}:{err.col}: {err.code} {err.message}")
```

Output example:

```
myapp/pipeline.py:17:1:  AO001 Event() is missing required field 'payload'
myapp/pipeline.py:42:12: AO002 actor_id receives a bare str literal; wrap with Redactable()
myapp/pipeline.py:53:5:  AO004 LLM provider call outside tracer span context
```

---

## `run_checks()`

```python
def run_checks(source: str, filename: str = "<string>") -> list[LintError]
```

Parse `source` as valid Python 3 with `ast`, visit every node, and return a
list of `LintError` objects (empty list when the file is clean).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `source` | `str` | — | UTF-8 Python source code to analyse |
| `filename` | `str` | `"<string>"` | File path; used in `LintError.filename` |

**Returns:** `list[LintError]` sorted by `(line, col)`.

**Raises:** `SyntaxError` (surfaced as `LintError` with code `AO000`) when
`source` contains a syntax error.

---

## `LintError`

```python
@dataclass(frozen=True)
class LintError:
    code: str        # e.g. "AO001"
    message: str     # human-readable description
    filename: str    # file the error was found in
    line: int        # 1-based line number
    col: int         # 1-based column number
```

All fields are immutable. `LintError` objects are hashable and safe to store
in sets.

---

## Error codes

### AO000 — Syntax error

```
AO000  Syntax error: {detail}
```

The source file could not be parsed. Returned instead of raising
`SyntaxError` so callers can handle all lint results uniformly. All other
checks are skipped when AO000 is returned.

---

### AO001 — Missing required `Event()` field

```
AO001  Event() is missing required field '{field}'
```

Triggered when an `Event(...)` constructor call is missing one of the three
required keyword arguments: `event_type`, `source`, or `payload`.

**Bad:**

```python
event = Event(event_type="llm.trace.span.completed", source="my-app@1.0.0")
# AO001: 'payload' is not provided
```

**Good:**

```python
event = Event(
    event_type="llm.trace.span.completed",
    source="my-app@1.0.0",
    payload=span.to_dict(),
)
```

---

### AO002 — Bare `str` literal for identity field

```
AO002  '{field}' receives a bare str literal; wrap with Redactable()
```

Triggered when `actor_id`, `session_id`, or `user_id` is assigned a bare
string literal. These fields often contain PII and should be wrapped in
`Redactable()` so the redaction pipeline can process them.

**Bad:**

```python
event = Event(..., actor_id="user-12345")
```

**Good:**

```python
from agentobs import Redactable
event = Event(..., actor_id=Redactable("user-12345", sensitivity="HIGH"))
```

---

### AO003 — Unknown `event_type` string

```
AO003  event_type string '{value}' is not a registered EventType value
```

Triggered when `event_type=` is assigned a string literal that is not
present in `agentobs.types.EventType`. This catches typos like
`"llm.trace.spam.completed"` before they produce silently-invalid events.

**Bad:**

```python
event = Event(event_type="llm.trase.span.completed", ...)  # typo
```

**Good:**

```python
from agentobs.types import EventType
event = Event(event_type=EventType.SPAN_COMPLETED, ...)
# or use the validated string:
event = Event(event_type="llm.trace.span.completed", ...)
```

---

### AO004 — LLM call outside trace context

```
AO004  LLM provider call outside tracer span context
```

Triggered when a call matching the pattern `*.chat.completions.create()`,
`*.messages.create()`, `*.generate()`, etc. is detected outside a
`with tracer.span(...)` or `async with agent_run(...)` block. Without a
span context the call will produce no observability data.

**Bad:**

```python
response = client.chat.completions.create(model="gpt-4o", messages=[...])
```

**Good:**

```python
async with tracer.span("call-llm"):
    response = client.chat.completions.create(model="gpt-4o", messages=[...])
```

---

### AO005 — Emit call outside agent context

```
AO005  emit_span / emit_agent_* called outside agent_run() / agent_step() context
```

Triggered when `emit_span()`, `emit_agent_run()`, or `emit_agent_step()` is
called outside an `agent_run()` or `agent_step()` context manager. Emitting
these events without a surrounding agent context means there is no parent
trace to attach them to.

**Bad:**

```python
emit_span(my_span)  # top-level, no agent context
```

**Good:**

```python
async with agent_run("my-agent") as run:
    emit_span(my_span)
```

---

## flake8 / ruff plugin

All five AO-codes are surfaced natively when `flake8` or `ruff` processes
files that import `agentobs`. The plugin is registered via the
`[project.entry-points."flake8.extension"]` entry in `pyproject.toml`:

```toml
[project.entry-points."flake8.extension"]
AO = "agentobs.lint._flake8:AgentOBSChecker"
```

After installing `agentobs` in your project, run:

```bash
flake8 myapp/
# or
ruff check myapp/
```

AO-codes appear alongside PEP-8 and other style warnings with no extra
configuration.

To **disable** a specific code on a line, use a `# noqa: AO002` comment:

```python
actor_id = "system"  # noqa: AO002
```

---

## CLI

```bash
python -m agentobs.lint [FILES_OR_DIRS...]
```

Recursively scans every `*.py` file in the paths you specify. If no paths
are given, the current directory is scanned.

**Exit codes:**

| Code | Meaning |
|------|---------|
| `0` | No errors found |
| `1` | One or more AO-errors found |
| `2` | Internal error (e.g. a path does not exist) |

**Example:**

```bash
# Check the whole project
python -m agentobs.lint .

# Check one file
python -m agentobs.lint myapp/pipeline.py

# Output
myapp/pipeline.py:17:1  AO001 Event() is missing required field 'payload'
myapp/pipeline.py:53:5  AO004 LLM provider call outside tracer span context
2 errors in 1 file.
```

Add to your CI pipeline (`Makefile`, GitHub Actions, etc.):

```yaml
- name: AgentOBS lint
  run: python -m agentobs.lint myapp/
```

---

## `AgentOBSChecker` (flake8 internals)

```python
class AgentOBSChecker:
    name: str = "agentobs-lint"
    version: str = "1.0.8"
    def __init__(self, tree: ast.AST, filename: str = "(none)") -> None: ...
    def run(self) -> Iterable[tuple[int, int, str, type]]: ...
```

This class is used internally by the flake8 plugin system. Application code
should not need to instantiate it directly — use `run_checks()` instead.

---

## See also

- [User guide — Linting & Static Analysis](../user_guide/linting.md)
- [`agentobs.event`](event.md) — Event envelope (AO001 checks its constructor)
- [`agentobs.redact`](redact.md) — `Redactable` wrapper (AO002 requires it)
- [`agentobs.types`](types.md) — `EventType` enum (AO003 validates against it)
- [`agentobs._span`](store.md) — span context managers (AO004/AO005 check for them)
