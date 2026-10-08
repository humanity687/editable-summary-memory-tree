# Paper 1 参考资料清单（供写作 Agent 使用）

> 本文件是**导航索引**，不是设计权威。一切机制以 `AGENTS.md` 为准；一切实验数字以对应
> `results.json` 与实验报告为准。写论文只读本清单 + 被指向的文件即可，不必通读全仓。
> 生成日期：2026-10-05。

---

## 0. 论文定位（先读这一节）

- **推荐标题（EN）**：*Structure Buys Correctness: Editable Summary Trees and an Edit-Correctness
  Benchmark for Long-Context Memory*
  **中文**：结构买的是正确性：可编辑摘要记忆树与长上下文「编辑正确性」基准
- **一句话主张**：在层次化可编辑记忆上，**区间精确失效买到的是编辑正确性**（零旧值残留、确定性预算、
  可审计），**召回对结构不敏感**（受模型/检索束缚）。
- **三条贡献**：
  1. 机制/系统：位置有序可编辑摘要树 + 区间精确失效 + 零 LLM 零阻塞编辑路径 + 脏卡懒重建 + 账本回滚 + 恒定预算。
  2. 基准：第一个「编辑正确性」基准（四结构 × 编辑风暴 × 反转/不变/远端三层 + 复述覆盖/旧值残留）。
  3. 实证：**同引擎/同语料/同预算**下结构对召回不敏感；结构买正确性（零残留跨 5 语料复现）；强模型/强向量鲁棒性。
- **措辞红线**（查新收缩版，见 `paper-dir-record.txt`）：
  - ✅ 可说「首个可编辑的文本摘要记忆树」「首个编辑正确性基准」（配限定）。
  - ❌ 别写「所有系统都是 append-only」「首创失效概念」；Mem0/Graphiti 已有事实/边级失效。
  - ❌ 召回别写「结构提升/退化」——写「同预算下不敏感」。

---

## 1. 必读文档（权威 / 框架 / 综述）

| 作用 | 文件 |
|---|---|
| **设计权威**（机制 + 参数台账 §11） | `AGENTS.md` |
| 实验历史/结果汇总（§4.x、§15、§16） | `HANDOFF.md` |
| 对外简介 | `README.md` |
| **查新 + 两篇拆分 + 主张收缩** | `paper-dir-record.txt` |
| 编辑风暴实验全报告（seed2/ahi/强臂） | `realtest/editbench/EXPERIMENT_REPORT.md` |
| ahi 主实验 8×5 + 就绪审查 | `realtest/editbench/AHI_ACT_REVIEW.md` |
| seed2 报告 | `realtest/editbench/REPORT.md` |
| 创新点/差分审查 | `realtest/editbench/INNOVATION_REVIEW.md` |
| **语料 + 题库设计 + 预注册**（不入语料） | `realtest/editbench/CORPUS_QA.md` |
| 聊天记忆基准 | `realtest/chatbench/EXPERIMENT_REPORT.md` + `COMPARE_REPORT.md` |

---

## 2. 机制代码（写 method 节用）

| 模块 | 文件 | 要点 |
|---|---|---|
| 桩引擎（零 LLM） | `core.py` | 原子/顺序链表/区间覆盖树/编辑失效/merge/铺陈组装/检索/影子/提升/回滚/注意力窗口 |
| 真实引擎 | `realtest/realengine.py` | LLM 蒸馏摘要 + 混合检索（向量+关键词+原子精排）+ 笔记本 + 失效/重建 + budget trim |
| 多介质切分 | `realtest/chattext.py` | code AST / config / md / 超限再切（`ATOM_CHAR_CAP`） |
| 句切分 | `realtest/splitter.py` | 中文句级 |
| LLM 客户端 | `realtest/llmclient.py` | DeepSeek/Ollama；`thinking` 开关、`max_tokens` |
| 属性测试 | `tests/`（303 绿） | 标记完备性/铺陈不变量/编辑边界/回滚对称/区间覆盖（§8.1） |

