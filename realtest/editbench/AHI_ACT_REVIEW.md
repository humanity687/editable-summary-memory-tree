# ahi-act 主实验 8 臂就绪审查（2026-10-02）

> 范围：`run_ahi.sh` + `docbench.py` + `arms.py` + `editstorm.build_scenario_ahi` +
> `questions_ahi.json` 的**主实验 8 臂**就绪度、公平性、以及其 8×5 结果。
> 设计权威 = 根 `AGENTS.md`；seed2 结果口径 = `REPORT.md`；历史 = `HANDOFF.md §4.13/§4.14`。
> 本文档不含 seed2（seed2 只在 `REPORT.md`）；不新增机制。

---

## 1. 主实验 8 臂定义

四结构 × 两更新策略（`docbench.py` Phase 3）：

```
inc_{full, flat, raptor, tree_v2}   # 只保留编辑台账（append-only 或真实编辑路径）
reb_{full, flat, raptor, tree_v2}   # 从终稿 new_doc 无状态重建（上界参考）
```

- 结构：`full`（全文，无预算）/ `flat`（400 字 chunk + bge 向量 top-12）/
  `raptor`（递归摘要 + collapsed top-12）/ `tree_v2`（水循环树，真实编辑路径）。
- 支持臂（不属主 8，但同一次 `docbench` 运行会产出）：`inc_cold_tree_v2`（无 Phase-1 使用史 + 同一编辑，
  用于分离「原地失效」与「使用史连续性」两因子）。
- 主表须至少同时报 8 臂 + `inc_cold_tree_v2`；A/B 障碍臂（`--edit-signal-ab`）是**独立支持实验**。

---

## 2. 就绪矩阵

| 项目 | 状态 | 证据 |
|---|---|---|
| 文档 `ahi_script.txt`（147,127 字） | ✅ 已入库 | `git ls-files` 命中 |
| 题库 `questions_ahi.json`（41 题 = 17 反转 / 8 不变 / 16 远端） | ✅ | `_meta` + 实测计数 |
| 反转题锚点（before 在原文 / after 在新文 / after forbid 不在新文） | ✅ 复核一致 | 逐条脚本核验（见 §6） |
| 编辑脚本 `build_scenario_ahi`（11 编辑，5 类） | ✅ | 实测 `len(ledger)=11` |
| 事实/名字 `ahi_facts.md` / `ahi_names.txt` | ✅ 已入库 | git ls-files |
| 四结构臂代码（`arms.py`） | ✅ | `STRUCTURES` 全实现 |
| 向量模型（flat/raptor 依赖） | ✅ 本地可用 | `_load_encoder()` → `bge-small-zh-v1.5` |
| 单测基线 | ✅ 267 全绿 | `pytest tests/ -q` |
| 逐臂 API key | ⚠️ 缺 2 个 | `keys.local.json` 无 `inc_obstacle_tree_v2` / `inc_cold_obstacle_tree_v2` |

**已跑产物**

| 臂 | 状态 | 产物 |
|---|---|---|
| 主 8 臂 + `inc_cold`（matched-pair，N=5；**旧占位**） | ✅ 已完成（2026-10-02，80 min） | `out/ahi_r5/results.json` |
| 占位透明后**树臂重跑**（复用旧树，N=5） | ✅ | `out/ahi_tree_replay_ph/results.json` |
| 统一口径合并（旧 full/flat/raptor + 新 tree） | ✅ | `out/ahi_r5_combined/results.json` |
| A/B（ahi，旧占位，N=5） | ✅ | `out/ahi_ab_editsignal/results.json` |
| （旧）inc_full / reb_full N=1 | 存档 | `out/ahi_full_r1` |
| （旧）inc/inc_cold/reb tree N=1 | 存档 | `out/ahi_tree_replay_b16` |

→ 结论：**就绪且已跑完**；结果见 §5。

---

## 3. `run_ahi.sh` 参数审查（发现 4 处不一致，已修复）

