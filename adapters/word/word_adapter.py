"""
Word adapter: CRUD over paragraphs in a .docx file, same confirm-first
pattern as Excel. Tables are exposed read/search only for the MVP; editing
table cells can be added later following write_paragraph as the template.
"""

from __future__ import annotations

import os
from typing import Any

from docx import Document

from adapters.base_adapter import BaseAdapter
from adapters.common.change_preview import ChangePreview


class WordAdapter(BaseAdapter):
    kind = "word"
    can_write = True

    def __init__(self, file_path: str):
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Word file not found: {file_path}")
        super().__init__(os.path.abspath(file_path))
        self.file_path = self.source
        self._doc = Document(self.file_path)

    # ---------- BaseAdapter contract ----------

    def read_all(self, max_chars: int = 20000) -> str:
        full = "\n".join(p.text for p in self._doc.paragraphs)
        for t_idx, table in enumerate(self._doc.tables):
            full += f"\n--- Table {t_idx + 1} ---\n"
            for row in table.rows:
                full += " | ".join(cell.text for cell in row.cells) + "\n"
        if len(full) > max_chars:
            return full[:max_chars] + f"\n... (truncated, {len(full) - max_chars} more chars)"
        return full

    def search(self, value: Any) -> list[int]:
        """Return paragraph indices (0-based) containing the substring."""
        needle = str(value).lower()
        return [i for i, p in enumerate(self._doc.paragraphs) if needle in p.text.lower()]

    def close(self) -> None:
        pass  # python-docx has no open handle to release explicitly

    def reload(self) -> None:
        self._doc = Document(self.file_path)

    # ---------- READ ----------

    def read_paragraph(self, index: int) -> str:
        return self._doc.paragraphs[index].text

    def paragraph_count(self) -> int:
        return len(self._doc.paragraphs)

    # ---------- WRITE (confirm-first) ----------

    def write_paragraph(self, index: int, new_text: str) -> ChangePreview:
        paragraph = self._doc.paragraphs[index]
        old_text = paragraph.text

        def _apply():
            # Replace run text in place to preserve paragraph-level formatting
            # as much as python-docx allows; simplest robust approach is to
            # clear runs and set one run with the new text.
            for run in paragraph.runs:
                run.text = ""
            if paragraph.runs:
                paragraph.runs[0].text = new_text
            else:
                paragraph.add_run(new_text)
            self._doc.save(self.file_path)

        return ChangePreview(
            description="write_paragraph", source=self.file_path,
            location=f"paragraph[{index}]", old_value=old_text, new_value=new_text,
            _apply_fn=_apply,
        )

    def append_paragraph(self, text: str) -> ChangePreview:
        target_index = len(self._doc.paragraphs)

        def _apply():
            self._doc.add_paragraph(text)
            self._doc.save(self.file_path)

        return ChangePreview(
            description="append_paragraph", source=self.file_path,
            location=f"paragraph[{target_index}]", old_value=None, new_value=text,
            _apply_fn=_apply,
        )

    def delete_paragraph(self, index: int) -> ChangePreview:
        if index < 0 or index >= len(self._doc.paragraphs):
            raise IndexError(f"paragraph[{index}] out of range")
        paragraph = self._doc.paragraphs[index]
        old_text = paragraph.text

        def _apply():
            element = paragraph._element
            element.getparent().remove(element)
            self._doc.save(self.file_path)

        return ChangePreview(
            description="delete_paragraph",
            source=self.file_path,
            location=f"paragraph[{index}]",
            old_value=old_text,
            new_value=None,
            _apply_fn=_apply,
        )

    def insert_paragraph(self, index: int, text: str) -> ChangePreview:
        """Insert a paragraph before the paragraph at `index` (0-based)."""
        if index < 0 or index > len(self._doc.paragraphs):
            raise IndexError(f"insert index {index} out of range")

        def _apply():
            from docx.oxml import OxmlElement
            from docx.text.paragraph import Paragraph

            if index >= len(self._doc.paragraphs):
                self._doc.add_paragraph(text)
            else:
                ref_para = self._doc.paragraphs[index]
                new_p = OxmlElement("w:p")
                ref_para._element.addprevious(new_p)
                Paragraph(new_p, ref_para._parent).add_run(text)
            self._doc.save(self.file_path)
            self.reload()

        return ChangePreview(
            description="insert_paragraph",
            source=self.file_path,
            location=f"paragraph[{index}]",
            old_value=None,
            new_value=text,
            _apply_fn=_apply,
        )

    def write_table_cell(
        self, table: int, row: int, col: int, value: str
    ) -> ChangePreview:
        """0-based table/row/col indices."""
        if table < 0 or table >= len(self._doc.tables):
            raise IndexError(f"table[{table}] out of range")
        tbl = self._doc.tables[table]
        if row < 0 or row >= len(tbl.rows):
            raise IndexError(f"table[{table}] row {row} out of range")
        if col < 0 or col >= len(tbl.rows[row].cells):
            raise IndexError(f"table[{table}] col {col} out of range")
        cell = tbl.cell(row, col)
        old = cell.text

        def _apply():
            cell.text = str(value)
            self._doc.save(self.file_path)

        return ChangePreview(
            description="write_table_cell",
            source=self.file_path,
            location=f"table[{table}][{row},{col}]",
            old_value=old,
            new_value=value,
            _apply_fn=_apply,
        )
