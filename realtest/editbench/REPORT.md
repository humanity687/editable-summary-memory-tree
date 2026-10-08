# 编辑风暴实验 · 正式报告（2026-10-01）

> 面向论文的正式报告（**仅 seed2 小数据主实验**）：实验设计 + 主表 + 结论 + 数据位置。
> 会话流水与 bug 记录见 `EXPERIMENT_REPORT.md`；设计权威 = 根 `AGENTS.md`。
> 大数据 ahi-act（《沙箱四声》147k）的实验与结果见 **`AHI_ACT_REVIEW.md`**（本报告只放 seed2）。
> A/B(`edit_signal`) 为 seed2 matched-pair，支持"编辑作使用信号"的**全局巩固**主张。数字均非论文定稿。

---

## 1. 实验设计

### 1.1 单因子：结构 × 更新策略
比较 4 种"参考资料上下文结构"在**发生编辑**后的表现，每结构两种更新策略：
- **结构**：`full`（全文发送，无预算，上下文腐烂基线）/ `flat`（400 字 chunk + bge 向量 top-12）/
  `raptor`（递归摘要 + collapsed top-k，复用 chatbench）/ `tree_v2`（水循环记忆树，真实编辑路径）。
- **更新策略**：`rebuild`（从**终稿**重建，上界参考）/ `incremental`（只保留编辑，尝试"延续")。
- **tree 额外面**：`inc_cold`（无 Phase-1 使用史的旧树 + 同一编辑）——把"原地失效"与"使用史连续性"分离。
- **A/B 额外臂**：`signal`（编辑新原子给热 HP，当使用信号）vs `obstacle`（给冷 HP，当障碍）。

### 1.2 文档与编辑
- **文档**：seed2《缠枝莲》12,191 字，7 处确定性编辑（rename / rewrite×2 / detail×2 / delete / append）。
- **编辑是确定性脚本**（`editstorm.py`，零 LLM），台账 = 所有延续臂的**同一份**输入。

### 1.3 各臂"编辑期输入"的公平渲染（关键修正）
所有 append-only 臂（full / flat / raptor）的编辑结果均渲染成 **function-calling 式"调用+返回"块**
（每次工具调用 `<tool_call name="edit_document">{args}</tool_call>` + `<tool_result>新内容</tool_result>`），
而非平铺 dump——避免"基线故意给差格式"的质疑。tree 用真实编辑路径（原地失效 + 懒重建）。

### 1.4 判分与统计
- **判分流水线**：确定性初筛（句内 require/forbid，先按句末标点/换行切段再归一）→ **仅失败样本**过 LLM judge 复核
  （消 `forbid` 对"引旧文并说明已改"的误伤）。复述 = checklist 覆盖/残留 + judge。
- **温度**：作答 `0.7`、蒸馏/判分 `0`。**每臂独立 API key**。方案 A：编辑/构建各 1 次，重复作答。
- **快照恢复**：所有臂在 Phase 2 结束点打快照，每遍作答前 `restore` → 重复测量独立。
- **预算**：seed2 tree 7k 字符；full 无预算（全文）。
- **N**：seed2 = 3 遍。

### 1.5 数据集与题库
- seed2：40 题（15 反转 / 5 不变 / 20 远端），7 编辑，题库 `questions_full.json`。
- 所有反转题过**锚点校验**：`before.require` 在原文出现、`after.require` 在新文出现、`after.forbid` 在新文缺席（两侧互斥）。
  **注（2026-10-06 更正）**：本版（C1）题库早于「锚点唯一性校验」流程，锚点采用短片段/全局串、按**出现性**判定；
  旧稿「before 唯一且在原文、after 唯一且在新文」表述有误（实测 `何倚笙` 在原文出现 29 次）。

---

## 2. 表 1：小数据主实验（seed2《缠枝莲》，N=3，temp 0.7）

> 全臂 function-calling 格式；tree 用 cool=4 最小冷却。QA = 40 题 × 3 遍；反转 = 15 题 × 3 遍。

| 结构 | P1 基线 | inc 总 | inc 反转 | reb 总 | reb 反转 | inc 复述 覆盖/残留 | reb 复述 覆盖/残留 |
|---|---|---|---|---|---|---|---|
| full   | 117/120 (98%) | 111 (92%) | 41/45 | 113 (94%) | 45/45 | **5% / 72%** | 90% / 0% |
| flat   | 113/120 (94%) | 83 (69%)  | 21/45 | 112 (93%) | 45/45 | 43% / 78% | 67% / 0% |
| raptor | 110/120 (92%) | 84 (70%)  | 23/45 | 108 (90%) | 45/45 | 48% / 33% | 71% / 0% |
| tree   | 109/120 (91%) | **107 (89%)** | **41/45** | 105 (88%) | 42/45 | **100% / 0%** | 71% / 0% |
| tree·cold | — | 99 (82%) | 41/45 | — | — | 86% / 0% | — |

**表 1 结论**
1. **全臂格式公平后，反转轴不再有"结构大胜"**：`inc_tree 41 ≈ inc_full 41 ≈ reb 42–45`；
   平坦检索式追加（flat 21 / raptor 23）仍显著腐烂。
