import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docx import Document
from pypdf import PdfWriter
from reportlab.pdfgen import canvas  # only used to build a test fixture PDF

from adapters.pdf.pdf_adapter import PdfAdapter
from adapters.word.word_adapter import WordAdapter
from adapters.terminal.terminal_adapter import TerminalAdapter
from adapters.registry import get_adapter_for_path, supported_extensions
from session.session_manager import SessionManager


def make_sample_pdf(path, pages_text):
    c = canvas.Canvas(path)
    for text in pages_text:
        c.drawString(72, 700, text)
        c.showPage()
    c.save()


def make_sample_docx(path, paragraphs):
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(path)


def test_pdf_read_and_search():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.pdf")
        make_sample_pdf(path, ["Revenue was strong this quarter", "Costs remained flat"])
        adapter = PdfAdapter(path)
        assert adapter.page_count == 2
        pages = adapter.search("Revenue")
        assert pages and "page 1" in pages[0].lower()
        assert adapter.search_pages("Revenue") == [1]


def test_word_read_write_search():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.docx")
        make_sample_docx(path, ["Intro paragraph", "Revenue grew 10%"])
        adapter = WordAdapter(path)
        assert adapter.search("Revenue") == [1]

        preview = adapter.write_paragraph(1, "Revenue grew 12%")
        assert adapter.read_paragraph(1) == "Revenue grew 10%"  # not applied yet
        preview.apply()

        adapter2 = WordAdapter(path)
        assert adapter2.read_paragraph(1) == "Revenue grew 12%"


def test_terminal_confirm_first():
    adapter = TerminalAdapter()
    preview = adapter.run_command("echo hello-merge-ai")
    assert "(no commands run yet" in adapter.read_all()  # not applied yet
    result = preview.apply()
    assert "hello-merge-ai" in result["stdout"]
    assert "hello-merge-ai" in adapter.read_all()


def test_registry_picks_correct_adapter():
    assert set(supported_extensions()) >= {".xlsx", ".pdf", ".docx"}
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.docx")
        make_sample_docx(path, ["hello"])
        adapter = get_adapter_for_path(path)
        assert adapter.kind == "word"


def test_session_mixed_adapter_types():
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = os.path.join(tmp, "a.pdf")
        docx_path = os.path.join(tmp, "b.docx")
        make_sample_pdf(pdf_path, ["Total assets 500"])
        make_sample_docx(docx_path, ["Total assets 500 confirmed"])

        manager = SessionManager()
        session = manager.new_session()
        session.merge_file(pdf_path)
        session.merge_file(docx_path)

        banner = session.banner_text()
        assert "a.pdf" in banner and "b.docx" in banner

        results = session.search_all("500")
        assert "a.pdf" in results and "b.docx" in results


if __name__ == "__main__":
    test_pdf_read_and_search()
    test_word_read_write_search()
    test_terminal_confirm_first()
    test_registry_picks_correct_adapter()
    test_session_mixed_adapter_types()
    print("All adapter tests passed.")
