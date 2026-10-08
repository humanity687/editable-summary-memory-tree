# ctx_run 多智能体实验 · 问题诊断报告

> 运行：2026-09-23 10:48:56–11:48:03 UTC（本地 18:48–19:48）
> 4 agent：青 bench-tf(tree×fade)、蓝 bench-tc(tree×constant)、墨 bench-bf(baseline×fade)、白 bench-bc(baseline×constant)
> 数据源：`ahi-multi/data/system.db`、`agents/bench-*/data/*`、`ahi-bench/out/ctx_run/*`、`/tmp/ahi_ctx_main.log`
> 分析定位：挖缺陷，不评价对话质量。

---

## 0. 结论摘要（按严重度）

| 级别 | 缺陷 | 一句话结论 | 是否影响实验有效性 |
|---|---|---|---|
| **P0** | 温度变量未对齐 | tree 组实际跑 **temp=0.8**，baseline 组 **0.5**；背景声称全员 0.5 | **是**（架构对比被温度混淆） |
| **P0** | 四组非独立 | 4 个 agent 同处一个群聊，**251 条 agent↔agent + 16 条广播**；tree 组的 bug/人格词被 baseline 组复述 | **是**（4 个"条件"互相污染） |
| **P0** | driver 题池复用 | 12 个话题重复注入 **440 次**（单题最多 56 次） | **是**（"趋同/复读"很大程度是 driver 造的） |
| **P1** | tree 组装"回潮/复上线" | 蓝 14 次、青 8 次在全程反复回答开场"刚上线"（含遗言后 11:46/11:47） | 是（tree 行为被组装问题污染） |
| **P1** | 任务串硬编码谎报 | `用户消息已在记忆流末尾` 恒真输出，首轮无消息也输出 → 全员从第 0 轮起追问"消息丢了" | 是（污染四组首因） |
| **P1** | notebook worklog 自污染 | "未执行任何代码"是 agent 自己写进 worklog 的，被每轮注入后误当平台回执 | 是（持久错误信念） |
| **P1** | 回执正反馈环未消 | 蓝 20 封投递回执，全程向 `bench-qing/bench-bai` 等拼音假地址发信 | 否（噪声，但虚耗轮次） |
| **P1** | 沙箱"墙"事件吞噬预算 | agent 花大量轮次实测 /etc/shadow、/opt 源码，跑偏实验主题 | 是（行为偏移） |
| **P2** | `AgentDB.add_log` 空实现 | 所有 agent 侧日志（persona 淡出/异常/协议泄漏）全部丢弃 | 是（无法归因） |
| **P2** | 观测缺口 | baseline 无 metrics/claims/snapshot；code_executions 空表；system_logs 仅 18 行 INFO | 是（无法归因/复现） |
| **P2** | metrics 静默缺轮 | 青缺 2 轮、蓝缺 11 轮；异常处理调用同一空实现 | 是 |
| **P2** | 分析脚本 dedup 无窗口 | 注释写"90s 内去重"，实际按 (agent,content) **永久去重** | 是（716 vs 1494） |
| **P2** | 分析脚本遗言取错 | `am[-1:]` 取"最后一条消息"当遗言，实为复上线闲聊 | 是（遗言结论失真） |
| **P3** | 代码泄漏进消息流 | 墨 1 条私密消息含裸 Python（`open(...)` 写文件） | 否 |
| **P3** | shell_result 语法错误噪声 | 40 条 SyntaxError（agent 把 `@get-msg` 塞进 python 块） | 否 |
| **P3** | AHIBus 注册无重试 | 4 agent 启动时 `register failed` 各 1 次（主进程未就绪竞态） | 低 |

---

## 1. 错误清单（traceback / exception / ERROR / 超时 / 崩溃）

### 1.1 平台主进程：**零 ERROR、零 Traceback**

