"""E4b: measured precision/recall cost of the LLM relevance gate.

Plan reference: ``paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md`` section 4.2 (E4b).

The benchmark ran with ``relevance_gate=False``, so the gate that Figure 1
advertises had no measured numbers. This script runs the *real* gate
(``lit_acquisition.llm.gate.run_relevance_gate``, same prompt and model) over a
deterministic sample of candidate papers and reports:

* how many papers the gate drops, and the resulting Precision@Retrieved before
  and after;
* the false-negative rate --- reference-set papers the gate would have silently
  removed --- which is the risk that matters for a clinical literature tool;
* cost proxies: wall-clock per paper and prompt size (the gate API does not
  return token usage, so characters are reported instead of tokens).

Because 225 full texts are not available, the gate is fed PubMed **abstracts**
through its ``parsed_markdown`` input. This is stated as a limitation: the
production gate reads PDF excerpts, and abstracts are a shorter, noisier input.

Truth labels for this measurement are the project's own heuristic (positive =
present in the frozen reference set; negative = retrieved by some system but
scored at or below the documented threshold). They are provisional: once the
E2b sheets are filled, re-run with ``--truth e2b`` to use the human labels.

Output: ``benchmarks/results/gate_eval.json``. Abstract texts are cached in
``benchmarks/results/abstracts_cache.json``.

Usage::

    python benchmarks/analysis/gate_eval.py [--n-per-class 60] [--truth heuristic|e2b]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from analysis.nested import build_nested_full_pipeline  # noqa: E402
from analysis.pooled_gold import candidate_pool  # noqa: E402
from gold_standard.build_variants import DISEASE_KEYWORDS, _score_title  # noqa: E402
from metrics import paper_id  # noqa: E402

RESULTS_DIR = BENCH_DIR / "results"
ANNOTATION_DIR = BENCH_DIR / "annotation"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
ABSTRACT_CACHE = RESULTS_DIR / "abstracts_cache.json"
OUT_FILE = RESULTS_DIR / "gate_eval.json"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
SEED = 20261014


def load_variants() -> dict[str, dict[str, Any]]:
    return {
        json.loads(line)["variant_id"]: json.loads(line)
        for line in (BENCH_DIR / "gold_standard" / "variants.jsonl").read_text().splitlines()
        if line.strip()
    }


def fetch_abstracts(pmids: list[str], cache: dict[str, str]) -> None:
    missing = [p for p in pmids if p and p not in cache]
    for start in range(0, len(missing), 100):
        chunk = missing[start : start + 100]
        if not chunk:
            continue
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(EFETCH, data={"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"})
            resp.raise_for_status()
        root = ET.fromstring(resp.text)
        for article in root.iter("PubmedArticle"):
            pmid_el = article.find(".//PMID")
            if pmid_el is None or not pmid_el.text:
                continue
            texts = []
            for abstract in article.iter("AbstractText"):
                label = abstract.attrib.get("Label")
                body = "".join(abstract.itertext()).strip()
                texts.append(f"{label}: {body}" if label else body)
            title_el = article.find(".//ArticleTitle")
            title = "".join(title_el.itertext()).strip() if title_el is not None else ""
            cache[pmid_el.text] = (title + "\n" + " ".join(texts)).strip()
        print(f"  fetched abstracts for {len(chunk)} PMIDs ({len(cache)} cached)")
        time.sleep(0.4)


def build_sample(n_per_class: int) -> list[dict[str, Any]]:
    """Deterministic, variant-stratified sample of labelled candidates.

    Candidates need a PMID (the gate input is the PubMed abstract); abstracts
    themselves are fetched afterwards, only for the sampled papers.
    """
    rng = random.Random(SEED)
    variants = load_variants()
    collected = json.loads(COLLECTED_FILE.read_text())
    nested, _ = build_nested_full_pipeline(collected)
    pools = {**collected, "full_pipeline_nested": nested}

    positives: dict[str, list[dict[str, Any]]] = {}
    negatives: dict[str, list[dict[str, Any]]] = {}
    for vid, variant in variants.items():
        positives[vid] = [p for p in variant.get("relevant_papers") or [] if str(p.get("pmid") or "")]
        existing = {paper_id(p) for p in variant.get("relevant_papers") or []}
        keywords = DISEASE_KEYWORDS.get(variant["gene"], [])
        pool = candidate_pool(pools, vid)
        negs = []
        for pid, paper in pool.items():
            if pid in existing or not str(paper.get("pmid") or ""):
                continue
            score, _reason = _score_title(paper.get("title") or "", variant["gene"], variant["variant"],
                                          keywords, [])
            if score <= 4:
                negs.append(paper)
        negatives[vid] = negs
        rng.shuffle(positives[vid])
        rng.shuffle(negatives[vid])

    sample: list[dict[str, Any]] = []
    for label, buckets, target in (("positive", positives, n_per_class), ("negative", negatives, n_per_class)):
        vids = sorted(buckets)
        cursor = {vid: 0 for vid in vids}
        picked = 0
        while picked < target:
            progressed = False
            for vid in vids:
                if picked >= target:
                    break
                index = cursor[vid]
                if index < len(buckets[vid]):
                    paper = buckets[vid][index]
                    cursor[vid] += 1
                    pid = paper_id(paper)
                    if any(item["paper_id"] == pid and item["variant_id"] == vid for item in sample):
                        continue
                    sample.append(
                        {
                            "variant_id": vid,
                            "query": variants[vid]["query"],
                            "paper_id": pid,
                            "pmid": str(paper.get("pmid") or ""),
                            "doi": paper.get("doi") or "",
                            "title": paper.get("title") or "",
                            "heuristic_label": label,
                            "language": paper.get("language"),
                        }
                    )
                    picked += 1
                    progressed = True
            if not progressed:
                break
    return sample


def compute_metrics(rows: list[dict[str, Any]], truth_key: str) -> dict[str, Any]:
    kept_relevant = sum(1 for r in rows if r.get("gate_relevant") and r[truth_key])
    dropped_relevant = sum(1 for r in rows if not r.get("gate_relevant") and r[truth_key])
    kept_irrelevant = sum(1 for r in rows if r.get("gate_relevant") and not r[truth_key])
    dropped_irrelevant = sum(1 for r in rows if not r.get("gate_relevant") and not r[truth_key])
    n = len(rows)
    return {
        "n": n,
        "kept_relevant": kept_relevant,
        "dropped_relevant": dropped_relevant,
        "kept_irrelevant": kept_irrelevant,
        "dropped_irrelevant": dropped_irrelevant,
        "false_negative_rate": round(dropped_relevant / (kept_relevant + dropped_relevant), 4)
        if (kept_relevant + dropped_relevant) else None,
        "false_positive_rate": round(kept_irrelevant / (kept_irrelevant + dropped_irrelevant), 4)
        if (kept_irrelevant + dropped_irrelevant) else None,
        "precision_at_retrieved_before": round((kept_relevant + dropped_relevant) / n, 4) if n else None,
        "precision_at_retrieved_after": round(kept_relevant / max(1, kept_relevant + kept_irrelevant), 4),
        "retained_fraction": round((kept_relevant + kept_irrelevant) / n, 4) if n else None,
        "errors": sum(1 for r in rows if r.get("gate_error")),
    }


async def main_async() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-per-class", type=int, default=60)
    parser.add_argument("--truth", choices=("heuristic", "e2b"), default="heuristic")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--backoff", type=float, default=20.0, help="seconds, multiplied by the attempt number")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    abstracts: dict[str, str] = json.loads(ABSTRACT_CACHE.read_text()) if ABSTRACT_CACHE.exists() else {}
    cache_before = len(abstracts)

    sample = build_sample(args.n_per_class)
    fetch_abstracts(sorted({s["pmid"] for s in sample if s["pmid"]}), abstracts)
    ABSTRACT_CACHE.write_text(json.dumps(abstracts, ensure_ascii=False))
    n_with_abstract = sum(1 for s in sample if abstracts.get(s["pmid"], "").strip())
    print(f"sample: {len(sample)} papers, {n_with_abstract} with a fetched abstract "
          f"({sum(1 for s in sample if s['heuristic_label'] == 'positive')} positives, "
          f"{sum(1 for s in sample if s['heuristic_label'] == 'negative')} negatives); "
          f"abstract cache {cache_before} -> {len(abstracts)}")

    from lit_acquisition.llm.gate import _SYSTEM_PROMPT, _USER_PROMPT, _parse_response

    # The gate is run per variant query (as in production): every sampled paper
    # of a variant is judged against that variant's query.
    by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in sample:
        by_variant[item["variant_id"]].append(item)

    import os

    base_url = (args.base_url or os.getenv("LIT_LLM_BASE_URL", "")).rstrip("/")
    api_key = args.api_key or os.getenv("LIT_LLM_API_KEY", "")
    model = args.model or os.getenv("LIT_LLM_MODEL", "gpt-5")
    if not base_url or not api_key:
        raise SystemExit("no LLM endpoint configured (LIT_LLM_BASE_URL / LIT_LLM_API_KEY)")

    async def judge(client: httpx.AsyncClient, query: str, item: dict[str, Any]) -> dict[str, Any]:
        """One gate verdict using the production prompt, with bounded retries.

        The production ``run_relevance_gate`` path is not used directly so that
        provider throttling can be handled explicitly (long back-off, and an
        honest "provider unavailable" status instead of treating errors as
        relevant) and so that token usage can be recorded when reported.
        """
        text = abstracts.get(item["pmid"], "")
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _USER_PROMPT.format(
                    query=query, lang=item.get("language") or "", title=item["title"],
                    max_pages=0, text=text[:6000],
                ),
            },
        ]
        last: str = ""
        for attempt in range(args.retries):
            try:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": model, "messages": messages, "max_tokens": 400},
                )
                if resp.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {resp.status_code}"
                    wait = args.backoff * (attempt + 1)
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        wait = max(wait, float(retry_after))
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                raw = (payload["choices"][0]["message"]["content"] or "").strip()
                relevant, doc_type, reason, parse_error = _parse_response(raw)
                usage = payload.get("usage") or {}
                return {
                    "relevant": relevant,
                    "doc_type": doc_type,
                    "reason": reason,
                    "error": parse_error,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                }
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(args.backoff * (attempt + 1))
        return {"relevant": False, "doc_type": "", "reason": "", "error": last or "unknown_error",
                "prompt_tokens": None, "completion_tokens": None}

    semaphore = asyncio.Semaphore(args.concurrency)

    async def guarded(client: httpx.AsyncClient, query: str, item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await judge(client, query, item)

    started = time.monotonic()
    prompt_chars = 0
    async with httpx.AsyncClient(timeout=120.0) as client:
        for vid, items in sorted(by_variant.items()):
            prompt_chars += sum(len(abstracts.get(i["pmid"], "")) + len(i["title"]) for i in items)
            verdicts = await asyncio.gather(*(guarded(client, items[0]["query"], i) for i in items))
            for item, verdict in zip(items, verdicts):
                item.update(
                    gate_relevant=verdict["relevant"] if not verdict["error"] else None,
                    gate_error=verdict["error"],
                    gate_reason=verdict["reason"],
                    gate_doc_type=verdict["doc_type"],
                    prompt_tokens=verdict["prompt_tokens"],
                    completion_tokens=verdict["completion_tokens"],
                )
    elapsed = time.monotonic() - started

    for item in sample:
        item["heuristic_truth"] = item["heuristic_label"] == "positive"

    e2b_truth: dict[str, bool] = {}
    sheets = [ANNOTATION_DIR / "e2b_sheet_adjudicated.csv", ANNOTATION_DIR / "e2b_sheet_raterA.csv"]
    for sheet in sheets:
        if not sheet.exists():
            continue
        import csv as _csv

        with sheet.open() as handle:
            for row in _csv.DictReader(handle):
                if (row.get("variant_level_evidence") or "").strip() in ("0", "1"):
                    e2b_truth[row["sample_id"]] = row["variant_level_evidence"].strip() == "1"
        break
    if args.truth == "e2b" and e2b_truth:
        for item in sample:
            key = f"{item['variant_id']}"
            item["e2b_truth"] = e2b_truth.get(key)
        truth_key = "e2b_truth"
        usable = [r for r in sample if r.get("e2b_truth") is not None]
    else:
        truth_key = "heuristic_truth"
        usable = sample

    judged = [r for r in usable if not r.get("gate_error")]
    error_rate = 1 - len(judged) / max(1, len(usable))
    provider_ok = error_rate <= 0.2
    metrics = compute_metrics(judged, truth_key) if provider_ok else None
    artifact = {
        "schema_version": 1,
        "protocol": {
            "plan_reference": "paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md §4.2 (E4b)",
            "gate": "lit_acquisition.llm.gate.run_relevance_gate (production prompt and model)",
            "input": "PubMed abstracts passed through the gate's parsed_markdown field (not PDFs)",
            "truth": (
                "project heuristic (provisional): positive = in the frozen reference set, negative = "
                "retrieved by some system and scored at or below the documented threshold"
                if truth_key == "heuristic_truth" else "E2b human annotation (variant-level evidence)"
            ),
            "sample_seed": SEED,
            "sampling": "variant-stratified round-robin over all 43 variants, then shuffled",
            "cost_note": "the gate API does not return token usage; characters and wall-clock are reported",
        },
        "status": "ok" if provider_ok else "provider_unavailable",
        "error_rate": round(error_rate, 4),
        "model": model,
        "base_url": base_url,
        "model_is_production_default": model == os.getenv("LIT_LLM_MODEL", "gpt-5"),
        "sample_size": len(sample),
        "n_judged": len(judged),
        "sample_with_abstract": sum(1 for s in sample if abstracts.get(s["pmid"], "").strip()),
        "metrics": metrics,
        "cost": {
            "wall_clock_seconds": round(elapsed, 2),
            "ms_per_paper": round(elapsed * 1000 / max(1, len(sample)), 1),
            "mean_prompt_chars": round(prompt_chars / max(1, len(sample)), 1),
        },
        "by_variant": {
            vid: {
                "n": len(items),
                "gate_relevant": sum(1 for i in items if i.get("gate_relevant")),
            }
            for vid, items in sorted(by_variant.items())
        },
        "decisions": [
            {k: item[k] for k in ("variant_id", "paper_id", "pmid", "title", "heuristic_label",
                                  "gate_relevant", "gate_reason", "gate_doc_type", "gate_error")}
            for item in sample
        ],
    }
    OUT_FILE.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))

    if metrics is None:
        print(f"\nstatus: provider_unavailable (error rate {error_rate:.2%}); no metrics computed")
    else:
        print(f"\ngate metrics (truth={truth_key}): {json.dumps(metrics, indent=1)}")
    print(f"cost: {artifact['cost']}")
    print(f"wrote {OUT_FILE}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