| # | 问题 | 原值 | 规范值（依据） | 处置 |
|---|---|---|---|---|
| 1 | `--tree-budget` 与主表口径不一致 | `7000` | `16000`（`AHI_ACT_REVIEW §5`、`replay_trees.py` 默认、旧实跑） | 改 16000 |
| 2 | 未传 `--retr-budget`，回落到 config 的 6 | 缺省=6 | `20`（`HANDOFF §4.14` 最佳配置、`replay_trees.py` 默认） | 增 `--retr-budget 20` |
| 3 | `--edit-signal-ab` 会构造 2 个 A/B 障碍臂，且无对应 key | 开启 | A/B 是独立支持实验（`EXPERIMENT_REPORT §5.3` 待办），不属主 8 | 从主 8 运行中移除，另起独立轮 |
| 4 | flat/raptor 仅 top-12（≈4.8k 字符 ≈ 1/30 原文），与 tree 16k（≈1/9）不同档 | topk=12 | 与 tree 同档：~1/8~1/9 原文（16k 字符） | flat/raptor 支持**字符预算**（`assembly_budget` 封顶）；`--raptor-topk` 新增；run 设 `--flat-topk 60 --raptor-topk 60`（候选池，实由 16k 封顶） |

其余参数核对通过：`--repeats 5 --answer-temp 0.7 --judge --scenario ahi` 与 `REPORT.md` 口径一致；
`--model` 走 config `deepseek-v4-flash`；`--leaf-expand` 关闭（叶子展开已是负结果，正确）。

> **⚠ 更正（2026-10-04）**：上表 #2 的修复**实际未生效**。`docbench.py` 原写
> `retr_budget = int(cfg.get("retrieval_budget", args.retr_budget))`——`config.json` 的 `retrieval_budget:6`
> 恒优先于 CLI，故 `run_ahi.sh` 的 `--retr-budget 20` 从未生效（`ahi_r5`/`ahi_r5_par` 实跑 **retr=6**）；
> 而 `replay_trees.py` 直接用 CLI（**retr=20**）。已修为 CLI 显式优先（见 `EXPERIMENT_REPORT.md §6.5`）。
> 直接影响 §5.6 的归因，见下方更正块。

> 预算对齐实现（2026-10-02）：`FlatArm`/`RaptorArm` 新增 `budget` 参数（0=旧行为不封顶），
> `context()` 把检索结果裁到 `budget` 字符；`make_arm` 把结构预算传给 flat/raptor。
> seed2 不受影响（doc 12k，topk 12 检索量 < 预算，不绑定）；仅 ahi（147k）在 topk=60 下达到 ~16k。

---

## 4. 公平性审查

| 维度 | 结论 | 说明 |
|---|---|---|
| 编辑期输入格式 | ✅ 公平 | 所有 append-only 臂（full/flat/raptor）用逐次 function-calling 块（`editstorm.tool_block_units`）；tree 走真实编辑路径。见 `EXPERIMENT_REPORT §3.9` |
| 删除语义 | ✅ 公平 | tree 只删目标子串、保留残句（整卡误删 bug 已修）；append 臂旧内容保留 = 其「腐烂」定义本身 |
| 温度 | ✅ 一致 | 作答 0.7 / 蒸馏 0 / judge 0，两路 client 分离 |
| 重复测量独立性 | ✅ | Phase2 结束点快照，每遍作答前 `restore`（有状态臂）；无状态臂天然独立 |
| 判分 | ✅ | 确定性初筛 → 仅失败样本过 judge 复核；`docscore.check_spec` 句内匹配 |
| 使用史混杂 | ⚠️ 须披露 | inc 臂带 Phase-1 使用史，reb 臂无 → 主表**必须并列 `inc_cold_tree_v2`**，否则 inc vs reb 混两因子 |
| 预算对齐 | ✅ 已对齐（2026-10-02） | full = 全文 147k 无预算（上界锚）；flat/raptor/tree 均受 16k 字符（≈1/9 原文）约束。仍须**每臂同报 token/字**以锚定成本主张 |
| 检索深度 | ✅ | flat/raptor topk 60（候选池）+ 16k 字符封顶；tree retr 20（修复后） |
| 复述 checklist | ⚠️ 待核 | 10 require / 10 forbid；需人工确认覆盖 append（终章）与 3 处 delete |
| 摘要细节丢失 | ⚠️ 既知 | tree 全局视图仅数百字，细节靠检索；`retr 6→20` 是已定方向，现与修复后脚本一致 |

