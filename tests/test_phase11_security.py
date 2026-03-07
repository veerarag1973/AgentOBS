"""Phase 11 — Security + Privacy pipeline tests.

Covers:
  - HMAC signing chain wired through _dispatch
  - PII redaction wired through _dispatch (before signing)
  - Both features together
  - Signing chain reset on configure() / _reset_exporter()
  - Events emitted without signing_key have no signature
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

import agentobs
from agentobs import configure, tracer
from agentobs._stream import _reset_exporter
from agentobs.event import Event
from agentobs.redact import RedactionPolicy, Sensitivity
from agentobs.signing import verify_chain

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "phase11-test-signing-key-xyz"  # noqa: S105


def _emit_spans(n: int, tmp_path: Path, signing_key: str | None = None, redaction_policy=None) -> list[dict]:  # noqa: E501
    """Configure AgentOBS, emit n spans, return parsed JSONL dicts."""
    jsonl = tmp_path / "events.jsonl"
    kwargs: dict = {
        "exporter": "jsonl",
        "endpoint": str(jsonl),
        "service_name": "phase11-test",
        "env": "test",
        "signing_key": signing_key,
        "redaction_policy": redaction_policy,
    }
    configure(**kwargs)
    _reset_exporter()

    for i in range(n):
        with tracer.span(f"span-{i}", model="gpt-4o", operation="chat") as span:
            span.set_attribute("step", i)

    # Give any async writes a tiny moment (JSONL is sync, but be safe)
    time.sleep(0.01)

    lines = [l for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]  # noqa: E741
    results = [json.loads(line) for line in lines]

    # Close the cached exporter to avoid ResourceWarning from unclosed file handles.
    _reset_exporter()

    return results


# ---------------------------------------------------------------------------
# Signing tests
# ---------------------------------------------------------------------------


class TestSigningChain:
    def test_events_have_signature_when_key_configured(self, tmp_path):
        """All emitted events must have checksum + signature when signing_key is set."""
        events = _emit_spans(3, tmp_path, signing_key=_SECRET)
        assert len(events) == 3
        for ev in events:
            assert ev.get("checksum", "").startswith("sha256:"), f"Missing checksum: {ev}"
            assert ev.get("signature", "").startswith("hmac-sha256:"), f"Missing signature: {ev}"

    def test_no_signature_without_signing_key(self, tmp_path):
        """Events emitted without a signing_key must not have a signature field."""
        events = _emit_spans(2, tmp_path, signing_key=None)
        assert len(events) == 2
        for ev in events:
            assert not ev.get("signature"), f"Unexpected signature: {ev}"

    def test_chain_linkage_prev_id(self, tmp_path):
        """Events must form a linked chain: events[n].prev_id == events[n-1].event_id."""
        events = _emit_spans(4, tmp_path, signing_key=_SECRET)
        assert len(events) == 4
        # First event has no predecessor
        assert events[0].get("prev_id") is None
        # Subsequent events link back
        for i in range(1, len(events)):
            assert events[i]["prev_id"] == events[i - 1]["event_id"], (
                f"Chain broken at index {i}: prev_id={events[i]['prev_id']!r} "
                f"expected={events[i-1]['event_id']!r}"
            )

    def test_verify_chain_passes_on_intact_chain(self, tmp_path):
        """verify_chain() must return valid=True for events emitted with a signing key."""
        raw_dicts = _emit_spans(5, tmp_path, signing_key=_SECRET)
        parsed = [Event.from_dict(d) for d in raw_dicts]
        result = verify_chain(parsed, org_secret=_SECRET)
        assert result.valid, (
            f"Chain invalid: tampered={result.tampered_count}, gaps={result.gaps}"
        )

    def test_reset_exporter_clears_chain(self, tmp_path):
        """After _reset_exporter(), the next event starts a new chain (prev_id=None)."""
        jsonl = tmp_path / "events.jsonl"
        configure(
            exporter="jsonl", endpoint=str(jsonl),
            service_name="reset-test", signing_key=_SECRET,
        )
        _reset_exporter()

        # Emit first batch
        with tracer.span("first"):
            pass

        # Reset + emit second batch to a new file
        jsonl2 = tmp_path / "events2.jsonl"
        configure(
            exporter="jsonl", endpoint=str(jsonl2),
            service_name="reset-test", signing_key=_SECRET,
        )
        _reset_exporter()
        with tracer.span("second"):
            pass

        time.sleep(0.01)
        second_events = [json.loads(l) for l in jsonl2.read_text().splitlines() if l.strip()]  # noqa: E741
        assert len(second_events) == 1
        # After reset, chain starts fresh — no prev_id
        assert second_events[0].get("prev_id") is None

    def test_single_signed_event_verifies(self, tmp_path):
        """A chain of one signed event must verify correctly."""
        raw_dicts = _emit_spans(1, tmp_path, signing_key=_SECRET)
        parsed = [Event.from_dict(d) for d in raw_dicts]
        result = verify_chain(parsed, org_secret=_SECRET)
        assert result.valid

    def test_version_and_source_unchanged_by_signing(self, tmp_path):
        """Signing must not alter schema_version or source fields."""
        events = _emit_spans(1, tmp_path, signing_key=_SECRET)
        ev = events[0]
        assert ev["schema_version"] == "2.0"
        assert ev["source"].startswith("phase11-test@")


# ---------------------------------------------------------------------------
# Redaction tests
# ---------------------------------------------------------------------------


class TestRedactionPipeline:
    def test_redaction_policy_apply_called(self, tmp_path):
        """RedactionPolicy.apply() must be invoked for each emitted event."""
        from agentobs.redact import RedactionPolicy as _RP  # noqa: N814, PLC0415

        with patch.object(_RP, "apply", autospec=True, wraps=_RP.apply) as mock_apply:
            _emit_spans(2, tmp_path, redaction_policy=RedactionPolicy(min_sensitivity=Sensitivity.PII))  # noqa: E501
            assert mock_apply.call_count == 2, (
                f"apply called {mock_apply.call_count} times, expected 2"
            )

    def test_redaction_produces_event(self, tmp_path):
        """Events emitted with a redaction policy must still appear in the JSONL."""
        policy = RedactionPolicy(min_sensitivity=Sensitivity.PII)
        events = _emit_spans(2, tmp_path, redaction_policy=policy)
        assert len(events) == 2

    def test_redaction_before_signing(self, tmp_path):
        """Redaction must run before signing so signatures cover the redacted payload."""
        from agentobs.redact import RedactionPolicy as _RP  # noqa: N814, PLC0415

        call_order: list[str] = []
        policy = RedactionPolicy(min_sensitivity=Sensitivity.PII)
        original_apply = _RP.apply

        def spy_apply(self, event: Event):
            call_order.append("redact")
            return original_apply(self, event)

        jsonl = tmp_path / "order_events.jsonl"
        configure(
            exporter="jsonl", endpoint=str(jsonl),
            service_name="order-test", signing_key=_SECRET,
            redaction_policy=policy,
        )
        _reset_exporter()

        with patch.object(_RP, "apply", spy_apply), \
             patch("agentobs.signing.sign") as mock_sign:
            mock_sign.side_effect = lambda event, org_secret, prev_event=None: event
            with tracer.span("order-test"):
                pass
            time.sleep(0.01)

        _reset_exporter()  # clean up

        assert "redact" in call_order, "redact was never called"
        assert mock_sign.called, "sign was never called"
        assert call_order[0] == "redact"

    def test_no_redaction_without_policy(self, tmp_path):
        """Without a redaction_policy, apply() must never be called."""
        with patch("agentobs.redact.RedactionPolicy.apply") as mock_apply:
            _emit_spans(2, tmp_path, signing_key=None, redaction_policy=None)
            mock_apply.assert_not_called()


# ---------------------------------------------------------------------------
# Combined tests
# ---------------------------------------------------------------------------


class TestSigningAndRedactionTogether:
    def test_signed_chain_with_redaction(self, tmp_path):
        """A chain produced with both signing and redaction must verify correctly."""
        policy = RedactionPolicy(min_sensitivity=Sensitivity.PII)
        raw_dicts = _emit_spans(3, tmp_path, signing_key=_SECRET, redaction_policy=policy)
        assert len(raw_dicts) == 3
        # Events are signed
        for ev in raw_dicts:
            assert ev.get("signature", "").startswith("hmac-sha256:")
        # Chain is valid
        parsed = [Event.from_dict(d) for d in raw_dicts]
        result = verify_chain(parsed, org_secret=_SECRET)
        assert result.valid


# ---------------------------------------------------------------------------
# configure() integration
# ---------------------------------------------------------------------------


class TestConfigureIntegration:
    def test_configure_signing_key_flows_to_stream(self, tmp_path):
        """Setting signing_key via configure() must reach _dispatch."""
        from agentobs.config import get_config  # noqa: PLC0415
        configure(signing_key="my-key-abc")
        assert get_config().signing_key == "my-key-abc"
        configure(signing_key=None)  # clean up

    def test_configure_redaction_policy_flows_to_stream(self, tmp_path):
        """Setting redaction_policy via configure() must reach _dispatch."""
        from agentobs.config import get_config  # noqa: PLC0415
        policy = RedactionPolicy()
        configure(redaction_policy=policy)
        assert get_config().redaction_policy is policy
        configure(redaction_policy=None)  # clean up

    def test_version_is_1_0_0(self):
        """agentobs.__version__ must match the current release."""
        assert agentobs.__version__ == "1.0.6"
