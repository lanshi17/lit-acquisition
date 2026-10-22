"""E2a evaluation: recall of the pipeline against ClinVar-curated citations.

Plan reference: ``paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md`` section 2.2 (E2a).

Ground truth: ``gold_standard/variants_clinvar.jsonl`` --- the literature that
clinical laboratories cite in their ClinVar submissions for the same 43
variants (``build_clinvar_ground_truth.py``). Nothing in this pool was chosen or
scored by this project, so a pipeline advantage here cannot be explained by
circularity with the federated providers.

The evaluation is entirely offline: the frozen candidate lists are scored
against the ClinVar citations, matching on DOI, PMID, or normalised title
(candidate lists are heterogeneous: Crossref items may carry a DOI but no
PMID).

Reported variants
-----------------
* **all curated citations** (``clinvar_all``) --- every citation attached to a
  verified ClinVar record for the variant;
* **strict** (``clinvar_strict``) --- only citations from records whose title
  contains the variant token, which excludes gene-anchored fallback records
  (legacy names such as "exon 7 deletion" or "Swedish").

Declared test family (paired Wilcoxon, Holm-corrected, 36 variants with
ClinVar records, on the all-citations truth): M4n vs M1 (SetRecall, Recall@20),
M2_recorded vs M1 (SetRecall), M3_recorded vs M1 (SetRecall), M3_lex vs M2_lex
(Recall@20), M3_rerank vs M2_rerank (Recall@20), M2_lex vs M2_rerank
(Recall@20), M3_lex vs M3_rerank (Recall@20), M4n vs S3 (SetRecall).

Output: ``benchmarks/results/clinvar_eval.json``.

Usage::

    python benchmarks/analysis/clinvar_eval.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from analysis.api_scores import load_cache, order_by_scores, rerank_scores, save_cache, warm_cache_size  # noqa: E402
from analysis.nested import build_nested_full_pipeline  # noqa: E402
from analysis.stats import holm_correction, paired_test  # noqa: E402
from lit_acquisition.algorithms import dedupe_candidates, rank_candidates  # noqa: E402

RESULTS_DIR = BENCH_DIR / "results"
GOLD_DIR = BENCH_DIR / "gold_standard"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
CLINVAR_FILE = GOLD_DIR / "variants_clinvar.jsonl"
OUT_FILE = RESULTS_DIR / "clinvar_eval.json"
KS = (5, 10, 20, 50)

METHOD_LABELS = {
    "pubmed_only": "M1 PubMed only",
    "m2_recorded": "M2 recorded federation order",
    "m2_lex": "M2_lex dedup + lexical rank",
    "m2_rerank": "M2_rerank + cross-encoder",
    "m3_recorded": "M3 recorded order (manuscript)",
    "m3_lex": "M3_lex dedup + lexical rank",
    "m3_rerank": "M3_rerank + cross-encoder",
    "full_pipeline_nested": "M4n nested full pipeline",
    "sota_dense": "S1 dense (recorded)",
    "sota_hybrid": "S3 hybrid (recorded, unranked pool)",
    "sota_mdense": "S6 multilingual dense (recorded)",
}

DECLARED = [
    ("m4n_vs_m1_setrecall", "pubmed_only", "full_pipeline_nested", "set"),
    ("m4n_vs_m1_recall20", "pubmed_only", "full_pipeline_nested", "20"),
    ("m2_vs_m1_setrecall", "pubmed_only", "m2_recorded", "set"),
    ("m3_vs_m1_setrecall", "pubmed_only", "m3_recorded", "set"),
    ("translation_lexical_recall20", "m2_lex", "m3_lex", "20"),
    ("translation_reranked_recall20", "m2_rerank", "m3_rerank", "20"),
    ("ranking_lexical_recall20", "m2_recorded", "m2_lex", "20"),
    ("crossencoder_recall20", "m2_lex", "m2_rerank", "20"),
    ("crossencoder_translated_recall20", "m3_lex", "m3_rerank", "20"),
    ("m4n_vs_s3_setrecall", "sota_hybrid", "full_pipeline_nested", "set"),
]


def normalize_title(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())[:120]


def normalize_doi(doi: str | None) -> str:
    value = (doi or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        value = value.removeprefix(prefix)
    return value


def candidate_id(paper: dict[str, Any]) -> set[str]:
    """All identifiers a candidate can be matched by."""
    ids = set()
    doi = normalize_doi(paper.get("doi"))
    if doi:
        ids.add(f"doi:{doi}")
    if paper.get("pmid"):
        ids.add(f"pmid:{str(paper['pmid']).strip()}")
    if paper.get("title"):
        ids.add(f"title:{normalize_title(paper['title'])}")
    return ids


def truth_papers(entry: dict[str, Any], strict: bool = False) -> list[set[str]]:
    """One identifier set per ClinVar-curated paper (the recall denominator)."""
    token_matched = {
        r["uid"] for r in entry.get("clinvar_records") or [] if r.get("token_match")
    }
    papers: list[set[str]] = []
    for paper in entry.get("papers") or []:
        if strict:
            sources = set(entry.get("pmid_sources", {}).get(str(paper["pmid"]), []))
            if not sources & token_matched:
                continue
        ids = candidate_id(paper)
        if ids:
            papers.append(ids)
    return papers


def hits_at_k(papers: list[dict[str, Any]], truth: list[set[str]], k: int | None) -> int:
    """Number of curated truth papers matched by the first *k* candidates."""
    subset = papers if k is None else papers[:k]
    candidate_ids = [candidate_id(p) for p in subset]
    return sum(1 for ids in truth if any(ids & cids for cids in candidate_ids))


async def main_async() -> None:
    collected = json.loads(COLLECTED_FILE.read_text())
    nested, _ = build_nested_full_pipeline(collected)
    queries = {
        json.loads(line)["variant_id"]: json.loads(line)["query"]
        for line in (GOLD_DIR / "variants.jsonl").read_text().splitlines()
        if line.strip()
    }
    clinvar = {
        entry["variant_id"]: entry
        for entry in (json.loads(line) for line in CLINVAR_FILE.read_text().splitlines() if line.strip())
    }

    m2_recorded = collected["multisource_no_translation"]
    m3_recorded = collected["multisource_with_translation"]

    def build_lexical(pool: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        out = {}
        for vid, items in pool.items():
            kept = [c for c in dedupe_candidates(items)
                    if (str(c.get("title") or "").strip() or c.get("doi") or c.get("url"))]
            out[vid] = rank_candidates(kept, expected_title=queries[vid])
        return out

    m2_lex = build_lexical(m2_recorded)
    m3_lex = build_lexical(m3_recorded)

    cache = load_cache()
    async with httpx.AsyncClient(timeout=90.0) as client:
        async def rerank(lists: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
            out = {}
            for vid, items in sorted(lists.items()):
                scores = await rerank_scores(client, queries[vid], items, cache)
                out[vid] = order_by_scores(items, scores)
            return out

        m2_rerank = await rerank(m2_lex)
        m3_rerank = await rerank(m3_lex)
    save_cache(cache)

    methods: dict[str, dict[str, list[dict[str, Any]]]] = {
        "pubmed_only": collected["pubmed_only"],
        "m2_recorded": m2_recorded,
        "m2_lex": m2_lex,
        "m2_rerank": m2_rerank,
        "m3_recorded": m3_recorded,
        "m3_lex": m3_lex,
        "m3_rerank": m3_rerank,
        "full_pipeline_nested": nested,
        "sota_dense": collected["sota_dense"],
        "sota_hybrid": collected["sota_hybrid"],
        "sota_mdense": collected["sota_mdense"],
    }

    evaluated = sorted(vid for vid, entry in clinvar.items() if entry.get("papers"))
    truth_all = {vid: truth_papers(clinvar[vid], strict=False) for vid in evaluated}
    truth_strict = {vid: truth_papers(clinvar[vid], strict=True) for vid in evaluated}

    # Temporal profile: the curated citation pool is the historical evidence
    # base, while the systems' queries are recency-oriented; the comparison is
    # only interpretable with that in view.
    def year_of(paper: dict[str, Any]) -> int | None:
        raw = str(paper.get("year") or paper.get("pub_date") or "")[:4]
        return int(raw) if raw.isdigit() else None

    truth_years = sorted(
        y for vid in evaluated for p in clinvar[vid]["papers"] if (y := year_of(p)) is not None
    )
    method_years: dict[str, dict[str, Any]] = {}
    for method, lists in methods.items():
        years = sorted(y for vid in evaluated for p in (lists.get(vid) or []) if (y := year_of(p)) is not None)
        method_years[method] = {
            "n_dated": len(years),
            "median_year": years[len(years) // 2] if years else None,
            "share_2015_or_later": round(sum(1 for y in years if y >= 2015) / len(years), 3) if years else None,
        }
    temporal = {
        "truth": {
            "n_dated": len(truth_years),
            "median_year": truth_years[len(truth_years) // 2] if truth_years else None,
            "share_2015_or_later": round(sum(1 for y in truth_years if y >= 2015) / len(truth_years), 3)
            if truth_years else None,
        },
        "methods": method_years,
        "note": (
            "ClinVar citations are the historical evidence base for a variant (functional studies, "
            "segregation analyses, older cohorts); the benchmark queries and providers skew recent. Low "
            "absolute recall on this truth therefore measures temporal reach, not clinical utility."
        ),
    }

    # Pool coverage: how much of the curated truth any method's pool contains.
    pooled_truth_coverage = {}
    for vid in evaluated:
        pool_ids: list[set[str]] = []
        for lists in methods.values():
            for paper in lists.get(vid) or []:
                pool_ids.append(candidate_id(paper))
        pooled_truth_coverage[vid] = sum(
            1 for ids in truth_all[vid] if any(ids & pids for pids in pool_ids)
        )

    def metrics_for(methods_subset: dict[str, dict[str, list[dict[str, Any]]]],
                    truth: dict[str, list[set[str]]]):
        out: dict[str, Any] = {}
        for method, lists in methods_subset.items():
            per_variant = {}
            for vid in evaluated:
                papers = lists.get(vid) or []
                rel = truth[vid]
                found = hits_at_k(papers, rel, None)
                per_variant[vid] = {
                    "set_recall": (found / len(rel)) if rel else 0.0,
                    "n_truth": len(rel),
                    "n_found": found,
                    "precision_at_20": hits_at_k(papers, rel, 20) / 20,
                    **{f"recall_at_{k}": (hits_at_k(papers, rel, k) / len(rel)) if rel else 0.0
                       for k in KS},
                }
            n = len(evaluated)
            out[method] = {
                "mean_set_recall": sum(per_variant[v]["set_recall"] for v in evaluated) / n,
                "mean_recall_at_20": sum(per_variant[v]["recall_at_20"] for v in evaluated) / n,
                "mean_recall_at_50": sum(per_variant[v]["recall_at_50"] for v in evaluated) / n,
                "mean_precision_at_20": sum(per_variant[v]["precision_at_20"] for v in evaluated) / n,
                "total_found": sum(per_variant[v]["n_found"] for v in evaluated),
                "total_truth": sum(per_variant[v]["n_truth"] for v in evaluated),
                "per_variant": per_variant,
            }
        return out

    results_all = metrics_for(methods, truth_all)
    results_strict = metrics_for(methods, truth_strict)

    def series(block: dict[str, Any], method: str, kind: str) -> list[float]:
        per_variant = block[method]["per_variant"]
        key = "set_recall" if kind == "set" else f"recall_at_{kind}"
        return [per_variant[v][key] for v in evaluated]

    tests: dict[str, dict[str, Any]] = {}
    for name, base, improved, kind in DECLARED:
        result = paired_test(series(results_all, base, kind), series(results_all, improved, kind))
        result.update({"base": base, "improved": improved, "metric": kind})
        tests[name] = result
    holm_correction(tests)

    artifact = {
        "schema_version": 1,
        "protocol": {
            "plan_reference": "paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md §2.2 (E2a)",
            "ground_truth": "ClinVar submitter assertion citations (gold_standard/variants_clinvar.jsonl)",
            "matching": "DOI, PMID or normalised title; candidate lists are heterogeneous",
            "variants_evaluated": evaluated,
            "n_variants": len(evaluated),
            "variants_without_clinvar_record": sorted(set(queries) - set(evaluated)),
            "no_live_retrieval": True,
            "sensitivity": "clinvar_strict restricts truth to records whose title contains the variant token",
            "cache": warm_cache_size(cache),
        },
        "truth_sizes": {
            vid: {"all": len(truth_all[vid]), "strict": len(truth_strict[vid]),
                  "in_pooled_candidates": pooled_truth_coverage[vid]}
            for vid in evaluated
        },
        "pool_coverage": {
            "truth_papers_total": sum(len(truth_all[v]) for v in evaluated),
            "truth_papers_found_by_some_method": sum(pooled_truth_coverage.values()),
            "note": (
                "Only (variant, paper) matches that some method retrieved can count toward recall; the "
                "ceiling is set by the pooled candidate coverage, not by the reference set."
            ),
        },
        "method_labels": METHOD_LABELS,
        "temporal_profile": temporal,
        "results_all_citations": {m: {k: v for k, v in block.items() if k != "per_variant"}
                                  for m, block in results_all.items()},
        "results_strict_citations": {m: {k: v for k, v in block.items() if k != "per_variant"}
                                     for m, block in results_strict.items()},
        "tests_holm_all_citations": tests,
    }
    OUT_FILE.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))

    print(f"variants evaluated: {len(evaluated)} (no ClinVar record: "
          f"{len(set(queries) - set(evaluated))})")
    coverage = artifact["pool_coverage"]
    print(f"curated citations: {coverage['truth_papers_total']} (variant,paper) pairs, "
          f"{coverage['truth_papers_found_by_some_method']} present in some method's candidates")
    for label, block in (("ALL CITATIONS", results_all), ("STRICT (token-matched records)", results_strict)):
        print(f"\n=== {label} ===")
        print("| Method | SetRecall | Recall@20 | Recall@50 | Prec@20 | Found |")
        print("| --- | --- | --- | --- | --- | --- |")
        for method in METHOD_LABELS:
            r = block[method]
            print(f"| {METHOD_LABELS[method]} | {r['mean_set_recall']:.3f} | {r['mean_recall_at_20']:.3f} "
                  f"| {r['mean_recall_at_50']:.3f} | {r['mean_precision_at_20']:.3f} "
                  f"| {r['total_found']}/{r['total_truth']} |")
    print("\n=== declared family (all citations) ===")
    print("| Test | Δ | p | p(Holm) | r | sig |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, t in tests.items():
        print(f"| {name} | {t['delta']:+.4f} | {t['p']:.4f} | {t['p_holm']:.4f} | {t['r']:+.2f} "
              f"| {'yes' if t['significant_holm'] else 'no'} |")
    print(f"\nwrote {OUT_FILE}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
