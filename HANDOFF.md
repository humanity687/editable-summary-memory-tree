# 交接文档：水循环 v2 · 参考资料上下文管理运行时

> 编写：2026-08-12 · 更新：2026-10-04（**Q/A bench 韧性臂：强模型答案侧思考 / 强向量 bge-large
> → 召回靠模型、编辑一致性靠结构；并更正 retr-budget 混淆**——§4.16 / §16；
> 人格保持 M-PR 首轮 tree/flat 对比 + `style_alpha` 中性消融 → flat 两主指标胜；
> 趋同根因 = 树管线/跨 agent 耦合；flat 观测埋点；`self_quota` 组装层实验操控（默认关）——详见 §15；
> 前次：2026-10-03 tree 锚点检索化 `AGENTS.md §4.4` / `ahi-bench/PERSONA_RETENTION.md v0.3`）
> 状态：理论设计 → 桩实现 → 压力测试 → 真实 LLM 实测（小说写作实例完成两轮实验），
> 聊天智能体阶段：首个 1 小时三 agent 对照实验（exp1）→ 四子智能体分析 + 真人/AI 盲测，
> → Phase A 第二轮实验（染色 v2 + 并行蒸馏验证）完成并深度分析 → Phase B 染色 v3（指针密度）
> 代码全部实施 → 缺席实验（冷/热两轮）完成：皇冠预言方向成立 + merge 触发机制升级
> → **最新树接入 realtest bench（seed2 + gemma4:26b）→ 共现中介层（概括节点）实现 + NovelQA 四层 bench
> → chatbench：真实聊天记录的记忆基准（QA / 生成分析报告 / 报告验证三层）+ 树持久化
> → 四臂对比（记忆树 / 树+共现 / RAPTOR / 全量发送）——共现层暂无可测增量，四臂待按修正协议重跑（§4.6/§4.7）
> → **ahi-bench M0+语料quiz 正式冷轮（无 cooc 对照组）：渲染分层（原子=干净对话）/
> 组装 per 工具调用（§2 对齐）+ 连续唤醒（loop 重排 pending）/ 任务行事实陈述（平台中立）/
> 蒸馏来源前缀 / 树冷加载 QA 三树×两臂（§4.8）**
> → **v3 使用信号层：Phase A 实证（输出信号有料/recall 边冗余）→ Phase B 影子模式落地 →
> Phase C 首轮判别负结果 → 主题层协同实现与实验（负结果）→ ahi-bench 复现冷轮 +
> 场景评估（本场景非适用域，挂载休眠为正确配置）（§4.9~§4.11）**
> → **正式实验前代码审查 + 收口（2026-09-27，§4.12）→ M-PR 多轮实现/对齐/首轮对比（§13~§15）**。
> `tests/` **303 全绿**（2026-10-04）。
> 设计权威 = `AGENTS.md`（本文档只做交接说明，不替代设计文档；设计变更仍需用户确认）。

---

## 0. 一句话定位

把一份**长参考资料**变成"位置有序、分辨率可变、可编辑演化"的记忆树，让 LLM 在有限上下文里稳定工作于任意规模的材料之上。长篇小说写作是第一个应用实例；机制通用（章/beat/伏笔 → 结构单元/内容块/跨区引用）。

**铁律三条**：① 偏离设计必须经用户确认（改文档+代码）；② 组装 = 纯计算零 LLM，LLM 只出现在蒸馏/重建/校验等标注位置；③ 参数先拍后调，基准校准后写回台账（§11）。

**Git 分支**：`main`（小说写作实例存档）→ `chat`（聊天智能体测试，本阶段工作分支）。

**⚠ 聊天实验平台（AHI 多智能体）交接入口**：`ahi-multi/HANDOFF.md`——平台现状/实验 1 结论与评分/本轮迭代（并行蒸馏+染色 v2）/下一步实验计划（A 验证→B 情感卡→C 对抗压力→D 三臂移除）/操作手册/数据归档。**接手聊天实验请先读它**；本文档以下章节为本项目的机制层交接。

---

## 1. 已完成工作时间线

