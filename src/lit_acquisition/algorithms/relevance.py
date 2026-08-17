"""Lexical relevance scoring (token-overlap, CJK-bigram aware)."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")


def relevance_tokens(text: str) -> list[str]:
    """Tokenize *text* for relevance scoring.

    ASCII/Latin words become casefolded word tokens; CJK runs are split into
    overlapping bigrams so unsegmented Chinese/Japanese titles still match
    query terms.
    """
    tokens: list[str] = []
    for tok in _TOKEN_RE.findall((text or "").casefold()):
        if _CJK_RE.search(tok):
            if len(tok) <= 2:
                tokens.append(tok)
            else:
                tokens.extend(tok[i : i + 2] for i in range(len(tok) - 1))
        else:
            tokens.append(tok)
    return tokens


def lexical_relevance(query: str, text: str) -> float:
    """Fraction of query tokens found in *text* (0.0-1.0).

    A dependency-free relevance signal so results come back ordered by topical
    match even when the neural rerank endpoint is not enabled.
    """
    query_tokens = set(relevance_tokens(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(relevance_tokens(text))
    hits = sum(1 for token in query_tokens if token in text_tokens)
    return hits / len(query_tokens)