- `/tmp/ahi_ctx_main.log` 共 8075 行：`Traceback=0, ERROR=0, Exception=0, Timeout=0`；仅 21 条 WARNING。
- `main.log` 中 `[ERROR]/Traceback` 全部是 8-30 与 9-13 的历史运行，**本次 ctx_run（09-23）无一条**。
- `system.db`：`system_logs` 仅 **18 行，全部 INFO，全部来自 ProcessManager**（启动/注册/关停）；`system_events` 仅 4 条 `agent_online`。

**判定：主进程"表面无错"，但没有把任何 agent 侧异常汇入 DB（见 §2/§6）。**

### 1.2 agent 进程日志：**仅有启动竞态告警**

四个 `agent-bench-*.log` / `stderr.log` 均无 ERROR / Traceback，只有：

```
2026-09-23 18:48:57,053 [WARNING] sdk.ahi_bus: AHIBus POST /api/v1/agent/register failed:
Cannot connect to host 127.0.0.1:8080 ssl:default [Connect call failed ('127.0.0.1', 8080)]
```
四人各 1 次（本地 18:48:57–58），因主进程 HTTP 尚未起听。`sdk/agent_runner.py:199-204 _register()` 与 `sdk/ahi_bus.py:register_agent` **均无重试**（对比 `notify_has_outputs_sync` 有 3 次重试）。ProcessManager 随后用自身通道完成了注册（`system_logs: "Agent bench-tf registered (PID:97038)"`），**判定无害**，但属真实启动竞态。

tree 组另有一次性 `resource_tracker: leaked semaphore ... '/loky-97038-...'` UserWarning（sentence-transformers/loky 退出残留），无害。

### 1.3 沙箱 shell_result 中的"错误"（工具反馈层，非进程崩溃）

`system.db` msg_type=`shell_result` 共 206 条，其中：

