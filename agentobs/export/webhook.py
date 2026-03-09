"""Webhook exporter for agentobs events.

Delivers events (or batches) as JSON HTTP POST requests to a configurable
URL with optional HMAC-SHA256 request signing.

Security
--------
* If ``secret`` is provided every request is signed with
  ``X-AgentOBS-Signature: hmac-sha256:<hex>`` so the receiver can verify
  authenticity.
* The ``secret`` value is **never** included in repr, logs, or exception
  messages.
* Retry logic uses truncated exponential back-off to avoid amplifying load on a
  degraded endpoint.

Transport
---------
Uses :func:`urllib.request.urlopen` in a thread-pool executor so the async
event loop is never blocked.  No external HTTP library is required.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

from agentobs.exceptions import ExportError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentobs.event import Event

__all__ = ["WebhookExporter"]

# Header name for the HMAC-SHA256 request signature.
# Note: kept as the legacy value for backwards-compatibility with existing receivers.
_SIGNATURE_HEADER = "X-AgentOBS-Signature"

# Maximum retry sleep (seconds) — hard ceiling regardless of attempt count.
_MAX_SLEEP: float = 30.0


def _is_private_ip_literal(host: str) -> bool:
    """Return ``True`` if *host* is a private/loopback/link-local **literal** IP.

    DNS hostnames are **not** resolved.  Only dotted-decimal IPv4 or bracketed
    IPv6 literals are evaluated, so ``"localhost"`` passes this check.
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False  # not an IP literal — treat as safe
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast


def _validate_http_url(
    url: str,
    param_name: str = "url",
    *,
    allow_private_addresses: bool = False,
) -> None:
    """Raise *ValueError* if *url* is not a valid ``http://`` or ``https://`` URL.

    When *allow_private_addresses* is ``False`` (default), also rejects URLs
    whose host is a literal private/loopback/link-local IP address.  DNS
    hostnames are **not** resolved so ``http://localhost/`` still passes.
    """
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
                f"({host!r}).  Set allow_private_addresses=True to permit this in "
                f"non-production environments."
            )


def _sign_body(body: bytes, secret: str) -> str:
    """Compute ``hmac-sha256:<hex>`` signature for *body*.

    Args:
        body:   Raw request body bytes.
        secret: HMAC secret string (UTF-8 encoded internally).

    Returns:
        Signature string in the form ``"hmac-sha256:<hexdigest>"``.
    """
    mac = hmac.new(
        secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    )
    return f"hmac-sha256:{mac.hexdigest()}"


class WebhookExporter:
    """Async exporter that sends agentobs events to an HTTP webhook endpoint.

    Each :meth:`export` call delivers a single event as the JSON body.
    :meth:`export_batch` delivers a JSON array.

    Args:
        url:         Destination webhook URL.
        secret:      Optional HMAC-SHA256 signing secret.  When provided, the
                     request includes an ``X-AgentOBS-Signature`` header.
        headers:     Optional extra HTTP request headers.
        timeout:     Per-request timeout in seconds (default 10.0).
        max_retries: Maximum retry attempts on transient failures (default 3).
                     Retries are attempted only for network errors and 5xx
                     responses.  4xx errors are not retried.

    Security:
        The ``secret`` is never included in ``repr()``, log messages, or
        exception strings.

    Example::

        exporter = WebhookExporter(
            url="https://hooks.example.com/events",
            secret="my-hmac-secret",
        )
        await exporter.export(event)
    """

    def __init__(  # noqa: PLR0913
        self,
        url: str,
        *,
        secret: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        allow_private_addresses: bool = False,
    ) -> None:
        if not url:
            raise ValueError("url must be a non-empty string")
        _validate_http_url(url, "url", allow_private_addresses=allow_private_addresses)
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._url = url
        self._secret: str | None = secret
        self._headers: dict[str, str] = dict(headers) if headers else {}
        self._timeout = timeout
        self._max_retries = max_retries

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def export(self, event: Event) -> None:
        """Export a single event as a JSON-encoded HTTP POST.

        Args:
            event: The event to deliver.

        Raises:
            ExportError: If all retry attempts fail.
        """
        body = event.to_json().encode("utf-8")
        await self._post(body, event_id=event.event_id)

    async def export_batch(self, events: Sequence[Event]) -> int:
        """Export multiple events as a JSON array in a single HTTP POST.

        Args:
            events: Sequence of events to deliver.

        Returns:
            Number of events sent.

        Raises:
            ExportError: If all retry attempts fail.
        """
        if not events:
            return 0
        array_json = (
            "["
            + ",".join(e.to_json() for e in events)
            + "]"
        )
        body = array_json.encode("utf-8")
        await self._post(body, event_id="")
        return len(events)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _do_http_post(url: str, body: bytes, headers: dict[str, str], timeout: float, event_id: str) -> None:
        """Execute a single HTTP POST; raises ExportError on failure."""
        req = urllib.request.Request(  # noqa: S310
            url=url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                resp.read()
        except urllib.error.HTTPError as exc:
            raise ExportError("webhook", f"HTTP {exc.code}: {exc.reason}", event_id) from exc
        except OSError as exc:
            raise ExportError("webhook", str(exc), event_id) from exc

    async def _post(self, body: bytes, event_id: str) -> None:
        """POST *body* to :attr:`_url` with retry and optional signing.

        Args:
            body:     Raw request body bytes.
            event_id: Event ID for error context (empty string for batches).

        Raises:
            ExportError: After exhausting all retry attempts.
        """
        request_headers: dict[str, str] = {
            "Content-Type": "application/json",
            **self._headers,
        }
        if self._secret is not None:
            request_headers[_SIGNATURE_HEADER] = _sign_body(body, self._secret)

        url = self._url
        timeout = self._timeout
        last_exc: ExportError | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                # Truncated exponential back-off: 1s, 2s, 4s … capped at 30s.
                sleep_secs = min(2 ** (attempt - 1), _MAX_SLEEP)
                await asyncio.sleep(sleep_secs)

            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(
                    None,
                    lambda: self._do_http_post(url, body, request_headers, timeout, event_id),
                )
            except ExportError as exc:
                last_exc = exc
                if exc.reason.startswith("HTTP 4"):
                    raise
            else:
                return  # success

        assert last_exc is not None  # always set when we reach here
        raise last_exc

    # ------------------------------------------------------------------
    # Repr — secret intentionally omitted
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        has_secret = self._secret is not None
        return (
            f"WebhookExporter(url={self._url!r}, "
            f"signed={has_secret!r}, "
            f"max_retries={self._max_retries!r})"
        )