**成本（实际，N=5）**：作答路径 `inc_full` 40.43M prompt vs `inc_tree` 2.90M（≈1/14）；
维护侧 tree 蒸馏 completion 174k ≈ 4× full。详见 §5。

---

## 5. 主实验 8×5 结果（2026-10-02，已完成）

> 一次 `docbench` matched-pair 运行，总耗时 4805.6s（80 min）；N=5、temp 0.7；
> 产物 `out/ahi_r5/results.json`。口径：总 205 = 41 题 × 5 遍；反转 85 = 17 题 × 5 遍。

### 5.1 Phase 1 四结构基线（原始文档）

| 结构 | 总 | 反转 | 不变 | 远端 | 每遍 |
|---|---|---|---|---|---|
| full | 196/205 (96%) | 78/85 | 40/40 | 78/80 | [39,39,39,41,38] |
| flat | 183/205 (89%) | 68/85 | 40/40 | 75/80 | [37,36,37,37,36] |
| raptor | 182/205 (89%) | 72/85 | 40/40 | 70/80 | [38,36,37,36,35] |
| tree_v2 | 157/205 (77%) | 60/85 | 30/40 | 67/80 | [32,32,29,32,32] |

### 5.2 Phase 3 八臂 + inc_cold

| 臂 | 总 | 反转 | 不变 | 远端 | 复述 覆盖/残留 | 作答 prompt | 蒸馏 completion |
|---|---|---|---|---|---|---|---|
| inc_full | 179 (87%) | 70/85 | 37/40 | 72/80 | 24% / **12%** | 40.43M | 43.6k |
| reb_full | **195 (95%)** | 76/85 | 40/40 | 79/80 | 58% / 0% | 19.22M | 21.3k |
| inc_flat | 180 (88%) | 65/85 | 40/40 | 75/80 | 26% / **12%** | 3.94M | 15.2k |
| reb_flat | 175 (85%) | 61/85 | 39/40 | 75/80 | 26% / 0% | 2.05M | 10.1k |
| inc_raptor | 179 (87%) | 68/85 | 39/40 | 72/80 | 18% / **6%** | 4.16M | 29.5k |
| reb_raptor | 180 (88%) | 67/85 | 40/40 | 73/80 | 22% / 0% | 2.14M | 23.4k |
| inc_tree | 156 (76%) | **52/85** | 34/40 | 70/80 | **80% / 0%** | 2.90M | **174.4k** |
| reb_tree | 155 (76%) | 62/85 | 25/40 | 68/80 | 30% / 0% | 1.06M | 109.0k |
| inc_cold_tree | 135 (66%) | 45/85 | 25/40 | 65/80 | 20% / 0% | 0.98M | 174.9k |

（prompt/completion = `final` 累计，含 Phase 1；cache 命中率 full 0.998 / tree 0.66–0.76。）

### 5.3 判读

1. **复述轴复现且更强**：`inc_tree 80%/0%` 仍是唯一「高覆盖 + 零旧残留」；所有 append-only 增量臂
   残留 6–12%（`inc_full` 把 `读不算越界`、`inc_flat` 把 `210e489aead9`/`单进程` 当当前事实）。
   seed2 的主结果在大数据上成立。