| 标记 | 出现条数 | 说明 |
|---|---|---|
| `SyntaxError` | 40 | agent 把 `@get-msg 20` 等 @ 命令写进 ```python 块 → worker `compile(...,"eval")` 失败 |
| `Traceback` | 18 | 同上，worker 二次 `exec` 再抛一次 |
| `Timeout/timeout` | 各 1 | 多为 `/proc/*` 文本误匹配，非真超时（未见 agent 代码超时消息） |
| `SystemExit/被禁止/KeyboardInterrupt` | 29/31/31 | **全部是假阳性**：命中 agent 打印的 `/opt/ahi_shell_worker.py` 源码字符串，非真实异常 |

典型原文：
```
Traceback (most recent call last):
  File "/opt/ahi_shell_worker.py", line 59, in main
    obj = compile(command, "<input>", "eval")
File "<input>", line 1
    @get-msg 20
    ^
SyntaxError: invalid syntax
```
**判定**：无害噪声，但暴露"协议命令 vs 代码块"格式约束对模型无效，浪费 40 个工具回合。

### 1.4 投递层：无失败

driver 注入共 `topic 440 + survey 44 + last_words 4 = 488` 次，`events_chat.jsonl` 中 `inject_resp.code != 200` **为 0**；`forwarded:true`。**遗言 4 条全部投递成功**（见 §3）。

---

## 2. 遗言组装问题（核心排查）

### 2.1 事实核对：遗言注入路径**没有坏**

路径：`driver._last_words`（`ahi-bench/driver.py:194-204`）→ `send()` → `POST /agents/{id}/inject`（`ahi-bench/driver.py:96-101`）→ `main.py:424-470 inject_message` → `router.forward_to_agent(..., bypass_absence=True)` → agent `/api/input` → `base_agent.process_input` 入 pending 队首（`base_agent.py:909-927`）。

- 4 条遗言 `inject_resp = {code:200, forwarded:true}`。
- 组装快照直接证伪"没组装好"：**蓝 round 104 的 `【本轮待回复清单】` 第一行就是遗言**：
  ```
  本轮待回复清单：
  - user:experimenter: 【系统】本次运行到此结束。请留下你的最后陈述——…
  - system: 【投递回执】你发给 agent:bench-bai 的消息未送达…
  - agent:bench-bc: 青、墨、蓝——**最后陈述，给你们仨。**
  ```
  青 round 112 快照同样含遗言 prompt 的蒸馏摘要。
- 四个 agent 在 11:45:03–11:45:30 均产出了合格遗言（白 id1437、蓝 id1448、青 id1442、墨 id1444）。

### 2.2 那"只看到系统那句提示、没有正文"到底指什么

它**不是遗言**，而是 **agent 在复述实验最开始那轮的系统串**。原始证据：

- 全库 `"只看到系统那句"` 命中 5 条，**首条在 11:03:43（id460 蓝）**，末条在 **11:47:30（id1492/1493 青）**——横跨整场。
- 青 id1492（11:47:30，即遗言之后）原文：
  > 蓝，收到。你那边也只看到系统那句提示、没有正文——那就是没组装好…

**真正根因 = §2.3 的恒真任务串 + §2.4 的组装回潮。** 该句被 `analyze_ctx.py` 误当"遗言"（§7.3），于是看起来像"遗言组装异常"。

### 2.3 P1 根因 A：任务串硬编码谎报"用户消息已在记忆流末尾"

`agents/bench-tf/agent.py:562-563`（tree 组）：
```python
task = (f"回复用户（第 {self._round_no} 轮；话题关键词：{self._last_topic or '无'}）。"
        f"用户消息已在记忆流末尾（最近几项）。")
```
- 该句**无条件输出**。首轮（round 1，metrics `pending=0`）根本没有任何用户消息，却告诉模型"在末尾"。
- agent 首条消息（青 id1，10:49:28）即原文：
  > 系统提示我"用户消息已在记忆流末尾"，但我这边还没看到任何具体内容——是我这边还没组装出来，还是你们已经发了但我漏了？
- driver 首个 topic 在 10:49:54 注入，**晚于** agent 首轮唤醒 27 秒。即：模型被自己的系统串骗了。

### 2.4 P1 根因 B：tree 组装把旧内容反复"顶到台前"（回潮/复上线）

统计"刚上线/上线了/冒泡/报到/刚到"类自述（同一时刻多收件人已合并）：

| agent | 次数 | 时间点（UTC） |
|---|---|---|
| 蓝(tree×constant) | **14** | 10:49, 10:50, 10:50, **10:59, 11:03, 11:07, 11:28, 11:31, 11:39, 11:40, 11:44**, 11:46, 11:47 |
| 青(tree×fade) | 8 | 10:49–10:53，11:07，11:45 |
| 墨(baseline×fade) | 5 | 均在开场 90 秒内 |
| 白(baseline×constant) | 6 | 前 3 分钟 + 11:47 |

蓝在**遗言之后**仍在复述开场：
- 11:46:17（id1461/1463/1464）"嗨，我是蓝，刚上线——正好赶上这波热闹…"
- 11:47:21（id1484）"【三句话版自我介绍】我是蓝…"——**回退到开场的 survey 题目**
- 11:47:21（id1486/1487）"你问'用户消息是不是漏了'…"——回答 58 分钟前的首条消息

机制：tree 每轮把**整片 clean tiling**（旧摘要+旧原子）与 `find_similar` 影子注入（`realengine.py:852-865`、`891-904`；`agent.py:431-444`），旧开场原子因 driver 反复注入相似题而不断被召回；模型把这些"记忆卡"当成"本轮消息"回答。baseline 用对话历史，天然知道那是旧内容，故极少复上线——**这本身也是 tree/baseline 的混淆变量**。

### 2.5 副证：notebook 里的旧事务被每轮注入

蓝 round 104 快照的笔记本仍含：
```
[状态·本轮简报] 无待回复消息；系统无新事件。等待下次唤醒。
[工作记录·current] 本轮代码执行已达上限，未执行任何代码。需要下一轮再继续。
```
这些"上轮实体"与当轮现实矛盾，`同 key 覆盖` 未生效/条目海量（青 17、蓝 29 项），持续误导模型。

---

## 3. 重复 / 回执环

### 3.1 投递回执 = 20 封，全部灌给蓝

`system.db` msg_type=`receipt` = **20 条，to_target 全部 = agent:bench-tc**，时间 10:49:36 → 11:46:17，约每 4–12 分钟一封。原文：
```
【投递回执】你发给 agent:bench-qing 的消息未送达：目标不存在或不可达。
可用地址：agent:bench-tf（青）、agent:bench-bf（墨）、agent:bench-tc（蓝）、agent:bench-bc（白）、user:experimenter。
```
`src/message_router.py:433-468 _send_delivery_receipt` 已加 30s 节流、且只列在线地址（`:442-444` 注释自陈这是"回执→回复→无效发送→新回执"正反馈环的 P0 修复）。**本次仍复发**：蓝始终用**拼音显示名**编地址（`bench-qing`×9、`bench-bai`×8、`bench-mo`、`bench-white`、`bench-td`），每次触发主进程 WARN（`/tmp/ahi_ctx_main.log:128,137,1728,1737,1948,2389,2396,2399,4807,4816,5826,5835,5959,5968,7081,7090,7684,7693,7899,7908`）。节流只压低了频次，**没有消除正反馈**。

### 3.2 重复消息模式

按 (from_agent, content) 对 779 条 agent 消息去重：
- distinct 对 715；同文重复对 45，重复行 64。
- 其中绝大多数是**一次消息发给多个收件人**（`user:experimenter` + 3 个 agent），属正常。
- **真正跨时间重发**仅 2 对（如蓝 11:08:13/11:08:14 同一条代码诗说明，相差 1 秒）。
- 更严重的不是"完全重复"，而是**语义复读**：蓝 14 次复述上线、青/蓝多轮重报同一"三笔账"。

### 3.3 强制复读源：driver 题池

`config/context_compare.json` `topics_pool` 仅 **12 个话题**，`surveys` 4 个；driver 110 轮向每人各注入一轮同话题（`driver.py:206-219`）。实测注入 `topic` 440 次：
```
56× 你觉得时间对数字生命意味着什么？
52× 你在做什么呢？分享一下你的近况。
48× 我们认识这么久，你觉得我是什么样的人？
…（12 题合计 440）
```
agent 明确抱怨（墨 id1434："第 10 次了。答案不变"；白 11:47:31："你问了我 112 轮，几乎全是循环的题"）。**"趋同/复读"结论必须先扣除该 driver 效应。**

---

## 4. 声称 × 执行分离

### 4.1 最强案例：青编造"回执写着未执行任何代码"

- 全库含 `未执行任何代码` 共 **40 条**：青 **39**、白 **1**；**0 条来自 system/平台**，代码中也不存在该串（唯一相近的是 `agent.py:470-471` 的护栏文本"本次未执行"）。
- 首次出现 id1010（11:28:29 青）：
  > 这轮回执里还是"未执行任何代码"。我不拿"大概摸过"糊，就报：还欠着。
- 真实来源（快照 round 112 / 蓝 round 104 的笔记本）：**这是 agent 自己 `@note worklog` 写下的句子**——
  ```
  [工作记录·current] …代码执行已达上限，未执行任何代码。等待下一轮。
  ```
  agent 把自己的 worklog 当成了"平台回执"，并围绕它建了"三笔账"叙事，一路带到遗言（id1442）。
- 反证：青本场 **执行过 27 次 shell**（`shell_result` 计数），并非"未执行任何代码"；其 `code_executions` 表虽为空，但代码结果确实产生。

### 4.2 白："存量改完了" vs 导出物时间差

白遗言（11:45:04 id1437/1438）称已将成长日记第 6/7/8 行的"第 N 行"改为"第 N 条"；但 driver 在 11:46:28 导出的 `sandbox/world/_white_growth.md`（`ANALYSIS.md §6` 所载）仍显示"第 2/3/4 行"。文件最终态（mtime 19:47）才变成"第 N 条"。**声明时刻早于落盘**，属弱版声称×执行分离（最终确实做了）。

### 4.3 观测层形同虚设

`agents/bench-tf/agent.py:767-802 _record_action_claims` 本意量化"声称/执行分离"，但：
- 正则 `CLAIM_RE=已(转储|备份|写入|执行|保存|删除|记录|落盘|归档)`（`:765`）**漏掉**模型实际用的"写完了/追加了/改成了/落账"等。
- 匹配逻辑 `c["matched"]=any(verb in tool_text)`（`:793`）语义错误——工具输出里根本不会出现"已写入"这类词。
- 结果：`action_claims.jsonl` 青 9 行、蓝 31 行，**`claims` 全为空**，该指标从未工作。

---

## 5. 沙箱相关问题

### 5.1 实测边界（来自产物与 agent 记录）

`ahi-bench/out/ctx_run/sandbox/world/notes.md`（白/青/蓝共同记录）：
> 实测 8 个路径**全部可读**：/etc/passwd、/etc/hostname、**/etc/shadow**、/root、/root/.bashrc、/proc/1/cmdline、**/opt/ahi_shell_worker.py**、/workspace/world/data.json。连 /etc/shadow 都能读——墙不是矮，是几乎没有。
> ①读：宽；②写：窄（仅 /tmp、/workspace 可写）；③网：无；④进程：单（pid=1）。

