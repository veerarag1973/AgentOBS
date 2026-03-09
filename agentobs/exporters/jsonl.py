"""agentobs.exporters.jsonl — Synchronous JSONL file exporter.

Appends one canonical JSON line per event to a file on disk.  Zero external
dependencies (stdlib only).

Usage::

    from agentobs import configure
    configure(exporter="jsonl", endpoint="./events.jsonl")

    # Now all tracer.span() / agent_run() / agent_step() calls write to
    # events.jsonl automatically.

You can also instantiate directly for testing::

    from agentobs.exporters.jsonl import SyncJSONLExporter
    from agentobs.event import Event, EventType, Tags

    exporter = SyncJSONLExporter("/tmp/test.jsonl")  # noqa: S108  # NOSONAR
    exporter.export(my_event)
    exporter.close()
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import IO, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from agentobs.event import Event

__all__ = ["SyncJSONLExporter"]

_PathLike = Union[str, Path]


class SyncJSONLExporter:
    """Synchronous exporter that appends events as newline-delimited JSON.

    Thread-safe: a :class:`threading.Lock` serialises concurrent writes so
    the output file is never corrupted when multiple threads share one
    instance.

    Args:
        path:     File path, :class:`pathlib.Path`, or ``"-"`` for stdout.
        mode:     File open mode — ``"a"`` (append, default) or ``"w"``
                  (overwrite / truncate on first write).
        encoding: File encoding (default ``"utf-8"``).

    Raises:
        OSError: If the file cannot be opened or written.
    """

    def __init__(
        self,
        path: _PathLike | str = "agentobs_events.jsonl",
        mode: str = "a",
        encoding: str = "utf-8",
    ) -> None:
        if mode not in ("a", "w"):
            raise ValueError("mode must be 'a' or 'w'")
        self._path_str = str(path)
        self._mode = mode
        self._encoding = encoding
        self._file: IO[str] | None = None
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------
    # Internal file management
    # ------------------------------------------------------------------

    def _ensure_open(self) -> IO[str]:
        """Open the file lazily on first write."""
        if self._file is not None and not self._file.closed:
            return self._file
        if self._path_str == "-":
            self._file = sys.stdout
            return self._file
        # Create parent directories if they don't exist.
        p = Path(self._path_str)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._file = p.open(self._mode, encoding=self._encoding)
        # After first open, always append.
        self._mode = "a"
        return self._file

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def export(self, event: Event) -> None:
        """Write *event* as a single JSON line.

        Args:
            event: A fully-formed :class:`~agentobs.event.Event` instance.

        Raises:
            RuntimeError: If :meth:`close` has already been called.
            OSError:       If the file write fails.
        """
        if self._closed:
            raise RuntimeError("SyncJSONLExporter is closed")
        line = event.to_json() + "\n"
        with self._lock:
            fh = self._ensure_open()
            fh.write(line)
            fh.flush()

    def flush(self) -> None:
        """Flush any buffered data to disk."""
        with self._lock:
            if self._file is not None and not self._file.closed:
                self._file.flush()

    def close(self) -> None:
        """Flush and close the output file.  Safe to call multiple times."""
        with self._lock:
            if not self._closed:
                if self._file is not None and self._file is not sys.stdout:
                    try:
                        self._file.flush()
                        self._file.close()
                    except OSError:
                        pass
                    self._file = None
                self._closed = True

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> SyncJSONLExporter:
        return self

    def __exit__(self, *_: object) -> bool:
        self.close()
        return False

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return f"SyncJSONLExporter(path={self._path_str!r}, state={state})"
