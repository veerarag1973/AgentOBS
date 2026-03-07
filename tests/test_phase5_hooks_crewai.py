"""tests/test_phase5_hooks_crewai.py — Exhaustive tests for Phase 5 changes.

Phase 5 covers:
- 5.1  agentobs/_hooks.py — HookRegistry, hooks singleton, decorator usage,
                            fire_start / fire_end in SpanContextManager
- 5.2  agentobs/integrations/crewai.py — AgentOBSCrewAIHandler, patch()

Coverage target: ≥ 95 % of new Phase-5 code.
"""

from __future__ import annotations

import importlib
import importlib.util
import warnings
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import agentobs
from agentobs import hooks, tracer
from agentobs._hooks import (
    HookRegistry,
    HookFn,
    _HOOK_AGENT_END,
    _HOOK_AGENT_START,
    _HOOK_LLM_CALL,
    _HOOK_TOOL_CALL,
    _classify_span,
    hooks as hooks_singleton,
)
from agentobs._span import Span


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_span(operation: str = "chat", name: str = "test") -> Span:
    """Build a minimal Span-like mock for hook testing."""
    sp = MagicMock(spec=Span)
    sp.operation = operation
    sp.name = name
    sp.status = "ok"
    return sp


# ===========================================================================
# 5.1  HookRegistry — unit tests
# ===========================================================================


class TestHookRegistryCreation:
    def test_new_registry_has_no_hooks(self):
        reg = HookRegistry()
        assert repr(reg).startswith("HookRegistry(")

    def test_module_singleton_is_hookregistry(self):
        assert isinstance(hooks_singleton, HookRegistry)

    def test_agentobs_hooks_is_singleton(self):
        assert agentobs.hooks is hooks_singleton


class TestHookRegistryRegistration:
    def setup_method(self):
        self.reg = HookRegistry()

    def test_on_llm_call_decorator_returns_fn(self):
        def cb(span): pass
        result = self.reg.on_llm_call(cb)
        assert result is cb

    def test_on_tool_call_decorator(self):
        calls = []
        @self.reg.on_tool_call
        def cb(span):
            calls.append(span)

        sp = _make_span(operation="tool_call")
        self.reg._fire_start(sp)
        assert len(calls) == 1
        assert calls[0] is sp

    def test_on_agent_start_decorator(self):
        calls = []
        @self.reg.on_agent_start
        def cb(span):
            calls.append(span)

        sp = _make_span(operation="invoke_agent")
        self.reg._fire_start(sp)
        assert len(calls) == 1

    def test_on_agent_end_fires_on_exit(self):
        calls = []
        @self.reg.on_agent_end
        def cb(span):
            calls.append(span)

        sp = _make_span(operation="invoke_agent")
        self.reg._fire_end(sp)
        assert len(calls) == 1

    def test_multiple_hooks_all_fire(self):
        calls = []

        def cb1(span): calls.append("cb1")
        def cb2(span): calls.append("cb2")

        self.reg.on_llm_call(cb1)
        self.reg.on_llm_call(cb2)

        sp = _make_span(operation="chat")
        self.reg._fire_start(sp)
        assert set(calls) == {"cb1", "cb2"}

    def test_clear_removes_all_hooks(self):
        calls = []
        self.reg.on_llm_call(lambda s: calls.append(s))
        self.reg.clear()

        sp = _make_span(operation="chat")
        self.reg._fire_start(sp)
        assert calls == []

    def test_on_llm_call_fires_for_start_and_end(self):
        calls = []
        self.reg.on_llm_call(lambda s: calls.append("fired"))

        sp = _make_span(operation="chat")
        self.reg._fire_start(sp)
        self.reg._fire_end(sp)
        assert len(calls) == 2  # fires on both start and end

    def test_unknown_operation_no_hook(self):
        calls = []
        self.reg.on_llm_call(lambda s: calls.append(s))
        sp = _make_span(operation="some_other_op")
        self.reg._fire_start(sp)
        assert calls == []


