"""Paired significance testing with multiplicity control.

Provides the statistical machinery used by ``make_paper_data.py``:

- paired Wilcoxon signed-rank tests (scipy, exact method where available)
- matched-pairs rank-biserial correlation as effect size (ties excluded)
- Holm-Bonferroni step-down correction within a declared test family
- bootstrap 95% confidence intervals of mean paired differences
  (variant-level resampling, fixed seed for reproducibility)
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np
from scipy import stats as sps

BOOT_N = 10_000
BOOT_SEED = 42


def paired_test(before: Sequence[float], after: Sequence[float]) -> dict[str, Any]:
    """Wilcoxon signed-rank test on paired scores (after - before).

    Returns mean delta, two-sided p, matched-pairs rank-biserial r
    (zero differences excluded), the number of non-zero pairs, and a
    bootstrap 95% CI of the mean paired difference.
    """
    b = np.asarray(before, dtype=float)
    a = np.asarray(after, dtype=float)
    if b.shape != a.shape:
        raise ValueError("paired samples must have equal length")
    d = a - b

    nz = d[d != 0]
    if nz.size == 0:
        p = 1.0
        r = 0.0
    else:
        # Drop zero differences ourselves (the standard "wilcox" treatment):
        # scipy silently falls back to the normal approximation when zeros
        # are present in the input, which is inaccurate for the small
        # non-zero counts that occur here; on the zero-free vector
        # method="auto" selects the exact distribution when there are no
        # rank ties.
        res = sps.wilcoxon(nz, alternative="two-sided", method="auto")
        p = float(res.pvalue)
        ranks = sps.rankdata(np.abs(nz))
        r_pos = ranks[nz > 0].sum()
        r_neg = ranks[nz < 0].sum()
        r = float((r_pos - r_neg) / (r_pos + r_neg))

    lo, hi = bootstrap_ci_mean(d)
    return {
        "delta": float(d.mean()),
        "p": p,
        "r": r,
        "n_nonzero": int(nz.size),
        "ci95": [lo, hi],
    }


def bootstrap_ci_mean(
    diffs: Sequence[float],
    n_boot: int = BOOT_N,
    seed: int = BOOT_SEED,
) -> tuple[float, float]:
    """Percentile bootstrap 95% CI of the mean of paired differences."""
    d = np.asarray(diffs, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, d.size, size=(n_boot, d.size))
    means = d[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def holm_correction(tests: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Apply Holm-Bonferroni step-down correction to a family of tests.

    Args:
        tests: mapping test_name -> result dict containing a raw ``p``.

    Returns:
        The same mapping with ``p_holm`` and ``significant_holm`` (alpha=.05)
        added to each result dict (in place and returned for convenience).
    """
    m = len(tests)
    ordered = sorted(tests.items(), key=lambda kv: kv[1]["p"])
    running_max = 0.0
    for i, (name, res) in enumerate(ordered):
        adj = (m - i) * res["p"]
        running_max = max(running_max, adj)
        res["p_holm"] = min(1.0, running_max)
        res["significant_holm"] = res["p_holm"] < 0.05
    return tests
