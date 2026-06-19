"""Extract plain text from uploaded documents (PDF / DOCX / MD / TXT)."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("olisar.knowledge.extract")

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".md", ".markdown", ".txt", ".text"}


def extract_document(path: str) -> str:
    """Return the document's text, or raise ValueError for unsupported types."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix in {".md", ".markdown", ".txt", ".text"}:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    raise ValueError(f"unsupported document type: {suffix or '(none)'}")


def _extract_pdf(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            log.warning("failed to extract a PDF page in %s", path)
    return "\n\n".join(p for p in pages if p.strip())


def _extract_docx(path: str) -> str:
    import docx

    document = docx.Document(path)
    return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())
