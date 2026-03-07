Below is a **single consolidated specification** that combines:

* the **15 improvements** discussed earlier
* **+ 3 additional high-impact APIs** that will make AgentOBS feel like a serious observability SDK similar in philosophy to OpenTelemetry.

This spec is written so you can **directly use it as a design document for the next version of the AgentOBS SDK**.

---

# AgentOBS SDK vNext Specification

## SpanForge Agentic AI Observability SDK

### Objective

Provide a **standard developer SDK** for instrumenting agentic AI systems so that their execution can be observed, validated, and debugged using the AgentOBS ecosystem.

The SDK must enable developers to easily capture:

* agent execution flows
* LLM interactions
* tool calls
* reasoning steps
* failures and latency

while exporting structured traces compatible with the AgentOBS specification.

---

# 1. Core Trace Object

The SDK must introduce a **trace abstraction** representing a complete agent execution.

Example:

```python
trace = agentobs.start_trace("agent_run")

trace.llm_call(...)
trace.tool_call(...)
trace.end()
```

A trace contains multiple **spans** representing execution steps.

Trace fields:

```
trace_id
agent_name
service_name
environment
start_time
end_time
spans[]
```

---

# 2. Span Hierarchy

The SDK must support **nested spans**.

Example hierarchy:

```
agent_run
 ├─ llm_call
 ├─ tool_call
 │   └─ external_api
 └─ llm_call
```

Required fields:

```
span_id
parent_span_id
trace_id
span_type
start_time
end_time
duration
```

---

# 3. Context Propagation

The SDK must automatically propagate trace context across:

* functions
* threads
* async tasks

Developers must **not manage IDs manually**.

---

# 4. Python Context Manager API

Provide a Pythonic API using `with`.

Example:

```python
with agentobs.agent_run("research_agent"):
    with agentobs.llm_call(model="gpt-4"):
        response = llm(prompt)

    with agentobs.tool_call("search"):
        results = search(query)
```

Benefits:

* automatic timing
* parent-child relationships
* cleaner developer experience

---

# 5. Automatic Timing

Each span must automatically capture:

```
start_time
end_time
duration
```

Latency measurement must require **zero developer effort**.

---

# 6. Standardized LLM Span Schema

All LLM calls must follow a standard structure.

Required fields:

```
span_type: llm_call
model
provider
prompt
response
input_tokens
output_tokens
latency
temperature
```

Optional fields:

```
top_p
max_tokens
stop_sequences
```

---

# 7. Standardized Tool Span Schema

All tool calls must follow a common schema.

Required fields:

```
span_type: tool_call
tool_name
arguments
result
status
duration
```

Optional fields:

```
external_api
retry_count
```

---

# 8. Error Telemetry

The SDK must capture explicit failure events.

Supported error span types:

```
agent_error
llm_error
tool_error
timeout_error
```

Error fields:

```
error_type
error_message
stack_trace
span_id
```

---

# 9. Exporter Architecture

The SDK must support pluggable exporters.

Supported exporters:

```
ConsoleExporter
FileExporter
HTTPExporter
CollectorExporter
```

Configuration example:

```python
agentobs.configure_exporter(
    type="http",
    endpoint="https://collector.spanforge.ai"
)
```

---

# 10. Sampling Controls

To reduce telemetry overhead, support sampling.

Configuration:

```
sample_rate
error_sampling
trace_filters
```

Example:

```python
agentobs.configure(sample_rate=0.1)
```

---

# 11. Async Execution Support

The SDK must support async execution.

Example:

```python
async with agentobs.llm_call():
    response = await llm(prompt)
```

Required compatibility:

* asyncio
* async frameworks
* streaming LLM responses

---

# 12. Trace Serialization

Provide utilities to export traces.

Supported formats:

```
JSON
NDJSON
AgentOBS schema
```

Example:

```python
trace.save("trace.json")
trace.to_json()
```

---

# 13. Debug Utilities

Provide built-in developer debugging tools.

Functions:

```
trace.print_tree()
trace.summary()
trace.visualize()
```

Example output:

```
Agent Run
 ├─ LLM Call (1.1s)
 ├─ Tool Call: search (0.4s)
 └─ LLM Call (0.9s)
```

---

# 14. Global SDK Configuration

Allow global configuration.

Example:

```python
agentobs.configure(
    service_name="research_agent",
    environment="prod",
    version="1.0"
)
```

Supported fields:

```
service_name
agent_name
environment
version
```

---

# 15. Framework Integration Hooks

Expose hooks for framework integrations.

Supported hooks:

```
on_agent_start()
on_agent_end()
on_llm_call()
on_tool_call()
```

These hooks allow integration with:

* LangChain
* LlamaIndex
* CrewAI

---

# 16. MCP Trace Access APIs

Provide APIs so MCP servers can access traces.

Functions:

```
get_trace(trace_id)
get_last_agent_run()
list_tool_calls(trace_id)
list_llm_calls(trace_id)
```

This enables **self-observing agents**.

---

# 17. Attribute & Tag System (New)

Allow spans to include arbitrary metadata.

Example:

```python
span.set_attribute("task_id", "abc123")
span.set_attribute("dataset", "research_docs")
```

Attributes help build:

* analytics
* dashboards
* filters

---

# 18. Event Logging Inside Spans (New)

Spans must support structured events.

Example:

```python
span.add_event(
    name="retrieval_started",
    metadata={"query": "AI observability"}
)
```

Events capture intermediate activity without creating new spans.

---

# 19. Metrics Extraction API (New)

Provide a way to extract metrics from traces.

Example metrics:

```
agent_success_rate
llm_latency
tool_failure_rate
token_usage
```

Example API:

```python
agentobs.metrics.aggregate(traces)
```

This allows dashboards similar to:

* Prometheus
* Grafana

---

# Final SDK Architecture

```
AgentOBS SDK
--------------------------------

Core
Trace object
Span hierarchy
Context propagation

Instrumentation
Agent runs
LLM calls
Tool calls

Runtime
Async support
Sampling
Configuration

Export
Console exporter
File exporter
HTTP exporter

Developer Tools
Trace visualization
Debug utilities
Serialization

Ecosystem
Framework integrations
MCP APIs
Metrics extraction
```

---

# Strategic Outcome

With this SDK spec implemented:

```
AgentOBS SDK
     ↓
AgentOBS traces
     ↓
Validate
     ↓
Debug
     ↓
Dashboards / MCP / analytics
```

You would have the **foundation for a complete agentic AI observability ecosystem**.

---

If you want, I can also help you design something extremely useful next:

**The AgentOBS Collector (similar to OpenTelemetry Collector)** — which would become the **central ingestion layer for your entire ecosystem.**
