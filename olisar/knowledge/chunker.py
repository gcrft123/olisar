"""Token-aware text chunking for the knowledge base.

Splits on paragraph boundaries into ~512-token chunks with a small overlap so a
fact that straddles a boundary still lands whole in at least one chunk. Uses the
same cheap chars/4 token estimate as the rest of the system (gemini-embedding-001
caps at ~2048 tokens per input, so we stay well under).
"""

from __future__ import annotations

import re

from olisar.memory.writer import estimate_tokens

_PARA_SPLIT = re.compile(r"\n\s*\n")
MIN_CHUNK_CHARS = 30  # drop tiny fragments (stray nav text, single chars, etc.)


def _hard_split(paragraph: str, limit_chars: int) -> list[str]:
    """Break a single oversized paragraph into <=limit pieces."""
    return [paragraph[i : i + limit_chars] for i in range(0, len(paragraph), limit_chars)]


def chunk_document(
    text: str, *, target_tokens: int = 512, overlap_tokens: int = 64
) -> list[str]:
    target = target_tokens * 4  # chars
    overlap = overlap_tokens * 4

    paragraphs: list[str] = []
    for para in _PARA_SPLIT.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) > target:
            paragraphs.extend(_hard_split(para, target))
        else:
            paragraphs.append(para)

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if current and len(current) + len(para) + 2 > target:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{para}" if tail else para
        else:
            current = para if not current else f"{current}\n\n{para}"
    if current.strip():
        chunks.append(current)

    return [c.strip() for c in chunks if len(c.strip()) >= MIN_CHUNK_CHARS]


def estimate_chunk_tokens(chunk: str) -> int:
    return estimate_tokens(chunk)