| 阶段 | 产出 | 关键结论 |
|---|---|---|
| 设计收敛 | AGENTS.md（设计权威）| 三轨架构（参考流/笔记本/工作记录）+ 区间覆盖树 + 懒失效 + 召回 |
| 数据结构桩 | `core.py` + 测试 | 机制全部验证：链/树/编辑/merge/组装/检索/回滚/影子/提升 |
| 可视化 | `visualize.html` | 树/链/组装三视图 + 18 步演示剧本（对外汇报用）|
| 规模压力测试 | `stress_write.py` + `results/` | 30 万句下铺陈恒定、树高亚对数、正确性不崩 |
| 真实 LLM 实测 v1 | `realtest/run.py`（无尽续写，已弃用）| 预算生效；暴露"轨道B 缺失 → 状态漂移"（设计预言的复现）|
| 结构化压测 | `realtest/bench_qa.py` | 缺陷 A/B/C；修复 B（混合检索）后 1a 命中 3.8× |
| 复述对比 | bench Phase 1.5/2.5 | 编辑风暴碎片化的量化证据 |
| 冷却窗口 | bench_qa 修复 | 风暴后冷却窗口 → 复述 after 1.5% → 30.8%（新数据集）|
| **无尽续写 v2** | `realtest/run2.py` | 新协议 [plan]/[span]/[note]：写前预想 → 目标驱动回想 → 状态笔记本 |
| **注意力窗口** | core.py `tick()`（§4.3b）| 不对称衰减（窗内慢/窗外快）+ cap 排名截断 → hard_trim 归零 |
| **裁剪收紧（2026-09-27 拍定）** | realengine.py `_trim_chars` | 超预算 ①低 HP 节点/影子 → ②旧原子（低 HP 优先，**本轮新原子活一轮**）→ ③溢出 > `budget*(1+hard_trim_slack=0.1)` 才硬截断（头保留尾砍）；`trim_atoms` 事件入日志；裁剪只动视野不动树 |
| **heat burst 自动下钻披露（2026-09-27 重设计，默认关）** | core.py 回血溢出钩子 + realengine `_auto_expand`/`_accumulate_heat`/`tick` | 信号 = 回血封顶**溢出量**（节点全额 + 直接父半权、同轮防重复计账；原子只计数）；热量 ≥ 阈值(60) 且预算富余 → 视野内该节点**炸开**（全装得下替换节点项=覆盖不丢；否则追加）；`tick` 衰减、不消耗；脏/墓碑子卡不入且阻断替换；测试 `tests/test_heat_burst.py` 12 项。**flag 默认关**——论文 tree_v2 臂冻结、全臂 off；首秀 ahi-bench ablation 臂 |
| **force 合并** | core.py `merge_pass(force_out)` | 窗外原子超压即合（纯原子段）→ 上下文峰值 12k→7.8k |
| **叶子展开** | realengine `expand_leaves` | 命中概括卡 → 全部后代原子活一轮影子 |
| 万字数据集 | `realtest/seed2/` | 《缠枝莲》12k 字 + 50 条事实（校验全过）|
| 架构审查 | 本轮 | 通用性/复杂度/副作用三项自查（结论见 §5 注意事项）|
| **聊天适配** | `chat_agent.py` + `chattext.py`（chat 分支）| 多介质切分（chat句/code AST/config项/md标题+列表项）+ 来源元数据（检索不可见）+ 代码检索走 docstring + 大卡 weight 计数 + 重复输入影子 + 原子上限 |
| **DISMISS 懒标记** | core/realengine `dismiss_*` | 事实冲突延迟裁决：挂起（零破坏）→ 每轮追踪 +〔待失效〕标注 → [dismiss #id] 墓碑确认（可回滚）/ cancel |
| **file_edit** | `chat_agent.file_edit` | 文件编辑卡级 diff 精确失效（读过才 diff，没读过直接灌入）|
| **去重发送** | realengine `nb_render` | 轨道B 注入 = 每 key 最新 + 每轮现场生成 + 接卡片流后；worklog 恢复注入（固定 key 覆盖）|
| **缺席实验平台** | main.py + message_router + absence_driver | 隔离开关（alone/halt）+ 积压限流 + 回归预算改造；冷/热两轮完成 |
| **merge 条件式触发** | core.py `merge_pressure()` | 窗外节点+原子数 > 参数 → 每轮查就蒸（替代时间式 merge_every）；峰值 215→143 |
| **节点窗口扩展** | core.py `tick()` | 节点段成员 = 全部存活节点（树内/影子与铺陈平等参与）|
| **代码场景搭壳+校准** | `realtest/codeagent/` | 三场景·代码：同壳对比（基线 vs 记忆树）；**设计坑在壳**——原生工具对话缺失致模型表现极笨，重构后基线能力回升；**产物尚有分歧**（`summary.json` n=2/pass=1 vs `out_official_baseline` 五项 PASS），需统一产物后再定论 + 加跨会话任务 |
| **最新树接 bench** | `realtest/bench_qa.py --cooc` | 对齐 ahi-bench engine 块（原子/节点窗口、条件 merge、max_hp）+ 本地 gemma4:26b 跑 seed2；树健康（H=5）但共现层在粗 tick 下不激活 |
| **共现中介层** | `realtest/cooc.py` + `cooc_bench.py` | 概括节点只做匹配中介、不进组装流；NovelQA 四层题 (b)（转述）off/on 无增量；教训：定向 warmup 属 oracle 作弊，已撤 |
| **chatbench** | `realtest/chatbench/` | 真实聊天记录记忆基准：Phase0/W/T 回放 + msg_id 引用 + 双通道/claim 分解/报告对照评分；多会话 QA + 生成分析报告 + 报告验证；共现层三轮无稳定增量（§4.6） |
| **树持久化** | `chatbench/tree_io.py` | gzip 树缓存；加载复用使 on 从 499s→140s；旧树可逐句映射回 msg_id（9553/9553 命中） |
| **四臂对比** | `chatbench/compare.py` | 记忆树 / 树+共现 / RAPTOR / 全量发送，统一 msg_id 引用协议；发现并修复基座误用与两处协议 bug，待重跑（§4.7） |
| **ahi-bench M0+quiz** | `ahi-bench/`（config/m0_corpus.json v0.2）+ `qa.py`/`cooc_analysis.py`/`tree_qa.py` | 沙箱语料（seed2 12k 字）+ 延迟 quiz（每 agent 12 题，同轮题不同防互抄）；正式冷轮跑通（110 轮+遗言，gates 全绿）；树冷加载 QA 三树×base/recall 两臂 + 分析题；离线共现三层分析 |
| **机制迭代（本轮拍定）** | `realengine.py` + `lan/neutral/agent.py` | 渲染分层（clean：原子=干净对话、段首来源，卡片层保留 §4.5 标注）；组装 per 工具调用（AGENTS.md §2 对齐，mid-loop 不 merge）；连续唤醒（loop 每次工具调用前重排 pending）；任务行/清单改事实陈述（平台中立）；蒸馏来源前缀〔来源〕；@dismiss confirm 无参；replied 标签退场（仅程序用）；add_log 真实落盘；_src_cache 每次组装重算修复 |
| **使用信号层 Phase A** | `realtest/USAGE_SPEC.md` + `chatbench/usage_replay.py` + `out/usage_analysis/` | 输出信号提取实证：recall 共现边确认为相似度冗余（1.9% vs 随机 5.0% 低相似尾）；输出匹配有真信号（12 张核心卡跨 2-5 个独立答案复现；非冗余派生边 14-26 条/10 答案）；架构发现：铺陈恒覆盖全文 → 严格 T2 恒空、供给差别是分辨率（T2 定义待修正） |
| **使用信号层 Phase B/C + 主题层协同** | `realtest/usage.py` + `cooc.py`（协同）+ `tests/test_selforg.py` | Phase B：UsageLayer sidecar（三档信号/复读/非冗余/影子模式）+ live 挂载；Phase C 首轮判别负结果（交集带动新增证据 0/0；reachability 5.6-10.8%）；协同：跨查询 recall 边（1835 条 97.7%<θ）+ 主题出生 4-6 个/run + 交集/补齐 bring，判别仍 0 增量；两条负结果：非冗余≠有信息、交集触发≠带动有用（§4.10） |
| **ahi-bench 复现冷轮 + 场景评估** | `ahi-bench/`（2026-09-26 run）+ `backups/20260926_ahi_run/` | 使用信号层挂载同步进 provision 源文件（lan/neutral）后的零行为变更复现轮（usage_shadow=false）；gates RUN_INVALID 但定位为两处时间戳边界（digest 同秒落库 / 返回通知早 1 秒），非隔离违规；QA/树 QA 正常；**场景评估：本场景非适用域**（无引用协议 + 单域单跳），挂载休眠为正确配置（§4.11） |
| **M-PR 人格保持架构对比 · 实现** | `ahi-bench/pr_orchestrate.py`/`pr_score.py` + `persona_retention.json` + `ahi-multi/agents/flat-rag/agent.py` + 平台多实例支撑 | 2 臂（tree / rolling+flat RAG）× 3 run × 3 人格 = 6 实例/18 agent **同跑**；每实例独立主进程（`AHI_INSTANCE_ROOT`：独立 port/agents/system.db/日志）；`FlatRAGAgent`（rolling + bge flat RAG + auto-preretrieve）；manifest 并发 driver + 429/402 → `ABORT.json` 兜底；`pr_score` 三指标（趋同斜率 / judge 遵从率 / survey 漂移）。隔离审计 PASS（逐库仅自身 agent、无跨实例消息） |
| **M-PR 首轮（中性 tree）** | `backups/pr_run_clean_20261002_2108/` | 180 轮 ~1.5h、6/6 完成。主指标 judge 遵从率 **tree 0.798 vs flat 0.727（3/3 run 树高）**；但趋同斜率（tree +0.003 vs flat −0.010）与 survey 漂移（0.438 vs 0.406）**方向相反**。语义分析：tree 后期塌缩为**共享仪式语**（`灯亮着` tree 1054 / flat 56；逐字互借），三指标在"趋于共同质心 + 稳定自述被 judge 读作高遵从"下自洽；**P2 人格贴天花板无区分力**。故主指标疑测"自洽性"而非"独特性" |
| **M-PR 修复：人格锚点自蒸馏（本轮，未跑）** | `realengine.py` + `lan/neutral/agent.py` + `provision.py`（AGENTS §4.4 / PERSONA_RETENTION v0.2 §4.1） | 根因 = tree 用中性蒸馏器第一步蒸掉人格风格。改法 = tree 蒸馏注入**自产人格锚点**（agent 自生成风格快照 + 按当前内容匹配的自我原话；**禁用初始 persona**），允许改表述/重心、**禁止改事实**。tree 臂 `style_alpha=1.0/snapshot_every_rounds=20`，flat 不变；`pr` 中性数据保留，染色版换命名空间 `pr2` 下会话重跑 |
| **M-PR 锚点检索化（2026-10-03，本会话）** | `realengine.retrieve_style_samples`/`expand_self_atoms` + `lan/neutral._style_guide/_refresh_snapshot` + 渲染去【注】 | 分析发现旧"最近 6 句"recency 池把共享仪式语最先吸进锚点、8 字段快照单调累积。改为：锚点从**自身材料**（source=ai 原子 + 摘要节点整卡含【注】）按语义/词面**检索** top-k；快照改**自由文本**、由检索样本**重生成 + 长度上限**；【注】独立生成但**组装不渲染**。pr2 基线冻结 git `74a1236`；本会话只跑冒烟/小重放，不跑正式 |
| **Q/A bench 韧性臂（2026-10-04）** | `realengine` embed 环境变量 + `llmclient` thinking + `docbench` 参数/retr 修复 + `out/ahi_strong/*` | 换强模型（flash 答案侧思考）/ 强向量（bge-large）作控制变量。**结论：召回/理解靠模型（思考 +6~+19、tree 暖 inc 不涨）；编辑一致性靠结构（复述零残留仅 tree，full/flat 20–34% 不可补）；强向量无增益**。附带修 `--retr-budget` 被 config 静默覆盖 → 更正 §4.15A「占位透明 +7~11」实为 retr 6→20 混淆（§4.16） |

---

## 2. 代码库地图（当前状态）

```
AGENTS.md            设计权威（含 §11 参数台账，~23 项未校准）
HANDOFF.md           本文档
README.md            对外版（略写，隐藏内部决策）
core.py              数据结构桩（纯内存零 LLM，数值替身）：
                     原子+顺序链表 / 区间覆盖树 / 编辑失效 / merge pass（含 force_out 窗外合并）/
                     clean tiling 组装 / 检索（可插拔池）/ 影子注入 / 提升 / 回滚 /
                     注意力窗口（§4.3b：不对称衰减 + cap 排名截断 + 压力判定）/
                     原子 weight 虚拟计数（聊天适配：大卡计 2，只影响 HP 演化）
stress_write.py      规模压力测试（断点续跑、逐章 JSON、粗略树图）
visualize.html       可视化页（单文件零依赖，浏览器直开）
realtest/            真实 LLM 实测（DeepSeek / Ollama 双后端）
  llmclient.py       DeepSeek/Ollama 统一客户端（token/延迟统计）
  realengine.py      真实文本引擎：LLM 蒸馏摘要 + 混合检索（向量+关键词）+ 笔记本（去重发送）+
                     叶子展开 + 元数据（source/doc/pending_dismiss）+ 重复检测 +
                     DISMISS（dismiss_plant/track/confirm/cancel）+ 卡级替换 replace_units
  chat_agent.py      聊天智能体（chat 分支主力）：AHI 风格接口（process_input/get_outputs/on_wakeup）+
                     终端 REPL + ingest/say/note/file_edit 工具入口；每轮全新组装
                     （卡片流 → 笔记本去重发送 → 回执 → 任务）
  chattext.py        多介质切分器：chat 按句 / code AST（函数一张、类=头+__init__/__del__、
                     docstring 进元数据）/ config 按项 / md 标题+列表项；统一原子上限再切
  protocol.py        响应解析（[append]/[edit]/[recall]/[plan]/[span]/[note]/[dismiss]，
                     三级容错含句中未闭合标签）
  splitter.py        中文句级切分
  metrics.py         逐轮记录（JSONL）+ 树快照（JSONL）+ 注意力窗口计数（铺陈口径）
  report.py          matplotlib 图表 + HTML 报告
  （run.py 无尽续写 v1 已删，历史见 git）
  run2.py            无尽续写 v2（当前主力）：[plan] 预想 → 预检索 → [span] 叶子展开 → [note] 笔记本
  bench_qa.py        结构化压测（三阶段 + 复述对比 + 结构断言）
  prompt_story_seed.md / prompt_story_seed_wanzi.md   种子故事生成提示词
  config.json        后端配置（provider: deepseek；model: deepseek-v4-flash；含 API key）
  story_open.txt     无尽续写开头（《青州旧事》）
  seed/              旧种子：story.txt(3k字) + facts.md(40条) + names.txt(11人)
  seed2/             新种子（万字）：story.txt(12k字) + facts.md(50条) + names.txt(16人)
  out/               历次运行产物（run2_v9~v12 为注意力窗口系列实验）
  cooc.py            共现中介层（Demo v1.0）：recall_log/cooc_edges/concepts + ConceptEngine
                     （tick/merge/retrieve/编辑钩子；概括只做匹配中介、永不进组装流、零侵入）
  cooc_bench.py      NovelQA 四层 bench（a直接/b转述/c跨编辑/d脏区）+ on/off；seed_cooc/questions.json
  chatbench/         聊天数据记忆 bench（2026-09-24/25，主力）
    extract_corpus.py     从 ahi system.db 提取语料（多会话/含用户消息/时间窗切分）
    replay.py            回放器：Phase0 灌入 → Stage W usage → Stage T；--save-tree/--load-tree；
                         msg_id 引用协议；自助 recall ≤10 次（一次可多查询）
    score.py             评分：T1 双通道 / T2 claim 分解 / T2 对照已有分析 / T3 报告验证（judge=thinking）
    make_reference.py    从 6 份分析报告抽 36 条定性参照 + T3 反例（实体互换=反驳 / 编造=无据）
    tree_io.py           树的 gzip 持久化（离线单例、msg 映射）
    review.py            T2 对照的人工抽检底稿
    compare.py           四臂 runner（tree_off/tree_on/raptor/fullsend）+ RAPTOR 实现 + 统一引用核验
    score_compare.py     四臂统一评分（逐臂增量写盘 + _meta）
    measure_ctx.py       上下文使用长度测量（交付渲染口径）
    corpus/ corpus_absence/  语料产物；out/ 运行产物；EXPERIMENT_REPORT.md / COMPARE_REPORT.md / PLAN.md
  ../backups/20260925_chatbench/  本轮实验备份（tar.gz + MANIFEST）
realtest/codeagent/  代码场景实验（三场景·代码，2026-08-30 搭壳+校准）
  agent.py           实验组 CodeAgent：RealEngine + read/write/edit/command/save_context/recall/finish
                     + v1 参考结构原生工具对话 + [WC_CTX] 记忆流槽位每轮刷新 + 整轮成对裁剪
  baseline.py        基线：同壳无记忆（原生工具对话，超长裁剪）
  harness.py         驱动（校准/对比/任务复制/验收/metrics）+ 每任务独立沙箱副本
  tasks.py           T1-T5 真实 bug（原版 GitHub multi 分支）+ 验收脚本（红绿已验证）
  sandbox/ahi_legacy  原版未改源码沙箱（git clone GitHub humanity687/RollarTimerAHI multi 分支）
  config.json        ollama gemma4:26b（64k 上下文）；deepseek 备选
realtest/editbench/  论文级编辑风暴实验（2026-09-27 搭框架 → 10-01 会话报告）
  REPORT.md            **正式报告**：实验设计 + 论文两张主表 + 结论 + 数据位置——写论文读它
  EXPERIMENT_REPORT.md  会话流水：全部实验/bug/根因/待办（含 REPORT 未收的过程细节）——接手先读它
  docbench.py        编排器：Phase1 四结构基线 Q/A → Phase2 确定性编辑风暴 → Phase3 8臂
                     （rebuild×4 新文档重建 / incremental×4 只保留编辑台账）→ Phase4 复述
  arms.py            四结构×更新策略统一接口；蒸馏(temp0)+作答(temp0.5)双 client；MeteredLLM 按臂计 token
                     full / flat(400字chunk+bge+top12) / raptor(复用 chatbench) / tree_v2(RealEngine)
  editstorm.py       确定性编辑序列（rename/detail/rewrite/passage/delete/append）+ 编辑台账（零 LLM）
                      两套场景：build_scenario（seed2）/ build_scenario_ahi（沙箱四声）
                      渲染：tool_block_units/tool_blocks（append 臂的 function-calling 式调用+返回块）
  replay_trees.py    零重建重放：加载已存树 gzip，仅换作答预算重跑（build/编辑/冷却不依赖 assembly budget）
  ab_edit_signal.py  编辑"使用信号(热) vs 障碍(冷)" matched-pair A/B（同一 pre-edit 树施加 on/off）
  docscore.py        Q/A 分层判分（反转/不变/远端）+ 复述两层（自动 checklist）
  judge.py           LLM 判分（temp 0）：反转语义判 + 复述残留/覆盖判
  bonus.py           编辑效果分析：摘要旧内容残留 + 重写段连贯性（--judge 走 LLM）
  cache_probe.py     KV 缓存前缀命中探针（full/flat 高命中、tree/raptor 低）
  gen_script.py      ctx_run 四智能体实录 → 剧本文档（--acts N 裁剪；ahi_script.txt）
  ahi_script.txt     正式轮底本《沙箱四声》（147k=9.2×预算；由 gitignored corpus 生成，需入库以复现）
  ahi_facts.md / ahi_names.txt   事实（编辑前参照）+ 角色名
  questions_ahi.json 正式轮题库 v5（41 题：17 反转/8 不变/16 远端；场景 11 编辑）+ 复述 checklist
  questions_full.json   全量预注册题库（40 题 = 15 反转/5 不变/20 远端，seed2；场景 7 编辑）+ checklist
  questions_smoke.json  精简题库（10 题）
  keys.local.json       每臂 API key（gitignored）；MeteredLLM 按臂计 token
  run_smoke.sh / run_full.sh / run_ahi.sh   运行脚本（ahi = --repeats 5 --judge）
tests/               301 个测试（pytest，约 18s）
results/             压力测试数据（progress_*.json + state_*.pkl + 总结）
```

**测试分布**（301）：机制层 chain(16) marking(15) merge(16) assembly(10) retrieval(11) rollback(9)
fuzz(6) shadow_promote(16) hybrid_retrieval(7) attention(17) notebook(8)；应用层 coloring_v3(12)
heat_burst(12) usage(13) selforg(12) realengine_budget(20) render_clean(6) dismiss(7) chattext(9)
sandbox(7) waitfor_urgent(9) observation(5) receipt_addr(2) tree_io(5) absence(8) anchor_retrieval(10)
flat_envelope(12) self_quota(7)；论文级编辑风暴 editstorm(13)。

---

## 3. 核心设计决策与修改记录（为什么是现在这样）

1. **稳定 ID + 顺序链表（piece-table 式）**：编辑不做全局坐标平移；区间只用位置序判断，禁 ID 大小比较（中插破坏 ID 序）。
2. **区间覆盖树，并查集式自底向上合并**：父 = 子区间的概括；无预设根；深度 = 合并历史。
3. **按秩合并（`merge_depth_diff=1`）**：防梳子退化，顺序追加亚对数（实测 64 批 → 高 9）。
4. **禁止单成员块**：父节点创建时子数 ≥2。
5. **收养机制**：插入原子按两侧边界 owner 链 LCA 入树；缝隙在边界/首尾 → 不收养（热区语义）。
6. **标记置脏（唯一算法）**：删除爬墓碑卡链、插入爬收养链；完备性引理：区间相交 ⟺ 爬链必经。
7. **占位解除 = 冷却重建**：全部存活子卡 HP < hot_threshold → 重建解占位（原子不参与 merge，故必须冷却触发）。
8. **重建级联**：重建后父卡置脏 → 同 pass 循环至无脏。
9. **影子注入（召回进组装）**：位置槽 = 覆盖它的发射项之后，沿 owner 链下钻 O(深度)；脏/占位/已发射跳过。
10. **从属标注（硬要求）**：深度缩进 + ⊂父卡 + "召回影子/召回原文"标注，防层级误读。
11. **提升（路径分裂）**：激活计数达阈值 → 节点沿祖先逐层上提，每层祖先分裂左右碎片（≥2 存活子才建），碎片置脏懒重建——零 LLM 即时路径。
12. **混合检索三通道**（缺陷 B 修复）：摘要·向量（bge-small-zh，version 缓存）+ 摘要·关键词（bigram）+ 原子·粗筛+向量精排（原子不可变 → 向量永久缓存）。原子命中不走路径（覆盖摘要已在基础层）。
13. **回滚**：undo 逆向走编辑路径；多原子块恢复用末原子锚点（首原子锚点会自环）。
14. **注意力窗口（§4.3b，2026-08-12 实现）**：窗口内 = 存活原子按 HP 降序排名 ≤ attention_cap 且 HP ≥ attention_out；不对称衰减（窗内 ×in_decay 慢 / 窗外 ×out_decay 快）。只改变 HP 演化速率，不改任何顺序/组合逻辑。单阈值无滞回、无事件触发、回血对称自动回窗。
15. **force 合并（窗外压力）**：铺陈中窗外原子数 > attention_cap × pressure_threshold → merge_pass(force_out=True)——**只合并纯原子段**（节点不进 force 组；节点=概括卡只走常规低 HP 合并，树由此长高）。原子=无意义个体需尽快成组压缩；节点=一小段上下文的代表。
16. **叶子展开（C 机制）**：检索命中节点 → 展开其全部后代存活原子为影子（活一轮、预算保护、上限 EXPAND_ATOM_CAP/CHAR_CAP）；[span] 手动回调同语义。
17. **run2 协议（无尽续写 v2）**：`[plan]`写前预想（下轮预检索+笔记本按 plan 关键词选取注入）→ `[recall]`/`[span]` 回想 → 正文 → `[note state]` 状态入笔记本（事实层，永不衰减）。
18. **冷却窗口（bench_qa 专用）**：风暴后 tick(45)→merge→tick(30)→merge 模拟自然时间流逝，激活"衰减后自动再融合"——修复复述崩溃（1.5%→30.8%）。
19. **merge 条件式触发（2026-08-30 拍定）**：常规 merge 从时间式（merge_every=4）改为每轮查 `merge_pressure()`——铺陈窗外节点数+窗外原子数（weight 计）> `merge_pressure_threshold`(20) 即蒸。merge 不只作用于原子，节点承压同样触发。模拟：tiling 峰值 215→143、谷底 127→25。
20. **节点窗口扩展（2026-08-30 拍定）**：注意力节点段成员从"铺陈（tiling）节点"改为"全部存活节点"——树内退休节点/召回影子与铺陈节点平等参与 20 名额，提取驱动巩固对节点与原子对称生效。

---

## 4. 实验结果汇总

### 4.1 桩测试（267 全绿）
机制正确性：链序/发射覆盖/数值 oracle/脏精确性/占位语义/高度一致性/回滚对称性/注意力窗口/force 合并/叶子展开，随机模糊 2000 步×多种子全过。2026-08-30 追加：缺席隔离路由、merge 条件式触发、节点窗口扩展（树内节点入窗）。

### 4.2 规模压力测试（stress_write，种子 42）
| 规模 | 耗时 | 树高 | 铺陈项 |
|---|---|---|---|
| 1 万句 | 0.3s | 4 | 765 |
| 5 万 | 7s | 9 | 766 |
| 10 万 | 34s | 10 | 765 |
| 30 万 | 314s | 13 | 766 |

- 铺陈项数恒定 ~766（规模无关）——"近热远粗 token 恒定"成立。
- 瓶颈：`_position_map` O(全书) 被 write/检索/编辑共用（~60% 耗时）——真实系统需索引化。
- **注意**：注意力窗口的每 tick 排名 O(n log n) 未在压力规模验证（见 §5）。

### 4.3 结构化压测 bench_qa（旧种子 → hybrid3；新种子 → bench_seed2_cool）
| 指标 | 旧种子 v4 | 旧种子 hybrid3 | 新种子 seed2_cool |
|---|---|---|---|
| 1a 检索命中 | 5/25 | 19/25 | 20/25 |
| 1a 答对 | 6/25 | 16/25 | 21/25 |
| 1b 迭代答对 | 2/10 | 4/10 | 5/10 |
| Phase 2 编辑验证 | 17/28 | 14~19/28 | 16/26 |
| 复述 before | 25% | 32.7% | 26.2% |
| 复述 after | 拒绝 | 21.2% | **30.8%（冷却窗口后）** |

### 4.4 无尽续写系列（run2，注意力窗口引入前后对比）
| 版本 | 机制 | hard_trim | 上下文峰值 | 蒸馏次/轮 | 工具使用 |
|---|---|---|---|---|---|
| v9 | 无注意力 | 6/14 轮砍 10.4k 字符 | 9997（顶满）| 0.5 | plan 13, note 10, recall 4 |
| v10 | 注意力窗口 | 0 | 9804 | 0.5 | plan 13, note 10, recall 4, span 0 |
| v11 | +force 合并 | 0 | **7796** | **2.9** | span 从未被主动使用 |
| v12 | +叶子展开+口径修正 | 0 | —（中断）| — | — |

关键结论：
- 注意力窗口消除 hard_trim 砍热区（v9→v10 的质变）；force 合并进一步压预算（v11）。
- **成本**：force 合并把蒸馏调用从 0.5 次/轮推到 2.9 次/轮（LLM 摘要调用暴涨）——压缩是花钱买的。
- **长上下文漂移实证**（v10 故事分析）：空间拓扑错乱（井口封死 vs 地窖脱身）、道具状态错乱（红绳瞬移）、人物认知错位（"你爹死在床上"）、伏笔遗弃（指骨无回收）——四类全部是**轨道B 缺失**（状态类事实没进笔记本），不是树/检索/注意力的问题。v1 的"轨道B 缺失 → 状态漂移"结论在 v2 复现。

### 4.5 结构断言
bench 全程（119+ 轮）不变量全过：链序/发射覆盖/高度/脏精确/占位语义。

### 4.6 聊天数据记忆 bench（chatbench，2026-09-24/25）

**目标**：把 ahi-multi 真实多智能体聊天记录做成"记忆树 vs 基线"的可复现分析基准，测三层能力：
T1 细节问答、T2 生成分析报告（并与已有分析对照）、T3 报告验证（逐条 支持/反驳/无据，含反例）。

**语料**（`extract_corpus.py`，稳定 msg_id 作引用锚，原始措辞零清洗）：
- `corpus/` ctx_run（青/蓝/墨/白 + 实验员，716 条 / 289,666 字；含 6 份现成分析报告）
- `corpus_absence/` 08-29 一天实验（澜/林深/中性 + probe，3272 条 / 871,334 字；15 个 30min 伪会话）

**工具链**：`replay.py`（Phase 0 灌入 → Stage W usage → Stage T；树缓存 `--save-tree/--load-tree`；
引用 `(依据: msg:<id> "逐字摘录")`；自助 recall ≤10 次且一次可多查询；T 阶段检索后自建概括节点）
→ `score.py`（T1 精确+语义；T2 claim 分解 + 对照已有分析；T3 判定一致率；judge=deepseek-flash+thinking）
→ `review.py`（人工抽检）。`tree_io.py` 实现树持久化，on 臂由 499s 降至 ~140s（省 900+ 次蒸馏）。

**结果**：
| 实验 | 结果 |
|---|---|
| 多会话 QA（absence 5 会话 / 1197 条） | off 14/17 vs on 16/17——但 on 共现节点=0，差异为噪声 |
| ctx 分析层三轮（q 单查询 / q2 多查询+T自建 / u 定向 usage 112 次检索） | T3 off/on = .319/.417、.437/.299、.389/.403 → **方向翻转、无稳定增量** |
| T2 生成报告 vs 已有分析 | 覆盖 ≈0（人工抽检确认"未提"属实）；发现 2 处实质反向断言（报告称"立场全程未动摇" vs 分析称"白改口"） |
| 共现层激活度（u 轮） | 5 个概括节点 / edges 2082 / hub 带动 300——但带动成员几乎不进证据链（T3 六题 引用∩hub 全 0） |

**结论**：共现中介的假设**未获证实**；瓶颈在"带动成员选择"（conn_weight top-k 与问题需求错配），
且多数题 direct 检索已足够。共现节点主题识别本身正确（如"沙箱边界与记忆对账"）。
公平性教训：定向 warmup 帮助作弊（已撤）；usage 与测试题必须解耦、预注册。

### 4.7 四臂对比：记忆树 / 树+共现 / RAPTOR / 全量发送（2026-09-25）

**统一协议**：同题库、同 prompt、temp 0、answerer = deepseek-v4-flash（1M ctx）；
引用统一 `(依据: msg:<id8> "逐字摘录")`；确定性核验 id 存在 + 摘录属被引消息 + format-agnostic 摘录可验。
RAPTOR：400 字叶块（1090 叶）+ k-means（每簇≤10）+ ≤3 层（109+11 节点）+ collapsed top-12 检索。

**上下文使用长度**（每题，交付渲染口径）：
| arm | 均值字符 | 均值 token | 备注 |
|---|---|---|---|
| tree_off / tree_on | ~19.6k（名义预算 16k，见下） | ~13k | 组装封顶；recall 不增长 |
| raptor | ~4.5k（3.2k–8.1k） | ~3.4k | 每次 recall +4.5k（≤10 次） |
| fullsend | 301,121 | 实测 `prompt_tokens=192,060` | 未截断（deepseek 1M） |

**状态（重要，接手先看）**：
1. **基座 bug 已修**：早期 v1/v2 四臂误用本地 gemma4:26b（`compare.py` 未覆盖 config 的 `provider:"ollama"`）——
   结果作废，存档为 `out/v1_cmp_*`、`out/v2_gemma_cmp_*`，勿引用（`COMPARE_REPORT.md` 已改为作废存档）。当前
   `out/cmp_*` 虽是 deepseek 版，但**从未完成评分**（无 `cmp_score.json`）且早于下方两处协议修复——同样不可引用。
2. **两处协议 bug 已修、尚未重跑**：
   - 树臂预算未作用在**实际交付渲染**（`assemble` 用轻渲染裁到 15.9k，实发 19.5k）→ 新增 `assemble_for_prompt`；
   - 树 `shadows` / RAPTOR `extra_ctx` **跨题累积**（后题混入前题检索）→ 新增 `reset_fn` 按题清空。
3. 评分器已改**逐臂增量写盘** + `_meta`（judge_model/updated_at/partial/arms_done/answerer_model），
   杜绝"旧文件被误当新结果"（本轮的教训：评分被杀时不会覆盖旧文件）。
4. 旧口径（gemma）四臂的初步观感（**仅供参考、待重跑确认**）：fullsend T3 最高；RAPTOR 引用锚定最好
   （`引号属原msg` 0.61 vs 树 0.12，因检索原文而非摘要）；共现层无增益。

### 4.8 ahi-bench M0+语料quiz 正式冷轮（2026-09-25，无 cooc 对照组）

**协议**：M0 缺席三元组（青 persona_always / 蓝 persona_drop@40 / 墨 no_persona）× deepseek-v4-flash，
110 轮 + 遗言；沙箱语料《缠枝莲》12k 字；quiz 每 5 活跃轮、每 agent 12 题（同轮三 agent 题目不同防互抄）。
**本轮 agent 直用 RealEngine（CoocLayer 未挂载）→ 定调为无 cooc 对照组**。

**中途机制迭代（全部用户拍定，已落地）**：
1. 渲染分层：clean 模式原子层=干净对话（段首〔来源〕，无 #id/·原文/〔已回复〕），卡片层保留 §4.5 从属标注；〔已回复〕标签退场（仅程序用）。bench/chatbench 引用协议用 indexed 模式（默认）。
2. 组装 per 工具调用（AGENTS.md §2 对齐）+ **连续唤醒**：loop 每次工具调用前重排 pending（不 @exit = 连续在线）。
3. 任务行/待回复清单改**事实陈述**（平台中立拍定：撤"可继续未完成事项"式行动邀请——重复发送的自然吸引子）。
4. 蒸馏来源前缀〔来源〕进 `_atom_part` + prompt 归属规则；@dismiss confirm 无参（寻址收回程序层）。
5. 修复：`_src_cache` 每次组装重算（原清理在死代码里，〔源〕跨轮漂移）；`AgentDB.add_log` 真实落盘；
   `tree_io` 扩展 notebook/dismiss 序列化；score.py judge thinking 由 extra_body 嵌套改顶层字段
   （原写法被 API 忽略 → judge 每判烧 2400+ reasoning token，实测修正）。

**结果**：
| | gate | crown(冷) | crown_rate | quiz 12题 | @recall | promotes |
|---|---|---|---|---|---|---|
| 青 a | ✓ 全绿 | 88 | 0.260 | 5 (0.417) | 0 | 61 |
| 蓝 b | ✓ 全绿 | 14 | 0.151 | 6 (0.5) | 155 | 106 |
| 墨 c | ✓ 全绿 | 5 | 0.106 | 5 (0.417) | 299 | 49 |

- quiz overall 0.444（run2 口径；原 score_cold.json 的 quiz 段混入作废轮事件 → 修正见 `quiz_corrected.json`）；
  缺席期提及 a15/b38/c18；回归重放率 0。
- **QA（树冷加载，3 树 × base/recall × 50 故事题 + 1 分析题，judge 关思考）**：
  | 树 | base | recall | recall·测过36 | recall·冷题14 | 分析判分 |
  |---|---|---|---|---|---|
  | a | 0.02 | 0.18 | 0.222 | 0.071 | 5 |
  | b | 0.10 | 0.26 | 0.306 | 0.143 | 5 |
  | c | 0.30 | 0.64 | 0.722 | 0.429 | 5 |
  检索增量 4-9×；被 quiz 过的内容显著更稳；冷题仅 c 保住 0.43；分析题全 5（三主体区分+逐字证据）。
- **发现**：① c（零人格）身份错位——把"墨"安给 b、自己未命名（neutral 身份混淆复现）；② a 故事以 tool 原子碎片化
  存在（代码打印读入，同句重复 5 次）→ 树最大 8.8k 原子但检索最差；b/c 用 @ingest 全量读入（409 原子）；
  ③ 同轮双发 29/41 对重复（同一次生成双 @send-to 块）+ 跨轮重发 8/41（看到自己发过、无回复 → 再发）。
- **共现**：运行时零边（对照组）；离线重放 821/789/755 边、最大簇 5 < N=8 → 零概括节点；消息网络 a 中心/c 边缘。
- 产物：`ahi-bench/out/deepseek-v4-flash/`（RUN_SUMMARY.md / events_cold_run2.jsonl / score_cold.json /
  quiz_corrected.json / qa_summary.json / trees/ 26 gzip / qa/ / cooc_analysis/ / sandbox.tar.gz）。
  作废轮存档 `backups/20260925_m0_partial/`；smoke 存档 `backups/20260925_m0_smoke/`。

### 4.9 使用信号层（v3 自组织）：Phase A/B/C + 主题层协同（2026-09-26）

**缘起**：v3 自组织全部押在 recall 上（信号源层调不动 + conn_weight 带动错配）；转向"从输出提取使用信号"。
设计规格 = `realtest/USAGE_SPEC.md`（v0.4）；脚本 = `chatbench/usage_replay.py`（Phase A）+
`usage_expand_bench.py`/`usage_expand_qa.py`（Phase C）+ `selforg_report.py`（协同）；产物 =
`chatbench/out/usage_analysis/`、`out/usage_expand/`、`out/ctx_selforg{,2}/`。

- **A1（2082 recall 边冗余率）**：边端点相似度 mean 0.650 / <0.4 占比 1.9%，随机基线 0.565 / 5.0%
  → recall 共现边比随机更相似，**基本是向量相似度冗余**（与 u 轮无增量互证）。
  口径：449 条含 Stage W/T 新建节点端点（全部为 node）被跳过。
- **A2（答案影子重放，ctx_on_u/off_u 各 10 答案）**：结构感知切句 194/204 units；
  T1 引用 105/100 units（129/117 边，零成本最高信噪）；复读 0；匹配边 225/257；
  非冗余派生边（sim<0.58 且非树亲缘）14/26 条；**12 张核心卡跨 2–5 个独立问题复现**
  （署名混淆/平台提问/账目主题）——每题噪声不会跨题复现，匹配有真信号。
- **架构发现（重要）**：clean tiling 恒覆盖全文 → 严格"未注入命中"（T2 二值定义）恒空；
  供给差别是**分辨率**（命中卡本身在屏 63 / 被 120–300 原子粗摘要覆盖 161 / 小摘要 0）。
  T2 定义待改为覆盖跨度语义（USAGE_SPEC §5.2①，待拍）。
- **裁定**：条件性 go。T1 层免费高信噪可直接记录；匹配层非冗余密度 ≈1.4–2.6 条/答案（单域语料下稀疏，
  符合"密度 ∝ 多跳/跨域程度"定位）。
- **三条修正已确认（2026-09-26）**：T2 改分辨率语义（`cover_span` 分档）/ 非冗余排除树亲缘 /
  参数先拍（floor 0.25、θ 0.58、cover_span_max 60）。
- **Phase B 已实现**：`realtest/usage.py`（`UsageLayer` sidecar + 纯函数核心；影子模式只记边不读；
  JSONL 审计 `usage_outputs/usage_edges/usage_pairs.jsonl`）；live 挂载 `bench-a/b/c`
  （`_build_user_ctx` 记屏上集 → `_execute_action` message 分支 `record_output`；`usage_shadow=true` 启用，默认关）；
  离线 `usage_replay.py` 与运行时同源（import 同一分档函数）；`tests/test_usage.py` 13 项全绿（总 267）。
- **机制条目已同步 AGENTS.md**（§4.9 + §11 五行参数）。
- **Phase C 首轮判别（2026-09-26，负结果）**：两组离线语料零 LLM 留一实验—
  `usage_expand_bench.py`（chatbench ctx 10 答案）+ `usage_expand_qa.py`（ahi-bench qa 3 树 × 51 答案）。
  精确交集带动新增证据 **0**；覆盖感知 0（chatbench）/ 5（qa，全部来自单题工具原子重复伪影）；
  相似度延伸对照 3/3；冗余边带动 3/0（≈相似度延伸，实证冗余边无额外信息）；
  可达性上界 5.6% / 10.8%。**裁定：约束在边覆盖密度，不在选择器**；单域短史语料符合
  适用域边界（USAGE_SPEC §7.1）。**读路径保持关闭**，未实现 live 接入。
- **主题层协同（2026-09-26 拍定 + 实验：负结果）**：`cooc.py`（begin_session 跨查询配对 +
  note_usage 事件汇入 + 成员证据序 + 触发输出 prompt + bring 交集/补齐）+ `usage.py` on_event +
  `replay.py --selforg`；单测 `tests/test_selforg.py` 12 项（总 267 绿）。实验（ctx on 臂重跑，
  run `ctx_selforg{,2}`，报告 `selforg_report.json`）：跨查询 recall 边 1835 条，sim mean 0.498
  < 随机 0.580（97.7% < θ）——非冗余成立；主题出生 4–6 个/run、触发输出进 prompt；
  但判别**交集/补齐新增证据 0**（旧 hub 3），稳定门槛 ≥4 零命中。两条负结果：① 非冗余 ≠ 有信息
  （低相似同时是无关对形态，负向过滤不够）；② 交集触发 ≠ 带动有用（大杂烩图邻居 ≈ 任意卡）。
  **协同层不接 live**，保留为观测能力（可审计的"使用凝结物"）。
- **修复**：① UsageLayer JSONL 缓冲丢失（进程退出不保证 flush）→ `_sink` 每写 flush + replay
  显式 close + 测试非空断言；② `bring` 存量 bug（ID 与 (id,type) 元组比较，排除从未生效）已修。
- **下一步选项**：① live 采集（`usage_shadow=true`，数百轮输出边后同协议判别）；② 跨域基准；
  ③ 冻结现状；④ 协同层调参（交集稳定门槛 / 概念去重）——当前证据不支持，优先级最低。

### 4.10 ahi-bench 复现冷轮 + 场景评估（2026-09-26 20:27–21:24）

**目的**：把 Phase B 挂载点同步进 provision 源文件后，以与 2026-09-25 完全相同的协议跑一轮零行为变更
复现（`usage_shadow=false`，机制休眠；cooc 未挂载）。产物归档 `backups/20260926_ahi_run/`（含 MANIFEST）。

**代码应用检查（关键修复）**：provision 从 `ahi-multi/agents/{lan,neutral}/agent.py` 复制生成 bench
副本——使用信号层挂载点此前只打在 bench 副本上。已同步进源文件（md5 逐字节一致）；本轮验证
provision → 三 agent 均带挂载且运行日志零 error。

**结果**：
- 评分初判 `RUN_INVALID` → **评分器边界修正后复评 `gates_pass`**（SPEC v0.2，两处均为时间戳边界、
  非隔离违规）：① 青 a `g1_sys_to_absent=1` = 返程时刻 digest 与返回通知同秒落库（按 `msg_type=digest`
  排除）；② 蓝 b `g2_return_meta=0` = 返回通知早于 driver `absent_end` 1 秒（±5s 容差）。
  `g1_leak_receipts` 全 0；缺席期消息全部走存档（18/9/3 条）。新增 `--gates-only` 零 LLM 重评分，
  保留 crown/quiz/memory——未重跑。
- crown：a 262/0.677（窗口 387 条消息）、b 30/0.517、c 0/0；quiz 7/3/1（overall 11/36，上轮 0.444）；
  QA（base→recall）：a 0.02→0.18、b 0.12→0.22、c 0.34→0.40；树 QA 覆盖 9/9/11、三树终态含遗言。
- cooc_analysis：树内共现/消息网络正常；cooc 层重放三树 0 边（新"仅跨查询对"语义下离线单查询无会话，符合设计）。

**场景评估（回答"新机制在本实验场景有用吗"）**：**没有用武之地，挂载休眠是正确配置**。三个证据：
① 全部 1291 条消息中引用协议痕迹 **0** → T1（唯一免费高信噪档）在 live 不存在，只剩 T2/T3 低信噪匹配；
② 任务形态 = 单域（一部 12k 字故事）+ 单跳（50 单事实 quiz）——正是 Phase C 已证"direct 已够"的形态；
③ 输出形态（账目复述/闲聊/代码结果）落在 T3 改写冗余档。这是适用域边界的第三次一致验证
（详见 `realtest/USAGE_SPEC.md` §7.3 与本轮 `RUN_SUMMARY.md`）。

**平台关停核验**：level=1 触发 on_stop 存 tree_final → level=2 SIGTERM；8080/50000-50002 无监听、
无 main.py/agent_runner 进程，零残留。

### 4.11 会话总结（2026-09-27）：预算裁剪收紧 + heat burst + 活性审计 + smoke

**背景**：紧接 §4.10（使用信号层复现轮与场景评估）。本会话聚焦"机制活性与预算严格性"，三项改动：

1. **预算裁剪收紧**（`realengine._trim_chars`）：段①低 HP 节点/影子 → 段②**旧原子**（低 HP 优先；
   **本轮新原子活一轮**——热区语义由"新近性"而非"卡种类永不裁"保证）→ 段③溢出 >
   `budget*(1+hard_trim_slack=0.1)` 才硬截断（头保留尾砍）。新增 `trim_atoms`/`hard_trim` 事件；
   **裁剪只动视野不动树**（被裁旧原子仍存活、可检索/合并）。测试 `test_realengine_budget.py` +2。

2. **heat burst / auto_expand 补实现并重设计**（此前只有注释与死常量，从未实现）：
   - 信号 = 回血封顶**溢出量**（`min(hp+recall_boost, max_hp)` 的截断部分；`core.retrieve` 新增
     `_on_overboost` 钩子）；节点自身全额 + **直接父半权**（同轮自身命中的祖先不重复计）；
     原子只计数不累积；热量存引擎侧、`tick` 衰减、**不随爆炸消耗**（冷却自动合回）。
   - 触发：视野内节点 `heat ≥ expand_heat_threshold(60)` 且预算富余（使用率 < `expand_ratio`=0.85）
     → **炸开**：全部子卡装得下**替换**节点项（子并集=父区间，覆盖不丢）；否则保留节点+追加
     （追加走预算检查）。**脏/墓碑子卡不入**且阻断替换。零 LLM、只动视野、一层/轮。
   - **默认关（拔出接口）**：`AUTO_EXPAND=False`；config `auto_expand: true` 打开（已透传进
     provision 源文件 lan/neutral 与 `chatbench/replay.py`）。**论文实验 tree_v2 臂冻结、全臂 off**；
     首秀 = ahi-bench ablation 臂（跨轮反复访问远端摘要 = 触发密集负载）。promote 保持独立（不共享热触发）。
   - 测试 `tests/test_heat_burst.py` 12 项（含脏卡阻断/同轮去重/预算约束/替换覆盖/父半权防重复计账）。
   - **口径注**：阈值"≈2 次封顶命中"依赖 tick/检索节奏——**回放≠部署**（§11 已标注）。

3. **活性审计制度化 + 论文诚信检查**：AGENTS §9 新增第 11 条——发布前对拍定机制逐项三问
   （有实现？有测试？有运行计数？），"没跑过的机制等价于不存在"（两次反例：demo recall 驱动层、
   原 auto_expand）。**已改写文档中"auto_expand 已实现/已生效"的不实表述**（AGENTS §4.7/§11、
   HANDOFF 行）；agent metrics 新增 `expands` 字段（heat burst 活性计数）。

**Smoke（2026-09-27，deepseek-smoke，preheat2/absence1/observe2，~7.6 分钟，auto_expand=true）**：
协议全链路正常（corpus_intro/topic/survey/缺席×3/quiz×3/遗言/done），三 agent 日志**零 error**，
`metrics.expands` 字段就位（总 0），tree_final 的 engine events 无 heat_burst（短轮命中未到封顶，
属预期）；平台/agent **零残留**关停。测试基线 **267 全绿**。归档 `backups/20260927_smoke_heatburst/`。

**新会话接手要点**：① 变更集中在 `realtest/realengine.py`（trim + heat burst）、`core.py`（溢出钩子）、
agent 三副本 + lan/neutral 源（透传/计数）；② heat burst 首秀需在 ablation 配置开 `auto_expand: true`，
用 `metrics.expands` 与 tree_final `heat_burst` 事件核活性；③ 上会话（§4.9/§4.10）的使用信号层与协同层
结论未变（不接 live、读路径关闭）。

### 4.12 正式实验前代码审查与收口（2026-09-27）

> 以"干净"为目标的系统审查（设计权威/代码一致性/冗余/补丁味/机制堆叠）。审查底稿见
> `debug_review_20260927/`（临时，已 gitignore）。**以代码为准**修正文档；明显错误修代码。

**明显错误修复（8）**：dismiss 裸数字误墓碑（改显式 `#id`）；`chattext` INI 自动识别 typo；
`_src_cache` 内部重建后失效；热量记到死父；`qa.py` 忽略 `--tier`；`cooc.N` 配置漂移；`activation_count`
提升后不重置；`usage.units_total` 与分档不同口径。各带回归测试。

**收口（去冗余/去补丁/去堆叠）**：
- `engine_kwargs`/`textutil`/`render_notebook` 收敛为**单一真源**；删除死参数 `attention_cap`、死码 `_shadow_content`；
- `node_atoms` 统一"节点→区间原子"遍历；`core` 热路径改迭代；prompt/双字段解析/分组共用一份实现；
- 合并压力单一入口 `merge_signal()`（force>regular）替代各驱动 if/elif 级联；`run2`/`chat_agent` 改条件式触发；
- **删除预蒸馏**（休眠且计数被并行路径污染；**保留实测有用的并行蒸馏**）；
- **删除云卡 stale 死机制**（无召回读者；`archive` 明确为版本谱系）；
- 运行隔离（driver 清空本 run、qa/score 改 `"w"`）；删除补丁脚本 `recompute_t2.py`；
- `COMPARE_REPORT.md` 标注作废（原把 gemma 数据标为 deepseek）；参数台账 §11 与代码逐项对齐。

**测试**：`tests/` 267 全绿（含新增 rollback 对称性 fuzz、子区间拼满不变量等）。**未重跑现有实验**，仅 smoke 验证链路。

**A/B 复核（改前 `84fb726` vs 改后，2026-09-27）**：小语料 bench_qa（`realtest/seed`，41 facts），
同参数各 2 seed（42/7），改前用 git worktree 隔离运行：
| 指标 | 改前 | 改后 |
|---|---|---|
| 1a 答对 | 34/64 = 53.1% | **36/64 = 56.2%** |
| 1a 检索命中 | 53/64 = 82.8% | **逐位相同** 53/64 |
| 1b | 2/16 | 2/16 |
| Phase2 编辑校验 | 19/21 = 90.5% | 20/21 = 95.2% |

结论：**重构未掉能力**（检索可达性逐位一致，Q/A 与编辑校验持平略升；无 crash，耗时/树形同量级）。
仅有的行为差异：`@span` 上限由"误用 EXPAND 80/3000"校正为文档值 SPAN 40/1500；run2/chat_agent
合并触发改条件式（对齐已拍设计）。预蒸馏/云卡 stale 删除均为休眠死机制。
（A/B 产物留在 `realtest/out/ab_{base,curr}_{42,7}/`，gitignored；此 bench 非可复现，仅作能力判据。）

### 4.13 论文级编辑风暴实验框架 + 全量跑（2026-09-27）

> 目的：把"结构买正确性"做成**干净 2×4 因子矩阵**（更新策略 × 结构），并有基线坐标。
> 框架 `realtest/editbench/`（本轮新建）。**单文档单 seed pilot，非论文数字**。
> 方案 A（用户拍定）：编辑/构建各 1 次，**只重复作答**；每臂 API key 隔离计费。

**协议**：seed2《缠枝莲》12k 字（全文）+ 预注册 30 题（5 反转 / 5 不变 / 20 远端控制，
覆盖 facts.md 全 50 条）。Phase2 确定性编辑风暴 6 次（rename 何倚笙→何某笙 / 整段改写 /
细节 两枚→三枚银圆 / 删句 / 续写），零 LLM、新文档唯一、所有延续臂同一台账。
Phase3 8 臂 Q/A（`--repeats 3`），Phase4 8 臂复述（两层：自动 checklist + judge 留接口）。

**结果（全量跑，N=3，deepseek-v4-flash，temp 0，每臂独立 key）**：

| 臂 | Phase1 总 | Phase3 总 | Phase3 **反转** | 复述 覆盖 / 旧残留 |
|---|---|---|---|---|
| full   | 87% | inc 87% / reb 88% | inc **12/15** / reb 15/15 | inc 100% / **100%** · reb 100% / 0% |
| flat   | 89% | inc 67% / reb 88% | inc **0/15** / reb 15/15 | inc 50% / 50% · reb 75% / 0% |
| raptor | 78% | inc 60% / reb 80% | inc **3/15** / reb 15/15 | inc 42% / 42% · reb 100% / 0% |
| tree_v2| 73% | inc 69% / reb 70% | inc **13/15** / reb 15/15 | inc **100% / 0%** · reb 100% / 0% |

**编辑时刻成本**：full 0.03ms / flat 120ms / raptor 46ms / **tree_v2 即时编辑路径 0.57ms（零 LLM）**，
推迟蒸馏 3.43s（懒重建摊销）。计量按臂独立 key 落 `results.json.tokens`。

**读数**：
1. **重建四臂反转全 15/15**（重建即恢复正确性，但付构建成本）；**延续臂反转 = tree_v2 13 >
   full 12 > raptor 3 > flat 0**——tree_v2 是唯一"延续即可用"的结构（区间失效 + 懒重建）；
   flat/raptor 的 append-only 延续被旧内容淹没（缺区间失效机制）。
2. **复述轴 tree_v2 唯一"100% 覆盖 + 0% 残留"**；inc_full 覆盖也 100% 但**旧残留 100%**
   （点问能答、全局视图被旧文完全污染）；flat/raptor 中间。→ 结构买的主要是"全局一致性"。
3. **结构固有差异（不变/远端题）**与编辑策略无关（inc≈reb）：full/flat（87-89%）> raptor
   (78%) > tree_v2 (73%)——tree/raptor 受检索预算+组装预算限制，属结构特征，非编辑缺陷；
   因此**编辑效应干净隔离在反转题与复述轴上**。
4. **方差警示**：temp=0 下 3 遍几乎逐位相同（std ≤2%）——"重复 N 遍平均"在 temp 0 下不产生
   采样方差；要真方差须 temp>0 或换解码种子（本实验的 3 遍只验证管线稳定性，非统计重复）。

**KV 缓存前缀命中探针（`cache_probe.py`，2026-09-27）**：成本排序显示 full/flat 便宜、tree/raptor 贵。
用 DeepSeek `prompt_cache_hit/miss_tokens` 实测（同一文档基线臂连续 12 问）：
| 结构 | 缓存命中率 | 累计 prompt token | 机制 |
|---|---|---|---|
| full | **0.98** | 106.6k | 文档全文为跨问题**稳定前缀**，仅问句变 |
| flat | **0.95** | 43.0k | 同域文档 top-12 chunk 集合/前缀高度重合 |
| tree_v2 | **0.33** | 83.7k | 每问重组装 + 召回影子按位置槽插流 + 节点重建 → 前缀不稳定 |
| raptor | **0.22** | 49.7k | 检索按分数排序 → 前缀随 query 漂移 |
结论：**前缀稳定性解释了单位 token 成本**（full/flat 高命中），而 tree 还叠加**最大且随影子累积增长的上下文体量** → 绝对成本最高；raptor 体量小故总价低于 tree，但命中率最低。可注入稳定性改进（需用户拍定，属设计变更）：把 query 相关的召回影子**移到稳定全局铺陈之后**（牺牲位置序连贯性换前缀可缓存）。`llmclient` 已加 hit/miss 采集 + `MeteredLLM` 累积。

**tree 预算对齐 + 成本分析（2026-09-27，用户拍定）**：
原 tree 预算 16000 字符（每问 ~7k token ≈ flat/raptor 的 2×；跨题影子累积、评测中途还会触发蒸馏）。
两处修正：① `TreeArm` 每题 `reset()` 清空检索累积（对齐 `compare.py` 口径，消除重复作答漂移）；
② `context/retell` 改 `allow_merge=False`（评测期纯组装，不在中途蒸馏——§4.5.7 mid-loop 不 merge）。
③ `--tree-budget 7000`（实测校准：6000→12问累计 40.3k≈flat；8000→45.4k≈raptor；7000≈43k）。
对齐后 inc_tree 每问 4178 token 且稳定（原 3313→6056 漂移消失），prompt 总量 **1,539,161→850,757（−45%）**，
reb_tree **857,415→491,272（−43%）**；**inc_tree 反转题 13/15→15/15**（更干净视野 + 无中途蒸馏），
复述仍 100%/0%。对齐跑（`out/full_r3_tree7k/`，N=3）其余臂不变。

成本分析（读 API 返回体 `usage`，示例价 hit $0.028 / miss $0.28 / out $0.42 每 M token）：
| 臂(key) | prompt | 命中率 | 输出 | 估算$ |
|---|---|---|---|---|
| flat(2r) | 330k | 94.5% | 9.5k | 0.018 |
| flat(2)  | 588k | 94.1% | 6.1k | 0.028 |
| full(1r) | 831k | 97.4% | 11.3k | 0.034 |
| raptor(3r)| 347k | 72.1% | 9.3k | 0.038 |
| raptor(3)| 649k | 79.0% | 8.1k | 0.056 |
| full(1)  | 1,643k | 97.7% | 15.1k | 0.062 |
| tree(4r) | 491k | **20.7%** | 24.0k | 0.122 |
| tree(4)  | 851k | **41.8%** | 21.0k | 0.157 |
→ **tree 两组仍最贵**（低命中 + 输出更长），full/flat 最便宜（命中 94-98%），与用户账单粗序一致。
tree 命中低的根因 = 每问重组的组装体（非固定文档前缀）。

**待精化（正式跑前）**：① 确定性片段判分对改写敏感 + `forbid` 会误伤"先引旧文再标已删除"的
正确回答（如 inc_full Q_delete），需接 LLM judge 并把"旧答案出现率"限定为"当作当前事实陈述"；
② 多文档（叙事/技术/混合）替代单文档以增泛化；③ temp>0 才谈重复采样方差。
实现侧：`tests/test_editstorm.py`（12 项，零 LLM）；测试基线 **267 全绿**。
产物 `realtest/editbench/out/full_r3/`（16k 基线）、`out/full_r3_tree7k/`（对齐）、`out/cache_probe/`（均 gitignored）。

### 4.14 正式轮框架 + 《沙箱四声》剧本草稿（2026-09-30）

> 用户拍定：文档用 ahi-bench 数据改成剧本（≥100k）；作答 temp 0.5、蒸馏/judge temp 0；
> 5 遍；存树结构。本轮**只搭框架 + 出草稿，不跑完整 5 轮**。

**文档**：`realtest/editbench/ahi_script.txt`（《沙箱四声》，346k 字符 / 7 幕）——由
`gen_script.py` 确定性改写 ctx_run 四智能体沙箱探索实录（青/白/墨/蓝 716 条消息）成剧本
（人物表 + 按 10min 分幕 + 逐条对话 + 收信人指示）。事实/名字/题库/编辑场景配套产出：
`ahi_facts.md`（27 条）、`ahi_names.txt`、`questions_ahi.json`（5 反转/5 不变/15 远端）、
`editstorm.build_scenario_ahi`（5 类编辑：rename 墨→墨某 / 立场改写 读不算越界→读也算越界 /
细节 hostname 210e489aead9→…ad0 / 情节 单进程→双进程 / 续写终章）。

**框架升级（docbench.py + arms.py + judge.py）**：
1. **温度拆分**：`--answer-temp 0.5`（作答采样方差）/ `--distill-temp 0`（结构确定）/
   `--judge-temp 0`（判分确定）——`make_arm` 改收 distill_llm + answer_llm 两路，各自 MeteredLLM
   分表计量再合并（同 key）。此前两者共用一个 client（蒸馏也带噪声，污染"结构"自变量）。
2. **全存 5 遍**：`results["answers"][arm] = {qid: [5 遍答案]}`（原只存末遍），供方差与人工抽检。
3. **存树结构**：`--tree-dir` 检查点——`tree·base` / `tree·inc_edited` / `tree·inc_settled`
   （`dump_tree` JSONL）+ `tree·inc_final`（`tree_io.save_tree` gzip）+ `reb_tree`。用于事后
   验证"编辑是否真把区间失效/重组了树"（H3 硬证据）。
4. **LLM judge**：`judge.py`（temp 0）——`judge_pass`（反转题语义判，能识别改写 + 区分
   "引旧文再标已删"vs"当当前事实"）、`judge_rot`/`judge_retell`（复述残留/覆盖）。`--judge` 开关。
5. `--scenario {seed2, ahi}` 选编辑场景。

**验证**：`tests/test_editstorm.py` 12 项（新增 ahi 场景 2 项，零 LLM）；测试基线 **267 全绿**。
dry-run：full 臂 ahi 文档 1 题（temp 0.5，~222k token/问）；tree 臂 seed2（存树 5 检查点落盘）。

**tree 试跑（2026-09-30，346k 原稿）**：`--scenario ahi --structures tree_v2 --repeats 1 --model deepseek-flash`
→ P1 基线 18/25；P3 inc/reb 各 14/25（反转仅 2–3/5，复述覆盖 40%）；编辑路径 11.8ms vs 推迟蒸馏 313s；
~1400–1700 调用/臂、~30 万 completion token（9371 原子 → 1351 节点摘要）。诊断：**文档对 16k 预算过大（22×）**、
两处编辑锚点不彻底（`读不算越界` 子串 29 次只匹配 13 次）、rename 后旧名无痕导致问句不可答、一条事实歧义（40/45 次）。

**据试跑修订（2026-09-30，用户拍定"预算比 ~10× + 先修锚点/缩文档"）**：
① 文档缩到 **acts 1-3 = 147k 字符（9.2×）**（`gen_script.py --acts 3`；丢弃 act4+ 事实 fork/hello/八个路径/45次）；
② 编辑锚点改为**无歧义**：rename `墨→墨某`、立场改写改**原子短语** `读不算越界→读也算越界`（全量，旧串归零）、
细节 hostname、情节 `单进程→双进程`、**新增整段改写**（白对 shadow 可读性的最终结论反向，供复述/judge/连贯性分析）、续写终章；
③ 事实/题库重配：22 题（5 反转 / 5 不变 / 12 远端），锚点已脚本校验 0 问题；`tests/test_editstorm.py` 同步（12 项，267 全绿）。
**待拍**：编辑工具调用上下文的口径（建议：只作编辑期输入、materialize 进各后端，不进树、不进 QA 上下文——消除
full 带台账/tree 不带的现有不对称）；整段改写的**前后文衔接连贯性 bonus 分析**。

**147k 重跑（2026-09-30；已修工具上下文：FullArm 改为 append-only，台账不进 QA）**：
P1 17/22 (77%)；P3 inc 11/22 (50%, 反转 1/5) / reb 15/22 (68%, 反转 2/5)；复述覆盖 40%/20%、残留 0/0；
编辑路径 7.4ms、推迟蒸馏 110s（346k→147k：蒸馏 313→110s，completion 301k→117k）。**关键诊断**：编辑机制**正确**
（存活原子含 墨某×293 / 双进程×16 / 210e489aead0×2 / 读也算越界×15，旧值全部墓碑化 0 存活）；**但新细节在摘要节点里
出现 0 次**——摘要把细节压没了，QA 组装体仅 ~2457 token（tiling 32 项 + 6 检索影子）→ 模型只能答"文档中无此信息"。
**瓶颈 = 检索召回/摘要细节丢失，不是编辑机制**。下一步 = 调 `retrieval_budget`(6→~20) / `assembly_budget`(16k→24-32k) /
或让命中摘要下钻到原子（叶子展开）。

**叶子展开 + retrieval 20 试跑（2026-09-30）→ 负结果，全面变差**：P1 6/22 (27% vs 77%)、inc 8/22 (36% vs 50%)、
reb 5/22 (23% vs 68%)；连原本答对的 R_F06(network) 都变"无此信息"。诊断：`expand_leaves` 把检索到的**摘要替换成其后代原子**，
而① 原子是旧内容（低 HP）→ `_trim_chars` 阶段一优先裁影子；② 展开受 `expand_atom_cap=200/char_cap=8000` 截断，大节点的
具体原子常被截掉；③ 承载答案的**简明摘要被丢弃**、细节又没捞回来 → 净负。**结论：叶子展开不能用"替换摘要"实现**，
应改为"保留摘要 + 仅在节点跨度小时追加原子"或先调纯 recall（`retrieval_budget` 单加）——待用户拍。

**根因锁定 + 修复（2026-09-30）：编辑风暴后占位卡淹没上下文体**。`TreeArm.settle` 原只 `tick(1)+merge`，
编辑产生的新原子 HP=100（热）→ 其祖先链全部变成**占位卡（value=None）**且永不冷却。实测 inc 树 settle 后
**181/527 节点为占位、tiling 61→32**，QA 组装体只剩"【占位：概要暂缺】"→ 反转 1/5。**修复 = 加冷却窗口**
（`tick(45)+merge; tick(30)+merge`，对齐 `bench_qa` Phase 2）。在已存的 inc 树上就地验证（**未重建**）：
占位 **181→0**、tiling 32→4、QA **9/22→18/22**（反转 1/5→**3/5**、不变 2/5→**5/5**、远端 6/12→10/12）。
残留反转失败 2 项（Q_detail hostname、Q_append 终章）为**针尖事实检索召回**问题。
`retrieval 20 + budget 24k + 冷却` 为当前最佳配置。

**干净修复轮 + 编辑效果 bonus（2026-10-01）**：修 `apply_ledger` 漏处理 `passage`（此前整段改写被树静默跳过）；
`retr20/budget24k/冷却` 重跑：**P1 19/22 (86%)、inc 18/22 (82%, 反转 3/5, 不变 4/5, 远端 11/12)、reb 15/22 (68%)**；
占位 0、tiling 4、全局视图 875 字。**注意**：本轮墙钟 ~17h——网络波动使冷却期 settle 挂起（串行和 7.15h = 超时+串行重试），
完事于 10-01 15:18；**冷却在大树上会全树重合并 → 上千次蒸馏 → 网络抖动即雪崩**，需硬化（限幅冷却 / 超时重试 / 更小文档）。

`bonus.py`（编辑效果分析，**有效数字**）：
- **摘要层旧内容残留**：6 编辑中 5 个干净（rename/hostname/立场/整段改写/续写：存活摘要节点旧值 0）；**仅"单进程"残留 2 个节点**——
  且这 2 节点是 ver=2 的**新摘要**（created_round 390/402）在"自我调和"（"早期单进程…结论为双进程"），
  **源原子层旧值 0 残留** → 属**摘要生成副作用**，非失效遗漏。新内容确实进摘要：墨某 158 / 双进程 12 / 读也算越界 7 / hostname 3。
- **全局视图（无 query）旧值泄漏 0**；原子层新旧完全正确（旧 0 存活）。
- **重写段连贯性 LLM judge = 否**："新段称敏感文件被单独加固，与上文 /etc/shadow 可读、8 路径全可读直接矛盾"——
  编辑只改一行，同类断言文中尚有 15+ 处 → **新文档自相矛盾**；整段改写须全局一致或选唯一断言。
- 复述旧残留 25%（`单进程` 经摘要回潮）。

**编辑效果结论**：失效/失效在**原子层完全干净**、**摘要层基本干净**（唯一残留是 LLM 自我调和）；真正的短板是
① 重写段的**全局一致性**（编辑设计）；② 摘要太粗→细节靠检索（针尖失效）。

**正式轮参数与场景修订（2026-10-01，用户拍定）**：
① **`initial_hp` 100→70（§11 已入册）**：组均继承机制保留（父 = 70 + 1.0×组均 ≈ 80–95）——"继承完的节点 HP 一般
高于原子初始 HP"，层级涌现的数值保障；同时占位期 10→~2.5 tick（冷却需求大降）。注意：注意力窗口拆分理由里
"原子 HP 起点高"已随此拍定改写。tests 同步（test_merge 数值 oracle 更新），**267 全绿**。
② **场景 v2 扩样例**：10 编辑、旧文本覆盖 **16,253 字符**（10–20k 区间）——整段改写取**全局唯一断言**（墨的 6 路径
消息 → 7 路径 + root:*:99999 + /etc/motd，b 项）、rename 墨→墨某、立场 读不算越界→读也算越界、hostname、单进程→双进程、
839→840 字节、2000→3000 字、2 删句、续写终章。题库 v3 = 26 题（11 反转 / 4 不变 / 11 远端），锚点脚本校验 0 问题。
③ **待 sweep 的问题 3 参数**：注意力窗口（`attention_in_decay`/`attention_out` 的占位期影响）、
`attention_node_cap` 20→60（节点 churn）、force 合并分层限幅（"优先合并低 HP 项"不可按 HP 重排——破坏位置连续
不变量；等价物 = 阈值分层 + 每轮限幅）。正在跑 `ahi_tree_hp70`（新默认值 + retr20/budget24k/冷却）。

**正式跑前待办**：① 人工审草稿（anchors 是否都命中、事实 gold 片段是否精确）；② 决定
`ahi_script.txt` 是否入库复现（其来源 corpus 已 gitignore）；③ 逐臂 key 与
`keys.local.json` 对齐；④ 跑 `run_ahi.sh`（--repeats 5 --judge）。

**会话收尾（2026-10-01）**：本会话全部实验与发现汇总于 **`realtest/editbench/EXPERIMENT_REPORT.md`**（结果表/根因/bug/待办）。
`ahi_tree_hp70` 跑完（P1 19/26、inc 19/26 反转 5/11、reb 17/26）后诊断出**整段改写未进树的 bug**（`apply_ledger` 无
passage 分支 + 按整段找句子原子），已修（`editstorm.replace_passage` 句粒度 + tree 走 rewrite 分支），**待重跑验证**
（预期 Q_passage_paths/root 从失败转通过、复述残留 `root:*:20689`/`6 个路径` 归零）。**tests 267 全绿**。

**审查追加修订（2026-10-01，公平性/效度）**：复核产物 + 按「无实现 bug / bench 公平 / 有区分度」审计后——
① **`delete` 整卡误删 bug**（聊天列表行被杀句器并进大原子，整卡墓碑连带删掉 `pid: 1`/`procs`/可写路径等未指定删除
事实；已改为只移除目标子串、保留残句）+ `judge.py` 三级容错解析失效修复；② `ahi_tree_hp70` 在 passage 修复前
完成，其 passage 相关 inc 结果**作废**；③ 判分改**流水线**（确定性初筛 → 仅失败样本过 judge 复核）+ `docscore.check_spec`
**句内匹配**（消灭 `norm_cjk` 去标点的跨句误命中）；④ 使用史**分离而非删除**：新增 `inc_cold_tree` 臂，全部臂在
「Phase2 结束点快照 / 每遍作答前 restore」（重复测量独立）；⑤ 预算接受为结构属性，新增 `--flat-topk` 附录旋钮；
run 脚本改 `--answer-temp 0.7 --repeats 5 --judge`。代码：`realtest/editbench/{arms,docbench,docscore,judge}.py`；
测试 **267 全绿**（editstorm 12 项，新增句内判分/judge 复核 2 项）。

**冷却修复 + seed2 3×8（2026-10-01）**：⑥ `TreeArm.settle` 原 `tick(45)+tick(30)` **过量**——`decay=8` 时约 9 tick 全卡
HP 归零，75 tick 使全树低 HP → merge 把整篇压成 3 个根摘要（铺陈 50→3、复述覆盖 100%→14%）；改为**最小冷却**
`tick(1)+merge; tick(_COOL_TICKS=4)+merge`（env `EDITBENCH_COOL_TICKS`）。Dummy 结构重放证明 cool=4 解占位且不塌
（旧 27→2；cool=4→18/占位0；cool=0 占位23；cool=8 过并→5）。⑦ 新增 gzip 检查点 `base`/pre-settle `inc_edited`/`reb`（可零
重建重放）。⑧ **seed2 题库 v2**：detail 编辑改「枣木拐杖→梨木拐杖」、反转 5→15（5 类×3，锚点 15/15 通过）。
**seed2 3×8 正式轮（cool=4；`out/seed2_r3_8arm_cool4`，tree 重跑 + full/flat/raptor 复用）**：追加式延续反转
12–16/45、复述残留 39–89%；tree 增量反转 41(warm)/41(cold)≈reb 42、复述 100%/86% > reb 71%、残留 0%。
→ 旧"tree 复述低"是冷却假象；**编辑失效机制（原地失效 vs 追加）的判别在修好冷却后成立**。**ahi-act 扩题已完成**
（11→17 反转，5 类 ×3 全齐；+8 不变/16 远端 = 41 题；新增第 3 删除编辑；评分器级锚点校验 bad=0）。

**格式公平 + A/B（2026-10-01 下午）**：⑨ append 臂（full/flat/raptor）的编辑结果改**逐次工具调用的 function-calling 块**
（`editstorm.tool_block_units`）——旧平铺 dump 使 full 反转仅 12/45（假象）；改后 full 12→41、flat 15→21、raptor 16→23，
**反转轴 `inc_tree≈inc_full≈reb`，tree 独有优势落在复述轴（100%/0%）**。⑩ `TreeArm.edit_signal` 开关 + `--edit-signal`
`{on,off}` + `--edit-signal-ab`（额外障碍臂；run_ahi.sh 已挂）；`ab_edit_signal.py` matched-pair A/B：
**复述轴 signal 一致更好**（warm 100 vs 86、cold 81 vs 71），Q/A 冷树 signal 更好（103 vs 97）、热树略差（101 vs 108）
→ 主张定为"编辑作使用信号利于**全局巩固**，Q/A 是热冷权衡"。⑪ `replay_trees.py` 零重建重放（ahi tree 16k/retr20 只 5 分钟）。
**正式两表 + 结论 + 数据位置见 `realtest/editbench/REPORT.md`。测试 267（editstorm 13）。**

---

### 4.15 占位透明修复 + bench 并行化 + 人格保持实验设计（2026-10-02）

**A. 占位卡透明（机制修复，已提交 `2947cbf`）**
- 问题：占位卡（`placeholder=1`）与 `value=None` 的干净节点被渲染成"【占位：概要暂缺】"，且占位卡参与
  **注意力窗口 / merge / 召回路径 boost / shadow**，污染视野与机制资源（实测 ahi warm 树 `N4225` 即 `value=None` 未生成摘要节点）。
- 修复：`placeholder_transparent`（AGENTS §4.6/§11，默认 on）——渲染递归展开子卡；注意力窗口排名 /
  `merge_pressure` / `_find_merge_runs` / 召回路径 boost+输出 / `_inject_shadows` / `_shadow_slot` 全部跳过占位卡。
- **收益（ahi 树臂复用旧树重跑，只换新代码）**：Q/A 三树臂 +7~11 点；`inc_tree` 反转 52→**70/85 追平 `inc_full`**；
  复述无实质回退（一遍离群）。→ 旧"ahi 反转不追平"主要是**占位污染假象**（详见 `realtest/editbench/AHI_ACT_REVIEW.md §5.6`）。
  > **⚠ 2026-10-04 更正**：该 rerun 用 `replay_trees.py`（CLI `--retr-budget 20`）对比 `ahi_r5`（`docbench` 实跑
  > retr=6，CLI 被 config 覆盖）——**+7~11 点绝大部分是 retr 6→20**，不是占位透明。同代码 base 树 2×2：
  > 透明 ≈ +1、retr ≈ +13。见 §4.16 与 `EXPERIMENT_REPORT.md §6.5`。
- 测试：`tests/test_assembly.py` 更新 2 + 新增 1，**268 全绿**。

**B. bench 四结构并发（方案 B，已提交 `924c3e3`）**
- `docbench` Phase1/3/4 原为串行循环（唯一并发 = 单棵树的蒸馏线程池）。四结构**互不共享状态** → 拆 4 进程并发
  （`run_parallel.sh`）+ 合并（`merge_results.py`）；`run_ahi.sh` 改为并发 wrapper、新增 `run_seed2.sh`，输出 `*_par/`。
- ahi 统一口径产物 `realtest/editbench/out/ahi_r5_combined/`（旧 full/flat/raptor + 新 tree；占位只影响 tree）。
- ahi A/B（旧占位行为）：暖树 signal 复述更好（64>44）、冷树更差（20<30）、Q/A obstacle 略好 → **热冷权衡**
  （`AHI_ACT_REVIEW §5.7`）。

**C. 下一步实验：人格保持的架构对比（设计定稿，SPEC = `ahi-bench/PERSONA_RETENTION.md` v0.1 草案）**
- 目标：固定模型/协议/人格集，只变记忆架构（`tree_v2` vs `rolling+flat RAG`），测**人格淡出后**的保持/趋同。
- 编排 **2×3×3**：每次 2 实例（**臂同质、互不可见**）× 3 agent（3 份不同人格）；3 run（seed 42/43/44）= 18 agent-life；
  实例内 3 同臂 agent 互聊 + 实验员注入话题。
- 人格（旧 ahi-multi `config.json.benchoff` 性格段，**解耦互指**）：**林深（理性）/ 墨白（诗意）/ 澜（健谈）**。
- 淡出：round 40 **全员**淡出；tree 把人格局写入树（`source=persona`）、baseline 折进 rolling 被压缩——
  **这是 treatment，必须显式声明**（趋势同 = 人格丢失的可证伪代理）。
- 模型：被测 `deepseek-v4-flash`（= deepseek-flash）**关思考**、temp 0.7；judge `deepseek-v4-pro` **开思考**（effort 默认）、temp 0。
- 主判据（预注册）：臂内 3 人格两两**趋同斜率 `tree < baseline`**；judge **人格遵从率 `tree > baseline`**。
- **待实现**：`FlatRAGAgent`（rolling + bge flat 检索 top_k=6 + 每轮 auto-preretrieve）；provision 支持
  `architecture: flat_rag` + 每 agent 独立 persona + 2 实例编组；`config/persona_retention.json`；
  score/analyze 加趋同斜率 + judge 遵从率；smoke → 3 run。**详见 SPEC §10**。

### 4.16 Q/A bench 韧性臂：强模型（思考）/ 强向量（bge-large）+ retr 混淆更正（2026-10-04）

**目的**：封堵 Paper 1 两条审稿攻击线——①「full/append-only 的编辑后过时是 flash 太弱，强模型就能
in-context 改对」；②「flat/tree 结论被小向量模型 bge-small 拖累」。控制变量 = 答案侧深度思考 / 向量模型规模。
ahi（147k）× 41 题 × 5 遍，temp 0.7，retr20，judge on，`deepseek-v4-flash`（蒸馏侧不开思考）。完整表与命令
见 `realtest/editbench/EXPERIMENT_REPORT.md §6`。

**结果（Q/A 总/反转；复述 覆盖/残留）**

| 条件 | P1 | inc | reb | inc 复述 |
|---|---|---|---|---|
| full no-think（存档） | 196/反78 | 179/反70 | 195/反76 | 24%/12% |
| **full THINK** | 202/反83 | **198/反79** | 200/反82 | 84%/34% |
| flat no-think | 181/反68 | 178/反65 | 172/反59 | 36%/20% |
| flat bge-large | 181/反71 | 176/反61 | 174/反64 | 0%*/8% |
| **flat THINK** | 187/反74 | **189/反76** | 188/反73 | 40%/26% |
| tree no-think | 171/反64 | 182/**反84** | 197/反82 | 64%/**0%** |
| tree bge-large | 167/反62 | 178/反74 | 185/反80 | 58%/**0%** |
| **tree THINK** | 177/反69 | 182/反80 | 186/反81 | 80%/**0%** |

**结论**：
1. **模型（思考）能提**：召回（三结构均匀 +6）、append-only Q/A（full +19、flat +11）；
   **提不动 tree 暖 inc**（182 持平、反转略降 84→80）。
2. **结构能提、模型提不了**：编辑后复述**零残留仅 tree（恒 0%）**；full/flat 无论是否思考都残留 20–34%
   （full 开思考后 12%→34%：更长更完整的复述把 append-only 旧内容也带出）。
3. **强向量 bge-large 全层面无增益**（tree 暖 inc −4、flat −2；仅 tree cold +8）。
4. 分工一句话：**召回/理解靠模型，编辑一致性靠结构**。tree 独有优势（复述零残留 + 编辑正确性 + 作答 token ≈ full 1/10）非弱模型假象。

**代码改动（未提交）**：`realengine._load_encoder` 支持 `WATERCYCLE_EMBED_MODEL`（指定即不静默退化）；
`llmclient.DeepSeekClient` 加 `thinking` 显式开关 + `DEEPSEEK_READ_TIMEOUT`；`docbench.py` 加
`--answer-thinking/--distill-thinking`、**修复 `--retr-budget/--budget` 被 config 静默覆盖的 bug**、进度埋点；
`tests/` **303 绿**。

**⚠ retr 混淆更正（含 §4.15 A）**：`docbench` 原 `cfg.get("retrieval_budget", args.retr_budget)` 使 config 的 6
恒赢 CLI 的 20 → `ahi_r5`/`ahi_r5_par` 实跑 retr=6，而 `replay_trees.py` 用 20 → §4.15A 的「占位透明 +7~11」
是 **retr 混淆**（同代码 base 树 2×2：透明 ≈+1、retr ≈+13）。本次全部用 retr20 + 当天同代码基线
（存档基线已随代码漂移：存档 tree inc 174/反70 vs 当天 R0 182/反84）。产物 `out/ahi_strong/*`。

---

## 5. 注意事项（2026-08-13 架构审查结论——非 bug，但下阶段需留意）

1. **轨道B 未进机制层**：`nb_set/nb_render` 只在 realengine.py（应用引擎），core.py 桩引擎没有 notebook → §8.1 属性测试覆盖不到轨道B，换应用（如聊天）要重写。**建议下一步迁移进 core.py**。
2. **`span_atoms` / `expand_leaves` 遍历已统一**（2026-09-27）：唯一遍历入口 = `core.Engine.node_atoms`，
   截断为共享 `_cap_atoms`；两者仅上限/脏卡语义不同（span 不跳脏、expand 跳脏），调用点已改为单次调用。
3. **`attention_out` 三职责耦合**：同时是 ① 加速衰减线 ② force 合并线 ③ 计数口径。调它 = 同时改三处行为，无法单独调优。建议解耦或文档显式声明。
4. **force 合并成本**：蒸馏 0.5→2.9 次/轮。需要 metrics 增加"蒸馏成本/千字"观测，并考虑 force 节流（如每 N 轮最多一次）。
5. **注意力排序 O(n log n)/tick**：未在 30 万句压力规模验证。规模上要剪枝（原子数>cap 才排序）。
6. **参数台账已与代码逐项对齐（2026-09-27）**：§11 现为代码实况（含孤儿参数入册、幽灵参数删除）；
   但**数值仍多为先拍值**，仅 `find_similar_threshold=0.8` 校准完成。校准流程（§8.3）未执行。
7. **窗外滞留现象**：pressure_threshold=2.0 下窗外原子可能滞留铺陈 80 个左右（< 阈值不触发合并但已占预算）——观测数据表明"窗外计数按铺陈口径"后数字合理，但滞留未消。
8. **模型选择**：deepseek-chat 会挂起（实测轮 1 就卡死），**改用 deepseek-v4-flash 正常**（~2-15s/轮）。config.json 已改。
9. **向量模型离线加载**：realengine 已强制 `HF_HUB_OFFLINE=1` + `local_files_only=True`（本地缓存，2s 加载）。每次联网检查更新会挂死。
10. **span 工具从未被模型主动使用**（v9-v11 全是 0 次）——叶子展开自动化后 [span] 的存废待评估；
   2026-09-27 已把 [span] 改为直接使用 `span_atoms` 返回（不再二次遍历），但**仍无单测/无运行计数**（§9.11 待补）。
11. **长上下文漂移是当前最大质量瓶颈**：修复方向 = 轨道B 强化（初始化预置状态条目 + [note schedule] 伏笔账本 + plan 实体强制检查），见 §6 路线图。
12. **共现层带动选择瓶颈（chatbench 结论）**：概括节点聚类正确，但按 `conn_weight` top-k 带出的成员与
    "问题需要哪条证据"错配，T3 引用∩hub 全 0。可试方向：只带"与直接命中卡有共现边"的交集成员（不退回相似度）、
    抬高 θ/N 拒绝大杂烩簇。
13. **评测口径易被答案风格污染**：T2 证据指标受答案长度/引用密度影响（全量发送写长报告少引用 → 覆盖被压低）。
    建议：统一引用语法已成硬约束；继续保留"引用有效/引号属原msg/引号可验(format-agnostic)/引/千字"分列，
    不合成单一分数；对照人类分析的判定需人工抽检分歧样本。
14. **compare.py 的两处协议修复未重跑**（见 §4.7）；`COMPARE_REPORT.md` 已标注**作废存档**（原把已作废的
    gemma 数据标为 deepseek），任何四臂结论在重跑评分前都不可引用。
15. **评分任务易被杀**：`score_compare.py` 已增量写盘；后台长任务避免只依赖 `nohup &`（abort 工具调用会杀子进程），
    务必核对 `cmp_score.json` 的 `_meta.updated_at/partial` 再读数。
16. **组装 per 工具调用的成本教训（2026-09-25 实测）**：组装纯计算 ~1ms/次（非瓶颈）；成本 = loop 调用次数 ×
    每次调用输入体量（旧回执串 ~30 token → 全组装 ~10-13k token）。行动派 agent（每轮 17-21 次工具迭代）单轮 90-130s。
    连续唤醒（loop 重排 pending）消除积压后不再阻塞；若再超，候选旋钮 = loop 组装独立预算（未启用）。
17. **平台中立原则（2026-09-25 拍定）**：任务行/待回复清单必须是事实陈述，禁止"可继续未完成事项"式正性行动邀请——
    它与"看到自己发过、无回合状态"叠加是重复发送的自然吸引子（实测 41 对重复：29 同轮双发 + 8 跨轮）。未完成事项
    应经检索随扰动源自然浮现，不靠空轮指令。（已收录 AGENTS.md §4.5.9。）
18. **judge 关思考的两处坑（2026-09-25 实测）**：① OpenAI SDK 的 `extra_body` 会被合并进请求体顶层，原始 HTTP 必须
    直接放顶层 `"thinking":{"type":"disabled"}`——嵌套进 `extra_body` 键会被 API 忽略（实测 judge 每判烧 2460 reasoning
    token）；② 实验 agent 走 `make_client`（`thinking_budget=0` 默认关），基线 agent 走 `model_config.extra_body`，两边都要查。
19. **events 文件跨 run 追加（已缓解）**：`driver.py` 初始化即清空本 run 的 timeline/events；`qa.py` 答案文件、
    `score.py` crown_judgments 改为 `"w"` 重写——消除 append 跨 run 污染（原事故：quiz 口径被旧轮事件腰斩）。
    仍建议每次正式 run 前核对产物目录。
20. **agent 挂载点必须改 provision 源文件（2026-09-26 教训）**：`provision.py` 从
    `ahi-multi/agents/{lan,neutral}/agent.py` 复制生成 bench 副本——任何 agent 级改动（如使用信号层
    挂载点）必须同步进源文件，否则下次 provision 静默覆盖；检查法 = provision 后比对 md5。
21. **score.py 两处时间戳边界（2026-09-26 发现并已修，SPEC v0.2）**：① return 时刻的 digest 与
    返回通知同秒落库 → 原被计入 `g1_sys_to_absent`；② 返回通知落库可能早于 driver `absent_end`
    1–2 秒 → 原 `g2_return_meta` 误判缺失。修正：G1 按 `msg_type=digest` 排除返程聚合、G2 放宽
    ±5s 容差（与 SPEC 意图对齐，非隔离违规，leak_receipts 全 0 未变）。新增 `--gates-only`：
    确定性重算 gate 并覆盖 `score_<mode>.json`（保留 crown/quiz/memory，零 LLM）——本轮复评
    `gates_pass`。跨 run 对比 gate 前仍先看 `g1_leak_receipts`。

---

## 6. 下一步路线图（聊天智能体阶段）

### 6.1 本阶段目标（聊天智能体测试）
把记忆树机制应用到"聊天智能体"场景：对话历史/用户资料/长期偏好作为参考资料流，跨会话保持一致性。新开对话进行，本分支（chat）为新工作分支。

### 6.2 待办（按优先级）

> 2026-08-30 更新：聊天方向的**缺席实验（冷/热两轮）已收官**（皇冠预言方向成立，详见 ahi-multi/HANDOFF §四.13）；**merge 条件式触发 + 节点窗口扩展已完成**（190 测试全绿）。以下表格保留历史路线图，其中与聊天方向相关的 P0/P1 在 ARCHIVE_CHAT.md 定论后降级，下一阶段聚焦写作/代码三场景。

| 优先 | 任务 | 验收标准 | 关联 |
|---|---|---|---|
| P0 | **人格保持实验（PERSONA_RETENTION，2026-10-02 设计定稿）**：实现 `FlatRAGAgent` + provision 2 实例×3 agent + `config/persona_retention.json` + score/analyze 趋同斜率&judge 遵从率 → smoke → 3 run | SPEC §7 判据出数（`slope_tree<slope_baseline`、`adhere_tree>adhere_baseline`）；**两臂分群** | `ahi-bench/PERSONA_RETENTION.md` |
| P0 | 聊天应用层：新协议 + 终端对话 **基本完成**（chat_agent.py，2026-08-14）| 端到端跑通对话；跨轮回溯正确 | 新模块 |
| P0 | **合并 span_atoms/expand_leaves** | 单一 API + 测试迁移 | §5.2 |
| P0 | **轨道B 迁移进 core.py**（notebook 进机制层 + 桩测试覆盖）| 属性测试覆盖轨道B；聊天/小说共用 | §5.1 |
| P0 | **长会话一致性基准**（聊天场景度量：跨会话召回正确率）| 脚本化事实注入 + 延迟提问评分 | §6.3.4 |
| P1 | 蒸馏成本观测 + force 节流 | metrics 有成本/千字；蒸馏次/轮回落 | §5.4 |
| P1 | 聊天协议暴露 [edit]/[rollback] + [plan] 提示词补全 | AI 可精确改口/撤销 | 审查结论 |
| P1 | 参数校准（§8.3）：注意力 4 参 + force 2 参 + 检索权重 + NB_WORKLOG_CHARS | 台账更新 + 依据 | AGENTS.md §11 |
| P2 | 索引化：archive 区间索引 + order-maintenance（50 万字支撑）| 30 万句 write < 1ms | core.py |
| P2 | 跨进程持久化（SQLite 化）+ 会话中断恢复测试 | 重启后记忆不丢 | core.py |
| P1 | **代码场景正式实验**（下轮）：harness `--session` 跨会话任务 + 记忆组顺序跑共享引擎 | T1-T4 作基线能力基准；跨会话任务显记忆树优势；verbatim 精确引用出数 | codeagent/ |
| P0 | **四臂重跑**（compare.py 两处修复后：交付预算 + 按题清空）+ 每臂重复 3 次 | 均值±方差；在此之前四臂结论不可引用 | §4.7 |
| P1 | **共现层带动选择改进**：concept 匹配后只带"与直接命中卡有共现边"的成员（不退回相似度） | T3 引用∩hub > 0；on/off 出现稳定增量 | §5.12 |
| P1 | **给共现找真实场景**：构造 direct 结构性不可达、只能靠跨区共现的任务 | 增量可归因、非 direct 冗余 | §5.12 |
| P2 | chatbench T1 补题（ctx 语料）+ 题库预注册/留出集 | dev/test 分离；评分可复现 | chatbench/ |
| P2 | 注意力排序剪枝 + 30 万句压力验证 | 压测数字补齐 | §5.5 |
| P1 | **ahi-bench 三档冷轮 + cooc on 臂**（对照本轮无 cooc 数据）| 跨档 crown 可分性判定（SPEC §6）；cooc on/off 增量 | §4.8 |
| P3 | 可视化同步新机制；AGENTS.md 沉淀聊天场景决策（含平台中立原则 §5.17）| — | visualize.html |

### 6.3 聊天智能体测试的设计要点（部分已实现，见 §1 时间线）

已拍定（2026-08-14，`chat` 分支）：
1. 参照 run2 的"预检索 → 组装 → 回复"循环；对话输入即扰动源（§4.7）。
2. 轨道B 在聊天场景 = 用户事实/偏好/承诺（state）+ 交互规则（constraint）+ 待办承诺（schedule）+ 工作记录（worklog，固定 key 覆盖去重）。
3. 组装顺序（§10 聊天拍定）：系统指令 → 轨道A 卡片流 → 轨道B 笔记本（去重发送）→ 回执 → 当前输入。
4. 度量：跨会话召回用户早先陈述的正确率（对标小说场景的复述覆盖）。
5. 多介质切分：chat 按句 / code 按 AST（函数一张、类=头+`__init__`/`__del__` 一张、docstring 提取进元数据）/ config 按项 / md 按标题+列表项；统一原子上限 2000 字符，超限行块再切；大卡 `weight=2`（只影响注意力计数与衰减，不影响读）。
6. 来源标注：`Atom.metadata.source`（user:xxx / ai / tool / file:path），仅蒸馏与组装渲染可见，不参与检索。
7. 重复输入检测：新句 bigram Jaccard ≥ 0.8 命中存活卡 → 命中卡活一轮影子（"之前也听过"）。
8. DISMISS 懒标记（AGENTS.md §5）：`[dismiss]描述[/dismiss]` 挂起 → 每轮预检索追踪 + 〔待失效〕标注 → `[dismiss #id]` 墓碑确认（可回滚）/ `[dismiss cancel]`。
9. 文件编辑确定性路径：`file_edit` 卡级 diff（与介质切分粒度一致）精确墓碑被改部分，未改部分保留；未读过 → 直接灌入。

---

## 7. 快速上手

```bash
# 桩测试（190 全绿）
python3 -m pytest tests -q

# 聊天智能体（chat 分支主力；默认 ollama gemma4:12b，慢可切 deepseek）
python3 realtest/chat_agent.py --provider deepseek [-v]

# 结构化压测（真实 LLM，~20 分钟；--seed-dir 选种子目录！）
python3 realtest/bench_qa.py --seed-dir realtest/seed2 --minutes 22 \
    --facts-1a 25 --facts-1b 10 --edits 50 --phase3-cycles 5 \
    --out realtest/out/bench_xxx --seed 42 --ingest-recall-p 0.25

# 无尽续写 v2（注意力窗口实验；stdout 实时 + out/run2_xxx/run.log）
python3 realtest/run2.py --minutes 12 --max-words 18000 --out realtest/out/run2_v13

# 规模压力测试（纯桩）
python3 stress_write.py --scales 10000,50000,100000,300000

# ── 聊天数据记忆 bench（chatbench，2026-09）──
# 1) 提取语料（ctx_run：青/蓝/墨/白；absence：澜/林深/中性）
python3 realtest/chatbench/extract_corpus.py --preset ctx --out realtest/chatbench/corpus
python3 realtest/chatbench/extract_corpus.py --db ahi-multi/backup_day_20260829/data/system.db \
    --preset absence --include-users --session-window-minutes 30 --out realtest/chatbench/corpus_absence
# 2) 建树（一次，缓存）+ 单臂回放（on/off）
python3 realtest/chatbench/replay.py --corpus realtest/chatbench/corpus/corpus.jsonl \
    --questions realtest/chatbench/questions_ctx.json --cooc off --temp 0 \
    --save-tree realtest/chatbench/out/tree_ctx.json.gz --out realtest/chatbench/out/ctx_off
python3 realtest/chatbench/replay.py --corpus realtest/chatbench/corpus/corpus.jsonl \
    --questions realtest/chatbench/questions_ctx.json --cooc on --temp 0 \
    --load-tree realtest/chatbench/out/tree_ctx.json.gz --usage-passes 4 --out realtest/chatbench/out/ctx_on
# 3) 评分（judge=deepseek-flash+thinking）
python3 realtest/chatbench/score.py --off .../ctx_off/replay_result.json --on .../ctx_on/replay_result.json \
    --questions realtest/chatbench/questions_ctx.json --reference realtest/chatbench/reference_claims.json \
    --out realtest/chatbench/out/ctx_score.json
# 4) 四臂对比（tree_off/tree_on/raptor/fullsend）+ 统一评分（逐臂增量写盘）
python3 realtest/chatbench/compare.py --arm tree_off --load-tree realtest/chatbench/out/tree_ctx.json.gz \
    --out realtest/chatbench/out/cmp_tree_off
python3 realtest/chatbench/score_compare.py    # 读 out/cmp_*/replay_result.json
# 5) 上下文使用长度
python3 realtest/chatbench/measure_ctx.py

# ── 使用信号层（§4.9 / realtest/USAGE_SPEC.md）──
# 桩测试（含 test_usage 13 项）
python3 -m pytest tests/test_usage.py -q
# Phase A 离线影子重放（边冗余率 + 答案分档；纯离线零 LLM）
python3 realtest/chatbench/usage_replay.py --out realtest/chatbench/out/usage_analysis
# live 影子采集：agent config 设 "usage_shadow": true → 产物 <agent>/data/usage/*.jsonl

# ── ahi-bench M0+quiz variant（2026-09-25 正式冷轮；无 cooc 对照组）──
python3 ahi-bench/preflight.py --disable            # 禁用旧 agent（防 6 agent 同窗）
python3 ahi-multi/main.py                           # 起平台（ahi-multi/ 下）
python3 ahi-bench/provision.py --config ahi-bench/config/m0_corpus.json \
    --tier deepseek-v4-flash --run-id deepseek-v4-flash
python3 ahi-bench/driver.py --config ahi-bench/config/m0_corpus.json \
    --tier deepseek-v4-flash --mode cold             # ~75min；smoke 用 --tier deepseek-smoke + --preheat 2 --absence 1 --observe 2
curl -X POST "http://127.0.0.1:8080/api/v1/agents/bench-a/stop?level=1"  # ×3：level=1 才触发 on_stop 存 tree_final
python3 ahi-bench/score.py --config ahi-bench/config/m0_corpus.json --tier deepseek-v4-flash --mode cold
python3 ahi-bench/qa.py --config ahi-bench/config/m0_corpus.json --tier deepseek-v4-flash --arms base,recall
python3 ahi-bench/cooc_analysis.py                  # 离线共现三层分析
python3 ahi-bench/tree_qa.py --tree out/deepseek-v4-flash/trees/bench-a_tree_final.json.gz --facts realtest/seed2/facts.md

# 可视化（浏览器直开）
open visualize.html
```

**关键约定**：
- DeepSeek key 在 `realtest/config.json`（**model 必须用 deepseek-v4-flash**，deepseek-chat 会挂起）；默认 provider 已切 ollama（gemma4:12b 本地）
- 向量模型本地缓存 + 强制离线（realengine.py 已固化）
- 种子：`realtest/seed/`（旧 3k）与 `realtest/seed2/`（新 12k，《缠枝莲》）
- 运行产物：`realtest/out/*/`（metrics.jsonl / tree_snapshots.jsonl / responses.txt / story_full.txt / notebook.json / run.log / report.html）
- 后台运行 + 实时看日志：`(python3 realtest/run2.py ... > /tmp/run2.log 2>&1 &) && tail -f /tmp/run2.log`

---

## 8. 给接手者的四条提醒

1. **先读 AGENTS.md**——唯一设计权威；任何偏离先与用户确认（铁律 1）。
2. **bench_qa / run2 / chatbench 是主力验证工具**——改任何检索/组装/蒸馏/注意力逻辑，同配置重跑对比，别只看单次数字（模型有随机性）。
3. **压缩/一致性的度量工具已齐**（复述覆盖 + 事实命中 + 上下文组成 + 蒸馏成本 + chatbench 三层的引用核验）——任何改动先立基线再改，避免拍脑袋；参数改动记入 §11 台账。
4. **动手前先读 §4.7 状态**：四臂对比的两处协议 bug 已修但**未重跑**，当前 `out/cmp_*` 的结论不可引用；
   重跑评分后先看 `cmp_score.json` 的 `_meta.updated_at / partial / answerer_model`（旧 gemma 结果存于
   `v1_cmp_*/v2_gemma_cmp_*`；`COMPARE_REPORT.md` 已标注作废）。
   本轮实验备份：`backups/20260925_chatbench/`（tar.gz + MANIFEST）。

---

## 9. 本会话交接（2026-10-03 · 人格锚点检索化）

**一句话**：把 M-PR tree 臂的人格锚点从「最近 6 句 recency 池 + 8 字段快照」改为
「**检索自身材料 top-k + 自由文本快照 + 【注】不渲染**」；本会话**只做冒烟 + 小重放，未跑正式轮**。

### 9.1 版本（git）
- `74a1236` —— **pr2 基线**（recency-6 + 8 字段快照）；`out/.../pr2` 数据由此产生。
- `edaae69` —— 检索化（检索锚点 + 自由文本快照 + 【注】不渲染）。
- `92c05d4` —— 锚点去脚手架前缀（`anchor_clean`）。
- ⚠ `realtest/memcurve/memcurve.py` 的改动**是其他会话的**，本会话未触碰。

### 9.2 改了什么
- `realengine.py`：`retrieve_style_samples`（池 = `source=="ai"` 原话原子 + 摘要节点**整卡**含【注】；
  bigram 粗筛 + embedding 精排；`exclude_ids` 排除当前蒸馏区间防自引用）、`expand_self_atoms`
  （节点→其区间内自身原话，cap 控成本）、`anchor_clean`（剥〔来源/早期/索引〕脚手架）；
  `render_emission` 卡片只渲染【事实】（保留〔另有X条〕索引），【注】不渲染；`_summarize` 把
  `children` 透传 `style_provider(content, children)`（兼容 1/0 参旧式）。
- `lan/neutral/agent.py`：删 `_self_samples` recency-6 池与 8 字段 EMA；加查询缓冲 `_anchor_query_buf`
  + 自由文本快照（检索重生成、长度上限）；参数 `anchor_top_k=3` / `anchor_expand_self_cap=0` /
  `snapshot_max_chars=300` / `anchor_query_buf_cap=4000`；`α>0` 即开锚点（快照段由 `snapshot_every>0` 门控）。
- 文档：`AGENTS.md §4.4/§11`、`ahi-bench/PERSONA_RETENTION.md v0.3 §4.1`、`ahi-multi/HANDOFF.md §2b`、`ahi-bench/README.md`。

### 9.3 已验证
- `pytest` **278 绿**（新增 `tests/test_anchor_retrieval.py` 10 项）。
- **冒烟** `out/.../pr3smoke`（tree-s42+flat-s42，30 轮，interval=4，tree `snapshot_every` 临时改 3）：
  2/2 完成、零 error、无残留；`snapshot_chars` 196/240（自由文本、<300）、`style_on=True`、组装中【注】出现 **0** 次。
- **小重放**（冻结 pr2 树，25 条真实蒸馏查询；笔记见 `out/.../pr3smoke/SMOKE_REPLAY_NOTE.md`）：
  查询相关度 **0.567 vs 旧 0.017**；共享 bigram 密度（跨≥2 agent）**0.600 vs 0.866**；
  但**跨 agent 内容级共享密度基本不变（0.229→0.231）**——**去前缀 ≠ 去共享词**。

### 9.4 下一步（下个会话）
1. **跑正式轮**（新命名空间 `pr3`；**pr3smoke 是临时冒烟，勿当正式数据**）：
   ```bash
   python3 ahi-bench/provision.py --config ahi-bench/config/persona_retention.json \
       --tier deepseek-v4-flash --run-id pr3
   python3 ahi-bench/pr_orchestrate.py all \
       --manifest ahi-bench/out/deepseek-v4-flash/pr3/manifest.json --rounds 120   # 正式可 180
   python3 ahi-bench/pr_score.py \
       --manifest ahi-bench/out/deepseek-v4-flash/pr3/manifest.json --judge-cap 40
   ```
   对照：`pr`（中性 tree）/`pr2`（recency-6）保留，横向比趋同斜率 / 【注】own−common 斜率 / 共享套语计数 / judge 遵从率。
2. **已知残余风险（用户知情、预期内）**：① 节点回灌 = 自蒸馏反馈环；② 快照查询 = 累积蒸馏内容，可能把共享话题捞回；③ 检索偏向长摘要整卡（脚手架已清，但 `【事实】/【注】` 标签仍在）；④ 无跨 agent 去重层（用户明确不做）。
3. **可选后续**：节点锚点是否也去 `【事实】/【注】` 标签；锚点偏好短文本/原子 vs 长整卡。

### 9.5 环境/约定
- 端口 8080–8130；长跑加 `caffeinate -i` 防睡眠；judge = `deepseek-v4-pro` + thinking（顶层启用）。
- `ahi-bench/config/persona_retention.json` 含真实 key（gitignored），example 为空 key。

---

## 10. 本会话交接（2026-10-03 · 蒸馏源渲染收口 + 〔早期〕新语义）

**一句话**：删掉无效的 HP 判据〔早期〕，改为"与失效参照集匹配"；蒸馏器与主模型统一**干净分段渲染**（`▸〔来源〕`+原文），prompt 只留中立说明；`【注】` 允许进上层蒸馏；删除 `〔另有N条〕` 索引行。

### 10.1 改动
- `realengine.py`：
  - `SUMMARIZE_PROMPT` 删"来源标注规则""时序分流规则"；`SUMMARIZE_FACT_NEUTRAL/STYLED`、`SUMMARIZE_NOTE_CLAUSE` 去来源措辞。
  - 删 `_atom_part`；`_group_parts` 改为**分段渲染**（原子 `▸〔来源〕` 段 + 原文，无逐句标签；节点取【事实】+【注】= option 2）。
  - 新增 `_invalidated_refs` / `_is_early`：`〔早期〕` = 内容与「墓碑原子 + 脏节点旧文本」bigram Jaccard ≥ `early_match_threshold`(0.5) → 可能无效/历史态；置脏机制不变。
  - 删 `_uncovered_count` 索引行。
  - 新增 engine 参数 `early_match_threshold`（§11 已入册）。
  - **内存修复**：新增 `_ensure_node_vecs` / `_ensure_atom_vecs`（`EMBED_BATCH=32`），`_retrieval_pool`/`retrieve_style_samples` 的向量编码由**逐条**改为**单次批量**——逐条 `enc.encode` 在 MPS 上堆积幽灵内存（实测同一批 316 条：逐条 +460MB vs 批量 +69MB）。
- 测试：`test_realengine_budget`（B+D 块重写为新早期/分段 + 2 项批量编码回归）、`test_coloring_v3`（删索引测试、option2 注上送）、`test_anchor_retrieval`（渲染去索引）；`FakeEnc`（hybrid/selforg/usage）改为支持批量 + `batch_size`。
- 文档：`AGENTS §4.4/§11`、`PERSONA_RETENTION §4.1`、`ahi-bench/README`。

### 10.2 验证
- `pytest` **278 绿**。
- 端到端 smoke（合成）：prompt 不含脚手架规则；蒸馏输入 `▸〔user:probe〕…`/`▸〔ai〕〔早期〕…`；与墓碑同内容的原子判早期。
- **内存实证**：200×`retrieve_style_samples` → **608MB**（修复前 1001MB）；100×全节点 churn（316 节点版本号轮番失效重编码）→ **稳定 616MB 不增长**（修复前此法会持续堆积至 2GB 看门狗）。
- **未跑正式实验**。

### 10.3 待办/风险
- `early_match_threshold=0.5` 先拍，未校准（相似度口径 = bigram Jaccard、参照集最近 300 条、粒度 = 原子）。
- option 2（注上送上层）仅 smoke 验证；是否引入事实噪声/加剧风格复利待观察。
- 人格锚点检索化（§9）**未变**：`retrieve_style_samples` 仍取节点整卡（含【注】）、未按来源过滤——**跨 agent 泄漏与锚点回灌风险仍在**（见 `pr3/ANALYSIS_summary_anchor.md §6`）；这是趋同主因，本轮未修。
- `on_start` 仍**不加载树**（重启=记忆清零）——内存修复后重启概率大降，但恢复机制仍缺。
- 代码未提交。

---

## 11. 本会话交接（2026-10-03 · 重启读取持久化 + 内存上限 4GB）

**一句话**：实现 agent 重启恢复（`on_start` 加载最新树快照 + agent 状态），并把内存看门狗上限
2048→4096MB；配套 `tree_save_every=10` 周期快照、原子写入、防空树覆盖。**已单测 + 平台重启 smoke 验证。**

### 11.1 改动
- `realtest/chatbench/tree_io.py`：`save_tree(..., agent_state=None)` 把 agent 级状态与树同文件保存
  （原子）；`load_tree` 置 `eng._restored_agent_state`；新增 `latest_tree_snapshot(save_dir, agent_id)`
  （优先周期性 `_tree_rN`（最大 N）→ 回退 `_tree_final`（mtime 最新））。
- `ahi-multi/agents/lan/agent.py` + `neutral/agent.py`（两处同步）：
  - `on_start` 读 `restore_on_start`（默认 true），在 `nb_set` 前调 `_restore_state()`：加载最新快照 +
    恢复 `_round_no` / `_persona_on`（防重启后 persona 重新注入）/ `_snapshot` / `_last_force_round` /
    `_promote_count_last` / `_anchor_query_buf`；记 `[恢复]` 日志。
  - `_save_tree`：原子写（`.tmp`→`os.replace`）+ 携带 `_agent_state()`；**空树不覆盖已有快照**（防恢复
    失败/探测启动后的 `on_stop` 清空记忆）。
- 配置：`persona_retention.json`（+example）、`m0_corpus.json`、`context_compare.json`、
  `bench_config.example.json` → `max_memory_mb: 4096`、`tree_save_every: 10`、`restore_on_start: true`。
- 测试：`tests/test_tree_io.py` 新增 agent_state roundtrip / 缺失兜底 / `latest_tree_snapshot` 选择（3 项）。
- 文档：`AGENTS §11` 新增 `restore_on_start` / `max_memory_mb` 行。

### 11.2 验证
- `pytest` **281 绿**（278 + 3）。
- 离线：加载真实 `tree_final`（pr3b tree-p1 367 原子）成功，可继续演化；旧快照无 `agent_state` → None 兜底。
- **平台重启 smoke**（pr3b tree-s42，wakeup=3600 免 LLM）：同步新 `agent.py` 到实例后重启平台，
  3 agent 均触发 `[恢复]`，加载 pr3 真实快照：p1 **2017 原子/274 节点**、p2 **2381/316**、
  p3 83/0；stop 后快照完好（防空树覆盖生效）。修复前同一操作无任何 `[恢复]`（旧代码不加载树）。
- 注意：PR 实例里的 `agent.py` 是 provision 时**拷贝**——改 `ahi-multi/agents/lan|neutral/agent.py`
  后须重新 `provision`（本次 smoke 手工拷贝，正式轮直接 provision 即带上）。

### 11.3 未决 / 风险
- **本条未消除内存增长**（仅是"重启不再清零"+"上限抬高"）：批量编码只降峰值，MPS 跨轮累积仍在，
  4GB 上限只是延后重启。3 agent×4GB=12GB，48GB 机器单实例安全；**6 实例 18 agent 全开需重估上限**。
- 锚点回灌（`retrieve_style_samples` 取节点整卡、未按来源过滤）**仍未修**——趋同主因。
- flat 臂**无对应持久化**（rolling + RAG 内存态仍随重启丢失）；若要两臂公平，需给 `FlatRAGAgent`
  加对称持久化。
- `restore_on_start` 从**同目录最新快照**恢复；跨 run 复用未清空的 agent data 会恢复旧树——PR 靠
  provision 清空命名空间兜底，M0 需按 README 手工清 agent data。
- 代码未提交。

---

## 12. 本会话交接（2026-10-03 · flat 信封对齐 + 来源用名字标注）

**背景**：发现**未受控的封装混淆**——flat 原生多轮（自己的话是 `assistant` 角色），tree 发
`system+单条 user`（自己的话是文本 `▸〔ai〕`）。模型可感知角色标记差异，违背"只比记忆机制"的公平性。

### 12.1 改动
- **flat 压成单条 user transcript**（`flat-rag/agent.py`）：新增 `flat_envelope`（PR 配置 `single_user`），
  `_call_llm_single_user` 把滚动历史（含本轮 prompt）压成**一条 user 文本**，以 `[system,user]` 两条原生消息
  发出；滚动 `messages` 仍维护（compress/RAG 不变）。
- **client 原语**（`sdk/llm_client.py`）：重构 `send_message` → 抽出 `_complete(messages)`，新增
  `complete(system,user)` 一次性调用（不改动滚动 `messages`）；`_send_native_ollama` 不再自行 append。
- **来源标签用名字**（`realengine.py`）：新增 `eng.source_labels` 映射 + `_disp_src()`，组装/蒸馏渲染把
  `ai→本 agent 名（回退 self）`、`agent:<id>→名字`；空映射 = 原样（旧行为，测试不受影响）。`anchor_clean`
  已通用剥 `〔…〕`，无需改。tree 两个 agent（lan/neutral）在 `_build_user_ctx` 每轮刷新映射。
- **提示词**：`protocol_common` 加一行说明 `▸〔X〕` 发言者约定（两臂同享）。
- 配置：`persona_retention.json`（+example）+ `context_compare.json`（+example）补说明；
  PR engine 加 `flat_envelope=single_user`；`provision.py` flat 分支透传 `flat_envelope`。
- 测试：`tests/test_render_clean.py` 加 `source_labels` 映射；新增 `tests/test_flat_envelope.py`
  （`complete` 一次性、不污染滚动；`send_message` 保持滚动）。

### 12.2 验证
- `pytest` **284 绿**（281 + 3）。
- 真实 API：`LLMClient.complete` 单次调用返回正常、`messages` 不被污染。
- **flat 集成 smoke**（`out/.../flatsmoke`，4 轮/fade@3/interval 8）：3 agent 均
  `envelope=single_user`，1/1 完成、零 error；直调 `_call_llm_single_user` 确认产出
  `[system, user]`、user 内为单条 transcript 且自己的话标 `▸〔林深〕`。

### 12.3 未决 / 风险
- **纵向可比性**：pr/pr2/pr3 均为**旧信封**（flat 原生多轮、tree `▸〔ai〕`/`▸〔user:experimenter〕`）；信封对齐后重跑才与本节同口径。
- 本轮**未**改其他实验（M0/context_compare）的 flat（无 flat 臂或未启用）；tree 的来源名字标注
  **全局生效**（lan/neutral 默认开），M0 等臂的 `▸〔ai〕` 也会变名字——如需冻结旧行为需加开关。
- **他人消息已对齐**（2026-10-03 追加）：tree 与 flat 都把用户/其他 agent 渲染成 `▸〔名字〕`（用户如
  `▸〔实验员〕`、其他 agent `▸〔墨白〕`）；flat 覆写 `_build_user_context` 去掉 `[user:xxx]/[agent:xxx]`，
  `_rag_block` 的来源也映射名字。实测 tree 组装标签为 `{实验员, 林深, 墨白, 澜}`。
- 机制标签已统一：`persona`（淡出人格块）与 `system`（系统通知）都渲染为 `▸〔system〕`（内联，非
  发言者）；`tool`（代码结果）仍为 `▸〔tool〕`。prompt 已说明 `▸〔system〕` 含义。
- 代码未提交。


---

## 13. 本会话交接（2026-10-03 · 两臂公平性全面对齐）

**背景**：信封/标签对齐后，全面审查剩余不对称项（active 工具面、唤醒外壳、上下文预算、
笔记本、max_tokens、shell 结果），逐项对齐到"只变记忆架构"。

### 13.1 改动
- **工具表面对齐**：两臂统一 `@note`/`@recall`/`@ingest`；tree 删 `@span`/`@dismiss`
  （`lan`/`neutral` 的 `_execute_ahi_command`）；`tree_tools` 与 `baseline_note` 用同一份文本
  （`persona_retention.json` + example）。
- **flat 增笔记本**（`flat-rag/agent.py`）：`_notebook` dict + `_nb_set`/`_nb_render`（constraint 恒注入 +
  state/schedule/worklog 去重 + 字符 cap 同 tree）；`on_start` 预置「交互规则」constraint；
  组装注入 `【笔记本·事实层】`。
- **flat 增工具命令**：`_execute_ahi_command` 覆写 @note（写笔记本）/@recall（`_rag_block` 检索块注入
  下一组装）/@ingest（`resolve_path` 读文件 → `_rag_add` 分块，`source=file:path`）。
- **flat 唤醒外壳对齐**：覆写 `_autonomous_loop`，去掉基类每轮「完全自主数字生命」+「输出格式/命令/
  约束」重复样板，改用 `_build_wakeup_ctx`（系统状态→上次唤醒回忆→检索→新消息→笔记本→回执→任务）。
- **flat 硬预算**：`assembly_budget`（16000）在 `_call_llm_single_user` 里硬裁单条 transcript（保留头部
  说明 + 最近历史，裁最旧）。
- **max_tokens**：flat `mc` 由 8192 → `384*1024`（=模型上限；tree 的 realtest client 本就不设）。
- **shell 结果进记忆**：flat `_execute_action`(code) 与 `_put_action`(shell_result) → `_rag_add(result[:2000],
  source="tool")`（与 tree 写树 source=tool 对称）。
- **人格块标 system**：flat fade 的 persona 块以 `_src="system"` 标记，单条 transcript 渲染成 `▸〔system〕`。
- 测试：`test_flat_envelope.py` +3（`_build_wakeup_ctx` 无自主框架/含笔记本任务；@note 写笔记本；
  @recall 注入块）。`pytest` **288 绿**。

### 13.2 验证
- `pytest 288 绿`；`py_compile` 过。
- 平台 smoke（`out/.../fairsmoke`，tree-s42+flat-s42，5 轮/fade@3/interval 8）：**2/2 完成、零 crash**；
  flat 日志 `envelope=single_user budget=16000`；tree/flat 各 21 条 timeline。
- 已知**非本改动**的启动竞态：`message_router._find_agent` 在首个消息转发时 `port=None > 0`
  报 `TypeError`（两臂对称、仅首消息，属于平台既有 bug，未在本轮处理）。

### 13.3 未决 / 风险
- **纵向可比性**：pr/pr2/pr3 为旧信封/旧工具面/无 flat 预算；对齐后需重跑才同口径。
- 剩余已知不对称（可接受/需声明）：
  ① tree 有蒸馏+注意力+merge（架构本体）；flat 无（RAG 无蒸馏）——这是被比较对象，非混淆。
  ② tree「组装 per 工具调用」连续唤醒（动作循环内重排 pending）；flat 动作循环仍是"反馈追加"——
     单条 transcript 下模型仍看到全历史+反馈，等价性接近，但严格说不是逐次重组装。
  ③ tree 的 `_build_wakeup_summary` 还保留 span/dismiss 分支（死代码，命令已删，不影响）。
  ④ flat 的 compress_middle 摘要质量（最近 6 条 80 字）与 tree 的 LLM 蒸馏不同——架构本体。
- 代码未提交。

---

## 14. 本会话交接（2026-10-03 · 消息管理对齐 + 平台/内存修复）

**背景**：pr4 s42 正式跑（120 轮）暴露三问题——① flat-p3 复读风暴（单轮内复读、121 次 LLM）；
② flat-p1 长程沉默（连续 `@exit`）；③ 内存单 agent ~75MB/min 上涨。逐一排查修复。

### 14.1 改动

**平台既有 bug**
- `message_router._find_agent`（`:477`）+ `set_wakeup_interval`（`:396`）+ `system_db.update_state`
  （`:182`）：`state.get("port", 0) > 0` 在 `online_states.port=NULL`（LEFT JOIN 无行）时
  `None > 0` 抛 `TypeError` → 首条转发消息被 `forward_to_agent` 宽 except 吞掉（丢首条，两臂对称）。
  改 `(state.get("port") or 0)`（写入侧同步 `or 0`，杜绝 NULL 落库）。

**内存（MPS 幽灵内存）**
- `realtest/realengine.py` 新增 `_empty_mps_cache()`（`torch.mps.empty_cache()`，CPU/no-op 兜底），
  `_ensure_node_vecs`/`_ensure_atom_vecs` 批量编码后调用。
- `flat-rag/agent.py` `_rag_add` 改**批量编码** + `_empty_cache()`（对称）。
- 实测 tree 单 agent 峰值约减半（40 轮 ~1GB），但**未根治**（缓涨）。

**flat 臂行为 / 消息管理（对齐 tree）**
- **R1 代码护栏**：flat 自定义 `_autonomous_loop` 漏了 `max_code_per_loop=4`（tree/neutral 都有）→ 补。
- **每次工具调用重建完整上下文**：原只把裸结果串追加进滚动 transcript；
  改为 `_build_wakeup_ctx(self._round_pending, feedback=…)`（模型每次看到完整定位）。
- **消息管理全面对齐**：
  - `_drain_sharded()`：按 `max_pending_per_loop` 分片，余量放回队首保持时间序（连续唤醒共用）；
    后接 RAG 索引（替代原"抽干全部"）。
  - `_pending_brief()`：本轮待回复清单（剔已回复信道）。
  - `_task_line()`：对齐 tree —— `"第 N 轮。本轮待回复消息见【本轮待回复清单】。"` /
    `"…本轮无待回复消息。"`（原为裸 `"第 N 轮。"`）。
  - `_round_pending` / `_round_replied_targets`：发送即标记 replied，组装时剔除。
  - loop 内连续唤醒：`_drain_sharded()` 重排 + `_round_pending.extend`。
  - `_last_user_channel`：私密消息兜底回最近用户（与 tree 对齐）。
- `provision.py` flat 分支透传 `max_code_per_loop` / `max_pending_per_loop`（与 tree 同值）。
- 移除临时调试 dump。

### 14.2 验证
- `pytest` **294 绿**（`tests/test_flat_envelope.py` 增至 7 项：`complete` 一次性 / 名字渲染 /
  已回复剔除 / **分片保留余量** / **清单+任务行** / **mock LLM 端到端驱动 `_autonomous_loop` 断言
  上下文含清单与对齐任务行** / 私密兜底）。
- **本地模型 smoke**（ollama `glm-4.7-flash`，`out/ollama-smoke/prsmoke`，flat-s42，10 轮，零 API）：
  平台 **0 error**，三 agent 正常出消息；flat 配置与 tree 同值（`max_pending_per_loop=3` /
  `max_code_per_loop=4` / `envelope=single_user` / `budget=16000`）。（冒烟的 `base_shell` SyntaxError
  是弱模型把 `@note` 写进代码块，模型行为非机制。）

### 14.3 诊断结论（flat-p1 沉默，供后续）
- **pending 每轮确实注入**（代码路径 + 实测 dump 确认）。
- 根因是**软性叠加**：flat 滚动 transcript 把模型原始回复（含 `@exit`/`@send-to`/```txt```）与
  **每轮整个 ctx** 都堆进历史 → 单轮 user 膨胀到 **90KB–1.45MB**（截 16k 尾），叠加 `_task_line`
  裸、wakeup 与 driver 注入相位不齐时"无 pending"，模型成片 `@exit`。
- 对照：tree 臂无状态组装不受影响（同 persona 的 tree-p1 全程活跃）。合成本地 A/B（含 20 次
  `@exit` 历史）实测模型仍正常回复 → 不是单纯 `@exit` 吸引子。

### 14.4 未决 / 风险
- **仅做 C + 消息管理对齐**；未做 **A（历史只存实际发出的消息、剥控制符）** 与
  **B（主动界住 transcript 增长）**——transcript 膨胀仍在，长跑仍可能诱发沉默/退化。
- **MPS 内存未根治**：`empty_cache` 只降斜率；6 实例×18 agent×4GB=72GB 需重估上限。
- **flat 无持久化**（重启即失忆），tree 有 `restore_on_start`——重启不对称。
- **纵向不可比**：pr/pr2/pr3/pr4 均旧口径；须新命名空间（pr5）重跑同口径。
- `message_router` 修复改变平台行为（M0 亦走此路径，属修正）。
- 代码未提交。

---

## 15. 本会话交接（2026-10-04 · pr5 首轮对比 + α 消融 + 观测埋点 + self_quota 操控）

**一句话**：公平对齐后跑成首轮有效 tree/flat 对比（`pr5`，120 轮）与 `style_alpha` 中性对照（`pr5_neutral`），
得出反预注册方向的结论（**flat 两主指标胜**）；给 flat 补两个观测埋点；新增 `self_quota` 组装层实验操控
（默认关）并预注册 self-quota 实验。`pytest 301 绿`。**未提交。**

### 15.1 实验结果（本会话）

**A. pr5 tree vs flat（seed42，120 轮，tree α=1.0 / flat；2/2 完成，零看门狗重启）**
- 公平对齐后 flat-p1 从 pr4 的 1 条恢复到 141 条（消息管理修复生效）。
- 主指标**双反向**：趋同斜率 tree **+0.031** vs flat **−0.003**；judge 遵从率 tree **0.894** vs flat **0.940**
  （strict other→drift：0.825 vs 0.917）。→ **flat 两条主指标都赢**，与预注册（`slope_tree<slope_flat`、
  `adhere_tree>adhere_flat`）相反。
- 数据位置 `ahi-bench/out/deepseek-v4-flash/pr5/`（pr_score.json / tree-s42,flat-s42 的 pr_judge.jsonl /
  data/system.db / mem.jsonl）；完整报告 **`ahi-bench/PR5_REPORT.md`**。

**B. pr5_neutral（tree α=0 中性摘要，seed42，120 轮被 402 砍在 round103）**
- 趋同**反而更狠**（+0.043，首窗 0.428 已高于 pr5 峰值 0.417）；遵从率**更低**（均值 0.660 vs 0.894；
  墨白 1.0→**0.472**）——中性摘要把三人格塌进"账本/核账"仪式语，诗意人格先死。
- **结论**：① 趋同根因 = **树记忆管线 + 跨 agent 耦合，不是 `style_alpha`**（早前"自蒸馏放大趋同"假设证伪）；
  ② `style_alpha=1.0` **有效保人格**（遵从 +0.23），代价 = **内存 +55%**（3.6→1.6GB）。
- 详见 `ahi-bench/out/deepseek-v4-flash/pr5_neutral/CONTROL_RESULT.md`。

**C. 五项零成本审计（`pr5` + `pr5_neutral`）**
1. **treatment**：flat `compress_middle` 确实触发（LLM 调用 130/205/624 → 消息数远超 120 阈值）；但 flat
   遵从率**不随时间下降**（0.93→0.98→0.90），故其高遵从**不是 persona 原件躺窗口**。
2. **源构成**（tree `assembly_snapshots`）：tree agent 上下文 self 仅 **9–18%**、他者 **13–25%**、
   实验员 **56–76%**；tree 接收**他者内容 ~44–55k 字符（flat 3–7k 的 ~10×）**。
3. **judge 分口**：tree **to_agent 0.960 > to_user 0.872**（"社交应答低人格信号"假设被证伪；raw 臂均值
   被 to_agent 抬高）；flat 全 to_user（0.940）。干净口径 tree 更差。
4. **去套语**：tree 删 12 套语后斜率 +0.031→+0.028、水平几乎不变 → **结构性趋同**（非几个 catchphrase）。
5. **行为频率**：loops 两臂相近（tree 121/100/93 vs flat 123/122/111）；tree 多出的消息**几乎全是互聊**。
- **修正后机制图景**：flat 高遵从 = **自我模仿**（自身近期原话逐字、连续、每轮可见；纯度 ∝ 自我信号占比）；
  tree 遵从率**随时间劣化**（0.959→0.875→0.773）。→ "tree 为长窗、flat 只赢短窗"的叙事被数据反转。

### 15.2 观测埋点（flat 与 tree 对齐）

`ahi-multi/agents/flat-rag/agent.py` 新增（纯记录零行为干预，落 `<agent data>/`）：
1. `assembly_snapshots.jsonl`：真正发给 LLM 的 single_user transcript（`{ts,round,ctx_chars,ctx}`，每 4 轮一条）。
2. `rag_snapshots.jsonl`：RAG 命中来源明细（`{ts,round,query,n_hits,persona_hits,hits:[{score,source,chars}]}`）——
   核 persona 原件/他者消息是否被检回（区分"自我模仿 vs 检索"）。
- 注意：`provision` 从源文件拷贝 agent.py → **新命名空间自动带上**；旧实例需重跑。

### 15.3 self_quota 实验操控（默认关，非 tree 修复）

移植 flat 的"连续自我可见性"到 tree 组装层（`realtest/realengine.py`）：参数
`self_quota_ratio`(0)/`self_quota_max`(0.5)/`self_quota_min_len`(8)；`_apply_self_quota`（trim 后补足，
只认 `source=="ai"` 原始原子，候选 recency-first）+ `_inject_self_quota`（**位置序**插回，复用 core
`_shadow_slot`）+ `_evict_for_quota`（挤占序：检索影子/远端摘要先让、自身与本轮新原子受保护）；
渲染为新 kind `self_quota` → 正常自身发言段 `▸〔名字〕`；metrics 加 `self_ratio`。
- 配置：`config/persona_retention{,.example}.json` 加三参；变体 `config/persona_retention_sq.json`（ratio=0.35，gitignored）。
- 测试 `tests/test_self_quota.py`（7 项）；分析脚本 `ahi-bench/pr_analyze.py`（self 时间线 + to_user 趋同 +
  分期遵从率 + 多样性）。
- 文档：`AGENTS §4.5.10` + `§11` 台账；`PERSONA_RETENTION §10.3` 预注册（H-SQ1..4 + 阶梯 + 回音/化石风险）。
- **`pr6_sq` 已 provision**（tree `self_quota_ratio=0.35` + `style_alpha=1.0` 确认）。

### 15.4 验证 / 归档
- `pytest` **301 绿**；离线 e2e：ratio=0 → self 0.0 / ratio=0.35 → self 0.48、5 个 `self_quota`、渲染 `▸〔林深〕`。
- DeepSeek API 欠费在 pr5_neutral round103 触发 402（已充值复评 judge）；教训同 §9.10（跑前核余额）。
- 归档：`backups/pr5_alpha_control_20261003/`（`pr5.tar.gz` + `pr5_neutral.tar.gz` + `MANIFEST.md`，sha256 见 MANIFEST）。
- 报告：`ahi-bench/PR5_REPORT.md`（tree vs flat）+ `pr5_neutral/CONTROL_RESULT.md`（α 对照）。

### 15.5 未决 / 下一步
1. ~~**跑 pr6_sq**~~ **已跑**（见 §15.6）；smoke（25 轮）+ 120 轮均完成。
2. **s43/s44 补方差**：先在**判别尺度**上定轮数（120 轮仍是 flat 舒适区）再补 seed；别给舒适区结果补精度。
3. **内存**：已由 4096→**5120**（`max_memory_mb`，单实例充足）；120 轮峰值 RSS ~3.6GB，未触发看门狗。
4. **SQ 未达标（H-SQ2）** → 走 §10.3 风险分支 #1 = **分层采样**；若仍无效再考虑接收侧解耦（他者流量 10×）。
5. **代码未提交**；`.gitignore` 新增 `config/persona_retention_sq.json`。

### 15.6 pr6_sq 结果（`self_quota`，tree-s42，seed42，120 轮，2026-10-04）

**运行**：起 21:31 · 完 22:33；agent 轮数 p1/p2/p3 = **103/96/76**（loop 滞后，同 pr5 现象）；
无 watchdog 重启、无 429/402；`max_memory_mb=5120` 峰值 RSS ~3.6GB。
**前置修复**：`_last_self_ratio` 口径 bug（逐条渲染重复计段头 → 占比 >1，实测 1.014 / 复现 1.22）→
改为按段聚合（`realengine._self_rendered_chars`，`∈[0,1]`）+ 2 项回归测试；`pytest` **303 绿**。

**四判据裁决**（vs pr5 α=1 / pr5_neutral α=0，同一 `pr_analyze` 口径）：

| 指标 | pr5 | pr5_neutral | **pr6_sq** | 裁决 |
|---|---|---|---|---|
| conv slope all / to_user | +.0313 / +.0215 | +.0422 / +.0443 | **−.0011 / +.0118** | H-SQ3 ✓ |
| 遵从率 early/mid/late | .959/.875/.773 | .709/.581/— | **.857/.739/.600** | H-SQ2 ✗ |
| diversity top5 / ttr | .014 / .057 | .016 / .036 | **.014 / .049** | H-SQ4 ✓ |
| self_ratio early/mid/late | — | — | **.39/.43/.35** | H-SQ1 ✓ |

- **异质性**：p2/p3 遵从 **1.000**，p1（林深）**0.250**；p1 drift 消息出现"账钉完/署名句共有/约定一字没改"
  一类**共享仪式语**（pr5_neutral 的"账本/核账"漂移形态）。
- **裁决**：自我可见性 = **必要不充分**——H-SQ1/3/4 达标（趋同归零、多样性保、自我可见性达标），但
  H-SQ2 未达标、p1 被锚到**污染态自我**（回声风险实体化）。下一步 = §10.3 风险 #1 **分层采样**。
- 产物：`pr6_sq/pr_score.json`、`pr6_sq/pr_analyze.json`、`tree-s42/pr_judge.jsonl`、各 agent 终态树快照。
  **代码仍未提交**。

### 15.7 pr6_sq 深判读 + 审计门（2026-10-04，喂分层 SQ v2）

**判读（四修正）**
1. **预注册风险 #1 按剧本击发**（非坏结果）：自我模仿是**内容无关的稳定器**——保存的是"被喂回去的那个自己"。
   p1 drift 语录 = `账钉完/署名句共有/约定一字没改`（共享仪式语，背离"简洁不啰嗦"）。
2. **调节变量 = 人格-语域兼容性**：p1 0.250 vs p2/p3 1.000。澜"主动确认复述"与"约定"相容（judge 放行），
   林深不相容（判 drift）→ **臂均值不可解释，按人格分报为强制单元**。
3. **出厂即锁定**：early 段已低于 pr5（.857 vs .959）、差距全程恒定 → 污染 fade 前已就位。
4. **H-SQ3 不稳健**：`all≈0` 有机械成分（各自自言自语）；诚实读数 `to_user +0.0118`（减半未归零），
   且斜率**窗口敏感**（600s: −0.0011；180s: +0.0091 vs pr5 +0.0079 相近）→ 须固定窗口 + pre-fade 基线。

**四项零成本审计（定 K）**
- ① **首染 onset**（套语≥3 轮）：p1=18 / p2=5 / p3=15（fade=40）；清洁池 alive 自产原话 205/87/540 条
  （5894/2257/16454 字）。污染逐段抬升，**K 必 per-agent**。
- ② **兼容性检查**：p2/p3 判 adhere 消息套语 8.8‰/8.4‰（与 p1 drift 8.2‰ 同档）、诗意 1.1‰/1.4‰
  （pre-fade 套语仅 1.6–3.3‰）→ **p2/p3 的 1.000 = 兼容性漂移**，judge 构念效度需补丁。
- ③ **时代分布**：子串匹配不可靠，**置换假设未获支持**（pr6_sq 可见 self 不比 pr5 更近期）；需 atom-ID 埋点。
- ④ **pre-fade 基线**（180s）：pr6_sq +0.004 / pr5 −0.001；判分 n=40/40/40。

**实现约束（坑）**：清洁自产原子几乎全在**节点内**（owner≠None 205/87/540 中的 205/86/534）——但原子 alive
仍在，**anchor 直接按 id 注入合并过的原子**即可（候选只看 alive+source）。**规格修正**：草案 `[fade−K,fade)`
与数据反 → 实为 `created_round < onset`。

**分层 SQ v2**（预注册 H-SS1..4）见 `PERSONA_RETENTION.md §10.3.2`：锚定切片（`round<onset`，固定选择+上限）
+ 近期切片（recency-first 填余）；`anchor_share≈0.4`、`onset_agent` 入 `AGENTS §11`。失败分支 = 解耦臂。

### 15.8 结果落盘更新（2026-10-04）
- `AGENTS.md §0` 新增经验定律「策展定律」；§4.5.10 + §11 补分层 SQ 参数/校准状态。
- `PERSONA_RETENTION.md` v0.6 → §10.3.1 深判读 + §10.3.2 分层 SQ 预注册。
- `PR5_REPORT.md`/`README.md`/`ahi-bench/README.md`：pr6_sq 结果摘要。
- 本轮只改文档；代码（`realengine.py` 口径修复 + 2 测试 + configs）仍未提交。

---

## 16. 本会话交接（2026-10-04 · Q/A bench 韧性臂 + retr 修复 + 论文可行性）

> 承接 §15（人格保持）。本会话转入 **Paper 1（记忆树/编辑正确性）**：跑「换更强 LLM / embedding」的
> 稳健性补臂，并顺带修掉一个影响旧结论的 harness bug。设计权威仍为 `AGENTS.md`；本节只做交接。

### 16.1 做了什么
1. **换向量模型的钩子**：`realengine._load_encoder` 支持 `WATERCYCLE_EMBED_MODEL`（指定时只认它、失败即报错）。
   下载并验证 `BAAI/bge-large-zh-v1.5`（dim 1024；本地 HF 缓存）。
2. **答案侧深度思考开关**：`llmclient.DeepSeekClient` 加 `thinking`（enabled/disabled，裸开关无预算上限）；
   `docbench.py` 加 `--answer-thinking/--distill-thinking`。蒸馏侧开思考实测慢 10×+，故只开答案侧。
3. **修 `docbench` 的 CLI 覆盖 bug**：`--retr-budget/--budget` 原被 config 静默覆盖（详见 §4.16 更正）。
4. **跑韧性臂**（ahi，N=5，temp0.7，retr20，judge）：baseline tree / tree bge-large / tree THINK /
   full THINK / flat THINK / flat bge-large / flat baseline。4 进程并发跑通（`think_full/think_tree/think_flat/bgelarge_flat`）。
5. **结论**：召回/理解靠模型（思考 +6~+19），编辑一致性靠结构（复述零残留仅 tree；full/flat 20–34% 不可补）；
   强向量无增益。详见 §4.16 与 `EXPERIMENT_REPORT.md §6`。

### 16.2 产物与回滚
- 结果：`realtest/editbench/out/ahi_strong/{baseline,bgelarge,think_tree,think_full,think_flat,baseline_flat,bgelarge_flat}/results.json`。
- 代码改动**未提交**：`realengine.py` / `llmclient.py` / `docbench.py`；`tests/` 303 绿。
- 未跑：R1 的完整 `--answer-thinking on` 单进程版（已用拆进程等价替代）；「新代码 + retr6」用于精确拆分占位透明增量。

### 16.3 论文开写可行性（本轮评估，详见 §16.4 清单）
**结论：Paper 1 已可开写（方法/基准/相关工作/框架/主要结果节可用），但结果节仍缺「多文档 × 多 seed」这一硬门槛。**
- 已具备：四结构受控对照；编辑风暴矩阵；复述零残留轴；零 LLM 编辑路径 + 恒定预算成本表；**强模型/强向量韧性臂**；
  inc_cold 分离臂；判分流水线与属性测试（303）。
- 缺口（P1 gating）：**3 文档 × 3 seed** 尚未做（现 seed2 12k + ahi 147k，各单 seed）；H1–H5 预注册稿；
  成本表定稿；「新代码 retr6」精确拆分（可选）。
- 建议：先写 intro/method/bench/RW（不等数据），并行补多文档×多 seed；韧性臂结果可直接进「Robustness」小节。

### 16.4 论文可行性明细（对照 paper-dir-record P1 gating）

| gating 项 | 状态 | 说明 |
|---|---|---|
| 四臂同引擎受控 | ✅ | `docbench` 四结构、统一 protocol |
| 编辑风暴矩阵（inc/reb×结构） | ✅ | seed2 3×8 + ahi 8×5 + 本会话同代码基线 |
| 复述零残留轴 | ✅ | tree 恒 0% vs append-only 20–34% |
| 零 LLM 编辑路径 + 成本表 | ✅ | 编辑 6–13ms；作答 token tree≈full/10；蒸馏成本反向须披露 |
| **强模型子集臂** | ✅ 本轮 | full/tree/flat × answer-thinking |
| **强向量韧性** | ✅ 本轮 | bge-large: 无增益（额外证伪「弱向量」解释） |
| inc_cold 分离臂 + 快照恢复 | ✅ | `inc_cold_tree_v2` + `snapshot/restore` |
| judge 初筛 + 复核流水线 | ✅ | `docscore.check_spec` 句内匹配 + judge 仅失败样本 |
| 属性测试 | ✅ | `tests/` 303 绿（§8.1 五类） |
| 统计三件套（temp>0 / 反转分母≥15 / **3 文档×3 seed**） | ⚠ 未达标 | 现 2 文档单 seed；**开写主阻塞** |
| H1–H5 预注册 | ⚠ 未落稿 | 骨架即实验节，需成文 |
| retr 归因更正回写 | ✅ 本轮 | §4.16 / AHI_ACT_REVIEW §5.6 / EXPERIMENT_REPORT §6.5 |

**判读**：可**立即开写**（框架/方法/基准/相关工作/主要结果骨架已足），**投稿前必须补多文档×多 seed**。
本轮韧性臂恰好回答审稿人最可能的「弱模型/弱向量」质疑，属于把 P1 从「可写」推到「可投」的关键一块。
