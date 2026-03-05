# tracium.ulid

Zero-dependency ULID (Universally Unique Lexicographically Sortable Identifier)
generation, validation, and timestamp extraction.

ULIDs are 26-character Crockford Base32 strings encoding a 48-bit millisecond
timestamp and an 80-bit random component. They are lexicographically sortable,
URL-safe, and monotonic within the same millisecond.

```
01ARZ3NDEKTSV4RRFFQ69G5FAV
├──────────┤├────────────────┤
 Timestamp (10 chars)   Random (16 chars)
    48 bits, ms             80 bits
```

---

## Module-level functions

### `generate() -> str`

Generate a new ULID string.

Properties of the returned ULID:
- 26 characters long
- Crockford Base32 characters (`[0-9A-HJKMNP-TV-Z]`)
- Lexicographically sortable (earlier ULIDs sort before later ones)
- Monotonic within the same millisecond
- Random component seeded from `os.urandom` (CSPRNG)

**Returns:** `str` — a 26-character uppercase ULID string.

**Raises:** `ULIDError` — on the astronomically unlikely event of internal state overflow or backwards-clock exhaustion.

**Example:**

```python
from tracium.ulid import generate

event_id = generate()  # e.g. "01ARYZ3NDEKTSV4RRFFQ69G5FAV"
```

---

### `validate(value: str) -> bool`

Return `True` if `value` is a syntactically valid ULID string.

Validation checks:
1. Exactly 26 characters long.
2. All characters are in the Crockford Base32 alphabet (case-insensitive; I/L/O treated as 1/1/0).
3. The timestamp component does not overflow the 48-bit range.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `value` | `str` | The string to validate. |

**Returns:** `bool` — `True` if valid, `False` otherwise.

**Example:**

```python
from tracium.ulid import validate

validate("01ARYZ3NDEKTSV4RRFFQ69G5FAV")  # True
validate("not-a-ulid")                     # False
```

---

### `extract_timestamp_ms(ulid: str) -> int`

Extract the embedded millisecond timestamp from a ULID.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `ulid` | `str` | A valid 26-character ULID string. |

**Returns:** `int` — Unix timestamp in milliseconds.

**Raises:** `ULIDError` — if `ulid` is not a valid ULID.

**Example:**

```python
from tracium.ulid import extract_timestamp_ms
from datetime import datetime, timezone

ms = extract_timestamp_ms("01ARYZ3NDEKTSV4RRFFQ69G5FAV")
dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
```

---

## Constants

### `ULID_REGEX: str`

Regex pattern for strict canonical-form ULID validation:

```
^[0-7][0-9A-HJKMNP-TV-Z]{25}$
```

This pattern is stricter than `validate()` — it does not accept lowercase or
alias characters (I/L/O). Use `validate()` for permissive validation.
