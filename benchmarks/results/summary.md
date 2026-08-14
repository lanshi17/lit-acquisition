# Benchmark Results (corrected protocol)

## Fixed-K metrics (K=20) + set-level metrics

| Method | Recall@20 | Prec@20 | F1@20 | nDCG@20 | SetRecall | Prec@Retr | Cands/variant |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full_pipeline | 0.334±0.102 | 0.308±0.134 | 0.314±0.114 | 0.325±0.123 | 0.348±0.116 | 0.264±0.138 | 26.0 |
| full_pipeline_nested | 0.330±0.100 | 0.305±0.133 | 0.311±0.112 | 0.325±0.118 | 0.345±0.114 | 0.261±0.139 | 26.0 |
| multisource_no_translation | 0.247±0.136 | 0.238±0.160 | 0.239±0.143 | 0.273±0.162 | 0.247±0.136 | 0.238±0.160 | 20.0 |
| multisource_with_translation | 0.329±0.099 | 0.303±0.130 | 0.309±0.111 | 0.324±0.117 | 0.329±0.099 | 0.320±0.143 | 19.3 |
| pubmed_only | 0.171±0.183 | 0.138±0.135 | 0.146±0.140 | 0.201±0.172 | 0.171±0.183 | 0.229±0.240 | 14.2 |


## Fixed-K metrics (K=10)

| Method | Recall@10 | Prec@10 | F1@10 | nDCG@10 | SetRecall | Prec@Retr | Cands/variant |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full_pipeline | 0.122±0.058 | 0.237±0.152 | 0.159±0.083 | 0.253±0.156 | 0.348±0.116 | 0.264±0.138 | 26.0 |
| full_pipeline_nested | 0.123±0.058 | 0.237±0.145 | 0.159±0.081 | 0.256±0.148 | 0.345±0.114 | 0.261±0.139 | 26.0 |
| multisource_no_translation | 0.120±0.077 | 0.237±0.176 | 0.158±0.104 | 0.260±0.186 | 0.247±0.136 | 0.238±0.160 | 20.0 |
| multisource_with_translation | 0.123±0.058 | 0.237±0.145 | 0.159±0.081 | 0.256±0.148 | 0.329±0.099 | 0.320±0.143 | 19.3 |
| pubmed_only | 0.122±0.107 | 0.207±0.184 | 0.148±0.131 | 0.232±0.186 | 0.171±0.183 | 0.229±0.240 | 14.2 |


## Paired Wilcoxon tests (Holm-corrected family)

| Test | Δ | p | p(Holm) | r | 95% CI | sig |
| --- | --- | --- | --- | --- | --- | --- |
| M2_vs_M1_set_recall | +0.076 | 0.0254 | 0.1016 | +0.50 | [-0.017, +0.158] | no |
| M3_vs_M2_set_recall | +0.082 | 0.0372 | 0.1016 | +0.45 | [+0.015, +0.149] | no |
| M4n_vs_M3_set_recall | +0.016 | 0.2500 | 0.2500 | +1.00 | [+0.000, +0.037] | no |
| M4n_vs_M1_set_recall | +0.173 | 0.0005 | 0.0038 | +0.79 | [+0.091, +0.251] | yes |
| M4n_vs_M2_set_recall | +0.097 | 0.0280 | 0.1016 | +0.48 | [+0.026, +0.171] | no |
| M4n_vs_M1_recall20 | +0.159 | 0.0006 | 0.0038 | +0.78 | [+0.082, +0.230] | yes |
| M4n_vs_M1_ndcg50 | +0.124 | 0.0050 | 0.0251 | +0.57 | [+0.046, +0.199] | yes |
| M4n_vs_M3_prec_retr | -0.058 | 0.0002 | 0.0020 | -1.00 | [-0.091, -0.029] | yes |


## Run-to-run variance (duplicated M3 configuration)

- identical candidate lists: 18/30
- mean Jaccard: 0.891 (min 0.429)
- mean |Δ set recall|: 0.0215 (max 0.2222)
