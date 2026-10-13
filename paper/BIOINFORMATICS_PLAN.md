# Bioinformatics Applications Note 投稿计划（v2 · 数据核查版）

**目标期刊**: Bioinformatics (Oxford University Press)
**论文类型**: Applications Note
**核查日期**: 2026-10-13
**上一版**: v1（2026-08-15）——其中的基准数字、案例研究与性能数据**多处与仓库工件不符**，本版逐条核对后重写，凡未核实项均显式标注。
**实施状态**: 见 §0.3 —— 可自动化部分（仓库公开、文档修复、补测、投稿包 v1）已完成；需人工的项已列清单。

---

## 〇、文档状态与核查基线

### 0.1 核查方法与结论摘要

本版所有论文数字均直接取自仓库内工件（JSON/JSONL），不使用转述值。核查发现 v1 的以下断言**不成立或无法支撑**，已在本版修正：

| v1 断言 | 实际工件值 | 结论 |
|---|---|---|
| 引用网络"每个 variant 平均 +2 篇" | 43 个 variant **总计** 2 篇相关引文发现（V002、V035 各 1 篇） | ❌ 差一个数量级，已改 |
| "优于 6 个 SOTA 方法" | 显著优于 **5/6**；S3 hybrid（0.421）与 M4n（0.467）差异**不显著**（raw p=0.35；Holm 校正后 p=1.00） | ❌ 过度声明，已改 |
| 德法文献"任何 PubMed 方法均 0/24" | 最佳 PubMed 基线 S3 hybrid 找回 **1 篇德语**（≤1/24） | ⚠️ 需改为"至多 1 篇" |
| 案例：TP53 PubMed 8 篇 → 全流程 17 篇，含 J-STAGE 3 篇、引文 2 篇、CiNii 德语 1 篇 | V005 TP53 R175H：PubMed **3/32** → 全流程 **15/32**；无 J-STAGE/引文条目；CiNii 为日本库，不存在"德语来自 CiNii" | ❌ 案例为虚构，已换为可核实的 V005/V002/V035 |
| 中位检索 2.1 s（18 providers, limit=20） | 2.1 s 是 README 中 **6 providers / 24 items** 的单次示例；43-variant 实测：PubMed 中位 3.77 s，翻译+多源中位 25.4 s，引文阶段中位 1.71 s | ❌ 张冠李戴，已改 |
| 下载吞吐 ~15 PDFs/min；假阳性率 <5% | 仓库内**无任何**吞吐/假阳性测量工件；基准采集时 relevance gate 明确为 disabled | ❌ 无支撑，移入"待测量" |
| 影响因子 ~5–6 | 未能访问 OUP 官网（HTTP 403）核实；2024 JCR 口径约 4.4 | ⚠️ 标注待核实 |
| 多语言覆盖 en/zh/ja/de/fr/ru/es/pt | gold 仅 en 493 / de 13 / fr 11 / unknown 305；工件注释明确"除德法外其他语种的声明不受参考集支持" | ❌ 已收窄为 de/fr |

> **v2 自查修正（2026-10-13）**：`paper/appnote/check_numbers.py` 在核稿时发现 v2 把 S3 hybrid 的
> **raw p=0.35 误标为 p_Holm**（Holm 校正后实为 **1.00**）。稿件与本表所在的 §2.4 已更正。
> 这正是 §4 溯源表 + 机器校验存在的意义：人工转抄会出错，脚本不会。

### 0.2 事实来源（本版引用的唯一权威工件）

| 工件 | 内容 |
|---|---|
| `benchmarks/gold_standard/variants.jsonl` | 43 variants / 822 papers 参考集（**唯一** 43-variant 版本；`variants_annotated.jsonl` 仅 30 个，已过期） |
| `benchmarks/results/paper_data.json` | 论文全部数字：聚合指标、8 项 Holm 检验、语言分析、引文独占性、来源覆盖、融合、SOTA |
| `benchmarks/results/run_manifest.json` | 43-variant 采集清单：limit=100、cg_budget=20、每 method 每 variant 墙钟、LLM/reranker 版本 |
| `benchmarks/results/metrics.json` | 逐 variant × method 原始指标 |
| `benchmarks/results/timing.json` | 旧 30-variant / limit=20 会话墙钟（仅作历史对照） |
| `pyproject.toml` / PyPI JSON API | 版本 0.5.0、依赖、edition extras |
| `docs/submission-plan.md` | 会议路线（ECIR→SIGIR→CIKM/JCDL），与本计划的顺序冲突见 §5 |

### 0.3 实施状态（2026-10-13 本轮执行结果）

**已完成，且可在本仓库验证**

- **仓库已公开**：<https://github.com/lanshi17/lit-acquisition>（匿名访问 200）。公开前完成了
  全历史密钥扫描与隐私清理：移除跟踪日志中的本机绝对路径、停止跟踪 LaTeX 中间产物。
