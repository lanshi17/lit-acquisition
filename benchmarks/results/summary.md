# Benchmark Results (corrected protocol)

## Fixed-K metrics (K=20) + set-level metrics

| Method | Recall@20 | Prec@20 | F1@20 | nDCG@20 | SetRecall | Prec@Retr | Cands/variant |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full_pipeline | 0.276±0.149 | 0.265±0.160 | 0.265±0.149 | 0.301±0.184 | 0.467±0.152 | 0.121±0.057 | 79.3 |
| full_pipeline_nested | 0.276±0.149 | 0.265±0.160 | 0.265±0.149 | 0.301±0.184 | 0.467±0.152 | 0.121±0.057 | 79.3 |
| multisource_no_translation | 0.153±0.108 | 0.147±0.104 | 0.147±0.102 | 0.164±0.118 | 0.472±0.229 | 0.087±0.045 | 100.0 |
| multisource_with_translation | 0.276±0.149 | 0.265±0.160 | 0.265±0.149 | 0.301±0.184 | 0.465±0.151 | 0.132±0.062 | 72.3 |
| pubmed_only | 0.171±0.173 | 0.155±0.147 | 0.156±0.144 | 0.205±0.173 | 0.269±0.201 | 0.145±0.234 | 80.7 |


## Fixed-K metrics (K=10)

| Method | Recall@10 | Prec@10 | F1@10 | nDCG@10 | SetRecall | Prec@Retr | Cands/variant |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full_pipeline | 0.150±0.107 | 0.293±0.212 | 0.196±0.137 | 0.306±0.225 | 0.467±0.152 | 0.121±0.057 | 79.3 |
| full_pipeline_nested | 0.150±0.107 | 0.293±0.212 | 0.196±0.137 | 0.306±0.225 | 0.467±0.152 | 0.121±0.057 | 79.3 |
| multisource_no_translation | 0.088±0.078 | 0.167±0.143 | 0.113±0.098 | 0.169±0.143 | 0.472±0.229 | 0.087±0.045 | 100.0 |
| multisource_with_translation | 0.150±0.107 | 0.293±0.212 | 0.196±0.137 | 0.306±0.225 | 0.465±0.151 | 0.132±0.062 | 72.3 |
| pubmed_only | 0.112±0.095 | 0.207±0.176 | 0.141±0.118 | 0.230±0.183 | 0.269±0.201 | 0.145±0.234 | 80.7 |


## Paired Wilcoxon tests (Holm-corrected family)

| Test | Δ | p | p(Holm) | r | 95% CI | sig |
| --- | --- | --- | --- | --- | --- | --- |
| M2_vs_M1_set_recall | +0.203 | 0.0000 | 0.0001 | +0.77 | [+0.128, +0.277] | yes |
| M3_vs_M2_set_recall | -0.007 | 0.9056 | 1.0000 | +0.02 | [-0.087, +0.069] | no |
| M4n_vs_M3_set_recall | +0.002 | 0.5000 | 1.0000 | +1.00 | [+0.000, +0.004] | no |
| M4n_vs_M1_set_recall | +0.198 | 0.0000 | 0.0002 | +0.74 | [+0.123, +0.269] | yes |
| M4n_vs_M2_set_recall | -0.005 | 0.8506 | 1.0000 | +0.03 | [-0.086, +0.071] | no |
| M4n_vs_M1_recall20 | +0.105 | 0.0040 | 0.0161 | +0.51 | [+0.035, +0.170] | yes |
| M4n_vs_M1_ndcg50 | +0.162 | 0.0001 | 0.0004 | +0.66 | [+0.086, +0.234] | yes |
| M4n_vs_M3_prec_retr | -0.011 | 0.0000 | 0.0000 | -1.00 | [-0.016, -0.006] | yes |


## Run-to-run variance (duplicated M3 configuration)

- identical candidate lists: 18/30
- mean Jaccard: 0.891 (min 0.429)
- mean |Δ set recall|: 0.0215 (max 0.2222)


## 2025-2026 SOTA baselines (collected 2026-08-14)

## Fixed-K metrics (K=20) + set-level metrics

