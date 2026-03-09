"""Tests for agentobs/export/webhook.py — WebhookExporter.

Coverage targets
----------------
* ``__init__`` argument validation.
* HMAC signature computation (``_sign_body``).
* Signature header present/absent based on ``secret`` parameter.
* Single-event export (``export``).
* Batch export (``export_batch``).
* Retry logic on transient 5xx and network errors.
* No retry on 4xx client errors.
* ``secret`` never appears in ``repr()``.
* Retry limit exhausted → ``ExportError`` raised.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import urllib.error
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentobs.event import Event
from agentobs.exceptions import ExportError
from agentobs.export.webhook import (
    _SIGNATURE_HEADER,
    WebhookExporter,
    _sign_body,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event() -> Event:
    return Event(
        event_type="llm.trace.span.completed",
        source="test-tool@1.0.0",
        payload={"status": "ok"},
    )


def _mock_urlopen_success() -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = b""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=resp)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _make_http_error(code: int, reason: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://example.com",  # NOSONAR
        code=code,
        msg=reason,
        hdrs=None,  # type: ignore[arg-type]
        fp=None,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# _sign_body
# ---------------------------------------------------------------------------


class TestSignBody:
    def test_returns_hmac_sha256_prefix(self) -> None:
        result = _sign_body(b"hello", "secret")
        assert result.startswith("hmac-sha256:")

    def test_signature_is_correct(self) -> None:
        body = b"test-body"
        secret = "my-secret"  # noqa: S105
        expected_mac = hmac.new(
            secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256
        ).hexdigest()
        result = _sign_body(body, secret)
        assert result == f"hmac-sha256:{expected_mac}"

    def test_deterministic(self) -> None:
        assert _sign_body(b"data", "key") == _sign_body(b"data", "key")

    def test_different_bodies_differ(self) -> None:
        assert _sign_body(b"data1", "key") != _sign_body(b"data2", "key")

    def test_different_secrets_differ(self) -> None:
        assert _sign_body(b"data", "key1") != _sign_body(b"data", "key2")


# ---------------------------------------------------------------------------
# WebhookExporter.__init__ validation
# ---------------------------------------------------------------------------


class TestWebhookExporterInit:
    def test_empty_url_raises(self) -> None:
        with pytest.raises(ValueError, match="url"):
            WebhookExporter("")

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            WebhookExporter("http://example.com", timeout=0.0)  # NOSONAR

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            WebhookExporter("http://example.com", timeout=-1.0)  # NOSONAR

    def test_negative_max_retries_raises(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            WebhookExporter("http://example.com", max_retries=-1)  # NOSONAR

    def test_zero_max_retries_allowed(self) -> None:
        exp = WebhookExporter("http://example.com", max_retries=0)  # NOSONAR
        assert exp._max_retries == 0

    def test_invalid_scheme_url_raises(self) -> None:
        with pytest.raises(ValueError, match="url"):
            WebhookExporter("ftp://invalid-scheme.example.com")  # NOSONAR

    def test_defaults(self) -> None:
        exp = WebhookExporter("http://example.com")  # NOSONAR
        assert exp._timeout == pytest.approx(10.0)
        assert exp._max_retries == 3
        assert exp._secret is None

    def test_headers_copied(self) -> None:
        headers = {"X-Custom": "value"}
        exp = WebhookExporter("http://example.com", headers=headers)  # NOSONAR
        headers["X-Extra"] = "extra"
        assert "X-Extra" not in exp._headers


# ---------------------------------------------------------------------------
# WebhookExporter.__repr__  — secret must never appear
# ---------------------------------------------------------------------------


class TestWebhookRepr:
    def test_repr_contains_url(self) -> None:
        exp = WebhookExporter("https://hooks.example.com/events")
        assert "hooks.example.com" in repr(exp)

    def test_repr_does_not_contain_secret(self) -> None:
        exp = WebhookExporter("http://example.com", secret="ultra-secret-value")  # noqa: S106  # NOSONAR
        assert "ultra-secret-value" not in repr(exp)

    def test_repr_shows_signed_true_when_secret_set(self) -> None:
        exp = WebhookExporter("http://example.com", secret="s3cr3t")  # noqa: S106  # NOSONAR
        assert "True" in repr(exp)

    def test_repr_shows_signed_false_without_secret(self) -> None:
        exp = WebhookExporter("http://example.com")  # NOSONAR
        assert "False" in repr(exp)

    def test_repr_shows_max_retries(self) -> None:
        exp = WebhookExporter("http://example.com", max_retries=5)  # NOSONAR
        assert "5" in repr(exp)


# ---------------------------------------------------------------------------
# export — single event
# ---------------------------------------------------------------------------


class TestExportSingleEvent:
    def test_export_makes_post_request(self) -> None:
        exp = WebhookExporter("http://example.com/hook")  # NOSONAR
        event = _make_event()
        captured: list = []

        def _fake(req, timeout=None):
            captured.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            asyncio.run(exp.export(event))

        assert len(captured) == 1
        assert captured[0].get_method() == "POST"
        assert captured[0].full_url == "http://example.com/hook"  # NOSONAR

    def test_export_body_is_event_json(self) -> None:
        exp = WebhookExporter("http://example.com/hook")  # NOSONAR
        event = _make_event()
        captured: list = []

        def _fake(req, timeout=None):
            captured.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            asyncio.run(exp.export(event))

        body = captured[0].data
        assert body == event.to_json().encode("utf-8")

    def test_export_with_secret_adds_signature_header(self) -> None:
        exp = WebhookExporter("http://example.com/hook", secret="my-secret")  # noqa: S106  # NOSONAR
        event = _make_event()
        captured: list = []

        def _fake(req, timeout=None):
            captured.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            asyncio.run(exp.export(event))

        req = captured[0]
        # urllib.Request stores headers with capitalize(): "X-llm-toolkit-schema-Signature" → "X-llm-toolkit-schema-signature"  # noqa: E501
        sig_key = _SIGNATURE_HEADER.capitalize()
        sig_header = req.headers.get(sig_key)
        assert sig_header is not None
        assert sig_header.startswith("hmac-sha256:")

    def test_export_without_secret_no_signature_header(self) -> None:
        exp = WebhookExporter("http://example.com/hook")  # NOSONAR
        event = _make_event()
        captured: list = []

        def _fake(req, timeout=None):
            captured.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            asyncio.run(exp.export(event))

        req = captured[0]
        sig_key = _SIGNATURE_HEADER.capitalize()
        assert req.headers.get(sig_key) is None

    def test_export_signature_is_verifiable(self) -> None:
        secret = "verify-me-secret"  # noqa: S105  # NOSONAR
        exp = WebhookExporter("http://example.com/hook", secret=secret)  # NOSONAR
        event = _make_event()
        captured: list = []

        def _fake(req, timeout=None):
            captured.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            asyncio.run(exp.export(event))

        req = captured[0]
        sig_key = _SIGNATURE_HEADER.capitalize()
        received_sig = req.headers.get(sig_key)
        expected_sig = _sign_body(event.to_json().encode("utf-8"), secret)
        assert received_sig == expected_sig

    def test_export_sets_json_content_type(self) -> None:
        exp = WebhookExporter("http://example.com/hook")  # NOSONAR
        event = _make_event()
        captured: list = []

        def _fake(req, timeout=None):
            captured.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            asyncio.run(exp.export(event))

        assert captured[0].get_header("Content-type") == "application/json"

    def test_export_includes_custom_headers(self) -> None:
        exp = WebhookExporter(
            "http://example.com/hook",  # NOSONAR
            headers={"X-Tenant": "acme"},
        )
        event = _make_event()
        captured: list = []

        def _fake(req, timeout=None):
            captured.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            asyncio.run(exp.export(event))

        assert captured[0].get_header("X-tenant") == "acme"


# ---------------------------------------------------------------------------
# export_batch
# ---------------------------------------------------------------------------


class TestExportBatch:
    def test_batch_sends_json_array(self) -> None:
        exp = WebhookExporter("http://example.com/hook")  # NOSONAR
        events = [_make_event() for _ in range(3)]
        captured: list = []

        def _fake(req, timeout=None):
            captured.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            count = asyncio.run(exp.export_batch(events))

        assert count == 3
        import json  # noqa: PLC0415
        body = json.loads(captured[0].data)
        assert isinstance(body, list)
        assert len(body) == 3

    def test_batch_empty_returns_zero_no_request(self) -> None:
        exp = WebhookExporter("http://example.com/hook")  # NOSONAR
        calls: list = []

        with patch("urllib.request.urlopen", side_effect=calls.append):
            count = asyncio.run(exp.export_batch([]))

        assert count == 0
        assert len(calls) == 0

    def test_batch_signature_over_array_body(self) -> None:
        secret = "batch-secret"  # noqa: S105
        exp = WebhookExporter("http://example.com/hook", secret=secret)  # NOSONAR
        events = [_make_event(), _make_event()]
        captured: list = []

        def _fake(req, timeout=None):
            captured.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            asyncio.run(exp.export_batch(events))

        req = captured[0]
        expected_sig = _sign_body(req.data, secret)
        sig_key = _SIGNATURE_HEADER.capitalize()
        received_sig = req.headers.get(sig_key)
        assert received_sig == expected_sig


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    def test_retries_on_os_error_until_success(self) -> None:
        exp = WebhookExporter(
            "http://example.com/hook",  # NOSONAR
            max_retries=2,
        )
        event = _make_event()
        call_count = 0

        def _fake(req, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OSError("temporary failure")
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):  # noqa: SIM117
            with patch("agentobs.export.webhook.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(exp.export(event))

        assert call_count == 3

    def test_retries_on_5xx_until_success(self) -> None:
        exp = WebhookExporter(
            "http://example.com/hook",  # NOSONAR
            max_retries=2,
        )
        event = _make_event()
        call_count = 0

        def _fake(req, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise _make_http_error(503, "Service Unavailable")
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):  # noqa: SIM117
            with patch("agentobs.export.webhook.asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(exp.export(event))

        assert call_count == 2

    def test_no_retry_on_4xx(self) -> None:
        exp = WebhookExporter(
            "http://example.com/hook",  # NOSONAR
            max_retries=3,
        )
        event = _make_event()
        call_count = 0

        def _fake(req, timeout=None):
            nonlocal call_count
            call_count += 1
            raise _make_http_error(400, "Bad Request")

        with patch("urllib.request.urlopen", side_effect=_fake):  # noqa: SIM117
            with pytest.raises(ExportError) as exc_info:
                asyncio.run(exp.export(event))

        # Should not retry on 4xx.
        assert call_count == 1
        assert "HTTP 4" in exc_info.value.reason

    def test_raises_after_max_retries_exceeded(self) -> None:
        exp = WebhookExporter(
            "http://example.com/hook",  # NOSONAR
            max_retries=2,
        )
        event = _make_event()

        def _fake(req, timeout=None):
            raise OSError("persistent failure")

        with patch("urllib.request.urlopen", side_effect=_fake):  # noqa: SIM117
            with patch("agentobs.export.webhook.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(ExportError) as exc_info:
                    asyncio.run(exp.export(event))

        assert exc_info.value.backend == "webhook"
        assert "persistent failure" in exc_info.value.reason

    def test_export_error_carries_event_id(self) -> None:
        exp = WebhookExporter(
            "http://example.com/hook",  # NOSONAR
            max_retries=0,
        )
        event = _make_event()

        with patch("urllib.request.urlopen", side_effect=OSError("fail")):  # noqa: SIM117
            with pytest.raises(ExportError) as exc_info:
                asyncio.run(exp.export(event))

        assert exc_info.value.event_id == event.event_id

    def test_zero_retries_fails_immediately(self) -> None:
        exp = WebhookExporter(
            "http://example.com/hook",  # NOSONAR
            max_retries=0,
        )
        event = _make_event()
        call_count = 0

        def _fake(req, timeout=None):
            nonlocal call_count
            call_count += 1
            raise OSError("fail")

        with patch("urllib.request.urlopen", side_effect=_fake), pytest.raises(ExportError):
            asyncio.run(exp.export(event))

        assert call_count == 1