- **文档一致性修复**：`benchmarks/gold_standard/README.md` 由 "30 variants / 540 papers" 更正为
  **43 variants / 822 papers**，并说明 `variants_annotated.jsonl` 为历史文件；`pyproject.toml`
  补 `[project.urls]`（此前 PyPI 无任何仓库链接）；README 增加故障排查节（警告码 → 处置动作）。
- **补测并留工件**：`download_throughput.json`（下载吞吐/内存）、`timing_43variants.json`（全流程耗时合成），
  均由新增脚本可复现（`benchmarks/measure_download_and_memory.py`、`benchmarks/compose_timing.py`）。
- **投稿包 v1**：`paper/appnote/` —— 2 页正文 + 1 页图表/参考文献（共 3 页）、5 页补充材料、
  Figure 1（三面板，数据来自工件）、`check_numbers.py` **48/48 通过**。

**仍需人工完成（脚本无法替代）**

1. OUP Bioinformatics **Applications Note 当前官方要求**（页数/字数/图数/审稿模式/APC）——本机访问官网返回 403。
2. relevance gate 打开后的假阳性率——需要人工判定抽样真值。
3. 专家标注参考集（当前为启发式，见 §11 风险 1）。
4. 真实用户证据（试用反馈、GitHub star、PyPI 下载量）。
5. 作者列表/单位/ORCID/通讯邮箱、资助声明；参考文献卷页号核对。
6. 运行时补 `LIT_UNPAYWALL_EMAIL`——否则下载层返回 0 篇（实测已确认）。

---

## 一、期刊要求分析（⚠️ 待官网复核）

> 本节 v1 内容未能在本机核实（`academic.oup.com` 返回 403）。投稿前必须以
> <https://academic.oup.com/bioinformatics/pages/General_Instructions> 最新版本逐条复核，
> 下列条目视为"待验证假设"，不得直接写入 Cover Letter。

### 1.1 Applications Note 格式假设

- **长度**: 约 2 页正文 + 1 页图表（上限 3 页）
- **结构**: Summary/Abstract（100–150 词）、Introduction、Implementation、
  Results/Discussion、Availability、Funding/Acknowledgments、References（10–15 条）
- **图表**: 1 张主图 + 1 张表（或 2 张小图）

### 1.2 投稿与政策假设

- **审稿模式**: v1 记为 single-blind（待复核；若为双盲，需去掉 `paper/` 中已含作者信息的版本）
- **预印本**: 允许 bioRxiv/medRxiv 同步（待复核）
- **数据/代码**: 必须提供；补充材料允许
- **ORCID**: OUP 要求通讯作者提供（待复核具体范围）
- **AI 使用声明**: OUP 有生成式 AI 使用披露政策，需在投稿时填写（本工作 LLM 用于翻译/相关性判定，属方法组件，需在 Methods 与声明中同时说明）

### 1.3 期刊计量（待复核）

- 影响因子：v1 写"~5–6"；按 2024 JCR 口径约 **4.4**，请在 JCR/官网确认最新值后再引用
- 中科院分区：v1 写"1 区/2 区"——需按最新《中国科学院文献情报中心期刊分区表》确认（本工作不以此作为卖点，避免 Cover Letter 出错）

---

## 二、论文框架设计

### 2.1 标题

**主选**:
```
lit-acquisition: Multi-source Multilingual Biomedical Literature Search
with Citation Network Traversal
```

**备选**:
```
lit-acquisition: An Open Toolkit for Biomedical Literature Acquisition
Across 18+ Providers and 6 Languages
```

### 2.2 作者信息（待填）

- [ ] 确定作者顺序与单位
- [ ] 注册 ORCID（OUP 要求）
- [ ] 确定通讯作者与 email
- [ ] 作者贡献声明（CRediT）

### 2.3 Abstract（≤150 词，数字已核实）

> Comprehensive literature review is essential for clinical variant interpretation,
> but existing tools are limited to single databases and predominantly English content.
> We present **lit-acquisition**, an open-source toolkit that unifies **19 literature
> channels** (15 federated search backends plus PubMed, Semantic Scholar,
> ClinicalTrials.gov and Zenodo services) behind one interface, adding LLM-based
> cross-lingual query translation and citation-graph traversal. On an exploratory
> reference set of **43 genetic variants (822 papers)** — heuristically annotated,
> not expert-verified, with a candidate pool that overlaps the evaluated system —
> the end-to-end pipeline raises PubMed-only set recall from **0.269 to 0.467**
> (+0.198; bootstrap 95% CI [+0.123, +0.269]; Holm-adjusted p=0.0002), and recovers
> **21/24 (88%)** annotated German/French papers that every English-only baseline
> finds at most one of. lit-acquisition ships as a lightweight API-only edition and
> an LLM-enhanced agent edition (v0.5.0, MIT).

