"""PDF search_hits / citation helpers (no Azure)."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reportlab.pdfgen import canvas

from adapters.pdf.pdf_adapter import PdfAdapter, keywords_from_text
from session.agent.nodes import _pdf_citation_chunks
from session.session_manager import Session


def _make_pdf(path, pages_text):
    c = canvas.Canvas(path)
    for text in pages_text:
        c.drawString(72, 700, text)
        c.showPage()
    c.save()


def test_search_hits_and_search_strings():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.pdf")
        _make_pdf(path, ["Revenue was strong this quarter", "Costs remained flat"])
        adapter = PdfAdapter(path)
        hits = adapter.search_hits("Revenue")
        assert hits and hits[0]["page"] == 1
        assert "Revenue" in hits[0]["snippet"]
        pages = adapter.search("Revenue")
        assert isinstance(pages[0], str)
        assert "page 1" in pages[0].lower()
        assert adapter.search_pages("Revenue") == [1]


def test_keywords_and_citation_chunks():
    assert "Revenue" in keywords_from_text("What is the Revenue figure please")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.pdf")
        _make_pdf(path, ["Net assets equal 900 million"])
        session = Session("t")
        session.merge_file(path)
        chunks = _pdf_citation_chunks(session, ["assets 900"])
        assert chunks
        assert "CITATIONS" in chunks[0]
        assert "Page 1" in chunks[0]


if __name__ == "__main__":
    test_search_hits_and_search_strings()
    test_keywords_and_citation_chunks()
    print("PDF citation tests passed.")
