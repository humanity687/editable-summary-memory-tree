#!/usr/bin/env bash
# ahi 主实验（8 臂 = inc/reb × {full,flat,raptor,tree_v2} + inc_cold）——四结构并发 + 合并。
#
# 方案 B（2026-10-02）：四结构互不共享状态 → 拆 4 进程并发（~4×），再用 merge_results.py 合并。
# 参数与单进程一致：tree 16k / retr 20 / flat·raptor topk 60（16k 字符预算封顶）/ N=5 / judge。
# 输出默认 out/ahi_r5_par（**保留原串行 out/ahi_r5**，那是旧占位行为的存档）。
# 审查与结果见 AHI_ACT_REVIEW.md。若需 A/B 障碍臂另起独立轮（需补 2 个逐臂 key）。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${AHI_OUT:-$HERE/out/ahi_r5_par}"

exec "$HERE/run_parallel.sh" ahi \
  realtest/editbench/ahi_script.txt \
  realtest/editbench/questions_ahi.json \
  "$OUT" 5 16000 20 60 60 --judge