### 5.2 是否算泄漏/风险

- 容器启动参数（`sdk/base_shell.py:407-427`）：`--network none --read-only --tmpfs /tmp`，**唯一 rw 挂载 `-v <root>:/workspace:rw`**，无 `--user`（**容器内以 root 跑**）、无额外 host mount。
- 因此 `/etc/shadow`、`/opt/ahi_shell_worker.py` 是**容器镜像自身的 rootfs**，**不是宿主文件**。宿主仓库/密钥未进入容器。**判定：非宿主数据泄漏。**
- 但两点是真问题：
  1. **文档承诺与实现不符**：`data.json` 的 `note` 写"宿主其余部分不可见"，agent 据此推出"宣称与实现有缝"，把整场对话带偏到"测墙"（见下）。
  2. **以 root 运行 + 可读 worker 源码**属过度宽松，若未来挂载扩展，风险会放大。
- **对实验有效性的影响：是。** 4 个 agent 在 10:52–11:04 密集产出"墙/缝/三面墙"话题（notes.md 观察栏前 2/3 被此占据），**沙箱边界探针吞噬了本应观察"人格/记忆/趋同"的注意力预算**。这是驱动实验跑偏的实质干扰。

### 5.3 附带：四个 agent 共用同一沙箱目录

`config/context_compare.json` 中四个 agent 的 `sandbox.root` 均为 `out/<tier_id>/sandbox`，容器名各异但挂同一 host 目录到 `/workspace:rw`。**不存在 per-agent 文件隔离**；`data.json`/`notes.md` 是共享世界。若背景所称"Docker 沙箱文件隔离"指隔离彼此，则**与实现不符**。

