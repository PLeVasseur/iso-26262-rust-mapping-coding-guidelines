from __future__ import annotations

import re

TOKEN_RE = re.compile(r"[a-z0-9_]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "for",
    "how",
    "in",
    "is",
    "it",
    "kinds",
    "of",
    "on",
    "or",
    "rust",
    "that",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "available",
    "features",
    "feature",
    "language",
    "programming",
    "support",
    "supports",
    "techniques",
    "with",
    "why",
}


def tokenize(text: str) -> set[str]:
    tokens = [
        token for token in TOKEN_RE.findall(str(text).lower()) if token and token not in STOPWORDS
    ]
    return set(tokens)


def tokenize_raw(text: str) -> set[str]:
    tokens = [token for token in TOKEN_RE.findall(str(text).lower()) if token]
    return set(tokens)


def split_csv_field(raw: str) -> list[str]:
    values = [value.strip() for value in str(raw).split(",") if value.strip()]
    return sorted(set(values))
