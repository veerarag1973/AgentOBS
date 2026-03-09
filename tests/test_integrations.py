"""Tests for agentobs.integrations (LangChain + LlamaIndex handlers)."""

from __future__ import annotations

import sys
import types
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from agentobs import Event

# ---------------------------------------------------------------------------
# Helpers: inject fake langchain / llamaindex modules
# ---------------------------------------------------------------------------


def _inject_fake_langchain() -> None:
    """Inject minimal stub modules so LangChain imports succeed without the real package."""
    if "langchain_core" not in sys.modules:
        langchain_core = types.ModuleType("langchain_core")
        langchain_core.callbacks = types.ModuleType("langchain_core.callbacks")
        sys.modules["langchain_core"] = langchain_core
        sys.modules["langchain_core.callbacks"] = langchain_core.callbacks


def _inject_fake_llamaindex() -> None:
    """Inject minimal stub modules so LlamaIndex imports succeed without the real package."""
    if "llama_index.core.callbacks" not in sys.modules:
        llama_index_mod = types.ModuleType("llama_index")
        core_mod = types.ModuleType("llama_index.core")
        callbacks_mod = types.ModuleType("llama_index.core.callbacks")
        # Wire attribute relationships as Python's import system would.
        llama_index_mod.core = core_mod  # type: ignore[attr-defined]
        core_mod.callbacks = callbacks_mod  # type: ignore[attr-defined]
        sys.modules["llama_index"] = llama_index_mod
        sys.modules["llama_index.core"] = core_mod
        sys.modules["llama_index.core.callbacks"] = callbacks_mod


# ---------------------------------------------------------------------------
# LangChain handler
# ---------------------------------------------------------------------------


class TestLLMSchemaCallbackHandler:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        _inject_fake_langchain()

    def _make_handler(self) -> Any:
        from agentobs.integrations.langchain import LLMSchemaCallbackHandler  # noqa: PLC0415

        return LLMSchemaCallbackHandler(source="test-app", org_id="org-1")

    def test_import_error_without_langchain(self) -> None:
        """Verify ImportError is raised when langchain is not installed."""
        with patch.dict(sys.modules, {"langchain_core": None, "langchain": None}):
            import agentobs.integrations.langchain as lc_mod  # noqa: PLC0415

            with pytest.raises(ImportError, match="LangChain"):
                lc_mod._require_langchain()

    def test_construction(self) -> None:
        handler = self._make_handler()
        assert handler._source == "test-app"
        assert handler._org_id == "org-1"
        assert handler.events == []

    def test_on_llm_start_emits_event(self) -> None:
        handler = self._make_handler()
        run_id = uuid.uuid4()
        handler.on_llm_start(
            serialized={"id": ["openai", "ChatOpenAI"]},
            prompts=["Hello"],
            run_id=run_id,
        )
        assert len(handler.events) == 1
        ev = handler.events[0]
        assert ev.event_type == "llm.trace.span.started"
        assert ev.source == "test-app"

    def test_on_llm_end_emits_event(self) -> None:
        handler = self._make_handler()
        run_id = uuid.uuid4()

        response = MagicMock()
        response.llm_output = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}  # noqa: E501

        handler.on_llm_end(response=response, run_id=run_id)
        assert len(handler.events) == 1
        ev = handler.events[0]
        assert ev.event_type == "llm.trace.span.completed"
        assert ev.payload["prompt_tokens"] == 10

    def test_on_llm_error_emits_event(self) -> None:
        handler = self._make_handler()
        run_id = uuid.uuid4()
        handler.on_llm_error(error=ValueError("bad"), run_id=run_id)
        assert len(handler.events) == 1
        assert handler.events[0].event_type == "llm.trace.span.error"

    def test_on_tool_start_emits_event(self) -> None:
        handler = self._make_handler()
        run_id = uuid.uuid4()
        handler.on_tool_start(
            serialized={"name": "search"},
            input_str="query text",
            run_id=run_id,
        )
        assert handler.events[0].event_type == "llm.trace.tool_call.started"
        assert handler.events[0].payload["tool_name"] == "search"

    def test_on_tool_end_emits_event(self) -> None:
        handler = self._make_handler()
        run_id = uuid.uuid4()
        handler.on_tool_end(output="result text", run_id=run_id)
        assert handler.events[0].event_type == "llm.trace.tool_call.completed"

    def test_on_tool_error_emits_event(self) -> None:
        handler = self._make_handler()
        run_id = uuid.uuid4()
        handler.on_tool_error(error=RuntimeError("fail"), run_id=run_id)
        assert handler.events[0].event_type == "llm.trace.tool_call.error"

    def test_clear_events(self) -> None:
        handler = self._make_handler()
        run_id = uuid.uuid4()
        handler.on_llm_start(serialized={"id": ["x"]}, prompts=["p"], run_id=run_id)
        assert len(handler.events) == 1
        handler.clear_events()
        assert len(handler.events) == 0

    def test_on_llm_end_no_token_usage(self) -> None:
        handler = self._make_handler()
        run_id = uuid.uuid4()
        response = MagicMock()
        response.llm_output = {}
        handler.on_llm_end(response=response, run_id=run_id)
        ev = handler.events[0]
        assert ev.event_type == "llm.trace.span.completed"

    def test_org_id_in_event(self) -> None:
        handler = self._make_handler()
        run_id = uuid.uuid4()
        handler.on_llm_start(serialized={"id": ["x"]}, prompts=["p"], run_id=run_id)
        assert handler.events[0].org_id == "org-1"

    def test_exporter_called(self) -> None:
        """When an exporter is provided, it should receive the event."""
        import asyncio  # noqa: PLC0415

        _inject_fake_langchain()
        from agentobs.integrations.langchain import LLMSchemaCallbackHandler  # noqa: PLC0415

        exported: list[Any] = []

        class FakeExporter:
            async def export(self, event: Event) -> None:
                await asyncio.sleep(0)
                exported.append(event)

        handler = LLMSchemaCallbackHandler(source="app", exporter=FakeExporter())
        run_id = uuid.uuid4()

        loop = asyncio.new_event_loop()
        try:
            # on_llm_start emits an event — exporter.export is scheduled as task
            # but since the loop isn't running when called synchronously, the
            # task is never executed.  Just verify the handler didn't crash.
            handler.on_llm_start(serialized={"id": ["x"]}, prompts=["p"], run_id=run_id)
        finally:
            loop.close()

        assert len(handler.events) == 1


