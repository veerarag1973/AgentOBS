"""agentobs.export.datadog — Datadog trace/metric exporter.

Delivers AgentOBS events to the **Datadog Agent** (trace intake on
``/v0.3/traces``) and, when an API key is supplied, to the **Datadog Metrics
API** (``/api/v2/series``).

Transport
---------
Uses :func:`urllib.request.urlopen` in a thread-pool executor so the event
loop is never blocked.  No external dependencies are required — stdlib only.

Traces vs metrics
-----------------
* Every event that carries a ``trace_id`` is forwarded to the Agent's trace
  intake as a Datadog APM span.
* Numeric fields enumerated in :data:`_METRIC_FIELDS` are forwarded to the
  Datadog Metrics API (requires ``api_key``).

Usage::

    from agentobs.export.datadog import DatadogExporter

    exporter = DatadogExporter(
        service="my-llm-app",
        env="production",
        api_key="dd-api-key",
    )
    await exporter.export(event)
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agentobs.exceptions import ExportError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentobs.event import Event

__all__ = [
    "_METRIC_FIELDS",
    "DatadogExporter",
    "DatadogResourceAttributes",
]

# ---------------------------------------------------------------------------
# Metric fields extracted from event payloads
# ---------------------------------------------------------------------------

#: Payload keys that are surfaced as Datadog custom metrics when numeric.
_METRIC_FIELDS: frozenset[str] = frozenset(
    {
        "cost_usd",
        "token_count",
        "latency_ms",
        "duration_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "reasoning_tokens",
    }
)

# ---------------------------------------------------------------------------
# Resource attributes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatadogResourceAttributes:
    """Datadog resource-level metadata emitted as ``key:value`` tags.

    Args:
        service: Datadog ``service`` tag value.
        env:     Datadog ``env`` tag value.
        version: Datadog ``version`` tag value (default ``"0.0.0"``).
        extra:   Additional ``key:value`` tags to emit.
    """

    service: str
    env: str
    version: str = "0.0.0"
    extra: dict[str, str] = field(default_factory=dict)

    def to_tags(self) -> list[str]:
        """Return a list of ``"key:value"`` tag strings."""
        tags = [
            f"service:{self.service}",
            f"env:{self.env}",
            f"version:{self.version}",
        ]
        for k, v in self.extra.items():
            tags.append(f"{k}:{v}")
        return tags


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _is_private_ip_literal(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast


def _validate_http_url(url: str, param_name: str = "url", *, allow_private_addresses: bool = False) -> None:  # noqa: E501
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            f"{param_name} must be a valid http:// or https:// URL; got {url!r}"
        )
    if not allow_private_addresses:
        host = parsed.hostname or ""
        if _is_private_ip_literal(host):
            raise ValueError(
                f"{param_name} resolves to a private/loopback/link-local IP address "
                f"({host!r}).  Set allow_private_addresses=True to permit this."
            )


def _validate_dd_site(dd_site: str) -> None:
    """Raise *ValueError* if *dd_site* is not a plain hostname (no scheme, no spaces, has a dot)."""
    if not dd_site:
        raise ValueError("dd_site must be a non-empty hostname (e.g. 'datadoghq.com'), got empty string")  # noqa: E501
    if "/" in dd_site:
        raise ValueError(
            f"dd_site must be a plain hostname without a URL scheme or path; got {dd_site!r}"
        )
    if " " in dd_site:
        raise ValueError(
            f"dd_site must not contain spaces; got {dd_site!r}"
        )
    if "." not in dd_site:
        raise ValueError(
            f"dd_site must be a fully-qualified hostname with at least one dot; got {dd_site!r}"
        )


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _iso_to_epoch_ns(ts: str) -> int:
    """Convert an ISO-8601 timestamp string to nanoseconds since the Unix epoch."""
    ts_clean = ts.rstrip("Z") if ts.endswith("Z") else ts
    if "+" in ts_clean[10:]:
        ts_clean = ts_clean[: ts_clean.rfind("+")]
    # Pad to microseconds
    if "." not in ts_clean:
        ts_clean += ".000000"
    try:
        dt = datetime.strptime(ts_clean, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ExportError(
            "datadog",
            f"cannot parse event timestamp {ts!r}: {exc}",
        ) from exc
    return int(dt.timestamp() * 1_000_000_000)


def _iso_to_epoch_us(ts: str) -> int:
    """Return microseconds since epoch (used for Datadog span start time)."""
    return _iso_to_epoch_ns(ts) // 1_000


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


def _make_span_id() -> int:
    """Generate a random 64-bit span ID as an unsigned integer."""
    return secrets.randbits(64)


def _trace_id_to_int(trace_id: str | None) -> int:
    """Convert a hex trace-id string to an unsigned 64-bit integer (low 64 bits)."""
    if not trace_id:
        return _make_span_id()
    try:
        # Datadog uses 64-bit trace IDs; take the low 64 bits
        return int(trace_id[-16:], 16)
    except (ValueError, TypeError):
        return _make_span_id()


def _span_id_to_int(span_id: str | None) -> int:
    """Convert a hex span-id string to an unsigned 64-bit integer."""
    if not span_id:
        return _make_span_id()
    try:
        return int(span_id[-16:], 16)
    except (ValueError, TypeError):
        return _make_span_id()


# ---------------------------------------------------------------------------
# Main exporter
# ---------------------------------------------------------------------------


class DatadogExporter:
    """Async exporter that sends AgentOBS events to Datadog.

    Events with a ``trace_id`` are forwarded to the Datadog Agent as APM
    spans.  Numeric fields listed in :data:`_METRIC_FIELDS` are sent as
    custom metrics (requires ``api_key``).

    Args:
        service:    Datadog ``service`` tag.
        env:        Datadog ``env`` tag.
        agent_url:  Datadog Agent base URL (default ``"http://localhost:8126"``).
        api_key:    Datadog API key for the Metrics API (optional).
        dd_site:    Datadog site hostname used for Metrics API
                    (e.g. ``"datadoghq.com"``).  Required when ``api_key``
                    is provided and you want metrics to go to DD cloud.
        timeout:    Per-request timeout in seconds (default 10.0).

    Raises:
        ValueError: If any constructor argument fails validation.
    """

    def __init__(  # noqa: PLR0913
        self,
        service: str,
        env: str = "production",
        *,
        agent_url: str = "http://localhost:8126",  # NOSONAR
        api_key: str | None = None,
        dd_site: str | None = None,
        timeout: float = 10.0,
        allow_private_addresses: bool = False,
    ) -> None:
        if not service:
            raise ValueError("service must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        _validate_http_url(agent_url, "agent_url", allow_private_addresses=allow_private_addresses)
        if dd_site is not None:
            _validate_dd_site(dd_site)

        self._service = service
        self._env = env
        self._agent_url = agent_url.rstrip("/")
        self._api_key: str | None = api_key
        self._dd_site: str | None = dd_site
        self._timeout = timeout
        self._resource = DatadogResourceAttributes(service=service, env=env)

    # ------------------------------------------------------------------
    # Public conversion API
    # ------------------------------------------------------------------

    def to_dd_span(self, event: Event) -> dict[str, Any]:
        """Convert a AgentOBS :class:`~agentobs.event.Event` to a Datadog APM span dict.

        Args:
            event: The event to convert.

        Returns:
            A dict compatible with the Datadog Agent v0.3/traces payload.
        """
        start_ns = _iso_to_epoch_ns(event.timestamp)
        duration_ns = int(event.payload.get("duration_ms", 0) * 1_000_000)

        trace_id = _trace_id_to_int(event.trace_id)
        span_id = _span_id_to_int(event.span_id)

        meta: dict[str, str] = {
            "llm.source": event.source,
            "llm.event_type": str(event.event_type),
        }
        if event.org_id:
            meta["llm.org_id"] = event.org_id
        if event.team_id:
            meta["llm.team_id"] = event.team_id
        if event.actor_id:
            meta["llm.actor_id"] = event.actor_id
        if event.session_id:
            meta["llm.session_id"] = event.session_id

        # Surface tags — Tags is dict-like, use .get() not getattr
        if event.tags:
            for tag_field in ("env", "model", "region", "version"):
                val = event.tags.get(tag_field, None)
                if val:
                    meta[f"llm.tag.{tag_field}"] = str(val)

        # Flatten top-level payload string fields into meta
        for k, v in event.payload.items():
            if isinstance(v, str):
                meta[f"llm.{k}"] = v

        return {
            "name": str(event.event_type),
            "service": self._service,
            "resource": str(event.event_type),
            "type": "custom",
            "trace_id": trace_id,
            "span_id": span_id,
            "start": start_ns,
            "duration": max(0, duration_ns),
            "meta": meta,
            "error": 0,
        }

    def to_dd_metric_series(self, event: Event) -> list[dict[str, Any]]:
        """Extract numeric payload fields as Datadog metric series entries.

        Only fields listed in :data:`_METRIC_FIELDS` with non-bool numeric
        values are emitted.  Returns an empty list if none qualify.

        Args:
            event: The event to inspect.

        Returns:
            A list of Datadog metric series dicts (may be empty).
        """
        series: list[dict[str, Any]] = []
        ts_sec = _iso_to_epoch_ns(event.timestamp) // 1_000_000_000
        tags = list(self._resource.to_tags())
        if event.org_id:
            tags.append(f"org:{event.org_id}")

        for key, value in event.payload.items():
            if key not in _METRIC_FIELDS:
                continue
            # Skip booleans — they satisfy isinstance(v, (int, float)) on Python
            if isinstance(value, bool):
                continue
            if not isinstance(value, (int, float)):
                continue
            series.append(
                {
                    "metric": f"llm.{key}",
                    "type": 0,
                    "points": [{"timestamp": ts_sec, "value": float(value)}],
                    "tags": tags,
                }
            )
        return series

    async def export(self, event: Event) -> None:
        """Export a single event to Datadog.

        Sends as an APM trace span when ``event.trace_id`` is set.  Additionally
        sends any numeric metric fields to the Metrics API if ``api_key`` is set.

        Args:
            event: The event to export.

        Raises:
            ExportError: On HTTP or network errors.
        """
        tasks = []

        if event.trace_id:
            tasks.append(self._send_traces([event]))

        metric_series = self.to_dd_metric_series(event)
        if metric_series and self._api_key:
            tasks.append(self._send_metrics(metric_series))

        if tasks:
            await asyncio.gather(*tasks)

    async def export_batch(self, events: Sequence[Event]) -> None:
        """Export multiple events to Datadog in parallel.

        Args:
            events: Sequence of events to deliver.
        """
        if not events:
            return

        trace_events = [e for e in events if e.trace_id]
        if trace_events:
            await self._send_traces(trace_events)

        if self._api_key:
            all_series: list[dict[str, Any]] = []
            for event in events:
                all_series.extend(self.to_dd_metric_series(event))
            if all_series:
                await self._send_metrics(all_series)

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    async def _send_traces(self, events: Sequence[Event]) -> None:
        """Send *events* to the Datadog Agent trace intake.

        Args:
            events: Events to convert to APM spans and send.

        Raises:
            ExportError: On HTTP or network errors.
        """
        spans = [self.to_dd_span(e) for e in events]
        # Agent expects: [[span, span, ...]] — list of traces, each trace is a list of spans
        payload = json.dumps([spans]).encode("utf-8")

        url = f"{self._agent_url}/v0.3/traces"
        headers = {
            "Content-Type": "application/json",
            "Datadog-Meta-Lang": "python",
        }

        await asyncio.get_event_loop().run_in_executor(None, lambda: self._do_post(url, payload, headers, "datadog-traces"))  # noqa: E501

    async def _send_metrics(self, series: list[dict[str, Any]]) -> None:
        """Send *series* to the Datadog Metrics API.

        Args:
            series: List of metric series dicts.

        Raises:
            ExportError: On HTTP or network errors.
        """
        dd_site = self._dd_site or "datadoghq.com"
        url = f"https://api.{dd_site}/api/v2/series"
        payload = json.dumps({"series": series}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "DD-API-KEY": self._api_key or "",
        }
        await asyncio.get_event_loop().run_in_executor(None, lambda: self._do_post(url, payload, headers, "datadog-metrics"))  # noqa: E501

    def _do_post(self, url: str, body: bytes, headers: dict[str, str], context: str) -> None:
        """Perform a synchronous HTTP POST (called in executor).

        Args:
            url:     Target URL.
            body:    Request body bytes.
            headers: HTTP headers.
            context: Human-readable context for error messages.

        Raises:
            ExportError: On HTTP or network failure.
        """
        req = urllib.request.Request(url=url, data=body, headers=headers, method="POST")  # noqa: S310  # NOSONAR
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310  # NOSONAR
                resp.read()
        except urllib.error.HTTPError as exc:
            raise ExportError(
                "datadog", f"HTTP {exc.code} from {url}: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise ExportError("datadog", f"network error posting to {url}: {exc}") from exc

    # ------------------------------------------------------------------
    # dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"DatadogExporter(service={self._service!r}, env={self._env!r}, "
            f"agent_url={self._agent_url!r})"
        )
