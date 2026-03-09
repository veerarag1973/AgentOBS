"""agentobs.toolsmith — Tool Schema Builder (RFC-0001, Tool 4 / toolsmith).

Converts Python type annotations and docstrings into provider-specific tool
schemas and validates call arguments at runtime.

Public API::

    from agentobs.toolsmith import tool, ToolRegistry

    registry = ToolRegistry()

    @tool(registry=registry, description="Search the web for a query.")
    def search_web(query: str, max_results: int = 5) -> list[str]:
        \"\"\"Search the web.

        Args:
            query: The search query string.
            max_results: Maximum number of results to return.
        \"\"\"
        ...

    # Provider schemas
    openai_tools  = registry.to_openai_tools()
    anthropic_tools = registry.to_anthropic_tools()

    # Runtime call with validation
    result = registry.call("search_web", {"query": "llm tracing", "max_results": 3})
"""

from __future__ import annotations

import functools
import inspect
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, get_args, get_origin

__all__ = [
    "tool",
    "ToolRegistry",
    "ToolSchema",
    "ToolParameter",
    "ToolValidationError",
    "build_openai_schema",
    "build_anthropic_schema",
    "default_registry",
]


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ToolValidationError(Exception):
    """Raised when a tool call fails argument validation.

    Attributes:
        tool_name:  Name of the tool that was called.
        reason:     Human-readable description of the validation failure.
    """

    def __init__(self, tool_name: str, reason: str) -> None:
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"Tool validation failed for '{tool_name}': {reason}")


# ---------------------------------------------------------------------------
# Type annotation → JSON Schema mapping
# ---------------------------------------------------------------------------

# Map from Python built-in types to JSON Schema primitive type strings.
_PRIMITIVE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    bytes: "string",
}


def _annotation_to_json_schema(annotation: Any) -> dict[str, Any]:
    """Recursively convert a Python type annotation to a JSON Schema dict.

    Handles:
    - Primitives: ``str``, ``int``, ``float``, ``bool``
    - ``list`` / ``List[T]`` → ``{"type": "array", "items": <schema>}``
    - ``dict`` / ``Dict[K, V]`` → ``{"type": "object"}``
    - ``Optional[T]`` (``Union[T, None]``) → nullable schema
    - Bare ``list`` / ``dict`` without generics
    - Unrecognised annotations → ``{}``
    """
    import types as _types  # noqa: PLC0415

    # Strip inspect.Parameter.empty → {}
    if annotation is inspect.Parameter.empty:
        return {}

    # Handle None/NoneType
    if annotation is type(None):
        return {"type": "null"}

    # Primitives
    if annotation in _PRIMITIVE_MAP:
        return {"type": _PRIMITIVE_MAP[annotation]}

    # Generic aliases (list[str], List[str], Optional[str], Union[...], etc.)
    origin = get_origin(annotation)
    args = get_args(annotation)

    # Union / Optional
    # Optional[X] == Union[X, None]
    if origin is _union_type() or _is_union_origin(origin):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and len(args) == 2:
            # Optional[X] → inline nullable
            inner = _annotation_to_json_schema(non_none[0])
            inner = dict(inner)  # copy
            # JSON Schema draft-04 / OpenAI style: add nullable flag if any
            # We use a simple nullable wrapper compatible with OpenAI
            return inner  # OpenAI/Anthropic treat missing as optional; no nullable needed
        if len(non_none) == len(args):
            # Pure Union — return anyOf
            return {"anyOf": [_annotation_to_json_schema(a) for a in args]}
        # Union with None — return anyOf with null
        return {"anyOf": [_annotation_to_json_schema(a) for a in args]}

    # list / List[T]
    if origin is list or annotation is list:
        if args:
            return {"type": "array", "items": _annotation_to_json_schema(args[0])}
        return {"type": "array"}

    # dict / Dict[K, V]
    if origin is dict or annotation is dict:
        return {"type": "object"}

    # tuple
    if origin is tuple or annotation is tuple:
        return {"type": "array"}

    # Fallback: unknown annotation
    return {}