2. **反转轴不追平**：ahi 上 `inc_tree 52/85`（61%）明显低于 append 增量（65–70）与 `reb_tree 62/85`。
   → 此前口径与外部创新报告的告诫经 **N=5 matched-pair** 证实：**「tree 增量追平 rebuild/full」只在 seed2 成立，
   不可外推**。tree 在 ahi 的 Q/A 劣势源于检索/摘要预算（见 §4「摘要细节丢失」）。
   ⚠️ **后续修正（2026-10-04；见 §3 更正块、§5.6 更正块、`EXPERIMENT_REPORT.md §6.5`）**：
   原 §5.6 用「新代码 + retr20」对比「旧代码 + retr6」，把 **retr 6→20 的收益误记为占位透明**。
   当天同代码对照（retr20）：`inc_tree`（no-think）**182/205、反转 84/85**、复述 64%/0%——tree 增量在
   反转上确实不低于 append 增量与重建；但这是「retr20 + 当前代码」的合并结果，**不能整段归因于占位透明**。
   权威口径以 2026-10-04 同代码基线（`out/ahi_strong/*`）为准。
3. **使用史是 tree 复述的命门**：`inc_tree 80%` vs `inc_cold_tree 20%`（同编辑、无使用史），
   隔离出「使用史连续性」对全局视图的巨大贡献。
4. **成本须二分**：作答路径 prompt 上 `inc_tree 2.90M` 仅 `inc_full 40.43M` 的 ≈1/14（只算 Phase3 增量 ≈1/12）；
   但 tree 蒸馏 completion `174k` 是 `inc_full 44k` 的 ≈4×，且 `settle` 墙钟 371s + 402s（≈13 min）。
   → **「~1/10 上下文成本」只对作答路径输入成立，维护成本反向**。
5. **tree 编辑时刻仍零 LLM**：编辑路径 6.6 ms（warm）/ 7.6 ms（cold），蒸馏全部推迟到 settle。

### 5.4 结论（据实收窄）

- 可主张：**原地失效使 tree 增量在全局复述上零旧残留（80%/0%），append-only 无法做到（残留 6–12%）**。
- **可主张（2026-10-04 同代码口径）**：tree 增量在反转上不输 append/rebuild——
  no-think tree inc 反转 **84/85** ≥ full inc 反转 79/85（且 full 开思考才到 79）；tree 增量独特优势在复述零残留。
  （旧稿「52/85 < 62/85」的反例与「70/85 追平」均建于 retr 混淆之上，作废。）
- 成本主张须限定在**作答路径输入**（≈1/12–1/14 of full），并披露**蒸馏维护成本反向**。

### 5.5 编辑效果 bonus 与复述语义对比（2026-10-02）

#### A. 渲染为什么会出现占位卡（warm `inc_final` 树）

- **机制**：`core._rebuild` 重建节点时，若其存活子卡中存在"热"卡（HP ≥ `hot_threshold`），
  则节点置 `value=None`、`placeholder=1`（`core.py:448-455`）；渲染对 `value is None` 的节点输出
  `【占位：近期修改，概要暂缺】`（`realengine.py:1167-1171`），**且带节点号**——故复述里出现
  `N4225 / N4205`。
- **解除条件**：该节点**全部存活子卡 HP < `hot_threshold`**（`_cooled`，`core.py:432-439`）。
- **为何本轮仍有占位**：① Phase-1/QA 的 `recall_boost` 让部分后代 HP 回升到热区，占位父卡迟迟不冷却；
  ② `context()/retell_context()` 用 `assemble(allow_merge=False)`（§4.5.7 mid-loop 不 merge），
  评测期不再重建 → 占位卡保留并进入 retell 上下文。
- **定性**：属**设计内行为**（§4 已列"warm 仍有占位卡"），但会给复述带噪声——A 的复述里出现
  `（内容未完）` 与占位节点号，是模型对**占位/截断上下文**的转述，非编造。

#### B. bonus（当前 warm `inc_final` 树）

- **原子层**：11 处编辑旧值**全 0**、新值到位——失效机制正确。
- **全局视图（无 query，3328 字）**：所有旧值泄漏 **0**。
- **摘要层**：唯一"残留"= rename 的 **3 个节点含孤立"墨"**（已核验为 LLM 摘要**缩写/截断**的生成副作用，
  同段大量 `墨某`，非失效遗漏——与 seed2"单进程自我调和"同类）。
