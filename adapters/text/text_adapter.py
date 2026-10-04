"""
Plain-text adapter (.txt / .md / .csv): read + confirm-first replace/append/write.
"""

from __future__ import annotations

import os
from typing import Any

from adapters.base_adapter import BaseAdapter
from adapters.common.change_preview import ChangePreview


class TextAdapter(BaseAdapter):
    kind = "text"
    can_write = True

    def __init__(self, file_path: str):
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Text file not found: {file_path}")
        super().__init__(os.path.abspath(file_path))
        self.file_path = self.source

    def _read_raw(self) -> str:
        with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def _write_raw(self, text: str) -> None:
        with open(self.file_path, "w", encoding="utf-8") as f:
            f.write(text)

    def read_all(self, max_chars: int = 20000) -> str:
        full = self._read_raw()
        if len(full) > max_chars:
            return full[:max_chars] + f"\n... (truncated, {len(full) - max_chars} more chars)"
        return full

    def search(self, value: Any) -> list[int]:
        needle = str(value).lower()
        hits = []
        for i, line in enumerate(self._read_raw().splitlines()):
            if needle in line.lower():
                hits.append(i + 1)
        return hits

    def replace_text(self, old: str, new: str, count: int = 0) -> ChangePreview:
        current = self._read_raw()
        if old not in current:
            raise ValueError(f"Text not found: {old!r}")
        updated = current.replace(old, new, count if count > 0 else -1)

        def _apply():
            self._write_raw(updated)

        return ChangePreview(
            description="replace_text",
            source=self.file_path,
            location="file",
            old_value=old,
            new_value=new,
            _apply_fn=_apply,
        )

    def append_text(self, text: str) -> ChangePreview:
        current = self._read_raw()
        addition = text if text.startswith("\n") or current.endswith("\n") or not current else "\n" + text

        def _apply():
            self._write_raw(current + addition)

        return ChangePreview(
            description="append_text",
            source=self.file_path,
            location="eof",
            old_value=None,
            new_value=text,
            _apply_fn=_apply,
        )

    def write_all(self, text: str) -> ChangePreview:
        old = self._read_raw()

        def _apply():
            self._write_raw(text)

        return ChangePreview(
            description="write_all",
            source=self.file_path,
            location="file",
            old_value=old[:200] + ("…" if len(old) > 200 else ""),
            new_value=text[:200] + ("…" if len(text) > 200 else ""),
            _apply_fn=_apply,
        )