**Availability**: `pip install lit-acquisition` ·
<https://github.com/lanshi17/lit-acquisition> · MIT License
**Contact**: [通讯作者 email]

### 2.4 正文大纲

#### 1. Introduction（约 400 词）

- **段 1 问题**：ACMG/AMP 证据收集依赖已发表文献；瓶颈是跨库手工检索、非英语文献易被忽略、引文网络未被系统探索、付费墙阻断全文。
- **段 2 现有工具局限**：PubMed/EuropePMC 单源且以英语为中心；Google Scholar 无结构化 API；商业工具（Mastermind、Covidence）闭源且昂贵；没有开源方案同时组合"多源联邦 + 多语言 + 引文遍历"。
- **段 3 贡献**：
  1. 19 个异构通道的统一接口（15 个纯 Python httpx 后端 + 4 个服务客户端）
  2. LLM 跨语言查询翻译（6 语言：en/zh/ja/de/fr/ru）与语言感知路由
  3. 基于 Semantic Scholar 的引文图 BFS 遍历（深度/扇出可配）
  4. 开放获取全文获取（Unpaywall/EuropePMC/DOI→PDF），含 SSRF 防护与体积/跳转上限
  5. 双 edition 交付：lite（纯 API）与 agent（LLM 增强）

#### 2. Implementation（约 500 词）

- **架构**：三阶段 search → download → relevance gate；模块化为
  `providers/`（后端与服务工作）、`gateway`（调度/超时/错误分类/重试）、
  `orchestration`（并发扇出与工作流）、`llm/`（翻译、相关性门控、神经重排）、
  `net/`（连接池、SSRF/密钥防护、安全下载）、`algorithms/`（无副作用排序/去重/分类）、`health`（健康度与熔断）。
- **多源联邦**（数字已核实）：
  - 15 个搜索后端：`arxiv, base, biorxiv, cinii, core, crossref, doaj, europepmc, jstage, medrxiv, openaire, openalex, pmc, scielo, unpaywall`
  - 4 个服务通道：`pubmed, semantic_scholar, clinical_trials, zenodo`
  - 并发检索 + 健康感知优先级；按 DOI/PMID/归一化标题去重
  - 需配置的通道（BASE/CORE API key、Unpaywall 邮箱）缺失时以 `CONFIG_MISSING` 干净跳过
- **多语言翻译**（agent edition）：目标语言 en/zh/ja/de/fr/ru；领域提示保留基因符号与 HGVS；
  实测配置：`Qwen/Qwen2.5-72B-Instruct`，temperature 0.2，max_tokens 1024，system prompt SHA256 前 16 位 `96fe414ab84606dd`（`run_manifest.json`）
- **引文网络遍历**：以 M3 列表首个 DOI 为种子，depth=1、max_papers=20、direction=both（`run_manifest.json`）
- **神经重排**（agent edition）：`BAAI/bge-reranker-v2-m3` 作为最终排序阶段（判定"门控"规则：保留 rerank 分数 ≥ M3 top-20 最低分的引文条目）
- **实现细节**：Python ≥3.12；核心依赖 `httpx[socks]`、`pydantic`、`loguru`、`pymupdf`；agent edition 追加 `openai`
- **安装**：
  ```bash
  pip install lit-acquisition                    # lite
  pip install "lit-acquisition[agent]"           # agent（LLM）
  pip install "lit-acquisition[web-search]"      # Firecrawl/Tavily/SerpApi
  ```

#### 3. Results（约 400 词，全部数字可溯源）

**段 1 基准设计（须如实描述）**
- 参考集：43 个临床变异（BRCA1、TP53、CFTR、HFE 等），822 篇论文
- **标注方式**：自动化启发式协议，**未经专家复核**；候选池来自 PubMed/EuropePMC/Crossref 等与被评系统重叠的通道 → 存在池化/循环偏差。因此所有指标是**相对覆盖比较**，不是临床检索质量
- 语种：en 493、de 13、fr 11、unknown 305（**仅德法有非英语标注**，其他语种声明不受支持）
- 指标：SetRecall（macro）、Recall@K、Precision@K、nDCG@K（K∈{5,10,15,20,50}）、Precision@Retrieved
- 统计：配对 Wilcoxon 双侧（丢弃零差）、Holm-Bonferroni 校正（预声明 8 检验族）、bootstrap 10,000 次（seed 42）

**段 2 主要结果**

| 方法 | SetRecall | Recall@20 | nDCG@20 | 相关论文数 |
|---|---|---|---|---|
| M1 PubMed-only | 0.269 | 0.171 | 0.205 | 213 |
| M2 Multi-source（无翻译） | 0.472 | 0.153 | 0.164 | 373 |
| M3 + Translation | 0.465 | 0.276 | 0.301 | 392 |
| **M4n + Citation（嵌套）** | **0.467** | **0.276** | **0.301** | **394** |