# ---------------------------------------------------------------------------
# LlamaIndex handler
# ---------------------------------------------------------------------------


class TestLLMSchemaEventHandler:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        _inject_fake_llamaindex()

    def _make_handler(self) -> Any:
        from agentobs.integrations.llamaindex import LLMSchemaEventHandler  # noqa: PLC0415

        return LLMSchemaEventHandler(source="rag-app", org_id="org-2")

    def test_import_error_without_llamaindex(self) -> None:
        with patch.dict(sys.modules, {"llama_index": None, "llama_index.core": None}):
            import agentobs.integrations.llamaindex as li_mod  # noqa: PLC0415

            with pytest.raises(ImportError, match="LlamaIndex"):
                li_mod._require_llamaindex()

    def test_construction(self) -> None:
        handler = self._make_handler()
        assert handler._source == "rag-app"
        assert handler._org_id == "org-2"
        assert handler.events == []

    def test_on_event_start_llm(self) -> None:
        handler = self._make_handler()
        event_id = handler.on_event_start("LLM", payload={"model_dict": {"model": "gpt-4o"}}, event_id="ev-1")  # noqa: E501
        assert event_id == "ev-1"
        assert len(handler.events) == 1
        assert handler.events[0].event_type == "llm.trace.span.started"

    def test_on_event_end_llm(self) -> None:
        handler = self._make_handler()
        handler.on_event_start("LLM", event_id="ev-1")
        handler.on_event_end("LLM", event_id="ev-1")
        # One start + one end event.
        assert len(handler.events) == 2
        assert handler.events[1].event_type == "llm.trace.span.completed"

    def test_on_event_start_tool(self) -> None:
        handler = self._make_handler()
        handler.on_event_start("FUNCTION_CALL", payload={"tool": {"name": "search"}}, event_id="ev-2")  # noqa: E501
        assert handler.events[0].event_type == "llm.trace.tool_call.started"

    def test_on_event_end_tool(self) -> None:
        handler = self._make_handler()
        handler.on_event_start("FUNCTION_CALL", event_id="ev-2")
        handler.on_event_end("FUNCTION_CALL", payload={"output": "result"}, event_id="ev-2")
        assert handler.events[1].event_type == "llm.trace.tool_call.completed"

    def test_on_event_start_query(self) -> None:
        handler = self._make_handler()
        handler.on_event_start("QUERY", payload={"query_str": "What is AI?"}, event_id="ev-3")
        assert handler.events[0].event_type == "llm.trace.query.started"

    def test_on_event_end_query(self) -> None:
        handler = self._make_handler()
        handler.on_event_start("QUERY", event_id="ev-3")
        handler.on_event_end("QUERY", event_id="ev-3")
        assert handler.events[1].event_type == "llm.trace.query.completed"

    def test_unknown_event_type_does_not_emit(self) -> None:
        handler = self._make_handler()
        handler.on_event_start("SOME_UNKNOWN_TYPE", event_id="ev-x")
        assert len(handler.events) == 0

    def test_duration_ms_calculated(self) -> None:
        handler = self._make_handler()
        handler.on_event_start("LLM", event_id="ev-1")
        handler.on_event_end("LLM", event_id="ev-1")
        completed = handler.events[1]
        # duration_ms may be 0.0 or positive — both are valid.
        assert completed.payload["duration_ms"] is not None

    def test_start_trace_end_trace_noop(self) -> None:
        handler = self._make_handler()
        # Should not raise.
        handler.start_trace("trace-id")
        handler.end_trace("trace-id", {})

    def test_clear_events(self) -> None:
        handler = self._make_handler()
        handler.on_event_start("LLM", event_id="ev-1")
        handler.clear_events()
        assert len(handler.events) == 0

    def test_on_event_end_unknown_event_id(self) -> None:
        handler = self._make_handler()
        # Ending an event that was never started should not raise.
        handler.on_event_end("LLM", event_id="unknown-id")
        # A completed event with None duration.
        ev = handler.events[0]
        assert ev.payload["duration_ms"] is None