def _union_type() -> Any:
    """Return the ``types.UnionType`` (Python 3.10+ ``int | None``) or
    the ``typing.Union.__class_getitem__`` for older Pythons."""
    import types as _types  # noqa: PLC0415
    return getattr(_types, "UnionType", None)


def _is_union_origin(origin: Any) -> bool:
    """Return True if *origin* is ``typing.Union``."""
    import typing  # noqa: PLC0415
    return origin is getattr(typing, "Union", None)


# ---------------------------------------------------------------------------
# Docstring parser
# ---------------------------------------------------------------------------


def _parse_param_docs(docstring: str | None) -> dict[str, str]:
    """Extract parameter descriptions from a Google-style or NumPy docstring.

    Supports Google style::

        Args:
            query: The search query.
            max_results: Maximum number of results.

    and NumPy style::

        Parameters
        ----------
        query : str
            The search query.

    Returns:
        Mapping of ``{param_name: description}``.
    """
    if not docstring:
        return {}

    # Normalise indentation so regexes work regardless of source indentation.
    docstring = inspect.cleandoc(docstring)

    param_docs: dict[str, str] = {}

    # Google style — look for an Args/Arguments/Parameters section
    google_match = re.search(
        r"(?:Args|Arguments|Parameters)\s*:\s*\n((?:[ \t]+\S.*\n?)+)",
        docstring,
        re.MULTILINE,
    )
    if google_match:
        section = google_match.group(1)
        for line_match in re.finditer(
            r"^[ \t]+(\w+)\s*:\s*(.+)", section, re.MULTILINE
        ):
            name, desc = line_match.group(1), line_match.group(2).strip()
            param_docs[name] = desc
        if param_docs:
            return param_docs

    # NumPy style — Parameters section followed by a dashed underline
    numpy_match = re.search(
        r"Parameters\s*\n[ \t]*-{3,}[ \t]*\n((?:.+\n?)*?)(?:\n[ \t]*\n|\Z)",
        docstring,
        re.MULTILINE,
    )
    if numpy_match:
        section = numpy_match.group(1)
        # Match "param : type\n    description" or "param\n    description"
        for param_match in re.finditer(
            r"^(\w+)[ \t]*(?::[^\n]*)?\n[ \t]+(.+)", section, re.MULTILINE
        ):
            name, desc = param_match.group(1), param_match.group(2).strip()
            param_docs[name] = desc

    return param_docs


# ---------------------------------------------------------------------------
# ToolParameter / ToolSchema
# ---------------------------------------------------------------------------


@dataclass
class ToolParameter:
    """Describes one parameter of a tool function.

    Attributes:
        name:             Parameter name.
        json_schema:      JSON Schema dict for this parameter.
        description:      Human-readable description (from docstring).
        required:         ``True`` if the parameter has no default value.
    """

    name: str
    json_schema: dict[str, Any]
    description: str = ""
    required: bool = True


@dataclass
class ToolSchema:
    """Complete schema for a single tool function.

    Attributes:
        name:         Tool name (defaults to ``fn.__name__``).
        description:  Tool description (from the decorator or docstring summary).
        parameters:   Ordered list of :class:`ToolParameter` objects.
        fn:           The original callable.
    """

    name: str
    description: str
    parameters: list[ToolParameter]
    fn: Callable[..., Any]


# ---------------------------------------------------------------------------
# Schema builders
# ---------------------------------------------------------------------------