- 相对 PubMed-only：**+0.198 SetRecall**（95% CI [+0.123, +0.269]；p_Holm=0.0002；rank-biserial r=0.74）
- 增益主要来自联邦步骤本身：M2 vs M1 **+0.203**（p_Holm=0.0001）；翻译与引文在集合层面**不显著**（M3 vs M2 p_Holm=1.0；M4n vs M3 p_Holm=1.0）——**不要**把 M3/M4 说成"再提升召回"
- 浅层排序显著改善：Recall@20 **+0.105**（p_Holm=0.016）；nDCG@50 **+0.162**（p_Holm=0.0004）
- 代价（须主动报告）：引文条目使 Precision@Retrieved 下降 **−0.011**（p_Holm=1.9e-06）

**多语言贡献（精确表述）**
- M3/M4n 找回 21/24 篇德法参考论文（de 11/13、fr 10/11）
- 所有英语单语基线（含多语言 dense 检索 S6）为 **0/24**；最佳 PubMed 池基线 S3 hybrid 为 **1/24**（1 篇德语）
- 表述模板："recovers 21 of 24 annotated German/French papers, versus at most one for every English-only baseline"

**引文遍历（精确表述）**
- 43 个 variant **合计** 2 篇相关引文发现，其中 1 篇未出现在任何关键词列表中：
  - V002 BRCA1 c.5266dupC → *Familial history and prevalence of BRCA1, BRCA2 and TP53 pathogenic variants in HBOC Brazilian patients*（Sci Rep 2022, doi:10.1038/s41598-022-23012-3）
  - V035 HFE p.C282Y → *HFE p.C282Y Polymorphism and Risk of Metabolic Syndrome Components: Systematic Review and Meta-Analysis*（Medicina 2026, doi:10.3390/medicina62030589）
- 结论口径："citation traversal adds a small number of otherwise-unretrieved relevant papers (2/822), at a measurable list-level precision cost"

**SOTA 对比（精确表述）**
- 与 2025–2026 六个单通道方法比较：显著优于 **5/6**；最强的 S3 hybrid BM25+dense+cross-encoder（0.421）低于本流程（0.467）但**差异不显著**（raw p=0.35；Holm 校正后 p=1.00）
- **必须披露**：S1 dense、S2 cross-encoder、S5 agentic、S6 multilingual dense 均只在同一个 PubMed 候选池（平均 80.7 条）内重排，其 SetRecall 与 M1 **恒等**——它们是"池受限"基线，不构成对集合召回的挑战。有意义的对手是 S3
- 离线融合对照：并集 SetRecall 0.646（相关 532 篇）、RRF 0.646——**融合多方法会更高**，说明本工具的价值在"单次可复现流程内的覆盖+多语言+引文"，而非绝对召回上限（论文需如此表述）

**来源贡献（嵌套 M4，相关论文 394 篇）**
- Crossref 222、PMC 90、Semantic Scholar 65、EuropePMC 15、citation_graph 2、ClinicalTrials.gov 0（唯一检索 8 条，无相关命中）
- 该数字用于说明"多源互补"，注意是 first-seen 归因（去重后）

**段 3 案例研究（真实可核实，替代 v1 虚构案例）**
- **V005 TP53 p.R175H（Li-Fraumeni，32 篇参考）**：PubMed-only 3/32 → 全流程 15/32；PubMed-only 与 S3 hybrid 对 3 篇德法参考论文均为 **0**，本流程全部找回
- **V002 BRCA1 c.5266dupC（29 篇）**：7/29 → 17/29，其中 1 篇由引文网络独占找回
- **V035 HFE p.C282Y（28 篇）**：8/28 → 17/28，其中 1 篇由引文网络独占找回

**段 4 性能（实测，非估算）**
- 43-variant / limit=100 采集会话（`run_manifest.json`）：
  - PubMed-only 中位 **3.77 s/variant**（p90 6.03 s）
  - 多源无翻译中位 **22.26 s**（p90 36.63 s）
  - 多源+翻译中位 **25.40 s**（p90 41.47 s）
  - 引文阶段中位 **1.71 s**（合计 84.2 s / 43 variants）
  - → 全流程按"翻译阶段 + 引文阶段"估算：中位约 **27 s/variant**
- 单次浅层检索示例（README，6 通道、24 条）：**2.1 s**；v0.3.2 起 6 语言扇出阶段约 **10.5 s**
- **已补测（2026-10-13，工件见括号）**：下载吞吐与内存
  （`benchmarks/results/download_throughput.json`：4 variants / 187 候选 → 18 篇，**3.99 PDFs/min** 端到端、
  候选→PDF **9.6%**、**15.06 s/篇**、峰值 RSS **85.7 MB**）；43-variant 全流程耗时合成
  （`timing_43variants.json`：中位 **28.65 s/variant**，p90 44.61 s）
- **仍待测**：relevance gate 打开后的假阳性率（需人工判定抽样真值）

