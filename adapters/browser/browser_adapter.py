"""
Browser adapter — talks to the Merge AI Chrome extension via local HTTP bridge.

If the extension is not connected, read_all raises a clear error and overlay
falls back to vision screenshot on merge.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote_plus

from adapters.base_adapter import BaseAdapter
from adapters.browser import bridge_server
from adapters.common.change_preview import ChangePreview


class BrowserAdapter(BaseAdapter):
    kind = "browser"
    can_write = True  # navigate / activate_tab (confirm-first)

    def __init__(self, source: str = "active-tab", base_url: Optional[str] = None):
        super().__init__(source)
        self.base_url = base_url or bridge_server.ensure_bridge()

    def _status(self) -> dict:
        return bridge_server.http_json("GET", f"{self.base_url}/status")

    def is_connected(self) -> bool:
        st = self._status()
        return bool(st.get("ok") and st.get("connected"))

    def _ensure_bridge(self) -> dict:
        st = self._status()
        if not st.get("ok"):
            raise RuntimeError(
                "Browser bridge not reachable. Start Merge AI and load the Chrome extension."
            )
        if not st.get("connected"):
            raise RuntimeError(
                "Chrome extension not connected. Load chrome_extension/ as an unpacked "
                "extension and keep Chrome open."
            )
        return st

    # ---------- BaseAdapter ----------

    def read_all(self) -> str:
        st = self._ensure_bridge()
        tab = st.get("active_tab") or {}
        title = tab.get("title") or "(no title)"
        url = tab.get("url") or ""
        text = (tab.get("text") or "").strip()
        if not text:
            text = "(no page text yet — focus a tab; extension syncs on navigation)"
        return f"URL: {url}\nTitle: {title}\n\n{text}"

    def search(self, value: Any) -> list:
        needle = str(value).lower()
        hits = []
        try:
            text = self.read_all()
        except RuntimeError:
            return hits
        for i, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                hits.append(f"line {i}: {line.strip()[:120]}")
        return hits

    # ---------- Actions (confirm-first) ----------

    def navigate(self, url: str) -> ChangePreview:
        url = (url or "").strip()
        if not url:
            raise ValueError("navigate needs url")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        def _apply():
            self._ensure_bridge()
            return bridge_server.http_json(
                "POST", f"{self.base_url}/navigate", {"url": url}
            )

        return ChangePreview(
            description="navigate",
            source=self.source,
            location="browser",
            old_value=None,
            new_value=url,
            _apply_fn=_apply,
        )

    def google_search(self, query: str) -> ChangePreview:
        query = (query or "").strip()
        if not query:
            raise ValueError("google_search needs query")
        url = "https://www.google.com/search?q=" + quote_plus(query)

        def _apply():
            self._ensure_bridge()
            return bridge_server.http_json(
                "POST", f"{self.base_url}/google_search", {"query": query}
            )

        return ChangePreview(
            description="google_search",
            source=self.source,
            location="browser",
            old_value=None,
            new_value=query,
            _apply_fn=_apply,
        )

    def activate_tab(self, tab_id: Any) -> ChangePreview:
        if tab_id is None or str(tab_id).strip() == "":
            raise ValueError("activate_tab needs tab_id")

        def _apply():
            self._ensure_bridge()
            return bridge_server.http_json(
                "POST",
                f"{self.base_url}/activate_tab",
                {"tab_id": tab_id},
            )

        return ChangePreview(
            description="activate_tab",
            source=self.source,
            location=f"tab:{tab_id}",
            old_value=None,
            new_value=str(tab_id),
            _apply_fn=_apply,
        )

    def list_tabs(self) -> list[dict]:
        st = self._ensure_bridge()
        tabs = st.get("tabs") or []
        return tabs if isinstance(tabs, list) else []
