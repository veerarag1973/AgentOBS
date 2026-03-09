"""Tests for agentobs.ulid — 100% coverage target.

Test categories
---------------
* unit   — fast, isolated function tests
* perf   — performance regression guards
* security — entropy and CSPRNG verification
"""

from __future__ import annotations

import re
import threading
import time
from unittest.mock import patch

import pytest

from agentobs.exceptions import ULIDError
from agentobs.ulid import (
    _ALPHABET,
    _MAX_TIMESTAMP,
    ULID_LENGTH,
    ULID_REGEX,
    _encode_ulid,
    _now_ms,
    _secure_random_80,
    _spin_until_next_ms,
    _ULIDGenerator,
    extract_timestamp_ms,
    generate,
    validate,
)

# ---------------------------------------------------------------------------
# Alphabet & constants
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConstants:
    def test_alphabet_length(self) -> None:
        assert len(_ALPHABET) == 32

    def test_alphabet_no_duplicates(self) -> None:
        assert len(set(_ALPHABET)) == 32

    def test_alphabet_excludes_confusing_chars(self) -> None:
        """Crockford Base32 excludes I, L, O, U."""
        for ch in ("I", "L", "O", "U"):
            assert ch not in _ALPHABET, f"Confusing character {ch!r} found in alphabet"

    def test_ulid_length_constant(self) -> None:
        assert ULID_LENGTH == 26

    def test_max_timestamp(self) -> None:
        # 2^48 - 1
        assert _MAX_TIMESTAMP == (1 << 48) - 1


# ---------------------------------------------------------------------------
# _encode_ulid
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEncodeUlid:
    def test_returns_26_chars(self) -> None:
        result = _encode_ulid(0, 0)
        assert len(result) == ULID_LENGTH

    def test_min_values(self) -> None:
        result = _encode_ulid(0, 0)
        assert result == "0" * ULID_LENGTH

    def test_max_timestamp(self) -> None:
        result = _encode_ulid(_MAX_TIMESTAMP, 0)
        assert len(result) == ULID_LENGTH

    def test_only_alphabet_chars(self) -> None:
        ulid = _encode_ulid(1_700_000_000_000, 12345678901234567890)
        assert all(c in _ALPHABET for c in ulid)

    def test_timestamp_overflow_raises(self) -> None:
        with pytest.raises(ULIDError, match="exceeds ULID maximum"):
            _encode_ulid(_MAX_TIMESTAMP + 1, 0)

    def test_large_random(self) -> None:
        rand_max = (1 << 80) - 1
        result = _encode_ulid(1, rand_max)
        assert len(result) == ULID_LENGTH

    def test_encodes_known_timestamp(self) -> None:
        """Timestamp 0 should produce ten '0' characters at position 0–9."""  # noqa: RUF002
        result = _encode_ulid(0, 1)
        assert result[:10] == "0" * 10


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidate:
    def test_valid_generated_ulid(self) -> None:
        assert validate(generate()) is True

    def test_rejects_empty_string(self) -> None:
        assert validate("") is False

    def test_rejects_too_short(self) -> None:
        assert validate("01ARYZ3NDEKTSV4RRFFQ69G5F") is False  # 25 chars

    def test_rejects_too_long(self) -> None:
        assert validate("01ARYZ3NDEKTSV4RRFFQ69G5FAVX") is False  # 27 chars

    def test_rejects_invalid_chars(self) -> None:
        assert validate("01ARYZ3NDEKTSV4RRFFQ69G5FI") is False  # I not in alphabet

    def test_rejects_non_string(self) -> None:
        assert validate(12345) is False  # type: ignore[arg-type]  # NOSONAR
        assert validate(None) is False  # type: ignore[arg-type]  # NOSONAR

    def test_accepts_lowercase(self) -> None:
        """Case-insensitive validation — lowercase chars are accepted."""
        ulid = generate().lower()
        assert validate(ulid) is True

    def test_regex_pattern_consistent(self) -> None:
        """ULID_REGEX must accept all generated ULIDs."""
        pattern = re.compile(ULID_REGEX)
        for _ in range(50):
            assert pattern.match(generate()), "Generated ULID did not match ULID_REGEX"

    def test_all_zeros_valid(self) -> None:
        assert validate("0" * 26) is True

    def test_timestamp_overflow_invalid(self) -> None:
        """A ULID whose timestamp segment decodes to > MAX_TIMESTAMP is invalid."""
        # Timestamp component 'ZZZZZZZZZZ' decodes to 33_554_431 * 2^45 + …
        # which is >>  _MAX_TIMESTAMP
        assert validate("Z" * 26) is False


# ---------------------------------------------------------------------------
# extract_timestamp_ms
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractTimestampMs:
    def test_roundtrip(self) -> None:
        before = _now_ms()
        ulid = generate()
        after = _now_ms()
        ts = extract_timestamp_ms(ulid)
        assert before <= ts <= after

    def test_raises_on_invalid_ulid(self) -> None:
        with pytest.raises(ULIDError, match="Cannot extract timestamp"):
            extract_timestamp_ms("not-a-ulid")

    def test_known_zero_timestamp(self) -> None:
        ulid = _encode_ulid(0, 42)
        assert extract_timestamp_ms(ulid) == 0

    def test_known_specific_timestamp(self) -> None:
        ms = 1_700_000_000_000  # 2023-11-14
        ulid = _encode_ulid(ms, 42)
        assert extract_timestamp_ms(ulid) == ms


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerate:
    def test_returns_string(self) -> None:
        assert isinstance(generate(), str)

    def test_length(self) -> None:
        assert len(generate()) == ULID_LENGTH

    def test_unique(self) -> None:
        results = {generate() for _ in range(1000)}
        assert len(results) == 1000

    def test_uppercase(self) -> None:
        ulid = generate()
        assert ulid == ulid.upper()

    def test_valid(self) -> None:
        for _ in range(100):
            assert validate(generate())

    def test_lexicographic_order(self) -> None:
        """Later ULIDs must be >= earlier ULIDs (string comparison)."""
        prev = generate()
        for _ in range(200):
            curr = generate()
            assert curr >= prev, f"Ordering violated: {prev!r} > {curr!r}"
            prev = curr


