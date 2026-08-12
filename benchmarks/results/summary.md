# Benchmark Results (K=50)

| Method | Recall | Precision | F1 | nDCG |
| --- | --- | --- | --- | --- |
| full_pipeline | 0.348±0.116 | 0.264±0.138 | 0.288±0.117 | 0.320±0.116 |
| multisource_no_translation | 0.247±0.136 | 0.238±0.160 | 0.239±0.143 | 0.260±0.146 |
| multisource_with_translation | 0.329±0.099 | 0.320±0.143 | 0.316±0.115 | 0.310±0.100 |
| pubmed_only | 0.171±0.183 | 0.229±0.240 | 0.172±0.161 | 0.195±0.170 |


## pubmed_only
- Variants: 30
- Recall: 0.171 ± 0.183
- Precision: 0.229 ± 0.240
- F1: 0.172 ± 0.161
- nDCG: 0.195 ± 0.170
- Relevant found: 83/541
- Source coverage: {'pubmed': 83}
- Language coverage: {'en': 83}

## multisource_no_translation
- Variants: 30
- Recall: 0.247 ± 0.136
- Precision: 0.238 ± 0.160
- F1: 0.239 ± 0.143
- nDCG: 0.260 ± 0.146
- Relevant found: 143/541
- Source coverage: {'pmc': 142, 'crossref': 1}
- Language coverage: {'unknown': 143}

## multisource_with_translation
- Variants: 30
- Recall: 0.329 ± 0.099
- Precision: 0.320 ± 0.143
- F1: 0.316 ± 0.115
- nDCG: 0.310 ± 0.100
- Relevant found: 182/541
- Source coverage: {'pmc': 49, 'crossref': 131, 'europepmc': 2}
- Language coverage: {'unknown': 113, 'en': 48, 'de': 12, 'fr': 9}

## full_pipeline
- Variants: 30
- Recall: 0.348 ± 0.116
- Precision: 0.264 ± 0.138
- F1: 0.288 ± 0.117
- nDCG: 0.320 ± 0.116
- Relevant found: 195/541
- Source coverage: {'pmc': 48, 'crossref': 135, 'citation_graph': 11, 'europepmc': 1}
- Language coverage: {'unknown': 122, 'en': 50, 'de': 12, 'fr': 11}
