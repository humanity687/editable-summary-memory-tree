# Paper 1 相关工作：搜索提示词与差分核查清单

> 用途：交给检索/写作 agent 做定向查新，产出「相关工作」节素材与差分表。
> 本工作一句话：**可编辑的文本摘要记忆树（区间精确失效+懒重建+账本回滚+恒定预算）**；
> 主张：**结构买正确性（编辑后零残留），召回对结构不敏感**。

---

## 0. 给检索 Agent 的系统提示词（可整段粘贴）

```
你是文献检索助手。围绕「LLM 长上下文 / Agent 记忆 / 可编辑记忆 / 记忆基准」检索相关工作。
规则：
1) 只报告你**实际检索到**的论文/系统，附题名、作者或机构、年份、venue、可访问链接；
   检索不到就写「未找到」，禁止编造引用或把猜测写成事实。
2) 每条输出固定格式：
   [题名] | 作者/机构 | 年 | venue | 链接
   - 他们做了什么（1–2 句，读摘要，必要时读正文）
   - 记忆载体（摘要树/向量库/KG/滚动窗口/…）
   - 更新方式（append-only / 事实级 LLM 判定更新 / bi-temporal 失效 / 区间失效 / 无更新）
   - 与本工作的**差分**（一句话）
3) 重点核查下面「待验证 claim」。对每个 claim 给出：支持/反驳/无定论的证据 + 最接近的邻居工作。
4) 标注该工作属于 Paper1（编辑正确性/召回/基准）还是 Paper2（人格保持/自我模仿）还是两者。
5) 优先一手来源：arXiv、ACL Anthology、OpenReview、PMLR、NeurIPS/ICLR/ICML、Semantic Scholar、
   Google Scholar、官方 GitHub/技术报告。
```

---

## 1. 待验证 claim（决定差分表述）

- **C1（召回不敏感）**：在**同引擎、同语料、同预算**下，层次结构与平面检索对召回无显著差异。
  找邻居/反例（已知：Mem0 报告图结构对 single/multi-hop 无增益甚至略降）。
- **C2（可编辑记忆差分）**：现有系统是**事实/关系级**失效（Mem0 的 LLM ADD/UPDATE/DELETE；Zep/Graphiti 的
  bi-temporal `invalid_at`），**没有**「文本摘要树上的**区间精确失效 + 零 LLM 零阻塞传播 + 脏卡懒重建 + 账本回滚 + 恒定预算」。
  逐项核查是否真的没人做全。
- **C3（编辑正确性基准）**：**没有**专门测「编辑后旧值残留 / 不变项 / 远端项 / 复述零残留」的记忆编辑正确性基准。
- **C4（使用驱动重组，M1）**：A-MEM（新记忆触发 LLM 链接）、Letta/MemGPT sleep-time、Generative Agents
  reflection、HippoRAG —— 差分钉在「**内生使用信号（组装/输出/编辑）** + **非冗余判据** + **非 LLM 维护**」。
- **C5（恒定预算/成本）**：是否有工作把「恒定额定预算」作为记忆系统的一等属性并测 token 成本。
- **C6（代码/结构化场景）**：结构化记忆 + 失效传播在 coding agent 记忆（SWE-agent、Cursor/Claude Code、
  CLAUDE.md/AGENTS.md 工作流）中是否已有。

---

## 2. 主题簇与查询串（英文为主）

### A. 层次化 / 递归摘要记忆
搜索串：
- `RAPTOR recursive abstractive processing tree retrieval`
- `hierarchical summarization long document memory LLM`
- `summary tree memory agent long context`
- `recursive summarization drift long context`
- `hierarchical memory consolidation LLM agent`
要提取：记忆载体是否是「连续区间自底向上合并的摘要树」；更新/失效机制。

### B. Agent 记忆系统
搜索串：
- `LLM agent long-term memory survey`
- `Mem0 memory ADD UPDATE DELETE LLM`
- `Letta MemGPT memory hierarchy`
- `Zep Graphiti temporal knowledge graph agent memory bi-temporal`
- `A-MEM agentic memory Zettelkasten link evolution`
- `Generative Agents memory stream reflection retrieval`
- `MemoRAG HippoRAG long-term memory retrieval`
- `Voyager ExpeL experience memory agent`
要提取：更新语义（append-only vs 事实级 vs 边级失效）、是否有结构（树/KG）、是否有失效传播。