# ---------------------------------------------------------------------------
# LangChain: additional coverage gaps
# ---------------------------------------------------------------------------


class TestLangChainAdditionalCoverage:
    """Covers lines 45, 128, and 181->184 in langchain.py."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        _inject_fake_langchain()

    def _make_handler(self, exporter: Any = None) -> Any:
        from agentobs.integrations.langchain import LLMSchemaCallbackHandler  # noqa: PLC0415

        return LLMSchemaCallbackHandler(source="test-app", org_id="org-1", exporter=exporter)

    def test_require_langchain_fallback_to_langchain_callbacks(self) -> None:
        """Line 45: fallback when langchain_core unavailable, langchain.callbacks present."""
        import types  # noqa: PLC0415

        # Stub a minimal langchain.callbacks module (not langchain_core).
        fake_langchain = types.ModuleType("langchain")
        fake_lc_callbacks = types.ModuleType("langchain.callbacks")
        fake_langchain.callbacks = fake_lc_callbacks  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "langchain_core": None,
                "langchain_core.callbacks": None,
                "langchain": fake_langchain,
                "langchain.callbacks": fake_lc_callbacks,
            },
        ):
            import agentobs.integrations.langchain as lc_mod  # noqa: PLC0415

            result = lc_mod._require_langchain()
            assert result is fake_lc_callbacks

    def test_on_llm_start_with_running_event_loop(self) -> None:
        """Line 128: loop.create_task called when invoked inside a running event loop."""
        import asyncio  # noqa: PLC0415

        exported: list[Any] = []

        class FakeExporter:
            async def export(self, event: Any) -> None:
                await asyncio.sleep(0)
                exported.append(event)

        handler = self._make_handler(exporter=FakeExporter())
        run_id = uuid.uuid4()

        async def _run() -> None:
            # Called inside asyncio.run → loop IS running → create_task fires.
            handler.on_llm_start(
                serialized={"id": ["x"]},
                prompts=["hello"],
                run_id=run_id,
            )
            # Allow the task to execute.
            await asyncio.sleep(0)

        asyncio.run(_run())
        # The event was emitted and the export task was scheduled + ran.
        assert len(handler.events) == 1
        assert len(exported) == 1

    def test_on_llm_end_llm_output_not_dict_takes_false_branch(self) -> None:
        """Lines 181->184: False branch when llm_output is not a dict."""
        handler = self._make_handler()
        run_id = uuid.uuid4()

        response = MagicMock()
        response.llm_output = None  # has attribute but isinstance(..., dict) is False

        handler.on_llm_end(response=response, run_id=run_id)
        ev = handler.events[0]
        assert ev.event_type == "llm.trace.span.completed"
        # token fields default to None when llm_output is not a valid dict
        assert ev.payload["prompt_tokens"] is None
        assert ev.payload["completion_tokens"] is None

    def test_on_llm_end_no_llm_output_attr(self) -> None:
        """Lines 181->184: False branch when llm_output attribute absent."""
        handler = self._make_handler()
        run_id = uuid.uuid4()

        # Plain object without llm_output attribute.
        class _FakeResponse:
            ...
        handler.on_llm_end(response=_FakeResponse(), run_id=run_id)
        ev = handler.events[0]
        assert ev.event_type == "llm.trace.span.completed"
        assert ev.payload["prompt_tokens"] is None


# ---------------------------------------------------------------------------
# LlamaIndex: additional coverage gaps
# ---------------------------------------------------------------------------


class TestLlamaIndexAdditionalCoverage:
    """Covers lines 43, 116-119, 128, 212-213, 235->exit in llamaindex.py."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        _inject_fake_llamaindex()

    def _make_handler(self, exporter: Any = None) -> Any:
        from agentobs.integrations.llamaindex import LLMSchemaEventHandler  # noqa: PLC0415

        return LLMSchemaEventHandler(source="rag-app", org_id="org-2", exporter=exporter)

    def test_require_llamaindex_fallback_to_legacy_callbacks(self) -> None:
        """Line 43: fallback when llama_index.core.callbacks unavailable."""
        import types  # noqa: PLC0415

        fake_llama = types.ModuleType("llama_index")
        fake_callbacks = types.ModuleType("llama_index.callbacks")
        fake_llama.callbacks = fake_callbacks  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "llama_index.core": None,
                "llama_index.core.callbacks": None,
                "llama_index": fake_llama,
                "llama_index.callbacks": fake_callbacks,
            },
        ):
            import agentobs.integrations.llamaindex as li_mod  # noqa: PLC0415

            result = li_mod._require_llamaindex()
            assert result is fake_callbacks

    def test_make_event_with_running_event_loop(self) -> None:
        """Lines 116-119: loop.create_task called when loop is running."""
        import asyncio  # noqa: PLC0415

        exported: list[Any] = []

        class FakeExporter:
            async def export(self, event: Any) -> None:
                await asyncio.sleep(0)
                exported.append(event)

        handler = self._make_handler(exporter=FakeExporter())

        async def _run() -> None:
            handler.on_event_start("LLM", payload={}, event_id="ev-loop")
            await asyncio.sleep(0)

        asyncio.run(_run())
        assert len(handler.events) == 1
        assert len(exported) == 1

    def test_cb_event_type_str_enum_value(self) -> None:
        """Line 128: _cb_event_type_str returns str(event_type.value) for enum-like objects."""
        from agentobs.integrations.llamaindex import LLMSchemaEventHandler  # noqa: PLC0415

        class FakeEnum:
            value = "LLM"

        result = LLMSchemaEventHandler._cb_event_type_str(FakeEnum())
        assert result == "LLM"

    def test_on_event_end_llm_with_raw_token_usage(self) -> None:
        """Lines 212-213: token_info populated when response.raw is a dict with usage."""
        handler = self._make_handler()
        handler.on_event_start("LLM", event_id="ev-tok")

        response_mock = MagicMock()
        response_mock.raw = {
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
            }
        }
        handler.on_event_end("LLM", payload={"response": response_mock}, event_id="ev-tok")

        completed = handler.events[1]
        assert completed.event_type == "llm.trace.span.completed"
        assert completed.payload["prompt_tokens"] == 7
        assert completed.payload["completion_tokens"] == 3
        assert completed.payload["total_tokens"] == 10

    def test_on_event_end_unknown_type_no_event_emitted(self) -> None:
        """Line 235->exit: on_event_end with none of the known types → no event."""
        handler = self._make_handler()
        handler.on_event_start("SOME_UNKNOWN", event_id="ev-unk")
        handler.on_event_end("SOME_UNKNOWN", event_id="ev-unk")
        # on_event_start for unknown type emits nothing; on_event_end also emits nothing.
        assert len(handler.events) == 0

    def test_make_event_with_exporter_loop_not_running(self) -> None:
        """Lines 118->122: exporter present but loop.is_running() is False (sync call)."""
        import asyncio  # noqa: PLC0415

        exported: list[Any] = []

        class FakeExporter:
            async def export(self, event: Any) -> None:  # pragma: no cover  # NOSONAR
                exported.append(event)

        handler = self._make_handler(exporter=FakeExporter())

        # Create an explicit non-running event loop so get_event_loop() succeeds
        # but loop.is_running() returns False → covers the False branch of line 118.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            handler.on_event_start("LLM", payload={}, event_id="ev-sync")
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        # Event is emitted; export task was NOT scheduled (loop not running).
        assert len(handler.events) == 1
        assert len(exported) == 0

