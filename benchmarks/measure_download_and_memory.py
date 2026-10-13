"""Measure open-access download throughput and process memory.

Implements the supplementary performance measurement required by
``paper/BIOINFORMATICS_PLAN.md`` (section 7.1, item 3). The benchmark
collection runs recorded search-phase wall clock only; download throughput
and peak memory had no artifact at all.

What this measures
------------------
For a small, deterministic sample of reference-set variants it runs the real
acquisition workflow with ``action="download"`` and records:

* wall clock for the whole call (search + download phases),
* how many candidate links were discovered vs how many PDFs actually landed,
* bytes and PDFs-per-minute for the download phase,
* peak RSS of the process (``ru_maxrss``).

Deliberate limitations (also written into the artifact)
-------------------------------------------------------
* The relevance gate is **off** so the number is comparable across runs; the
  gate's LLM latency and false-positive rate are *not* measured here.
* Downloaded PDFs go to a temporary directory that is deleted afterwards;
  only metadata is kept.
* Single session, live APIs: provider rate limiting and network variance are
  part of the measurement, as in the collection runs.
* ``LIT_UNPAYWALL_EMAIL`` decides whether the DOI->OA route is available;
  per-download ``source`` values make the mix auditable.

Usage::

    set -a; source .env; set +a
    python benchmarks/measure_download_and_memory.py --variants 4 --limit 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import shutil
import statistics as st
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lit_acquisition import online_acquisition_workflow  # noqa: E402

GOLD = ROOT / "benchmarks" / "gold_standard" / "variants.jsonl"
OUT = ROOT / "benchmarks" / "results" / "download_throughput.json"


def _peak_rss_mb() -> float:
    """Peak resident set size of this process in MiB (Linux: ru_maxrss is KiB)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _load_variants(n: int) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    with GOLD.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                variants.append(json.loads(line))
    return variants[:n]


def _as_dict(entry: Any) -> dict[str, Any]:
    """Download records may be dicts or dataclasses depending on the version."""
    if isinstance(entry, dict):
        return entry
    return {
        field: getattr(entry, field, None)
        for field in ("file_path", "source", "doi", "pmcid", "url", "warnings")
    }


async def _measure_variant(variant: dict[str, Any], limit: int, download_path: Path) -> dict[str, Any]:
    query = variant.get("query") or f"{variant['gene']} {variant['disease']}"
    started = time.monotonic()
    try:
        result = await online_acquisition_workflow(
            {
                "query": query,
                "action": "download",
                "limit": limit,
                "language": "en",
                "download_path": str(download_path),
                "relevance_gate": False,
                "compact": False,  # keep candidate_links: they are the success-rate denominator
                "timeout": 90,
            }
        )
    except Exception as exc:  # noqa: BLE001 - recorded as a failure datum
        return {
            "variant_id": variant["variant_id"],
            "query": query,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.monotonic() - started, 2),
            "candidates": 0,
            "downloads": 0,
            "bytes": 0,
            "sources": {},
        }
    elapsed = time.monotonic() - started

    downloads = [_as_dict(entry) for entry in (result.get("downloads") or [])]
    sizes = []
    sources: dict[str, int] = {}
    for record in downloads:
        path = record.get("file_path")
        if path and Path(path).exists():
            sizes.append(Path(path).stat().st_size)
        source = record.get("source") or "unknown"
        sources[source] = sources.get(source, 0) + 1

    candidates = result.get("candidate_links") or []
    warnings = result.get("warnings") or []
    return {
        "variant_id": variant["variant_id"],
        "query": query,
        "elapsed_s": round(elapsed, 2),
        "candidates": len(candidates),
        "downloads": len(downloads),
        "bytes": sum(sizes),
        "sources": sources,
        "success": bool(result.get("success")),
        "summary": result.get("summary", ""),
        "warnings": [str(w) for w in warnings][:12],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", type=int, default=4, help="number of reference-set variants to run")
    parser.add_argument("--limit", type=int, default=8, help="candidate budget per variant")
    args = parser.parse_args()

    variants = _load_variants(args.variants)
    if not variants:
        print("no variants loaded", file=sys.stderr)
        return 1

    tmpdir = Path(tempfile.mkdtemp(prefix="litacq-throughput-"))
    print(f"measuring {len(variants)} variants (limit={args.limit}) into {tmpdir}")
    rows: list[dict[str, Any]] = []
    try:
        for variant in variants:
            row = await _measure_variant(variant, args.limit, tmpdir)
            rows.append(row)
            print(
                f"  {row['variant_id']}: {row['downloads']}/{row['candidates']} PDFs "
                f"in {row['elapsed_s']}s ({row['bytes']} B)"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    total_pdfs = sum(row["downloads"] for row in rows)
    total_candidates = sum(row["candidates"] for row in rows)
    total_bytes = sum(row["bytes"] for row in rows)
    total_seconds = sum(row["elapsed_s"] for row in rows)
    download_rate = (total_pdfs / total_seconds * 60.0) if total_seconds else 0.0

    artifact = {
        "schema_version": 1,
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "command": (
                "python benchmarks/measure_download_and_memory.py "
                f"--variants {args.variants} --limit {args.limit}"
            ),
            "reference_set": "benchmarks/gold_standard/variants.jsonl",
            "note": (
                "Wall clock covers search + download phases for one call per variant. "
                "Live APIs; relevance gate disabled; PDFs written to a temporary "
                "directory and deleted after measuring."
            ),
        },
        "config": {
            "variants": [row["variant_id"] for row in rows],
            "candidate_budget_per_variant": args.limit,
            "language": "en",
            "relevance_gate": False,
        },
        "per_variant": rows,
        "aggregate": {
            "n_variants": len(rows),
            "candidates_total": total_candidates,
            "pdfs_total": total_pdfs,
            "pdf_success_rate": round(total_pdfs / total_candidates, 4) if total_candidates else 0.0,
            "bytes_total": total_bytes,
            "seconds_total": round(total_seconds, 2),
            "pdfs_per_minute": round(download_rate, 2),
            "seconds_per_pdf": round(total_seconds / total_pdfs, 2) if total_pdfs else None,
            "peak_rss_mb": round(_peak_rss_mb(), 1),
            "median_variant_seconds": round(st.median([r["elapsed_s"] for r in rows]), 2),
        },
        "caveats": [
            "Small sample (see config.variants); not a full-corpus throughput benchmark.",
            "Relevance-gate latency and false-positive rate are NOT measured here.",
            "Unpaywall's DOI->OA route requires LIT_UNPAYWALL_EMAIL; when unset only "
            "PMC/EuropePMC and direct-URL routes can succeed.",
            "Provider rate limits are included in the wall clock, as in the collection runs.",
        ],
    }
    OUT.write_text(json.dumps(artifact, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nartifact -> {OUT.relative_to(ROOT)}")
    print(json.dumps(artifact["aggregate"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
