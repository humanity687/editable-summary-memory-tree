# 创新性检索报告 · 评估与待办（2026-10-02）

> 来源：外部创新性检索工具产出的一份「创新性查询报告」（四篇文献 + 与已有工作重叠分析 +
> 主张逐项判断 + 补强实验建议）。本文件把它**落成可执行待办**，并据仓库实况修正其失准处。
> **状态：仅归档与规划，尚未执行**；正式两表与主张见 `REPORT.md`，设计权威 = 根 `AGENTS.md`。
> 本文件不做设计决策，只记录「报告怎么说 / 仓库实况如何 / 建议怎么做」。

---

## 0. 一句话结论

可辩护的新颖性是**机制组合**，不是问题本身：
**位置有序的原子里 + 连续区间摘要树 + 编辑区祖先确定性置脏 + 脏节点立即退出所有读路径 +
零 LLM 编辑/组装 + 延迟 LLM 重建 + 编辑作为使用信号 + 长程反转评测**。
报告识别这一层是对的；其不足是**未掌握本仓库已完成的基线矩阵与实验口径**，若干建议已过时或需收窄。

---

## 1. 报告引用的文献（已逐一核验，均真实）

| 文献 | arXiv | 与本项目关系 |
|---|---|---|
| Infini Memory: Maintainable Topic Documents for Long-Term LLM Agent Memory | 2606.10677 | 最相近：主题文档 + 缓冲/周期性 consolidation + 更新/拆分/合并维护。**非**连续区间树、非句级原子、非 owner 链置脏、非零 LLM 编辑路径 |
| CAM: A Constructivist View of Agentic Memory | 2510.05520 (NeurIPS 2025) | 语义网络 + 增量重叠聚类；提供多主题关联，但**无严格连续区间失效边界** |
| Memory for Autonomous LLM Agents（综述） | 2603.07670 | 综述「写—管—读」闭环、五类机制、四个基准；支撑 stale/选择性遗忘是活跃问题 |
| STALE（implicit conflict 基准） | 2605.06527 | 专测「旧记忆是否仍影响推理」，最好系统仅 55.2% → 支撑「新值被召回 ≠ 旧值被排除」 |

> 核验方式：逐一抓取 arXiv/HF 摘要页确认标题与摘要存在。STALE 的 HF 页抓取失败，条目待二次确认。

---

## 2. 报告判断**准确/有价值**的部分

1. 问题层不新（append-only 旧值残留 / 新旧事实冲突 / 结构维护 / 选择性遗忘已是公开问题）；
   新在**机制层组合**——报告这一点判断正确，与 `AGENTS.md` 的主张一致。
2. `StaleLeakage = 输出引用已失效命题次数 / 要求复述当前状态的测试次数` 的形式化定义，
   正对应 `EXPERIMENT_REPORT.md §3.4` 已标记的局限（指标应为 judge「旧值是否被当作当前事实」，
   而非仅测旧串是否出现）。
3. 成本拆分为 `C_input` / `C_maintenance` / `C_total` 是对「约 1/10」主张**最重要的修正**：
   现有 1/10 只是 ahi 上**复用已有树**、仅计**作答路径** prompt token；`EXPERIMENT_REPORT.md §0.6`
   本身记录 tree 缓存命中率仅 20–42%、构建/冷却蒸馏调用量随文档线性增长。
4. 把实验接到 STALE / MemoryAgentBench 的 selective forgetting / knowledge update 任务上——
   这是当前唯一的明确空白，价值最高。
5. 「原地失效使旧摘要在重建前即不可见」的机制表述，比「tree 比 append-only 好」更值得作为核心贡献。

---

## 3. 报告与仓库实况**不符/需收窄**的部分

1. **建议补充的基线其实已在**：报告要求加「vector retrieval」「summary-only」，
   但 `flat`（400 字 chunk + bge 向量 top-12）与 `raptor`（递归摘要 + collapsed top-k）即对应物，
   已是四结构 × 两策略矩阵的一部分（`arms.py` `STRUCTURES`）。**无需新建**。
2. **「tree 增量在反转上追平 rebuild/full」**：seed2 `inc_tree 41 ≈ inc_full 41 ≈ reb 42–45`（`REPORT.md` 表 1）；
   旧 ahi（占位污染）tree inc 反转 **52/85 < inc_full 70/85**，看似不可外推；
   **但占位透明修复后 ahi tree inc 反转 52→70/85，追平 `inc_full`**（`AHI_ACT_REVIEW §5.6`）。
   → 反转轴"追平"在大数据上成立，前提是清理占位/value-None 节点污染。
3. **「全局复述零腐烂」在大数据复现且更强**：ahi inc_tree 复述 **80%/0%**（N=5），
   append-only 残留 6–12%；但「100% 覆盖」仍只属 seed2（`AHI_ACT_REVIEW.md §5`）。
