# 实验设计优化计划（Experimental Design Optimization Plan）

**文档版本**: v1.0  
**制定日期**: 2026-10-13  
**适用目标**: *Bioinformatics* (OUP) Applications Note 投稿加固 / 审稿意见防御（Rebuttal-Ready）及长文（JCDL / JBI）扩展  
**关联文档**: [`../BIOINFORMATICS_PLAN.md`](../BIOINFORMATICS_PLAN.md) · [`README.md`](README.md)

---

## 〇、 核心诊断摘要：当前实验设计的四大软肋与优化策略

当前基准测试（43 个临床变异 / 822 篇文献）在**统计规范性**（配对 Wilcoxon、预声明 8 检验族 Holm 校正、10,000 次 Bootstrap、嵌套消融 `M4n`）与**溯源诚实度**（`check_numbers.py` 48/48 机器校验）上已达到高标准。但从审稿人视角审视代码与工件后，仍存在 **4 处结构性方法学软肋**。

本计划的设计原则是：**优先采用“基于冻结候选池（`collected_results.json`）的离线重算与外部独立真值映射”**，以**零/极低在线 API 采集成本**消除混杂变量与循环偏差，再辅以小样本精准补测闭环核心组件验证。

| 编号 | 审稿人高危攻击点（方法学缺陷） | 根本原因（代码与数据溯源） | 优化方案（可落地子实验） | 执行方式与工期 | 优先级 |
|---|---|---|---|---|---|
| **E1** | **`M2` vs `M3` 存在严重混杂变量**<br>（无法证明浅层排序增益来自多语言翻译而非排序器） | `multisource_no_translation.py` (`M2`) 直接返回 `search_parallel` 原始通道顺序，未执行去重与 `rank_candidates`；而 `M3` 调 `multilingual_acquisition_workflow` 同时启用了翻译+去重+词法/神经重排 | **构造受控中间基线 `M2r`（Ranked Multi-source, 无翻译）**：对冻结的 `M2` 候选池离线执行与 `M3` 完全一致的去重、词法排序及 `bge-reranker-v2-m3` 重排，严格正交拆解“多源”、“排序”与“翻译”三者的独立贡献 | 纯离线重算（基于 `collected_results.json`），**< 0.5 天** | **P0（最高）** |
| **E2** | **候选池同源与启发式循环偏差**<br>（Pooling & Heuristic Circularity Bias） | 822 篇参考集由 `PubMed + EuropePMC + Crossref` 经标题启发式打分生成，未经专家盲审；`M2–M4n` 恰好包含后两个源，导致 $+0.203$ 增益有“自我验证”嫌疑 | **E2a. 引入外部独立真值（ClinVar Curated Citations）**：通过 NCBI E-utilities 提取 43 个变异在 ClinVar 中由临床机构人工登记的真实证据 PMID/DOI 集合，零重采重算 `M1–M4n` 召回率<br>**E2b. 分层抽样双人专家盲标**：15 个变异 × 15 篇 = 225 篇，计算 Cohen's $\kappa$ | E2a：自动脚本 + 离线评估，**1 天**<br>E2b：人工标注，**5–7 天** | **P0（E2a）**<br>**P1（E2b）** |
| **E3** | **SOTA 基线受限于单源候选池**<br>（4/6 基线 SetRecall 恒等于 `M1`，被批“稻草人对比”） | `S1 (dense)`、`S2 (rerank)`、`S5 (agent)`、`S6 (mdense)` 仅在 ~80.7 篇的 PubMed 池内重排，数学上 SetRecall 不可能超 `M1` (0.269) | **解绑候选池（Multi-source Pool SOTA Evaluation）**：将 `S1`、`S2`、`S6` 迁移至冻结的多源候选池（`M2` 池）上离线运行，并在主表/附表中将“集合召回对比”与“同池 Top-$K$ 排序对比（Recall@20, nDCG@20）”双轨分离 | 离线嵌入/重排计算，**0.5–1 天** | **P1（高）** |
| **E4** | **核心模块验证缺口**<br>（未知语种黑洞、Relevance Gate 关闭、引文种子单薄） | 1. 305/822 篇参考集 `language="unknown"`（实测含漏标的德/法论文）<br>2. 主实验 `relevance_gate=False`<br>3. 引文仅用 top-1 DOI (`k=1`) 且带来 $-0.011$ 精度损失 | **E4a. 离线语种回填**：对 305 篇 `unknown` 论文按标题回填真实语种标签<br>**E4b. Relevance Gate 抽样评测**：100 篇样本实测开启 Gate 后的 Precision 增益与误杀率（FNR）<br>**E4c. 引文种子数 ($k\in\{1,3,5\}$) 与门控 (`M5g`) 消融** | E4a：离线脚本，**2 小时**<br>E4b/c：小样本 API + 评测，**1–2 天** | **P0（E4a）**<br>**P2（E4b/c）** |

