#!/usr/bin/env bash
# run_corpus_all.sh — C3–C5（D1/D2/D3）四结构主实验补跑（含 raptor）。
#
# 口径与 run_seed2/run_ahi 对齐：N=5、answer-temp 0.7、tree 16k、retr 20、flat/raptor topk 60、
# --splitter line、--judge。每篇内部四结构并发（run_parallel.sh）。
#   PARALLEL_DOCS=1  三篇也并发（默认 0=顺序跑三篇，避免与 ahi 同时跑时并发过高）。
# 输出：$CORPUS_OUT（默认 out/corpus_run3）/{D1_design,D2_experiments,D3_platform}/results.json
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"
RUN="$HERE/run_parallel.sh"
OUTROOT="${CORPUS_OUT:-$HERE/out/corpus_run3}"
REP="${REPEATS:-5}"
TB="${TREE_BUDGET:-16000}"
RB="${RETR_BUDGET:-20}"
FT="${FLAT_TOPK:-60}"
RT="${RAPTOR_TOPK:-60}"
PARALLEL_DOCS="${PARALLEL_DOCS:-0}"

DOCS=(
  "D1_design|realtest/editbench/out/corpus_proj/D1_design.txt|realtest/editbench/questions_D1_design.json"
  "D2_experiments|realtest/editbench/out/corpus_proj/D2_experiments.txt|realtest/editbench/questions_D2_experiments.json"
  "D3_platform|realtest/editbench/out/corpus_proj/D3_platform.txt|realtest/editbench/questions_D3_platform.json"
)

run_one() {
  local spec="$1" name doc qs rc
  IFS='|' read -r name doc qs <<<"$spec"
  echo "[corpus] $name → $OUTROOT/$name  ($(date '+%H:%M:%S'))"
  bash "$RUN" "$name" "$doc" "$qs" "$OUTROOT/$name" \
    "$REP" "$TB" "$RB" "$FT" "$RT" --splitter line --judge \
    > "$OUTROOT/$name.run.log" 2>&1
  rc=$?
  echo "[corpus] $name exit=$rc （log: $OUTROOT/$name.run.log，$(date '+%H:%M:%S')）"
  return $rc
}

mkdir -p "$OUTROOT"
echo "[corpus] out=$OUTROOT repeats=$REP tree=$TB retr=$RB topk=$FT/$RT parallel_docs=$PARALLEL_DOCS"

fail=0
if [ "$PARALLEL_DOCS" = "1" ]; then
  pids=()
  for spec in "${DOCS[@]}"; do run_one "$spec" & pids+=($!); done
  for p in "${pids[@]}"; do wait "$p" || fail=1; done
else
  for spec in "${DOCS[@]}"; do run_one "$spec" || fail=1; done
fi
echo "[corpus] done → $OUTROOT (fail=$fail)"
exit $fail
