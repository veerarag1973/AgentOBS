"""agentobs.debug — Developer experience utilities for inspecting traces.

Provides three standalone functions (also wired as methods on
:class:`~agentobs._trace.Trace`):

- :func:`print_tree` — pretty-print a hierarchical span tree to stdout.
- :func:`summary`    — return an aggregated statistics dict.
- :func:`visualize`  — generate a self-contained HTML Gantt timeline.

All three accept either a list of
:class:`~agentobs.namespaces.trace.SpanPayload` objects (the *serialised*
form used for storage/export) or a list of
:class:`~agentobs._span.Span` objects (the *live* form held by a
:class:`~agentobs._trace.Trace`).  Mixed lists are not supported.

Usage::

    from agentobs import start_trace, print_tree, summary, visualize

    with start_trace("research-agent") as trace:
        ...

    # After the trace ends its spans are collected internally:
    trace.print_tree()
    stats = trace.summary()
    html = trace.visualize()

    # Or pass raw spans from a JSONL file:
    from agentobs.stream import iter_file
    from agentobs.namespaces.trace import SpanPayload
    spans = [SpanPayload.from_dict(e.payload) for e in iter_file("events.jsonl")]
    print_tree(spans)
"""

from __future__ import annotations

import os
import html as _html_mod
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Sequence, Union

if TYPE_CHECKING:
    from agentobs._span import Span
    from agentobs.namespaces.trace import SpanPayload

__all__ = ["print_tree", "summary", "visualize"]

# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

# Union accepted by all public functions.
_SpanLike = Union["SpanPayload", "Span"]


def _to_payload(span: _SpanLike) -> "SpanPayload":
    """Coerce a live *Span* to a *SpanPayload* so we always work with one type."""
    from agentobs.namespaces.trace import SpanPayload  # noqa: PLC0415

    if isinstance(span, SpanPayload):
        return span
    # Assume Span (live)
    return span.to_span_payload()


def _coerce(spans: Sequence[_SpanLike]) -> list["SpanPayload"]:
    return [_to_payload(s) for s in spans]


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

_ANSI_GREEN = "\033[92m"
_ANSI_YELLOW = "\033[93m"
_ANSI_RED = "\033[91m"
_ANSI_RESET = "\033[0m"
_ANSI_BOLD = "\033[1m"
_ANSI_DIM = "\033[2m"


def _no_color() -> bool:
    """Return True when colour output should be suppressed."""
    return bool(os.environ.get("NO_COLOR") or os.environ.get("AGENTOBS_NO_COLOR"))


def _color(text: str, code: str) -> str:
    if _no_color():
        return text
    return f"{code}{text}{_ANSI_RESET}"


# ---------------------------------------------------------------------------
# print_tree()
# ---------------------------------------------------------------------------

# Box-drawing characters for the tree lines.
_BRANCH = "├─ "
_LAST = "└─ "
_PIPE = "│  "
_SPACE = "   "


def _status_badge(status: str) -> str:
    if status == "ok":
        return _color("ok", _ANSI_GREEN)
    if status == "error":
        return _color("error", _ANSI_RED)
    if status == "timeout":
        return _color("timeout", _ANSI_YELLOW)
    return status


def _span_label(p: "SpanPayload") -> str:
    """Build the single-line description of a span used in the tree."""
    model_str = ""
    if p.model is not None:
        model_name = getattr(p.model, "name", None) or str(p.model)
        model_str = f" [{model_name}]"

    dur = f"  {p.duration_ms:.0f}ms" if p.duration_ms else ""

    token_str = ""
    if p.token_usage is not None:
        inp = getattr(p.token_usage, "input_tokens", None) or getattr(p.token_usage, "prompt_tokens", None) or 0
        out = getattr(p.token_usage, "output_tokens", None) or getattr(p.token_usage, "completion_tokens", None) or 0
        if inp or out:
            token_str = f"  in={inp} out={out} tok"

    cost_str = ""
    if p.cost is not None:
        total = getattr(p.cost, "total_cost_usd", None) or 0.0
        if total:
            cost_str = f"  ${total:.4f}"

    error_str = ""
    if p.error:
        err_short = p.error[:40] + ("…" if len(p.error) > 40 else "")
        error_str = f"  {_color(err_short, _ANSI_RED)}"

    events_str = ""
    if p.events:
        events_str = f"  [{len(p.events)} event{'s' if len(p.events) != 1 else ''}]"

    badge = _status_badge(p.status)
    name = _color(p.span_name, _ANSI_BOLD)
    return f"{name}{model_str}  {badge}{dur}{token_str}{cost_str}{events_str}{error_str}"