---

## 一、 实验 E1：消除 `M2` vs `M3` 混杂变量（正交消融设计）

### 1.1 问题定位与证据
在当前 Table 1 中：
- `M1 (PubMed-only)`：SetRecall = `0.269`，Recall@20 = `0.171`，nDCG@20 = `0.205`
- `M2 (Multi-source, 无翻译)`：SetRecall = `0.472`，Recall@20 = **`0.153`**，nDCG@20 = **`0.164`**
- `M3 (+Translation)`：SetRecall = `0.465`，Recall@20 = **`0.276`**，nDCG@20 = **`0.301`**

代码审计表明，`benchmarks/baselines/multisource_no_translation.py` 在获取候选文献后直接按并发任务完成顺序输出，**既未调用 `dedupe_candidates`，也未调用 `rank_candidates`（词法相关性排序）或 `neural_rerank`**；而 `multisource_no_citation.py` (`M3`) 调用了完整的 `multilingual_acquisition_workflow`，内置了去重与排序。这导致 `M2 -> M3` 的 `+0.123 Recall@20` 混杂了“排序算法”与“多语言翻译”两个变量。

### 1.2 实验改进方案
无需重新请求 19 个外部数据源，直接基于 `benchmarks/results/collected_results.json` 中已冻结的 `multisource_no_translation` 候选列表，新增脚本 `benchmarks/analysis/deconfound_m2.py`，离线构造两个受控基线：
1. **`M2_lex`（Multi-source + Dedup + Lexical Rank, 无翻译）**：对 `M2` 原始列表执行 `dedupe_candidates` + `rank_candidates(query, candidates)`（即 lite edition 的默认排序策略）。
2. **`M2_rerank`（Multi-source + Dedup + Cross-Encoder Rerank, 无翻译）**：在 `M2_lex` 基础上使用与 `M3` 完全相同的 `BAAI/bge-reranker-v2-m3` 针对英文原查询重排。

### 1.3 正交消融链条（修正后的因果归因链）
$$\underbrace{	ext{M1}}_{	ext{PubMed}} \xrightarrow{	ext{+多源联邦}} \underbrace{	ext{M2\_raw}}_{	ext{未排序多源}} \xrightarrow{	ext{+去重与排序}} \underbrace{	ext{M2\_rerank}}_{	ext{单语多源排序}} \xrightarrow{	ext{+跨语言翻译路由}} \underbrace{	ext{M3}}_{	ext{多语言多源}} \xrightarrow{	ext{+引文网络 (嵌套)}} \underbrace{	ext{M4n}}_{	ext{全流程}}$$

### 1.4 预期产出与验收标准
- 产出工件：`benchmarks/results/m2_deconfounded.json`
- 统计检验：补充 `M2_rerank vs M1`（纯多源+排序增益）与 `M3 vs M2_rerank`（纯多语言翻译在整体集与德/法子集上的增益）的配对 Wilcoxon 检验。
- 论文修正：在正文 §3 与补充材料 §2 中明确区分“未排序多源池（`M2`）”与“排序后多源池（`M2r`）”，使因果推断无懈可击。

---

## 二、 实验 E2：破除候选池同源与启发式标注的循环偏差

