"""
Session manager: tracks multiple merged sources of ANY adapter type at once
(mix Excel + PDF + Word + terminal + vision in one session), produces the
status-banner text, detects stale file-based sources, and provides
cross-source search.

Session content vs session metadata (kept distinct on purpose):
- Metadata (persisted-friendly): title, timestamps, list of sources/kinds.
- Content: actual document data, loaded lazily via adapters and dropped
  when the source is removed. Never persisted beyond runtime in this MVP.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from adapters.base_adapter import BaseAdapter
from adapters.registry import get_adapter_for_path, get_adapter_for_kind


@dataclass
class MergedSource:
    identifier: str             # file path, or logical name for non-file adapters
    merged_at: datetime
    adapter: Optional[BaseAdapter] = field(default=None, repr=False)
    is_file: bool = True
    stale: bool = False

    @property
    def name(self) -> str:
        return os.path.basename(self.identifier) if self.is_file else self.identifier


class Session:
    def __init__(self, title: Optional[str] = None):
        self.id = str(uuid.uuid4())[:8]
        self.created_at = datetime.now()
        self._title = title
        self.sources: dict[str, MergedSource] = {}  # keyed by identifier
        # Chat history for this session: [{"role": "user"|"assistant"|"system", "content": str}]
        self.messages: list[dict[str, str]] = []

    # ---------- title ----------

    @property
    def title(self) -> str:
        if self._title:
            return self._title
        if not self.sources:
            return "New chat"
        names = [s.name for s in self.sources.values()]
        return ", ".join(names[:2]) + ("…" if len(names) > 2 else "")

    def set_title(self, title: str) -> None:
        cleaned = (title or "").strip()
        if len(cleaned) > 48:
            cleaned = cleaned[:45] + "…"
        self._title = cleaned or "New chat"

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    # ---------- merge / drop ----------

    def merge_file(self, file_path: str) -> MergedSource:
        """Merge a document on disk — adapter chosen automatically by extension."""
        abs_path = os.path.abspath(file_path)
        adapter = get_adapter_for_path(abs_path)  # raises FileNotFoundError / ValueError
        source = MergedSource(identifier=abs_path, merged_at=datetime.now(), adapter=adapter, is_file=True)
        self.sources[abs_path] = source
        # First merged file becomes the chat name
        if len(self.sources) == 1:
            self.set_title(source.name)
        return source

    def merge_kind(self, kind: str, name: Optional[str] = None) -> MergedSource:
        """Merge a non-file adapter (terminal, vision, browser, libreoffice_live)."""
        adapter = get_adapter_for_kind(kind, name)
        identifier = name or kind
        source = MergedSource(identifier=identifier, merged_at=datetime.now(), adapter=adapter, is_file=False)
        self.sources[identifier] = source
        if len(self.sources) == 1:
            self.set_title(source.name)
        return source

    def drop(self, identifier: str) -> None:
        key = os.path.abspath(identifier) if os.path.isabs(identifier) else identifier
        source = self.sources.pop(key, None)
        if source is None:
            # try matching as a file path resolved differently
            key = os.path.abspath(identifier)
            source = self.sources.pop(key, None)
        if source and source.adapter:
            source.adapter.close()

    def refresh_staleness(self) -> list[MergedSource]:
        """File-based sources go stale if the file disappears; non-file
        sources (terminal, vision, browser) are never marked stale here."""
        stale = []
        for source in self.sources.values():
            if source.is_file:
                source.stale = not os.path.isfile(source.identifier)
            if source.stale:
                stale.append(source)
        return stale

    # ---------- lookups ----------

    def get(self, identifier: str) -> Optional[MergedSource]:
        key = os.path.abspath(identifier) if os.path.isabs(identifier) else identifier
        return self.sources.get(key) or self.sources.get(os.path.abspath(identifier))

    def all_adapters(self) -> list[tuple[str, BaseAdapter]]:
        return [
            (s.name, s.adapter)
            for s in self.sources.values()
            if s.adapter and not s.stale
        ]

    def search_all(self, value) -> dict[str, list]:
        """Cross-source search: value -> {source_name: [locations...]}"""
        results = {}
        for name, adapter in self.all_adapters():
            try:
                hits = adapter.search(value)
            except NotImplementedError:
                continue  # stub adapters (browser, libreoffice, vision-uncaptured)
            if hits:
                results[name] = hits
        return results

    # ---------- banner ----------

    def banner_text(self) -> str:
        self.refresh_staleness()
        if not self.sources:
            return "No sources merged yet."

        active = [s.name for s in self.sources.values() if not s.stale]
        stale = [s.name for s in self.sources.values() if s.stale]

        parts = []
        if active:
            parts.append("Merged in this session: " + ", ".join(active))
        if stale:
            parts.append(
                "No longer available: " + ", ".join(stale)
                + " (missing) — reopen and re-merge, or continue without it"
            )
        return " · ".join(parts)

    def close_all(self) -> None:
        for source in list(self.sources.values()):
            if source.adapter:
                source.adapter.close()
        self.sources.clear()


class SessionManager:
    """Holds multiple named sessions, like a chat-history sidebar."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.active_session_id: Optional[str] = None

    def new_session(self, title: Optional[str] = None) -> Session:
        """Create a new chat and make it active. Never deletes prior sessions."""
        if title is None:
            title = f"Chat {len(self.sessions) + 1}"
        session = Session(title=title)
        self.sessions[session.id] = session
        self.active_session_id = session.id
        return session

    def get_active(self) -> Optional[Session]:
        if self.active_session_id is None:
            return None
        return self.sessions.get(self.active_session_id)

    def switch_to(self, session_id: str) -> Session:
        if session_id not in self.sessions:
            raise KeyError(f"No session with id {session_id}")
        self.active_session_id = session_id
        return self.sessions[session_id]

    def find_by_source(self, identifier: str) -> Optional[Session]:
        """Return an existing session that already merged this source id/path."""
        key = os.path.abspath(identifier) if os.path.isabs(identifier) or os.path.sep in identifier else identifier
        for session in self.sessions.values():
            if key in session.sources or identifier in session.sources:
                return session
            # Also match by basename title for convenience
            for source in session.sources.values():
                if source.identifier == identifier or source.name == os.path.basename(identifier):
                    return session
        return None

    def list_sessions(self) -> list[tuple[str, str]]:
        """Returns [(id, title), ...] newest first."""
        items = sorted(self.sessions.values(), key=lambda s: s.created_at, reverse=True)
        return [(s.id, s.title) for s in items]
