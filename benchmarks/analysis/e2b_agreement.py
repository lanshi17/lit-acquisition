"""E2b analysis: inter-rater agreement and heuristic accuracy on the sample.

Plan reference: ``paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md`` section 2.3 (E2b).

Reads the two completed annotation sheets produced by
``benchmarks/annotation/build_e2b_sample.py`` and reports:

* Cohen's kappa for Level 1 (topic relevance) and Level 2 (variant-level
  evidence), plus raw agreement and the confusion matrix;
* the heuristic's precision and recall against each rater and against the
  consensus of agreeing raters (disagreements listed for adjudication);
* the estimated false-negative rate of the heuristic, i.e. how often a paper the
  heuristic *rejected* is judged Level 2 evidence by both raters.

A third, adjudicated sheet can be supplied with ``--adjudicated``.

Output: ``benchmarks/results/e2b_agreement.json`` (and a console report).

Usage::

    python benchmarks/analysis/e2b_agreement.py
    python benchmarks/analysis/e2b_agreement.py --adjudicated benchmarks/annotation/e2b_sheet_adjudicated.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

REPO_ROOT = BENCH_DIR.parent
ANNOTATION_DIR = BENCH_DIR / "annotation"
RESULTS_DIR = BENCH_DIR / "results"
OUT_FILE = RESULTS_DIR / "e2b_agreement.json"

LEVELS = ("topic_relevant", "variant_level_evidence")



def display_path(value: str) -> str:
    """Record an input as a repo-relative path so artifacts carry no local home."""
    path = Path(value)
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def read_sheet(path: Path) -> dict[str, dict[str, str]]:
    with path.open() as handle:
        return {row["sample_id"]: row for row in csv.DictReader(handle)}


def read_key(path: Path) -> dict[str, str]:
    with path.open() as handle:
        return {row["sample_id"]: row["stratum_label"] for row in csv.DictReader(handle)}


def as_bool(value: str | None) -> bool | None:
    text = (value or "").strip().lower()
    if text in ("1", "true", "yes", "y", "t"):
        return True
    if text in ("0", "false", "no", "n", "f"):
        return False
    return None


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    n = len(pairs)
    if not n:
        return {"n": 0, "kappa": None, "agreement": None, "matrix": {}}
    matrix: Counter[tuple[bool, bool]] = Counter(pairs)
    agree = sum(count for (a, b), count in matrix.items() if a == b)
    po = agree / n
    pa_true = sum(count for (a, _), count in matrix.items() if a) / n
    pb_true = sum(count for (_, b), count in matrix.items() if b) / n
    pe = pa_true * pb_true + (1 - pa_true) * (1 - pb_true)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    return {
        "n": n,
        "kappa": round(kappa, 4),
        "agreement": round(po, 4),
        "matrix": {
            f"raterA={'1' if a else '0'},raterB={'1' if b else '0'}": count
            for (a, b), count in sorted(matrix.items())
        },
    }


def heuristic_metrics(labels: dict[str, str], truth: dict[str, bool]) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    for sample_id, value in truth.items():
        positive = labels.get(sample_id) == "heuristic_positive"
        if positive and value:
            tp += 1
        elif positive and not value:
            fp += 1
        elif not positive and value:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "false_negative_rate": round(fn / (fn + tp), 4) if (fn + tp) else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet-a", default=str(ANNOTATION_DIR / "e2b_sheet_raterA.csv"))
    parser.add_argument("--sheet-b", default=str(ANNOTATION_DIR / "e2b_sheet_raterB.csv"))
    parser.add_argument("--key", default=str(ANNOTATION_DIR / "e2b_key.csv"))
    parser.add_argument("--adjudicated", default=None)
    args = parser.parse_args()

    sheet_a = read_sheet(Path(args.sheet_a))
    sheet_b = read_sheet(Path(args.sheet_b))
    labels = read_key(Path(args.key))
    adjudicated = read_sheet(Path(args.adjudicated)) if args.adjudicated else None

    report: dict[str, Any] = {"schema_version": 1,
                              "sheets": {"a": display_path(args.sheet_a),
                                         "b": display_path(args.sheet_b)},
                              "n_items": len(labels), "levels": {}}
    consensus: dict[str, dict[str, bool]] = {level: {} for level in LEVELS}
    disagreements: list[dict[str, Any]] = []
    incomplete = 0

    for level in LEVELS:
        pairs = []
        for sample_id in labels:
            a = as_bool(sheet_a.get(sample_id, {}).get(level))
            b = as_bool(sheet_b.get(sample_id, {}).get(level))
            if a is None or b is None:
                incomplete += 1
                continue
            pairs.append((a, b))
            if a == b:
                consensus[level][sample_id] = a
            else:
                disagreements.append({"sample_id": sample_id, "level": level, "raterA": a, "raterB": b})
        report["levels"][level] = cohen_kappa(pairs)

    if incomplete:
        report["incomplete_cells"] = incomplete
        report["status"] = "incomplete-sheets"
        OUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"sheets are incomplete ({incomplete} missing cells); nothing else to compute yet.")
        print(f"wrote {OUT_FILE}")
        return

    # Heuristic accuracy on the consensus of agreeing raters.
    for level in LEVELS:
        truth = consensus[level]
        per_rater = {
            "raterA": heuristic_metrics(labels, {sid: v for sid, v in
                                                 ((s, as_bool(sheet_a[s][level])) for s in labels) if v is not None}),
            "raterB": heuristic_metrics(labels, {sid: v for sid, v in
                                                 ((s, as_bool(sheet_b[s][level])) for s in labels) if v is not None}),
        }
        report[f"heuristic_vs_{level}"] = {
            "consensus_n": len(truth),
            **heuristic_metrics(labels, truth),
            "per_rater": per_rater,
        }

    if adjudicated is not None:
        report["adjudication"] = {
            level: heuristic_metrics(
                labels,
                {sid: value for sid in labels
                 if (value := as_bool(adjudicated.get(sid, {}).get(level))) is not None},
            )
            for level in LEVELS
        }

    report["disagreements"] = disagreements
    report["n_disagreements"] = len(disagreements)
    OUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    for level in LEVELS:
        stats = report["levels"][level]
        print(f"{level}: kappa={stats['kappa']} agreement={stats['agreement']} (n={stats['n']}) "
              f"matrix={stats['matrix']}")
    for level in LEVELS:
        metrics = report[f"heuristic_vs_{level}"]
        print(f"heuristic vs {level} consensus: P={metrics['precision']} R={metrics['recall']} "
              f"FNR={metrics['false_negative_rate']} (tp={metrics['tp']} fp={metrics['fp']} "
              f"fn={metrics['fn']} tn={metrics['tn']})")
    print(f"disagreements to adjudicate: {len(disagreements)}")
    print(f"wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