---

## 3. 基准代码与工具链（写 bench/实验节用）

| 文件 | 作用 |
|---|---|
| `realtest/editbench/docbench.py` | 编排器：P1 四结构基线 → P2 编辑风暴 → P3 8 臂(inc/reb) → P4 复述；`--splitter {sentence,line,md}` |
| `realtest/editbench/arms.py` | 四结构统一接口：`FullArm / FlatArm / RaptorArm / TreeArm`；每臂蒸馏+作答双 client |
| `realtest/editbench/editstorm.py` | 确定性编辑（rename/detail/rewrite/passage/delete/append/insert）+ 台账 |
| `realtest/editbench/docscore.py` | Q/A 分层判分 + 复述覆盖/残留（支持**别名匹配**） |
| `realtest/editbench/judge.py` | LLM 判分（仅失败样本复核） |
| `realtest/editbench/build_corpus.py` | 项目文档语料拼接 + 去标识化 + 自指护栏 |
| `realtest/editbench/gen_questions.py` | spec→题库 + 锚点校验 |
| `realtest/editbench/retell_only.py` | 只重跑 Phase4 复述（tree 读检查点、flat/full 重建） |
| `realtest/editbench/replay_trees.py` | 零重建重放（复用树 gzip） |
| `realtest/editbench/run_*.sh` | ahi/seed2 并发运行 |
| `realtest/bench_qa.py` | 结构化压测（记忆体检/编辑风暴/混合循环） |

---

## 4. 数据 / 语料

| 语料 | 路径 | 长度 | 来源/许可 | 反转题数 |
|---|---|---|---|---|
| seed2《缠枝莲》 | `realtest/seed2/story.txt` | 12.2k | 合成，无版权 | 15 |
| ahi《沙箱四声》 | `realtest/editbench/ahi_script.txt` | 147.1k | 真实聊天日志转剧本（`gen_script.py`） | 17 |
| 项目语料 D1 设计规格 | `realtest/editbench/out/corpus_proj/D1_design.txt` | 69.7k | 本项目 git 跟踪文档拼接 | **8（未达标）** |
| 项目语料 D2 实验报告 | `…/D2_experiments.txt` | 81.6k | 同上 | **8（未达标）** |
| 项目语料 D3 平台/交接 | `…/D3_platform.txt` | 138.5k | 同上 | **8（未达标）** |

- 题库：`realtest/editbench/questions_{ahi,full,D1_design,D2_experiments,D3_platform}.json`
- spec（编辑+题+预注册单源）：`realtest/editbench/specs/{D1_design,D2_experiments,D3_platform}.json`
- 语料统计：`realtest/editbench/out/corpus_proj/report.json`（重复率/自指 token 已清）
- 注意：`out/`、`corpus/`、`debug_review_*` 均为 gitignore 的本地产物；语料可由 `build_corpus.py` 再生。

---

## 5. 关键结果产物（写 results 节用）

| 结果 | 文件 | 关键结论 |
|---|---|---|
| ahi 8×5 主表 | `out/ahi_r5/results.json`、`out/ahi_r5_combined/results.json` | tree 复述零残留；反转轴见报告 |
| ahi 占位透明重放 | `out/ahi_tree_replay_ph/results.json` | 见 AHI_ACT_REVIEW §5.6（**已被 retr 混淆更正**） |
| **强模型/强向量鲁棒臂** | `out/ahi_strong/{baseline,think_tree,think_full,think_flat,bgelarge,bgelarge_flat,baseline_flat}/results.json` | 表见 EXPERIMENT_REPORT §6.2 |
| **项目语料 4 结构** | `out/corpus_run2/{D1,D2,D3}/results.json` | Q/A + 复述 |
| 项目语料复述重跑 | `out/corpus_run2/{D1,D2,D3}/retell2/retell2.json` | record/nonrecord 覆盖 + rot |
| 编辑信号 A/B | `out/ahi_ab_editsignal/`、`out/seed2_ab_editsignal/` | 暖树 rep 更好（热冷权衡） |
| 缓存前缀探针 | `out/cache_probe/` | 前缀稳定性解释单位 token 成本 |
| seed2 3×8 表 | `out/seed2_r3_8arm_cool4/` | EXPERIMENT_REPORT §2.5 |

