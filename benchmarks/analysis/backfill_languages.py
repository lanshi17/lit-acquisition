"""E4a: offline language backfill for reference-set papers with unknown language.

Plan reference: ``paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md`` section 4.1.

305 of the 822 reference-set papers carry ``language: "unknown"`` because their
provider metadata (Crossref/PMC) has no ``language`` field. Spot checks show
that this bucket contains untagged German, French, Portuguese and Chinese
papers, so the multilingual claims of the manuscript rest on an artificially
small non-English denominator (13 German + 11 French papers).

This script recovers language labels **offline** from the paper titles with a
deterministic classifier --- no live retrieval, no reference-set edits:

1. **Unicode script detection** (CJK / Hiragana / Katakana / Hangul / Cyrillic
   / Arabic / Hebrew / Thai / Devanagari). Greek-letter symbols that occur in
   English biomedical titles (``alpha-synuclein``) are *not* treated as Greek
   prose: a language label is only emitted for Greek-alphabet **words**.
2. **Latin-script lexical scoring** for German, French, Spanish, Portuguese,
   Italian and Dutch: weighted function words, domain (clinical/genetics)
   vocabulary, language-distinctive spellings (``Syndrom`` vs ``syndrome``,
   ``gène`` vs ``gene``) and orthographic markers (``ß``, ``ñ``, ``ã/õ``,
   accents). English vocabulary is scored the same way and acts as the
   competing hypothesis; a non-English label is only emitted when it beats
   English by a margin, which keeps false non-English labels out of the gold
   set.
3. **Optional cross-checks** (never used to set the primary label):
   ``--cross-check`` runs ``langdetect`` (seeded) and ``--llm-adjudicate``
   asks the configured LLM endpoint for an independent opinion on the
   review queue.

Thresholds are not hand-picked: the 517 papers that already carry a provider
label (493 en, 13 de, 11 fr) are split deterministically into a calibration
half (odd ``variant_id``) and a held-out half (even), the threshold grid is
selected on the calibration half only, and the artifact reports both halves.
The audit also flags provider-label noise: five Chinese-language articles are
labelled ``en`` by their provider while their titles are entirely CJK.

Everything the classifier cannot resolve stays ``unknown`` and is written to a
human review queue (``results/language_backfill_review.csv``).

Outputs
-------
``benchmarks/results/language_analysis_backfilled.json``
    Decisions per paper, calibration/held-out validation, provider-label noise
    audit, review queue, and the per-method language-recovery table recomputed
    on the backfilled reference set.
``benchmarks/results/language_backfill_review.csv``
    Human review queue (all non-ASCII titles + everything undecided or
    low-confidence).

Usage::

    python benchmarks/analysis/backfill_languages.py [--cross-check] [--llm-adjudicate]
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from analysis.nested import build_nested_full_pipeline  # noqa: E402
from make_paper_data import load_gold  # noqa: E402
from metrics import paper_id  # noqa: E402

RESULTS_DIR = BENCH_DIR / "results"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
OUT_FILE = RESULTS_DIR / "language_analysis_backfilled.json"
REVIEW_FILE = RESULTS_DIR / "language_backfill_review.csv"

METHODS = {
    "pubmed_only": "M1 PubMed only",
    "multisource_no_translation": "M2 multi-source, no translation",
    "multisource_with_translation": "M3 + translation",
    "full_pipeline_nested": "M4n nested full pipeline",
}

# ── 1. Unicode script detection ───────────────────────────────────────────

SCRIPT_RANGES: tuple[tuple[str, str], ...] = (
    ("zh", r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"),
    ("ja", r"\u3040-\u309f\u30a0-\u30ff"),
    ("ko", r"\u1100-\u11ff\uac00-\ud7af"),
    ("ru", r"\u0400-\u04ff"),
    ("ar", r"\u0600-\u06ff\u0750-\u077f"),
    ("he", r"\u0590-\u05ff"),
    ("th", r"\u0e00-\u0e7f"),
    ("hi", r"\u0900-\u097f"),
)
_SCRIPT_RE = {lang: re.compile(f"[{rng}]") for lang, rng in SCRIPT_RANGES}
_GREEK_WORD = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]{3,}")

# A provider label may be "wrong" when the title is written in a non-Latin
# script: the article itself is not English. Such cases are audited, not
# silently counted as classifier errors.
SCRIPT_NOISE_MIN_CHARS = 5


def detect_script(title: str) -> tuple[str | None, str]:
    """Return (language, evidence) for a non-Latin script, else (None, '')."""
    counts = {lang: len(rx.findall(title)) for lang, rx in _SCRIPT_RE.items()}
    # Japanese mixes kana with Han; kana presence wins over the Han bucket.
    if counts["ja"]:
        return "ja", f"{counts['ja']} kana characters"
    if counts["ko"]:
        return "ko", f"{counts['ko']} Hangul characters"
    if counts["zh"]:
        return "zh", f"{counts['zh']} CJK characters"
    for lang in ("ru", "ar", "he", "th", "hi"):
        if counts[lang]:
            return lang, f"{counts[lang]} {lang} characters"
    greek_words = _GREEK_WORD.findall(title)
    if greek_words:
        return "el", f"Greek-alphabet word(s): {', '.join(greek_words[:3])}"
    return None, ""


# ── 2. Latin-script lexical scoring ───────────────────────────────────────

LANGUAGES = ("de", "fr", "es", "pt", "it", "nl")

# Function words (weight 1.0). Shared surface forms ("in", "on") appear in
# several lists on purpose: they support every language they belong to and
# therefore cannot decide alone.
FUNCTION_WORDS: dict[str, str] = {
    "de": (
        "aber alle als also am an auch auf aus bei bis da damit dann das dass dem den denn der des die dies "
        "diese diesem diesen dieser dieses einem einen einer eines ein eine er es für gegen hat haben hier "
        "ihr ihre im ist jede jeder jedes kann können mit nach nicht noch nur ob oder ohne sehr sein seine "
        "sind so über um und uns unter viel vom von vor war was weil wenn werden wie wieder wir wird wo zu "
        "zum zur zwischen sowohl sowie durch beim wurden wurde dessen deren"
    ),
    "fr": (
        "afin ainsi alors aussi autre autres au aux avec ce ces cet cette chez comme dans de des du elle "
        "elles en encore est et étaient était être eux il ils je la le les leur leurs lui mais mes moi mon "
        "ne nos notre nous on ou où par pas plus pour qu que quel quelle quelles quels qui sa sans se ses "
        "si soi son sont sous sur ta te tes toi ton tous tout toute toutes tu un une vos votre vous y deux "
        "trois"
    ),
    "es": (
        "al algo como con cual cuando de del desde donde dos el ella ellas ello ellos en entre era es esta "
        "este esto estos la las lo los mas más muy no nos o para pero por porque que se sin sobre son su "
        "sus también tan tres un una uno unos y ya"
    ),
    "pt": (
        "ao aos algo como com da das de dele dela do dos e ela ele eles em entre era essa esse esta este "
        "eu foi há isso isto já mais mas muito não nos o os ou para pela pelo por porque que se sem são "
        "sobre sua suas também um uma"
    ),
    "it": (
        "al alla alle anche che chi con da dal dalla dei del della delle dello di e è gli i il in la le "
        "lo ma nei nel nella non o per più quale quali se si sono su sul sulla tra un una uno"
    ),
    "nl": (
        "aan al als bij daar dan dat de den der deze dit door een en er het hij hoe hun ik in is je kan "
        "met naar niet nog of om ook op over te tot uit van voor was wat we werd worden wordt ze zijn zo"
    ),
}

# Domain vocabulary (weight 0.6): clinical/genetics words in the language.
DOMAIN_WORDS: dict[str, str] = {
    "de": (
        "befund behandlung bedeutung bericht beziehung brustkrebs diagnostik erkrankung ergebnis ergebnisse "
        "fallbericht familiär kinder kindern klinik klinische klinischen klinischer krankheit mangel "
        "mukoviszidose nachweis patienten patientinnen pränatal risikokalkulation rolle schützt screening "
        "stillen studien therapie tumor untersuchung ursache verlauf wirkung zusammenhang leitlinie "
        "wirksamkeit literatur dystrophie muskeldystrophie"
    ),
    "fr": (
        "analyse atteinte cancer cas clinique conseil déficit dépistage diagnostic donnée données enfant "
        "étude exome facteurs familial famille génétique héréditaire maladie malades maroc mutation "
        "mutations patient patiente patientes phénotypique prédisposition prévalence prise revue sein "
        "syndrome thérapeutique traitement tumeur variants"
    ),
    "es": (
        "análisis asociación cáncer caso clínico clínicos clínicas diagnóstico enfermedad estudio exoma "
        "familial familia genético genéticas hereditario mama mutación mutaciones paciente pacientes riesgo "
        "síndrome tratamiento tumor variante"
    ),
    "pt": (
        "análise associação câncer caso clínico diagnóstico doença estudo familial família genética "
        "hereditário mama mutação mutações paciente pacientes relato risco síndrome tratamento tumor "
        "variante"
    ),
    "it": (
        "analisi associazione cancro caso clinico diagnosi malattia esoma familiare famiglia genetico "
        "ereditario mammella mutazione mutazioni paziente pazienti rischio sindrome trattamento tumore "
        "variante"
    ),
    "nl": (
        "analyse behandeling borstkanker diagnose erfelijk geval ziekte mutatie mutaties onderzoek "
        "patiënt patiënten risico syndroom tumor variant"
    ),
}

# Language-distinctive spellings (weight 1.2): the word *shape* already differs
# from English, so a single hit is meaningful evidence, unlike a shared
# function word.
DISTINCTIVE_WORDS: dict[str, str] = {
    "de": (
        "syndrom therapie krankheit mukoviszidose pädiatrie phenylketonurie melanom malignes malignen "
        "mutiertes mutationen genen brustkrebs mangel häufigkeit untersuchung behandlung ergebnisse "
        "diagnostik leitlinie wirksamkeit risiko karzinom patienten patientinnen erwachsene kinder "
        "geschwister erkrankten morbus gehirn stoffwechsel endokrinologie"
    ),
    "fr": (
        "déficit gène gènes liée lié dépistage prédisposition maladie thérapeutique thérapie étude données "
        "rôle héréditaire fibrose kystique phénotypique prévalence revue atteinte famille français "
        "française chez deux trois"
    ),
    "es": (
        "síndrome enfermedad mutación mutaciones hereditario análisis diagnóstico tratamiento clínico mama "
        "cáncer español asociación"
    ),
    "pt": (
        "síndrome doença mutação mutações hereditário análise diagnóstico tratamento clínico mama câncer "
        "relato associação português"
    ),
    "it": (
        "sindrome malattia mutazione mutazioni ereditario analisi diagnosi trattamento clinico mammella "
        "cancro associazione italiano"
    ),
    "nl": (
        "syndroom ziekte mutatie mutaties erfelijk onderzoek behandeling diagnose patiënt patiënten geval "
        "borstkanker risico nederlands"
    ),
}

# Orthographic markers: (substring, bonus, per-occurrence cap).
ORTHO_MARKERS: dict[str, tuple[tuple[str, float, int], ...]] = {
    "de": (("ß", 1.6, 2), ("ä", 0.4, 3), ("ö", 0.4, 3), ("ü", 0.4, 3)),
    "fr": (("œ", 1.2, 2), ("é", 0.3, 4), ("è", 0.3, 4), ("ê", 0.3, 3), ("à", 0.3, 3),
           ("ç", 0.3, 3), ("ù", 0.3, 2), ("ô", 0.3, 2), ("î", 0.3, 2), ("ï", 0.3, 2)),
    "es": (("ñ", 1.2, 3), ("¿", 1.5, 2), ("¡", 1.5, 2), ("á", 0.3, 3), ("í", 0.3, 3), ("ó", 0.3, 3)),
    "pt": (("ã", 1.2, 3), ("õ", 1.2, 3), ("ç", 0.3, 3), ("á", 0.3, 3), ("â", 0.3, 3),
           ("ê", 0.3, 3), ("ó", 0.3, 3), ("ô", 0.3, 3), ("ú", 0.3, 3)),
    "it": (("à", 0.4, 3), ("è", 0.4, 3), ("é", 0.3, 3), ("ì", 0.4, 3), ("ò", 0.4, 3), ("ù", 0.4, 3)),
    "nl": (("ij", 0.5, 3), ("ë", 0.4, 2), ("ï", 0.4, 2)),
}

ENGLISH_FUNCTION = (
    "the and of with in for a an is are was were be been to from by on at as that this these those it its "
    "we our their his her they he she not no but or if when while during after before between among into "
    "using used use can may might also more most less than then there here which who whom whose what how "
    "all any both each few other some such only own same so too very"
)
ENGLISH_DOMAIN = (
    "case report reports mutation mutations gene genes patients patient study analysis review disease "
    "syndrome cancer clinical associated novel variant variants spectrum outcome treatment diagnosis "
    "hereditary family risk cohort trial exome sequencing functional expression systematic guideline"
)

_WORD_RE = re.compile(r"[a-zà-öø-ÿœß]+")


def _word_set(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.casefold()))


_FUNCTION_SETS = {lang: _word_set(words) for lang, words in FUNCTION_WORDS.items()}
_DOMAIN_SETS = {lang: _word_set(words) for lang, words in DOMAIN_WORDS.items()}
_DISTINCTIVE_SETS = {lang: _word_set(words) for lang, words in DISTINCTIVE_WORDS.items()}
_EN_FUNCTION = _word_set(ENGLISH_FUNCTION)
_EN_DOMAIN = _word_set(ENGLISH_DOMAIN)


def score_latin(title: str) -> tuple[dict[str, float], dict[str, int]]:
    """Weighted lexical score and distinctive-spelling hit count per language."""
    folded = title.casefold()
    words = _word_set(title)
    scores: dict[str, float] = {}
    distinctive: dict[str, int] = {}
    for lang in LANGUAGES:
        score = 1.0 * len(words & _FUNCTION_SETS[lang])
        score += 0.6 * len(words & _DOMAIN_SETS[lang])
        hits = len(words & _DISTINCTIVE_SETS[lang])
        score += 1.2 * hits
        for marker, bonus, cap in ORTHO_MARKERS[lang]:
            score += bonus * min(folded.count(marker), cap)
        scores[lang] = round(score, 3)
        distinctive[lang] = hits
    scores["en"] = round(1.0 * len(words & _EN_FUNCTION) + 0.6 * len(words & _EN_DOMAIN), 3)
    return scores, distinctive


DEFAULT_CONFIG: dict[str, Any] = {
    "min_non_en_score": 2.5,
    "min_non_en_margin": 1.2,
    "min_en_score": 1.0,
    "low_evidence": True,
    "low_evidence_max_tokens": 6,
}


def classify(title: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deterministic language decision for one title."""
    cfg = config or DEFAULT_CONFIG
    title = (title or "").strip()
    if not title:
        return {"language": "unknown", "confidence": "none", "method": "empty",
                "evidence": "empty title", "scores": {}, "distinctive": {}}

    script_lang, script_evidence = detect_script(title)
    if script_lang:
        return {"language": script_lang, "confidence": "high", "method": "unicode-script",
                "evidence": script_evidence, "scores": {}, "distinctive": {}}

    scores, distinctive = score_latin(title)
    en = scores["en"]
    best_lang = max(LANGUAGES, key=lambda lang: scores[lang])
    best = scores[best_lang]
    evidence = f"{best_lang} score {best:.1f} vs en {en:.1f} (distinctive hits: {distinctive[best_lang]})"

    if best >= cfg["min_non_en_score"] and (best - en) >= cfg["min_non_en_margin"]:
        confidence = "high" if (best - en) >= 3.0 else "medium"
        return {"language": best_lang, "confidence": confidence, "method": "lexical",
                "evidence": evidence, "scores": scores, "distinctive": distinctive}

    if (
        cfg["low_evidence"]
        and distinctive[best_lang] >= 1
        and en == 0.0
        and len(_WORD_RE.findall(title.casefold())) <= cfg["low_evidence_max_tokens"]
    ):
        return {"language": best_lang, "confidence": "low", "method": "lexical-distinctive-only",
                "evidence": evidence, "scores": scores, "distinctive": distinctive}

    if en >= cfg["min_en_score"]:
        return {"language": "en", "confidence": "high" if en >= 2.0 else "medium",
                "method": "lexical", "evidence": evidence, "scores": scores, "distinctive": distinctive}
    return {"language": "unknown", "confidence": "none", "method": "lexical",
            "evidence": evidence, "scores": scores, "distinctive": distinctive}


