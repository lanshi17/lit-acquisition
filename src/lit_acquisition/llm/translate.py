"""Query translation agent for multilingual literature acquisition.

Translates a user query into 6 language-optimized search queries using a
single LLM call.  Each translated query is tailored for the target language's
medical literature conventions (e.g., using local gene/disease nomenclature).

Usage::

    from .translate import translate_query, TranslatedQueries

    result = await translate_query("Rett syndrome MECP2 mutation case report")
    print(result.zh)  # Chinese-optimized query
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

# ``openai`` is a heavy import (~10s cold); loaded lazily when a client
# must be constructed so search-only startup stays fast.
if TYPE_CHECKING:
    from openai import AsyncOpenAI

from ..config import get_config
from .params import resolve_max_tokens

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
        model: Model override (default: ``cfg.translation.model``, falling
            back to ``cfg.llm.model`` when unset).
        base_url: Base URL override (default: ``cfg.translation.base_url``,
            falling back to ``cfg.llm.base_url`` when unset).
        api_key: API key override (default: first key from
            ``cfg.translation.all_api_keys``, falling back to ``cfg.llm``).

    Returns:
        TranslatedQueries with one string per target language.

    Raises:
        ValueError: If LLM config (model, base_url, or API key) is missing
            or the response is unparseable.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")

    cfg = get_config()

    # TranslationLLMConfig falls back to LLMConfig when fields are empty
    # (same pattern as rerank._endpoint).
    resolved_model = model or (cfg.translation.model or "").strip() or (cfg.llm.model or "").strip()
    resolved_base_url = (
        base_url or cfg.translation.base_url or cfg.llm.base_url or ""
    ).strip().rstrip("/")
    translation_keys = cfg.translation.all_api_keys
    fallback_keys = [cfg.llm.api_key] if cfg.llm.api_key else []
    all_keys = ([api_key] if api_key else []) or list(translation_keys) or fallback_keys
    resolved_api_key = all_keys[0] if all_keys else ""

    if not resolved_model or not resolved_base_url:
        raise ValueError("LLM model and base_url are required for query translation")
    if client is None and not resolved_api_key:
        raise ValueError(
            "API key is required for query translation: set LIT_TRANSLATION_API_KEY "
            "(or LIT_LLM_API_KEY to reuse the main LLM key)"
        )

    own_client = client is None
    if own_client:
        from openai import AsyncOpenAI  # lazy: heavy import

        # One client per configured key for rotation on 401/403/429.
        clients = [
            AsyncOpenAI(
                base_url=resolved_base_url,
                api_key=key,
                timeout=cfg.translation.timeout or _DEFAULT_TIMEOUT,
                max_retries=1,
            )
            for key in all_keys
        ]

    async def _call(active_client):
        return await active_client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _USER_TEMPLATE.format(query=query.strip())},
            ],
            temperature=0.2,
            max_tokens=min(resolve_max_tokens(cfg.translation.max_tokens, percentage=0.25), _DEFAULT_MAX_TOKENS),
        )

    try:
        if not own_client:
            resp = await _call(client)
        else:
            from openai import AuthenticationError, PermissionDeniedError, RateLimitError

            resp = None
            last_exc: Exception | None = None
            for idx, ac in enumerate(clients):
                try:
                    resp = await _call(ac)
                    break
                except (AuthenticationError, PermissionDeniedError, RateLimitError) as exc:
                    last_exc = exc
                    logger.warning("translation LLM key {}/{} failed ({}), rotating", idx + 1, len(clients), type(exc).__name__)
                    continue
            if resp is None:
                assert last_exc is not None
                raise last_exc
        raw = (resp.choices[0].message.content or "").strip()
        return _parse_response(raw, query.strip())
    except Exception:
        logger.exception("query translation failed")
        raise
    finally:
        if own_client:
            for ac in clients:
                await ac.close()


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
