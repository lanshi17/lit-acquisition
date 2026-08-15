# arXiv Submission Package — lit-acquisition

**Title**: lit-acquisition: An Open Multi-source Multilingual Biomedical
Literature Acquisition System with an Exploratory Coverage Benchmark
**Author**: Zhengshuai Yang (Hunan Agricultural University)
**arXiv account**: lanshi17 (logged in)

## Contents

```
main.tex          — full LaTeX source (acmart, nonacm+sigconf; self-contained,
                    references are inline thebibliography)
figures/*.pdf     — fig1-fig9 (architecture, ablation, coverage, multilingual,
                    citation, recall@K, heatmap, SOTA comparison, rerank)
main.pdf          — compiled preprint (11 pages)
.latexmkrc        — pins /usr/bin/pdflatex (Debian TeX Live, has acmart.cls)
```

The source is **self-contained**: no external .bib, no network fetch.
Compile with `latexmk -pdf` (or `pdflatex main.tex` twice).

## arXiv submission steps (v1.5 interface)

1. Log in at arxiv.org (account `lanshi17`), open "START NEW SUBMISSION".
2. **Category** (recommend in order): `cs.IR` (Information Retrieval) —
   the paper is an IR/systems evaluation; alternative `cs.DL` (Digital
   Libraries, JCDL-adjacent) or `q-bio.GN` (Genomics). arXiv requires at
   least one primary category from the cs group you belong to.
3. **Title**: paste from above. **Authors**: Zhengshuai Yang
   (the system will map to your account). Abstract: paste from main.tex
   (the `\begin{abstract}` block, plain text). Comments: e.g.
   "Exploratory coverage benchmark; reference set is heuristic, not
   expert-verified (see Section 4.1)".
4. **Upload source**: zip the *contents* of this folder (main.tex,
   figures/, .latexmkrc optional). Do NOT include main.pdf — arXiv
   recompiles from source and requires TeX. arXiv auto-detects
   `main.tex` as the root.
5. Use the **PDF preview** to confirm the compile (11 pages expected).
   If the preview fails, arXiv's log will point at the issue.
6. Complete the metadata form, agree to the arXiv license (recommend
   the default; note: source becomes archival — the file contains no
   secrets, keys, or personal data beyond the author block).
7. Submit; approval usually within 1-2 business days.

## Notes

- The `paper/main.tex` in the repo root is the JCDL draft (Anonymous
  author block preserved for double-blind submission). This preprint is
  the same content with the real author block and nonacm class.
- Data & reproducibility: raw candidate lists, gold standard, and the
  deterministic analysis pipeline are in the public repo; the paper's
  Reproducibility section links the release.
- The benchmark's reference set is heuristic (not expert-verified) and
  circular with the evaluated system; the paper states this explicitly
  — all claims are relative coverage comparisons.