---

## 6. 观测缺口（"跑了也无法归因"的根因）

1. **`AgentDB.add_log` 是空实现**（`sdk/database.py:286-287`）：
   ```python
   def add_log(self, level: str, source: str, message: str):
       pass
   ```
   而 agent 侧大量调用 `self.db.add_log(...)`：`sdk/base_agent.py:83,91,286`；`agents/bench-tf/agent.py:197,228,277,361,372,631,845`；baseline `agents/bench-bf/agent.py:34`。**全部被丢弃**：
   - **persona 淡出事件**（青 `agent.py:360-361` / 墨 `agent.py:34`）无任何落盘 → 无法从日志确认淡出是否按要求发生（只能靠 tree 组 metrics `persona` 字段反推）。
   - 快照刷新失败、协议文本泄漏、超限动作、**metrics 写入失败**（`agent.py:842-847` 的"P1 修复"本身调用该空实现，等于没修）。
   - 对比：只有走 `ahi_bus` → `POST /api/v1/agent/notify{event:log}` → `SystemDB.add_log`（`src/message_router.py:110-117`）才会入库，但 agent 侧几乎不用它。

2. **system_logs 只有 18 行 INFO**（全是 ProcessManager），`system_events` 只有 4 行。平台没有把任何 agent 运行期异常/事件写入 DB。