#### 4. Discussion（约 200 词）

- **优势**：首个把"多源联邦 + 多语言翻译 + 引文遍历 + 开放获取下载"组合进单一开源工具包的工作；双 edition 适配不同部署条件；相对 PubMed 的覆盖提升在 43 个变异上显著。
- **局限（主动写入，避免审稿人先说）**：
  1. 参考集为启发式、非专家金标准，且候选池与被评系统重叠（池化偏差）→ 仅作相对比较
  2. 引文遍历增益很小且带来精度代价；翻译在集合层面中性（价值体现在非英语覆盖）
  3. 4/6 SOTA 基线池受限，SOTA 对比的强度有限
  4. LLM 依赖（翻译/门控/重排）——由 lite edition 与显式降级警告缓解；LLM 版本与参数已记录
  5. API 速率限制影响吞吐（Semantic Scholar 匿名访问在大规模运行中出现 78 次 HTTP 429）
- **未来工作**：嵌入语义检索、自适应遍历深度、专家标注验证、与 ClinVar/LOVD 等变异库集成

#### 5. Availability

- 源码：<https://github.com/lanshi17/lit-acquisition>（MIT）
- PyPI：`lit-acquisition` **0.5.0**（2026-10-13 发布；lite/agent 双 edition 已上线）
- 基准数据：`benchmarks/gold_standard/`（43 variants / 822 papers）+ `benchmarks/results/`（全部指标与清单）
- 复现命令：
  ```bash
  python benchmarks/run_benchmark.py --collect --limit 100 --cg-budget 20
  python benchmarks/make_paper_data.py     # 确定性再生论文全部数字
  ```
- Python ≥3.12；核心依赖 httpx / pydantic / loguru / pymupdf；agent edition 追加 openai
- ⚠️ 注意：`benchmarks/` 与 `docs/` **不在 sdist/wheel 内**（`pyproject.toml` 显式排除），因此数据可用性必须指向 GitHub 仓库，并建议为基准快照发布 Zenodo DOI（见 §7）

---

## 三、图表设计（1 页）

**Figure 1（三面板，紧凑单栏）**
- Panel A：三阶段流程图（Search → Download → Relevance gate），标注 19 通道与两种 edition
- Panel B：通道 × 语种覆盖矩阵（据 `source_coverage.json` 与路由表）
- Panel C：M1/M2/M3/M4n 的 SetRecall 与 Recall@20 对比柱状图（含 95% CI 误差条）

**备选**：Figure 1 仅画架构；性能数字放入 Table 1。

**Table 1：性能对比（数字同 §2.4 段 2，勿改动）**

| Method | SetRecall | Recall@20 | nDCG@20 | Relevant found |
|---|---|---|---|---|
| M1: PubMed-only | 0.269 | 0.171 | 0.205 | 213 |
| M2: Multi-source | 0.472 | 0.153 | 0.164 | 373 |
| M3: +Translation | 0.465 | 0.276 | 0.301 | 392 |
| M4n: +Citation (nested) | **0.467** | **0.276** | **0.301** | **394** |

脚注（必须准确）：*M2 vs M1 与 M4n vs M1 的 SetRecall 差异经 Holm 校正后 p<0.001；
Recall@20 差异 p=0.016；M3/M4n 相对 M2/M3 的集合层面差异不显著；n=43 配对 Wilcoxon。*

**补充材料图表**：SOTA 对比（含池受限说明）、逐 variant 散点、语言恢复明细、
引文独占性两案例、运行方差（Jaccard）、provider 失败统计。

---

## 四、数字溯源表（写作时逐条对照，勿凭记忆填数）

| 论文中的数字 | 工件路径 | 键 |
|---|---|---|
| 43 variants / 822 papers | `benchmarks/gold_standard/variants.jsonl` | 行数 / `total_relevant` 求和 |
| 0.269 / 0.472 / 0.465 / 0.467 SetRecall | `benchmarks/results/paper_data.json` | `aggregates.*.set_recall.mean` |
| Recall@20 / nDCG@20 | 同上 | `aggregates.*.recall_at.20.mean` / `ndcg_at.20.mean` |
| 213/373/392/394 | 同上 | `aggregates.*.micro.relevant_found` |
| +0.198、CI、r、p_Holm | 同上 | `tests_holm.M4n_vs_M1_set_recall.*` |
| Recall@20 +0.105、nDCG@50 +0.162 | 同上 | `tests_holm.M4n_vs_M1_recall20` / `_ndcg50` |
| 精度代价 −0.011 | 同上 | `tests_holm.M4n_vs_M3_prec_retr.delta` |
| 21/24 德法恢复 | 同上 | `language_analysis.found_by_method.full_pipeline_nested` vs `gold_totals.de/fr` |
| 引文 2 篇 / 1 篇独占 | 同上 | `citation_exclusivity.*` |
| 来源贡献 222/90/65/15/2/0 | 同上 | `source_coverage_nested.*.relevant` |
| SOTA 5/6 显著、S3=0.421 不显著 | 同上 | `sota_baselines.aggregates.sota_hybrid.set_recall.mean`、`tests_holm.sota_hybrid_vs_full_pipeline_nested_set_recall` |
| 池受限基线均值 80.7 | 同上 | `sota_baselines.realized_mean_candidates.*` |
| 融合 0.646 | 同上 | `fusion.union.set_recall_macro` / `fusion.rrf.set_recall.mean` |
| 耗时中位数 | `benchmarks/results/run_manifest.json` | `per_variant_wall_s.*` |
| LLM/reranker 版本与参数 | 同上 | `llm.*`、`protocol.rerank.model` |
| 429 次数 78 | `benchmarks/results/paper_data.json` | `provider_failures.http_429_by_provider.semantic_scholar` |
| gold 语种分布 | 同上 | `gold_standard.languages` |

