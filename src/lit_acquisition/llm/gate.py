"""Phase 3: LLM-based relevance gate for downloaded PDFs.

After download, each PDF's content is checked against the original query
topic. Irrelevant files are removed from results and optionally deleted
from disk.

Usage::

    from .gate import run_relevance_gate

    gate_result = await run_relevance_gate(
        query="Rett syndrome MECP2 mutation",
        downloads=download_results,
    )
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

# NOTE: ``fitz`` (PyMuPDF) and ``openai`` are heavy imports (~2s and
# ~10s+ cold). They are imported lazily inside the functions that need
# them so search-only tool calls start up fast.
if TYPE_CHECKING:
    from openai import AsyncOpenAI

from ..config import get_config
from .params import resolve_max_tokens

# ── Prompt templates ──────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a medical literature relevance classifier.

Given a search query/topic and an excerpt from a downloaded PDF, determine \
whether the document is genuinely relevant to the query topic.

A document is RELEVANT if:
- Its primary subject matches the query topic
- It contains substantive discussion, data, or analysis about the topic
- It is a case report, review, or study directly about the topic

A document is NOT RELEVANT if:
- It only mentions the topic in passing or as a comparison
- It is primarily about a different disease/topic
- It is a general reference work, editorial, or organizational document
- The content does not match the query topic at all

Reply with ONLY JSON (no markdown fences):
{"relevant": true/false, "reason": "brief explanation in English"}"""

_SYSTEM_PROMPT_TYPED = """You are a medical literature relevance and document-type classifier.

Given a search query/topic and an excerpt from a downloaded PDF, determine:
1. Whether the document is genuinely relevant to the query topic.
2. What type of document it is.

Document types:
- "case_report": Individual patient case report, case series, clinical observation of one or a few patients.
- "sequencing": Sequencing study (NGS, WGS, WES, gene panel) focused on methodology or cohort analysis.
- "functional": Functional/mechanistic study (in vitro, in vivo, animal models, assays).
- "review": Review article, meta-analysis, systematic review, narrative review, overview.
- "thesis": PhD/master/bachelor thesis, dissertation, graduation project.
- "other": Editorial, letter, guideline, consensus, conference abstract, organizational document, product info.

A document is RELEVANT if its primary subject matches the query topic and it contains \
substantive discussion or data about the topic.

A document is NOT RELEVANT if it only mentions the topic in passing, is primarily about \
a different disease, or is a general reference/editorial/organizational document.

Reply with ONLY JSON (no markdown fences):
{"relevant": true/false, "doc_type": "case_report|sequencing|functional|review|thesis|other", "reason": "brief explanation"}"""

_USER_PROMPT = """Search query/topic: {query}

Document language: {lang}
Document title: {title}
PDF excerpt (first {max_pages} pages):
---
{text}
---

Is this document relevant to the search query?"""

_USER_PROMPT_TYPED = """Search query/topic: {query}

Document language: {lang}
Document title: {title}
PDF excerpt (first {max_pages} pages):
---
{text}
---

Is this document relevant? What is its document type?"""


# ── Config defaults ───────────────────────────────────────────────────────

_DEFAULT_MAX_PAGES = 3
_DEFAULT_MAX_CHARS = 3000
_DEFAULT_CONCURRENCY = 6
_DEFAULT_MAX_TOKENS = 4096


# ── Result types ──────────────────────────────────────────────────────────


@dataclass
class RelevanceJudgment:
    """Result of relevance check for a single PDF."""

    file_path: str
    relevant: bool = False
    doc_type: str = ""
    reason: str = ""
    error: str = ""


@dataclass
class RelevanceGateResult:
    """Aggregated result of the relevance gate."""

    total: int = 0
    relevant: int = 0
    irrelevant: int = 0
    errors: int = 0
    judgments: list[RelevanceJudgment] = field(default_factory=list)
    removed_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────