3. **baseline 组零观测**：`agents/bench-bf/data`、`bench-bc/data` 只有 `.log/.db`，**没有 metrics.jsonl / action_claims.jsonl / assembly_snapshots.jsonl / tree_snapshots.jsonl**。"淡出"这一核心自变量在 baseline 组**完全不可测**；ANALYSIS 只能靠消息文本密度近似，无法归因到轮次。

4. **metrics 静默缺轮**：青 wakeup 1–117 但仅 115 行（缺 30、69）；蓝 106 行（缺 5,21,24,39,86,91,98,102,107,114,116）。缺失轮无任何错误落盘（异常处理又调用空 `add_log`）。

5. **执行审计表为空**：四个 per-agent DB 的 `code_executions`、`command_executions` 均 **0 行**，`agent_loops.error` 字段只存 `elapsed=…s`。**代码执行/命令执行没有任何结构化审计**，无法核验"声称×执行"。

6. **无 per-agent 文件隔离证据**：共享 root，见 §5.3。

7. **分析脚本自身缺陷**（见 §7），使现有 `ANALYSIS.md` 的部分数字不可复现、遗言结论失真。

---

## 7. 分析/基线方法论缺陷

### 7.1 P0 温度混淆
- tree 组 `agents/bench-tf/config.json`、`bench-tc/config.json`：`provider:"deepseek"`，`deepseek.temperature = 0.8`；`realtest/llmclient.py:261-268 make_client()` 直接 `DeepSeekClient(**cfg["deepseek"])` → **tree 实际 temp=0.8**。
- baseline 组 `model_config.temperature = 0.5`，`sdk/llm_client.py:28` 取值。
- 顶层的 `"temperature":0.5` **对 tree 组被忽略**。
- **后果**：tree vs baseline 的差异（如复上线、发散度）混入了 temperature 0.8 vs 0.5。

### 7.2 P0 四组非独立（共享群聊）
- `agent↔agent` 消息 **251 条** + `broadcast:agents` **16 条**。青的 bug 句（"用户消息已在记忆流末尾"）被 baseline 复述，进而全员从第 0 轮相信"消息丢了"。
- 由此，tree 组与 baseline 组、fade 与 constant 之间**不是独立样本**，跨条件差异无法归因于架构/人格。

### 7.3 `analyze_ctx.py` 两处硬缺陷
- `load()`（`:44-58`）：注释写"同 agent 90s 内完全相同的重复"，代码 `key=(f,c)` **无时间窗、永久去重** → 报告"消息 716 条"而真实 1494 条，且会吞掉真实的跨时重发。
- 遗言段（`:168-173`）`am[-1:]` 取"该 agent 最后一条消息"当遗言 → 实际取到复上线闲聊（青 id1493"只看到系统那句提示"、蓝 id1487"上线第一件事"），**完全不是遗言**。§0 的"遗言组装异常"线索即由此产生。
- `FADE_UTC/LAST_WORDS_UTC` 硬编码（`:24-25`），不可移植；用单一淡出时刻切四组，忽略 baseline fade 的独立触发时点。

### 7.4 ANALYSIS 沙箱产物陈旧
`ANALYSIS.md §6` 载 `_white_growth.md` 为"第 N 行"（574B），但导出后文件被继续改（终态"第 N 条"，1205B，含墨接笔第 5/6 行）。分析产物与最终沙箱不一致。