def build_openai_schema(schema: ToolSchema) -> dict[str, Any]:
    """Convert *schema* to the OpenAI function calling format.

    Returns::

        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {
                    "type": "object",
                    "properties": { ... },
                    "required": [ ... ],
                }
            }
        }
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in schema.parameters:
        prop = dict(param.json_schema)
        if param.description:
            prop["description"] = param.description
        properties[param.name] = prop
        if param.required:
            required.append(param.name)

    fn_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        fn_schema["required"] = required

    return {
        "type": "function",
        "function": {
            "name": schema.name,
            "description": schema.description,
            "parameters": fn_schema,
        },
    }


def build_anthropic_schema(schema: ToolSchema) -> dict[str, Any]:
    """Convert *schema* to the Anthropic tool use format.

    Returns::

        {
            "name": "...",
            "description": "...",
            "input_schema": {
                "type": "object",
                "properties": { ... },
                "required": [ ... ],
            }
        }
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in schema.parameters:
        prop = dict(param.json_schema)
        if param.description:
            prop["description"] = param.description
        properties[param.name] = prop
        if param.required:
            required.append(param.name)

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        input_schema["required"] = required

    return {
        "name": schema.name,
        "description": schema.description,
        "input_schema": input_schema,
    }


# ---------------------------------------------------------------------------
# Schema extraction helper
# ---------------------------------------------------------------------------


def _build_schema(
    fn: Callable[..., Any],
    name: str | None = None,
    description: str | None = None,
) -> ToolSchema:
    """Introspect *fn* and return a :class:`ToolSchema`.

    Reads ``inspect.signature``  for parameter names, defaults, and annotations;
    reads ``inspect.getdoc`` for descriptions.  Uses :func:`typing.get_type_hints`
    to resolve PEP 563 stringified annotations (``from __future__ import annotations``).
    """
    import typing  # noqa: PLC0415

    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or ""
    param_docs = _parse_param_docs(doc)

    # Resolve actual type objects (handles PEP 563 / `from __future__ import annotations`).
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}

    # Extract description: from explicit arg first; otherwise first non-blank
    # line of the docstring.
    if description:
        tool_description = description
    elif doc:
        first_para_lines = []
        for line in inspect.cleandoc(doc).splitlines():
            stripped = line.strip()
            if not stripped or stripped.lower().startswith(("args:", "arguments:", "parameters", "returns", "raises", "example")):
                break
            first_para_lines.append(stripped)
        tool_description = " ".join(first_para_lines).strip()
    else:
        tool_description = ""

    parameters: list[ToolParameter] = []
    for param_name, param in sig.parameters.items():
        # Skip self/cls
        if param_name in ("self", "cls"):
            continue
        # Skip *args / **kwargs
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        # Prefer the resolved hint; fall back to the raw annotation.
        annotation = hints.get(param_name, param.annotation)
        json_schema = _annotation_to_json_schema(annotation)
        is_required = param.default is inspect.Parameter.empty
        param_desc = param_docs.get(param_name, "")

        parameters.append(
            ToolParameter(
                name=param_name,
                json_schema=json_schema,
                description=param_desc,
                required=is_required,
            )
        )

    return ToolSchema(
        name=name or fn.__name__,
        description=tool_description,
        parameters=parameters,
        fn=fn,
    )