# ── 3. Optional cross-checks ──────────────────────────────────────────────


def langdetect_label(title: str) -> str | None:
    """Independent n-gram detector (langdetect), seeded for determinism."""
    try:
        from langdetect import DetectorFactory, detect  # type: ignore

        DetectorFactory.seed = 0
        return detect(title)
    except Exception:  # noqa: BLE001 - optional dependency / very short titles
        return None


def llm_language(title: str) -> tuple[str | None, str]:
    """Ask the configured LLM for a language label (advisory cross-check)."""
    import os

    import httpx

    base = os.getenv("LIT_LLM_BASE_URL", "").rstrip("/")
    key = os.getenv("LIT_LLM_API_KEY", "")
    model = os.getenv("LIT_LLM_MODEL", "gpt-5")
    if not base or not key:
        return None, "no LLM endpoint configured"
    prompt = (
        "Identify the language of this scientific article title. Reply with ONLY the ISO 639-1 code "
        "(for example en, de, fr, es, pt, it, nl, zh, ja, ru) and nothing else.\n\nTitle: " + title
    )
    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"] or ""
        match = re.search(r"\b([a-z]{2})\b", content.strip().casefold())
        return (match.group(1) if match else None), content.strip()[:40]
    except Exception as exc:  # noqa: BLE001 - advisory only
        return None, f"llm error: {exc}"


