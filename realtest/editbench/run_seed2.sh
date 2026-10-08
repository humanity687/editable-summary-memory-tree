#!/usr/bin/env bash
# seed2 小数据主实验（8 臂）——四结构并发 + 合并（方案 B，2026-10-02）。
# 参数与已存档 seed2_r3_8arm_cool4 对齐：tree 7k / retr 6 / N=3 / temp 0.7 / judge。
# 输出默认 out/seed2_r3_par（保留原串行存档）。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${SEED2_OUT:-$HERE/out/seed2_r3_par}"

exec "$HERE/run_parallel.sh" seed2 \
  realtest/seed2/story.txt \
  realtest/editbench/questions_full.json \
  "$OUT" 3 7000 6 12 12 --judge
