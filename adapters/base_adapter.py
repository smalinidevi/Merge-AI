"""
Every adapter — Excel, PDF, Word, LibreOffice-live, Terminal, Vision, Browser —
implements this contract. The session layer and the future AI-query layer
talk to adapters only through this interface, so adding a new adapter never
requires touching session_manager.py or main.py's core logic.

Not every adapter can do everything (PDFs can't be edited in place, vision
is read-only by design). Rather than isinstance-checking adapter types
everywhere, callers check `.can_write` / `.can_execute` and call `read_all()`
/ `search()`, which every adapter must implement meaningfully.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseAdapter(ABC):
    kind: str = "base"          # short identifier: "excel", "pdf", "word", ...
    can_write: bool = False      # supports write/append/delete-style operations
    can_execute: bool = False    # supports "run something" ops (terminal only)
    requires_confirm: bool = True  # writes/executes always confirm-first; no adapter should override this to False

    def __init__(self, source: str):
        """`source` is a file path for document adapters, or a logical
        identifier (e.g. "local-shell", "active-tab") for system adapters."""
        self.source = source

    @abstractmethod
    def read_all(self) -> str:
        """Plain-text-ish dump of the whole source, for feeding an AI model
        as context. Should be reasonably bounded in size by the adapter
        (e.g. truncate/summarize huge sheets) — that's an adapter concern,
        not the caller's."""

    @abstractmethod
    def search(self, value: Any) -> list:
        """Return locations where `value` is found. Shape of each location
        is adapter-specific (cell coordinate, page number, paragraph index,
        line number) but must be human-readable when str()'d."""

    def close(self) -> None:
        """Release any open handles/connections. Default: no-op."""
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} kind={self.kind} source={self.source!r}>"
