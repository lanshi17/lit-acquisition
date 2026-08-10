# Lesson Log

## 2026-08-10 — PubMed 限流与超时处理

**问题**: 批量调用 NCBI E-utilities (esearch + esummary) 拉取 20 个变异的候选论文时,单次脚本在 2 分钟内超时。

**排查**:
1. 第一次尝试一次性跑 20 个变异,每次 400ms sleep,总耗时 >2 分钟
2. 发现 15 个已完成 (V011-V025),5 个未完成 (V026-V030)
3. 第二次只跑剩余 5 个,顺利完成

**根因**: 默认 2 分钟超时太紧;PubMed API 限流策略要求请求间隔 ≥300ms,加上 esummary 本身耗时较长,批量任务容易超时。

**解决**:
1. 分批次运行脚本,每批处理一部分变异
2. 使用文件缓存 (`candidates/V{nnn}_candidates.jsonl`) 避免重复拉取
3. 脚本先检查缓存文件,已存在的直接 skip

**预防措施**:
- 任何涉及外部 API 批量调用的脚本,都应设计为 **可断点续传**(基于文件存在性 skip)
- 对 NCBI/PubMed 等公开 API,遵守 rate limit (无 API key 时 3 req/s,有 key 时 10 req/s)
- 大任务拆分为多次小运行,或后台异步执行

---

## 2026-08-10 — Gold Standard 注释启发式评分

**问题**: 需要为 20 个新变异自动标注 relevant papers,无法手动逐个审核。

**方案**: 基于标题/pubtype 的加权评分
- 基因名出现在标题: +3
- 变异标识符 (p.G12D, c.35G>A 等) 出现在标题: +5
- 疾病词出现在标题: +1/词(上限 +3)
- Preferred pub_types (case reports, guideline 等): +2
- Review 类: -2
- Editorial/letter/comment/news: 直接排除

每个变异取 top-8 候选,保持与原 10 个变异的规模一致 (每变异 ~8 篇)。

**效果**: 20 个变异共标注 160 篇,加上原有 79 篇,总计 239 篇。

**局限**: 启发式无法替代领域专家审核,论文需标注为 "pending domain expert verification"。
