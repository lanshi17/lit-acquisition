"""Collect external single-source baselines.

Queries each provider's public REST API directly with the gold-standard
English query (no LLM, no federation, no citation graph) so the paper can
report transparent external reference points and explicit fusion baselines
(union / Reciprocal Rank Fusion) over per-source ranked lists.

Sources (no API key required):
    - EuropePMC REST
    - OpenAlex
    - Crossref
    - Semantic Scholar Graph (best effort; heavy anonymous rate limiting --
      failures are recorded and the source is marked unavailable)

Usage:
    python benchmarks/collect_external_baselines.py [--limit 50]

Output:
    benchmarks/results/external_baselines.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

BENCH_DIR = Path(__file__).parent
RESULTS_DIR = BENCH_DIR / "results"
GOLD_FILE = BENCH_DIR / "gold_standard" / "variants.jsonl"
OUT_FILE = RESULTS_DIR / "external_baselines.json"

MAILTO = "benchmark@example.org"  # polite-pool identification
TIMEOUT = 30.0


def load_variants() -> list[dict[str, Any]]:
    out = []
    with open(GOLD_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _get_json(url: str, params: dict[str, Any], retries: int = 4) -> Any:
    delay = 2.0
    last: Exception | None = None
    for _ in range(retries):
        try:
            r = httpx.get(url, params=params, timeout=TIMEOUT,
                          headers={"User-Agent": f"lit-acquisition-benchmark (mailto:{MAILTO})"})
            if r.status_code == 429:
                time.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"request failed after {retries} attempts: {last}")


def search_europepmc(query: str, limit: int) -> list[dict[str, Any]]:
    data = _get_json(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        {"query": query, "format": "json", "pageSize": limit},
    )
    items = []
    for rec in (data.get("resultList") or {}).get("result", [])[:limit]:
        items.append({
            "doi": rec.get("doi"),
            "pmid": rec.get("pmid"),
            "title": rec.get("title"),
            "year": str(rec.get("pubYear") or "") or None,
            "language": (rec.get("language") or None),
            "source": "europepmc_direct",
        })
    return items


def search_openalex(query: str, limit: int) -> list[dict[str, Any]]:
    data = _get_json(
        "https://api.openalex.org/works",
        {"search": query, "per-page": min(limit, 200), "mailto": MAILTO},
    )
    items = []
    for rec in data.get("results", [])[:limit]:
        doi = rec.get("doi") or ""
        items.append({
            "doi": doi.removeprefix("https://doi.org/") or None,
            "title": rec.get("display_name"),
            "year": str(rec.get("publication_year") or "") or None,
            "language": rec.get("language"),
            "source": "openalex_direct",
        })
    return items


def search_crossref(query: str, limit: int) -> list[dict[str, Any]]:
    data = _get_json(
        "https://api.crossref.org/works",
        {"query": query, "rows": limit, "mailto": MAILTO},
    )
    items = []
    for rec in (data.get("message") or {}).get("items", [])[:limit]:
        title = (rec.get("title") or [None])[0]
        issued = ((rec.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        items.append({
            "doi": rec.get("DOI"),
            "title": title,
            "year": str(issued) if issued else None,
            "language": rec.get("language"),
            "source": "crossref_direct",
        })
    return items


def search_semantic_scholar(query: str, limit: int) -> list[dict[str, Any]]:
    data = _get_json(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        {"query": query, "limit": min(limit, 100), "fields": "title,year,externalIds"},
    )
    items = []
    for rec in data.get("data", [])[:limit]:
        ext = rec.get("externalIds") or {}
        items.append({
            "doi": ext.get("DOI"),
            "pmid": ext.get("PubMed"),
            "title": rec.get("title"),
            "year": str(rec.get("year") or "") or None,
            "language": None,
            "source": "semantic_scholar_direct",
        })
    return items


SOURCES = {
    "europepmc_direct": search_europepmc,
    "openalex_direct": search_openalex,
    "crossref_direct": search_crossref,
    "semantic_scholar_direct": search_semantic_scholar,
}

# Minimum seconds between consecutive requests per source (politeness /
# anonymous rate limits; Semantic Scholar throttles hard without a key).
SOURCE_DELAY = {
    "europepmc_direct": 0.5,
    "openalex_direct": 0.3,
    "crossref_direct": 0.5,
    "semantic_scholar_direct": 3.0,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    variants = load_variants()
    started = datetime.now(timezone.utc).isoformat()

    results: dict[str, dict[str, list[dict[str, Any]]]] = {}
    failures: dict[str, list[str]] = {}
    timings: dict[str, float] = {}

    for source, fn in SOURCES.items():
        print(f"\n=== {source} ===", flush=True)
        per_variant: dict[str, list[dict[str, Any]]] = {}
        fails: list[str] = []
        consecutive = 0
        t0 = time.monotonic()
        for v in variants:
            vid = v["variant_id"]
            try:
                items = fn(v["query"], args.limit)
                consecutive = 0
            except Exception as exc:  # noqa: BLE001
                print(f"  [{source}] {vid}: FAILED ({exc})", flush=True)
                fails.append(vid)
                items = []
                consecutive += 1
                if consecutive >= 5:
                    print(f"  [{source}] 5 consecutive failures -- marking "
                          f"source unavailable and skipping remaining variants",
                          flush=True)
                    for rest in variants[variants.index(v) + 1:]:
                        fails.append(rest["variant_id"])
                        per_variant[rest["variant_id"]] = []
                    break
            per_variant[vid] = items
            print(f"  [{source}] {vid}: {len(items)}", flush=True)
            time.sleep(SOURCE_DELAY[source])
        timings[source] = round(time.monotonic() - t0, 1)
        results[source] = per_variant
        failures[source] = fails

    payload = {
        "collected_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "limit": args.limit,
        "note": (
            "Direct single-source REST queries with the gold English query; "
            "default API relevance ranking; no LLM, no federation. Collected "
            "on a later date than the main runs (disclosed in the paper)."
        ),
        "failures": failures,
        "wall_s": timings,
        "results": results,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {OUT_FILE}")
    for source, fails in failures.items():
        total = sum(len(v) for v in results[source].values())
        print(f"  {source}: {total} items, {len(fails)} failed variants")


if __name__ == "__main__":
    main()