---

## 五、与会议路线的关系（必须处理的冲突）

`docs/submission-plan.md` 走的是**会议优先链**：ECIR 2027（已因格式现实放弃）→ SIGIR 2027 → CIKM/JCDL 2027；
`paper/main.tex` 是 9.92 页 JCDL/CIKM 版（双盲、ACM 格式），JCDL 2027 截稿 **2027-07-15**。
本计划是**期刊路线**。两者使用**同一份基准与同一套主结果**，因此：

- **不可同时进行**：同一工作不得同时投会议与期刊；会议论文一旦正式发表（ACM 论文集），
  多数期刊会视为 prior publication。若先发会议，期刊版必须显著扩展并披露（新实验/新分析）。
- **建议决策点（2026-11 前定）**：
  - **方案 A（期刊优先）**：直接投 Bioinformatics Applications Note，按本计划 §6 执行，
    会议版作为"被拒后回退"（此时需重新匿名化并压缩为 10 页）。适合希望尽快拿到期刊记录。
  - **方案 B（会议优先）**：按 `docs/submission-plan.md` 投 JCDL/SIGIR，2027-08 后再以
    "扩展版 + 专家标注"投 Bioinformatics（期刊版必须新增内容：专家标注 κ、下载吞吐/精度实测、
    更多语种标注）。
- 无论哪条路，**专家标注参考集都是共同瓶颈**，应立刻启动（§7 第 2 项）。
- ⚠️ **匿名性取舍（2026-10-13 新事实）**：为满足期刊可用性要求，仓库已改为 **public**，
  且 `preprint/` 中已有真实姓名、单位与邮箱（git 历史不可撤回）。因此：
  - 走**方案 A（期刊优先）**：无冲突，Bioinformatics 为单盲（待复核），作者信息本就需要公开。
  - 走**方案 B（会议优先）**：仓库名与工具名可直接关联到作者，双盲要求下存在**去匿名化风险**。
    届时的处置：会议投稿期间把仓库临时改回 private，或改用匿名工件镜像
    （anonymous.4open.science），并确认预印本策略。

---

## 六、写作时间表（以 2026-10-13 为起点）

### Phase 1：内容压缩与重构（4 周，10-13 → 11-09）

**Week 1–2：主文压缩**
- [x] 从 9.92 页会议版提取核心内容，压到 2 页正文 → `paper/appnote/main.tex`（正文 2 页、1232 词）
- [x] 按 §2.4 的"精确表述"重写 Results（引文/SOTA/多语言三处口径已按工件改写）
- [x] 详细方法学与消融细节移入补充材料 → `paper/appnote/supplementary.tex`

**Week 3：图表**
- [x] Figure 1 三面板（`paper/appnote/make_figure1.py`，数据取自 `paper_data.json`）
- [x] Table 1 只留 M1–M4n 核心数字（并由 `check_numbers.py` 机器核对）
- [ ] 补充图表：SOTA（含池受限说明）、案例、语言恢复、运行方差

**Week 4：补充材料**
- [x] 详细方法学、完整指标表、SOTA 明细、复现命令与使用示例 → 补充材料 5 页

### Phase 2：优化与验证（3 周，11-10 → 11-30）

**Week 5：补测性能与精度**
- [x] 下载吞吐（PDFs/min）与失败率实测并留工件 → 3.99 PDFs/min、9.6%、85.7 MB
- [ ] relevance gate 开启后的假阳性率实测（人工抽检 + 记录抽样协议）
- [x] 43-variant 全流程墙钟（由同会话分阶段数合成，中位 28.65 s）
- [x] 内存峰值（85.7 MB，与下载测量同一工件）

**Week 6：内部与专家预审**
- [ ] 合作者/导师审阅
- [ ] 生物信息学检索专家反馈（benchmark 口径、SOTA 对比是否公允）
- [ ] 临床遗传学家试用（至少 3 位，记录场景与反馈）

