"""
Central factory so session_manager (and later the overlay's "Merge" button)
never has to know which adapter class handles which file type — add a new
adapter here once, and it's available everywhere.
"""

from __future__ import annotations

import os

from adapters.base_adapter import BaseAdapter
from adapters.excel.excel_adapter import ExcelAdapter
from adapters.pdf.pdf_adapter import PdfAdapter
from adapters.word.word_adapter import WordAdapter
from adapters.text.text_adapter import TextAdapter
from adapters.ppt.ppt_adapter import PptAdapter
from adapters.terminal.terminal_adapter import TerminalAdapter
from adapters.vision.vision_adapter import VisionAdapter
from adapters.libreoffice.libreoffice_adapter import LibreOfficeAdapter
from adapters.browser.browser_adapter import BrowserAdapter

# File-extension -> adapter class, for adapters that operate on files on disk.
_EXTENSION_MAP = {
    ".xlsx": ExcelAdapter,
    ".xlsm": ExcelAdapter,
    ".pdf": PdfAdapter,
    ".docx": WordAdapter,
    ".pptx": PptAdapter,
    ".txt": TextAdapter,
    ".md": TextAdapter,
    ".csv": TextAdapter,
    ".log": TextAdapter,
}

# Logical-kind -> adapter class, for adapters with no file on disk.
_KIND_MAP = {
    "terminal": TerminalAdapter,
    "vision": VisionAdapter,
    "libreoffice_live": LibreOfficeAdapter,
    "browser": BrowserAdapter,
}


def get_adapter_for_path(file_path: str) -> BaseAdapter:
    """Pick the adapter class by file extension and construct it.
    Raises ValueError for unsupported extensions."""
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    adapter_cls = _EXTENSION_MAP.get(ext)
    if adapter_cls is None:
        supported = ", ".join(sorted(_EXTENSION_MAP))
        raise ValueError(f"No adapter for extension '{ext}'. Supported: {supported}")
    return adapter_cls(file_path)


def get_adapter_for_kind(kind: str, source: str | None = None) -> BaseAdapter:
    """Construct a non-file adapter (terminal, vision, browser, libreoffice_live)."""
    adapter_cls = _KIND_MAP.get(kind)
    if adapter_cls is None:
        supported = ", ".join(sorted(_KIND_MAP))
        raise ValueError(f"No adapter for kind '{kind}'. Supported: {supported}")
    return adapter_cls(source) if source else adapter_cls()


def supported_extensions() -> list[str]:
    return sorted(_EXTENSION_MAP)


def supported_kinds() -> list[str]:
    return sorted(_KIND_MAP)