# ---------------------------------------------------------------------------
# Monotonicity — same millisecond
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMonotonicity:
    def test_same_ms_monotonic(self) -> None:
        """When clock is frozen, generator must still produce increasing ULIDs."""
        fixed_ms = _now_ms()
        gen = _ULIDGenerator()

        with patch("agentobs.ulid._now_ms", return_value=fixed_ms):
            ulids = [gen.generate() for _ in range(100)]

        for i in range(len(ulids) - 1):
            assert ulids[i] < ulids[i + 1], (
                f"Monotonicity violated at index {i}: "
                f"{ulids[i]!r} >= {ulids[i + 1]!r}"
            )

    def test_clock_backward_still_monotonic(self) -> None:
        """Backwards clock must not break ordering (uses last_ms + increment)."""
        gen = _ULIDGenerator()
        forward_ms = _now_ms() + 10_000  # 10 seconds in the future

        first: list[str] = []
        with patch("agentobs.ulid._now_ms", return_value=forward_ms):
            for _ in range(5):
                first.append(gen.generate())  # noqa: PERF401

        # Now simulate clock going backwards
        backward_ms = forward_ms - 5_000  # 5 seconds back
        second: list[str] = []
        with patch("agentobs.ulid._now_ms", return_value=backward_ms):
            for _ in range(5):
                second.append(gen.generate())  # noqa: PERF401

        # All second should be >= all first
        assert all(s >= f for s in second for f in first), (
            "Backwards-clock ULIDs are less than forwards-clock ULIDs"
        )

    def test_overflow_raises(self) -> None:
        """Simulates 2^80 random overflow with stalled clock."""
        gen = _ULIDGenerator()
        rand_max = (1 << 80) - 1
        fixed_ms = _now_ms()

        # Pre-seed generator with max random value at fixed_ms
        object.__setattr__(gen, "_last_ms", fixed_ms)
        object.__setattr__(gen, "_last_rand", rand_max - 1)

        # Two calls should exhaust the overflow and raise on the second attempt
        # since the clock won't have advanced (we don't mock spin here —
        # the clock will actually advance on second call, which is fine).
        # Instead, set last_rand to rand_max directly and check:
        object.__setattr__(gen, "_last_rand", rand_max)
        with patch("agentobs.ulid._now_ms", return_value=fixed_ms - 1):  # noqa: SIM117
            with pytest.raises(ULIDError, match="Random segment overflow"):
                gen.generate()

    def test_same_ms_overflow_spins_until_clock_advances(self) -> None:
        """Same-ms overflow takes the spin path (lines 122-124 branch coverage).

        When the random segment overflows within the *same* millisecond (not a
        backwards-clock scenario), the generator spins until the clock advances
        rather than raising.  This covers the spin branch.
        """
        gen = _ULIDGenerator()
        rand_max = (1 << 80) - 1
        fixed_ms = 1_000_000

        # Seed: last call was at fixed_ms with rand_max already used
        object.__setattr__(gen, "_last_ms", fixed_ms)
        object.__setattr__(gen, "_last_rand", rand_max)

        # _now_ms is called by generate() and by _spin_until_next_ms.
        # First call (in generate): returns fixed_ms → same millisecond, overflow.
        # Subsequent calls (in _spin_until_next_ms loop): return fixed_ms+1 → advance.
        call_results = iter([fixed_ms, fixed_ms + 1])

        def advancing_clock() -> int:
            try:
                return next(call_results)
            except StopIteration:
                return fixed_ms + 1

        with patch("agentobs.ulid._now_ms", side_effect=advancing_clock):
            ulid = gen.generate()
        assert validate(ulid), "ULID generated after spin must be valid"


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestThreadSafety:
    def test_concurrent_generation_unique(self) -> None:
        """Concurrent generate() calls across threads must produce unique IDs."""
        results: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            local = [generate() for _ in range(50)]
            with lock:
                results.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == len(set(results)), "Duplicate ULIDs from concurrent threads"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInternalHelpers:
    def test_now_ms_positive(self) -> None:
        assert _now_ms() > 0

    def test_now_ms_reasonable(self) -> None:
        """Timestamp should be after 2020-01-01 (sanity check)."""
        jan_2020_ms = 1_577_836_800_000
        assert _now_ms() > jan_2020_ms

    def test_secure_random_80_range(self) -> None:
        rand = _secure_random_80()
        assert 0 <= rand < (1 << 80)

    def test_secure_random_different(self) -> None:
        values = {_secure_random_80() for _ in range(20)}
        assert len(values) > 1, "CSPRNG produced repeated values"

    def test_spin_until_advances(self) -> None:
        """_spin_until_next_ms must return a value > current_ms."""
        before = _now_ms()
        result = _spin_until_next_ms(before)
        assert result > before


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestULIDPerformance:
    def test_generate_1000_under_50ms(self) -> None:
        """1000 ULIDs should be generated in well under 50ms."""
        start = time.perf_counter()
        for _ in range(1000):
            generate()
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 50, f"ULID generation too slow: {elapsed_ms:.1f}ms for 1000 ULIDs"