### 5.1 强模型/强向量表（EXPERIMENT_REPORT §6.2，Q/A 总/反转；复述 覆盖/残留）
| 条件 | P1 | inc | reb | inc 复述 |
|---|---|---|---|---|
| full no-think | 196/反78 | 179/反70 | 195/反76 | 24%/12% |
| **full THINK** | 202/反83 | **198/反79** | 200/反82 | 84%/**34%** |
| flat no-think | 181/反68 | 178/反65 | 172/反59 | 36%/20% |
| **flat THINK** | 187/反74 | **189/反76** | 188/反73 | 40%/26% |
| tree no-think | 171/反64 | 182/**反84** | 197/反82 | 64%/**0%** |
| tree bge-large | 167/反62 | 178/反74 | 185/反80 | 58%/**0%** |
| **tree THINK** | 177/反69 | 182/反80 | 186/反81 | 80%/**0%** |

### 5.2 项目语料（corpus_run2 + retell2）
| 文档 | inc_tree Q/A | inc_flat | inc_full | inc_tree rec/nonrec/rot | inc_full rec/rot |
|---|---|---|---|---|---|
| D1 | 55/100 | 84 | 92 | 0.7 / 0.6 / 0 | 1.0 / **0.9** |
| D2 | 29/100 | 44 | 74 | 0.2 / 0.3 / 0 | 0.5 / **1.0** |
| D3 | 70/100 | 91 | 90 | 0.9 / 0.7 / 0 | 0.5 / **1.0** |

- **头条**：tree 复述旧值残留恒 **0**；append-only（full/flat）>0（full 达 1.0）。
- **预注册**：暖树 record 覆盖 (0.2–0.9) 显著 > inc_cold(0)/reb_tree(0–0.2)——**方向成立、幅度未达 ~100%**。

---

## 6. 关键数字速查

- **tree 编辑路径**：6–13 ms、**零 LLM**；蒸馏推迟到 settle（数百 ms–秒级）。
- **作答 path token**：tree ≈ full 的 **1/10**；但**蒸馏 completion 反向**（tree ≈ full 的 4×）——必须并列披露。
- **缓存前缀命中**：full 0.998 / flat 0.95 / tree 0.66–0.76 / raptor 0.22。
- **属性测试**：`tests/` 303 绿。
- **seed2 3×8（§2.5）**：inc 反转 full 41 ≈ tree 41、flat 21、raptor 23；tree 复述唯一 100%/0%。

---

## 7. 复现命令

```bash
# 单元测试
python3 -m pytest tests/ -q

# ahi 四结构并发（16k / retr20 / N=5 / judge）
bash realtest/editbench/run_ahi.sh

# seed2
bash realtest/editbench/run_seed2.sh

# 项目语料（tree/flat/full；行切分；retr20）
python3 realtest/editbench/docbench.py --scenario D3_platform \
  --doc realtest/editbench/out/corpus_proj/D3_platform.txt \
  --questions realtest/editbench/questions_D3_platform.json \
  --keys realtest/editbench/keys.local.json --splitter line \
  --structures tree_v2,flat,full --repeats 5 --answer-temp 0.7 \
  --tree-budget 16000 --budget 16000 --retr-budget 20 --judge \
  --out realtest/editbench/out/corpus_run2/D3_platform

# 生成/校验题库
python3 realtest/editbench/gen_questions.py realtest/editbench/specs/D3_platform.json
# 只重跑复述
python3 realtest/editbench/retell_only.py --name D3_platform \
  --reuse realtest/editbench/out/corpus_run2/D3_platform \
  --out realtest/editbench/out/corpus_run2/D3_platform/retell2 --repeats 5
```

---

## 8. 已知修正与坑（写论文前必读）

1. **retr 混淆更正**：`docbench --retr-budget` 原被 `config.json` 的 6 静默覆盖；旧 `AHI_ACT_REVIEW §5.6`
   的「占位透明 +7~11」实为 **retr 6→20**（同代码 base 树 2×2：透明≈+1、retr≈+13）。见 EXPERIMENT_REPORT §6.5。
2. **输出截断**：`DeepSeekClient.chat` 原先不传 `max_tokens` → 长复述被 API 默认上限砍（D3 inc_full 曾断在
   「P2：索引」）。已加 `max_tokens`，config 设 **384k**。
3. **复述 prompt**：原为叙事口径，技术文档上模型拒答（覆盖≈0）。已改文档无关口径。见 `docbench.run_retell`。
4. **复述 checklist 别名匹配**：标识符在自由复述中不回显；`docscore.score_retell` 支持别名列表。
5. **tree 在长技术文档上召回弱**：D2 inc_tree 29/100（不变题 2/20）→ 行切分原子+摘要层+retr20 丢具体数字；
   与 ahi 上 tree 追平相反。**必须在论文中正面处理**（结构价值随语料/检索预算变化）。
6. **去标识漏网**：D1 spec 的 insert/append 文本含 `Qwen3-32B`；D2/D3 语料含裸 `gemma`/`DeepSeek`（未清）。
7. **自指护栏**：`build_corpus.py` 拒收含基准设计标记的源文件；题库/语料设计文档在 `CORPUS_QA.md`（不入语料）。

---

## 9. 统计 gating 状态（诚实）

| 项 | 状态 |
|---|---|
| temp>0 重复测量 | ✅ N=5（ahi/项目语料）、N=3（seed2） |
| 反转分母 ≥15/文档 | ⚠ seed2=15、ahi=17；**D1/D2/D3 各仅 8**（需补） |
| **3 文档 × 3 seed** | ❌ 现每篇 1 run；无跨 seed 方差 |
| inc_cold 分离臂 | ✅ |
| judge 初筛+复核 | ✅ |
| 成本表 | ✅（二分披露） |
| H1–H5 预注册 | ⚠ 骨架在，未成文 |
| 属性测试 | ✅ 303 绿 |
| 强模型/强向量子集臂 | ✅（`ahi_strong`） |

---

## 10. Paper 2（人格保持）单列参考资料

- SPEC：`ahi-bench/PERSONA_RETENTION.md`（v0.6；§10.3.1 pr6_sq 结论 / §10.3.2 分层 SQ v2 预注册）
- 报告：`ahi-bench/PR5_REPORT.md`、`ahi-bench/out/deepseek-v4-flash/pr5_neutral/CONTROL_RESULT.md`
- 编排/评分：`ahi-bench/pr_orchestrate.py`、`ahi-bench/pr_score.py`、`ahi-bench/pr_analyze.py`
- flat 臂：`ahi-multi/agents/flat-rag/agent.py`
- 关键结论：趋同根因=树管线/跨 agent 耦合；`style_alpha` 保人格有效 +55% 内存；`self_quota`（pr6_sq）
  H-SQ1/3/4 达标、H-SQ2 未达标（自我可见性=必要不充分）。**三杠杆只闭合 α 一条**。

---

## 11. 给写手 Agent 的建议分工

1. **Intro/Method**：只读 `AGENTS.md` §1–§5 + `core.py`/`realengine.py`；贡献三条照 §0。
2. **Benchmark**：读 `docbench.py`/`arms.py`/`editstorm.py`/`docscore.py` + `EXPERIMENT_REPORT §1/§6.5`。
3. **Results**：以 `results.json` 为准，表模板照 §5.1/§5.2；头条=零残留跨语料。
4. **Related work**：以 `paper-dir-record.txt` 的 T1/T2/T4/T5/T6 收缩版为准。
5. **Robustness**：EXPERIMENT_REPORT §6。
6. **Alignment/ablation**：先别吸收 P2 材料（属 Paper 2）。