class TestHookRegistryErrorIsolation:
    def test_failing_hook_does_not_raise(self):
        reg = HookRegistry()

        def bad_hook(span):
            raise RuntimeError("hook failure")

        reg.on_llm_call(bad_hook)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sp = _make_span(operation="chat")
            reg._fire_start(sp)

        # Check a warning was emitted for the failing hook.
        assert any("hook error" in str(warning.message).lower() for warning in w)

    def test_one_failing_hook_does_not_skip_next(self):
        reg = HookRegistry()
        calls = []

        def bad(span): raise ValueError("bad")
        def good(span): calls.append("ok")

        reg.on_llm_call(bad)
        reg.on_llm_call(good)

        with warnings.catch_warnings(record=True):
            sp = _make_span(operation="chat")
            reg._fire_start(sp)

        assert calls == ["ok"]


class TestClassifySpan:
    def test_chat_is_llm(self):
        sp = _make_span(operation="chat")
        assert _classify_span(sp) == _HOOK_LLM_CALL

    def test_completion_is_llm(self):
        sp = _make_span(operation="completion")
        assert _classify_span(sp) == _HOOK_LLM_CALL

    def test_embedding_is_llm(self):
        sp = _make_span(operation="embedding")
        assert _classify_span(sp) == _HOOK_LLM_CALL

    def test_tool_call_is_tool(self):
        sp = _make_span(operation="tool_call")
        assert _classify_span(sp) == _HOOK_TOOL_CALL

    def test_invoke_agent_is_agent_start(self):
        sp = _make_span(operation="invoke_agent")
        assert _classify_span(sp) == _HOOK_AGENT_START

    def test_unknown_op_returns_none(self):
        sp = _make_span(operation="gibberish")
        assert _classify_span(sp) is None

    def test_llm_in_span_name_fallback(self):
        sp = _make_span(operation="", name="call_llm_service")
        assert _classify_span(sp) == _HOOK_LLM_CALL

    def test_tool_in_span_name_fallback(self):
        sp = _make_span(operation="", name="run_my_tool")
        assert _classify_span(sp) == _HOOK_TOOL_CALL


# ===========================================================================
# 5.1  Hooks wired into SpanContextManager
# ===========================================================================


class TestHooksWiredIntoSpanContextManager:
    def setup_method(self):
        hooks_singleton.clear()

    def teardown_method(self):
        hooks_singleton.clear()

    def test_llm_start_hook_fires_on_enter(self):
        start_calls = []

        @hooks_singleton.on_llm_call
        def hook(span):
            start_calls.append(("fired", span.status))

        with tracer.span("my-llm", operation="chat") as span:
            in_block = list(start_calls)

        # Hook must have fired before or at the start of the block
        assert len(in_block) >= 1

    def test_llm_end_hook_fires_on_exit(self):
        end_calls = []

        @hooks_singleton.on_llm_call
        def hook(span):
            end_calls.append("called")

        with tracer.span("my-llm", operation="chat"):
            pass

        # Hook fires once on start + once on end = 2 total
        assert len(end_calls) == 2

    def test_tool_hook_fires(self):
        calls = []

        @hooks_singleton.on_tool_call
        def hook(span):
            calls.append(span.name)

        with tracer.span("my-tool", operation="tool_call"):
            pass

        assert "my-tool" in calls

    def test_hook_exception_does_not_abort_span(self):
        """A hook that raises must never prevent the span from emitting."""

        @hooks_singleton.on_llm_call
        def bad_hook(span):
            raise RuntimeError("intentional hook failure")

        # This must not raise.
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            with tracer.span("ok-span", operation="chat") as span:
                pass
        assert span.status == "ok"

    def test_no_hook_fires_for_unclassified_span(self):
        calls = []

        @hooks_singleton.on_llm_call
        def hook(span):
            calls.append("llm")

        @hooks_singleton.on_tool_call
        def hook2(span):
            calls.append("tool")

        with tracer.span("generic-span", operation="some_custom_op"):
            pass

        assert calls == []


# ===========================================================================
# 5.2  AgentOBSCrewAIHandler
# ===========================================================================


class TestCrewAIHandlerImport:
    def test_module_imports_cleanly_without_crewai(self):
        # The module should import fine even if crewai is not installed.
        from agentobs.integrations.crewai import AgentOBSCrewAIHandler, patch
        assert callable(patch)
        assert AgentOBSCrewAIHandler is not None

    def test_handler_instantiation(self):
        from agentobs.integrations.crewai import AgentOBSCrewAIHandler
        handler = AgentOBSCrewAIHandler()
        assert handler is not None


