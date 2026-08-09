"""Query translation agent for multilingual literature acquisition.

Translates a user query into 6 language-optimized search queries using a
single LLM call.  Each translated query is tailored for the target language's
medical literature conventions (e.g., using local gene/disease nomenclature).

Usage::

    from .query_translator import translate_query, TranslatedQueries

    result = await translate_query("Rett syndrome MECP2 mutation case report")
    print(result.zh)  # Chinese-optimized query
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from loguru import logger
from openai import AsyncOpenAI

from .config import get_config
from .utils.llm_params import resolve_max_tokens

# ── Contracts ──────────────────────────────────────────────────────────────

TARGET_LANGUAGES = ("en", "zh", "ja", "de", "fr", "ru")


@dataclass(frozen=True)
class TranslatedQueries:
    """Language-specific search queries derived from a single source query."""

    en: str
    zh: str
    ja: str
    de: str
    fr: str
    ru: str
    source_query: str

    def as_dict(self) -> dict[str, str]:
        """Return language→query mapping for iteration."""
        return {lang: getattr(self, lang) for lang in TARGET_LANGUAGES}


# ── Prompt ─────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a medical literature search query translator specializing in genetics \
and genomics.

Given a user query (any language), produce search queries optimized for \
finding biomedical literature — especially case reports, sequencing studies, \
and functional studies involving genetic variants — in each of the following \
languages.

Rules:
1. Each query must be in the TARGET language (except "en" which stays English).
2. Use domain-specific medical/genetics terminology appropriate for that \
language's literature databases.
3. Preserve the core meaning: disease name, gene symbols (use standard \
HGNC/HUGO names), variant information, and document type intent.
4. Gene symbols (e.g., MECP2, BRCA1, CFTR) and variant nomenclature \
(e.g., c.470C>T, p.Thr158Met) are universal — keep them as-is.
5. Keep each query concise (5–15 words). It will be used as a search string.
6. If the source query is already in one of the target languages, still \
produce optimized versions for all languages (don't just copy the source).

Return strict JSON only (no markdown fences):
{
  "en": "...",
  "zh": "...",
  "ja": "...",
  "de": "...",
  "fr": "...",
  "ru": "...",
}"""

_USER_TEMPLATE = "Translate this biomedical query into 6 languages:\n\n{query}"


# ── Defaults ───────────────────────────────────────────────────────────────

_DEFAULT_MAX_TOKENS = 1024
_DEFAULT_TIMEOUT = 30


# ── Public API ─────────────────────────────────────────────────────────────


async def translate_query(
    query: str,
    *,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> TranslatedQueries:
    """Translate *query* into 6 language-optimized search strings.

    Args:
        query: Source query in any language.
        client: Pre-built OpenAI client (optional, built from config if omitted).
        model: Model override (default: ``cfg.translation.model``).
        base_url: Base URL override (default: ``cfg.translation.base_url``).
        api_key: API key override (default: first key from ``cfg.translation.all_api_keys``).

    Returns:
        TranslatedQueries with one string per target language.

    Raises:
        ValueError: If LLM config is missing or response is unparseable.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")

    cfg = get_config()

    resolved_model = model or (cfg.translation.model or "").strip()
    resolved_base_url = (base_url or cfg.translation.base_url or "").strip().rstrip("/")
    resolved_api_key = api_key or (cfg.translation.all_api_keys[0] if cfg.translation.all_api_keys else "")

    if not resolved_model or not resolved_base_url:
        raise ValueError("LLM model and base_url are required for query translation")

    own_client = client is None
    if own_client:
        client = AsyncOpenAI(base_url=resolved_base_url, api_key=resolved_api_key)

    try:
        max_tokens = resolve_max_tokens(cfg.translation.max_tokens, percentage=0.25)
        resp = await client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _USER_TEMPLATE.format(query=query.strip())},
            ],
            temperature=0.2,
            max_tokens=min(max_tokens, _DEFAULT_MAX_TOKENS),
        )
        raw = (resp.choices[0].message.content or "").strip()
        return _parse_response(raw, query.strip())
    except Exception:
        logger.exception("query translation failed")
        raise
    finally:
        if own_client:
            await client.close()


def _parse_response(raw: str, source_query: str) -> TranslatedQueries:
    """Parse LLM JSON response into TranslatedQueries."""
    text = raw.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse translation response as JSON: {exc}") from exc

    # Validate all languages present
    missing = [lang for lang in TARGET_LANGUAGES if lang not in obj]
    if missing:
        raise ValueError(f"Translation response missing languages: {missing}")

    # Ensure all values are strings and stripped (handle JSON null → fallback)
    cleaned = {lang: str(obj[lang]).strip() if obj[lang] is not None else "" for lang in TARGET_LANGUAGES}
    empty = [lang for lang, val in cleaned.items() if not val]
    if empty:
        logger.warning("Empty translations for languages: {}", empty)
        # Fall back to source query for empty translations
        for lang in empty:
            cleaned[lang] = source_query

    return TranslatedQueries(
        en=cleaned["en"],
        zh=cleaned["zh"],
        ja=cleaned["ja"],
        de=cleaned["de"],
        fr=cleaned["fr"],
        ru=cleaned["ru"],
        source_query=source_query,
    )