def _dfs_print(
    span_id: str,
    children: dict[str | None, list["SpanPayload"]],
    payloads_by_id: dict[str, "SpanPayload"],
    prefix: str,
    is_last: bool,
    lines: list[str],
) -> None:
    p = payloads_by_id[span_id]
    connector = _LAST if is_last else _BRANCH
    lines.append(prefix + connector + _span_label(p))
    child_prefix = prefix + (_SPACE if is_last else _PIPE)
    kids = children.get(span_id, [])
    for i, child in enumerate(kids):
        _dfs_print(
            child.span_id,
            children,
            payloads_by_id,
            child_prefix,
            i == len(kids) - 1,
            lines,
        )


def print_tree(
    spans: Sequence[_SpanLike],
    *,
    trace_id: str | None = None,
    file: Any = None,
) -> None:
    """Pretty-print a hierarchical span tree.

    Example output::

        Agent Run: research-agent  [2.4s]
         ├─ llm_call:gpt-4o  [gpt-4o]  ok  1100ms  in=512 out=200 tok  $0.0031
         ├─ tool_call:search  ok  400ms
         │   └─ tool_call:fetch_url  ok  200ms
         └─ llm_call:gpt-4o  [gpt-4o]  ok  900ms  in=300 out=150 tok  $0.0021

    Args:
        spans:    Spans to render.  All spans in the same trace are shown;
                  use *trace_id* to filter when *spans* contains multiple traces.
        trace_id: Optional filter — show only spans with this trace ID.
        file:     Output file (default: ``sys.stdout``).
    """
    import sys  # noqa: PLC0415

    payloads = _coerce(spans)
    if not payloads:
        print("(no spans)", file=file or sys.stdout)
        return

    if trace_id is not None:
        payloads = [p for p in payloads if p.trace_id == trace_id]
        if not payloads:
            print(f"(no spans for trace_id={trace_id!r})", file=file or sys.stdout)
            return

    # Sort by start time.
    payloads = sorted(payloads, key=lambda p: p.start_time_unix_nano)

    payloads_by_id: dict[str, "SpanPayload"] = {p.span_id: p for p in payloads}
    children: dict[str | None, list["SpanPayload"]] = defaultdict(list)
    for p in payloads:
        children[p.parent_span_id].append(p)

    # Roots are spans whose parent is either None or absent from this set.
    roots = [p for p in payloads if p.parent_span_id not in payloads_by_id]

    out = file or sys.stdout
    lines: list[str] = []

    # Print a header for each trace encountered.
    traces_seen: set[str] = set()
    root_groups: dict[str, list["SpanPayload"]] = defaultdict(list)
    for r in roots:
        root_groups[r.trace_id].append(r)

    for tid, trace_roots in root_groups.items():
        if tid not in traces_seen:
            traces_seen.add(tid)
            # Compute total trace duration from first start to last end.
            trace_spans = [p for p in payloads if p.trace_id == tid]
            total_ms = sum(p.duration_ms for p in trace_spans)
            total_s = total_ms / 1000.0
            header = _color(f"Trace {tid[:8]}…  total≈{total_s:.1f}s", _ANSI_BOLD)
            lines.append(header)
        for i, root in enumerate(trace_roots):
            _dfs_print(
                root.span_id,
                children,
                payloads_by_id,
                "",
                i == len(trace_roots) - 1,
                lines,
            )

    print("\n".join(lines), file=out)


# ---------------------------------------------------------------------------
# summary()
# ---------------------------------------------------------------------------


def _sum_token_usage(payloads: list["SpanPayload"]) -> tuple[int, int]:
    """Sum input and output tokens across all payloads."""
    total_in = total_out = 0
    for p in payloads:
        if p.token_usage is not None:
            inp = getattr(p.token_usage, "input_tokens", None) or getattr(p.token_usage, "prompt_tokens", None) or 0
            out = getattr(p.token_usage, "output_tokens", None) or getattr(p.token_usage, "completion_tokens", None) or 0
            total_in += int(inp)
            total_out += int(out)
    return total_in, total_out