### 2.1 问题定位与证据
`benchmarks/gold_standard/variants.jsonl` 的 822 篇相关文献由自动化脚本从 `PubMed + EuropePMC + Crossref` 检索并按标题关键词启发式打分（gene $+3$, variant $+5$, disease $+3$, case report $+2$）生成。审稿人最核心的质疑将是：
1. **同源循环性（Circularity）**：待测系统 `M2–M4n` 包含 `EuropePMC` 和 `Crossref`，而 `M1` 仅含 `PubMed`，因此 `M2` 超越 `M1` 是预料之中的同源命中，未必代表真实的临床证据发现能力。
2. **启发式噪声（Heuristic Noise）**：标题含基因名与疾病名不等于该文针对该特定变异提供 ACMG 级临床证据（PS3/PVS1/PM2 等）。

### 2.2 子实验 E2a：引入外部独立临床真值（ClinVar Evidence Benchmark）
利用 **NCBI ClinVar** 数据库中由全球临床遗传学实验室（如 GeneDx, Invitae, Ambry, ClinGen 专家组）在提交变异解读（SCV 记录）时**人工附带的支撑文献（Assertion Citations, PMIDs）**，构建完全独立于本项目的外部真值集：
1. **构建方法（`benchmarks/gold_standard/build_clinvar_ground_truth.py`）**：
   - 针对 `variants.jsonl` 中的 43 个变异（如 `BRCA1 c.5266dupC`、`TP53 p.R175H`、`HFE p.C282Y` 等），通过 NCBI E-utilities (`esearch[db=clinvar]` $
ightarrow$ `elink[dbfrom=clinvar, db=pubmed]` 或解析 ClinVar VCV XML 中的 `<Citation>`) 提取所有关联 PMID，并通过 Crossref/PubMed 补齐对应 DOI。
   - 过滤掉早于 ACMG 指南或纯综述性质的通用指南（如 Richards 2015 本身），保留变异特异性的临床证据文献，生成 `benchmarks/gold_standard/variants_clinvar.jsonl`。
2. **离线零成本评测（`benchmarks/analysis/clinvar_eval.py`）**：
   - 直接在已采集冻结的 `collected_results.json` 上，以 `variants_clinvar.jsonl` 为真值计算 `M1`、`M2`、`M3`、`M4n` 及 `S3 hybrid` 的 `SetRecall` 与 `Recall@20`。
3. **学术价值**：
   - ClinVar 文献由人类临床专家独立录入，**既不依赖本项目的标题打分规则，也不依赖本项目的候选池抽取通道**。只要 `M4n` 在 ClinVar 真值集上依然显著优于 `M1`，即可一举击碎“循环偏差”与“启发式伪真值”两项最致命的审稿质疑。

### 2.3 子实验 E2b：启发式参考集的分层抽样专家盲审（计算 Cohen's $\kappa$）
1. **抽样方案**：从 43 个变异中按疾病领域分层抽取 **15 个变异**，每个变异从候选池中随机抽取 15 篇文献（10 篇启发式判定正例 + 5 篇启发式判定负例，共 **225 篇**）。
2. **双级标注标准**：
   - **Level 1（主题相关，Topic-Relevant）**：文献确实研究了目标基因与目标疾病/表型。
   - **Level 2（变异级证据，Variant-Level ACMG Evidence）**：文献在摘要或正文中明确报告了该特定 HGVS 变异的病例、家系共分离或功能实验。
3. **统计输出**：
   - 报告两位独立标注者之间的 **Cohen's $\kappa$**（目标 $\kappa \ge 0.70$）；
   - 报告启发式协议相对于人工标注的 **Precision（阳性预测值）与估计假阴性率**，写入补充材料 §1。

---

## 三、 实验 E3：解绑 SOTA 基线候选池与双轨制对比

### 3.1 问题定位与证据
当前 6 个 SOTA 基线中，`S1 (dense)`、`S2 (rerank)`、`S5 (agent)`、`S6 (mdense)` 的实现均以 `M1` 的 PubMed 检索结果（平均 80.7 篇）为输入池进行重排序。因此：
- 在 `SetRecall` 指标上，这 4 个方法与 `M1`（`0.269`）完全相等，拿它们与拥有 19 个通道的 `M4n`（`0.467`）比 `SetRecall` 缺乏公平性。
- 相反，这些方法真正的强项在于**改善浅层排序（`Recall@20`、`nDCG@20`）**。

