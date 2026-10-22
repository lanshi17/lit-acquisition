"""E2a: build an external, curator-sourced ground truth from ClinVar citations.

Plan reference: ``paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md`` section 2.2 (E2a).

The project reference set was produced by the project's own title heuristic over
a project-chosen pool, which invites the "circularity" objection: methods that
include EuropePMC/Crossref cannot help but beat PubMed-only on a pool those very
sources built. This script removes the project from the truth loop entirely.

For each of the 43 variants it collects the literature that **clinical
laboratories cite when they submit a variant interpretation to ClinVar**:

1. ClinVar records for the variant are located with NCBI E-utilities
   (``esearch db=clinvar``), using several query forms because ClinVar's
   variant-name index is token-based and the gene filter does not compose with
   the variant-name field; every candidate record is then verified by
   ``esummary`` on the gene symbol and, where possible, the variant token.
2. ``elink dbfrom=clinvar db=pubmed`` retrieves the cited PMIDs of exactly those
   records.
3. PubMed ``esummary`` supplies title/journal/year/DOI for each PMID.
4. A data-driven filter drops citations that are attached to many unrelated
   variants (generic guidelines/reviews such as the ACMG/AMP criteria paper);
   the threshold is reported in the artifact rather than hidden.

The result is written to ``benchmarks/gold_standard/variants_clinvar.jsonl``
(one JSON object per variant) plus ``results/clinvar_ground_truth.json`` with
the full provenance and audit trail. Raw E-utilities responses are cached in
``results/clinvar_cache.json`` so re-runs need no network access.

Usage::

    python benchmarks/gold_standard/build_clinvar_ground_truth.py
    python benchmarks/gold_standard/build_clinvar_ground_truth.py --offline   # cache only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

GOLD_DIR = BENCH_DIR / "gold_standard"
RESULTS_DIR = BENCH_DIR / "results"
VARIANTS_FILE = GOLD_DIR / "variants.jsonl"
OUT_FILE = GOLD_DIR / "variants_clinvar.jsonl"
SUMMARY_FILE = RESULTS_DIR / "clinvar_ground_truth.json"
CACHE_FILE = RESULTS_DIR / "clinvar_cache.json"

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_DELAY = 0.36  # ~3 requests/second, the unauthenticated E-utilities cap
RETRIES = 4

# PMIDs that are generic ACMG/AMP or reporting guidance rather than
# variant-specific evidence (also caught by the cross-variant filter).
KNOWN_GENERIC_PMIDS = {
    "25741868",  # Richards et al. 2015, ACMG/AMP sequence variant interpretation
    "24033266",  # ACMG clinical laboratory standards for NGS
    "24055113",  # ACMG recommendations for reporting incidental findings
}
CROSS_VARIANT_THRESHOLD = 2  # cited by >= this many of our variants -> generic


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


class Client:
    """Throttled E-utilities client with a persistent response cache."""

    def __init__(self, cache: dict[str, Any], offline: bool = False) -> None:
        self.cache = cache
        self.offline = offline
        self.client = httpx.Client(timeout=60.0)
        self.requests = 0
        self.cache_hits = 0
        self._last = 0.0

    def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        key = endpoint + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        if key in self.cache:
            self.cache_hits += 1
            return self.cache[key]
        if self.offline:
            raise RuntimeError(f"offline mode but cache miss: {key}")
        last: Exception | None = None
        for attempt in range(RETRIES):
            try:
                wait = REQUEST_DELAY - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                resp = self.client.get(f"{EUTILS}/{endpoint}", params=params)
                self._last = time.monotonic()
                self.requests += 1
                if resp.status_code in (429, 500, 502, 503, 504):
                    last = RuntimeError(f"HTTP {resp.status_code}")
                    time.sleep(1.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                self.cache[key] = data
                return data
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"{endpoint} failed: {last}")

    def close(self) -> None:
        self.client.close()


def esearch(client: Client, db: str, term: str, retmax: int = 200) -> list[str]:
    data = client.get(
        "esearch.fcgi",
        {"db": db, "term": term, "retmode": "json", "retmax": retmax, "sort": "relevance"},
    )
    return list(data.get("esearchresult", {}).get("idlist", []) or [])


def esummary(client: Client, db: str, ids: list[str]) -> dict[str, Any]:
    if not ids:
        return {}
    out: dict[str, Any] = {}
    for i in range(0, len(ids), 180):
        chunk = ids[i : i + 180]
        data = client.get("esummary.fcgi", {"db": db, "id": ",".join(chunk), "retmode": "json"})
        result = data.get("result", {})
        for uid in result.get("uids", []):
            out[uid] = result[uid]
    return out


def elink_pubmed(client: Client, clinvar_ids: list[str]) -> dict[str, list[str]]:
    """clinvar UID -> cited PubMed UIDs."""
    if not clinvar_ids:
        return {}
    data = client.get(
        "elink.fcgi", {"dbfrom": "clinvar", "db": "pubmed", "id": ",".join(clinvar_ids), "retmode": "json"}
    )
    out: dict[str, list[str]] = {}
    for linkset in data.get("linksets", []):
        ids = [str(i) for i in linkset.get("ids", [])]
        pmids: list[str] = []
        for db in linkset.get("linksetdbs", []):
            if db.get("dbto") == "pubmed":
                pmids.extend(str(x) for x in db.get("links", []))
        for uid in ids:
            out.setdefault(uid, [])
            out[uid].extend(p for p in pmids if p not in out[uid])
    return out


def variant_tokens(variant: str) -> list[str]:
    """Searchable spellings of a variant, longest/most specific first."""
    tokens: list[str] = []

    def add(value: str) -> None:
        value = value.strip().strip(",;")
        if value and value not in tokens:
            tokens.append(value)

    add(variant)
    stripped = re.sub(r"^(p|c)\.", "", variant)
    add(stripped)
    for parenthesised in re.findall(r"\(([^)]+)\)", variant):
        add(re.sub(r"^(p|c)\.", "", parenthesised))
    add(stripped.split(" ")[0])
    return tokens


def query_forms(variant: dict[str, Any]) -> list[str]:
    """ClinVar query forms, most specific first.

    ``gene[gene]`` does not compose with the variant-name field in ClinVar's
    index, and cDNA spellings are only matched when the token is submitted
    without the ``c.``/``p.`` prefix, so several forms are tried. Results are
    always verified against the gene symbol afterwards.
    """
    gene = variant["gene"]
    forms: list[str] = []
    for token in variant_tokens(variant["variant"]):
        forms.append(f'{gene}[gene] AND "{token}"')
        forms.append(f"{gene}[gene] AND {token}")
    for token in variant_tokens(variant["variant"]):
        forms.append(f'"{token}"')
    return forms


def collect_clinvar_records(client: Client, variant: dict[str, Any]) -> dict[str, Any]:
    """Locate the ClinVar records for one variant and audit how they were found."""
    gene = variant["gene"]
    var = variant["variant"]
    tokens = {_norm(t) for t in variant_tokens(var)}
    accepted: dict[str, dict[str, Any]] = {}
    tried: list[dict[str, Any]] = []

    for form in query_forms(variant):
        try:
            ids = esearch(client, "clinvar", form, retmax=500)
        except Exception as exc:  # noqa: BLE001
            tried.append({"form": form, "error": str(exc)})
            continue
        if not ids:
            tried.append({"form": form, "n_ids": 0, "n_accepted": 0})
            continue
        summaries = esummary(client, "clinvar", ids)
        n_accepted = 0
        for uid, record in summaries.items():
            title = str(record.get("title") or "")
            # Some records carry a locus placeholder instead of the gene symbol
            # (VHL records, for example, show gene_sort = LOC107303340), so the
            # HGVS transcript notation "(GENE)" in the title is accepted too.
            gene_sort = str(record.get("gene_sort") or "")
            if gene_sort.upper() != gene.upper() and f"({gene.upper()})" not in title.upper():
                continue
            # Accept when a variant token is present in the record title, or when
            # the query form was gene-anchored (legacy names such as "exon 7
            # deletion" or "Swedish" are not spelled out as HGVS in the title).
            title_norm = _norm(title)
            token_ok = any(token and token in title_norm for token in tokens)
            gene_anchored = gene.lower() in form.lower()
            if not (token_ok or gene_anchored):
                continue
            if uid not in accepted:
                accepted[uid] = {
                    "uid": uid,
                    "accession": record.get("accession"),
                    "title": title,
                    "gene_sort": record.get("gene_sort"),
                    "found_by": form,
                    "token_match": bool(token_ok),
                    "classification": (record.get("germline_classification") or {}).get("description"),
                    "review_status": (record.get("germline_classification") or {}).get("review_status"),
                    "last_evaluated": (record.get("germline_classification") or {}).get("last_evaluated"),
                }
                n_accepted += 1
        tried.append({"form": form, "n_ids": len(ids), "n_accepted": n_accepted})
        if n_accepted and form.startswith(f"{gene}[gene] AND"):
            break  # a gene-anchored form already produced verified records
    return {"records": list(accepted.values()), "queries": tried}


def pubmed_papers(client: Client, pmids: list[str]) -> dict[str, dict[str, Any]]:
    """PMID -> {title, journal, year, doi}."""
    summaries = esummary(client, "pubmed", pmids)
    out: dict[str, dict[str, Any]] = {}
    for pmid in pmids:
        record = summaries.get(pmid)
        if not record:
            continue
        doi = ""
        for article_id in record.get("articleids", []) or []:
            if article_id.get("idtype") == "doi":
                doi = str(article_id.get("value") or "")
                break
        out[pmid] = {
            "pmid": pmid,
            "title": record.get("title"),
            "journal": record.get("fulljournalname") or record.get("source"),
            "year": (record.get("pubdate") or "")[:4],
            "doi": doi,
            "pubtypes": record.get("pubtype") or [],
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="use the response cache only")
    args = parser.parse_args()

    cache: dict[str, Any] = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    variants = [json.loads(line) for line in VARIANTS_FILE.read_text().splitlines() if line.strip()]
    client = Client(cache, offline=args.offline)

    per_variant: list[dict[str, Any]] = []
    pmids_by_variant: dict[str, list[str]] = {}
    try:
        for variant in variants:
            vid = variant["variant_id"]
            located = collect_clinvar_records(client, variant)
            records = located["records"]
            links = elink_pubmed(client, [r["uid"] for r in records])
            pmids: list[str] = []
            pmid_source: dict[str, list[str]] = {}
            for uid, linked in links.items():
                for pmid in linked:
                    pmid_source.setdefault(pmid, []).append(uid)
                    if pmid not in pmids:
                        pmids.append(pmid)
            papers = pubmed_papers(client, pmids)
            pmids_by_variant[vid] = list(papers)
            per_variant.append(
                {
                    "variant_id": vid,
                    "gene": variant["gene"],
                    "variant": variant["variant"],
                    "disease": variant.get("disease"),
                    "clinvar_records": records,
                    "clinvar_queries": located["queries"],
                    "n_clinvar_records": len(records),
                    "n_cited_pmids": len(papers),
                    "pmid_sources": pmid_source,
                    "papers": [papers[p] for p in pmids if p in papers],
                }
            )
            print(f"  [{vid}] {variant['gene']} {variant['variant']}: "
                  f"{len(records)} ClinVar records -> {len(papers)} cited papers")
    finally:
        client.close()
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))

    # ── generic-citation filter (data-driven, reported) ───────────────────
    cross_variant: dict[str, set[str]] = {}
    for vid, pmids in pmids_by_variant.items():
        for pmid in pmids:
            cross_variant.setdefault(pmid, set()).add(vid)
    generic = {
        pmid for pmid, vids in cross_variant.items() if len(vids) >= CROSS_VARIANT_THRESHOLD
    } | KNOWN_GENERIC_PMIDS

    for entry in per_variant:
        kept = [p for p in entry["papers"] if p["pmid"] not in generic]
        entry["papers_all"] = entry["papers"]
        entry["papers"] = kept
        entry["n_cited_pmids"] = len(kept)
        entry["n_generic_removed"] = len(entry["papers_all"]) - len(kept)

    with OUT_FILE.open("w") as handle:
        for entry in per_variant:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    total_papers = sum(len(e["papers"]) for e in per_variant)
    summary = {
        "schema_version": 1,
        "protocol": {
            "plan_reference": "paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md §2.2 (E2a)",
            "source": "NCBI ClinVar submitter assertion citations (SCV -> PubMed links)",
            "collection": (
                "esearch db=clinvar (several query forms) -> esummary verification on gene symbol and "
                "variant token -> elink dbfrom=clinvar db=pubmed -> esummary db=pubmed"
            ),
            "generic_filter": {
                "cross_variant_threshold": CROSS_VARIANT_THRESHOLD,
                "known_generic_pmids": sorted(KNOWN_GENERIC_PMIDS),
                "n_filtered": len(generic),
                "filtered_pmids": sorted(generic),
            },
            "independence": (
                "ClinVar citations are entered by clinical laboratories and ClinGen expert panels; the "
                "project neither built this pool nor scored it, so recall against it is not circular with "
                "respect to the federated providers."
            ),
            "network_requests": client.requests,
            "cache_hits": client.cache_hits,
        },
        "variants": [
            {
                "variant_id": e["variant_id"],
                "gene": e["gene"],
                "variant": e["variant"],
                "n_clinvar_records": e["n_clinvar_records"],
                "n_papers": len(e["papers"]),
                "n_generic_removed": e["n_generic_removed"],
                "clinvar_accessions": [r["accession"] for r in e["clinvar_records"]],
                "queries": e["clinvar_queries"],
            }
            for e in per_variant
        ],
        "totals": {
            "variants": len(per_variant),
            "variants_with_records": sum(1 for e in per_variant if e["n_clinvar_records"]),
            "variants_with_citations": sum(1 for e in per_variant if e["papers"]),
            "cited_papers_after_filter": total_papers,
            "unique_pmids_after_filter": len({p["pmid"] for e in per_variant for p in e["papers"]}),
        },
    }
    SUMMARY_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"\nClinVar records for {summary['totals']['variants_with_records']}/{len(per_variant)} variants; "
          f"cited papers after generic filter: {total_papers} "
          f"({summary['totals']['unique_pmids_after_filter']} unique PMIDs)")
    print(f"generic citations removed: {len(generic)}")
    print(f"wrote {OUT_FILE}\nwrote {SUMMARY_FILE}")


if __name__ == "__main__":
    main()