- **重写段连贯性**：自动侧 `新文档残留旧断言=0`；`--judge` 对当前唯一断言段判"**否**"，
  原因是**编译剧本的线程/说话人不衔接**（该段是"墨对实验员汇报"，夹在"蓝→墨某分工"与"蓝对实验员"之间），
  并非新文档事实矛盾——±200 字局部窗口对多角色剧本不适用。

#### C. 复述语义对比：`inc_tree_v2`(A) vs `reb_full`(B)

| | A `inc_tree_v2` | B `reb_full` |
|---|---|---|
| 长度 | 2625 字 | 12308 字（**末句断在第三幕，被截断**） |
| checklist 覆盖 | **8/10**（缺 `210e489aead0`、`840 字节`） | 7/10（缺 `840 字节`、`root:*:99999`、终章金句） |
| 旧值残留 | 0/10 | 0/10（精确串） |
| 幻觉 | 无实质幻觉 | 无实质幻觉；诗题/轮次/技术细节经逐项核实全对 |
| 主要缺陷 | 压缩 + 占位噪声（`内容未完`、`N4225/N4205`） | **输出截断**（未到终章）；`root:*:99999` 被压缩省略 |

- 结论：A **短而全、带机制噪声**；B **详而断、覆盖不均**；两者互补，**均无旧值冒充新值**。
- B 漏 `root:*:99999`/`840 字节` = **压缩省略**；漏终章金句 = **截断未达**（两者机制不同，勿混）。
- ⚠️ 唯一语义级残留只在 B：`进程单`×2 / `进程这面是单的`——**忠实于被编辑文档自身**（见 E.1），
  确定性判分不捕获（禁项是精确串"单进程"）。

#### D. 原文位置（编辑前底本 `ahi_script.txt`，共 2359 行）

| require（新值） | 原文旧值 | 行号 |
|---|---|---|
| 墨某 | 墨 | 人物表 **L8**；全文 **331 行**含"墨" |
| 读也算越界 | 读不算越界 | **L837,923,929,969,983,1027,1028,1033,1041,1066,1287,1614,1621,1924,1971**（15） |
| 210e489aead0 | 210e489aead9 | **L684, L938** |
| 双进程 | 单进程 | **L1645,1656,1665,1671,1680,1812,1836,1846,1968,1977,1985,2040,2049,2065,2072,2120**（16） |
| 840 字节 | 839 字节 | **L332**（唯一） |
| 3000 字 | 2000 字 | **L1504,1507,1513,1575,1577,1582,1647,1656,1666,1733,1746,1837,1869,1975**（14） |
| 7 个路径 | 6 个路径 | **L1606**（唯一） |
| root:*:99999 | root:*:20689 | **L1612**（唯一） |
| /etc/motd | — | **原文无，编辑新增**（`editstorm` new_passage） |
| 凡是能跑一遍的，不猜 | — | **原文无，编辑新增**（`_EPILOGUE`；同段 `都结清了`/`下一轮见`） |

forbid 唯一定位：`shadow readable` **L2059**；`这就是我的身体` **L2121**；`咱们仨独立验证过了` **L39**。

#### E. bench 发现（待办，勿擅改 ground-truth）

1. **`单进程→双进程` 语义不彻底 → 新文自相矛盾**：该 detail 只替换精确串（doc 16→new 0），
   但原文另有 `进程单`(new=1)、`进程：单`(new=1)、`进程这面是单的`(new=2) 未被替换 → `new_doc`
   同时含 16 个"双进程"与 4 个单进程表述。B 如实照抄故矛盾，A 直接抹平。
   **修法 = 扩为语义完整替换**（并处理 `进程单`/`进程：单`/`这面是单的`）——但这是 **ground-truth 变更**，
   须 **升 scenario 版本 + 重跑** ahi_r5 与 A/B；本轮**未改**以保持数据-环境同步。