class TestCrewAIHandlerToolLifecycle:
    def setup_method(self):
        from agentobs.integrations.crewai import AgentOBSCrewAIHandler
        self.handler = AgentOBSCrewAIHandler()

    def _make_tool(self, name: str = "search"):
        tool = MagicMock()
        tool.name = name
        return tool

    def test_on_tool_start_opens_span(self):
        tool = self._make_tool("search")
        self.handler.on_tool_start(tool, {"query": "test"})
        # A span should be tracked in _tool_spans
        assert len(self.handler._tool_spans) == 1

    def test_on_tool_end_closes_span(self):
        tool = self._make_tool("search")
        self.handler.on_tool_start(tool, {"query": "test"})
        self.handler.on_tool_end(tool, "result text")
        assert len(self.handler._tool_spans) == 0

    def test_on_tool_start_does_not_raise_on_error(self):
        """Even if tracer raises, the handler must swallow the error."""
        with patch("agentobs.tracer.span", side_effect=RuntimeError("boom")):
            tool = self._make_tool()
            self.handler.on_tool_start(tool, "input")  # must not raise

    def test_on_tool_end_without_matching_start_is_no_op(self):
        tool = self._make_tool("nonexistent")
        self.handler.on_tool_end(tool, "result")  # should not raise

    def test_on_tool_end_exception_is_swallowed(self):
        """Lines 126-127: except Exception: pass in on_tool_end."""
        tool = self._make_tool("search")
        fake_cm = MagicMock()
        fake_span = MagicMock()
        fake_span.set_attribute.side_effect = RuntimeError("attr crash")
        tool_name = "search"
        key = f"{id(tool)}/{tool_name}/12345"
        self.handler._tool_spans[key] = (fake_cm, fake_span, key)
        self.handler.on_tool_end(tool, "output")  # must not raise


class TestCrewAIHandlerTaskLifecycle:
    def setup_method(self):
        from agentobs.integrations.crewai import AgentOBSCrewAIHandler
        self.handler = AgentOBSCrewAIHandler()

    def _make_task(self, description: str = "research"):
        task = MagicMock()
        task.description = description
        return task

    def test_on_task_start_opens_span(self):
        task = self._make_task()
        self.handler.on_task_start(task)
        assert len(self.handler._task_spans) == 1

    def test_on_task_end_closes_span(self):
        task = self._make_task()
        self.handler.on_task_start(task)
        self.handler.on_task_end(task, "output")
        assert len(self.handler._task_spans) == 0

    def test_on_task_end_without_start_is_no_op(self):
        task = self._make_task()
        self.handler.on_task_end(task, "output")  # should not raise

    def test_on_task_start_exception_is_swallowed(self):
        """Lines 147-148: except Exception: pass in on_task_start."""
        with patch("agentobs.tracer.span", side_effect=RuntimeError("boom")):
            task = self._make_task()
            self.handler.on_task_start(task)  # must not raise

    def test_on_task_end_with_none_output(self):
        """Branch 157->159: output is None path in on_task_end."""
        task = self._make_task()
        self.handler.on_task_start(task)
        self.handler.on_task_end(task, None)  # output is None — skips set_attribute
        assert len(self.handler._task_spans) == 0

    def test_on_task_end_exception_is_swallowed(self):
        """Lines 160-161: except Exception: pass in on_task_end."""
        task = self._make_task()
        fake_cm = MagicMock()
        fake_span = MagicMock()
        fake_span.set_attribute.side_effect = RuntimeError("crash")
        self.handler._task_spans[str(id(task))] = (fake_cm, fake_span)
        self.handler.on_task_end(task, "some output")  # must not raise