### 3.2 实验改进方案
新增脚本 `benchmarks/analysis/sota_fair_pool.py`，实施**双轨评估（Dual-Track SOTA Evaluation）**：
1. **Track 1 — 固定单源池下的浅层排序竞赛（Fixed PubMed Pool, Top-$K$ Ranking）**：
   - 固定候选池为 PubMed，专门对比 `M1 (BM25/Entrez)` vs `S1 (dense)` vs `S2 (cross-encoder)` vs `S3 (hybrid)` vs `S6 (multilingual dense)` 的 `Recall@20` 与 `nDCG@20`，诚实展示现代神经重排在单源池内的排序上限。
2. **Track 2 — 固定多源池下的重排竞赛（Unconstrained Multi-source Pool）**：
   - 将 `S1 (dense)`、`S2 (cross-encoder)`、`S6 (multilingual dense)` 应用于冻结的多源候选池（`M2`/`M3` 候选池）上进行离线重排（注：`benchmarks/results/rerank_eval.json` 中的 `M5` 实际上已完成了 `bge-reranker-v2-m3` 在 `M4n` 池上的离线重排验证，可直接复用并扩展至 `M2` 池）。
   - 证明：**“多源联邦（提供高召回底池） + 神经重排（提供高精度浅层排序）”是正交互补关系，而非互斥竞争关系**。

---

## 四、 实验 E4：补齐语种黑洞、相关性门控与引文扩展消融

### 4.1 子实验 E4a：305 篇 `unknown` 语种参考集文献的离线语种回填（零成本高收益）
- **现状发现**：对 `variants.jsonl` 的扫描显示，822 篇相关文献中有 **305 篇被标记为 `language: "unknown"`**（因 Crossref/PMC 元数据缺失 `language` 字段）。代码抽检已发现其中直接包含未标语种的**德语**（如 V002 *"Zur Risikokalkulation für Mutationen in den Genen BRCA1 und BRCA2..."*、V008 *"Beidseitiges Neoplasma mammae bei Cowden-Syndrom – ein Fallbericht"*）与**法语**（如 V002 *"Facteurs prédictifs de mutation germinale BRCA1..."*、V007 *"PALB2/FANCN, acteur dans la prédisposition au cancer du sein ?"*）文献！
- **执行方案（`benchmarks/analysis/backfill_languages.py`）**：
  - 使用确定性 Unicode 字符集检测（CJK/Cyrillic）结合高置信度标题语种识别（德/法/西/葡/英特征词与变音符号规则 + 人工复核全部非 ASCII 标题），将 305 篇 `unknown` 文献回填为真实语种标签，生成 `language_analysis_backfilled.json`。
  - **直接收益**：德/法（及潜在其他非英语）参考集基数将从 24 篇扩大至约 **30–35 篇**，统计检验效能（Power）立刻提升，且消除了审稿人对“37% 的真值语种未知”的质疑。

### 4.2 子实验 E4b：Relevance Gate（第 3 阶段 LLM 门控）的精度-召回权衡评测
- **问题**：Figure 1A 展示了完整的 `Search -> Translate -> Download -> Gate/rank` 流程，但基准采集中 `relevance_gate=False`，导致 LLM Gate 完全没有实测数字支撑。
- **执行方案（`benchmarks/analysis/gate_eval.py`）**：
  - 在 E2b 标注的 100–225 篇样本（或已下载的 18 篇全文 PDF + 82 篇含摘要候选）上，开启 `run_relevance_gate`。
  - 记录并报告：
    1. 开启 Gate 前后的 **Precision@Retrieved 变化**（预期显著上升）；
    2. Gate 的 **假阳性率（FPR）与假阴性率（FNR，误杀真实相关文献的比例）**；
    3. Token 消耗与单篇平均耗时（ms/paper）。

### 4.3 子实验 E4c：引文图遍历种子数（Top-$k$ Seeds）与门控（`M5g`）灵敏度分析
- **问题**：当前引文遍历仅取排名第 1 的 DOI 作为唯一种子（`k=1, depth=1, budget=20`），在 43 个变异上仅找回 2 篇相关文献，且使 `Precision@Retrieved` 下降 `-0.011`（$p_{	ext{Holm}}=1.9	imes 10^{-6}$）。
- **执行方案**：
  1. **直接启用已有工件 `rerank_eval.json` 中的 `M5g`（Gated Citation）结论**：只追加重排分数 $\ge$ `M3` Top-20 最低分的引文节点，展示如何在保留引文发现的同时消除精度下降代价。
  2. **种子数灵敏度实验（`k=1, 3, 5`）**：在 10–43 个变异上对比以 `M3` 前 $k$ 篇高分文献作为种子集合（多源种子 BFS）时的独有相关文献发现数与精度变化曲线。