def _sum_costs(payloads: list["SpanPayload"]) -> float:
    """Sum total_cost_usd across all payloads."""
    total = 0.0
    for p in payloads:
        if p.cost is not None:
            total += getattr(p.cost, "total_cost_usd", None) or 0.0
    return total


def summary(spans: Sequence[_SpanLike]) -> dict[str, Any]:
    """Return an aggregated statistics dict for the given spans.

    Example::

        {
            "trace_id": "ab12cd34...",
            "span_count": 4,
            "llm_calls": 2,
            "tool_calls": 1,
            "total_duration_ms": 2400.0,
            "total_input_tokens": 812,
            "total_output_tokens": 350,
            "total_cost_usd": 0.0052,
            "error_count": 0,
            "timeout_count": 0,
        }

    When *spans* cover multiple traces, per-trace values are not returned;
    use :func:`print_tree` or filter before calling.

    Args:
        spans: Spans to summarise.

    Returns:
        A plain ``dict``.  All numeric fields are 0 / 0.0 when no data
        is available (never ``None``).
    """
    payloads = _coerce(spans)
    if not payloads:
        return {
            "trace_id": None,
            "span_count": 0,
            "llm_calls": 0,
            "tool_calls": 0,
            "total_duration_ms": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0.0,
            "error_count": 0,
            "timeout_count": 0,
        }

    trace_ids = {p.trace_id for p in payloads}
    dominant_trace_id = payloads[0].trace_id if len(trace_ids) == 1 else None

    llm_ops = {"chat", "text_completion", "embeddings", "image_generation", "invoke_agent", "create_agent", "reasoning"}
    llm_calls = sum(
        1 for p in payloads
        if (str(p.operation.value if hasattr(p.operation, "value") else p.operation)).lower() in llm_ops
    )
    tool_calls = sum(
        1 for p in payloads
        if (str(p.operation.value if hasattr(p.operation, "value") else p.operation)).lower() == "execute_tool"
    )
    total_duration_ms = sum(p.duration_ms for p in payloads)

    total_input_tokens, total_output_tokens = _sum_token_usage(payloads)
    total_cost_usd = _sum_costs(payloads)

    error_count = sum(1 for p in payloads if p.status == "error")
    timeout_count = sum(1 for p in payloads if p.status == "timeout")

    return {
        "trace_id": dominant_trace_id,
        "span_count": len(payloads),
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "total_duration_ms": total_duration_ms,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cost_usd": total_cost_usd,
        "error_count": error_count,
        "timeout_count": timeout_count,
    }


