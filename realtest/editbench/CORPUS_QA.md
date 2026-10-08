# 项目文档语料与题库设计（**不得**进入语料源）

> 本文件记录 D1/D2/D3 语料构造与题库/预注册设计。
> **build_corpus.py 禁止**把本文件或含以下标记的内容拼进语料：
> `项目文档语料`、`题库与预注册`、`coverage_record`、`build_corpus`、`gen_questions`。

## 7. 项目文档语料（D1/D2/D3，2026-10-04）

> **背景**：Paper 1 的统计 gating 要求「多文档泛化」。项目自身文档是**真实、无版权、实体/数字/ID 密集**
> 的长参考材料，可作 seed2（叙事）/ ahi（对话）之外的第三类语料。工具 `build_corpus.py`（仅取
> `git ls-files` 中的文档，保证可复现/可分发）。

### 7.1 切分与产物
`build_corpus.py` 做：去 BOM/控制符 → 丢纯表格分隔行 → **剔自指答案键行**（见 §7.3）→ 超长行折行(600)
→ 跨文件**精确段去重**。输出 `out/corpus_proj/{D1_design,D2_experiments,D3_platform}.txt` + `report.json`。

| 语料 | 组成 | 规模 | 精确重复句 | 备注 |
|---|---|---|---|---|
| D1_design | AGENTS + USAGE_SPEC + PERSONA_RETENTION + SPEC | 69.7k | 0.2% | 机制规格 |
| D2_experiments | editbench/chatbench 报告 + PR5 + ahi-multi 实验文档 | 81.7k | 0.5% | 实验报告集（已移出 HANDOFF 以降重复） |
| D3_platform | HANDOFF + READMEs + ahi-multi 平台/交接 | 138.7k | 0.9% | 平台+会话纪要 |

### 7.2 质检（3 子智能体，逐篇）
- 文本完整、可切句、唯一锚点充足（实测可用串：`EMBED_BATCH=32`、`2947cbf`、`147,127 字`、
  `CoocLayer`、`NeutralAgent` 等；均可支撑 rename/detail/passage/delete/append 五类题）。
- 两个共性问题与处置：① **自指污染**（语料是本基准自己的实验报告，正文含旧编辑对/答案键）→ 黑名单清洗（§7.3）；
  ② **超长原子**（D3 代码/英文块被 `split_sentences` 合成 3717 字）→ 新增 bench `--splitter line`。

### 7.3 自指清洗与 `--splitter`
- 黑名单（含则整行剔除）：`Q_rename/Q_detail/Q_append/Q_passage/Q_delete`、`check_before/check_after`、
  以及旧编辑对 token（`墨某`/`读也算越界`/`840 字节`/`210e489aead*`/`root:*:*`/`shadow readable`/…）。
  清洗后三篇自指 token 残留 **0**。
- **`docbench.py --splitter {sentence,line,md}`**（`arms._split_doc`；默认 `sentence`，**历史 ahi 结果不受影响**）：
  - `line` = 一行一原子（忽略空行）——用于代码/表格/英文密布的 md 语料；
  - 实测 D3 最长原子 **3717 → 475**、`>2000` 原子 **2 → 0**（D1 1293→600、D2 1565→231）。
- 全量 `pytest 303 绿`。

### 7.4 待办
1. 为 D1/D2/D3 各写 `build_scenario_<name>(doc)`（5 类编辑）与题库 JSON（反转/不变/远端 + 锚点校验）；
2. 跑 tree/flat/full × 三篇（D2/D3 长文档注意 full 成本）；
3. 质检给出的候选编辑/题可直接进 scenario（如 D1 `NeutralAgent→BaselineAgent`、D3 `EMBED_BATCH=32→64`）。

---

## 8. 项目文档语料的题库与预注册（2026-10-04）

> 承接 §7。为 D1/D2/D3 各出一套高质量题库 + 编辑脚本，含**覆盖率两段预注册**；
> 单源 spec → 自动校验 → 生成题库。全部 `gen_questions.py` 锚点校验通过、`pytest 303 绿`。

### 8.1 工具链（单源 + 自动校验）
- `build_corpus.py`：去标识化（架构名中性化：`水循环→分层记忆树`、`RAPTOR→递归聚簇树`、`tree_v2→分层树`；
  基础模型统一 `glm-4.7-flash`；黑名单剔自指答案键）+ 拼接三主题文档。
- `specs/<name>.json`：**单源**（doc + edits + questions + retell_checklist + prereg）。
- `gen_questions.py`：施加编辑得 new_doc，逐条校验锚点（反转 before/after 唯一、forbid 缺席；rename/detail 全局替换不要求 old 唯一），
  输出 `questions_<name>.json`（`_meta` 带回 prereg）。
