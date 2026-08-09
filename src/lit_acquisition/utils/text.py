"""Text processing utilities - sanitization, JSON cleanup."""

from __future__ import annotations

import re

_MULTI_SPACE_RE = re.compile(r"\s+")


def sanitize_filename(name: str) -> str:
    """Sanitize filename by removing invalid characters.

    Removes Windows-unsafe characters and caps length at 120 chars.
    Returns "paper" if result is empty.
    """
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", str(name or "").strip())
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).strip()
    return (cleaned or "paper")[:120]


def strip_json_fences(content: str) -> str:
    """Strip Markdown code fences from LLM JSON output."""
    if not content:
        return ""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
