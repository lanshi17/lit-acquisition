"""Expand the exploratory reference set with new variants (same protocol).

Faithfully replicates the documented creation protocol of the original
30 variants (benchmarks/gold_standard/README.md): clinical-style query
formulation, candidate pooling from PubMed E-utilities + EuropePMC +
Crossref (channels overlapping the evaluated system --- the documented
pooling bias is preserved by design), and the weighted heuristic scoring:

- gene symbol in title:            +3
- variant identifier in title:     +5
- disease keywords in title:       up to +3 (one per keyword)
- preferred pub-types:             +2 (case report)
- reviews:                         -2
- excluded pub-types:              editorial, letter, comment, news

Papers scoring >= THRESHOLD are annotated relevant. Output appends to
``variants.jsonl`` (never overwrites existing variants).

Usage::

    python benchmarks/gold_standard/build_variants.py [--threshold 4]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

BENCH_DIR = Path(__file__).resolve().parent.parent
GOLD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from baselines.sota_common import pubmed_search  # noqa: E402
from collect_external_baselines import _get_json  # noqa: E402
from metrics import paper_id  # noqa: E402

VARIANTS_FILE = GOLD_DIR / "variants.jsonl"
THRESHOLD = 4
POOL_DEPTH = 15  # per channel; matches the cap-15/20 era of the original 30

EXCLUDED_PUBTYPES = {"editorial", "letter", "comment", "news", "newspaper article"}
REVIEW_PUBTYPES = {"review", "systematic review", "meta-analysis"}

# (gene, variant, disease, query) --- clinically actionable, well-studied
# variants in genes NOT already covered by the original 30.
NEW_VARIANTS = [
    ("RET", "p.M918T", "medullary thyroid carcinoma", "RET M918T medullary thyroid carcinoma case report"),
    ("JAK2", "p.V617F", "myeloproliferative neoplasm", "JAK2 V617F myeloproliferative neoplasm mutation"),
    ("FGFR3", "p.R248C", "achondroplasia", "FGFR3 R248C achondroplasia mutation"),
    ("BRCA2", "c.5946delT", "hereditary breast cancer", "BRCA2 5946delT hereditary breast cancer"),
    ("HFE", "p.C282Y", "hereditary hemochromatosis", "HFE C282Y hemochromatosis mutation"),
    ("CHEK2", "c.1100delC", "hereditary breast cancer", "CHEK2 1100delC breast cancer risk"),
    ("TTR", "p.V30M", "familial amyloid polyneuropathy", "TTR V30M transthyretin amyloidosis"),
    ("GJB2", "c.35delG", "non-syndromic hearing loss", "GJB2 35delG connexin hearing loss"),
    ("SLC26A4", "c.919-2A>G", "Pendred syndrome", "SLC26A4 Pendred syndrome mutation"),
    ("VHL", "p.R167W", "von Hippel-Lindau disease", "VHL R167W von Hippel-Lindau"),
    ("RYR1", "p.R614C", "malignant hyperthermia", "RYR1 R614C malignant hyperthermia"),
    ("ATM", "c.7271T>G", "ataxia-telangiectasia", "ATM 7271T>G ataxia telangiectasia"),
    ("CDH1", "p.1003C>T", "hereditary diffuse gastric cancer", "CDH1 hereditary diffuse gastric cancer mutation"),
    ("MTHFR", "c.677C>T", "hyperhomocysteinemia", "MTHFR 677C>T hyperhomocysteinemia"),
    ("COL1A1", "p.Gly409Cys", "osteogenesis imperfecta", "COL1A1 osteogenesis imperfecta mutation"),
]

DISEASE_KEYWORDS = {
    "RET": ["medullary thyroid", "men2", "multiple endocrine neoplasia"],
    "JAK2": ["myeloproliferative", "polycythemia", "essential thrombocythemia", "myelofibrosis"],
    "FGFR3": ["achondroplasia", "thanatophoric"],
    "BRCA2": ["breast cancer", "ovarian cancer", "brca2"],
    "HFE": ["hemochromatosis", "iron overload"],
    "F5": ["factor v leiden", "thrombophilia", "venous thrombosis"],
    "TTR": ["transthyretin", "amyloid", "familial amyloid polyneuropathy"],
    "GJB2": ["hearing loss", "deafness", "connexin 26"],
    "SLC26A4": ["pendred", "enlarged vestibular aqueduct", "sensorineural hearing loss"],
    "VHL": ["von hippel-lindau", "vhl disease", "renal cell carcinoma"],
    "RYR1": ["malignant hyperthermia", "ryanodine receptor"],
    "ATM": ["ataxia-telangiectasia", "ataxia telangiectasia"],
    "CDH1": ["hereditary diffuse gastric", "lobular breast"],
    "CHEK2": ["breast cancer", "chek2", "cancer susceptibility"],
    "MTHFR": ["hyperhomocysteinemia", "homocysteine", "thrombophilia"],
    "COL1A1": ["osteogenesis imperfecta", "collagen type i"],
}


def _score_title(
    title: str, gene: str, variant: str, disease_keywords: list[str], pubtypes: list[str]
) -> tuple[int, str]:
    """Weighted heuristic score per the documented protocol."""
    low = (title or "").lower()
    pts = {pt.lower() for pt in pubtypes}
    if pts & EXCLUDED_PUBTYPES:
        return -99, "excluded_pubtype"
    score = 0
    reasons: list[str] = []
    if gene.lower() in low:
        score += 3
        reasons.append("gene")
    # variant: HGVS forms (p.X, c.X) and bare legacy forms (e.g. V617F, 35delG)
    tokens = [t for t in re.split(r"[.,:()\s]+", variant) if t]
    bare = "".join(tokens)
    legacy_forms = {variant.lower(), bare.lower()}
    # drop a single leading HGVS prefix letter: "pV617F" -> "V617F"
    if len(bare) > 1 and bare[0].isalpha() and bare[1].isdigit():
        legacy_forms.add(bare[1:].lower())
    variant_hit = any(form in low for form in legacy_forms)
    if variant_hit:
        score += 5
        reasons.append("variant")
    for kw in disease_keywords:
        if kw in low:
            score += 1
            reasons.append("disease")
    if pts & REVIEW_PUBTYPES:
        score -= 2
    return score, "+".join(reasons) or "none"


def _evidence_type(pubtypes: list[str]) -> str:
    pts = {pt.lower() for pt in pubtypes}
    if "case reports" in pts or "case report" in pts:
        return "case_report"
    if pts & {"clinical study", "clinical trial", "observational study"}:
        return "sequencing"
    return "other"


async def _pool_europepmc(client: httpx.AsyncClient, query: str, limit: int = 50) -> list[dict[str, Any]]:
    """EuropePMC search with pubTypeList, mapped to a uniform item schema."""
    data = _get_json(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        {"query": query, "format": "json", "pageSize": limit},
        retries=3,
    )
    items = []
    for r in (data.get("resultList") or {}).get("result", []) or []:
        items.append(
            {
                "doi": r.get("doi"),
                "pmid": str(r.get("pmid")) if r.get("pmid") else None,
                "title": r.get("title"),
                "journal": r.get("journalInfo", {}).get("journal", {}).get("title"),
                "year": r.get("pubYear"),
                "pubtypes": r.get("pubTypeList", {}).get("pubType", []) or [],
                "language": "en",
                "source": "europepmc",
            }
        )
    return items


async def _pool_crossref(client: httpx.AsyncClient, query: str, limit: int = 50) -> list[dict[str, Any]]:
    data = _get_json(
        "https://api.crossref.org/works",
        {"query.bibliographic": query, "rows": limit, "select": "DOI,title,container-title,issued,type,published-print,published-online"},
        retries=3,
    )
    items = []
    for it in (data.get("message") or {}).get("items", []) or []:
        year = None
        for k in ("published-print", "published-online", "issued"):
            dp = it.get(k, {}).get("date-parts", [[None]])
            if dp and dp[0] and dp[0][0]:
                year = dp[0][0]
                break
        items.append(
            {
                "doi": it.get("DOI"),
                "pmid": None,
                "title": (it.get("title") or [None])[0],
                "journal": (it.get("container-title") or [None])[0],
                "year": str(year) if year else None,
                "pubtypes": [it.get("type", "")],
                "language": "en",
                "source": "crossref",
            }
        )
    return items


async def build_variant(client: httpx.AsyncClient, spec: tuple[str, str, str, str]) -> dict[str, Any]:
    gene, variant, disease, query = spec
    pool: list[dict[str, Any]] = []
    # EuropePMC and Crossref first: their copies carry pubType/type metadata
    # (case-report bonus, review penalty, exclusions), and win dedup.
    pool.extend(await _pool_europepmc(client, query, limit=POOL_DEPTH))
    pool.extend(await _pool_crossref(client, query, limit=POOL_DEPTH))
    for p in await pubmed_search(client, query, retmax=POOL_DEPTH, sort="relevance"):
        p["pubtypes"] = []
        p["source"] = "pubmed"
        pool.append(p)

    # Dedup by canonical identity (DOI > PMID > normalized title).
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for p in pool:
        pid = paper_id(p)
        if pid and pid in seen:
            continue
        if pid:
            seen.add(pid)
        deduped.append(p)

    kws = DISEASE_KEYWORDS[gene]
    relevant: list[dict[str, Any]] = []
    for p in deduped:
        score, reason = _score_title(
            p.get("title") or "", gene, variant, kws, p.get("pubtypes") or []
        )
        if score >= THRESHOLD:
            relevant.append(
                {
                    "doi": p.get("doi"),
                    "pmid": p.get("pmid"),
                    "title": p.get("title"),
                    "journal": p.get("journal"),
                    "year": p.get("year"),
                    "language": p.get("language") or "unknown",
                    "source": p.get("source"),
                    "evidence_type": _evidence_type(p.get("pubtypes") or []),
                    "annotation_reason": reason,
                }
            )
    # Sort by score desc, then year desc (most confident first).
    relevant.sort(key=lambda r: (-_score_title(r["title"] or "", gene, variant, kws, [])[0],
                                 -(int(r.get("year") or 0))))
    return {
        "variant_id": "",
        "gene": gene,
        "variant": variant,
        "disease": disease,
        "query": query,
        "relevant_papers": relevant,
        "total_relevant": len(relevant),
    }


async def main_async(threshold: int) -> None:
    global THRESHOLD
    THRESHOLD = threshold
    existing: dict[str, dict[str, Any]] = {}
    for line in VARIANTS_FILE.read_text().splitlines():
        if line.strip():
            v = json.loads(line)
            existing[v["variant_id"]] = v
    if not existing:
        raise SystemExit("variants.jsonl missing or empty; cannot append")
    used_genes = {v["gene"] for v in existing.values()}
    for spec in NEW_VARIANTS:
        if spec[0] in used_genes:
            print(f"WARNING: gene {spec[0]} already covered; skipping")
            continue

    next_num = len(existing) + 1
    async with httpx.AsyncClient(timeout=60.0) as client:
        for spec in NEW_VARIANTS:
            if spec[0] in used_genes:
                continue
            v = await build_variant(client, spec)
            v["variant_id"] = f"V{next_num:03d}"
            next_num += 1
            print(f"{v['variant_id']} {v['gene']}: {len(v['relevant_papers'])} relevant from pool")
            with open(GOLD_DIR / "candidates" / f"{v['variant_id']}_candidates.jsonl", "w") as f:
                for p in v["relevant_papers"]:
                    f.write(json.dumps(p, ensure_ascii=False) + "\n")
            existing[v["variant_id"]] = v
    VARIANTS_FILE.write_text(
        "\n".join(json.dumps(existing[k], ensure_ascii=False) for k in sorted(existing)) + "\n"
    )
    print(f"variants.jsonl now has {len(existing)} variants")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(main_async(args.threshold))
