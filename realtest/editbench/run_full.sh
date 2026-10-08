#!/usr/bin/env bash
# 编辑风暴实验正式跑：全量题库 × 3 遍作答（方案 A：编辑/构建各 1 次）。
# 每臂 API key 在 keys.local.json（gitignored）：4 baseline + 4 rebuild。
set -euo pipefail
cd "$(dirname "$0")/../.."   # 仓库根

python3 realtest/editbench/docbench.py \
  --doc realtest/seed2/story.txt \
  --questions realtest/editbench/questions_full.json \
  --keys realtest/editbench/keys.local.json \
  --out realtest/editbench/out/full_r5 \
  --repeats 5 \
  --answer-temp 0.7 \
  --judge \
  --tree-budget 7000   # 对齐 flat/raptor 实测用量（6000→40k / 8000→45k）
