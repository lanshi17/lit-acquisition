# JCDL 2027 Submission Notes

**Paper**: lit-acquisition: An Open Multi-source Multilingual Biomedical
Literature Acquisition System with an Exploratory Coverage Benchmark
**Format**: ACM sigconf (acmart), double-blind, 10 pages excluding references
**Current state**: main text 9.92 pages (REFERENCES starts p.10, y≈681);
11 pages total incl. 31 references. Builds with 0 errors.

## Deadline (verified from CFP)

- **JCDL 2027 full/short papers: July 15, 2027 (Anywhere on Earth)**
- Notification ~Aug 2027; camera-ready ~Sep 2027; conference ~Oct 2027
- JCDL 2026 (deadline Jul 15, 2026) has closed; 2027 is the next window.

## Submission site

EasyChair (JCDL 2026 portal was `easychair.org/conferences/?conf=jcdl26`;
JCDL 2027 portal will be announced with the CFP — same system).

## Double-blind compliance (already verified)

- Author block is Anonymous (`\documentclass[sigconf,screen]{acmart}` +
  Anonymous author/affiliation/email) — do NOT add real names for review.
- No repo URLs, no author names, no self-citations, no acknowledgments.
- CCS concepts removed (optional at submission; add at camera-ready).
- `\acmConference[JCDL '27]{...}` set (not identity-revealing).
- Preprint: NOT submitted (arXiv endorsement was blocked) — fine either
  way, since anonymized preprints may be posted without citing.

## Before submitting (recommended, we have 11 months)

1. **Expert re-annotation** (the paper's own #1 future work): dual
   annotation of a stratified sample with clinical-genetics expertise,
   Cohen's κ. This removes the biggest review risk (heuristic reference
   set) and unlocks PS3/PVS1 evidence claims.
2. **ORCID**: register one (required by ACM for accepted papers).
3. **Anonymous artifacts**: put code/data/repro pipeline on
   anonymous.4open.science (CFP explicitly encourages this) and link it
   anonymously in the Reproducibility section.
4. **Re-run stability**: the live-API variance story is documented; an
   additional collection session before submission would strengthen the
   "robust across sessions" claim.
5. Re-read the CFP when published for the exact JCDL 2027 dates/portal.

## Submission checklist (at submission time)

- [ ] PDF via EasyChair, anonymous, ≤10 pages excluding references
- [ ] ACM sigconf format (current main.tex ✓)
- [ ] No identity-revealing content (re-check after any edits)
- [ ] Reproducibility statement present (Sec. 8) + anonymous artifact link
- [ ] All figures/tables render (build with `latexmk -pdf` in paper/)
- [ ] AI-use disclosure per ACM policy if applicable

## Build

```
cd paper && latexmk -pdf   # Debian TeX Live (paper/.latexmkrc pins /usr/bin/pdflatex)
```