**Week 7：语言与格式**
- [ ] 英文母语校对；术语统一（provider/channel/edition 等）
- [ ] 按 OUP 模板排版（官网复核后进行）
- [ ] 检查所有数字与 §4 溯源表一致

### Phase 3：投稿准备（2 周，12-01 → 12-14）

- [ ] OUP 官网核对 Applications Note 最新要求（页数/图数/声明/APC）
- [ ] 引用完整性（10–15 条）；LLM 与数据源均被引用
- [ ] 利益冲突、作者贡献、数据可用性、AI 使用声明
- [ ] 建议审稿人 3–5 位；Cover Letter（按 §10）
- [ ] 注册 OUP 投稿系统并上传 manuscript + 补充材料 + 基准快照

### Phase 4：审稿响应

- 预期 4–8 周；Prepare minor/major revision 的预案见 §11

---

## 七、提升成功率的关键行动

### 7.1 必须完成（高优先级）

1. **专家验证参考集（最高优先级，唯一长工期）**
   - [ ] 分层抽样 ≥30 variants × 2 位临床遗传学背景标注者
   - [ ] 区分"与基因/疾病相关"与"可作为 variant 级 ACMG 证据"
   - [ ] 计算 Cohen's κ；κ>0.6 才可写 "expert-validated"，否则保持 "heuristic reference set" 并强调相对比较
2. **用户证据**
   - [ ] 邀请 3–5 位临床遗传学家/生物信息学家试用，记录场景与反馈
   - [ ] 如实记录 GitHub star、PyPI 下载量（PyPI JSON API 可提供 releases 时间线）
3. **性能与精度补充数据**（当前**无**工件）
   - [ ] 下载吞吐与失败率；relevance gate 假阳性率；43-variant 全流程墙钟；内存
   - [ ] 通道贡献度与失败案例（Semantic Scholar 429 已有记录，可分析）
4. **仓库与文档一致性修复**
   - [ ] `benchmarks/gold_standard/README.md` 仍写 "30 variants / 540 papers"，而 `variants.jsonl` 已是 43/822 → 更新；或明确 `variants_annotated.jsonl`（30 个）为历史文件
   - [ ] PyPI 缺 `project_urls`（homepage/repository）→ 在 `pyproject.toml` 补 `[project.urls]` 后发新版本，便于审稿人找到仓库
   - [ ] README Quick Start 5 分钟上手 + Jupyter notebook 示例；API 文档站（MkDocs/Sphinx）
   - [ ] 故障排查指南（key 缺失、429、Unpaywall 邮箱）
   - [ ] 为基准快照发布 Zenodo DOI（`benchmarks/` 不在 PyPI 包内）
   - [ ] **本文档本身未纳入版本管理**：`paper/` 被 `.gitignore` 整体忽略（`git check-ignore` 已确认）。
     若要跟踪，需把 `.gitignore` 的 `paper/` 改为 `paper/*` 再加 `!paper/BIOINFORMATICS_PLAN.md`
     （父目录被整体忽略时 `!` 反包含单文件无效），或把计划复制到 `docs/`。
5. **ORCID 与作者信息**（OUP 要求）

### 7.2 建议完成（加分项）

- [ ] bioRxiv 预印本（投稿前；注意与会议路线的匿名要求协调）
- [ ] 社区推广（BOSC、r/bioinformatics、相关邮件列表）
- [ ] Galaxy wrapper / Nextflow / Snakemake 模块
- [ ] 与临床实验室合作试用并记录真实案例

---

## 八、备选方案（若 Bioinformatics 拒稿）

- **B1: Journal of Biomedical Informatics** — 用 10 页完整版，强调方法学
- **B2: BMC Bioinformatics** — OA（需 APC），范围更宽，审稿较快
- **B3: Database (OUP)** — 工具/数据库导向，OA，同出版社
- **B4: 会议回退** — 按 `docs/submission-plan.md`：JCDL 2027（2027-07-15 截稿）或 SIGIR/CIKM（截稿以最新 CFP 为准；SIGIR ~2027-01、CIKM ~2027-05 需复核）

---

## 九、投稿清单

**内容完整性**
- [ ] Abstract ≤150 词（数字与 §2.3 一致）
- [ ] 正文 ≤2 页；图表 ≤1 页；引用 10–15 条
- [ ] 补充材料齐备（方法学、完整指标、SOTA 明细、复现说明）

**数字与技术**
- [ ] 全部数字经 §4 溯源表逐条核对
- [ ] 图 ≥300 DPI；OUP LaTeX 模板
- [ ] 所有作者 ORCID；资金声明；利益冲突；作者贡献（CRediT）
- [ ] LLM 使用声明 + 模型/参数/提示哈希记录（`run_manifest.json`）

