"""Evaluation metrics for benchmark.

Computes Recall@K, Precision@K, F1@K, nDCG@K, and source coverage.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MethodResult:
    """Results for a single method on a single variant."""

    variant_id: str
    method: str
    found_papers: list[dict[str, Any]] = field(default_factory=list)
    # found_papers: [{doi, pmid, title, source, language, ...}]


@dataclass
class VariantMetrics:
    """Per-variant metrics for a single method."""

    variant_id: str
    method: str
    recall: float = 0.0
    precision: float = 0.0
    f1: float = 0.0
    ndcg: float = 0.0
    total_relevant: int = 0
    total_found: int = 0
    relevant_found: int = 0
    source_coverage: dict[str, int] = field(default_factory=dict)
    language_coverage: dict[str, int] = field(default_factory=dict)


def _normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    return doi.strip().lower().lstrip("https://doi.org/").lstrip("http://doi.org/")


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


def compute_recall_at_k(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
    k: int = 50,
) -> float:
    """Recall@K: fraction of relevant papers found in top-K results."""
    if not relevant_papers:
        return 0.0
    relevant_ids = {_paper_id(p) for p in relevant_papers if _paper_id(p)}
    if not relevant_ids:
        return 0.0
    top_k = found_papers[:k]
    found_ids = {_paper_id(p) for p in top_k if _paper_id(p)}
    return len(relevant_ids & found_ids) / len(relevant_ids)


def compute_precision_at_k(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
    k: int = 50,
) -> float:
    """Precision@K: fraction of top-K results that are relevant."""
    if not found_papers:
        return 0.0
    relevant_ids = {_paper_id(p) for p in relevant_papers if _paper_id(p)}
    if not relevant_ids:
        return 0.0
    top_k = found_papers[:k]
    found_ids = {_paper_id(p) for p in top_k if _paper_id(p)}
    relevant_found = len(relevant_ids & found_ids)
    return relevant_found / len(top_k) if top_k else 0.0


def compute_f1_at_k(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
    k: int = 50,
) -> float:
    """F1@K: harmonic mean of precision@K and recall@K."""
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
    """nDCG@K: normalized discounted cumulative gain."""
    if not relevant_papers or not found_papers:
        return 0.0
    relevant_ids = {_paper_id(p) for p in relevant_papers if _paper_id(p)}
    if not relevant_ids:
        return 0.0

    # DCG: sum of rel_i / log2(i+1) for i=1..K
    dcg = 0.0
    for i, paper in enumerate(found_papers[:k]):
        pid = _paper_id(paper)
        if pid and pid in relevant_ids:
            dcg += 1.0 / math.log2(i + 2)  # i+2 because log2(1)=0

    # IDCG: ideal ranking (all relevant at top)
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))

    return dcg / idcg if idcg > 0 else 0.0


def compute_source_coverage(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
) -> dict[str, int]:
    """Count how many relevant papers each source contributed."""
    relevant_ids = {_paper_id(p) for p in relevant_papers if _paper_id(p)}
    coverage: dict[str, int] = defaultdict(int)
    for paper in found_papers:
        pid = _paper_id(paper)
        if pid and pid in relevant_ids:
            source = paper.get("source") or paper.get("_source_provider") or "unknown"
            coverage[source] += 1
    return dict(coverage)


def compute_language_coverage(
    found_papers: list[dict[str, Any]],
    relevant_papers: list[dict[str, Any]],
) -> dict[str, int]:
    """Count how many relevant papers were found per language."""
    relevant_ids = {_paper_id(p) for p in relevant_papers if _paper_id(p)}
    coverage: dict[str, int] = defaultdict(int)
    for paper in found_papers:
        pid = _paper_id(paper)
        if pid and pid in relevant_ids:
            lang = paper.get("language") or paper.get("lang") or "unknown"
            coverage[lang] += 1
    return dict(coverage)


def compute_variant_metrics(
    variant: dict[str, Any],
    method_results: list[dict[str, Any]],
    k: int = 50,
) -> VariantMetrics:
    """Compute all metrics for a single variant and method."""
    relevant = variant.get("relevant_papers") or []
    metrics = VariantMetrics(
        variant_id=variant["variant_id"],
        method=method_results[0].get("method", "unknown") if method_results else "unknown",
        total_relevant=len(relevant),
        total_found=len(method_results),
    )

    found_papers = method_results
    metrics.recall = compute_recall_at_k(found_papers, relevant, k)
    metrics.precision = compute_precision_at_k(found_papers, relevant, k)
    metrics.f1 = compute_f1_at_k(found_papers, relevant, k)
    metrics.ndcg = compute_ndcg_at_k(found_papers, relevant, k)
    metrics.relevant_found = int(metrics.recall * len(relevant)) if relevant else 0
    metrics.source_coverage = compute_source_coverage(found_papers, relevant)
    metrics.language_coverage = compute_language_coverage(found_papers, relevant)

    return metrics


def aggregate_metrics(
    all_metrics: list[VariantMetrics],
) -> dict[str, Any]:
    """Aggregate per-variant metrics into summary statistics."""
    if not all_metrics:
        return {}

    methods: dict[str, list[VariantMetrics]] = defaultdict(list)
    for m in all_metrics:
        methods[m.method].append(m)

    summary: dict[str, Any] = {}
    for method, metrics_list in methods.items():
        recalls = [m.recall for m in metrics_list]
        precisions = [m.precision for m in metrics_list]
        f1s = [m.f1 for m in metrics_list]
        ndcgs = [m.ndcg for m in metrics_list]

        # Aggregate source coverage across all variants
        total_source: dict[str, int] = defaultdict(int)
        total_lang: dict[str, int] = defaultdict(int)
        for m in metrics_list:
            for src, count in m.source_coverage.items():
                total_source[src] += count
            for lang, count in m.language_coverage.items():
                total_lang[lang] += count

        n = len(metrics_list)
        summary[method] = {
            "n_variants": n,
            "recall_mean": sum(recalls) / n,
            "recall_std": _std(recalls),
            "precision_mean": sum(precisions) / n,
            "precision_std": _std(precisions),
            "f1_mean": sum(f1s) / n,
            "f1_std": _std(f1s),
            "ndcg_mean": sum(ndcgs) / n,
            "ndcg_std": _std(ndcgs),
            "total_relevant_found": sum(m.relevant_found for m in metrics_list),
            "total_relevant": sum(m.total_relevant for m in metrics_list),
            "source_coverage": dict(total_source),
            "language_coverage": dict(total_lang),
        }

    return summary


def _std(values: list[float]) -> float:
    """Population standard deviation."""
    if len(values) <= 1:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def format_summary_table(summary: dict[str, Any]) -> str:
    """Format summary as a markdown table for the paper."""
    headers = ["Method", "Recall", "Precision", "F1", "nDCG"]
    rows = []
    for method, stats in sorted(summary.items()):
        rows.append(
            f"| {method} "
            f"| {stats['recall_mean']:.3f}±{stats['recall_std']:.3f} "
            f"| {stats['precision_mean']:.3f}±{stats['precision_std']:.3f} "
            f"| {stats['f1_mean']:.3f}±{stats['f1_std']:.3f} "
            f"| {stats['ndcg_mean']:.3f}±{stats['ndcg_std']:.3f} |"
        )
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    return "\n".join([header_line, sep_line] + rows)
