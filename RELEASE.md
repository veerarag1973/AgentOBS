# AgentOBS SDK — Release Runbook
# PyPI distribution: agentobs  |  Import: tracium
# Version: 1.0.0 — 2026-03-04
#
# STATUS: READY TO PUBLISH
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT'S IN 1.0.0
# ─────────────────────────────────────────────────────────────────────────────
#
#   Phase 0   Package rename llm_toolkit_schema → tracium
#   Phase 1   Configuration layer (configure(), env vars, singleton)
#   Phase 2   Core tracer + span (tracer.span() context manager)
#   Phase 3   Event emission (SpanPayload → Event → EventStream → Exporter)
#   Phase 4   Agent instrumentation (agent_run(), agent_step())
#   Phase 5   ConsoleExporter (human-readable dev output)
#   Phase 6   OpenAI integration (auto token + cost extraction)
#   Phase 7   Provider integrations (Anthropic, Ollama, Groq, Together AI)
#   Phase 8   Additional exporters (OTLP, Webhook, Datadog, Grafana Loki)
#   Phase 9   Framework integrations (LangChain, LlamaIndex)
#   Phase 10  CLI tooling (tracium validate / audit-chain / inspect / stats)
#   Phase 11  Security + privacy (HMAC signing chain, PII redaction)
#   Phase 12  Hardening + docs + 1.0.0 (this release)
#
#   Tests      1837+ passing, 96.36% coverage
#
# ─────────────────────────────────────────────────────────────────────────────
# PREREQUISITES
# ─────────────────────────────────────────────────────────────────────────────
#
#   pip install build twine
#
# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURE CREDENTIALS (~/.pypirc)
# ─────────────────────────────────────────────────────────────────────────────
#
#   [distutils]
#   index-servers = pypi testpypi
#
#   [pypi]
#   username = __token__
#   password = pypi-XXXXXXXXXX...
#
#   [testpypi]
#   username = __token__
#   password = pypi-XXXXXXXXXX...
#
#   OR export as environment variables (CI/CD preferred):
#     $env:TWINE_USERNAME = "__token__"
#     $env:TWINE_PASSWORD = "pypi-XXXXXXXXXX..."
#
# ─────────────────────────────────────────────────────────────────────────────
# RELEASE STEPS
# ─────────────────────────────────────────────────────────────────────────────
#
#   1. Confirm all tests pass:
#        python -m pytest --tb=short -q
#
#   2. Confirm version:
#        python -c "import tracium; print(tracium.__version__)"
#        # → 1.0.0
#
#   3. Build distribution artefacts:
#        python -m build
#
#   4. Upload to TestPyPI and smoke-test:
#        python -m twine upload --repository testpypi dist/*
#        pip install --index-url https://test.pypi.org/simple/ agentobs==1.0.0
#        python -c "import tracium; print(tracium.__version__)"
#
#   5. Upload to PyPI:
#        python -m twine upload dist/*
#        # Users then: pip install agentobs
#
#   6. Tag the release:
#        git tag v1.0.0
#        git push origin v1.0.0
#
# ─────────────────────────────────────────────────────────────────────────────
# CHANGELOG
# ─────────────────────────────────────────────────────────────────────────────
#
#   1.0.0 (2026-03-04)  — Initial stable release
#     - Full SpanForge Observability Standard v2.0 compliance
#     - HMAC signing chain (opt-in via signing_key=)
#     - PII redaction pipeline (opt-in via redaction_policy=)
#     - CLI: tracium validate / audit-chain / inspect / stats
#     - Exporters: console, jsonl, otlp, webhook, datadog, grafana_loki
#     - Integrations: openai, anthropic, ollama, groq, together, langchain, llamaindex
