"""Compose the 43-variant full-pipeline wall clock from the recorded phases.

The 2026-08-14 collection session (``benchmarks/results/run_manifest.json``)
recorded wall clock per method per variant, but the full pipeline was built as
*frozen M3 list + citation traversal*, so it has no single end-to-end number.
Its cost is the translation-phase run plus the citation phase, both measured in
that same session. This script composes them and writes a small artifact so the
number is reproducible instead of being retyped into the paper.

Deliberately *not* a fresh live run: a new session would measure today's API
latency and would not correspond to the frozen benchmark lists.

Usage::

    python benchmarks/compose_timing.py
"""

from __future__ import annotations

import json
import statistics as st
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "results" / "run_manifest.json"
OUT = ROOT / "benchmarks" / "results" / "timing_43variants.json"


def _stats(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
    return {
        "total_s": round(sum(values), 1),
        "mean_s": round(st.mean(values), 2),
        "median_s": round(st.median(values), 2),
        "p90_s": round(p90, 2),
        "max_s": round(max(values), 2),
        "min_s": round(min(values), 2),
    }


def main() -> int:
    manifest: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    per_variant: dict[str, dict[str, float]] = manifest["per_variant_wall_s"]

    def numeric(method: str, variant: str) -> float | None:
        value = per_variant.get(method, {}).get(variant)
        return float(value) if isinstance(value, (int, float)) else None

    variants = sorted(
        v
        for v in per_variant.get("multisource_with_translation", {})
        if numeric("multisource_with_translation", v) is not None
    )

    phases: dict[str, dict[str, float]] = {}
    for method in ("pubmed_only", "multisource_no_translation", "multisource_with_translation"):
        values = [numeric(method, v) for v in variants]
        phases[method] = _stats([v for v in values if v is not None])

    citation_values = [numeric("full_pipeline_citation_phase", v) for v in variants]
    phases["full_pipeline_citation_phase"] = _stats([v for v in citation_values if v is not None])

    composed: list[float] = []
    for variant in variants:
        translated = numeric("multisource_with_translation", variant)
        citation = numeric("full_pipeline_citation_phase", variant)
        if translated is not None and citation is not None:
            composed.append(translated + citation)

    artifact = {
        "schema_version": 1,
        "composed_at_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "source": "benchmarks/results/run_manifest.json",
            "collection_window_utc": [
                manifest.get("started_at_utc"),
                manifest.get("finished_at_utc"),
            ],
            "command": manifest.get("command_args"),
            "note": (
                "Full pipeline = translation-phase run (multisource_with_translation) + "
                "citation phase, both measured in the same 43-variant session. Composition, "
                "not a fresh end-to-end run."
            ),
        },
        "n_variants": len(variants),
        "phases": phases,
        "composed_full_pipeline": _stats(composed) if composed else {},
        "caveats": [
            "Wall clock includes provider rate-limit backoff and network variance.",
            "No memory or download-throughput data here; see download_throughput.json.",
            "A fresh live run would not correspond to the frozen benchmark lists.",
        ],
    }
    OUT.write_text(json.dumps(artifact, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"artifact -> {OUT.relative_to(ROOT)}")
    print(json.dumps({k: artifact[k] for k in ("n_variants", "composed_full_pipeline")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
