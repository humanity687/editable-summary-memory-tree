#!/usr/bin/env bash
# run_parallel.sh — 四结构并发跑 docbench + 合并（2026-10-02，方案 B）。
#
# 为什么可拆：full/flat/raptor/tree_v2 彼此不共享任何状态（各自 base/inc/reb/cold），
# 跨结构无耦合。故拆成 4 个进程并发，再合并为单份 results.json —— 等价于单进程全臂，
# 但 ~4× 提速；不改 bench 逻辑，无匹配性/公平性风险。
#
# 用法：
#   run_parallel.sh <scenario> <doc> <questions> <outdir> <repeats> \
#                   <tree_budget> <retr_budget> <flat_topk> <raptor_topk> [额外 docbench 参数...]
# 例：
#   run_parallel.sh ahi realtest/editbench/ahi_script.txt \
#       realtest/editbench/questions_ahi.json realtest/editbench/out/ahi_r5_par \
#       5 16000 20 60 60 --judge
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"

if [ "$#" -lt 9 ]; then
  echo "用法: run_parallel.sh <scenario> <doc> <questions> <outdir> <repeats> <tree_budget> <retr_budget> <flat_topk> <raptor_topk> [额外参数...]" >&2
  exit 2
fi
SCEN="$1"; DOC="$2"; QS="$3"; OUT="$4"; REP="$5"; TB="$6"; RB="$7"; FT="$8"; RT="$9"
shift 9
KEYS="$HERE/keys.local.json"
mkdir -p "$OUT"

echo "[parallel] scenario=$SCEN out=$OUT repeats=$REP tree=$TB retr=$RB flat_topk=$FT raptor_topk=$RT extras=$*"

pids=(); names=()
for s in full flat raptor tree_v2; do
  python3 "$HERE/docbench.py" \
    --scenario "$SCEN" --doc "$DOC" --questions "$QS" --keys "$KEYS" \
    --structures "$s" --out "$OUT/$s" \
    --repeats "$REP" --answer-temp 0.7 \
    --tree-budget "$TB" --retr-budget "$RB" \
    --flat-topk "$FT" --raptor-topk "$RT" "$@" \
    > "$OUT/$s.log" 2>&1 &
  pid=$!
  pids+=($pid); names+=("$s")
  echo "[launch] $s pid=$pid → $OUT/$s.log"
done

fail=0
for i in "${!pids[@]}"; do
  if wait "${pids[$i]}"; then
    echo "[ok] ${names[$i]}"
  else
    echo "[FAIL] ${names[$i]}（见 $OUT/${names[$i]}.log 末尾）"
    fail=1
  fi
done

python3 "$HERE/merge_results.py" --indir "$OUT" --out "$OUT/results.json"
exit $fail