2. **tree 的独有优势落在全局一致性（复述轴）**：`inc_tree 100%/0%`（覆盖 100%、旧残留 0%），
   而所有 append-only 增量臂旧残留 33–78%。
3. 编辑时刻：tree 编辑路径 **0.3 ms（零 LLM）**，蒸馏推迟到冷却窗口。

---

## 3. 表 2：A/B 支持数据（seed2，编辑作"使用信号(热)" vs "障碍(冷)"）

> seed2，N=3；**同一棵 pre-edit 树**（warm/cold 各一）分别施加 on/off 编辑，唯一差异 = 编辑新原子 HP。

| 臂 | 总 | 每遍 | 反转 | 复述 覆盖/残留 |
|---|---|---|---|---|
| warm_signal（热） | 101/120 | [34,33,34] | 40/45 | **100% / 0%** |
| warm_obstacle（冷） | 108/120 | [36,36,36] | 42/45 | 86% / 0% |
| cold_signal（热） | 103/120 | [35,34,34] | **42/45** | **81% / 0%** |
| cold_obstacle（冷） | 97/120 | [34,32,31] | **34/45** | 71% / 0% |
| reb（参考） | 107/120 | [35,36,36] | 42/45 | 86% / 0% |

**表 2 结论（A/B edit_signal 主张）**
1. **复述轴：signal 一致更好**（warm 100% vs 86%、cold 81% vs 71%，均 0 残留，逐遍一致）
   → **把编辑当使用信号（热/入窗/巩固）显著提升全局视图对编辑内容的覆盖**。这是最干净的支持数据。
2. **Q/A 冷树：signal 更好**（103 vs 97，反转 42 vs 34）→ 无其他使用史时，热编辑有增益。
3. **Q/A 热树：signal 略差**（101 vs 108，反转 40 vs 42）→ 有使用史时热编辑与既有热区争注意力，
   是**权衡**而非全胜。
4. 因此可主张"**编辑作为使用信号利于全局巩固**"，**不可**主张"Q/A 上一律更好"。

---

## 4. 主张汇总（seed2）
1. **原地失效让"修改不腐化记忆"**：tree 增量在反转上追平 rebuild/full（表 1），
   且全局复述零腐烂（100%/0%），append-only 臂则残留 33–78%。
2. **修改作为使用信号**：热编辑显著提升全局视图对编辑内容的覆盖（A/B 表 2）；Q/A 上是热冷权衡。
3. **成本**：tree 编辑路径零 LLM（0.3 ms），蒸馏推迟到冷却窗口。
   （大数据上"作答路径 ~1/10、但维护成本反向"的口径见 `AHI_ACT_REVIEW.md`。）

## 5. 已知限制
- 不变/远端层受检索/预算主导，**不能**从中推出"结构优势"，只能用于编辑轴外推的稳定性检查。
- 单文档（seed2 一篇），多文档泛化未做。
- retell 跨运行噪声地板较大（reb 复述 71–86%），A/B 结论依赖 matched-pair 而非跨运行。
- 大数据 ahi-act（含"反转不追平"、维护成本）的限制见 `AHI_ACT_REVIEW.md`。

## 6. 数据位置（`realtest/editbench/out/`）
| 产物 | 路径 | 说明 |
|---|---|---|
| 小数据主实验（合并新格式） | `seed2_r3_8arm_cool4/results.json` | 表 1 |
| ∟ tree 臂原始 | `seed2_r3_tree_cool4/` | inc/inc_cold/reb tree |
| ∟ full 臂重跑 | `seed2_full_r3/` | function-calling 格式 |
| ∟ flat/raptor 重跑 | `seed2_flat_raptor_r3/` | function-calling 格式 |
| A/B matched-pair（表 2） | `seed2_ab_editsignal/results.json` | 编辑作使用信号 |
| A/B 跨运行对照（作废参考） | `seed2_tree_obstacle_r3/` | 仅作噪声对照 |
| 大数据产物 | 见 `AHI_ACT_REVIEW.md` | ahi《沙箱四声》 |
| 题库 | `questions_full.json` | 40 题 |
| 编辑脚本 | `editstorm.py` | 确定性编辑 + function-calling 渲染 |
| A/B 脚本 | `ab_edit_signal.py` | matched-pair |

## 7. 可复现命令
```bash
# 小数据主实验（全臂；四结构并发 + 合并，2026-10-02 起）
bash realtest/editbench/run_seed2.sh     # → out/seed2_r3_par/
# 等价单进程（历史）：
python3 realtest/editbench/docbench.py --scenario seed2 --doc realtest/seed2/story.txt \
  --questions realtest/editbench/questions_full.json --keys realtest/editbench/keys.local.json \
  --repeats 3 --answer-temp 0.7 --tree-budget 7000 --judge --out realtest/editbench/out/seed2_r3
# A/B matched-pair（编辑作使用信号）
python3 realtest/editbench/ab_edit_signal.py --scenario seed2 --doc realtest/seed2/story.txt \
  --questions realtest/editbench/questions_full.json --tree-budget 7000 --repeats 3 --judge \
  --out realtest/editbench/out/seed2_ab_editsignal
```
# 大数据 ahi 主实验命令与结果见 AHI_ACT_REVIEW.md