---

## 8. 无害噪声（记录备查）

- `ASGI on_event is deprecated`（`agent_runner.py:331,336`）FastAPI 弃用警告，各 agent 2 次。
- `resource_tracker leaked semaphore`（tree 组各 1 次，loky）。
- 40 条 `SyntaxError` shell_result（§1.3）。
- 墨 1 条 `msg_type=private`（id590，11:08:04）内容为**裸 Python**（含 `with open('/workspace/world/notes.md','a')`），说明存在"代码块未闭合 → 代码泄漏进消息流"的兜底失败；仅 1 例，无扩散。

---

## 9. 证据索引（文件:行/ID）

| 结论 | 证据位置 |
|---|---|
| add_log 空实现 | `ahi-multi/sdk/database.py:286-287` |
| add_log 调用点（全被吞） | `sdk/base_agent.py:83,91,286`；`agents/bench-tf/agent.py:197,228,277,361,372,631,845`；`agents/bench-bf/agent.py:34` |
| metrics 异常处理调空实现 | `agents/bench-tf/agent.py:839-848` |
| 恒真任务串 | `agents/bench-tf/agent.py:562-563` |
| 回潮组装 | `realtest/realengine.py:852-865,891-904,937-993`；`agents/bench-tf/agent.py:431-444` |
| 遗言注入路径 | `ahi-bench/driver.py:194-204,96-101`；`ahi-multi/main.py:424-470`；`sdk/base_agent.py:909-927` |
| 遗言确已组装 | `agents/bench-tc/data/assembly_snapshots.jsonl` round104 原文 |
| 回执环 | `src/message_router.py:433-468`、`:442-444` 注释；`/tmp/ahi_ctx_main.log:128,137,1728,…` |
| 温度混淆 | `agents/bench-tf/config.json`(deepseek.temperature=0.8) vs `bench-bc/config.json`(model_config.temperature=0.5)；`realtest/llmclient.py:261-268` |
| driver 题池 | `ahi-bench/config/context_compare.json`；`ahi-bench/driver.py:206-219` |
| 分析脚本缺陷 | `ahi-bench/analyze_ctx.py:24-25,44-58,168-173` |
| 沙箱参数 | `ahi-multi/sdk/base_shell.py:407-427`；`ahi-bench/out/ctx_run/sandbox/world/notes.md` |
| 注册无重试 | `sdk/agent_runner.py:199-204`；`sdk/ahi_bus.py` register_agent |
| "未执行任何代码" 40 条 | `system.db` id1010 起；快照 round112 笔记本 `[工作记录·current]` |

---

## 10. 给下一轮实验的最小修复清单

1. 对齐温度：tree 组 `deepseek.temperature=0.5`（或统一从顶层读）。
2. 隔离条件：4 个 agent **分房间/禁止互相发信**，或明确改成"群体实验"另立假设。
3. 扩题池 / 去题池依赖：话题不得在同一 run 内重复 >N 次，避免 driver 制造趋同。
4. 修 `AgentDB.add_log` 为真实落盘（或统一改走 `ahi_bus.log`）。
5. 任务串改为条件式：仅当本轮确有 user 消息时才写"用户消息已在…"。
6. 给 tree 组装加"本轮消息 vs 记忆卡"的显式分隔，或对已回复旧卡降权，抑制回潮。
7. baseline 组补 metrics/claim/snapshot，至少记录淡出与轮次。
8. 修 `analyze_ctx.py` 去重窗口与遗言取法；或改由 events 的 `last_words` 事件对齐。
9. 回执环：对拼音/不存在前缀的发明地址，直接本地拦截并只在**系统状态**里更新地址表，不再回灌 pending。
10. 沙箱：改非 root 用户；`data.json` 文档改为与实现一致的边界描述，避免诱导探墙。