# ── 4. Calibration + validation on the provider-labelled subset ───────────


def labelled_papers(gold: dict[str, dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """(variant_id, provider_label, title, journal) for provider-labelled papers."""
    rows = []
    for vid, variant in gold.items():
        for paper in variant.get("relevant_papers") or []:
            truth = (paper.get("language") or "unknown").strip() or "unknown"
            if truth != "unknown":
                rows.append((vid, truth, paper.get("title") or "", paper.get("journal") or ""))
    return rows


def journal_corroborates(journal: str | None, lang: str) -> bool:
    """Whether the journal name independently supports a non-English label.

    Journal names are metadata independent of the article title, so a journal
    whose own name is written in the detected language is evidence that the
    provider ``language`` label — not the classifier — is wrong.
    """
    if not journal:
        return False
    scores, distinctive = score_latin(journal)
    best_lang = max(LANGUAGES, key=lambda code: scores[code])
    return bool(
        best_lang == lang
        and scores[lang] > scores["en"]
        and (scores[lang] >= 2.0 or distinctive[lang] >= 1)
    )


def _split(rows: list[tuple[str, str, str, str]]) -> tuple[list, list]:
    """Deterministic split by variant parity (no title-level peeking)."""
    calib = [r for r in rows if int(r[0][1:]) % 2 == 1]
    heldout = [r for r in rows if int(r[0][1:]) % 2 == 0]
    return calib, heldout


def _score_split(rows: list[tuple[str, str, str, str]], config: dict[str, Any]) -> dict[str, Any]:
    """Accuracy / precision / recall of the non-English decision."""
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for _, truth, title, _journal in rows:
        confusion[truth][classify(title, config)["language"]] += 1
    total = sum(sum(row.values()) for row in confusion.values())
    correct = sum(row.get(lang, 0) for lang, row in confusion.items())

    def prf(target: str) -> dict[str, float | int]:
        tp = sum(row.get(target, 0) for lang, row in confusion.items() if lang == target)
        fp = sum(row.get(target, 0) for lang, row in confusion.items() if lang != target)
        fn = sum(count for pred, count in confusion.get(target, {}).items() if pred != target)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 4),
                "recall": round(recall, 4), "f1": round(f1, 4)}

    # "non-English" is the positive class of interest; "unknown" is abstention
    # and is excluded from precision/recall (it is penalised via recall of the
    # target languages instead).
    non_en = {"tp": 0, "fp": 0, "fn": 0}
    for lang, row in confusion.items():
        for pred, count in row.items():
            if pred == "unknown":
                continue
            if lang != "en" and pred != "en":
                non_en["tp"] += count
            elif lang == "en" and pred != "en":
                non_en["fp"] += count
            elif lang != "en" and pred == "en":
                non_en["fn"] += count
    precision = non_en["tp"] / (non_en["tp"] + non_en["fp"]) if (non_en["tp"] + non_en["fp"]) else 0.0
    recall = non_en["tp"] / (non_en["tp"] + non_en["fn"]) if (non_en["tp"] + non_en["fn"]) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "n": total,
        "accuracy": round(correct / total, 4) if total else None,
        "macro_f1_en_vs_non_en": round((f1 + prf("en")["f1"]) / 2, 4),
        "non_english": {"precision": round(precision, 4), "recall": round(recall, 4),
                        "f1": round(f1, 4), **non_en},
        "per_language": {
            lang: {"n": sum(row.values()), "correct": row.get(lang, 0),
                   "recall": round(row.get(lang, 0) / sum(row.values()), 4),
                   "predicted_as": dict(row.most_common())}
            for lang, row in sorted(confusion.items())
        },
    }


