"""Shared utilities for Cell #1 pipeline scripts.

Imported by day3-hunt.py and day4-finalize.py via sys.path injection:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _lib import extract_keywords, extract_keyword_set_ci
"""
from __future__ import annotations
import re

STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or",
    "is", "are", "was", "were", "be", "been", "with", "when", "where",
    "from", "by", "as", "but", "not", "no", "if", "then", "this", "that",
    "it", "its", "than", "into", "out",
})


def extract_keywords(text: str, limit: int = 12) -> list[str]:
    """Heuristic keyword extraction.

    Dedup by case-insensitive equality; output preserves original case so that
    downstream "ALL-CAPS short token" checks (e.g. quoting NPE/DST/JSON for
    exact-match search) work correctly.

    Skips stopwords and tokens shorter than 4 chars unless ALL-CAPS.
    """
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]*", text or "")
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t.lower() in STOPWORDS:
            continue
        if len(t) < 4 and not t.isupper():
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def extract_keyword_set_ci(text: str, limit: int = 12) -> set[str]:
    """Case-insensitive keyword set, for fuzzy overlap matching (set ops)."""
    return {k.lower() for k in extract_keywords(text, limit)}
