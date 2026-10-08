# GAPS.md — Paper 1 数据缺口跟踪

> 用途：投稿前自检 + 审稿定位。每条缺口标注**支撑的论文主张 / 现状文件 / 补跑命令 / 输出路径 / 状态**。
> 纪律：声称存在但未跑 = 不存在（`AGENTS.md §9.11`）。生成：2026-10-06。

---

## 已解决（本次及前序）

| 项 | 状态 | 依据 |
|---|---|---|
| C1/C2 反转题锚点口径 | ✅ 已更正为"出现性" | 见本目录 `REPORT.md`、`EXPERIMENT_REPORT.md`、`questions_ahi.json`；`CORPUS_QA.md §9` |
| C3–C5 工具链入库 | ✅ | `build_corpus.py`/`gen_questions.py`/`specs/`/`questions_D*.json` 已入 git |
| 逐题 pass 持久化 | ✅ | `docbench.py` 新增 `results["qa_pass"]`（`<arm>·p1/p3` → `{id:[pass per repeat]}`，含 judge 复核） |
| 配对显著性脚本 | ✅ | `significance.py`（McNemar + Wilcoxon，prefer `qa_pass`） |

---

## 必补（论文主张依赖）

### G1. C3–C5 四结构（补 raptor）
- **支撑主张**：结构对召回不敏感（跨 5 语料）；tree 复述零残留跨语料。
- **现状**：`out/corpus_run2/{D1_design,D2_experiments,D3_platform}/results.json` 有 tree/flat/full（7 臂），**无 raptor**。
- **补跑**：`bash realtest/editbench/run_corpus_all.sh`
  （或 `PARALLEL_DOCS=1 bash realtest/editbench/run_corpus_all.sh` 三篇并发）
- **输出**：`out/corpus_run3/{D}/results.json`（tree/flat/full/raptor × {inc,reb} + P1 + cold；含 `qa_pass`）
- **状态**：✅ **已完成（2026-10-07）** — D1/D2/D3 三篇四结构各 5 遍；`inc_tree` 复述残留恒 0%，append-only inc 残留 42%/60%/52%

### G2. ahi raptor 同口径
- **支撑主张**：8 臂主表；成本表同轮口径。
- **现状**：`out/ahi_r5/results.json` 的 `inc_raptor`/`reb_raptor` 为 **retr6 + 旧占位**；
  `out/ahi_strong/*` **无 raptor**。
- **补跑**：`bash realtest/editbench/run_ahi.sh`（`run_parallel.sh` 已含 raptor；输出 `out/ahi_r5_par/`）
- **输出**：`out/ahi_r5_par/{full,flat,raptor,tree_v2}/results.json` + 合并 `results.json`
- **状态**：✅ **已完成（2026-10-07）** — 同轮四结构 ×5；`inc_raptor 180/205 (反70)`、`inc_tree 169 (反68)`、`inc_full 183 (反78)`

### G3. ahi 成本表统一口径
- **支撑主张**：作答路径 ≈ 1/10（须同轮、须二分）。
- **现状**：成本散在多次独立 run；单轮全臂文件只有 `ahi_r5`（旧口径）。
- **做法**：G2 的同一次 `run_ahi.sh` 产出即"同一轮 8 臂"；从此文件抽 `tokens.final`/`tokens.phase3`/`edit_time`。
- **输出**：`out/ahi_r5_par/results.json`
- **状态**：✅ **已完成（2026-10-07）** — 同一轮全 9 臂成本见该文件 `tokens.final`/`edit_time`；tree 作答≈full 的 1/10、蒸馏反向

### G4. 统计口径（方差 + 配对显著性）
- **支撑主张**：所有对比（inc vs reb、tree vs full/flat、signal vs obstacle）。
- **现状**：per-repeat 数组 + mean/std 已随每个 `results.json` 落盘；**显著性从未计算**。
- **做法**：
  ```bash
  # 已持久化 qa_pass 的新 run（corpus_run3 / ahi_r5_par）
  python3 realtest/editbench/significance.py --run <results.json> --out <results.json 同目录>/significance.csv
  # 旧 run（seed2_r3_8arm_cool4 / ahi_r5）无 qa_pass → 确定性回退，偏严（会低估 tree，已在输出标注）
  ```
- **输出**：各 run 目录下 `significance.csv`（Q/A McNemar + 按题 Wilcoxon + 复述 Wilcoxon）
- **状态**：✅ **已完成（2026-10-07）** — `out/ahi_r5_par/significance.csv`、`out/corpus_run3/{D1,D2,D3}/significance.csv`（新 run，9/9 臂含 judge）。
  典型：ahi `inc_tree vs inc_full` 反转层无显著差异（p=0.14）；D1/D2/D3 `inc_tree vs inc_full` 显著（p<1e-4 / p<1e-4 / p=0.005）。
  **新发现：tree 反转 run 间方差大**——本 run `inc_tree` 反转 68/85，旧同代码基线 `ahi_strong/baseline` 84/85（Phase1 一致 64/85），单 run 结论不稳，需多 run。

---

## 强烈建议（审稿防御）

| 项 | 说明 | 状态 |
|---|---|---|
| 成本表二分披露 | 作答路径 vs 蒸馏维护（含 settle 墙钟），同轮口径 | 随 G2/G3 |
| 反转分母 | D1–D3 各仅 8 反转（预注册要求 ≥15） | ⬜ 待扩题 |
| 多 seed | 现每语料 1 run，无跨 seed 方差 | ⬜ 待补 |

---

## 可选（不进主文，按定位文档处理）

| 项 | 产物 | 状态 |
|---|---|---|
| A/B 编辑信号 | `out/{seed2,ahi}_ab_editsignal/results.json`；`ab_edit_signal.py`；`REPORT.md §3`、`AHI_ACT_REVIEW.md §5.7`（ahi 为旧占位） | 已有 |
| 占位透明消融 | `out/ahi_tree_replay_ph`、`out/ahi_r5_combined`；`replay_trees.py`；`AHI_ACT_REVIEW.md §5.6`、`EXPERIMENT_REPORT.md §6.5`（含 retr 混淆更正） | 已有 |

---

## 已知口径陷阱（引用前必读）

1. **确定性 vs judge**：`significance.py` 对无 `qa_pass` 的旧 run 用确定性 `check_spec` 复算，**偏严**且**系统性低估 tree**（seed2 `inc_tree` 81 vs 主表 107）。新 run 的 `qa_pass` 含 judge，与主表一致。**跨新旧不可混比。**
2. **retr 混淆**：`doctbench --retr-budget` 曾被 config 覆盖；旧 `ahi_r5` 实跑 retr=6（见 `EXPERIMENT_REPORT.md §6.5`、`AHI_ACT_REVIEW.md §3/§5.6`）。
3. **复述口径**：`retell.coverage_ratio` 是**末遍**值；报告引用**各遍均值**（`per_repeat_coverage` 均值 / `all_runs_summary.csv` 的 `_mean` 列）。
4. **代码漂移**：正式引用用同代码基线；跨轮前核对 `results.json` 的 `_meta` 与运行脚本全部控制变量（`AGENTS.md §9.12`）。
