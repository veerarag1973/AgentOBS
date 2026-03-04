# Contributing

Thank you for considering a contribution to AgentOBS!
This guide covers everything you need to get a development environment running,
write code that matches the project's standards, and submit a pull request.

## Development setup

```bash
git clone https://github.com/veerarag1973/agentobs.git
cd agentobs
python -m venv .venv

# Windows
.venv\Scripts\activate
pip install -e ".[dev]"

# macOS / Linux
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the test suite:

```bash
pytest                             # all tests
pytest -m perf -v                  # NFR performance benchmarks only
pytest --cov=tracium -q         # with coverage report
```

## Code standards

The project uses **ruff** for linting and formatting, and **mypy** for static
type checking.

```bash
ruff check .       # lint
ruff format .      # format
mypy tracium    # type check
```

All CI checks must pass before a PR is merged. You can run them all at once
with:

```bash
pre-commit run --all-files   # after: pre-commit install
```

## Coverage requirement

**90% branch coverage is required** (minimum) on every commit.
New code must come with tests that cover every branch.

```bash
pytest --cov=tracium --cov-fail-under=100 -q
```

## Project layout

```text
tracium/
├── event.py           # Core Event + Tags dataclass
├── types.py           # EventType enum + helpers
├── ulid.py            # ULID generation and validation
├── signing.py         # HMAC signing, verify_chain, AuditStream
├── redact.py          # PII redaction framework
├── validate.py        # JSON Schema validation
├── migrate.py         # Migration helpers (Phase 9 scaffold)
├── models.py          # Pydantic v2 model layer (optional)
├── exceptions.py      # Domain exceptions
├── _cli.py            # CLI entry-point (coverage-omitted)
├── compliance/        # Compliance test suite
│   ├── _compat.py     # test_compatibility (CHK-1…5)
│   ├── test_chain.py  # verify_chain_integrity
│   └── test_isolation.py  # verify_tenant_isolation
├── export/            # Export backends
│   ├── otlp.py        # OTLP/Protobuf exporter
│   ├── webhook.py     # HTTP webhook exporter
│   └── jsonl.py       # JSONL file exporter
├── namespaces/        # Typed payload dataclasses
│   ├── trace.py       #  — llm.trace.*
│   ├── cost.py        # llm.cost.*
│   └── ...            # cache, diff, eval, fence, guard, prompt, redact, template
└── stream.py          # EventStream routing + filtering
```

## Adding a new namespace payload

1. Create `tracium/namespaces/<name>.py` following the existing pattern
   (frozen dataclass + `validate()` method + `from_event()` constructor).
2. Register the new `EventType` members in `tracium/types.py`.
3. Export the new payload class from `tracium/namespaces/__init__.py`
   and `tracium/__init__.py`.
4. Add tests in `tests/test_namespaces.py` — maintain 100% coverage.
5. Add a `docs/namespaces/<name>.md` page.

## Adding a new export backend

1. Create `tracium/export/<name>.py`. Inherit from
   `Exporter` and implement `export()` and `export_batch()`.
2. Export the class from `tracium/export/__init__.py`.
3. Add tests — `tests/test_export_<name>.py`.
4. Document in `docs/user_guide/export.md`.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat(signing): add key expiry validation
fix(ulid): handle clock regression edge case
docs(quickstart): add Kafka streaming example
test(compliance): cover non-monotonic timestamp branch
```

## Pull request checklist

Before opening a PR, confirm:

- [ ] `pytest --cov=tracium --cov-fail-under=100 -q` passes
- [ ] `ruff check .` reports no errors
- [ ] `mypy tracium` reports no errors
- [ ] New public API has Google-style docstrings
- [ ] `CHANGELOG.md` updated under the *Unreleased* section
- [ ] Documentation updated if new public API was added

## License

tracium is released under the [MIT License](https://github.com/veerarag1973/agentobs/blob/main/LICENSE).
By contributing you agree that your contributions will be licensed under the same terms.
