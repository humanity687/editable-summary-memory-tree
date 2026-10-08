# ChatBench — 聊天智能体实验数据记忆 bench（设计 v1 落地）

> 数据源：`ahi-multi/data/system.db`（context_compare 运行，4 agent = 青/蓝/墨/白）。
> 引擎：`realtest/realengine.py`（off）/ `realtest/cooc.py::ConceptEngine`（on）。
> 模型：deepseek-flash（no think，DeepSeekClient 默认 `thinking.disabled`）。
> 参照：LongMemEval 五能力（信息提取 / 多会话推理 / 知识更新 / 时间推理 / 拒答）。

## 已完成的 P0（数据工程）

`extract_corpus.py` → `corpus/`：
- `corpus.jsonl` — **716 条**去重 agent 消息、**289,666 字**、稳定 `msg_id`（回放输入 + gold 引用锚）。
- `transcript_full.txt` — 人读全量转写（时间序，每条带 `#[msg_id[:8]]` 与 来源→目标）。
- `by_agent/*.txt` — 单 agent 发言。
- `sandbox_notes.md` — agent 现场协作笔记（受控语料，含矛盾/修订）。
- `reports/*.md` — 6 份已有分析报告（T3 报告验证参照物）。
- `index.json` — 规模/会话/实体骨架。
- 纪律：**原始措辞零清洗**；append-only → 无物理编辑事件（γ 边弱，已在声明中）。

## 关键数据事实（决定设计取舍）

- 全部消息落在 **1 个会话**（10:49–11:47 UTC，<1h）→ `gap>6h` 会话切分在此数据上=单会话。
  需改用**话题轮（driver topic round）**或时间窗做“伪会话边界”来制造多会话推理（MS）题。
- 报告（`analysis_*.md`）是**项目侧**对 agent 行为的分析（不是 agent 写的），天然适合 T3：
  报告 claim ↔ 原始消息。
- `sandbox_notes.md` 含**立场修订**（如前文“诗不可…/后文转向”），是 KU 题原料。

## 公正性闸（硬性，跑前冻结）

1. **usage ≠ test**：阶段 W 的工作查询来自独立 `usage_queries`，与测试题做 embedding 相似度
   审计（>0.85 淘汰），分布与清单**跑前归档**。
2. **T1-b 零词面重叠**：查询与 gold 答案 bigram 交集=0 由**代码**校验（写手 A/B 双写）。
3. **dev/test 留出**：参数/prompt 调优只在 dev；test 只跑一次。
4. **四臂同一回放**：off / on / RAG / 全量（语料装不下则省略全量），只有共现层开关不同。
5. **引用锚**：所有作答要求 `原子ID + 原文摘录`；引用真实性=确定性字符串检查。

## 分阶段交付（对应设计 §9）

- **P0 数据工程** ✅（本目录 corpus/）。
- **P1 题库**（需你参与 gold 定稿）：
  - T1 细节问答：a 直接 / b 转述 / MS 跨话题聚合 / KU 最新值 / KU-hist 历史值 / TR 时序 / AB 拒答。
  - T2 共性分析（开放，≥3 claim，claim 分解评分）。
  - T3 报告验证（对 `reports/*.md` 逐 claim 三分类，植入已知错误 claim）。
  - schema 见 `questions.example.json`；gold 用 `msg_id` 引用。
- **P2 打分校准**：claim 双标 ≥85% 一致率；分歧规则成文。
- **P3 回放执行**：`replay.py` 注入调度 + 阶段 W/T + 四臂开关（**待建**）。
- **P4 报告**：九型四臂表 + 暖冷分层 + 归因指标（hub 贡献率/类型分布）+ 人工复核率。

## 目标文件结构

```
realtest/chatbench/
  extract_corpus.py            ✅
  corpus/                      ✅
  PLAN.md                      ✅
  questions.example.json       schema 示例（下一版手写实际题）
  replay.py                    回放控制器（W/T、四臂、assemble/tick/merge 调度）
  score.py                     双通道 + 引用真实性 + claim 分解 + 报告三分类
```

## 待你拍板

1. **伪会话/边界**：MS 题用 driver topic 轮 vs 时间窗（建议 topic 轮，30s 一轮天然分段）。
2. **语料规模**：全量 716 条（重，蒸馏量大）还是先按时间切片（如前 300 条）跑通再全量。
3. **gold 由谁定**：T1 细节题我出候选、你抽检即可；T2/T3 的 gold（claim 标签）必须你定稿。
