"""Command-line interface for agentobs utilities.

This module provides the ``agentobs`` entry-point command.  It is excluded
from coverage measurement because it is a thin integration shim over the
public library API — all business logic lives in tested library modules.

Entry-point (configured in pyproject.toml)::

    agentobs = "agentobs._cli:main"

Sub-commands
------------
``agentobs check``
    End-to-end health check: validates configuration, emits a test event,
    and confirms the export pipeline is working.  Exits 0 on success.

``agentobs check-compat <events.json>``
    Load a JSON file containing a list of serialised events and run the
    v1.0 compatibility checklist.  Exits 0 on success, 1 on violations,
    2 on usage/parse errors.

``agentobs list-deprecated``
    Print all event types registered in the global deprecation registry.

``agentobs migration-roadmap [--json]``
    Print the planned v1 → v2 migration roadmap from
    :func:`~agentobs.migrate.v2_migration_roadmap`.  Pass
    ``--json`` to emit JSON for machine consumption.

``agentobs check-consumers``
    Assert that all globally registered consumers are compatible with the
    installed schema version.  Exits 0 on success, 1 on incompatibilities.

``agentobs validate <events.jsonl>``
    Validate every event in a JSONL file against the published schema.
    Exits 0 if all events are valid, 1 if any fail validation.

``agentobs audit-chain <events.jsonl>``
    Verify the HMAC signing chain of events in a JSONL file.  Reads the
    signing key from the ``AGENTOBS_SIGNING_KEY`` environment variable.
    Exits 0 if the chain is intact, 1 if tampering or gaps are found.

``agentobs inspect <event_id> <events.jsonl>``
    Find a single event by ``event_id`` in a JSONL file and pretty-print
    its JSON envelope to stdout.  Exits 0 on success, 1 if not found.

``agentobs stats <events.jsonl>``
    Print a summary table of events in a JSONL file: event counts by type,
    total prompt/completion/total tokens, total cost, and timestamp range.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

_NO_EVENTS_MSG = "No events found in file."

def _cmd_check(_args: argparse.Namespace) -> int:
    """Implement the ``check`` sub-command — end-to-end health check."""
    import traceback  # noqa: PLC0415

    print("agentobs health check")
    print("=" * 40)
    ok = True

    # Step 1: Config
    try:
        from agentobs.config import get_config  # noqa: PLC0415
        cfg = get_config()
        print(f"[✓] Config loaded  exporter={cfg.exporter!r}  env={cfg.env!r}  "
              f"service={cfg.service_name!r}")
    except Exception as exc:
        print(f"[✗] Config failed: {exc}", file=sys.stderr)
        return 1

    # Step 2: Event creation
    try:
        from agentobs.event import Event  # noqa: PLC0415
        from agentobs.ulid import generate as gen_ulid  # noqa: PLC0415
        event = Event(
            event_type="llm.trace.span.completed",
            source=f"{cfg.service_name}@0.0.0",
            payload={
                "span_id": "0" * 16,
                "trace_id": "0" * 32,
                "span_name": "agentobs.health.check",
                "operation": "chat",
                "span_kind": "client",
                "status": "ok",
                "start_time_unix_nano": 0,
                "end_time_unix_nano": 1_000_000,
                "duration_ms": 1.0,
            },
            event_id=gen_ulid(),
        )
        print("[✓] Test event created")
    except Exception as exc:
        print(f"[✗] Event creation failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    # Step 3: Schema validation
    try:
        from agentobs.validate import validate_event  # noqa: PLC0415
        validate_event(event)
        print("[✓] Schema validation passed")
    except Exception as exc:
        print(f"[✗] Schema validation failed: {exc}", file=sys.stderr)
        ok = False

    # Step 4: Export pipeline
    try:
        from agentobs._stream import _dispatch  # noqa: PLC0415
        _dispatch(event)
        print("[✓] Export pipeline: event dispatched successfully")
    except Exception as exc:
        print(f"[✗] Export pipeline failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        ok = False

    # Step 5: TraceStore recording (only if enabled)
    if cfg.enable_trace_store:
        try:
            from agentobs._store import get_store  # noqa: PLC0415
            store = get_store()
            events = store.get_trace("0" * 32)
            if events is not None and len(events) >= 1:
                print(f"[✓] TraceStore recorded {len(events)} event(s)")
            else:
                print("[✗] TraceStore: event not found after dispatch", file=sys.stderr)
                ok = False
        except Exception as exc:
            print(f"[✗] TraceStore check failed: {exc}", file=sys.stderr)
            ok = False
    else:
        print("[–] TraceStore: disabled (set AGENTOBS_ENABLE_TRACE_STORE=1 to enable)")

    print("=" * 40)
    if ok:
        print("PASS — all checks passed.")
        return 0
    print("FAIL — one or more checks failed.", file=sys.stderr)
    return 1


def _cmd_check_compat(args: argparse.Namespace) -> int:
    """Implement the ``check-compat`` sub-command."""
    from agentobs.compliance import test_compatibility  # noqa: PLC0415
    from agentobs.event import Event  # noqa: PLC0415

    path = Path(args.file)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON in {path}: {exc}", file=sys.stderr)
        return 2

    if not isinstance(raw, list):
        print("error: JSON file must contain a top-level array of events", file=sys.stderr)
        return 2

    from agentobs.exceptions import DeserializationError, SchemaValidationError  # noqa: PLC0415
    try:
        events = [Event.from_dict(item) for item in raw]
    except (DeserializationError, SchemaValidationError, KeyError, TypeError) as exc:
        print(f"error: could not deserialise events: {exc}", file=sys.stderr)
        return 2

    result = test_compatibility(events)

    if result.passed:
        print(
            f"OK — {result.events_checked} event(s) passed all compatibility checks."
        )
        return 0

    print(
        f"FAIL — {len(result.violations)} violation(s) found in "
        f"{result.events_checked} event(s):\n"
    )
    for v in result.violations:
        event_ref = f"[{v.event_id}] " if v.event_id else ""
        print(f"  {event_ref}{v.check_id} ({v.rule}): {v.detail}")

    return 1


def _cmd_list_deprecated(_args: argparse.Namespace) -> int:
    """Implement the ``list-deprecated`` sub-command."""
    try:
        from agentobs.deprecations import list_deprecated  # noqa: PLC0415

        notices = list_deprecated()
        if not notices:
            print("No deprecated event types registered.")
            return 0

        print(f"{'Event Type':<50} {'Since':<8} {'Sunset':<8} Replacement")
        print("-" * 90)
        for n in notices:
            repl = n.replacement or "(no replacement)"
            print(f"{n.event_type:<50} {n.since:<8} {n.sunset:<8} {repl}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _cmd_migration_roadmap(args: argparse.Namespace) -> int:
    """Implement the ``migration-roadmap`` sub-command."""
    try:
        from agentobs.migrate import v2_migration_roadmap  # noqa: PLC0415
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    roadmap = v2_migration_roadmap()
    if not roadmap:
        print("No migration records found.")
        return 0

    if getattr(args, "json", False):
        output = [
            {
                "event_type": r.event_type,
                "since": r.since,
                "sunset": r.sunset,
                "sunset_policy": r.sunset_policy.value,
                "replacement": r.replacement,
                "migration_notes": r.migration_notes,
                "field_renames": r.field_renames,
            }
            for r in roadmap
        ]
        print(json.dumps(output, indent=2))
        return 0

    print(f"v1 → v2 Migration Roadmap ({len(roadmap)} changes)\n")
    for r in roadmap:
        arrow = f" → {r.replacement}" if r.replacement else " (removed)"
        print(f"  [{r.since}→{r.sunset}] {r.event_type}{arrow}")
        if r.migration_notes:
            import textwrap  # noqa: PLC0415
            wrapped = textwrap.fill(r.migration_notes, width=72, initial_indent="    ", subsequent_indent="    ")  # noqa: E501
            print(wrapped)
        if r.field_renames:
            for old, new in r.field_renames.items():
                print(f"    field rename: {old!r} → {new!r}")
        print()
    return 0


def _cmd_check_consumers(_args: argparse.Namespace) -> int:
    """Implement the ``check-consumers`` sub-command."""
    from agentobs.consumer import get_registry  # noqa: PLC0415

    registry = get_registry()
    all_records = registry.all()
    if not all_records:
        print("No consumers registered.")
        return 0

    incompatible = registry.check_compatible()
    if not incompatible:
        print(f"OK — all {len(all_records)} consumer(s) are compatible.")
        return 0

    print(f"INCOMPATIBLE — {len(incompatible)} consumer(s) require a newer schema:\n")
    for tool_name, version in incompatible:
        print(f"  {tool_name!r} requires schema v{version}")
    return 1


def _read_jsonl_events(path: Path):  # noqa: ANN202
    """Read a JSONL file and return a list of (lineno, Event | Exception) pairs."""
    from agentobs.event import Event  # noqa: PLC0415
    from agentobs.exceptions import DeserializationError, SchemaValidationError  # noqa: PLC0415

    results = []
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            event = Event.from_dict(obj)
            results.append((lineno, event))
        except (json.JSONDecodeError, DeserializationError, SchemaValidationError, KeyError, TypeError) as exc:  # noqa: E501
            results.append((lineno, exc))
    return results


def _cmd_validate(args: argparse.Namespace) -> int:
    """Implement the ``validate`` sub-command."""
    from agentobs.exceptions import SchemaValidationError  # noqa: PLC0415
    from agentobs.validate import validate_event  # noqa: PLC0415

    path = Path(args.file)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    rows = _read_jsonl_events(path)
    if not rows:
        print(_NO_EVENTS_MSG)
        return 0

    errors: list[tuple[int, str]] = []
    for lineno, item in rows:
        if isinstance(item, Exception):
            errors.append((lineno, f"parse error: {item}"))
            continue
        try:
            validate_event(item)
        except SchemaValidationError as exc:
            errors.append((lineno, str(exc)))

    total = len(rows)
    if not errors:
        print(f"OK — {total} event(s) passed schema validation.")
        return 0

    print(f"FAIL — {len(errors)} of {total} event(s) failed validation:\n")
    for lineno, msg in errors:
        print(f"  line {lineno}: {msg}")
    return 1


def _cmd_audit_chain(args: argparse.Namespace) -> int:  # noqa: PLR0911
    """Implement the ``audit-chain`` sub-command."""
    import os  # noqa: PLC0415

    from agentobs.signing import SigningError, verify_chain  # noqa: PLC0415

    path = Path(args.file)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    org_secret = os.environ.get("AGENTOBS_SIGNING_KEY", "")
    if not org_secret:
        print(
            "error: AGENTOBS_SIGNING_KEY environment variable is not set.",
            file=sys.stderr,
        )
        return 2

    rows = _read_jsonl_events(path)
    if not rows:
        print(_NO_EVENTS_MSG)
        return 0

    bad_lines = [(ln, exc) for ln, exc in rows if isinstance(exc, Exception)]
    if bad_lines:
        print(f"error: {len(bad_lines)} line(s) could not be parsed:", file=sys.stderr)
        for ln, exc in bad_lines[:5]:
            print(f"  line {ln}: {exc}", file=sys.stderr)
        return 2

    events = [ev for _, ev in rows]

    try:
        result = verify_chain(events, org_secret=org_secret)
    except SigningError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if result.valid:
        print(f"OK — chain of {len(events)} event(s) is intact.")
        return 0

    print(f"FAIL — chain verification failed ({result.tampered_count} tampered event(s)):\n")
    if result.first_tampered:
        print(f"  first tampered event_id: {result.first_tampered}")
    if result.gaps:
        print(f"  linkage gaps ({len(result.gaps)}):")
        for gap_id in result.gaps:
            print(f"    {gap_id}")
    return 1


def _cmd_inspect(args: argparse.Namespace) -> int:
    """Implement the ``inspect`` sub-command."""
    path = Path(args.file)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    rows = _read_jsonl_events(path)
    target_id = args.event_id

    for _lineno, item in rows:
        if isinstance(item, Exception):
            continue
        if item.event_id == target_id:
            print(json.dumps(item.to_dict(), indent=2))
            return 0

    print(f"error: event_id {target_id!r} not found in {path}", file=sys.stderr)
    return 1


def _cmd_stats(args: argparse.Namespace) -> int:
    """Implement the ``stats`` sub-command."""
    path = Path(args.file)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    rows = _read_jsonl_events(path)
    if not rows:
        print(_NO_EVENTS_MSG)
        return 0

    type_counts: dict[str, int] = {}
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    cost_usd = 0.0
    timestamps: list[str] = []
    parse_errors = 0

    for _lineno, item in rows:
        if isinstance(item, Exception):
            parse_errors += 1
            continue
        event_type = str(item.event_type) if item.event_type else "(unknown)"
        type_counts[event_type] = type_counts.get(event_type, 0) + 1
        payload = item.payload or {}
        prompt_tokens += int(payload.get("prompt_tokens") or 0)
        completion_tokens += int(payload.get("completion_tokens") or 0)
        total_tokens += int(payload.get("total_tokens") or 0)
        cost_usd += float(payload.get("cost_usd") or 0.0)
        if item.timestamp:
            timestamps.append(item.timestamp)

    total_events = len(rows) - parse_errors
    print(f"Events: {total_events}" + (f" ({parse_errors} parse error(s) skipped)" if parse_errors else ""))  # noqa: E501
    print()

    if type_counts:
        print(f"{'Event Type':<55} {'Count':>7}")
        print("-" * 65)
        for et, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"  {et:<53} {cnt:>7}")
        print()

    print(f"Prompt tokens:     {prompt_tokens:>12,}")
    print(f"Completion tokens: {completion_tokens:>12,}")
    print(f"Total tokens:      {total_tokens:>12,}")
    print(f"Cost (USD):        {cost_usd:>12.6f}")
    print()

    if timestamps:
        ts_sorted = sorted(timestamps)
        print(f"Earliest: {ts_sorted[0]}")
        print(f"Latest:   {ts_sorted[-1]}")

    return 0


def main(argv: list[str] | None = None) -> NoReturn:
    """Entry point for the ``agentobs`` CLI tool."""
    parser = argparse.ArgumentParser(
        prog="agentobs",
        description="agentobs command-line utilities",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # check sub-command (health check)
    sub.add_parser(
        "check",
        help="End-to-end health check: validates config, emits a test event, confirms export pipeline",
    )

    # check-compat sub-command
    compat_parser = sub.add_parser(
        "check-compat",
        help="Check a JSON file of events against the v1.0 compatibility checklist",
    )
    compat_parser.add_argument(
        "file",
        metavar="EVENTS_JSON",
        help="Path to a JSON file containing a list of serialised events",
    )

    # list-deprecated sub-command
    sub.add_parser(
        "list-deprecated",
        help="Print all deprecated event types from the global deprecation registry",
    )

    # migration-roadmap sub-command
    roadmap_parser = sub.add_parser(
        "migration-roadmap",
        help="Print the planned v1 → v2 migration roadmap",
    )
    roadmap_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output for machine consumption",
    )

    # check-consumers sub-command
    sub.add_parser(
        "check-consumers",
        help="Assert all registered consumers are compatible with the installed schema",
    )

    # validate sub-command
    validate_parser = sub.add_parser(
        "validate",
        help="Validate every event in a JSONL file against the published schema",
    )
    validate_parser.add_argument(
        "file",
        metavar="EVENTS_JSONL",
        help="Path to a JSONL file (one event JSON per line)",
    )

    # audit-chain sub-command
    audit_parser = sub.add_parser(
        "audit-chain",
        help="Verify HMAC signing chain integrity of events in a JSONL file",
    )
    audit_parser.add_argument(
        "file",
        metavar="EVENTS_JSONL",
        help="Path to a JSONL file of signed events (reads AGENTOBS_SIGNING_KEY env var)",
    )

    # inspect sub-command
    inspect_parser = sub.add_parser(
        "inspect",
        help="Pretty-print a single event by event_id from a JSONL file",
    )
    inspect_parser.add_argument(
        "event_id",
        metavar="EVENT_ID",
        help="The event_id to look up",
    )
    inspect_parser.add_argument(
        "file",
        metavar="EVENTS_JSONL",
        help="Path to a JSONL file to search",
    )

    # stats sub-command
    stats_parser = sub.add_parser(
        "stats",
        help="Print a summary of events in a JSONL file (counts, tokens, cost, timestamps)",
    )
    stats_parser.add_argument(
        "file",
        metavar="EVENTS_JSONL",
        help="Path to a JSONL file",
    )

    args = parser.parse_args(argv)

    if args.command == "check":
        sys.exit(_cmd_check(args))
    elif args.command == "check-compat":
        sys.exit(_cmd_check_compat(args))
    elif args.command == "list-deprecated":
        sys.exit(_cmd_list_deprecated(args))
    elif args.command == "migration-roadmap":
        sys.exit(_cmd_migration_roadmap(args))
    elif args.command == "check-consumers":
        sys.exit(_cmd_check_consumers(args))
    elif args.command == "validate":
        sys.exit(_cmd_validate(args))
    elif args.command == "audit-chain":
        sys.exit(_cmd_audit_chain(args))
    elif args.command == "inspect":
        sys.exit(_cmd_inspect(args))
    elif args.command == "stats":
        sys.exit(_cmd_stats(args))
    else:
        parser.print_help()
        sys.exit(2)