def _extract_text(
    pdf_path: str,
    max_pages: int = _DEFAULT_MAX_PAGES,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Extract plain text from the first pages of a PDF."""
    import fitz  # lazy: PyMuPDF is a heavy import

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return ""

    chunks: list[str] = []
    try:
        for i in range(min(max_pages, len(doc))):
            txt = doc[i].get_text("text") or ""
            if txt:
                chunks.append(txt)
            if sum(len(c) for c in chunks) >= max_chars:
                break
    finally:
        doc.close()

    return "\n".join(chunks)[:max_chars]


def _parse_response(raw: str) -> tuple[bool, str, str, str]:
    """Parse LLM JSON response into (relevant, doc_type, reason, error).

    ``error`` is non-empty when the response could not be parsed as JSON.
    An unparseable verdict is not an explicit ``relevant: false`` - callers
    must surface it as an error judgment so the file is kept.
    """
    text = raw.strip()
    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
        return (
            bool(obj.get("relevant", False)),
            str(obj.get("doc_type", "")),
            str(obj.get("reason", "")),
            "",
        )
    except json.JSONDecodeError as exc:
        return False, "", f"json_parse_error: {exc}", "json_parse_error"


# ── Core ──────────────────────────────────────────────────────────────────


async def _chat_create_with_rotation(
    clients: list[AsyncOpenAI],
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
) -> Any:
    """Call chat completions, rotating to the next configured key on
    authentication (401/403) or rate-limit (429) errors."""
    from openai import AuthenticationError, PermissionDeniedError, RateLimitError

    last_exc: Exception | None = None
    for idx, client in enumerate(clients):
        try:
            return await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.0,
                max_tokens=max_tokens,
            )
        except (AuthenticationError, PermissionDeniedError, RateLimitError) as exc:
            # Key quota exhausted / rejected: try the next configured key.
            last_exc = exc
            logger.warning(
                "LLM key {}/{} failed ({}), rotating",
                idx + 1,
                len(clients),
                type(exc).__name__,
            )
            continue
    assert last_exc is not None
    raise last_exc


async def _check_one(
    clients: list[AsyncOpenAI],
    sem: asyncio.Semaphore,
    model: str,
    query: str,
    download: dict[str, Any],
    max_pages: int,
    max_chars: int,
    max_tokens: int,
    literature_types: list[str] | None = None,
) -> RelevanceJudgment:
    """Check a single downloaded PDF for relevance to the query.

    When ``download["parsed_markdown"]`` is present (set by the multilingual
    workflow's early MinerU batch parse), it is used directly instead of
    re-extracting text from the PDF with PyMuPDF.
    """
    file_path = download.get("file_path") or ""
    lang = download.get("lang", "")
    title = download.get("title", "")

    parsed_markdown = download.get("parsed_markdown") or ""
    if parsed_markdown:
        text = parsed_markdown[:max_chars]
    else:
        if not file_path or not Path(file_path).exists():
            return RelevanceJudgment(file_path=file_path, error="file_not_found")
        try:
            text = await asyncio.to_thread(_extract_text, file_path, max_pages, max_chars)
        except Exception as exc:
            # A page-level PyMuPDF exception must not crash the whole gate;
            # keep the file by returning an error judgment.
            return RelevanceJudgment(file_path=file_path, error=f"extract_text_failed: {exc}")
        if not text or len(text.strip()) < 30:
            return RelevanceJudgment(file_path=file_path, error="empty_text")

    use_typed = bool(literature_types)
    system_prompt = _SYSTEM_PROMPT_TYPED if use_typed else _SYSTEM_PROMPT
    user_msg_tpl = _USER_PROMPT_TYPED if use_typed else _USER_PROMPT
    user_msg = user_msg_tpl.format(
        query=query,
        lang=lang,
        title=title,
        max_pages=max_pages,
        text=text,
    )

    async with sem:
        try:
            resp = await _chat_create_with_rotation(
                clients,
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=max_tokens,
            )
            raw = (resp.choices[0].message.content or "").strip()
            relevant, doc_type, reason, parse_error = _parse_response(raw)
            if parse_error:
                # Unparseable verdict is not an explicit relevant:false -
                # return an error judgment so the file is kept, not deleted.
                return RelevanceJudgment(
                    file_path=file_path,
                    relevant=False,
                    reason=reason,
                    error=parse_error,
                )

            # If literature_types is set, the LLM MUST emit a known doc_type
            # AND it must match. Missing/unknown doc_type is conservatively
            # rejected — never silently kept.
            if use_typed:
                if not doc_type:
                    return RelevanceJudgment(
                        file_path=file_path,
                        relevant=False,
                        doc_type=doc_type,
                        reason="doc_type_missing: classifier did not emit a doc_type",
                    )
                if doc_type not in literature_types:
                    return RelevanceJudgment(
                        file_path=file_path,
                        relevant=False,
                        doc_type=doc_type,
                        reason=f"doc_type_mismatch: {doc_type} not in {literature_types}",
                    )

            return RelevanceJudgment(
                file_path=file_path,
                relevant=relevant,
                doc_type=doc_type,
                reason=reason,
            )
        except Exception as exc:
            return RelevanceJudgment(
                file_path=file_path,
                error=str(exc)[:200],
            )


async def run_relevance_gate(
    *,
    query: str,
    downloads: list[dict[str, Any]],
    delete_files: bool = True,
    concurrency: int = _DEFAULT_CONCURRENCY,
    max_pages: int = _DEFAULT_MAX_PAGES,
    max_chars: int = _DEFAULT_MAX_CHARS,
    literature_types: list[str] | None = None,
) -> RelevanceGateResult:
    """Run LLM relevance gate on downloaded PDFs.

    Args:
        query: Original search query/topic for relevance comparison.
        downloads: List of download dicts (must have ``file_path``).
        delete_files: If True, delete irrelevant PDFs from disk.
        concurrency: Max parallel LLM calls.
        max_pages: Pages to extract per PDF.
        max_chars: Max chars per PDF excerpt.
        literature_types: If set, also classify document type and reject
            PDFs whose type is not in this list (e.g. ["case_report"]).

    Returns:
        RelevanceGateResult with filtered download list and statistics.
    """
    if not downloads:
        return RelevanceGateResult()

    cfg = get_config()
    model = (cfg.llm.model or "").strip()
    base_url = (cfg.llm.base_url or "").strip().rstrip("/")
    api_keys = cfg.llm.all_api_keys

    if not model or not base_url:
        logger.warning("relevance_gate skipped: missing LLM config")
        return RelevanceGateResult(
            total=len(downloads),
            relevant=len(downloads),
            warnings=["RELEVANCE_GATE_SKIPPED: no llm config"],
        )
    if not api_keys:
        logger.warning("relevance_gate skipped: no LLM API key configured")
        return RelevanceGateResult(
            total=len(downloads),
            relevant=len(downloads),
            warnings=["RELEVANCE_GATE_SKIPPED: no llm api key configured"],
        )

    max_tokens = resolve_max_tokens(cfg.llm.max_tokens, percentage=0.5)
    from openai import AsyncOpenAI  # lazy: heavy import, only needed when gating runs

    result = RelevanceGateResult(total=len(downloads))

    async with contextlib.AsyncExitStack() as stack:
        # One client per configured key so _check_one can rotate on
        # 401/403/429 (key quota / rate limits) without rebuilding clients.
        clients = [
            await stack.enter_async_context(
                AsyncOpenAI(base_url=base_url, api_key=key, timeout=cfg.llm.timeout, max_retries=1)
            )
            for key in api_keys
        ]
        sem = asyncio.Semaphore(concurrency)

        logger.info(f"relevance_gate: checking {len(downloads)} downloads against query: {query[:80]}")
        if literature_types:
            logger.info(f"relevance_gate: document type filter active: {literature_types}")

        tasks = [
            _check_one(clients, sem, model, query, dl, max_pages, max_chars, max_tokens, literature_types)
            for dl in downloads
        ]

        for coro in asyncio.as_completed(tasks):
            judgment = await coro
            result.judgments.append(judgment)

            if judgment.error:
                result.errors += 1
                # Treat errored files as relevant (keep them)
                logger.warning(f"relevance_gate error: {judgment.file_path}: {judgment.error}")
            elif judgment.relevant:
                result.relevant += 1
            else:
                result.irrelevant += 1
                result.removed_paths.append(judgment.file_path)
                logger.info(f"relevance_gate: removing {Path(judgment.file_path).name} - {judgment.reason[:80]}")

    if result.errors:
        result.warnings.append(
            f"RELEVANCE_GATE_ERRORS: {result.errors}/{result.total} items failed and were kept"
        )

    # Delete irrelevant files from disk
    if delete_files and result.removed_paths:
        for fp in result.removed_paths:
            try:
                os.unlink(fp)
                logger.debug(f"relevance_gate: deleted {fp}")
            except FileNotFoundError:
                pass
            except Exception as exc:
                logger.warning(f"relevance_gate: failed to delete {fp}: {exc}")

    logger.info(
        f"relevance_gate: total={result.total} "
        f"relevant={result.relevant} irrelevant={result.irrelevant} errors={result.errors}"
    )

    return result
