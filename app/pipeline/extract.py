# app/pipeline/extract.py
import logging
import multiprocessing
import queue as queue_mod
from pathlib import Path
import re
from typing import Any

log = logging.getLogger("pipeline")

# PDF parsing runs in a spawned subprocess (sandbox): uploaded PDFs are
# untrusted input, and PDF parsers are a historically vulnerability-rich
# area. A crash or hang in the parser must not take down the worker.
_EXTRACT_TIMEOUT_SECONDS = 60


def _extract_pages_sync(pdf_path: Path) -> list[str]:
    """Extract text pages with pdfplumber, falling back to pypdf."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return [(page.extract_text() or "") for page in pdf.pages]
    except ImportError:
        pass
    except Exception as e:
        log.warning(f"pdfplumber extraction failed for {pdf_path.name}: {e}")
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    return [(page.extract_text() or "") for page in reader.pages]


def _extract_worker(pdf_path: str, result_queue: "multiprocessing.Queue") -> None:
    """Child-process entry point: extract pages and ship them back."""
    try:
        result_queue.put(_extract_pages_sync(Path(pdf_path)))
    except Exception as e:  # noqa: BLE001 — must surface any failure to parent
        result_queue.put(e)


class Extractor:
    def __init__(self, gateway: Any = None):
        self.gateway = gateway

    def extract(self, pdf_path: Path) -> list[dict[str, Any]]:
        pages = self._extract_sandboxed(pdf_path)
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

    def _extract_sandboxed(self, pdf_path: Path) -> list[str]:
        """Run PDF text extraction in a spawned subprocess with a timeout.

        Uses the 'spawn' context (not fork) because the worker thread may
        call this from a non-main thread, where forking is unsafe.
        """
        ctx = multiprocessing.get_context("spawn")
        result_queue = ctx.Queue()
        proc = ctx.Process(target=_extract_worker, args=(str(pdf_path), result_queue))
        proc.start()
        try:
            result = result_queue.get(timeout=_EXTRACT_TIMEOUT_SECONDS)
        except queue_mod.Empty:
            proc.terminate()
            proc.join(timeout=5)
            raise ValueError(f"PDF extraction timed out after {_EXTRACT_TIMEOUT_SECONDS}s")
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
        if isinstance(result, Exception):
            raise result
        return result

    def _ocr_page(self, pdf_path: Path, page_num: int) -> tuple[str, int | None]:
        try:
            text = self._tesseract_ocr(pdf_path, page_num)
            if text and not self._is_garbage(text):
                return text, 1
        except Exception:
            pass
        return "", None

    def _tesseract_ocr(self, pdf_path: Path, page_num: int) -> str:
        import pytesseract
        from pdf2image import convert_from_path
        images = convert_from_path(str(pdf_path), first_page=page_num, last_page=page_num, dpi=200)
        return "\n".join(pytesseract.image_to_string(img) for img in images)

    @staticmethod
    def _is_garbage(text: str) -> bool:
        if not text:
            return True
        letters = sum(1 for c in text if c.isalpha())
        return letters < len(text) * 0.3