# ---------------------------------------------------------------------------
# visualize()
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AgentOBS Trace Visualizer</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #111; color: #eee;
          margin: 0; padding: 16px; }}
  h1   {{ font-size: 1.1rem; color: #aaa; margin: 0 0 12px; }}
  .chart {{ position: relative; overflow-x: auto; }}
  .row  {{ display: flex; align-items: center; min-height: 28px;
            border-bottom: 1px solid #222; }}
  .row:hover {{ background: #1a1a1a; }}
  .label {{ flex: 0 0 220px; font-size: 0.78rem; padding: 2px 8px 2px 0;
             white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
             color: #ccc; }}
  .label .model {{ color: #888; font-size: 0.72rem; }}
  .bar-wrap {{ flex: 1 1 auto; position: relative; height: 18px; }}
  .bar  {{ position: absolute; height: 100%; border-radius: 3px;
           display: flex; align-items: center; padding: 0 4px;
           font-size: 0.68rem; white-space: nowrap; overflow: hidden;
           box-sizing: border-box; }}
  .bar.ok      {{ background: #1f6f3a; color: #7effa4; }}
  .bar.error   {{ background: #7a1e1e; color: #ffaaaa; }}
  .bar.timeout {{ background: #7a5c00; color: #ffe080; }}
  .legend  {{ margin-top: 14px; display: flex; gap: 16px; font-size: 0.76rem; }}
  .leg-dot {{ width: 12px; height: 12px; border-radius: 2px; display: inline-block; margin-right: 4px; }}
  .leg-ok  {{ background: #1f6f3a; }}
  .leg-err {{ background: #7a1e1e; }}
  .leg-to  {{ background: #7a5c00; }}
  .stats   {{ margin-top: 14px; font-size: 0.8rem; color: #aaa; }}
  .stats b {{ color: #ddd; }}
</style>
</head>
<body>
<h1>AgentOBS — Trace Visualizer</h1>
<div class="chart">
  {rows}
</div>
<div class="legend">
  <span><span class="leg-dot leg-ok"></span>ok</span>
  <span><span class="leg-dot leg-err"></span>error</span>
  <span><span class="leg-dot leg-to"></span>timeout</span>
</div>
<div class="stats">{stats}</div>
</body>
</html>
"""


def _build_span_row_html(p: "SpanPayload", t_min: int, total_range: int) -> str:
    """Build the HTML row string for a single span in the Gantt chart."""
    left_pct = (p.start_time_unix_nano - t_min) / total_range * 100
    width_pct = max((p.end_time_unix_nano - p.start_time_unix_nano) / total_range * 100, 0.3)
    css_class = p.status if p.status in {"ok", "error", "timeout"} else "ok"

    label_text = _html_mod.escape(p.span_name)
    model_part = ""
    if p.model is not None:
        model_name = getattr(p.model, "name", None) or str(p.model)
        model_part = f'<span class="model"> [{_html_mod.escape(str(model_name))}]</span>'

    bar_label = f"{p.duration_ms:.0f}ms"
    if p.token_usage is not None:
        inp = getattr(p.token_usage, "input_tokens", None) or 0
        out = getattr(p.token_usage, "output_tokens", None) or 0
        if inp or out:
            bar_label += f" in={inp} out={out}"

    title_attr = _html_mod.escape(
        f"{p.span_name}  {p.status}  {p.duration_ms:.1f}ms"
        + (f"  {p.error}" if p.error else "")
    )
    return (
        f'<div class="row">'
        f'<div class="label">{label_text}{model_part}</div>'
        f'<div class="bar-wrap">'
        f'<div class="bar {css_class}" title="{title_attr}" '
        f'style="left:{left_pct:.3f}%;width:{width_pct:.3f}%">'
        f'{_html_mod.escape(bar_label)}'
        f'</div>'
        f'</div>'
        f'</div>'
    )


def visualize(
    spans: Sequence[_SpanLike],
    *,
    output: str = "html",
    path: str | None = None,
) -> str:
    """Generate a self-contained HTML Gantt timeline for *spans*.

    The output is pure HTML/CSS — no external dependencies, no JavaScript
    required.  Spans are rendered as proportionally-sized bars on a shared
    timeline axis.

    Args:
        spans:  Spans to visualise.
        output: Currently only ``"html"`` is supported (reserved for future
                formats such as ``"svg"``).
        path:   Optional file path.  When provided the HTML is written to
                this file **in addition to** being returned as a string.

    Returns:
        A self-contained HTML string.

    Raises:
        ValueError: If *output* is not ``"html"``.
    """
    if output != "html":
        raise ValueError(f"visualize: unsupported output format {output!r}. Only 'html' is supported.")

    payloads = _coerce(spans)
    if not payloads:
        html_out = _HTML_TEMPLATE.format(rows="<p style='color:#888'>No spans to display.</p>", stats="")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html_out)
        return html_out

    payloads = sorted(payloads, key=lambda p: p.start_time_unix_nano)

    t_min = payloads[0].start_time_unix_nano
    t_max = max(p.end_time_unix_nano for p in payloads)
    total_range = max(t_max - t_min, 1)  # avoid divide-by-zero

    rows_html = [_build_span_row_html(p, t_min, total_range) for p in payloads]

    stats = summary(payloads)
    stats_html = (
        f"<b>{stats['span_count']}</b> spans  "
        f"<b>{stats['llm_calls']}</b> LLM calls  "
        f"<b>{stats['tool_calls']}</b> tool calls  "
        f"total <b>{stats['total_duration_ms']:.0f}ms</b>  "
        f"tokens in=<b>{stats['total_input_tokens']}</b> "
        f"out=<b>{stats['total_output_tokens']}</b>  "
        f"cost <b>${stats['total_cost_usd']:.4f}</b>  "
        f"errors=<b>{stats['error_count']}</b>"
    )

    html_out = _HTML_TEMPLATE.format(rows="\n  ".join(rows_html), stats=stats_html)

    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html_out)

    return html_out