def tune(calib: list[tuple[str, str, str, str]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Grid-search thresholds on the calibration half only.

    The objective is computed after removing provider-label noise (cases the
    classifier can prove are mislabelled), so that correctly recovered German
    or Spanish titles are not punished as errors.
    """
    clean = [r for r in calib if r[2] not in label_noise_audit(calib)["titles"]]
    grid = []
    for min_score in (2.0, 2.2, 2.5, 2.8, 3.0, 3.5):
        for min_margin in (0.8, 1.0, 1.2, 1.5, 2.0):
            for low_evidence in (True, False):
                config = dict(DEFAULT_CONFIG, min_non_en_score=min_score, min_non_en_margin=min_margin,
                              low_evidence=low_evidence)
                result = _score_split(clean, config)
                grid.append({"config": config, "macro_f1": result["macro_f1_en_vs_non_en"],
                             "accuracy": result["accuracy"], "non_english": result["non_english"]})
    grid.sort(key=lambda row: (row["macro_f1"], row["accuracy"]), reverse=True)
    return grid[0]["config"], grid[:10]


def label_noise_audit(rows: list[tuple[str, str, str, str]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Provider labels contradicted by independent evidence.

    Two provable classes:

    * **script**: the title is written entirely in a non-Latin script, which
      cannot be an English-language article;
    * **journal**: the classifier labels the title non-English and the journal
      name is itself written in that language (journal metadata is independent
      of the title).
    """
    cases = []
    titles = set()
    for vid, truth, title, journal in rows:
        decision = classify(title, config)
        pred = decision["language"]
        if pred == truth or pred == "unknown":
            continue
        script_lang, evidence = detect_script(title)
        counts = {lang: len(rx.findall(title)) for lang, rx in _SCRIPT_RE.items()}
        if script_lang and max(counts.values(), default=0) >= SCRIPT_NOISE_MIN_CHARS:
            cases.append({"variant_id": vid, "provider_label": truth, "detected_language": script_lang,
                          "detection": "unicode-script", "evidence": evidence, "title": title,
                          "journal": journal})
            titles.add(title)
        elif journal_corroborates(journal, pred):
            cases.append({"variant_id": vid, "provider_label": truth, "detected_language": pred,
                          "detection": "journal-name", "evidence": f"journal written in {pred}",
                          "title": title, "journal": journal})
            titles.add(title)
    return {
        "n": len(cases),
        "note": (
            "Provider 'language' labels contradicted by the title script or by the journal name; these are "
            "excluded from the adjudicated accuracy below and are evidence that the provider metadata, not "
            "the classifier, is wrong."
        ),
        "cases": cases,
        "titles": titles,
    }


# ── 5. Backfill + language recovery recomputation ─────────────────────────


def backfill(
    gold: dict[str, dict[str, Any]],
    config: dict[str, Any],
    *,
    cross_check: bool,
    llm_adjudicate: bool,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Return (backfilled gold copy, decision rows, assignment counts)."""
    patched = copy.deepcopy(gold)
    decisions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for vid, variant in patched.items():
        for paper in variant.get("relevant_papers") or []:
            original = (paper.get("language") or "unknown").strip() or "unknown"
            if original != "unknown":
                continue
            title = paper.get("title") or ""
            decision = classify(title, config)
            row = {
                "variant_id": vid,
                "pmid": paper.get("pmid"),
                "doi": paper.get("doi"),
                "title": title,
                "journal": paper.get("journal"),
                "original_language": "unknown",
                "detected_language": decision["language"],
                "confidence": decision["confidence"],
                "method": decision["method"],
                "evidence": decision["evidence"],
                "scores": decision["scores"],
                "distinctive": decision["distinctive"],
            }
            if cross_check:
                row["langdetect"] = langdetect_label(title)
            if llm_adjudicate:
                llm_label, llm_raw = llm_language(title)
                row["llm"] = llm_label
                row["llm_raw"] = llm_raw
            paper["language_backfilled"] = decision["language"]
            paper["language_backfill_confidence"] = decision["confidence"]
            if decision["language"] != "unknown":
                paper["language"] = decision["language"]
            counts[decision["language"]] += 1
            decisions.append(row)
    return patched, decisions, dict(counts)


def recovery_table(
    methods_results: dict[str, dict[str, list[dict[str, Any]]]],
    gold: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Per-method gold recovery split by language (same math as paper_data)."""
    gold_by_lang: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for vid, variant in gold.items():
        for paper in variant.get("relevant_papers") or []:
            pid = paper_id(paper)
            if pid:
                gold_by_lang[(paper.get("language") or "unknown")].add((vid, pid))
    out: dict[str, Any] = {}
    for method, results in methods_results.items():
        found: set[tuple[str, str]] = set()
        for vid, papers in results.items():
            for paper in papers:
                pid = paper_id(paper)
                if pid:
                    found.add((vid, pid))
        out[method] = {lang: len(pairs & found) for lang, pairs in sorted(gold_by_lang.items())}
    return {"gold_totals": {lang: len(p) for lang, p in sorted(gold_by_lang.items())},
            "found_by_method": out}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cross-check", action="store_true", help="run langdetect as a second opinion")
    parser.add_argument("--llm-adjudicate", action="store_true", help="ask the LLM endpoint for a third opinion")
    parser.add_argument("--no-tune", action="store_true", help="use the default thresholds unmodified")
    args = parser.parse_args()

    gold = load_gold()
    rows = labelled_papers(gold)
    calib, heldout = _split(rows)
    if args.no_tune:
        config, top = DEFAULT_CONFIG, []
    else:
        config, top = tune(calib)
    calibration = _score_split(calib, config)
    heldout_result = _score_split(heldout, config)
    noise = label_noise_audit(rows, config)
    noise_titles = noise["titles"]
    noise_artifact = {key: value for key, value in noise.items() if key != "titles"}
    all_clean = [r for r in rows if r[2] not in noise_titles]
    calib_clean = [r for r in calib if r[2] not in noise_titles]
    heldout_clean = [r for r in heldout if r[2] not in noise_titles]
    overall = _score_split(rows, config)
    overall_adjudicated = _score_split(all_clean, config)

    patched, decisions, counts = backfill(
        gold, config, cross_check=args.cross_check, llm_adjudicate=args.llm_adjudicate
    )

    collected = json.loads(COLLECTED_FILE.read_text())
    nested, _ = build_nested_full_pipeline(collected)
    methods_results: dict[str, dict[str, list[dict[str, Any]]]] = {
        "pubmed_only": collected["pubmed_only"],
        "multisource_no_translation": collected["multisource_no_translation"],
        "multisource_with_translation": collected["multisource_with_translation"],
        "full_pipeline_nested": nested,
    }
    before = recovery_table(methods_results, gold)
    after = recovery_table(methods_results, patched)

    added: Counter[str] = Counter()
    for row in decisions:
        if row["detected_language"] != "unknown":
            added[row["detected_language"]] += 1

    queue = []
    for row in decisions:
        non_ascii = any(ord(ch) > 127 for ch in row["title"])
        disagree = (
            (row.get("langdetect") and row["langdetect"] != row["detected_language"])
            or (row.get("llm") and row["llm"] != row["detected_language"])
        )
        if (non_ascii or row["detected_language"] == "unknown" or disagree
                or row["confidence"] in ("medium", "low")):
            queue.append(row)

    cross_summary = None
    if args.cross_check:
        comparable = [r for r in decisions if r.get("langdetect")]
        agree = sum(1 for r in comparable if r["langdetect"] == r["detected_language"])
        non_en_agree = [r for r in comparable if r["detected_language"] != "en"]
        cross_summary = {
            "detector": "langdetect 1.0.9 (DetectorFactory.seed = 0)",
            "n": len(comparable),
            "agreement_with_rules": round(agree / len(comparable), 4) if comparable else None,
            "agreement_on_non_english": (
                round(sum(1 for r in non_en_agree if r["langdetect"] == r["detected_language"])
                      / len(non_en_agree), 4) if non_en_agree else None
            ),
        }

    artifact = {
        "schema_version": 1,
        "protocol": {
            "plan_reference": "paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md §4.1 (E4a)",
            "scope": "all 305 unknown-language papers in the frozen 43-variant reference set",
            "method": (
                "deterministic: Unicode script detection + weighted function/domain/distinctive vocabulary "
                "and orthographic markers; English is the competing hypothesis, and a non-English label "
                "needs score >= min_non_en_score with a margin >= min_non_en_margin over English"
            ),
            "thresholds": config,
            "threshold_selection": (
                "grid search over 60 configurations on the odd-variant calibration half only "
                "(macro-F1 of en vs non-English); held-out half reported below"
            ),
            "top_calibration_configs": top,
            "no_live_retrieval": True,
            "reference_set_edits": "none on disk; backfilled labels live in this artifact only (in-memory copy)",
            "cross_check": cross_summary,
            "llm_adjudication": bool(args.llm_adjudicate),
        },
        "validation": {
            "calibration_half": calibration,
            "calibration_half_adjudicated": _score_split(calib_clean, config),
            "heldout_half": heldout_result,
            "heldout_half_adjudicated": _score_split(heldout_clean, config),
            "all_labelled": overall,
            "all_labelled_adjudicated": overall_adjudicated,
            "provider_label_noise": noise_artifact,
            "limitation": (
                "The classifier sees titles only, while provider 'language' describes the publication, so "
                "English-titled non-English articles are unrecoverable by design (V026 is the known "
                "instance). Labels are title-language evidence and the review queue carries the residual "
                "uncertainty."
            ),
        },
        "assignments": {
            "n_unknown_before": len(decisions),
            "assigned": dict(sorted(added.items())),
            "still_unknown": counts.get("unknown", 0),
            "decided": sum(1 for d in decisions if d["detected_language"] != "unknown"),
        },
        "gold_totals_before": before["gold_totals"],
        "gold_totals_after": after["gold_totals"],
        "recovery_before": before["found_by_method"],
        "recovery_after": after["found_by_method"],
        "method_labels": METHODS,
        "decisions": decisions,
        "human_review_queue_size": len(queue),
        "human_review_note": (
            "Queue = all non-ASCII titles, undecided titles, medium/low-confidence labels and cross-check "
            "disagreements; the plan requires human sign-off on non-ASCII titles before these labels are "
            "used in a primary claim."
        ),
    }
    OUT_FILE.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))

    with REVIEW_FILE.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["variant_id", "pmid", "doi", "detected_language", "confidence", "method",
                         "langdetect", "llm", "evidence", "title"])
        for row in sorted(queue, key=lambda r: (r["detected_language"], r["variant_id"])):
            writer.writerow([row["variant_id"], row["pmid"], row["doi"], row["detected_language"],
                             row["confidence"], row["method"], row.get("langdetect", ""), row.get("llm", ""),
                             row["evidence"], row["title"]])

    # ── console report ────────────────────────────────────────────────────
    print(f"thresholds: {config}")
    for name, result in (
        ("calibration", calibration),
        ("calibration (noise removed)", _score_split(calib_clean, config)),
        ("held-out", heldout_result),
        ("held-out (noise removed)", _score_split(heldout_clean, config)),
        ("all labelled", overall),
        ("all (noise removed)", overall_adjudicated),
    ):
        non_en = result["non_english"]
        print(f"{name:28s} n={result['n']:4d} acc={result['accuracy']:.3f} "
              f"non-EN P={non_en['precision']:.3f} R={non_en['recall']:.3f} F1={non_en['f1']:.3f}")
    for lang, row in _score_split(heldout_clean, config)["per_language"].items():
        print(f"  held-out {lang}: recall {row['recall']:.3f} ({row['correct']}/{row['n']}) "
              f"-> {row['predicted_as']}")
    print(f"provider-label noise cases: {noise['n']} "
          f"({', '.join(sorted({c['variant_id'] for c in noise['cases']}))})")
    for case in noise["cases"]:
        print(f"  [{case['detection']}] {case['variant_id']} provider={case['provider_label']} "
              f"detected={case['detected_language']} journal={case['journal']} :: {case['title'][:60]}")
    print(f"\nbackfill: {artifact['assignments']}")
    print(f"gold totals before: {before['gold_totals']}")
    print(f"gold totals after : {after['gold_totals']}")
    print("\n| Method | de before/after | fr before/after |")
    print("| --- | --- | --- |")
    for method in METHODS:
        b, a = before["found_by_method"][method], after["found_by_method"][method]
        print(f"| {METHODS[method]} | {b.get('de', 0)} -> {a.get('de', 0)} | "
              f"{b.get('fr', 0)} -> {a.get('fr', 0)} |")
    if cross_summary:
        print(f"\nlangdetect cross-check: {cross_summary}")
    print(f"\nwrote {OUT_FILE}\nwrote {REVIEW_FILE} ({len(queue)} rows for review)")


if __name__ == "__main__":
    main()
