"""agentobs.auto — Automatic integration discovery and patching.

Call :func:`setup` to automatically detect and patch all AgentOBS-supported
LLM libraries that are installed in the current environment.  This eliminates
the need to manually import each integration module.

Usage \u2014 fastest path to value::

    import agentobs.auto
    agentobs.auto.setup()  # patches everything installed

Or call explicitly for programmatic control::

    from agentobs.auto import setup
    patched = setup(verbose=True)
    # patched = {"openai", "anthropic"}

Note
----
:func:`setup` is **not** called automatically on import.  You must call it
explicitly so that importing :mod:`agentobs` never silently monkey-patches
third-party libraries without your consent.

Supported libraries (patched when installed):
    * **openai** — :mod:`agentobs.integrations.openai`
    * **anthropic** — :mod:`agentobs.integrations.anthropic`
    * **groq** — :mod:`agentobs.integrations.groq`
    * **ollama** — :mod:`agentobs.integrations.ollama`
    * **together** — :mod:`agentobs.integrations.together`

Callback-based integrations (register manually):
    * **LangChain** — use :class:`~agentobs.integrations.langchain.LLMSchemaCallbackHandler`
    * **LlamaIndex** — use :class:`~agentobs.integrations.llamaindex.LLMSchemaEventHandler`
    * **CrewAI** — use :func:`~agentobs.integrations.crewai.patch`

Security note
-------------
Monkey-patching is only applied when the target library is already installed.
The patching flag ``_agentobs_patched`` prevents double-patching.  Each
integration is wrapped in a ``try/except`` so a broken integration never
prevents the others from loading.
"""

from __future__ import annotations

import importlib.util
import threading
import warnings

__all__ = ["setup", "teardown", "patched_integrations"]

# Internal registry of successfully patched integrations (module name → patch fn).
_PATCHED: set[str] = set()
_PATCHED_LOCK = threading.Lock()

# Map of library import name → (integration module path, patch fn name, unpatch fn name)
_INTEGRATIONS: list[tuple[str, str, str, str]] = [
    ("openai", "agentobs.integrations.openai", "patch", "unpatch"),
    ("anthropic", "agentobs.integrations.anthropic", "patch", "unpatch"),
    ("groq", "agentobs.integrations.groq", "patch", "unpatch"),
    ("ollama", "agentobs.integrations.ollama", "patch", "unpatch"),
    ("together", "agentobs.integrations.together", "patch", "unpatch"),
]


def setup(*, verbose: bool = False) -> set[str]:
    """Detect and patch all installed AgentOBS-supported LLM libraries.

    Iterates over supported integrations and calls their ``patch()`` function
    if the underlying library is installed.  Already-patched integrations are
    skipped silently (idempotent).

    Args:
        verbose: When ``True``, print a status line for each integration
                 attempted.

    Returns:
        Set of library names that were newly patched in this call (does not
        include libraries already patched in previous calls).

    Example::

        from agentobs.auto import setup
        patched = setup(verbose=True)
        # openai patched ✓
        # anthropic not installed, skipped

    Note:
        Callback-based integrations (LangChain, LlamaIndex, CrewAI) are not
        auto-patched because they require manual handler registration.  See
        their respective integration guides.
    """
    newly_patched: set[str] = set()

    for lib_name, integration_module, patch_fn, _unpatch_fn in _INTEGRATIONS:
        if lib_name in _PATCHED:
            if verbose:
                print(f"  {lib_name}: already patched, skipped")
            continue

        # Check if the library is installed without importing it.
        if importlib.util.find_spec(lib_name) is None:
            if verbose:
                print(f"  {lib_name}: not installed, skipped")
            continue

        try:
            mod = importlib.import_module(integration_module)
            getattr(mod, patch_fn)()
            _PATCHED.add(lib_name)
            newly_patched.add(lib_name)
            if verbose:
                print(f"  {lib_name}: patched \u2713")
        except Exception as exc:
            warnings.warn(
                f"agentobs.auto: failed to patch {lib_name!r}: {exc}",
                UserWarning,
                stacklevel=2,
            )
            if verbose:
                print(f"  {lib_name}: patch failed — {exc}")

    return newly_patched


def teardown(*, verbose: bool = False) -> set[str]:
    """Unpatch all auto-patched integrations and reset the auto-patch registry.

    Calls ``unpatch()`` on every integration that was patched via
    :func:`setup`.  Safe to call even if :func:`setup` was never called.

    Args:
        verbose: When ``True``, print a status line for each integration.

    Returns:
        Set of library names that were unpatched.
    """
    unpatched: set[str] = set()

    for lib_name, integration_module, _patch_fn, unpatch_fn in _INTEGRATIONS:
        with _PATCHED_LOCK:
            if lib_name not in _PATCHED:
                continue
        try:
            mod = importlib.import_module(integration_module)
            getattr(mod, unpatch_fn)()
            with _PATCHED_LOCK:
                _PATCHED.discard(lib_name)
            unpatched.add(lib_name)
            if verbose:
                print(f"  {lib_name}: unpatched \u2713")
        except Exception as exc:
            warnings.warn(
                f"agentobs.auto: failed to unpatch {lib_name!r}: {exc}",
                UserWarning,
                stacklevel=2,
            )

    return unpatched


def patched_integrations() -> set[str]:
    """Return the set of library names currently patched via :func:`setup`.

    Returns:
        Snapshot of the currently patched integration names.
    """
    with _PATCHED_LOCK:
        return set(_PATCHED)


# NOTE: setup() is NOT called automatically on import.
# Call agentobs.auto.setup() explicitly to patch installed integrations.
# This is intentional: importing agentobs should never monkey-patch
# third-party libraries without explicit user consent.
