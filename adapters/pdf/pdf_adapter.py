"""
PDF adapter: read + structured extraction only. PDFs are not edited in
place in this design — there's no reliable "write back" for PDF text the
way there is for Excel/Word, so can_write stays False by design, not as a
missing feature.

Citations: search_hits() returns page + snippet for RAG-style answers.
Live viewer highlight (blue box) is intentionally out of scope.
"""

from __future__ import annotations

import os
import re
from typing import Any

from pypdf import PdfReader

from adapters.base_adapter import BaseAdapter


class PdfAdapter(BaseAdapter):
    kind = "pdf"
    can_write = False

    def __init__(self, file_path: str):
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"PDF file not found: {file_path}")
        super().__init__(os.path.abspath(file_path))
        self.file_path = self.source
        self._reader = PdfReader(self.file_path)
        self._page_text_cache: list[str] | None = None

    def _page_texts(self) -> list[str]:
        if self._page_text_cache is None:
            self._page_text_cache = [page.extract_text() or "" for page in self._reader.pages]
        return self._page_text_cache

    # ---------- BaseAdapter contract ----------

    def read_all(self, max_chars: int = 20000) -> str:
        full = "\n\n".join(
            f"--- Page {i + 1} ---\n{text}" for i, text in enumerate(self._page_texts())
        )
        if len(full) > max_chars:
            return full[:max_chars] + f"\n... (truncated, {len(full) - max_chars} more chars)"
        return full

    def search_hits(self, value: Any, *, snippet_radius: int = 80, max_hits: int = 20) -> list[dict]:
        """Return [{page, snippet, quote}] for substring matches (case-insensitive)."""
        needle = str(value or "").strip()
        if not needle:
            return []
        needle_l = needle.lower()
        hits: list[dict] = []
        for i, text in enumerate(self._page_texts()):
            lower = text.lower()
            start = 0
            while True:
                idx = lower.find(needle_l, start)
                if idx < 0:
                    break
                left = max(0, idx - snippet_radius)
                right = min(len(text), idx + len(needle) + snippet_radius)
                snippet = text[left:right].replace("\n", " ").strip()
                if left > 0:
                    snippet = "…" + snippet
                if right < len(text):
                    snippet = snippet + "…"
                quote = text[idx : idx + len(needle)].replace("\n", " ").strip()
                hits.append(
                    {
                        "page": i + 1,
                        "snippet": snippet,
                        "quote": quote,
                    }
                )
                if len(hits) >= max_hits:
                    return hits
                start = idx + max(1, len(needle_l))
        return hits

    def search(self, value: Any) -> list:
        """Human-readable locations: 'page N: \"snippet\"' (also works with str())."""
        return [
            f'page {h["page"]}: "{h["snippet"]}"' for h in self.search_hits(value)
        ]

    def search_pages(self, value: Any) -> list[int]:
        """1-indexed page numbers containing the substring (unique, ordered)."""
        seen: list[int] = []
        for h in self.search_hits(value):
            p = int(h["page"])
            if p not in seen:
                seen.append(p)
        return seen

    # ---------- extras ----------

    def read_page(self, page_number: int) -> str:
        """1-indexed page number."""
        texts = self._page_texts()
        if not (1 <= page_number <= len(texts)):
            raise IndexError(f"Page {page_number} out of range (1-{len(texts)})")
        return texts[page_number - 1]

    @property
    def page_count(self) -> int:
        return len(self._reader.pages)


_STOP = frozenset(
    "a an the and or of to for in on is are was were be by with from as at "
    "this that these those it its what where which who how find locate show "
    "tell me please can you".split()
)


def keywords_from_text(text: str, *, max_terms: int = 8) -> list[str]:
    """Lightweight keyword extract for PDF retrieval (no external NLP)."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}", text or "")
    out: list[str] = []
    seen = set()
    for w in words:
        lw = w.lower()
        if lw in _STOP or lw in seen:
            continue
        seen.add(lw)
        out.append(w)
        if len(out) >= max_terms:
            break
    return out
