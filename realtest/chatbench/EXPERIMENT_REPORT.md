# 水循环记忆树 · 共现中介层（概括节点）实验报告

> 版本：2026-09-24 · 模型：作答 deepseek-v4-flash（no-think）；judge deepseek-v4-flash（thinking 2048）；
> 向量 bge-small-zh-v1.5（本地全局单例）。语料：ahi-multi 平台真实多智能体实验记录。

---

## 0. TL;DR

1. 把共现中介层（概括节点）接入了 v2 记忆树，并建成一套**可复现的多会话/单文档分析 bench**（引用强制、双通道+claim 分解评分、与已有分析对照、树持久化、≤10 次自助 recall）。
2. 共现层能按预期工作：从使用事件里长出主题正确的概括节点（如"沙箱边界与记忆对账"）。
3. 但在三个独立回放轮次中，**"有共现节点 vs 无共现节点"在 T2/T3 上没有可测的优化**：T3 均分差在 ±0.014~0.14 之间且方向不稳定；T2 与已有分析的一致率两臂相同。
4. **根因（诊断确证）**：带动成员几乎不进入答案证据链（T3 六题"引用∩hub"全为 0）。瓶颈是**带动选择**（按 conn_weight 取 top-k，与"问题需要哪条证据"不匹配），而非概括节点概念本身。

---

## 1. 背景与假设

- v2 记忆树：位置有序的区间覆盖树；分辨率随热度变化；编辑失效 + 懒重建；不可遗忘事实层。
- 新增共现中介层：异构共现边（β 召回 / γ 编辑，ε 慢衰减）→ 簇达阈值时蒸馏出**冻结的概括节点**；检索时概括只做**匹配中介**、按**连接权（conn_weight）**带动成员补进召回影子，**概括本身永不进组装流**（DEMO v1.0 设计）。
- 待验证假设：**使用驱动的共现中介能在向量检索之外产生可测量的召回增量。**

---

## 2. 语料与数据工程

`extract_corpus.py` 从平台 `system.db` 提取，稳定 `msg_id` 作 gold 引用锚；原始措辞零清洗。

| 语料 | 来源 | 规模 | 会话 | 用途 |
|---|---|---|---|---|
| ctx_run | 09-23 context_compare（青/蓝/墨/白 + 实验员） | 716 条 / 289,666 字 | 1（伪会话） | **T2/T3 主实验**（有 6 份现成分析报告） |
| absence day | 08-29 24h 实验（澜/林深/中性 + probe/管理员） | 3272 条 / 871,334 字 | 15（30min 窗） | 多会话 QA 层验证 |
| ctx 单文档 | 同上 ctx_run | 前 ~40k 字 | — | 单文档分析冒烟 |

已有分析参照：`corpus/reports/` 6 份（ANALYSIS.md + analysis_consistency/convergence/problems/recall/tree.md）。
`make_reference.py` 从中抽取 **36 条纯定性结论**（自动剔除含数字/统计词的定量 claim）。

---

## 3. Bench 架构

### 3.1 回放协议（`replay.py`）
```
Phase 0  按时间序逐句灌入（source=发言人）；周期 tick + 按压力 merge；可缓存树
Stage W  usage 查询（分析式，与测试题解耦）重复 P 轮 → 累积 β 边；on 侧长概括节点
Stage T  测试题：检索 → 组装 → 带引用作答；支持 [recall] 自助检索（≤10 次，可一次多个）
```
### 3.2 题型
- **T1 细节问答**：a 直接 / b 转述 / KU / KU-hist / AB 拒答。
- **T2 生成分析报告**：参与度、人格/存在表达变化、趋同、问题异常（≥3~4 条，逐条引用）。
- **T3 报告验证**：给一批陈述逐条判 **支持/反驳/无据** + 引用。含机器生成反例（实体互换=反驳、编造=无据）。

### 3.3 评分（`score.py`）
- T1：精确通道（gold 片段）+ 语义通道（LLM judge 等价）+ 引用真实性；AB 按正确拒答。
- T2：报告内部指标（claim 分解 → 引用覆盖/支持/硬事实）+ **对照已有分析**（12 条参照的覆盖/矛盾率）。
- T3：逐条判定与金标一致率 + 引用真实性。
- 引用真实性为**确定性检查**（引用 id 必须存活）。

### 3.4 公正性控制（硬性）
1. usage 与测试题分离，usage 为分析式全域问题，不含测试答案照抄。
2. 两臂温度 0、同题、同协议；**off = RealEngine（v2 等价），on = ConceptEngine（仅多共现层）**。
3. **同树隔离**（强化实验）：两臂加载同一缓存树、跑完全相同的 usage 检索（树演化一致），唯一变量 = 有无概括节点。
4. 定性参照单独分类，定量不计分。

---

## 4. 工程实现

- `tree_io.py`：树持久化（gzip JSON；只存 v2 树，不存 cooc，保证两臂同树）。**实测 on 从 499s→140s**（省去 907 次蒸馏）。
- `cooc.py`：`CoocLayer` + `ConceptEngine`（tick/merge_pass/retrieve/delete/insert 钩子）。
- 自助检索修复：一次响应多个 `[recall]` 全部执行；按查询总次数计上限 10。
- T 阶段 `_tap_concepts`：每次检索后触发概括创建（"检索对检索"）。
- 蒸馏单次 API 超时不再击穿 merge_pass（长跑健壮性）。
- 向量加载：离线环境变量前置 + **全局单例**。

---

## 5. 实验结果