---

## 五、 实施路线图与对现有稿件的影响控制

为确保不影响当前已就绪的 *Bioinformatics* Applications Note 投稿进度，实行**“两阶段渐进式落地”**：

### 阶段 I：零重采离线加固（投稿前可立即完成，耗时 1–1.5 天）
1. **[E4a] 离线语种回填**：运行脚本识别并人工核验 305 篇 `unknown` 文献的标题语种，在补充材料 §6 增加一段说明（保持原 21/24 指标不变作为主文基准，同时报告回填全部未知语种标签后的完整德/法恢复率）。
2. **[E1] 离线解混杂 `M2r` 分析**：对冻结的 `M2` 列表运行去重与排序，生成 `m2_deconfounded.json`，在补充材料 §3 增加 1 行 `M2 (ranked)` 对照，彻底堵住“排序混杂”漏洞。
3. **[E2a] ClinVar 独立真值映射**：拉取 43 个变异的 ClinVar 关联 PMIDs，在冻结列表上算出独立真值召回率，作为补充材料新增小节（*Validation against ClinVar-curated citations*）。

> **红线约束**：阶段 I 的所有新结果可优先作为补充材料（`supplementary.tex`）的加固证据或在 `main.tex` 中以半句话引用，所有改动必须同步更新 `paper/appnote/check_numbers.py`，确保校验脚本 **100% 通过**且正文严格维持 **3 页上限**。

### 阶段 II：小样本实测与人工标注（审稿周期内并行推进，耗时 1–2 周，用于 Revision / 长文扩展）
1. **[E2b] 完成 15 变异 × 15 篇的双人专家盲标**，计算 Cohen's $\kappa$ 与启发式协议准确率；
2. **[E4b] 完成 100 篇样本的 Relevance Gate 开启实测**，获取 FPR/FNR 与耗时；
3. **[E3 & E4c] 完成多源池 SOTA 对比与引文 Top-$k$ 种子灵敏度曲线**，同步支撑后续完整方法学长文（JBI / JCDL）。

---

## 六、执行状态（2026-10-14 更新）

阶段 I 全部完成，阶段 II 的可离线/可自动化部分完成，人工标注部分已交付可直接开工的工具包。
所有新结果均落在 `benchmarks/results/*.json` 与 `paper/appnote/tables/supp_hardening.tex`
（由 `paper/appnote/make_review_hardening.py` 从工件生成，禁止手抄数字）。
`check_numbers.py` 现为 **70/70 通过**，`main.tex` 维持 **3 页**，`supplementary.pdf` 编译通过（10 页）。

