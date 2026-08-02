import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from app.pipeline.extract import Extractor


def _make_text_pdf(path: Path, pages: list[str]):
    from fpdf import FPDF
    pdf = FPDF()
    for text in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, txt=text)
    pdf.output(str(path))


def test_extract_text_pdf(tmp_path):
    pdf_path = tmp_path / "book.pdf"
    _make_text_pdf(pdf_path, ["Chapter 1: Combat", "Goblins have AC 15."])
    ext = Extractor(gateway=None)
    blocks = ext.extract(pdf_path)
    assert len(blocks) == 2
    assert blocks[0]["page"] == 1
    assert "Chapter 1" in blocks[0]["text"]
    assert blocks[0]["ocr"] is False
    assert blocks[1]["page"] == 2
    assert "AC 15" in blocks[1]["text"]
    assert blocks[1]["ocr"] is False


def test_extract_empty_pages_fallback_to_pypdf(tmp_path):
    pdf_path = tmp_path / "book.pdf"
    _make_text_pdf(pdf_path, ["Hello world"])
    ext = Extractor(gateway=None)
    blocks = ext.extract(pdf_path)
    assert len(blocks) == 1
    assert "Hello" in blocks[0]["text"]