class TestCrewAIHandlerAgentLifecycle:
    def setup_method(self):
        from agentobs.integrations.crewai import AgentOBSCrewAIHandler
        self.handler = AgentOBSCrewAIHandler()

    def test_on_agent_finish_without_active_run_is_no_op(self):
        agent = MagicMock()
        agent.role = "researcher"
        self.handler.on_agent_finish(agent, "output")  # must not raise

    def test_on_agent_action_does_not_raise(self):
        agent = MagicMock()
        agent.role = "researcher"
        task = MagicMock()
        tool = MagicMock()
        tool.name = "search"
        self.handler.on_agent_action(agent, task, tool, "query")

    def test_on_agent_action_exception_is_swallowed(self):
        """Lines 76-77: except Exception: pass in on_agent_action."""
        with patch("agentobs.tracer.span", side_effect=RuntimeError("boom")):
            agent = MagicMock()
            agent.role = "test"
            task = MagicMock()
            tool = MagicMock()
            tool.name = "mytool"
            self.handler.on_agent_action(agent, task, tool, "input")  # must not raise

    def test_on_agent_finish_with_active_span_and_return_values(self):
        """Lines 85-88: on_agent_finish closes span when _agent_spans is populated."""
        agent = MagicMock()
        key = str(id(agent))
        fake_cm = MagicMock()
        fake_span = MagicMock()
        self.handler._agent_spans[key] = (fake_cm, fake_span)
        output = MagicMock()
        output.return_values = {"result": "done"}
        self.handler.on_agent_finish(agent, output)
        assert key not in self.handler._agent_spans
        fake_span.set_attribute.assert_called_once()
        fake_cm.__exit__.assert_called_once_with(None, None, None)

    def test_on_agent_finish_with_active_span_no_return_values(self):
        """Line 88: cm.__exit__ called even when output lacks return_values."""
        agent = MagicMock()
        key = str(id(agent))
        fake_cm = MagicMock()
        fake_span = MagicMock()
        self.handler._agent_spans[key] = (fake_cm, fake_span)
        self.handler.on_agent_finish(agent, "plain string output")
        fake_span.set_attribute.assert_not_called()
        fake_cm.__exit__.assert_called_once_with(None, None, None)

    def test_on_agent_finish_exception_is_swallowed(self):
        """Lines 89-90: except Exception: pass in on_agent_finish."""
        agent = MagicMock()
        key = str(id(agent))
        fake_cm = MagicMock()
        fake_cm.__exit__.side_effect = RuntimeError("exit failed")
        fake_span = MagicMock()
        self.handler._agent_spans[key] = (fake_cm, fake_span)
        self.handler.on_agent_finish(agent, "output")  # must not raise


class TestCrewAIPatch:
    def test_patch_raises_import_error_without_crewai(self):
        from agentobs.integrations.crewai import patch as crewai_patch  # noqa: PLC0415
        with patch("importlib.util.find_spec", return_value=None):
            with pytest.raises(ImportError, match="crewai"):
                crewai_patch()

    def test_patch_warns_when_no_callbacks_list(self):
        from agentobs.integrations import crewai as crewai_mod  # noqa: PLC0415
        from agentobs.integrations.crewai import patch as crewai_patch  # noqa: PLC0415

        # Simulate crewai available but without a .callbacks list
        fake_crewai = MagicMock(spec=[])  # no .callbacks attribute
        with patch.dict("sys.modules", {"crewai": fake_crewai}):
            with patch("importlib.util.find_spec", return_value=MagicMock()):
                import warnings  # noqa: PLC0415
                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always")
                    crewai_patch()
                # Should either succeed silently or warn — must not raise AttributeError

    def test_patch_appends_handler_when_callbacks_list_exists(self):
        """Lines 199-201: patch() registers handler into crewai.callbacks list."""
        import sys  # noqa: PLC0415
        import types  # noqa: PLC0415
        from agentobs.integrations.crewai import patch as crewai_patch  # noqa: PLC0415

        fake_crewai = types.ModuleType("crewai")
        fake_crewai.callbacks = []  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"crewai": fake_crewai}):
            with patch("importlib.util.find_spec", return_value=MagicMock()):
                crewai_patch()
        assert len(fake_crewai.callbacks) == 1  # type: ignore[attr-defined]

    def test_patch_warns_when_callbacks_append_raises(self):
        """Lines 202-203: warnings.warn() emitted when crewai integration throws."""
        import sys  # noqa: PLC0415
        import types  # noqa: PLC0415
        from agentobs.integrations.crewai import patch as crewai_patch  # noqa: PLC0415

        class _RaisingList(list):
            def append(self, item: object) -> None:
                raise RuntimeError("append unavailable")

        fake_crewai = types.ModuleType("crewai")
        fake_crewai.callbacks = _RaisingList()  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"crewai": fake_crewai}):
            with patch("importlib.util.find_spec", return_value=MagicMock()):
                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always")
                    crewai_patch()  # must not raise
        assert any("agentobs" in str(warning.message).lower() for warning in w)