### 5.1 主实验：ctx_run，T3 报告验证一致率（均分）
| 轮次 | 说明 | off（无共现） | on（有共现） | Δ |
|---|---|---|---|---|
| q | 单查询旧版 | 0.319 | 0.417 | +0.098 |
| q2 | 多查询 + T 自建节点 | 0.437 | 0.299 | −0.138 |
| u | **定向 usage（112 次检索）** | **0.389** | **0.403** | **+0.014** |
| 合计 | | 0.382 | 0.373 | −0.009 |

→ 方向随轮次翻转，量级为噪声；**无稳定增量**。

### 5.2 T2 生成报告 vs 已有分析（定性参照覆盖）
q/q2/u 三轮回放中，两臂覆盖均 ≈0.00–0.08，**完全一致**（人工抽检确认"未提"属实）。

### 5.3 共现层确实在工作（强化轮 u）
- on：**5 个概括节点**（成员 56/55/38/11/8）、边 2082、hub 带动 300 次、T 阶段自建 2 个。
- 节点标题主题正确：沙箱边界与记忆对账 / 四生命存在验证 / 协作规矩与上线分工 / 笔记栏与存在边界 / 前后一致性与改口。

### 5.4 多会话 QA 层（absence，5 会话）
- off 14/17、on 16/17（当轮 on 无共现节点，差异为噪声）。

### 5.5 附：小说 QA 层（seed2，早前）
- 直接细节问答两臂无差异（(b) 层 off/on 均 0.083）；off/on 树同构，共现层零侵入。

---

## 6. 诊断（决定性的负面证据）

强化轮 u 的 on 答案中，"引用 id ∩ hub"：

| 题 | 引用数 | hub 数 | 交集 | hub 占引用 |
|---|---|---|---|---|
| t2_part | 23 | 10 | 3 | 0.13 |
| t2_persona | 20 | 5 | 3 | 0.15 |
| t2_converge | 12 | 15 | 0 | 0.00 |
| t2_problems | 22 | 5 | 0 | 0.00 |
| **T3 六题** | 1–8 | 5–24 | **0** | **0.00** |

→ **共现层带出的卡片几乎不进入最终证据链**。概括节点聚类识别正确，但**带动选择**（conn_weight top-k）带出的是泛化卡片，不是问题所需的具体证据；同时 direct 检索对多数题已足够，共现层呈冗余。

---

## 7. 结论

1. **假设未被证实**：在本 bench 上，"有共现节点 vs 无共现节点"没有可测量的召回增量。
2. 这**不是对共现机制的证伪**，而是明确了当前实现的瓶颈在**带动成员选择策略**（conn_weight 与问题需求的错配），以及部分任务与 direct 检索冗余。
3. 工程的共现层本身稳定、零侵入（两臂树同构）、可解释（节点标题正确、日志完整）。

---

## 8. 局限与下一步

**局限**
- 样本小（T3 6 批、每轮单次），LLM 方差主导。
- T2/T3 参照为项目侧量化分析，已筛纯定性，但"覆盖"判定依赖 LLM judge（含少量误报"矛盾"）。
- 任务多为 direct 检索可解，未触及共现的用武之地。

**下一步（按性价比）**
1. **带动选择改进**：concept 匹配后，只带"与直接命中卡有共现边"的交集成员（仍用共现信号，不退回相似度）——针对诊断出的根因。
2. **提高概括精度**：抬高 θ/N，拒绝 56 成员的大杂烩簇。
3. **配对扩样本**：同配置重复 3~5 次（树已缓存，每轮 on/off 各 ~3 min），报告配对差值与方差。
4. **换任务**：构造 direct 结构性查不到、只能靠跨区共现的场景（"真实场景如何逼出共现"）。

---

## 附录：关键文件

```
realtest/chatbench/
  extract_corpus.py        语料提取（多会话/用户消息/gap 与时间窗切分）
  replay.py                回放器（Phase 0/W/T，--save-tree/--load-tree，≤10 recall）
  cooc.py                  （realtest/）共现层 + ConceptEngine
  tree_io.py               树持久化
  make_reference.py        定性参照 claim + T3 反例题库
  review.py                T2 对照人工抽检底稿
  score.py                 双通道 + claim 分解 + 对照 + T3（thinking judge）
  questions_absence.json   多会话 QA 题库（冻结）
  questions_ctx.json       ctx 分析题库：usage 28 + T2 4 + T3 6 批
  reference_claims.json    36 条定性参照
  corpus/ | corpus_absence/  语料产物
  out/                     各轮回放与评分产物
```

### 复现命令
```bash
# 建树（一次）
python3 realtest/chatbench/replay.py --corpus realtest/chatbench/corpus/corpus.jsonl \
  --questions realtest/chatbench/questions_ctx.json --cooc off --temp 0 \
  --save-tree realtest/chatbench/out/tree_ctx.json.gz --out realtest/chatbench/out/ctx_off
# on（复用树）
python3 realtest/chatbench/replay.py --corpus realtest/chatbench/corpus/corpus.jsonl \
  --questions realtest/chatbench/questions_ctx.json --cooc on --temp 0 \
  --load-tree realtest/chatbench/out/tree_ctx.json.gz --usage-passes 4 --out realtest/chatbench/out/ctx_on
# 评分
python3 realtest/chatbench/score.py --off .../ctx_off/replay_result.json \
  --on .../ctx_on/replay_result.json --questions .../questions_ctx.json \
  --reference .../reference_claims.json --out .../ctx_score.json
```
