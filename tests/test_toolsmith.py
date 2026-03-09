"""Tests for agentobs.toolsmith — Tool 4: Tool Schema Builder.

Covers:
- @tool decorator (with and without arguments)
- _param_to_json_schema type mappings (str, int, float, bool, list, dict, Optional)
- Docstring parsing (Google-style, NumPy-style, plain)
- build_openai_schema() format
- build_anthropic_schema() format
- ToolRegistry.register / get / names / __len__ / __contains__
- ToolRegistry.to_openai_tools() / to_anthropic_tools()
- ToolRegistry.call() — success, missing required param, unexpected param
- ToolValidationError attributes
- Module-level default_registry
- Public API accessible from agentobs namespace
"""

from __future__ import annotations

from typing import Optional

import pytest

import agentobs
from agentobs.toolsmith import (
    ToolParameter,
    ToolRegistry,
    ToolSchema,
    ToolValidationError,
    _annotation_to_json_schema,
    _build_schema,
    _parse_param_docs,
    build_anthropic_schema,
    build_openai_schema,
    default_registry,
    tool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry() -> ToolRegistry:
    """Return a fresh, empty registry (isolated from default_registry)."""
    return ToolRegistry()


# ---------------------------------------------------------------------------
# _annotation_to_json_schema
# ---------------------------------------------------------------------------


class TestAnnotationToJsonSchema:
    def test_str(self):
        assert _annotation_to_json_schema(str) == {"type": "string"}

    def test_int(self):
        assert _annotation_to_json_schema(int) == {"type": "integer"}

    def test_float(self):
        assert _annotation_to_json_schema(float) == {"type": "number"}

    def test_bool(self):
        assert _annotation_to_json_schema(bool) == {"type": "boolean"}

    def test_bytes(self):
        assert _annotation_to_json_schema(bytes) == {"type": "string"}

    def test_list_bare(self):
        assert _annotation_to_json_schema(list) == {"type": "array"}

    def test_list_generic_str(self):
        assert _annotation_to_json_schema(list[str]) == {
            "type": "array",
            "items": {"type": "string"},
        }

    def test_list_generic_int(self):
        assert _annotation_to_json_schema(list[int]) == {
            "type": "array",
            "items": {"type": "integer"},
        }

    def test_dict_bare(self):
        assert _annotation_to_json_schema(dict) == {"type": "object"}

    def test_dict_generic(self):
        assert _annotation_to_json_schema(dict[str, int]) == {"type": "object"}

    def test_none_type(self):
        assert _annotation_to_json_schema(type(None)) == {"type": "null"}

    def test_optional_str(self):
        """Optional[str] is Union[str, None] — should produce string schema."""
        schema = _annotation_to_json_schema(Optional[str])
        # We accept the inner type (since OpenAI doesn't require nullable wrapper)
        assert schema.get("type") == "string" or "anyOf" in schema

    def test_optional_int(self):
        schema = _annotation_to_json_schema(Optional[int])
        assert schema.get("type") == "integer" or "anyOf" in schema

    def test_unknown_annotation(self):
        class CustomType:
            pass
        result = _annotation_to_json_schema(CustomType)
        assert isinstance(result, dict)  # should not raise; returns {}

    def test_empty_annotation(self):
        import inspect
        result = _annotation_to_json_schema(inspect.Parameter.empty)
        assert result == {}

    def test_tuple_bare(self):
        result = _annotation_to_json_schema(tuple)
        assert result == {"type": "array"}


# ---------------------------------------------------------------------------
# _parse_param_docs
# ---------------------------------------------------------------------------


class TestParseParamDocs:
    def test_google_style(self):
        doc = """
        Summary line.

        Args:
            query: The search query.
            max_results: Maximum number of results.
        """
        result = _parse_param_docs(doc)
        assert result["query"] == "The search query."
        assert result["max_results"] == "Maximum number of results."

    def test_arguments_keyword(self):
        doc = """
        Args:
            x: First number.
        """
        result = _parse_param_docs(doc)
        assert result["x"] == "First number."

    def test_empty_docstring(self):
        assert _parse_param_docs("") == {}

    def test_none_docstring(self):
        assert _parse_param_docs(None) == {}

    def test_no_args_section(self):
        doc = "This function does something."
        assert _parse_param_docs(doc) == {}

    def test_numpy_style(self):
        doc = """Summary.

        Parameters
        ----------
        query : str
            The search query.
        n : int
            Number of results.
        """
        result = _parse_param_docs(doc)
        assert "query" in result
        assert "n" in result

    def test_partial_docs(self):
        """Only some params documented."""
        doc = """Fn.

        Args:
            x: The x value.
        """
        result = _parse_param_docs(doc)
        assert result.get("x") == "The x value."


# ---------------------------------------------------------------------------
# _build_schema
# ---------------------------------------------------------------------------


class TestBuildSchema:
    def test_basic_schema(self):
        def my_fn(query: str, n: int = 5) -> list[str]:
            """Search for something."""
            ...

        schema = _build_schema(my_fn)
        assert schema.name == "my_fn"
        assert "Search" in schema.description
        assert len(schema.parameters) == 2

        q_param = next(p for p in schema.parameters if p.name == "query")
        assert q_param.required is True
        assert q_param.json_schema == {"type": "string"}

        n_param = next(p for p in schema.parameters if p.name == "n")
        assert n_param.required is False  # has default
        assert n_param.json_schema == {"type": "integer"}

    def test_custom_name_and_description(self):
        def fn(x: str) -> str:
            """Original doc."""
            ...

        schema = _build_schema(fn, name="custom_name", description="Custom desc.")
        assert schema.name == "custom_name"
        assert schema.description == "Custom desc."

    def test_no_annotations(self):
        def fn(a, b):
            ...

        schema = _build_schema(fn)
        assert len(schema.parameters) == 2
        for p in schema.parameters:
            assert p.json_schema == {}  # unknown annotation

    def test_skips_self(self):
        class Cls:
            def method(self, x: str) -> None:
                ...

        schema = _build_schema(Cls.method)
        assert all(p.name != "self" for p in schema.parameters)

    def test_skips_args_kwargs(self):
        def fn(*args, **kwargs):
            ...

        schema = _build_schema(fn)
        assert schema.parameters == []

    def test_param_descriptions_from_docstring(self):
        def search(query: str, limit: int = 10) -> list[str]:
            """Find things.

            Args:
                query: What to search for.
                limit: Max results.
            """
            ...

        schema = _build_schema(search)
        q = next(p for p in schema.parameters if p.name == "query")
        assert q.description == "What to search for."
        lim = next(p for p in schema.parameters if p.name == "limit")
        assert lim.description == "Max results."

    def test_optional_param_not_required(self):
        def fn(x: str, y: Optional[str] = None) -> str:
            ...

        schema = _build_schema(fn)
        y_param = next(p for p in schema.parameters if p.name == "y")
        assert y_param.required is False

    def test_list_param_schema(self):
        def fn(items: list[str]) -> None:
            ...

        schema = _build_schema(fn)
        assert schema.parameters[0].json_schema == {
            "type": "array",
            "items": {"type": "string"},
        }


# ---------------------------------------------------------------------------
# build_openai_schema
# ---------------------------------------------------------------------------


class TestBuildOpenAISchema:
    def _schema(self, fn, **kwargs):
        return _build_schema(fn, **kwargs)

    def test_structure(self):
        def search(query: str, n: int = 5) -> list[str]:
            """Search the web."""
            ...

        result = build_openai_schema(self._schema(search))
        assert result["type"] == "function"
        assert "function" in result
        fn = result["function"]
        assert fn["name"] == "search"
        assert "parameters" in fn
        params = fn["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
        assert "query" in params["properties"]
        assert "required" in params
        assert "query" in params["required"]
        assert "n" not in params["required"]

    def test_description_in_property(self):
        def fn(q: str) -> str:
            """Fn.

            Args:
                q: The query text.
            """
            ...

        result = build_openai_schema(self._schema(fn))
        prop = result["function"]["parameters"]["properties"]["q"]
        assert prop["description"] == "The query text."

    def test_no_required_when_all_optional(self):
        def fn(x: str = "default") -> str:
            ...

        result = build_openai_schema(self._schema(fn))
        params = result["function"]["parameters"]
        assert "required" not in params or params.get("required") == []

    def test_string_param_type(self):
        def fn(name: str) -> None:
            ...

        result = build_openai_schema(self._schema(fn))
        assert result["function"]["parameters"]["properties"]["name"]["type"] == "string"

    def test_bool_param_type(self):
        def fn(flag: bool = False) -> None:
            ...

        result = build_openai_schema(self._schema(fn))
        assert result["function"]["parameters"]["properties"]["flag"]["type"] == "boolean"

    def test_custom_description(self):
        def fn(x: str) -> str:
            ...

        result = build_openai_schema(self._schema(fn, description="My override."))
        assert result["function"]["description"] == "My override."


# ---------------------------------------------------------------------------
# build_anthropic_schema
# ---------------------------------------------------------------------------


class TestBuildAnthropicSchema:
    def _schema(self, fn, **kwargs):
        return _build_schema(fn, **kwargs)

    def test_structure(self):
        def search(query: str, n: int = 5) -> list[str]:
            """Search the web."""
            ...

        result = build_anthropic_schema(self._schema(search))
        assert "name" in result
        assert "description" in result
        assert "input_schema" in result
        inp = result["input_schema"]
        assert inp["type"] == "object"
        assert "properties" in inp
        assert "required" in inp
        assert "query" in inp["required"]
        assert "n" not in inp["required"]

    def test_description_in_property(self):
        def fn(q: str) -> str:
            """Fn.

            Args:
                q: The query.
            """
            ...

        result = build_anthropic_schema(self._schema(fn))
        prop = result["input_schema"]["properties"]["q"]
        assert prop["description"] == "The query."

    def test_name(self):
        def my_lookup(x: str) -> str:
            ...

        result = build_anthropic_schema(self._schema(my_lookup))
        assert result["name"] == "my_lookup"

    def test_list_param_items(self):
        def fn(items: list[int]) -> None:
            ...

        result = build_anthropic_schema(self._schema(fn))
        prop = result["input_schema"]["properties"]["items"]
        assert prop["type"] == "array"
        assert prop["items"] == {"type": "integer"}


# ---------------------------------------------------------------------------
# @tool decorator
# ---------------------------------------------------------------------------


class TestToolDecorator:
    def setup_method(self):
        self.reg = _make_registry()

    def test_basic_decoration(self):
        @tool(registry=self.reg)
        def lookup(query: str) -> str:
            """Look something up."""
            return f"result:{query}"

        assert "lookup" in self.reg
        schema = self.reg.get("lookup")
        assert schema is not None
        assert schema.name == "lookup"

    def test_function_still_callable(self):
        @tool(registry=self.reg)
        def add(x: int, y: int) -> int:
            return x + y

        assert add(1, 2) == 3

    def test_without_parentheses(self):
        reg = _make_registry()

        @tool
        def my_fn(x: str) -> str:
            return x

        # Registers in default_registry
        assert "my_fn" in default_registry
        default_registry.unregister("my_fn")

    def test_explicit_name(self):
        @tool(name="web_search", registry=self.reg)
        def search(query: str) -> str:
            return query

        assert "web_search" in self.reg
        assert "search" not in self.reg

    def test_explicit_description(self):
        @tool(description="Explicit description.", registry=self.reg)
        def fn(x: str) -> str:
            return x

        schema = self.reg.get("fn")
        assert schema.description == "Explicit description."

    def test_schema_attached_to_function(self):
        @tool(registry=self.reg)
        def fn_with_schema(x: int) -> int:
            return x

        assert hasattr(fn_with_schema, "__tool_schema__")
        assert isinstance(fn_with_schema.__tool_schema__, ToolSchema)

    def test_functools_wraps_preserves_name(self):
        @tool(registry=self.reg)
        def my_named_fn(x: str) -> str:
            return x

        assert my_named_fn.__name__ == "my_named_fn"

    def test_multiple_tools_registered(self):
        @tool(registry=self.reg)
        def tool_a(x: str) -> str:
            return x

        @tool(registry=self.reg)
        def tool_b(y: int) -> int:
            return y

        assert len(self.reg) == 2
        assert "tool_a" in self.reg
        assert "tool_b" in self.reg


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def setup_method(self):
        self.reg = _make_registry()

    def _add(self, name: str, fn=None):
        if fn is None:
            def _fn(x: str) -> str:
                return x
            _fn.__name__ = name
            fn = _fn
        schema = _build_schema(fn)
        object.__setattr__(schema, "name", name) if False else None
        # Re-build with proper name
        schema = ToolSchema(
            name=name,
            description="",
            parameters=schema.parameters,
            fn=schema.fn,
        )
        self.reg.register(schema)
        return schema

    def test_register_and_get(self):
        def fn(x: str) -> str:
            return x

        schema = _build_schema(fn)
        self.reg.register(schema)
        assert self.reg.get("fn") is schema

    def test_get_missing_returns_none(self):
        assert self.reg.get("nonexistent") is None

    def test_names_sorted(self):
        for n in ("zebra", "apple", "mango"):
            self._add(n)
        assert self.reg.names() == ["apple", "mango", "zebra"]

    def test_len(self):
        assert len(self.reg) == 0
        self._add("a")
        assert len(self.reg) == 1
        self._add("b")
        assert len(self.reg) == 2

    def test_contains(self):
        self._add("my_tool")
        assert "my_tool" in self.reg
        assert "other" not in self.reg

    def test_unregister(self):
        self._add("x")
        self.reg.unregister("x")
        assert "x" not in self.reg

    def test_unregister_missing_noop(self):
        self.reg.unregister("does_not_exist")  # should not raise

    def test_clear(self):
        self._add("a")
        self._add("b")
        self.reg.clear()
        assert len(self.reg) == 0

    def test_replace_on_duplicate_name(self):
        def fn1(x: str) -> str:
            return "v1"

        def fn2(x: str) -> str:
            return "v2"

        s1 = _build_schema(fn1, name="my_fn")
        s2 = _build_schema(fn2, name="my_fn")
        self.reg.register(s1)
        self.reg.register(s2)
        assert len(self.reg) == 1
        assert self.reg.get("my_fn").fn is fn2

    def test_repr(self):
        self._add("alpha")
        r = repr(self.reg)
        assert "alpha" in r


# ---------------------------------------------------------------------------
# ToolRegistry.to_openai_tools / to_anthropic_tools
# ---------------------------------------------------------------------------


class TestRegistrySchemaExport:
    def setup_method(self):
        self.reg = _make_registry()

        @tool(registry=self.reg)
        def search(query: str, limit: int = 10) -> list[str]:
            """Search the web.

            Args:
                query: The query string.
                limit: Max results.
            """
            return []

        @tool(registry=self.reg)
        def compute(x: float, y: float) -> float:
            """Compute something."""
            return x + y

    def test_to_openai_tools_length(self):
        tools = self.reg.to_openai_tools()
        assert len(tools) == 2

    def test_to_openai_tools_structure(self):
        tools = self.reg.to_openai_tools()
        for t in tools:
            assert t["type"] == "function"
            assert "function" in t
            assert "name" in t["function"]
            assert "parameters" in t["function"]

    def test_to_openai_tools_param_types(self):
        tools = self.reg.to_openai_tools()
        search_tool = next(t for t in tools if t["function"]["name"] == "search")
        props = search_tool["function"]["parameters"]["properties"]
        assert props["query"]["type"] == "string"
        assert props["limit"]["type"] == "integer"

    def test_to_anthropic_tools_length(self):
        tools = self.reg.to_anthropic_tools()
        assert len(tools) == 2

    def test_to_anthropic_tools_structure(self):
        tools = self.reg.to_anthropic_tools()
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "input_schema" in t
            assert t["input_schema"]["type"] == "object"

    def test_to_anthropic_tools_required(self):
        tools = self.reg.to_anthropic_tools()
        search_tool = next(t for t in tools if t["name"] == "search")
        assert "query" in search_tool["input_schema"]["required"]
        assert "limit" not in search_tool["input_schema"].get("required", [])

    def test_descriptions_in_properties(self):
        tools = self.reg.to_openai_tools()
        search_tool = next(t for t in tools if t["function"]["name"] == "search")
        assert search_tool["function"]["parameters"]["properties"]["query"].get("description") == "The query string."


# ---------------------------------------------------------------------------
# ToolRegistry.call()
# ---------------------------------------------------------------------------


class TestRegistryCall:
    def setup_method(self):
        self.reg = _make_registry()
        self.calls = []

        @tool(registry=self.reg)
        def greet(name: str, greeting: str = "Hello") -> str:
            result = f"{greeting}, {name}!"
            self.calls.append(result)
            return result

    def test_call_success(self):
        result = self.reg.call("greet", {"name": "Alice"})
        assert result == "Hello, Alice!"

    def test_call_with_optional(self):
        result = self.reg.call("greet", {"name": "Bob", "greeting": "Hi"})
        assert result == "Hi, Bob!"

    def test_call_missing_required_raises(self):
        with pytest.raises(ToolValidationError) as exc_info:
            self.reg.call("greet", {})
        assert "name" in str(exc_info.value)
        assert exc_info.value.tool_name == "greet"

    def test_call_unexpected_param_raises(self):
        with pytest.raises(ToolValidationError) as exc_info:
            self.reg.call("greet", {"name": "Alice", "unknown_param": "x"})
        assert "unexpected" in str(exc_info.value).lower()

    def test_call_unknown_tool_raises_key_error(self):
        with pytest.raises(KeyError):
            self.reg.call("nonexistent_tool", {})

    def test_call_invokes_function(self):
        self.reg.call("greet", {"name": "Carol"})
        assert len(self.calls) == 1
        assert "Carol" in self.calls[0]


# ---------------------------------------------------------------------------
# ToolValidationError
# ---------------------------------------------------------------------------


class TestToolValidationError:
    def test_attributes(self):
        err = ToolValidationError("my_tool", "missing x")
        assert err.tool_name == "my_tool"
        assert err.reason == "missing x"

    def test_str_contains_tool_name(self):
        err = ToolValidationError("my_tool", "missing x")
        assert "my_tool" in str(err)
        assert "missing x" in str(err)

    def test_is_exception(self):
        err = ToolValidationError("t", "r")
        assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# End-to-end workflow
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_full_workflow(self):
        """Registration → schema export → call → result."""
        reg = _make_registry()

        @tool(registry=reg)
        def multiply(x: float, y: float) -> float:
            """Multiply two numbers.

            Args:
                x: First factor.
                y: Second factor.
            """
            return x * y

        # Schema export
        openai_tools = reg.to_openai_tools()
        assert len(openai_tools) == 1
        fn_def = openai_tools[0]["function"]
        assert fn_def["name"] == "multiply"
        assert fn_def["parameters"]["properties"]["x"]["type"] == "number"
        assert fn_def["parameters"]["properties"]["y"]["type"] == "number"
        assert "x" in fn_def["parameters"]["required"]
        assert "y" in fn_def["parameters"]["required"]

        anthropic_tools = reg.to_anthropic_tools()
        assert anthropic_tools[0]["name"] == "multiply"

        # Runtime call
        assert reg.call("multiply", {"x": 3.0, "y": 4.0}) == pytest.approx(12.0)

    def test_optional_params_not_in_required(self):
        reg = _make_registry()

        @tool(registry=reg)
        def search(query: str, limit: int = 10, verbose: bool = False) -> list[str]:
            return []

        schema = build_openai_schema(reg.get("search"))
        required = schema["function"]["parameters"].get("required", [])
        assert "query" in required
        assert "limit" not in required
        assert "verbose" not in required

    def test_list_return_type_ignored(self):
        """Return type annotations do not appear in parameter schemas."""
        reg = _make_registry()

        @tool(registry=reg)
        def get_tags(item_id: str) -> list[str]:
            return []

        params = reg.to_openai_tools()[0]["function"]["parameters"]["properties"]
        assert "item_id" in params
        # No 'return' param from return type annotation
        assert len(params) == 1


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


class TestPublicAPI:
    def test_tool_accessible(self):
        assert hasattr(agentobs, "tool")
        assert agentobs.tool is tool

    def test_tool_registry_accessible(self):
        assert hasattr(agentobs, "ToolRegistry")
        assert agentobs.ToolRegistry is ToolRegistry

    def test_tool_schema_accessible(self):
        assert hasattr(agentobs, "ToolSchema")
        assert agentobs.ToolSchema is ToolSchema

    def test_tool_parameter_accessible(self):
        assert hasattr(agentobs, "ToolParameter")
        assert agentobs.ToolParameter is ToolParameter

    def test_tool_validation_error_accessible(self):
        assert hasattr(agentobs, "ToolValidationError")
        assert agentobs.ToolValidationError is ToolValidationError

    def test_build_openai_schema_accessible(self):
        assert hasattr(agentobs, "build_openai_schema")
        assert agentobs.build_openai_schema is build_openai_schema

    def test_build_anthropic_schema_accessible(self):
        assert hasattr(agentobs, "build_anthropic_schema")
        assert agentobs.build_anthropic_schema is build_anthropic_schema

    def test_default_registry_accessible(self):
        assert hasattr(agentobs, "default_registry")
        assert agentobs.default_registry is default_registry

    def test_all_contains_tool4_symbols(self):
        for sym in (
            "tool",
            "ToolRegistry",
            "ToolSchema",
            "ToolParameter",
            "ToolValidationError",
            "build_openai_schema",
            "build_anthropic_schema",
            "default_registry",
        ):
            assert sym in agentobs.__all__, f"{sym!r} missing from __all__"
