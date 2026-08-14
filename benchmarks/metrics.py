"""Evaluation metrics for benchmark.

Definitions (all lists are ranked; ``K`` is a fixed cutoff):

- ``Recall@K``      = |relevant ∩ top-K| / |relevant|
- ``Precision@K``   = |relevant ∩ top-K| / K            (FIXED denominator K,
  standard definition; when a method returns fewer than K items the missing
  ranks count against it)
- ``Precision@Retrieved`` = |relevant ∩ returned| / |returned|  (the lenient
  variant that divides by the actual list length; reported separately and
  never called "Precision@K")
- ``SetRecall``     = |relevant ∩ returned| / |relevant|  (recall of the full
  returned candidate set, independent of K)
- ``F1@K``          = harmonic mean of Recall@K and Precision@K
- ``nDCG@K``        = standard binary-gain nDCG

Aggregation reports both the macro average (mean over variants) and the
micro average (pooled counts over all variants).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

DEFAULT_KS = (5, 10, 15, 20, 50)


def _normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        d = d.removeprefix(prefix)
    return d or None


def _paper_id(paper: dict[str, Any]) -> str | None:
    """Return a unique identifier for a paper (DOI > PMID > title)."""
    doi = _normalize_doi(paper.get("doi"))
    if doi:
        return f"doi:{doi}"
    pmid = paper.get("pmid") or paper.get("PMID")
    if pmid:
        return f"pmid:{str(pmid).strip()}"
    title = (paper.get("title") or "").strip().lower()
    if title:
        return f"title:{title[:100]}"
    return None


def paper_id(paper: dict[str, Any]) -> str | None:
    """Public alias of the canonical paper identity function."""
    return _paper_id(paper)


def _relevant_ids(relevant_papers: Iterable[dict[str, Any]]) -> set[str]:
    return {pid for p in relevant_papers if (pid := _paper_id(p))}


def _hits_at_k(
    found_papers: list[dict[str, Any]],
    relevant_ids: set[str],
    k: int | None,
) -> int:
    """Number of unique relevant papers within the top-K of the ranked list."""
    top = found_papers if k is None else found_papers[:k]
    found = {pid for p in top if (pid := _paper_id(p))}
    return len(relevant_ids & found)


def compute_recall_at_k(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
    k: int = 50,
) -> float:
    """Recall@K: fraction of relevant papers found in top-K results."""
    rel = _relevant_ids(relevant_papers)
    if not rel:
        return 0.0
    return _hits_at_k(found_papers, rel, k) / len(rel)


def compute_precision_at_k(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
    k: int = 50,
) -> float:
    """Precision@K with the standard FIXED denominator K.

    Methods that return fewer than K candidates are penalized for the
    missing ranks. Use :func:`compute_precision_at_retrieved` for the
    length-normalized variant.
    """
    rel = _relevant_ids(relevant_papers)
    if not rel or k <= 0:
        return 0.0
    return _hits_at_k(found_papers, rel, k) / k


def compute_precision_at_retrieved(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
) -> float:
    """Precision over the actually returned list (denominator = list length)."""
    if not found_papers:
        return 0.0
    rel = _relevant_ids(relevant_papers)
    if not rel:
        return 0.0
    return _hits_at_k(found_papers, rel, None) / len(found_papers)


def compute_set_recall(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
) -> float:
    """Recall of the entire returned candidate set (no cutoff)."""
    rel = _relevant_ids(relevant_papers)
    if not rel:
        return 0.0
    return _hits_at_k(found_papers, rel, None) / len(rel)


def compute_f1_at_k(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
    k: int = 50,
) -> float:
    """F1@K: harmonic mean of fixed-denominator Precision@K and Recall@K."""
    recall = compute_recall_at_k(found_papers, relevant_papers, k)
    precision = compute_precision_at_k(found_papers, relevant_papers, k)
    if recall + precision == 0:
        return 0.0
    return 2 * recall * precision / (recall + precision)


def compute_ndcg_at_k(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
    k: int = 50,
) -> float:
    """nDCG@K with binary gains."""
    if not relevant_papers or not found_papers:
        return 0.0
    rel = _relevant_ids(relevant_papers)
    if not rel:
        return 0.0

    dcg = 0.0
    seen: set[str] = set()
    for i, paper in enumerate(found_papers[:k]):
        pid = _paper_id(paper)
        if pid and pid in rel and pid not in seen:
            dcg += 1.0 / math.log2(i + 2)  # i+2 because log2(1)=0
            seen.add(pid)

    ideal_hits = min(len(rel), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def compute_source_coverage(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
) -> dict[str, int]:
    """Count relevant papers per source label.

    NOTE: source labels reflect the FIRST provider that surfaced each
    deduplicated record ("first-seen attribution"), not exclusive
    availability; a paper attributed to Crossref may also be indexed by
    other providers.
    """
    rel = _relevant_ids(relevant_papers)
    coverage: dict[str, int] = defaultdict(int)
    counted: set[str] = set()
    for paper in found_papers:
        pid = _paper_id(paper)
        if pid and pid in rel and pid not in counted:
            counted.add(pid)
            source = paper.get("source") or paper.get("_source_provider") or "unknown"
            coverage[source] += 1
    return dict(coverage)


def compute_language_coverage(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
) -> dict[str, int]:
    """Count relevant papers found per language metadata value."""
    rel = _relevant_ids(relevant_papers)
    coverage: dict[str, int] = defaultdict(int)
    counted: set[str] = set()
    for paper in found_papers:
        pid = _paper_id(paper)
        if pid and pid in rel and pid not in counted:
            counted.add(pid)
            lang = paper.get("language") or paper.get("lang") or "unknown"
            coverage[lang] += 1
    return dict(coverage)


@dataclass
class VariantMetrics:
    """Per-variant metrics for a single method."""

    variant_id: str
    method: str
    n_retrieved: int = 0
    n_relevant: int = 0
    relevant_found: int = 0  # set-level unique relevant hits
    set_recall: float = 0.0
    precision_at_retrieved: float = 0.0
    recall_at: dict[str, float] = field(default_factory=dict)
    precision_at: dict[str, float] = field(default_factory=dict)  # fixed denominator
    f1_at: dict[str, float] = field(default_factory=dict)
    ndcg_at: dict[str, float] = field(default_factory=dict)
    source_coverage: dict[str, int] = field(default_factory=dict)
    language_coverage: dict[str, int] = field(default_factory=dict)


def compute_variant_metrics(
    variant: dict[str, Any],
    found_papers: list[dict[str, Any]],
    method: str,
    ks: Iterable[int] = DEFAULT_KS,
) -> VariantMetrics:
    """Compute all metrics for a single variant and method."""
    relevant = variant.get("relevant_papers") or []
    rel_ids = _relevant_ids(relevant)

    m = VariantMetrics(
        variant_id=variant["variant_id"],
        method=method,
        n_retrieved=len(found_papers),
        n_relevant=len(rel_ids),
    )
    m.relevant_found = _hits_at_k(found_papers, rel_ids, None)
    m.set_recall = compute_set_recall(found_papers, relevant)
    m.precision_at_retrieved = compute_precision_at_retrieved(found_papers, relevant)
    for k in ks:
        key = str(k)
        m.recall_at[key] = compute_recall_at_k(found_papers, relevant, k)
        m.precision_at[key] = compute_precision_at_k(found_papers, relevant, k)
        m.f1_at[key] = compute_f1_at_k(found_papers, relevant, k)
        m.ndcg_at[key] = compute_ndcg_at_k(found_papers, relevant, k)
    m.source_coverage = compute_source_coverage(found_papers, relevant)
    m.language_coverage = compute_language_coverage(found_papers, relevant)
    return m


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    """Population standard deviation (matches previously reported tables)."""
    if len(values) <= 1:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def aggregate_metrics(
    all_metrics: list[VariantMetrics],
    ks: Iterable[int] = DEFAULT_KS,
) -> dict[str, Any]:
    """Aggregate per-variant metrics into macro + micro summary statistics."""
    if not all_metrics:
        return {}

    methods: dict[str, list[VariantMetrics]] = defaultdict(list)
    for m in all_metrics:
        methods[m.method].append(m)

    summary: dict[str, Any] = {}
    for method, rows in methods.items():
        entry: dict[str, Any] = {"n_variants": len(rows)}

        # Macro averages (mean over variants) per K.
        for family in ("recall_at", "precision_at", "f1_at", "ndcg_at"):
            entry[family] = {}
            for k in ks:
                key = str(k)
                vals = [getattr(r, family)[key] for r in rows]
                entry[family][key] = {"mean": _mean(vals), "std": _std(vals)}

        for name in ("set_recall", "precision_at_retrieved"):
            vals = [getattr(r, name) for r in rows]
            entry[name] = {"mean": _mean(vals), "std": _std(vals)}

        # Micro (pooled) aggregates.
        total_rel = sum(r.n_relevant for r in rows)
        total_found = sum(r.relevant_found for r in rows)
        total_ret = sum(r.n_retrieved for r in rows)
        entry["micro"] = {
            "relevant_found": total_found,
            "total_relevant": total_rel,
            "total_retrieved": total_ret,
            "set_recall": total_found / total_rel if total_rel else 0.0,
            "precision_at_retrieved": total_found / total_ret if total_ret else 0.0,
        }
        entry["candidates_per_variant"] = {
            "mean": _mean([float(r.n_retrieved) for r in rows]),
            "min": min((r.n_retrieved for r in rows), default=0),
            "max": max((r.n_retrieved for r in rows), default=0),
        }

        # Pooled source / language coverage.
        src: dict[str, int] = defaultdict(int)
        lang: dict[str, int] = defaultdict(int)
        for r in rows:
            for s, c in r.source_coverage.items():
                src[s] += c
            for lg, c in r.language_coverage.items():
                lang[lg] += c
        entry["source_coverage"] = dict(src)
        entry["language_coverage"] = dict(lang)

        summary[method] = entry

    return summary


def format_summary_table(summary: dict[str, Any], k: int = 20) -> str:
    """Format summary as a markdown table (fixed-denominator metrics at K)."""
    key = str(k)
    headers = [
        "Method",
        f"Recall@{k}",
        f"Prec@{k}",
        f"F1@{k}",
        f"nDCG@{k}",
        "SetRecall",
        "Prec@Retr",
        "Cands/variant",
    ]
    rows = []
    for method, s in sorted(summary.items()):
        rows.append(
            f"| {method} "
            f"| {s['recall_at'][key]['mean']:.3f}±{s['recall_at'][key]['std']:.3f} "
            f"| {s['precision_at'][key]['mean']:.3f}±{s['precision_at'][key]['std']:.3f} "
            f"| {s['f1_at'][key]['mean']:.3f}±{s['f1_at'][key]['std']:.3f} "
            f"| {s['ndcg_at'][key]['mean']:.3f}±{s['ndcg_at'][key]['std']:.3f} "
            f"| {s['set_recall']['mean']:.3f}±{s['set_recall']['std']:.3f} "
            f"| {s['precision_at_retrieved']['mean']:.3f}±{s['precision_at_retrieved']['std']:.3f} "
            f"| {s['candidates_per_variant']['mean']:.1f} |"
        )
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    return "\n".join([header_line, sep_line] + rows)
