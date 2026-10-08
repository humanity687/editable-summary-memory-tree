# 四臂对比实验报告（作废存档）

> **状态：作废，结论不可引用（2026-09-27 审查发现并标注）。**

## 为什么作废

本文件此前声称四臂作答模型为 `deepseek-v4-flash`，但经复核：

- 本文件 §2 / §5 的数值与 `out/v1_cmp_*`、`out/v2_gemma_cmp_*` 两套运行**逐位相符**；
- 这两套运行的 `replay_result.json` 中 `model` 字段均为 **`gemma4:26b`（本地模型）**；
- `HANDOFF.md` 早已声明早期 v1/v2 四臂误用 gemma，**结果作废、勿引用**。

因此本报告把**已作废的 gemma 数据标注成了 deepseek**，属论文诚信风险（AGENTS §9.11）。

## 真实状态

- 真正的 deepseek 四臂运行产物在 `out/cmp_*`，但：
  - **从未完成评分**（`out/cmp_score.json` 不存在；`out/cmp_score.log` 曾为 0 字节，已删）；
  - 该批 `cmp_*` 产出时间早于 `compare.py` 的两处协议修复
    （`assemble_for_prompt` 交付预算、`reset_fn` 按题清空）。
- 结论：**deepseek 四臂对比尚无有效结果**，需按修正后的协议重跑并评分后方可引用。

## 记录

- 作废数据目录：`out/v1_cmp_*`、`out/v2_gemma_cmp_*`（勿引用）。
- 补丁脚本 `recompute_t2.py` 已删除（其"手抄回填"做法绕过统一评分路径）。
- 重跑命令见 `HANDOFF.md` 快速上手；评分后须核对 `cmp_score.json` 的
  `_meta.updated_at / partial / answerer_model`。