**可用性**
- [ ] GitHub 仓库公开、README 与文档完整
- [ ] PyPI `lit-acquisition` 0.5.0 可安装（lite 与 agent 两条命令均验证）
- [ ] 基准数据可下载（GitHub / Zenodo DOI）
- [ ] 示例代码可运行

**伦理与数据**
- [ ] 数据可用性声明（含"启发式、非专家金标准、池化偏差"表述）
- [ ] MIT 许可证；所有使用的 API/数据源被引用
- [ ] AI 使用声明

---

## 十、Cover Letter 与审稿人

### 10.1 Cover Letter 要点

1. **价值**：临床变异解读的文献检索瓶颈；首个把多源联邦、多语言翻译、引文遍历与
   开放获取下载组合进单一开源工具包的工作。
2. **技术亮点（只用核实数字）**：相对 PubMed-only，43 variants 上 SetRecall 0.269→0.467
   （+0.198，p_Holm=0.0002）；找回 21/24 篇德法参考论文（英语单语基线至多 1 篇）；
   已开源并发布 0.5.0（lite/agent 双 edition）。
3. **适配 Bioinformatics**：Applications Note 格式匹配；服务生物信息学与临床基因组学社区；
   有可复现的基准与统计验证，且主动披露参考集为启发式、非临床质量结论。
4. **建议审稿人**：3–5 位（见 10.2），说明研究方向与理由。

### 10.2 建议审稿人画像（待补具体姓名，不得为合作者）

1. 生物医学信息检索专家（biomedical IR / literature mining；TREC Genomics、BioASQ 方向）
2. 临床生物信息学专家（variant interpretation / ACMG-AMP 实践）
3. 多语言/跨语言 IR 专家（cross-lingual retrieval，有生物医学背景更佳）
4. 开源生物信息学工具作者（理解工具类论文评审标准）

---

## 十一、风险与应对

| # | 可能的审稿意见 | 应对 |
|---|---|---|
| 1 | "参考集是启发式标注，非金标准" | 已在 Methods/数据声明中主动披露；强调仅作相对比较；若专家标注完成（κ>0.6）则升级表述 |
| 2 | "候选池与被评系统重叠（池化/循环偏差）" | 同 §11.1；补充外部单源基线（EuropePMC/OpenAlex/Crossref 直连，已采集）作旁证 |
| 3 | "缺乏真实用户证据" | 收集 ≥3 个试用案例；强调刚发布（v0.5.0, 2026-10-13）并正在推广 |
| 4 | "新颖性不足，只是 API 整合" | 强调组合创新与显著覆盖提升；指出 4/6 SOTA 基线池受限、唯一有意义的对手 S3 也被超越（虽不显著）——**不要**声称"全面碾压" |
| 5 | "引文遍历增益太小（2/822）且降低精度" | 如实报告；定位为"补充性"通道，价值在于可复现的自动化，而非数量 |
| 6 | "翻译在集合层面无增益" | 承认集合层面中性；价值在非英语覆盖（21/24 vs ≤1/24）与语言路由 |
| 7 | "与商业工具（Mastermind）比较不足" | 商业工具闭源不可复现；强调开源、可扩展、可审计 |
| 8 | "LLM 依赖影响可复现性" | lite edition 无 LLM 依赖；LLM 版本/温度/提示哈希已记录；门控/重排可关闭 |
| 9 | "下载吞吐/假阳性率缺失" | 投稿前补测并写入补充材料（§7.1-3） |

---

## 十二、成功指标与资源

### 12.1 成功指标

**短期（3 个月）**
- [ ] 论文 draft 完成并通过内部审阅
- [ ] ≥3 个外部用户试用反馈
- [ ] 专家标注启动或完成（κ 可报）
- [ ] 补充数据（吞吐/精度/全流程墙钟）补齐

**中期（6 个月）**
- [ ] 正式投稿 Bioinformatics
- [ ] bioRxiv 预印本（若与会议路线不冲突）
- [ ] 社区出现使用/讨论

**长期（12 个月）**
- [ ] 论文被接收（Bioinformatics 或备选）
- [ ] ≥1 个真实临床/生物信息学用例发表
- [ ] 被其他研究引用

### 12.2 资源需求

- **人力**：第一作者（12 周全职 / 24 周兼职）；合作者（专家验证与审阅，每人 5–10 h）；临床专家（试用与案例，每人 3–5 h）
- **计算**：基准重跑约 2–4 h × 3 次；LLM API（翻译/门控/重排）约 $50–100
- **其他**：英文润色 $200–500（可选）；bioRxiv 免费；Bioinformatics 非强制 OA（传统订阅模式无 APC，具体以官网为准）

---

## 联系方式

**项目负责人**: [姓名]
**Email**: [email]
**GitHub**: <https://github.com/lanshi17/lit-acquisition>
**PyPI**: <https://pypi.org/project/lit-acquisition/>（0.5.0）
**更新日期**: 2026-10-13（v2，数据核查版）
