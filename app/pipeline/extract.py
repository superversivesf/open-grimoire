# app/pipeline/extract.py
from pathlib import Path
import re


class Extractor:
    def __init__(self, gateway=None):
        self.gateway = gateway

    def extract(self, pdf_path: Path) -> list[dict]:
        pages = self._extract_with_pdfplumber(pdf_path)
        if pages is None:
            pages = self._extract_with_pypdf(pdf_path)
        blocks = []
        for i, text in enumerate(pages, start=1):
            clean = (text or "").strip()
            if len(clean) < 50:
                ocr_text, tier = self._ocr_page(pdf_path, i)
                if ocr_text:
                    blocks.append({"page": i, "text": ocr_text, "ocr": True, "ocr_tier": tier})
                    continue
            blocks.append({"page": i, "text": clean, "ocr": False, "ocr_tier": None})
        return blocks

    def _extract_with_pdfplumber(self, pdf_path: Path) -> list[str] | None:
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                return [(page.extract_text() or "") for page in pdf.pages]
        except Exception:
            return None

    def _extract_with_pypdf(self, pdf_path: Path) -> list[str]:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        return [(page.extract_text() or "") for page in reader.pages]

    def _ocr_page(self, pdf_path: Path, page_num: int) -> tuple[str, int | None]:
        try:
            text = self._tesseract_ocr(pdf_path, page_num)
            if text and not self._is_garbage(text):
                return text, 1
        except Exception:
            pass
        if self.gateway is not None:
            try:
                text = self._vision_ocr(pdf_path, page_num)
                if text:
                    return text, 2
            except Exception:
                pass
        return "", None

    def _tesseract_ocr(self, pdf_path: Path, page_num: int) -> str:
        import pytesseract
        from pdf2image import convert_from_path
        images = convert_from_path(str(pdf_path), first_page=page_num, last_page=page_num, dpi=200)
        return "\n".join(pytesseract.image_to_string(img) for img in images)

    def _vision_ocr(self, pdf_path: Path, page_num: int) -> str:
        raise NotImplementedError("vision OCR not yet implemented")

    @staticmethod
    def _is_garbage(text: str) -> bool:
        if not text:
            return True
        letters = sum(1 for c in text if c.isalpha())
        return letters < len(text) * 0.3