# ---------------------------------------------------------------------------
# @tool decorator
# ---------------------------------------------------------------------------


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    registry: "ToolRegistry | None" = None,
) -> Any:
    """Decorate a function as a tool, building its schema from type annotations.

    Can be used with or without arguments::

        @tool
        def search(query: str) -> str: ...

        @tool(description="Search the web.", registry=my_registry)
        def search(query: str) -> str: ...

    Args:
        fn:          The function to decorate (when used without parentheses).
        name:        Override the tool name (defaults to ``fn.__name__``).
        description: Override the description (defaults to docstring summary).
        registry:    :class:`ToolRegistry` to register this tool into.
                     When ``None``, uses :data:`default_registry`.

    Returns:
        The original function unchanged, with a ``__tool_schema__`` attribute
        set to the generated :class:`ToolSchema`.
    """
    def _decorate(f: Callable[..., Any]) -> Callable[..., Any]:
        schema = _build_schema(f, name=name, description=description)
        # Attach the schema to the function for easy introspection.
        f.__tool_schema__ = schema  # type: ignore[attr-defined]

        # Register in the provided registry or the module default.
        reg = registry if registry is not None else default_registry
        reg.register(schema)

        @functools.wraps(f)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            return f(*args, **kwargs)

        _wrapper.__tool_schema__ = schema  # type: ignore[attr-defined]
        return _wrapper

    # Called as @tool (without parentheses)
    if fn is not None:
        return _decorate(fn)

    # Called as @tool(...) (with parentheses)
    return _decorate


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Collects :class:`ToolSchema` objects and provides schema export + call routing.

    Usage::

        registry = ToolRegistry()

        @tool(registry=registry)
        def search_web(query: str, max_results: int = 5) -> list[str]:
            \"\"\"Search the web.\"\"\"
            ...

        openai_tools = registry.to_openai_tools()
        result = registry.call("search_web", {"query": "hello"})
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._schemas: dict[str, ToolSchema] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, schema: ToolSchema) -> None:
        """Register a :class:`ToolSchema`.

        If a schema with the same name already exists it is silently replaced.
        """
        with self._lock:
            self._schemas[schema.name] = schema

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry by name.  No-op if not registered."""
        with self._lock:
            self._schemas.pop(name, None)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, name: str) -> ToolSchema | None:
        """Return the :class:`ToolSchema` for *name*, or ``None``."""
        with self._lock:
            return self._schemas.get(name)

    def names(self) -> list[str]:
        """Return a sorted list of registered tool names."""
        with self._lock:
            return sorted(self._schemas.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._schemas)

    def __contains__(self, name: str) -> bool:
        with self._lock:
            return name in self._schemas

    # ------------------------------------------------------------------
    # Schema export
    # ------------------------------------------------------------------

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """Return all tools as an OpenAI ``tools`` list.

        Suitable for passing directly to
        ``openai.chat.completions.create(tools=...)``.
        """
        with self._lock:
            schemas = list(self._schemas.values())
        return [build_openai_schema(s) for s in schemas]

    def to_anthropic_tools(self) -> list[dict[str, Any]]:
        """Return all tools as an Anthropic ``tools`` list.

        Suitable for passing directly to
        ``anthropic.messages.create(tools=...)``.
        """
        with self._lock:
            schemas = list(self._schemas.values())
        return [build_anthropic_schema(s) for s in schemas]

    # ------------------------------------------------------------------
    # Runtime call
    # ------------------------------------------------------------------

    def call(self, name: str, args: dict[str, Any]) -> Any:
        """Look up a tool by *name*, validate *args*, and call it.

        Args:
            name: Tool name as registered.
            args: Argument dict to pass to the function.

        Returns:
            The return value of the tool function.

        Raises:
            KeyError: If *name* is not registered.
            ToolValidationError: If *args* fails signature-based validation.
        """
        with self._lock:
            schema = self._schemas.get(name)
        if schema is None:
            raise KeyError(f"No tool named '{name}' is registered.")

        # Validate: check required params are present.
        for param in schema.parameters:
            if param.required and param.name not in args:
                raise ToolValidationError(
                    name,
                    f"required parameter '{param.name}' is missing from args",
                )

        # Check for unexpected params.
        known = {p.name for p in schema.parameters}
        unexpected = set(args.keys()) - known
        if unexpected:
            raise ToolValidationError(
                name,
                f"unexpected parameter(s): {', '.join(sorted(unexpected))}",
            )

        # Call via signature.bind for proper kwarg handling.
        try:
            sig = inspect.signature(schema.fn)
            bound = sig.bind(**args)
            bound.apply_defaults()
        except TypeError as exc:
            raise ToolValidationError(name, str(exc)) from exc

        return schema.fn(*bound.args, **bound.kwargs)

    def clear(self) -> None:
        """Remove all registered tools."""
        with self._lock:
            self._schemas.clear()

    def __repr__(self) -> str:
        names = self.names()
        return f"ToolRegistry({names!r})"


# ---------------------------------------------------------------------------
# Module-level default registry
# ---------------------------------------------------------------------------

#: Module-level singleton registry used when ``@tool`` is called without an
#: explicit ``registry=`` argument.
default_registry: ToolRegistry = ToolRegistry()
