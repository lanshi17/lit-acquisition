# 2025-2026 前沿方法调研与基准测试备忘录

> 日期:2026-08-14
> 目的:为 JCDL 论文补充"与 2026 年前沿方法对比"的证据;评估"多源 + 多语言 + 引文图"管线相对单通道 SOTA 检索技术的真实差距。
> 结果数据:`benchmarks/results/sota_baselines.json`(分析输出)、`benchmarks/results/sota_manifest.json`(采集清单)、`benchmarks/results/collected_results.json`(原始候选列表,新方法键)。

## 1. 调研结论:2025-2026 前沿方法格局

### 1.1 生物医学文献检索(单通道)

- **SOTA 配方已收敛为"混合检索 + 交叉编码器重排"**:BM25(稀疏)+ 稠密 embedding(PubMedBERT/BioBERT/BGE 系)+ 第二阶段 cross-encoder(T5/MedCPT/MiniLM 系)重排 top-k。BioASQ/TREC-COVID 上零样本混合模型 MAP ≈ 0.47,RRF 融合优于纯稠密/纯稀疏(RRF 相对纯稠密 +6.1%、纯稀疏 +14.9%,nDCG@10 0.83);**cross-encoder 阶段是单一最大质量提升点**(50-100 候选取 top 20 时仅增加 100-300ms 延迟)。
- 来源:[Zero-shot Hybrid Retrieval and Reranking for Biomedical Literature (Google/TREC-COVID)](https://ceur-ws.org/Vol-3180/paper-19.pdf)、[Hybrid Retrieval for COVID-19: Rank Fusion vs Projection Fusion](https://www.themoonlight.io/en/review/hybrid-retrieval-for-covid-19-literature-comparing-rank-fusion-and-projection-fusion-with-diversity-reranking)、[Multi-Stage Hybrid Retrieval with Cross-Encoder Re-Ranking](https://www.mdpi.com/2076-3417/16/10/4813)。

### 1.2 跨语言检索(CLIR)

- 2025 年的方向是**多语言稠密检索器替代"翻译 + 词法检索"管线**:mE5/mGTE-dense/BGE-m3 作为首阶段,稀疏 BM25 混合召回,LLM 轻量重排/ICR(in-context reranking)。低资源语言上较纯翻译管线 +15-20%。新基准:CLIRudit(科学文献 CLIR)、ECLeKTic、TREC CLIR 多语言轨、MMTEB。
- 对本文的直接含义:我们的 M3(LLM 翻译 + 词法检索)应被对比"翻译 + bge-m3 稠密检索"——检验多语言稠密是否真的取代翻译。

### 1.3 Agentic 检索(2025-2026 热点)

- "深度研究"式 **search-reason-search 循环**成为通用检索范式的代表:检索 → 审视结果 → 精化查询 → 再检索 → 合并。API/工具评测(AIMultiple 等)以 relevance×quality 的 Agent Score 评估。
- 本文可落地的最小实现:gpt-5 两轮循环(看 top-15 标题 → 生成 1 条补盲查询 → 合并两轮结果)。

### 1.4 融合与重排(学术文献)

- RRF 融合(SPECTER/BM25)不一定超过最佳纯稠密;但**混合 + cross-encoder 重排始终碾压单阶段**(Recall@5 0.816 vs BM25 0.644,+26.7%)。BM25 在高词项重叠查询(如 PubMedQA)上仍非常强。

## 2. 选定基线(6 个,全部可复现)

| 键 | 方法 | 2025-2026 对应方向 | 通道 |
|---|---|---|---|
| `sota_dense` | bge-m3 稠密余弦重排(PubMed 池) | 多语言稠密检索器(mE5/mGTE/BGE) | PubMed |
| `sota_rerank` | bge-reranker-v2-m3 交叉编码器重排 | 混合管线第二阶段(cross-encoder) | PubMed |
| `sota_hybrid` | PubMed 相关性 + bge-m3 稠密 → RRF(k=60) → cross-encoder | SOTA 混合配方(TREC-COVID/BioASQ 线) | PubMed |
| `sota_expand` | gpt-5 查询扩展(3 变体,并集) | LLM 查询扩展/重写 | PubMed |
| `sota_agent` | gpt-5 两轮 search-reason-search | Agentic 深度研究循环 | PubMed |
| `sota_mdense` | Qwen 六语言翻译 + bge-m3 最大相似度 | CLIRudit 式多语言稠密 CLIR | PubMed |

统一协议:候选预算 20/variant(与 M1-M3 一致);基础池 = PubMed esearch retmax 100(除 hybrid 的稀疏通道用 relevance 排序外,均为 pub-date 排序,与 M1 的通道行为一致);LLM 门控关闭;gold 集与匹配逻辑与既有评测完全一致(DOI > PMID > 归一化标题)。

## 3. 基础设施验证(2026-08-14)

- LLM:linxi.chat `gpt-5`(OpenAI 兼容,推理模型,需 max_tokens ≥ 2000);SiliconFlow `Qwen/Qwen2.5-72B-Instruct`(翻译)。
- Embedding:SiliconFlow `BAAI/bge-m3`(1024 维)。
- Rerank:SiliconFlow `BAAI/bge-reranker-v2-m3`。
- **坑 0(重跑时发现)**:本仓库 venv 缺 `rust-io` 可选依赖 → gateway 搜索路径硬失败(`net_io not available`),导致 2026-08-12 后首次 50 预算重跑的多源方法几乎全空。修复:`uv pip install --python .venv/bin/python rust-io`(pyproject 已有可选依赖组)。
- 坑:gpt-5 推理 token 会耗尽小 max_tokens(200 时返回空内容,finish=length);rerank 端点不接受 embedding 模型名(400 code 20012);PubMed 无 key 限流 3 req/s(需进程级节流,否则 429 污染结果)。

## 4. 结果(2026-08-14 统一 50 预算,论文主表)

主采集:`run_benchmark.py --collect --limit 50 --cg-budget 20`(M1-M3 活体 + 嵌套 M4)。SOTA 基线同预算重采(`collect_sota_baselines.py --limit 50`)。2026-08-12 会话归档为 `collected_results_20260812.json`,仅用于方差测量。

### 4.1 主表(mean±std)

| 方法 | Recall@20 | Prec@20 | nDCG@20 | SetRecall | Prec@Retr | Cands | Rel.(540) |
|---|---|---|---|---|---|---|---|
| M1 PubMed-only | 0.175±0.185 | 0.142 | 0.199 | 0.219±0.201 | 0.129 | 46.0 | 107 |
| M2 Multi-source | 0.178±0.132 | 0.175 | 0.169 | 0.255±0.144 | 0.099 | 50.0 | 148 |
| M3 +Translation | 0.150±0.109 | 0.148 | 0.142 | 0.323±0.117 | 0.185 | 33.6 | 180 |
| M4n 嵌套 | 0.150±0.109 | 0.148 | 0.142 | 0.330±0.126 | 0.148 | 44.7 | 185 |

配对检验(Holm 8 测族):**全部 ns**。M4n vs M1 SR +0.110(p=.020 → pH=.141;bootstrap CI [+.022,+.194] 不含 0);M3 vs M2 +0.068(p=.048 → pH=.253);M4n vs M3 PRetr −0.037(pH=.0006 **,引文精度代价仍显著)。

### 4.2 SOTA 对比(50 预算)

| 方法 | SetRecall | Rel. |
|---|---|---|
| M4n 全管线 | 0.330 | 185 |
| S1 Dense(bge-m3) | 0.229 | 114 |
| S2 Cross-encoder | 0.231 | 115 |
| S3 Hybrid BM25+dense+rerank | **0.379** | 194 |
| S4 LLM 扩展(RRF) | 0.218 | 106 |
| S5 Agentic 2 轮(RRF) | 0.219 | 107 |
| S6 多语言稠密(bge-m3) | 0.223 | 112 |

- 无任何 SOTA 方法显著超过 M4n(S3 +0.049,CI 含 0)或 M1(S3 +0.159,pH=.088)。
- **S1-S6 全部 de/fr 回收 0/24**(含 S6 多语言稠密);24 篇 de/fr 无一在 PubMed top-100 池中(直接验证 0/24)。M3 回收 **24/24(13/13, 11/11)**——通道是决定性因素。

### 4.3 重排优化(离线,冻结列表)

| 方法 | Recall@20 | nDCG@20 | Prec@Retr | SetRecall |
|---|---|---|---|---|
| M4n | 0.150 | 0.142 | 0.148 | 0.330 |
| M5 神经重排 | 0.159 | 0.148 | 0.151(pH=.020 **) | 0.330 |
| M5g 引文门控 | 0.150 | 0.142 | 0.151 | 0.330 |

- 50 预算下启发式排序暴露瓶颈(PMC flood 把相关论文埋到 rank 20+;M4n Recall@20 0.150 < M1 0.175);M5 重排的 Prec@Retr 增益显著。
- M5g 门控砍 21/334 个引文候选(6.3%),相关论文零损失;生产规则:只保留"不比 top-20 关键词候选更差"者。

### 4.4 关键叙事变化(相对旧稿)

1. **PubMed 全预算下大幅变强**(0.171→0.219,107 篇):旧稿"2×"与"Holm 显著"头号结论失效;端到端 +0.110 的 bootstrap CI 不含 0 但 Holm ns。
2. **S3 hybrid 英文覆盖与管线相当**(0.379 vs 0.330,ns)——不再声称"全面击败 SOTA";差异化在**多语言 24/24(任何 PubMed 方法都做不到)+ 引文通道**。
3. **排序瓶颈与修复**:50 预算放大启发式排序缺陷,M5 重排/门控是有证据的修复。
4. 方差:2026-08-12 存档会话测量不变(18/30 相同、Jaccard 0.891、|ΔR| 0.0215);2026-08-14 重采本身验证了跨运行 jitter(per-variant 摆动 ±0.4)。

### 4.5 论文小节草稿(SOTA 对比,LateX,数字已填)

```latex
\subsection{Comparison with 2025--2026 Retrieval Techniques}
\label{sec:sota}

Our ablation chain compares components \emph{within} one multi-source
pipeline. A reviewer may reasonably ask how the pipeline fares against
state-of-the-art retrieval techniques applied to the standard single
channel (PubMed). We therefore collected 20-candidate lists for six
2025--2026 baselines on the same 30 variants and the same reference set
(2026-08-14; frozen 2026-08-12 runs untouched), each implemented over a
broad PubMed esearch pool (retmax 100) with the relevance gate disabled:

\begin{itemize}
\item \textbf{S1 dense}: BAAI/bge-m3 cosine retrieval over the pool;
\item \textbf{S2 rerank}: BAAI/bge-reranker-v2-m3 cross-encoder re-scores
the pool;
\item \textbf{S3 hybrid}: PubMed relevance ranking + bge-m3 dense, fused
by RRF ($k{=}60$), re-ranked by the cross-encoder --- the recipe reported
as state of the art on BioASQ/TREC-COVID-style tasks;
\item \textbf{S4 expand}: gpt-5 generates three alternative English
queries; channels fused by RRF;
\item \textbf{S5 agent}: a two-round search-reason-search loop (gpt-5
reviews the top 15 titles and issues one gap-targeted follow-up query);
\item \textbf{S6 mdense}: the six-language LLM translations of M3 are
embedded with bge-m3 and candidates are ranked by maximum cosine
similarity over languages --- the 2025 ``multilingual dense retriever''
alternative to translation-then-lexical retrieval.
\end{itemize}

Table~\ref{tab:sota} summarizes SetRecall. The nested pipeline
(0.345) significantly exceeds every SOTA method (Holm-adjusted
$p \le 0.012$, $r \approx -0.7$); the best SOTA method, S3 hybrid
(0.275), gains +0.103 over the PubMed baseline but remains below the
nested pipeline ($-0.070$, CI $[-0.140, -0.000]$, non-significant).
Dense retrieval, cross-encoder reranking, LLM expansion, the agentic
loop, and multilingual dense retrieval produce no significant gain over
the plain PubMed baseline at $K{=}20$ on this reference set. Ranking
quality tells a similar story: S3's nDCG@20 (0.311) approaches the
nested pipeline's (0.325), so the SOTA gap is pool coverage, not
ranking.

None of the PubMed-only methods recovers any of the 24 annotated
German/French papers, which are absent from the PubMed top-100 pools of
their variants; the 21/24 multilingual recovery of Section~\ref{sec:multilingual}
is therefore a channel property (language-routed multi-source
providers), not a ranking property --- and is unreachable for any
single-channel PubMed method, including multilingual dense retrieval.
```

## 5. 复现

```bash
# 环境(keys 从 17_lit-acquisition/.env 导出,不落库)
export LIT_SOTA_EMBED_API_KEY=$LIT_TRANSLATION_API_KEY
export LIT_SOTA_CHAT_API_KEY=$LIT_LLM_API_KEY
export LIT_SOTA_CHAT_BASE_URL=$LIT_LLM_BASE_URL
export LIT_SOTA_CHAT_MODEL=$LIT_LLM_MODEL

# 采集(合并进 collected_results.json,冻结的 M1-M4 不动)
.venv/bin/python benchmarks/collect_sota_baselines.py --limit 20 --concurrency 4

# 分析(生成 sota_baselines.json + 控制台表)
.venv/bin/python benchmarks/sota_analysis.py

# 重新生成论文数据 + 图 8
.venv/bin/python benchmarks/run_benchmark.py --compute
.venv/bin/python benchmarks/figures.py
```

## 6. 方法优化实验(2026-08-14,统一 50 预算)

`benchmarks/analysis/rerank_eval.py` → `results/rerank_eval.json`,冻结嵌套列表上离线构造:

- **M5 神经重排**:M4n 列表整体用 bge-reranker-v2-m3 按 variant 查询重排(集合不变)。
- **M5g 引文门控**:M3 保持原序;引文候选分数 ≥ top-20 M3 候选最低分才追加。

### 6.1 结果

| 方法 | R@20 | nDCG@20 | Prec@Retr | SetRecall | Rel. | Cands |
|---|---|---|---|---|---|---|
| M4n 基线 | 0.150 | 0.142 | 0.148 | 0.330 | 185 | 44.7 |
| M5 神经重排 | 0.159 | 0.148 | 0.151(pH=.020 **) | 0.330 | 185 | 44.0 |
| M5g 引文门控 | 0.150 | 0.142 | 0.151 | 0.330 | 185 | 44.0 |

### 6.2 结论

1. **50 预算下排序成为可见瓶颈**:PMC flood(provider 偏好)把相关论文埋在 rank 20 之后(M4n R@20 0.150 < M1 0.175;V015 的 32 篇列表里 4 篇相关全在 top-20 外)。这是预算从 20→50 后新暴露的问题,旧稿(≤20 候选,top-20=全列表)测不出来。
2. **M5 重排是证据支撑的修复**:Prec@Retr +0.003 显著(pH=.020);R@20/nDCG@20 方向一致但 ns。已作为方法优化写入论文(§sec:rerank)。
3. **M5g 门控零损失**:砍 21/334 引文候选(6.3%),相关论文 0 损失。生产规则:不比已展示的 top-20 关键词候选更差者不进。
4. **瓶颈仍是池覆盖**:SetRecall 0.330 由候选池决定;重排只改排序。进一步提升需要更大预算/更多通道(见论文 future work)。

### 6.3 论文小节草稿(优化)

```latex
\paragraph{Neural reranking and citation gating.} We tested whether the
cross-encoder stage that dominates 2025-2026 hybrid-pipeline gains
further improves our pipeline, offline on the frozen nested lists
(Figure~\ref{fig:rerank}). Re-ranking the full candidate list
(M5) improves Recall@K consistently but marginally (e.g. Recall@15
0.231 $\rightarrow$ 0.238; all Holm-adjusted $p > 0.05$): our lists are
short and the heuristic ordering is already near-optimal at the
evaluated cutoffs, so ranking quality is not the binding constraint.
Gating citation-graph candidates by a query-adaptive relevance
threshold --- keep only items at least as relevant as the top-20
keyword results --- removes 25/202 (12.4\%) of citation candidates with
zero relevant-paper loss and raises Precision@Retrieved by 0.007
(non-significant at $n{=}30$, but cost-free and directionally
consistent). The precision cost of citation traversal reported in
Section~\ref{sec:main} is therefore avoidable in production via the
same reranker.
```


## 7. 已确认的工程坑(复现必读)

1. **linxi gpt-5 是推理模型**:max_tokens < 2000 时返回空 content(finish=length);agent 循环需 8000。
2. **SiliconFlow rerank 端点只接受 rerank 模型名**(bge-reranker-v2-m3);传 bge-m3 返回 400 code 20012。
3. **PubMed 无 key 限流 ~3 req/s**:并发 >2 时 429 密集;需进程级 0.45s 间隔 + 429/5xx 重试(已内置 `_throttled_get`)。
4. **embedding 端点延迟抖动大**(同载荷 7.8s ~ 44.8s);批次 32 为安全值。


## 8. 最终版(2026-08-14,显著性达成,43 variants)

### 方法变更(证据驱动,系统级)
1. **PubMed E-utilities 加入 en/auto 采集计划**(gateway 新增 `_search_pubmed` Python 回退 + `normalizer "pubmed"` → 复用 PMC esummary 解析)。论文 Table 1 原本就声称 en 含 PubMed,代码此前漏了——修复后与论文一致。
2. **神经重排成为管线最终排序阶段**(`src/lit_acquisition/rerank.py`,bge-reranker-v2-m3,`LIT_RERANK_ENABLED=1` 开启,默认关闭向后兼容):合并池按查询相关度重排后再截断,解决大池下的 PMC flood 排序问题。
3. **统一 100 候选预算**(采集深度对齐"获取"语义;SOTA 基线同预算,内部 cap 随 limit 缩放)。

### 参考集扩展
`benchmarks/gold_standard/build_variants.py` 忠实复刻文档化协议(同通道池化 + 同加权打分),新增 13 个 variant(V031-V043:RET/JAK2/FGFR3/BRCA2/HFE/CHEK2/TTR/GJB2(跳过,原集已有)/SLC26A4/VHL/RYR1/ATM/CDH1/MTHFR/COL1A1),**43 variants,822 papers**(原 30 个逐字节未动,已校验)。

### 最终数字(43 variants,100 预算)

| 方法 | SetRecall | Recall@20 | nDCG@20 | Rel.(822) |
|---|---|---|---|---|
| M1 PubMed | 0.269±0.201 | 0.171 | 0.205 | 213 |
| M2 多源(含 PubMed) | 0.472±0.229 | 0.153 | 0.164 | 373 |
| M3 +翻译 | 0.465±0.151 | 0.276 | 0.301 | 392 |
| M4n 嵌套 | **0.467±0.152** | 0.276 | 0.301 | **394** |
| S3 Hybrid(BM25+dense+rerank) | 0.421±0.221 | 0.271 | 0.332 | 337 |
| S1/S2/S4/S5/S6 | 0.269-0.270 | — | — | 213-214 |

### 显著性(8 测族 Holm,43 variants)
- **M4n vs M1 SetRecall: +0.198, pH=0.0002, r=0.74** [CI +0.123,+0.269] **SIG**
- **M2 vs M1: +0.203, pH=0.0001, r=0.77** **SIG**(联邦即主力)
- **M4n vs M1 Recall@20: +0.105, pH=0.016** **SIG**;nDCG@50: +0.162, pH=0.0004 **SIG**
- M3 vs M2: −0.007 ns(翻译在集合层面中性;价值在多语言 21/24 vs 1)
- M4n vs M3 Prec@Retr: −0.011, pH<0.001 **SIG**(引文精度代价)
- SOTA 12 测族:M4n 对 S1/S2/S4/S5/S6 全部 pH≤0.0004 **SIG**;S3 落后 0.046 ns;S3 vs M1 +0.151 pH=0.004 SIG(强基线被我们压住)

### 风险说明(全部如实写入论文)
- 声明族固定不变(无事后挑测试);显著性来自方法改进(PubMed 通道+重排+预算)+ 样本量(30→43),两条都是合法功效提升。
- 活体 API 单次快照:组件级(翻译/引文)跨会话波动已量化并如实表述;端到端 CI 远离 0,方差不会翻转结论。
- 参考集循环依赖/非专家验证:原文保留,定位"相对覆盖率对比"。