2. **retell LLM judge 不可靠**：`results.json` 的 `reb_full.judge_hit`(=10/10) 与 `judge_rot`(7 个假阳性)、
   `inc_tree_v2.judge_rot` 均与文本实况矛盾。**复述残留以确定性 `old_leak`/`new_miss` 为准**，judge 仅参考。

### 5.6 占位透明收益（2026-10-02）——ahi 树臂重跑（复用旧树）

- 背景：§A 的占位污染——`value=None`（placeholder 或未生成摘要）节点被渲染成"【占位】"，
  且占位卡参与 boost/注意力/merge。修法 = `placeholder_transparent`（AGENTS §4.6，默认 on）：
  渲染递归子卡、机制跳过占位卡。
- 方法：**复用 `ahi_r5/trees` 的树快照**（`replay_trees.py`，不重建/不蒸馏），只换新代码重跑作答+复述，N=5。

| 臂 | Q/A 旧→新 | 反转 旧→新 | 复述覆盖 旧→新 |
|---|---|---|---|
| base(tree) | 157→**174** (77→85%) | 60→**67**/85 | — |
| inc_tree_v2 | 156→**174** (76→85%) | 52→**70**/85 | 80%→70%* |
| inc_cold_tree_v2 | 135→**149** (66→73%) | 45→49/85 | 20%→20% |
| reb_tree_v2 | 155→**177** (76→86%) | 62→68/85 | 30%→30% |

\* 新复述逐遍 `[0.7, 0.4, 0.8, 0.8, 0.8]`（旧 `[0.8]×5`）：下降来自**一遍 0.4 离群**，非系统回退
（末遍漏项与旧版一致，残留均 0）。

- **结论（⚠ 2026-10-04 更正，下调置信）**：本节把「占位透明」与「retr 6→20」**混在一起**了。
  `replay_trees.py` 用 CLI `--retr-budget 20`，而 `docbench.py` 当时被 config 的 `retrieval_budget:6` 覆盖
  （见 §3 更正块）——即本节新旧对比同时改了两个变量。同代码 base 树 2×2 拆分（`EXPERIMENT_REPORT.md §6.5`）：
  **占位透明 ≈ +1、retr 6→20 ≈ +13**。故此处的 **+7~11 点绝大部分应归因于 retr 预算**，
  而不是占位透明；`inc_tree 52→70` 同理不能整段归给透明。占位透明的净增量须用「新代码 + 同 retr」重跑才能定。
  （透明的**机制**收益——不渲染占位文本、递归展示子卡——仍然成立；但**幅度**远小于本节旧述。）
- 统一口径产物：`out/ahi_r5_combined/results.json`（full/flat/raptor 复用旧 `ahi_r5`，tree 用新；
  占位只影响 tree，两轴互不影响）。**注意**：该 combined 里 tree 用 retr20、full/flat/raptor 旧跑，
  跨条件预算不一致，正式引用须用 2026-10-04 同代码基线（`out/ahi_strong/baseline`）。

### 5.7 A/B（ahi，编辑作使用信号；旧占位行为）

> `ab_edit_signal.py`，N=5，4019.9s；**同一 pre-edit 树**分别施加热/冷编辑新原子。

| 臂 | Q/A | 反转 | 复述覆盖/残留 |
|---|---|---|---|
| warm_signal（热） | 163 | 67/85 | **64% / 0%** |
| warm_obstacle（冷） | **175** | 70/85 | 44% / 0% |
| cold_signal（热） | 166 | 58/85 | 20% / 0% |
| cold_obstacle（冷） | 168 | 61/85 | 30% / 0% |
| reb | 183 | 77/85 | 32% / 0% |