4. **「编辑作为使用信号」不是 Q/A 全胜**：A/B 是**权衡**——复述轴 signal 一致更好
   （warm 100 vs 86、cold 81 vs 71），但暖树 Q/A signal 略差（101 vs 108）。`REPORT.md §3`
   已把主张限定为「利于**全局巩固**」。报告的「很值得强调」需加此限定。
5. **「1/10」是条件性系统结果**：实测（`AHI_ACT_REVIEW.md §5`）作答路径 tree ≈1/14 full，
   但维护侧蒸馏 completion ≈4× full、settle ≈13 min。须按 `C_input`/`C_maintenance` 分列并披露反向。

---

## 4. 待办清单（优先级 × 具体动作 × 验收）

### P0 — 主结果可信度（论文能否立住）
- [ ] **接 STALE / MemoryAgentBench 的选择性遗忘 / knowledge update 子任务**：不只用自定义复述。
      验收：在至少一个公开基准上报告 stale leakage / current-state accuracy / contradiction rate。
- [ ] **正式定义「腐烂」并给统计口径**：`StaleLeakage`、current-state accuracy、omission、contradiction、
      95% CI；样本量、反转次数、编辑类型、judge 协议全部公开；显式排除测试集泄漏。
- [ ] **成本三拆分表**：`C_input` / `C_maintenance` / `C_total` × {full, flat, raptor, tree_inc, reb}，
      含延迟。验收：能解释「1/10」是哪一列，维护成本占比多少。

### P1 — 实验矩阵补强
- [ ] **编辑操作矩阵**（现有 5 类 → 扩展）：事实替换 / 删除 / 中间插入 / 跨节点替换 / 局部重写 /
      反转 / 连续多次编辑（A→B→C→A）/ **回滚** / 热编辑 / 冷编辑。验收：每类有对应题目与 ground truth。
- [ ] **复述任务拆细**：global synopsis / entity-state ledger / timeline reconstruction /
      edited-span verification / full-history contradiction audit（避免单一「复述」混淆不同压力）。
- [ ] **ahi 大数据 N≥3、matched-pair**：N=5 matched-pair 已完成（`AHI_ACT_REVIEW.md §5`）；跨文档/多 seed 未做。
- [ ] **多文档泛化**：3 篇 × 3 seed（叙事 / 技术 / 混合），替代单文档。
- [ ] 补一个**有更新维护的 topic-document baseline**（Infini Memory 式）；如可行，公平适配 CAM。

### P2 — 机制分析（可作论文亮点图）
- [ ] **编辑信号消融**：编辑→热原子 / 编辑→热原子+召回回血 / 编辑→热原子+节点注意力窗口 /
      编辑不产热信号；记录编辑区进入全局视图比例、非编辑区被挤出比例、Q/A 命中率、token、复述正确率。
- [ ] 把「热编辑提升全局覆盖、Q/A 热冷权衡」做成机制图（现有 `REPORT.md` §3 的 A/B 表 2 是雏形）。

---

## 5. 论文主张建议版本（据实收窄）

三贡献（替代报告的四主张合并版）：

1. **编辑安全的有序上下文树**：位置有序的原子—摘要区间树，使局部编辑通过确定性原地失效传播到
   受影响摘要祖先；失效摘要在重建前即被排除出上下文读路径，避免 append-only 的旧值残留。
2. **上下文级编辑信号**：把编辑新原子视为短期使用信号，经 HP/衰减/注意力窗口影响其全局视图分辨率；
   提升编辑内容可见性，同时揭示全局覆盖与局部问答的热冷权衡。
3. **成本—稳定性折中**：在无 LLM 的在线编辑/组装路径上，长程状态反转任务中达到接近 full rebuild
   的一致性，同时降低每轮上下文与维护成本。

**禁用措辞**（现有工作已证明 stale memory / 选择性遗忘 / 结构维护是活跃问题）：
「首次 / 完全防止 / 100%」等，除非带实验条件限定。
建议句：「在本实验设置下，tree incremental 的反转一致性与复述覆盖达到相近或更优，且作答路径上下文成本显著更低。」

---

## 6. 与已有工作的边界（供 related work 直接取用）

已有（不新）：维护外部记忆、处理新旧事实冲突、主题文档更新/重写、结构拆分合并、选择性遗忘、
时间/来源元数据、评估旧信息残留。

本仓库差异（未见同构公开）：
句级原子 + 位置链表 + 连续区间摘要树 + 编辑区祖先脏化 + 脏节点立即排除 + 零 LLM 组装 +
延迟 LLM 重建 + 编辑作为 HP/使用信号 + 长程反转评测。
