"""Unit tests for overlay.window_utils path heuristics (no X11 required)."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from overlay.window_utils import (
    ActiveWindowInfo,
    classify_window,
    friendly_window_name,
    guess_file_path_for_window,
    is_document_window,
)


def _touch(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"")
    return path


def test_guess_from_viewer_title():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = _touch(os.path.join(tmp, "report.pdf"))
        info = ActiveWindowInfo(
            window_id="1",
            title=f"{pdf} - Document Viewer",
            window_class="Evince",
        )
        assert guess_file_path_for_window(info) == os.path.abspath(pdf)


def test_guess_from_libreoffice_em_dash_title():
    with tempfile.TemporaryDirectory() as tmp:
        docx = _touch(os.path.join(tmp, "notes.docx"))
        info = ActiveWindowInfo(
            window_id="2",
            title=f"{os.path.basename(docx)} — LibreOffice Writer",
            window_class="libreoffice-writer",
        )
        # Basename-only: resolve via cwd by chdir into tmp
        old = os.getcwd()
        try:
            os.chdir(tmp)
            assert guess_file_path_for_window(info) == os.path.abspath(docx)
        finally:
            os.chdir(old)


def test_guess_excel_calc_title():
    with tempfile.TemporaryDirectory() as tmp:
        xlsx = _touch(os.path.join(tmp, "ledger.xlsx"))
        info = ActiveWindowInfo(
            window_id="3",
            title=f"{os.path.basename(xlsx)} — LibreOffice Calc",
            window_class="libreoffice-calc",
        )
        old = os.getcwd()
        try:
            os.chdir(tmp)
            assert guess_file_path_for_window(info) == os.path.abspath(xlsx)
        finally:
            os.chdir(old)


def test_guess_excel_dirty_marker():
    with tempfile.TemporaryDirectory() as tmp:
        xlsx = _touch(os.path.join(tmp, "budget.xlsx"))
        info = ActiveWindowInfo(
            window_id="3b",
            title=f"{os.path.basename(xlsx)} * - Excel",
            window_class="excel",
        )
        old = os.getcwd()
        try:
            os.chdir(tmp)
            assert guess_file_path_for_window(info) == os.path.abspath(xlsx)
        finally:
            os.chdir(old)


def test_guess_from_bracketed_name():
    with tempfile.TemporaryDirectory() as tmp:
        xlsx = _touch(os.path.join(tmp, "ledger.xlsx"))
        info = ActiveWindowInfo(
            window_id="3",
            title=f"[{os.path.basename(xlsx)}] - Spreadsheet",
            window_class="soffice",
        )
        old = os.getcwd()
        try:
            os.chdir(tmp)
            assert guess_file_path_for_window(info) == os.path.abspath(xlsx)
        finally:
            os.chdir(old)


def test_is_document_window_excel():
    info = ActiveWindowInfo("9", "ledger.xlsx — LibreOffice Calc", "libreoffice-calc")
    assert is_document_window(info) is True
    assert is_document_window(ActiveWindowInfo("10", "Terminal", "gnome-terminal")) is False


def test_classify_window_kinds():
    assert classify_window(
        ActiveWindowInfo("1", "ledger.xlsx — LibreOffice Calc", "libreoffice-calc")
    ) == "file"
    assert classify_window(
        ActiveWindowInfo("2", "Example Domain - Google Chrome", "google-chrome")
    ) == "browser"
    assert classify_window(
        ActiveWindowInfo("3", "user@host: ~", "gnome-terminal-server")
    ) == "terminal"
    assert classify_window(
        ActiveWindowInfo("4", "Merge_AI - Cursor", "cursor.cursor")
    ) == "vision"
    assert classify_window(
        ActiveWindowInfo("5", "Merge AI", "main.py.Main.py")
    ) == "self"


def test_friendly_window_name():
    assert (
        friendly_window_name(
            ActiveWindowInfo("1", "Merge_AI - Cursor", "cursor.cursor")
        )
        == "Cursor window"
    )
    assert (
        friendly_window_name(
            ActiveWindowInfo("2", "Example - Google Chrome", "google-chrome")
        )
        == "Chrome"
    )
    assert (
        friendly_window_name(
            ActiveWindowInfo("3", "Docs - Microsoft Edge", "microsoft-edge")
        )
        == "Edge"
    )
    assert (
        friendly_window_name(
            ActiveWindowInfo("4", "user@host: ~", "gnome-terminal-server")
        )
        == "Terminal"
    )


def test_guess_excel_under_desktop_subdir():
    home = os.path.expanduser("~")
    desktop = os.path.join(home, "Desktop")
    if not os.path.isdir(desktop):
        print("skip desktop subdir test — no Desktop")
        return
    try:
        probe = tempfile.mkdtemp(dir=desktop)
        os.rmdir(probe)
    except OSError:
        print("skip desktop subdir test — Desktop not writable")
        return
    with tempfile.TemporaryDirectory(dir=desktop) as tmp:
        xlsx = _touch(os.path.join(tmp, "audit_probe.xlsx"))
        info = ActiveWindowInfo(
            "8",
            "audit_probe.xlsx — LibreOffice Calc",
            "libreoffice.libreoffice-calc",
        )
        assert guess_file_path_for_window(info) == os.path.abspath(xlsx)


def test_guess_returns_none_when_missing():
    info = ActiveWindowInfo(
        window_id="4",
        title="Untitled - Text Editor",
        window_class="gedit",
    )
    assert guess_file_path_for_window(info) is None


def test_guess_ignores_unsupported_extension():
    # .txt is supported (TextAdapter); use a truly unknown extension
    with tempfile.TemporaryDirectory() as tmp:
        weird = _touch(os.path.join(tmp, "notes.xyz"))
        info = ActiveWindowInfo(
            window_id="5",
            title=f"{weird} - Text Editor",
            window_class="gedit",
        )
        assert guess_file_path_for_window(info) is None


if __name__ == "__main__":
    test_guess_from_viewer_title()
    test_guess_from_libreoffice_em_dash_title()
    test_guess_excel_calc_title()
    test_guess_excel_dirty_marker()
    test_guess_from_bracketed_name()
    test_is_document_window_excel()
    test_classify_window_kinds()
    test_guess_excel_under_desktop_subdir()
    test_guess_returns_none_when_missing()
    test_guess_ignores_unsupported_extension()
    print("All window_utils tests passed.")
