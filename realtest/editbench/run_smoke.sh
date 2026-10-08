#!/usr/bin/env bash
# 编辑风暴实验 smoke：1 文档 × 1 遍（方案 A：编辑/构建各 1 次，作答 1 遍）
# 论文口径：把 --repeats 改为 5。
set -euo pipefail
cd "$(dirname "$0")/../.."   # 仓库根

python3 realtest/editbench/docbench.py \
  --doc realtest/seed2/story.txt \
  --questions realtest/editbench/questions_smoke.json \
  --out realtest/editbench/out/smoke \
  --repeats 1
