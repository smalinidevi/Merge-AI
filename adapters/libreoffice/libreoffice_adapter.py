"""
LibreOffice live-document adapter via PyUNO.

Edits the document that is *already open* in LibreOffice so changes appear
immediately — no File→Reload and no stale on-disk copy.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from adapters.base_adapter import BaseAdapter
from adapters.common.change_preview import ChangePreview
from adapters.libreoffice import uno_bridge


class LibreOfficeAdapter(BaseAdapter):
    kind = "libreoffice_live"
    can_write = True

    def __init__(self, source: str = "localhost:2002", file_path: Optional[str] = None):
        super().__init__(source)
        self._port = DEFAULT_PORT_FROM_SOURCE(source)
        self.file_path = os.path.abspath(file_path) if file_path else None
        self._ctx = None
        self._desktop = None
        self._document = None

    @classmethod
    def for_open_file(cls, file_path: str, port: int = uno_bridge.DEFAULT_PORT) -> Optional["LibreOfficeAdapter"]:
        """Return a live adapter if LibreOffice already has this file open.

        Does not start soffice — only attaches when a UNO port is already listening.
        """
        if not file_path or not os.path.isfile(file_path):
            return None
        if not uno_bridge.port_is_open(port=port):
            return None
        try:
            ctx, desktop = uno_bridge.connect(port=port)
        except Exception:
            return None
        doc = uno_bridge.find_document_for_path(desktop, file_path)
        if doc is None:
            return None
        adapter = cls(source=f"localhost:{port}", file_path=file_path)
        adapter._ctx = ctx
        adapter._desktop = desktop
        adapter._document = doc
        return adapter

    def _ensure_doc(self):
        if self._document is not None:
            return self._document
        self._ctx, self._desktop = uno_bridge.connect(port=self._port)
        if not self.file_path:
            raise RuntimeError("No file_path bound to LibreOfficeAdapter.")
        doc = uno_bridge.find_document_for_path(self._desktop, self.file_path)
        if doc is None:
            raise RuntimeError(
                f"File is not open in LibreOffice: {self.file_path}"
            )
        self._document = doc
        return doc

    # ---------- BaseAdapter ----------

    def read_all(self, max_rows_per_sheet: int = 80) -> str:
        doc = self._ensure_doc()
        if uno_bridge.is_calc(doc):
            return self._read_calc(doc, max_rows_per_sheet)
        if uno_bridge.is_writer(doc):
            return self._read_writer(doc)
        title = getattr(doc, "Title", self.file_path or "document")
        return f"(open in LibreOffice: {title} — unsupported doc type for read_all)"

    def search(self, value: Any) -> list:
        needle = str(value).lower()
        hits = []
        text = self.read_all()
        for i, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                hits.append(i)
        return hits

    def close(self) -> None:
        self._document = None
        self._desktop = None
        self._ctx = None

    def reload(self) -> None:
        """Re-resolve the live component (still no disk reload)."""
        self._document = None
        self._ensure_doc()

    # ---------- Calc ----------

    def _read_calc(self, doc, max_rows: int) -> str:
        chunks = []
        sheets = doc.getSheets()
        for i in range(sheets.getCount()):
            sheet = sheets.getByIndex(i)
            chunks.append(f"--- Sheet: {sheet.Name} ---")
            cursor = sheet.createCursor()
            cursor.gotoEndOfUsedArea(False)
            end_col = cursor.RangeAddress.EndColumn
            end_row = cursor.RangeAddress.EndRow
            for r in range(0, min(end_row + 1, max_rows)):
                vals = []
                for c in range(0, end_col + 1):
                    cell = sheet.getCellByPosition(c, r)
                    s = cell.getString()
                    if s == "":
                        try:
                            v = cell.getValue()
                            # Empty numeric cells often report 0 — keep blank if formula/string empty
                            s = str(v) if v else ""
                        except Exception:
                            s = ""
                    vals.append(s)
                chunks.append(", ".join(vals))
            if end_row + 1 > max_rows:
                chunks.append(f"... ({end_row + 1 - max_rows} more rows truncated)")
        return "\n".join(chunks)

    def _sheet(self, doc, sheet_name: Optional[str] = None):
        sheets = doc.getSheets()
        if sheet_name:
            if not sheets.hasByName(sheet_name):
                raise KeyError(f"Sheet '{sheet_name}' not open in LibreOffice")
            return sheets.getByName(sheet_name)
        return sheets.getByIndex(0)

    def write_cell(
        self, cell: str, new_value: Any, sheet_name: Optional[str] = None
    ) -> ChangePreview:
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("write_cell requires a Calc spreadsheet open in LibreOffice")
        sheet = self._sheet(doc, sheet_name)
        uno_cell = sheet.getCellRangeByName(cell)
        old = uno_cell.getString()
        if old == "":
            try:
                old = uno_cell.getValue()
            except Exception:
                old = None

        def _apply():
            self._set_cell_value(uno_cell, new_value)

        return ChangePreview(
            description="write_cell (live)",
            source=self.file_path or self.source,
            location=f"{sheet.Name}!{cell}",
            old_value=old,
            new_value=new_value,
            _apply_fn=_apply,
        )

    def append_row(
        self, values: list[Any], sheet_name: Optional[str] = None
    ) -> ChangePreview:
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("append_row requires Calc")
        sheet = self._sheet(doc, sheet_name)
        cursor = sheet.createCursor()
        cursor.gotoEndOfUsedArea(False)
        target_row = cursor.RangeAddress.EndRow + 1  # 0-based next row

        def _apply():
            for c, val in enumerate(values):
                self._set_cell_value(sheet.getCellByPosition(c, target_row), val)

        return ChangePreview(
            description="append_row (live)",
            source=self.file_path or self.source,
            location=f"{sheet.Name}!row {target_row + 1}",
            old_value=None,
            new_value=values,
            _apply_fn=_apply,
        )

    def delete_row(
        self, row_index: int, sheet_name: Optional[str] = None
    ) -> ChangePreview:
        """row_index is 1-based (same as ExcelAdapter)."""
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("delete_row requires Calc")
        sheet = self._sheet(doc, sheet_name)
        if row_index < 1:
            raise ValueError("row_index must be >= 1")
        zero = row_index - 1
        cursor = sheet.createCursor()
        cursor.gotoEndOfUsedArea(False)
        end_col = cursor.RangeAddress.EndColumn
        old_values = [
            sheet.getCellByPosition(c, zero).getString() for c in range(0, end_col + 1)
        ]

        def _apply():
            rows = sheet.getRows()
            rows.removeByIndex(zero, 1)

        return ChangePreview(
            description="delete_row (live)",
            source=self.file_path or self.source,
            location=f"{sheet.Name}!row {row_index}",
            old_value=old_values,
            new_value=None,
            _apply_fn=_apply,
        )

    def insert_row(
        self,
        row_index: int,
        values: Optional[list[Any]] = None,
        sheet_name: Optional[str] = None,
    ) -> ChangePreview:
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("insert_row requires Calc")
        if row_index < 1:
            raise ValueError("row_index must be >= 1")
        sheet = self._sheet(doc, sheet_name)
        zero = row_index - 1

        def _apply():
            sheet.getRows().insertByIndex(zero, 1)
            if values:
                for c, val in enumerate(values):
                    self._set_cell_value(sheet.getCellByPosition(c, zero), val)

        return ChangePreview(
            description="insert_row (live)",
            source=self.file_path or self.source,
            location=f"{sheet.Name}!row {row_index}",
            old_value=None,
            new_value=values,
            _apply_fn=_apply,
        )

    def insert_column(
        self, column: Any, sheet_name: Optional[str] = None
    ) -> ChangePreview:
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("insert_column requires Calc")
        sheet = self._sheet(doc, sheet_name)
        idx = self._col_index(column)  # 0-based for UNO
        letter = self._col_letter(idx + 1)

        def _apply():
            sheet.getColumns().insertByIndex(idx, 1)

        return ChangePreview(
            description="insert_column (live)",
            source=self.file_path or self.source,
            location=f"{sheet.Name}!col {letter}",
            old_value=None,
            new_value=letter,
            _apply_fn=_apply,
        )

    def delete_column(
        self, column: Any, sheet_name: Optional[str] = None
    ) -> ChangePreview:
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("delete_column requires Calc")
        sheet = self._sheet(doc, sheet_name)
        idx = self._col_index(column)
        letter = self._col_letter(idx + 1)

        def _apply():
            sheet.getColumns().removeByIndex(idx, 1)

        return ChangePreview(
            description="delete_column (live)",
            source=self.file_path or self.source,
            location=f"{sheet.Name}!col {letter}",
            old_value=letter,
            new_value=None,
            _apply_fn=_apply,
        )

    def clear_range(
        self, cell_range: str, sheet_name: Optional[str] = None
    ) -> ChangePreview:
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("clear_range requires Calc")
        sheet = self._sheet(doc, sheet_name)
        rng = sheet.getCellRangeByName(cell_range)

        def _apply():
            rng.clearContents(1 | 2 | 4 | 8 | 16 | 32 | 64 | 128 | 256 | 512)

        return ChangePreview(
            description="clear_range (live)",
            source=self.file_path or self.source,
            location=f"{sheet.Name}!{cell_range}",
            old_value=cell_range,
            new_value=None,
            _apply_fn=_apply,
        )

    def write_range(
        self,
        start_cell: str,
        values: list[list[Any]],
        sheet_name: Optional[str] = None,
    ) -> ChangePreview:
        if not values or not isinstance(values, list):
            raise ValueError("write_range needs a 2D values list")
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("write_range requires Calc")
        sheet = self._sheet(doc, sheet_name)
        anchor = sheet.getCellRangeByName(start_cell)
        addr = anchor.getCellAddress()
        r0, c0 = addr.Row, addr.Column

        def _apply():
            for ri, row_vals in enumerate(values):
                if not isinstance(row_vals, (list, tuple)):
                    row_vals = [row_vals]
                for ci, val in enumerate(row_vals):
                    self._set_cell_value(
                        sheet.getCellByPosition(c0 + ci, r0 + ri), val
                    )

        return ChangePreview(
            description="write_range (live)",
            source=self.file_path or self.source,
            location=f"{sheet.Name}!{start_cell}",
            old_value=None,
            new_value=values,
            _apply_fn=_apply,
        )

    def create_sheet(
        self, name: str, index: Optional[int] = None
    ) -> ChangePreview:
        name = (name or "").strip()
        if not name:
            raise ValueError("sheet name is required")
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("create_sheet requires Calc")
        sheets = doc.getSheets()
        if sheets.hasByName(name):
            raise ValueError(f"Sheet already exists: {name}")
        pos = int(index) if index is not None else sheets.getCount()

        def _apply():
            # Document may have been mutated; re-fetch sheets
            s = self._ensure_doc().getSheets()
            insert_at = min(max(pos, 0), s.getCount())
            s.insertNewByName(name, insert_at)

        return ChangePreview(
            description="create_sheet (live)",
            source=self.file_path or self.source,
            location=name,
            old_value=None,
            new_value=name,
            _apply_fn=_apply,
        )

    def rename_sheet(self, old_name: str, new_name: str) -> ChangePreview:
        old_name = (old_name or "").strip()
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("new sheet name is required")
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("rename_sheet requires Calc")
        sheets = doc.getSheets()
        if not sheets.hasByName(old_name):
            raise KeyError(f"Sheet '{old_name}' not found")
        if sheets.hasByName(new_name) and new_name != old_name:
            raise ValueError(f"Sheet already exists: {new_name}")

        def _apply():
            self._ensure_doc().getSheets().getByName(old_name).setName(new_name)

        return ChangePreview(
            description="rename_sheet (live)",
            source=self.file_path or self.source,
            location=old_name,
            old_value=old_name,
            new_value=new_name,
            _apply_fn=_apply,
        )

    def delete_sheet(self, name: str) -> ChangePreview:
        name = (name or "").strip()
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("delete_sheet requires Calc")
        sheets = doc.getSheets()
        if not sheets.hasByName(name):
            raise KeyError(f"Sheet '{name}' not found")
        if sheets.getCount() <= 1:
            raise ValueError("Cannot delete the only sheet in the workbook")

        def _apply():
            self._ensure_doc().getSheets().removeByName(name)

        return ChangePreview(
            description="delete_sheet (live)",
            source=self.file_path or self.source,
            location=name,
            old_value=name,
            new_value=None,
            _apply_fn=_apply,
        )

    def copy_sheet(self, name: str, new_name: str) -> ChangePreview:
        name = (name or "").strip()
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("new sheet name is required")
        doc = self._ensure_doc()
        if not uno_bridge.is_calc(doc):
            raise RuntimeError("copy_sheet requires Calc")
        sheets = doc.getSheets()
        if not sheets.hasByName(name):
            raise KeyError(f"Sheet '{name}' not found")
        if sheets.hasByName(new_name):
            raise ValueError(f"Sheet already exists: {new_name}")

        def _apply():
            s = self._ensure_doc().getSheets()
            # copyByName(src, dest, insert_position)
            s.copyByName(name, new_name, s.getCount())

        return ChangePreview(
            description="copy_sheet (live)",
            source=self.file_path or self.source,
            location=f"{name} → {new_name}",
            old_value=name,
            new_value=new_name,
            _apply_fn=_apply,
        )

    @staticmethod
    def _col_index(column: Any) -> int:
        """0-based column index for UNO."""
        if isinstance(column, int):
            if column < 1:
                raise ValueError("column index must be >= 1")
            return column - 1
        text = str(column).strip().upper()
        if text.isdigit():
            idx = int(text)
            if idx < 1:
                raise ValueError("column index must be >= 1")
            return idx - 1
        n = 0
        for ch in text:
            if not ("A" <= ch <= "Z"):
                raise ValueError(f"Invalid column: {column}")
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return n - 1

    @staticmethod
    def _col_letter(index_1based: int) -> str:
        n = index_1based
        letters = ""
        while n:
            n, rem = divmod(n - 1, 26)
            letters = chr(65 + rem) + letters
        return letters or "A"

    @staticmethod
    def _set_cell_value(cell, value: Any) -> None:
        if value is None:
            cell.setString("")
        elif isinstance(value, bool):
            cell.setValue(1.0 if value else 0.0)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            cell.setValue(float(value))
        else:
            # numeric strings → number when possible
            text = str(value)
            try:
                if text.strip() and all(ch in "0123456789.-+" for ch in text.strip()):
                    cell.setValue(float(text))
                    return
            except ValueError:
                pass
            cell.setString(text)

    # ---------- Writer ----------

    def _read_writer(self, doc, max_chars: int = 20000) -> str:
        text = doc.getText().getString()
        if len(text) > max_chars:
            return text[:max_chars] + f"\n... (truncated, {len(text) - max_chars} more chars)"
        return text

    def _paragraphs(self, doc) -> list:
        paras = []
        enum = doc.getText().createEnumeration()
        while enum.hasMoreElements():
            el = enum.nextElement()
            try:
                if el.supportsService("com.sun.star.text.Paragraph"):
                    paras.append(el)
            except Exception:
                continue
        return paras

    def write_paragraph(self, index: int, new_text: str) -> ChangePreview:
        doc = self._ensure_doc()
        if not uno_bridge.is_writer(doc):
            raise RuntimeError("write_paragraph requires Writer")
        paras = self._paragraphs(doc)
        if index < 0 or index >= len(paras):
            raise IndexError(f"paragraph[{index}] out of range (0..{len(paras)-1})")
        para = paras[index]
        old = para.getString()

        def _apply():
            para.setString(new_text)

        return ChangePreview(
            description="write_paragraph (live)",
            source=self.file_path or self.source,
            location=f"paragraph[{index}]",
            old_value=old,
            new_value=new_text,
            _apply_fn=_apply,
        )

    def append_paragraph(self, text: str) -> ChangePreview:
        doc = self._ensure_doc()
        if not uno_bridge.is_writer(doc):
            raise RuntimeError("append_paragraph requires Writer")
        target_index = len(self._paragraphs(doc))

        def _apply():
            cursor = doc.getText().createTextCursor()
            cursor.gotoEnd(False)
            doc.getText().insertString(cursor, "\n" + text, False)

        return ChangePreview(
            description="append_paragraph (live)",
            source=self.file_path or self.source,
            location=f"paragraph[{target_index}]",
            old_value=None,
            new_value=text,
            _apply_fn=_apply,
        )

    def delete_paragraph(self, index: int) -> ChangePreview:
        doc = self._ensure_doc()
        if not uno_bridge.is_writer(doc):
            raise RuntimeError("delete_paragraph requires Writer")
        paras = self._paragraphs(doc)
        if index < 0 or index >= len(paras):
            raise IndexError(f"paragraph[{index}] out of range")
        para = paras[index]
        old = para.getString()

        def _apply():
            # Replace with empty — full structural delete is fragile across LO versions
            para.setString("")

        return ChangePreview(
            description="delete_paragraph (live)",
            source=self.file_path or self.source,
            location=f"paragraph[{index}]",
            old_value=old,
            new_value="",
            _apply_fn=_apply,
        )

    def insert_paragraph(self, index: int, text: str) -> ChangePreview:
        doc = self._ensure_doc()
        if not uno_bridge.is_writer(doc):
            raise RuntimeError("insert_paragraph requires Writer")
        paras = self._paragraphs(doc)
        if index < 0 or index > len(paras):
            raise IndexError(f"insert index {index} out of range")

        def _apply():
            text_obj = doc.getText()
            cursor = text_obj.createTextCursor()
            if index >= len(self._paragraphs(doc)):
                cursor.gotoEnd(False)
                text_obj.insertString(cursor, "\n" + text, False)
            else:
                # Insert before target paragraph
                target = self._paragraphs(doc)[index]
                cursor.gotoRange(target.getStart(), False)
                text_obj.insertString(cursor, text + "\n", False)

        return ChangePreview(
            description="insert_paragraph (live)",
            source=self.file_path or self.source,
            location=f"paragraph[{index}]",
            old_value=None,
            new_value=text,
            _apply_fn=_apply,
        )

    def write_table_cell(
        self, table: int, row: int, col: int, value: str
    ) -> ChangePreview:
        """0-based table/row/col for Writer tables (best-effort via UNO)."""
        doc = self._ensure_doc()
        if not uno_bridge.is_writer(doc):
            raise RuntimeError("write_table_cell requires Writer")
        tables = doc.getTextTables()
        if table < 0 or table >= tables.getCount():
            raise IndexError(f"table[{table}] out of range")
        tbl = tables.getByIndex(table)
        # UNO tables are often 1-based named cells A1-style; use getCellByPosition
        try:
            cell = tbl.getCellByPosition(int(col), int(row))
        except Exception as e:
            raise IndexError(f"table[{table}] cell ({row},{col}): {e}") from e
        old = cell.getString()

        def _apply():
            cell.setString(str(value))

        return ChangePreview(
            description="write_table_cell (live)",
            source=self.file_path or self.source,
            location=f"table[{table}][{row},{col}]",
            old_value=old,
            new_value=value,
            _apply_fn=_apply,
        )


def DEFAULT_PORT_FROM_SOURCE(source: str) -> int:
    if ":" in (source or ""):
        try:
            return int(source.rsplit(":", 1)[-1])
        except ValueError:
            pass
    return uno_bridge.DEFAULT_PORT