| 计划项 | 状态 | 工件 / 脚本 | 关键结果 |
|---|---|---|---|
| **E1** 解混杂 `M2r` | ✅ 完成（超出计划：改为对称 2×2 因子设计 + 双参考集） | `benchmarks/analysis/deconfound_m2.py` → `results/m2_deconfounded.json` | 多源联邦 SetRecall +0.203（$p_{\rm Holm}$=0.0001）；去重+词法排序 Recall@20 +0.065（Holm 显著）；**翻译在排序固定下 +0.088（词法层）/ +0.127（重排层）**且 SetRecall 不变；标题级 cross-encoder 在本参考集上无增益甚至有害（-0.066，Holm 显著），已按“参考集依赖”如实报告 |
| **E2a** ClinVar 独立真值 | ✅ 完成 | `benchmarks/gold_standard/build_clinvar_ground_truth.py`、`benchmarks/analysis/clinvar_eval.py` → `results/clinvar_ground_truth.json`、`results/clinvar_eval.json` | 36/43 变异有 ClinVar 记录、3,275 条临床实验室引文；**结论为阴性**：该真值上流水线不优于 PubMed-only，原因是时间偏斜（真值中位年份 2011 vs 检索池 2025）。已在补充材料中如实报告并据此收窄主张 |
| **E2c**（执行中新发现，非原计划）池化参考集扩充 | ✅ 完成 | `benchmarks/analysis/pooled_gold.py`、`benchmarks/analysis/expanded_reference.py` → `results/expanded_reference.json` | 用项目**自身**打分规则对 10 个系统并集池（11,161 篇）重判，参考集 822→1135；所有头部结论在扩充集上保持（M4n−M1 SetRecall +0.184，$p_{\rm Holm}$=0.0003） |
| **E3** 解绑候选池 SOTA | ✅ 完成 | `benchmarks/analysis/sota_fair_pool.py` → `results/sota_fair_pool.json` | 双轨评估：同池内 SetRecall 恒定，跨轨 0.262→0.505 证明**增益来自联邦而非排序**；在冻结池上重跑 S1/S2/S3/S6 排序器（修正后），无一超过流水线自身词法排序 |
| **E4a** 语种回填 | ✅ 完成 | `benchmarks/analysis/backfill_languages.py` → `results/language_analysis_backfilled.json` | 305 篇未标注中判定 300 篇；德 13→**18**、法 11→**20**；留出集准确率 0.984、非英语精确率 0.933；另审计出 **8 例 provider 标签错误**（5 篇中文题名被标 en，3 篇由期刊名佐证） |
| **E4b** Relevance Gate 实测 | 🟡 已实测（替代模型，待生产模型复跑） | `benchmarks/analysis/gate_eval.py` → `results/gate_eval.json` | 生产模型 `gpt-5`（linxi.chat）当时持续 429；改用同一 prompt 的 `Qwen2.5-72B-Instruct` 替代并显式标注；120 篇平衡样本：Gate 保留 96.7%、误杀 2/60 相关文献（FNR 0.033）、放行 58/60 负例（FPR 0.967），即摘要级输入下 Gate 近似直通、并非有效的精度过滤器；成本 1,233 ms/篇、约 1,650 字符/篇；同时把“全错误”情形改为 `provider_unavailable` 而非输出伪指标 |
| **E4c** 引文种子与门控 | ✅ 完成 | `benchmarks/analysis/rerank_eval_corrected.py`、`benchmarks/analysis/citation_seeds.py` → `results/rerank_eval_corrected.json`、`results/citation_seeds.json` | M5g 门控在保留 2 篇引文独有文献的同时把 Precision@Retrieved 提升 +0.0103（$p_{\rm Holm}<10^{-4}$）；种子 k=1/3/5：独有相关文献 2→3→6，但精度显著下降（$p_{\rm Holm}<0.001$），故 k=1+门控为推荐工作点 |
| **E2b** 双人盲标 | 🟡 工具包已交付，待人工标注 | `benchmarks/annotation/build_e2b_sample.py`、`benchmarks/analysis/e2b_agreement.py` → `benchmarks/annotation/*.csv`、`README.md` | 15 变异 × 15 篇 = 225 篇（150 正例 + 75 负例，疾病分层，固定种子），双份空白表 + 分析师密钥 + Cohen's κ/精确率/假阴性率分析脚本 |
| **缺陷修复**（执行中发现） | ✅ 完成 | `benchmarks/baselines/sota_common.py`、`benchmarks/analysis/api_scores.py`、`tests/test_rerank_ordering.py` | 共享重排助手 `rerank_docs` 曾把 provider 结果按**输入序**还原，导致记录在案的 S2/S3 基线与 `rerank_eval.json` 的 cross-encoder 阶段实际为空操作；已修复、加回归测试，并以离线缓存路径重算所有受影响结论 |

**对现有稿件的影响**：`main.tex` 仍为 3 页，新增内容以半句话引用补充材料；`supplementary.tex`
新增九个小节（参考集扩充、E1 因子设计、语种回填、ClinVar 真值、双轨 SOTA、重排缺陷与门控、
种子灵敏度、Gate 实测、标注包状态）。Cy 与 `sota_baselines.json` 中受缺陷影响的
S2/S3 数字已在补充材料中**以修正后的离线重算结果覆盖说明**；未改动历史工件本体以保留溯源。