- `docbench.py`：动态注册 `specs/*.json` → `--scenario <name>`；新增 `--splitter {sentence,line,md}`（§7.3）。

### 8.2 题库设计（每篇）
- **5~6 条编辑**：4~5 类 + **一条 `insert`「段内插入实验数据」**（`editstorm.insert_after`）。
- **10 道题**：4 反转（edit 驱动）+ 1 append/insert（novel，改后才有）+ 2 不变 + 3 远端。
- **复述 checklist 两段（预注册）**：
  - `require_record` = **被题库问到、且未被编辑**的点（不变/远端题目标）——检验 **warm 使用史巩固**；
  - `require_nonrecord` = **文档中存在但任何题都未问**的点——对照组；
  - 预注册：`inc_tree`（暖）对 record 覆盖 **~100%** 且显著高于 `inc_cold`/`reb`；nonrecord 各臂相近。
  - 由 `docscore.score_retell` 的 `coverage_record` / `coverage_nonrecord` 输出（Phase4 复述）。
- 自动自检：非 record 词**不出现在任何题的 q/require/forbid**；record 词**均被问到**。

### 8.3 本轮修掉的 harness bug（三处，影响正确性）
1. **`docscore.score_qa` 回退链 bug**：`check_after` 缺失时回退 `{}` → 不变/远端题在 Phase3 **无条件 pass**（虚高）。
   改为回退 `check_before`。
2. **`docbench --retr-budget/--budget` 被 config 静默覆盖**（§6.5）。
3. **`ATOM_CHAR_CAP` 未在 tree build 路径生效** → 新增 `--splitter line` 兜住超长原子（§7.3）。

### 8.4 三篇题库
`questions_{D1_design,D2_experiments,D3_platform}.json` + `specs/*.json`；已扩为**各 20 题**（8 反转 + 2 append + 4 不变 + 6 远端）+ 30 词复述清单（record 10 / nonrecord 10）；编辑 10~12 条。
运行：`python3 realtest/editbench/docbench.py --scenario D3_platform --doc realtest/editbench/out/corpus_proj/D3_platform.txt
--questions realtest/editbench/questions_D3_platform.json --splitter line --repeats 5 --answer-temp 0.7 --retr-budget 20 --judge`
（`--splitter line` 对项目文档语料；seed2/ahi 仍用默认 `sentence`）。

---

## 9. 锚点口径更正与公开依赖（2026-10-06）

### 9.1 C1/C2 题库锚点口径更正
C1（seed2）/C2（ahi）题库**早于** `gen_questions.py` 引入的「锚点唯一性校验」，其反转题锚点采用短片段/全局串，
仅按**出现性**判定（`before.require` 在原文、`after.require` 在新文、`after.forbid` 在新文缺席、两侧互斥），
**不满足**"旧值在原文唯一、新值在新文唯一"。实测反例：ahi `墨`×349、`双进程`×16；seed2 `何倚笙`×29。
旧稿（`REPORT.md`/`EXPERIMENT_REPORT.md`/`questions_ahi.json` 的 `_meta`）中"唯一"表述已更正。
C3–C5（D1/D2/D3）经 `gen_questions.py` 严格锚点唯一性校验，不受影响。

### 9.2 D1–D3 语料公开再生所需的输入文件
`build_corpus.py` 的 GROUPS 依赖以下**已跟踪**文档（若公开仓库裁剪目录，须至少保留这些文件，否则 D 语料不可再生）：

| 组 | 依赖文件 |
|---|---|
| D1_design | `AGENTS.md`、`realtest/USAGE_SPEC.md`、`ahi-bench/PERSONA_RETENTION.md`、`ahi-bench/SPEC.md` |
| D2_experiments | `realtest/editbench/{EXPERIMENT_REPORT,AHI_ACT_REVIEW,REPORT,INNOVATION_REVIEW}.md`、`realtest/chatbench/{EXPERIMENT_REPORT,PLAN,COMPARE_REPORT}.md`、`ahi-bench/PR5_REPORT.md`、`ahi-multi/experiments/**`（EXPERIMENTS / ABSENCE_EXPERIMENT / ARCHIVE_CHAT / analysis/ANALYSIS_COLD_HOT / analysis/ANALYSIS_COLD_1H） |
| D3_platform | `README.md`、`HANDOFF.md`、`ahi-bench/README.md`、`ahi-multi/{HANDOFF,README,CHANGES,CLAUDE,next_exp_insts}.md` |

（`build_corpus.py` 有自指护栏：含基准设计标记的源文件会被拒绝入语料；本文件不进入 GROUPS。）