### C. 可编辑 / 可更新记忆 & 知识编辑
搜索串：
- `memory editing agent knowledge update benchmark`
- `knowledge editing large language models survey`
- `append-only vs editable memory LLM`
- `fact invalidation temporal reasoning memory`
- `document editing retrieval consistency`
- `versioning / rollback memory system`
要提取：是否有「区间级失效 + 懒重建 + 回滚账本」；是否有「编辑后一致性」度量。

### D. 长上下文 / 记忆基准与评测
搜索串（基准名逐一核）：
- `LongBench`、`RULER`、`InfiniteBench / ∞Bench`、`NarrativeQA`、`Qasper`
- `LoCoMo long-term conversational memory`
- `LongMemEval`、`MemoryAgentBench`、`STALE memory agent benchmark`
- `temporal reasoning under knowledge updates benchmark`
- `multi-hop QA long context memory`
要提取：这些基准测的是**召回/理解**还是**编辑后正确性**；有无「旧值残留/过时」指标。

### E. 检索方法（作为背景）
- `hybrid dense sparse retrieval rerank long context`
- `bigram keyword + embedding hybrid retrieval Chinese`
- `vector cache invalidation document update`

### F. 上下文压缩 / 恒定预算 / 成本
- `context compression LLM gist tokens LLMLingua`
- `memorizing transformers / recurrent memory`
- `token budget fixed context agent`
- `KV cache prefix reuse long context cost`

### G. 一致性与时间推理
- `consistency long-horizon LLM memory`
- `knowledge conflict temporal invalid LLM`
- `stale knowledge retrieval failure`

### H.（归属 Paper 2，标注即可）
- `persona drift long conversation LLM`
- `self-conditioning / self-imitation style persistence LLM`
- `summarization bias persona compression`
- `memory backend social simulation agents`
- `LLM multi-agent convergence lexical convergence`

---

## 3. 必须填的「差分表」（产出目标）

| 邻居系统 | 记忆载体 | 更新方式 | 失效粒度 | 懒重建 | 回滚 | 恒定预算 | 编辑后一致性评测 |
|---|---|---|---|---|---|---|---|
| Mem0 | | 事实级 LLM ADD/UPDATE/DELETE | | | | | |
| Zep/Graphiti | KG + bi-temporal | 边级 `invalid_at` | | | | | |
| A-MEM | note + 链接 | 新记忆触发 LLM 演化 | | | | | |
| MemGPT/Letta | 分层 + 换页 | | | | | | |
| RAPTOR | 递归摘要树 | 无（静态构建） | | | | | |
| 本工作 | 连续区间摘要树 | 编辑触发区间失效（零 LLM） | **区间** | ✅ | ✅ 账本 | ✅ 字符预算 | **编辑正确性基准** |

---

## 4. 检索执行建议

- 每个主题簇先跑 3–5 个搜索串，按 **年份（近 3 年优先）+ venue** 排序，取前 10–20 条做标题+摘要筛选。
- 对每个候选，追一张引用表（forward citations）找最接近的邻居。
- 对最接近的 5–8 篇读正文的「method/update/related」小节，确认 C1–C6 的差分。
- 产出一节 `RELATED_WORK_DRAFT.md`：按 A–G 主题成段，每段结尾一句「与本工作的区别」。
- 所有引用附链接；无法核实的一律标「未找到」。

---

## 5. 输出模板（供 agent 直接填）

```
### 主题：<A 层次化摘要记忆>
1. [题名] | 作者 | 年 | venue | 链接
   - 做了什么：
   - 记忆载体 / 更新方式 / 失效粒度：
   - 与本工作差分：
2. ...
小节结论（≤2 句）：<本簇共性 + 我们的差分>

差分表：见 §3（逐行填）
待验证 claim 状态：C1=支持/反驳/无定论；C2=... 
```