| Method | Recall@20 | Prec@20 | nDCG@20 | SetRecall | Prec@Retr | Cands | Rel. |
| --- | --- | --- | --- | --- | --- | --- | --- |
| pubmed_only | 0.171±0.173 | 0.155±0.147 | 0.205±0.173 | 0.269±0.201 | 0.145±0.234 | 80.7 | 213 |
| full_pipeline_nested | 0.276±0.149 | 0.265±0.160 | 0.301±0.184 | 0.467±0.152 | 0.121±0.057 | 79.3 | 394 |
| S1 Dense (bge-m3) | 0.182±0.164 | 0.163±0.134 | 0.229±0.168 | 0.269±0.201 | 0.145±0.234 | 80.7 | 213 |
| S2 Cross-encoder rerank | 0.171±0.173 | 0.155±0.147 | 0.205±0.173 | 0.269±0.201 | 0.145±0.234 | 80.7 | 213 |
| S3 Hybrid BM25+dense+rerank | 0.271±0.161 | 0.257±0.161 | 0.332±0.193 | 0.421±0.221 | 0.174±0.223 | 80.6 | 337 |
| S4 LLM query expansion | 0.173±0.171 | 0.156±0.145 | 0.202±0.165 | 0.270±0.201 | 0.145±0.234 | 80.7 | 214 |
| S5 Agentic 2-round search | 0.171±0.173 | 0.155±0.147 | 0.205±0.173 | 0.269±0.201 | 0.145±0.234 | 80.7 | 213 |
| S6 Multilingual dense (bge-m3) | 0.181±0.164 | 0.163±0.136 | 0.225±0.169 | 0.269±0.201 | 0.145±0.234 | 80.7 | 213 |

## SOTA paired tests vs M1 / M4n (SetRecall, Holm family)

| Test | Δ | p | p(Holm) | r | 95% CI | sig |
| --- | --- | --- | --- | --- | --- | --- |
| sota_dense_vs_pubmed_only_set_recall | +0.000 | 1.0000 | 1.0000 | +0.00 | [+0.000, +0.000] | no |
| sota_dense_vs_full_pipeline_nested_set_recall | -0.198 | 0.0000 | 0.0004 | -0.74 | [-0.269, -0.123] | yes |
| sota_rerank_vs_pubmed_only_set_recall | +0.000 | 1.0000 | 1.0000 | +0.00 | [+0.000, +0.000] | no |
| sota_rerank_vs_full_pipeline_nested_set_recall | -0.198 | 0.0000 | 0.0004 | -0.74 | [-0.269, -0.123] | yes |
| sota_hybrid_vs_pubmed_only_set_recall | +0.151 | 0.0006 | 0.0039 | +0.73 | [+0.077, +0.228] | yes |
| sota_hybrid_vs_full_pipeline_nested_set_recall | -0.046 | 0.3502 | 1.0000 | -0.17 | [-0.133, +0.041] | no |
| sota_expand_vs_pubmed_only_set_recall | +0.001 | 1.0000 | 1.0000 | +1.00 | [+0.000, +0.004] | no |
| sota_expand_vs_full_pipeline_nested_set_recall | -0.196 | 0.0000 | 0.0004 | -0.73 | [-0.268, -0.122] | yes |
| sota_agent_vs_pubmed_only_set_recall | +0.000 | 1.0000 | 1.0000 | +0.00 | [+0.000, +0.000] | no |
| sota_agent_vs_full_pipeline_nested_set_recall | -0.198 | 0.0000 | 0.0004 | -0.74 | [-0.269, -0.123] | yes |
| sota_mdense_vs_pubmed_only_set_recall | +0.000 | 1.0000 | 1.0000 | +0.00 | [+0.000, +0.000] | no |
| sota_mdense_vs_full_pipeline_nested_set_recall | -0.198 | 0.0000 | 0.0004 | -0.74 | [-0.269, -0.123] | yes |

## Neural-rerank optimization (offline, on frozen nested lists)

| Method | Recall@20 | Prec@20 | nDCG@20 | Prec@Retr | SetRecall | Rel. | Cands |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M4n baseline | 0.276±0.149 | 0.265±0.160 | 0.301±0.184 | 0.121±0.057 | 0.467±0.152 | 394 | 79.3 |
| M5 neural rerank | 0.276±0.149 | 0.265±0.160 | 0.301±0.184 | 0.122±0.057 | 0.467±0.152 | 394 | 78.9 |
| M5g gated citation | 0.276±0.149 | 0.265±0.160 | 0.301±0.184 | 0.131±0.062 | 0.465±0.151 | 392 | 72.6 |

Citation gating: kept 12/299 items, relevant found unchanged.