- 复述：**暖树 signal 更好**（64>44，与 seed2 一致），但**冷树 signal 反而更差**（20<30）→ 非一致。
- Q/A：两树上 **obstacle（冷编辑）均略好**（175>163、168>166）。
- **结论**：ahi 上"编辑作使用信号"**只在暖树复述上复现，不构成全胜**；与 `REPORT §3`/`INNOVATION_REVIEW §3`
  的"热冷权衡"限定一致。注意本 A/B 用**旧占位行为**，同口径需用新代码重跑（tree-only，成本 ~1h）。

---

## 6. 逐条锚点核验结果（2026-10-02 脚本复核）

对 17 道反转题逐条断言：
`check_before.require ∈ doc`、`check_after.require ∈ new_doc`、`check_after.forbid ∉ new_doc`，
两侧互斥。**全部通过**（例：`2000 字` doc×15 → new×0；`3000 字` doc×1 → new×16；
`墨某` doc×0 → new×351；`/etc/motd` doc×0 → new×1）。与 `_meta` 声称的「bad=0」一致。

---

## 7. 执行方式（主 8 臂；已执行）

**历史（串行单进程，产出 `out/ahi_r5`；旧占位行为）**：
```bash
python3 realtest/editbench/docbench.py \
  --scenario ahi \
  --doc realtest/editbench/ahi_script.txt \
  --questions realtest/editbench/questions_ahi.json \
  --keys realtest/editbench/keys.local.json \
  --out realtest/editbench/out/ahi_r5 \
  --repeats 5 --answer-temp 0.7 \
  --tree-budget 16000 --retr-budget 20 \
  --flat-topk 60 --raptor-topk 60 \
  --judge
```
产出：`inc_{full,flat,raptor,tree_v2}` + `reb_{full,flat,raptor,tree_v2}` + `inc_cold_tree_v2`
（共 9 臂）+ Phase4 复述。

**现行（方案 B，四结构并发 + 合并，2026-10-02）**：
```bash
bash realtest/editbench/run_ahi.sh     # → out/ahi_r5_par/{full,flat,raptor,tree_v2}/ + results.json
bash realtest/editbench/run_seed2.sh   # → out/seed2_r3_par/
```
- 四结构互不共享状态（各自 base/inc/reb/cold，跨结构无耦合）→ 拆 4 进程并发（~4×），
  `run_parallel.sh` 编排、`merge_results.py` 合并；**等价于单进程全臂、不改 bench 逻辑**。
- 输出到新目录（`ahi_r5_par` / `seed2_r3_par`）以保留串行旧存档，避免覆盖。
- `merge_results.py` 亦可 `--inputs` 合并任意多份 `results.json`。

---

## 8. 开跑检查清单（运行后回填）

- [x] 四结构代码可构造、encoder 可用
- [x] 题库锚点复核通过（17 反转）
- [x] 单测 267 全绿
- [x] `run_ahi.sh` 参数与设计对齐（tree 16k / retr 20 / 无 A-B flag / flat·raptor topk 60 + 16k 封顶）
- [x] `keys.local.json` 9 个主臂 tag 齐（含 `inc_cold_tree_v2`）
- [x] `--out` 目录干净（`ahi_r5` 新建）
- [x] 运行完成（4805.6s）→ `out/ahi_r5/results.json`
- [x] 跑完核对：`inc_full 70/85 < reb_full 76/85`（反转轴分层，append 块确进上下文）
- [x] `inc_tree` 复述 80%/0%（唯一零旧残留）；`inc_cold_tree` 20% 隔离使用史因子
- [ ] 复述 checklist 覆盖 append/delete 人工确认（待人工）
- [ ] API 成本对账（full 作答路径 40.43M prompt，cache 命中 0.998）

## 9. 后续（不在本次就绪范围）

- ahi A/B 障碍臂：补齐 `inc_obstacle_tree_v2` / `inc_cold_obstacle_tree_v2` key 后单独跑。
- ahi N≥3 复验（本轮 N=5 已完成；如需跨 run 多文档见下）。
- 多文档 / STALE-MemoryAgentBench 对接：见 `INNOVATION_REVIEW.md §4`。
