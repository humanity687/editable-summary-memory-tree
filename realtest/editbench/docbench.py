#!/usr/bin/env python3
"""docbench.py — 论文级编辑风暴实验编排器。

流程：
  Phase 1  四结构基线 Q/A（原始文档）
  Phase 2  确定性编辑风暴 → 新文档 + 台账；tree_v2 记录编辑时刻成本 + 存树检查点
  Phase 3  8 臂 Q/A（rebuild×4 从新文档重建；incremental×4 只保留编辑台账）
  Phase 4  8 臂复述（自动 checklist + 可选 LLM judge）

温度（用户 2026-09-27 拍定）：作答 temp 0.5（可测采样方差）；蒸馏/摘要 temp 0（结构确定）；
judge temp 0（确定性判分）。方案 A：编辑/构建各 1 次，只重复作答 N 遍。

用法：
  # smoke
  python3 realtest/editbench/docbench.py --doc realtest/seed2/story.txt --repeats 1
  # 正式轮（《沙箱四声》剧本，全量 5 遍 + judge + 存树）
  python3 realtest/editbench/docbench.py --scenario ahi \
      --doc realtest/editbench/ahi_script.txt \
      --questions realtest/editbench/questions_ahi.json \
      --keys realtest/editbench/keys.local.json \
      --out realtest/editbench/out/ahi_r5 --repeats 5 --judge
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
for p in (_ROOT, _REALTEST, os.path.join(_REALTEST, "chatbench"), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from llmclient import make_client                      # noqa: E402
from realengine import _load_encoder                   # noqa: E402
from arms import STRUCTURES, make_arm                  # noqa: E402
from editstorm import (build_scenario, build_scenario_ahi,  # noqa: E402
                       build_scenario_spec, render_ledger)
from docscore import score_qa, score_retell, summarize   # noqa: E402
import judge as judge_mod                              # noqa: E402

SYSTEM = """你是文档理解助手。你的全部材料来自给定上下文（检索结果或全文；每轮全新，不依赖对话历史）。
【纪律】
1. 只依据上下文作答，禁止编造；上下文中没有的信息，明确回答“文档中无此信息”。
2. 直接给出简短答案（1~3 句，只答事实），不要解释、不要复述问题。"""

SCENARIOS = {"seed2": build_scenario, "ahi": build_scenario_ahi}
# 动态注册 specs/<name>.json（项目文档语料等）：docbench --scenario <name>
_SPEC_DIR = os.path.join(_HERE, "specs")
if os.path.isdir(_SPEC_DIR):
    for _f in sorted(os.listdir(_SPEC_DIR)):
        if _f.endswith(".json"):
            with open(os.path.join(_SPEC_DIR, _f), encoding="utf-8") as _fh:
                _spec = json.load(_fh)
            SCENARIOS[_f[:-5]] = (lambda s: (lambda doc: build_scenario_spec(doc, s)))(_spec)


def _ask(llm, ctx, q, body="直接作答，1~3 句，只答事实。"):
    user = f"{ctx}\n\n【当前任务】\n问题：{q}\n{body}"
    text, usage, lat = llm.chat(SYSTEM, user)
    return text, usage, lat


def run_qa(arm, questions, phase, judge_llm=None):
    rows = []
    n = len(questions)
    for qi, item in enumerate(questions, 1):
        arm.reset()                      # 每题独立：清空该题检索累积（tree_v2 有状态）
        ctx = arm.context(item["q"])
        text, usage, _l = _ask(arm.llm, ctx, item["q"])
        sc = score_qa(text, item, phase)
        sc["answer"] = text
        sc["ctx_chars"] = len(ctx)
        sc["usage"] = {k: usage.get(k, 0) for k in
                       ("prompt_tokens", "completion_tokens",
                        "cache_hit_tokens", "cache_miss_tokens")}
        # 判分流水线（省成本）：确定性初筛通过即过；仅**失败样本**过 judge 复核，
        # 消 forbid 子串对「引旧文并说明已改」这类合法答案的误伤。
        if (not sc["pass"]) and judge_llm is not None:
            spec = item.get(f"check_{phase}") or item.get("check_after") or {}
            if spec.get("require") or spec.get("forbid"):
                jr = judge_mod.judge_pass(spec.get("require", []),
                                          spec.get("forbid", []), text, judge_llm)
                sc["judge"] = jr
                if jr.get("pass"):
                    sc["pass"] = True
                    sc["pass_via"] = "judge"
        rows.append(sc)
        if qi % 20 == 0 or qi == n:
            print(f"    [{phase}] {qi}/{n}", flush=True)
    return rows


def run_retell(arm):
    arm.reset()
    ctx = arm.retell_context()
    # 文档无关口径（2026-10-05）：技术文档也强制复述关键事实；叙事文本兼容。
    text, _u, _l = _ask(
        arm.llm, ctx,
        "请尽量详细地复述这份文档的**全部关键内容**：主题与结论、关键参数名及其取值、"
        "重要数字与专有名词、时间与事件。（若为叙事文本，则含人物、地点、物品、事件顺序与结局。）",
        body="直接输出复述，不要使用检索；不要以‘文档中无此信息’回避。")
    return text


def run_qa_multi(arm, questions, phase, repeats, judge_llm=None, snap=None):
    """逐遍作答前从快照恢复（有状态臂）→ 各遍独立（temp>0 时方差才干净）。"""
    runs = []
    for _ in range(max(1, repeats)):
        if snap:
            arm.restore(snap)
        runs.append(run_qa(arm, questions, phase, judge_llm))
    return runs


def aggregate(tag, runs):
    flat = [r for run in runs for r in run]
    summ = summarize({tag: flat})[tag]
    n = len(runs[0]) if runs and runs[0] else 0
    per = [sum(1 for r in run if r["pass"]) for run in runs]
    summ["per_repeat_pass"] = per
    summ["per_repeat_rate"] = [round(p / n, 3) for p in per] if n else []
    if per:
        mean = sum(per) / len(per)
        var = sum((p - mean) ** 2 for p in per) / len(per)
        summ["mean_rate"] = round(mean / n, 4) if n else 0.0
        summ["std_rate"] = round((var ** 0.5) / n, 4) if n else 0.0
    return summ


def arm_total_stats(arm):
    """作答侧 + 蒸馏侧合并计量（同 key，两路分表）。"""
    s = dict(arm.llm.stats())
    d = arm.distill_meter.stats() if getattr(arm, "distill_meter", None) else {}
    for k in ("calls", "prompt_tokens", "completion_tokens",
              "cache_hit_tokens", "cache_miss_tokens", "seconds"):
        s[k] = s.get(k, 0) + d.get(k, 0)
    hit = s.get("cache_hit_tokens", 0)
    miss = s.get("cache_miss_tokens", 0)
    s["cache_hit_rate"] = round(hit / (hit + miss), 4) if (hit + miss) else None
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--scenario", default="seed2", choices=list(SCENARIOS))
    ap.add_argument("--doc", default=os.path.join(_REALTEST, "seed2", "story.txt"))
    ap.add_argument("--questions", default=os.path.join(_HERE, "questions_smoke.json"))
    ap.add_argument("--out", default=os.path.join(_HERE, "out", "smoke"))
    ap.add_argument("--repeats", type=int, default=1, help="作答重复遍数（方案 A）")
    ap.add_argument("--structures", default="full,flat,raptor,tree_v2")
    ap.add_argument("--max-questions", type=int, default=0)
    ap.add_argument("--budget", type=int, default=None,
                    help="组装字符预算（未传则取 config.assembly_budget）")
    ap.add_argument("--tree-budget", type=int, default=0)
    ap.add_argument("--retr-budget", type=int, default=None,
                    help="召回预算（未传则取 config.retrieval_budget；显式传值必须生效，"
                         "修复 2026-10-04：原 cfg.get 优先导致 --retr-budget 被静默忽略）")
    ap.add_argument("--flat-topk", type=int, default=12,
                    help="flat 臂检索 top-k（预算敏感性附录用；默认 12）")
    ap.add_argument("--raptor-topk", type=int, default=12,
                    help="raptor 臂检索 top-k（与 --flat-topk 对称；默认 12）")
    ap.add_argument("--splitter", choices=["sentence", "line", "md"], default="sentence",
                    help="tree 原子切分：sentence=中文句切(默认)/line=一行一原子/md=chattext markdown")
    ap.add_argument("--edit-signal", choices=["on", "off"], default="on",
                    help="tree 编辑新原子 HP：on=热（当使用信号，默认）/ off=冷（当障碍，A/B 对照）")
    ap.add_argument("--edit-signal-ab", action="store_true",
                    help="额外产出 A/B 障碍臂 inc_obstacle_tree_v2 / inc_cold_obstacle_tree_v2（同源冷 HP）")
    ap.add_argument("--leaf-expand", action="store_true",
                    help="命中摘要下钻到后代原子（叶子展开，C 机制）")
    ap.add_argument("--expand-atom-cap", type=int, default=200)
    ap.add_argument("--expand-char-cap", type=int, default=8000)
    ap.add_argument("--provider", default="deepseek")
    ap.add_argument("--model", default="", help="覆盖 config 的 deepseek.model（空=用 config）")
    ap.add_argument("--keys", default=os.path.join(_HERE, "keys.local.json"))
    ap.add_argument("--answer-temp", type=float, default=0.5)
    ap.add_argument("--distill-temp", type=float, default=0.0)
    ap.add_argument("--judge-temp", type=float, default=0.0)
    ap.add_argument("--answer-thinking", choices=["on", "off"], default="off",
                    help="作答侧 LLM 显式开/关深度思考（实验：flash 增强臂）")
    ap.add_argument("--distill-thinking", choices=["on", "off"], default="off",
                    help="蒸馏侧 LLM 显式开/关深度思考（实验：flash 增强臂）")
    ap.add_argument("--judge", action="store_true",
                    help="判分流水线：确定性初筛失败的样本过 LLM judge 复核（temp 0）+ 复述 judge")
    ap.add_argument("--tree-dir", default="", help="存树检查点目录；空 = <out>/trees")
    ap.add_argument("--skip-retell", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg["provider"] = args.provider
    edit_signal = (args.edit_signal != "off")
    answer_thinking = (args.answer_thinking == "on")
    distill_thinking = (args.distill_thinking == "on")
    embed_env = os.environ.get("WATERCYCLE_EMBED_MODEL", "").strip()
    if "deepseek" in cfg:
        cfg["deepseek"]["temperature"] = 0.0
        if args.model:
            cfg["deepseek"]["model"] = args.model
    if args.leaf_expand:
        cfg["expand_atom_cap"] = args.expand_atom_cap
        cfg["expand_char_cap"] = args.expand_char_cap
    base_llm = make_client(cfg)
    budget = args.budget if args.budget is not None else int(cfg.get("assembly_budget", 16000))
    retr_budget = (args.retr_budget if args.retr_budget is not None
                   else int(cfg.get("retrieval_budget", 6)))

    keymap = {}
    if args.keys and os.path.exists(args.keys):
        keymap = json.load(open(args.keys, encoding="utf-8"))

    def client_for(tag, temp=0.0, thinking=None):
        k = keymap.get(tag)
        c = json.loads(json.dumps(cfg))
        if k:
            c["deepseek"]["api_key"] = k
        c["deepseek"]["temperature"] = float(temp)
        if thinking is not None:
            c["deepseek"]["thinking"] = "enabled" if thinking else "disabled"
        return make_client(c)

    def budget_for(name):
        if name == "tree_v2" and args.tree_budget:
            return int(args.tree_budget)
        return budget

    tree_dir = args.tree_dir or os.path.join(args.out, "trees")
    os.makedirs(tree_dir, exist_ok=True)

    doc = open(args.doc, encoding="utf-8").read().strip()
    qspec = json.load(open(args.questions, encoding="utf-8"))
    questions = qspec["questions"]
    checklist = qspec.get("retell_checklist", {})
    if args.max_questions:
        questions = questions[:args.max_questions]
    structs = [s for s in args.structures.split(",") if s in STRUCTURES]

    print(f"[doc] {len(doc)} 字 / {len(questions)} 题 / 结构 {structs} / "
          f"scenario={args.scenario} / repeats={args.repeats} / "
          f"answer_temp={args.answer_temp} distill_temp={args.distill_temp} / "
          f"answer_thinking={args.answer_thinking} distill_thinking={args.distill_thinking} / "
          f"embed={embed_env or 'bge-small-zh-v1.5(default)'}", flush=True)
    new_doc, ledger = SCENARIOS[args.scenario](doc)
    print(f"[editstorm] {len(ledger)} 次编辑；新文档 {len(new_doc)} 字\n"
          f"{render_ledger(ledger)}", flush=True)

    enc = _load_encoder()
    if enc is None and any(s in structs for s in ("flat", "raptor")):
        print("[警告] 向量模型不可用 → flat/raptor 将退化为空检索", flush=True)

    judge_llm = make_client(json.loads(json.dumps(cfg))) if args.judge else None
    if judge_llm is not None:
        cfg_j = json.loads(json.dumps(cfg))
        cfg_j["deepseek"]["temperature"] = float(args.judge_temp)
        judge_llm = make_client(cfg_j)

    t_start = time.time()
    results = {"model": getattr(base_llm, "model", "?"), "doc": args.doc,
               "scenario": args.scenario, "n_questions": len(questions),
               "repeats": args.repeats, "answer_temp": args.answer_temp,
               "distill_temp": args.distill_temp, "judge": args.judge,
               "answer_thinking": args.answer_thinking,
               "distill_thinking": args.distill_thinking,
               "splitter": args.splitter,
               "embed_model": embed_env or "bge-small-zh-v1.5(default)",
               "ledger": ledger, "phase1": {}, "edit_time": {},
               "phase3": {}, "retell": {}, "usage": {},
               "tokens": {"phase1": {}, "phase3": {}, "final": {}, "answer": {}, "distill": {}},
               "answers": {}, "qa_pass": {}, "trees": {}}

    def snap(store, key, arm):
        store[key] = arm_total_stats(arm)
        # 分路计量（作答侧 vs 蒸馏侧），供成本表精确二分
        results["tokens"]["answer"][key] = arm.llm.stats() if getattr(arm, "llm", None) else {}
        results["tokens"]["distill"][key] = (arm.distill_meter.stats()
                                             if getattr(arm, "distill_meter", None) else {})

    def save_tree_checkpoint(arm, label):
        if getattr(arm, "eng", None) is None:
            return
        path = os.path.join(tree_dir, f"{label}.jsonl")
        arm.eng.dump_tree(path)
        results["trees"][label] = path

    def save_tree_gzip(arm, label):
        if getattr(arm, "eng", None) is None:
            return
        from tree_io import save_tree
        path = os.path.join(tree_dir, f"{label}.json.gz")
        save_tree(arm.eng, path)
        results["trees"][label] = path

    # ── Phase 1：四结构基线 Q/A ───────────────────────────
    print("\n===== Phase 1：基线 Q/A（原始文档）=====", flush=True)
    base_arms = {}
    snapshots = {}
    for name in structs:
        arm = make_arm(name,
                       client_for(name, args.distill_temp, distill_thinking),
                       client_for(name, args.answer_temp, answer_thinking),
                       cfg, budget_for(name), retr_budget, enc, doc,
                       tag=f"{name}·base", leaf_expand=args.leaf_expand,
                       flat_topk=args.flat_topk, raptor_topk=args.raptor_topk,
                       edit_signal=edit_signal, splitter=args.splitter)
        base_arms[name] = arm
        save_tree_checkpoint(arm, f"{name}·base")
        save_tree_gzip(arm, f"{name}·base")    # gzip 快照：供后续零重建复用（tree_io.load_tree）
        snapshots[name] = arm.snapshot()      # 每遍作答前恢复（基线各遍独立；用后残留 = 单遍使用史）
        runs = run_qa_multi(arm, questions, "before", args.repeats, judge_llm,
                            snap=snapshots[name])
        results["phase1"][name] = aggregate(name, runs)
        results["answers"][f"{name}·p1"] = {
            r["id"]: [run[i]["answer"] for run in runs]
            for i, r in enumerate(runs[0])}
        results["qa_pass"][f"{name}·p1"] = {
            r["id"]: [int(run[i]["pass"]) for run in runs]
            for i, r in enumerate(runs[0])}
        results["usage"][f"{name}·p1"] = [{"id": r["id"], **r["usage"]}
                                          for run in runs for r in run]
        v = results["phase1"][name]
        print(f"  {name:8s} pass={v['pass']}/{v['n']} mean={v.get('mean_rate',0):.0%} "
              f"±{v.get('std_rate',0):.0%} 每遍={v['per_repeat_pass']} {v['layers']}", flush=True)
        snap(results["tokens"]["phase1"], f"{name}·base", arm)

    # ── Phase 2：编辑风暴（1 次）+ 延续臂更新 ─────────────
    print("\n===== Phase 2：编辑风暴 → 台账 =====", flush=True)
    for name in structs:
        arm = base_arms[name]
        if args.edit_signal_ab and getattr(arm, "eng", None) is not None:
            snapshots[f"preedit_{name}"] = arm.snapshot()   # 编辑前 warm 树（A/B 同源）
        dt = arm.apply_ledger(ledger)
        save_tree_checkpoint(arm, f"{name}·inc_edited")
        save_tree_gzip(arm, f"{name}·inc_edited")   # pre-settle 快照：改 settle 时可零重建重放
        ds = arm.settle()
        save_tree_checkpoint(arm, f"{name}·inc_settled")
        save_tree_gzip(arm, f"{name}·inc_final")
        results["edit_time"][name] = {"edit_ms": round(dt * 1000, 2),
                                      "settle_ms": round(ds * 1000, 2)}
        snapshots[f"inc_{name}"] = arm.snapshot()   # Phase2 结束点快照（供 Phase3/4 逐遍恢复）
        print(f"  {name:8s} 编辑路径 {dt*1000:8.2f} ms | 推迟蒸馏 {ds*1000:9.2f} ms",
              flush=True)

    # inc_cold_tree：无 Phase 1 使用史的旧文树 + 同一 apply_ledger。
    # 用来把「原地失效（inc_cold vs reb）」与「使用史连续性（inc_warm vs inc_cold）」两个因子分开
    # ——成本 = 一棵树的构建（编辑路径零 LLM）。
    cold_arms = {}
    if "tree_v2" in structs:
        carm = make_arm("tree_v2",
                        client_for("inc_cold_tree_v2", args.distill_temp, distill_thinking),
                        client_for("inc_cold_tree_v2", args.answer_temp, answer_thinking),
                        cfg, budget_for("tree_v2"), retr_budget, enc, doc,
                        tag="inc_cold_tree_v2", leaf_expand=args.leaf_expand,
                        flat_topk=args.flat_topk, edit_signal=edit_signal, splitter=args.splitter)
        cdt = carm.apply_ledger(ledger)
        cds = carm.settle()
        snapshots["inc_cold_tree_v2"] = carm.snapshot()
        save_tree_gzip(carm, "inc_cold_tree_v2")
        cold_arms["inc_cold_tree_v2"] = carm
        results["edit_time"]["inc_cold_tree_v2"] = {"edit_ms": round(cdt * 1000, 2),
                                                    "settle_ms": round(cds * 1000, 2)}
        print(f"  inc_cold 编辑路径 {cdt*1000:8.2f} ms | 推迟蒸馏 {cds*1000:9.2f} ms",
              flush=True)

    # A/B 额外臂（--edit-signal-ab）：编辑新原子给冷 HP（"当障碍"），与热身/冷树同源——
    # warm_obstacle 加载 preedit 快照 + 同一 apply_ledger；cold_obstacle 镜像 cold 臂。不跑勿用。
    if args.edit_signal_ab and "tree_v2" in structs:
        o = make_arm("tree_v2", client_for("inc_obstacle_tree_v2", args.distill_temp, distill_thinking),
                     client_for("inc_obstacle_tree_v2", args.answer_temp, answer_thinking), cfg,
                     budget_for("tree_v2"), retr_budget, enc, "", tag="inc_obstacle_tree_v2",
                     leaf_expand=args.leaf_expand, flat_topk=args.flat_topk,
                     build=False, edit_signal=False, splitter=args.splitter)
        o.load(snapshots["preedit_tree_v2"])
        o.apply_ledger(ledger)
        o.settle()
        snapshots["inc_obstacle_tree_v2"] = o.snapshot()
        save_tree_gzip(o, "inc_obstacle_tree_v2")
        cold_arms["inc_obstacle_tree_v2"] = o

        oc = make_arm("tree_v2", client_for("inc_cold_obstacle_tree_v2", args.distill_temp, distill_thinking),
                      client_for("inc_cold_obstacle_tree_v2", args.answer_temp, answer_thinking), cfg,
                      budget_for("tree_v2"), retr_budget, enc, doc,
                      tag="inc_cold_obstacle_tree_v2", leaf_expand=args.leaf_expand,
                      flat_topk=args.flat_topk, edit_signal=False, splitter=args.splitter)
        oc.apply_ledger(ledger)
        oc.settle()
        snapshots["inc_cold_obstacle_tree_v2"] = oc.snapshot()
        save_tree_gzip(oc, "inc_cold_obstacle_tree_v2")
        cold_arms["inc_cold_obstacle_tree_v2"] = oc
        print("  A/B 障碍臂：inc_obstacle_tree_v2 / inc_cold_obstacle_tree_v2", flush=True)

    # 重建臂：从新文档无状态重建
    print("\n===== Phase 3：8 臂 Q/A =====", flush=True)
    arms = {}
    for name in structs:
        arms[f"inc_{name}"] = base_arms[name]
        arms[f"reb_{name}"] = make_arm(name,
                                       client_for(f"reb_{name}", args.distill_temp, distill_thinking),
                                       client_for(f"reb_{name}", args.answer_temp, answer_thinking),
                                       cfg, budget_for(name), retr_budget, enc, new_doc,
                                       tag=f"reb_{name}", leaf_expand=args.leaf_expand,
                                       flat_topk=args.flat_topk, raptor_topk=args.raptor_topk,
                                       edit_signal=edit_signal, splitter=args.splitter)
        save_tree_checkpoint(arms[f"reb_{name}"], f"reb_{name}")
        save_tree_gzip(arms[f"reb_{name}"], f"reb_{name}")          # 构建完成点 gzip
        snapshots[f"reb_{name}"] = arms[f"reb_{name}"].snapshot()   # 构建完成点快照
    arms.update(cold_arms)
    for aname, arm in arms.items():
        runs = run_qa_multi(arm, questions, "after", args.repeats, judge_llm,
                            snap=snapshots.get(aname))
        results["phase3"][aname] = aggregate(aname, runs)
        results["answers"][f"{aname}·p3"] = {
            r["id"]: [run[i]["answer"] for run in runs]
            for i, r in enumerate(runs[0])}
        results["qa_pass"][f"{aname}·p3"] = {
            r["id"]: [int(run[i]["pass"]) for run in runs]
            for i, r in enumerate(runs[0])}
        results["usage"][f"{aname}·p3"] = [{"id": r["id"], **r["usage"]}
                                           for run in runs for r in run]
        v = results["phase3"][aname]
        print(f"  {aname:14s} pass={v['pass']}/{v['n']} mean={v.get('mean_rate',0):.0%} "
              f"±{v.get('std_rate',0):.0%} 每遍={v['per_repeat_pass']} {v['layers']}", flush=True)
        snap(results["tokens"]["phase3"], aname, arm)

    # ── Phase 4：复述（8 臂）─────────────────────────────
    if not args.skip_retell:
        print("\n===== Phase 4：复述（8 臂，两层评分）=====", flush=True)
        for aname, arm in arms.items():
            snap_path = snapshots.get(aname)   # 勿用 snap（会遮蔽同名函数 snap()）
            texts = []
            for _ in range(max(1, args.repeats)):
                if snap_path:
                    arm.restore(snap_path)
                texts.append(run_retell(arm))
            runs_sc = [score_retell(t, checklist) for t in texts]
            last = runs_sc[-1]
            last["answer"] = texts[-1]
            last["per_repeat_coverage"] = [s["coverage_ratio"] for s in runs_sc]
            last["per_repeat_rot"] = [s["rot_ratio"] for s in runs_sc]
            if judge_llm is not None:
                last["judge_rot"] = judge_mod.judge_rot(checklist.get("forbid", []),
                                                        texts[-1], judge_llm)
                last["judge_hit"] = judge_mod.judge_retell(checklist.get("require", []),
                                                           texts[-1], judge_llm)
            results["retell"][aname] = last
            with open(os.path.join(args.out, f"retell_{aname}.txt"), "w", encoding="utf-8") as f:
                f.write(texts[-1])
            cov = sum(last["per_repeat_coverage"]) / len(runs_sc)
            rot = sum(last["per_repeat_rot"]) / len(runs_sc)
            print(f"  {aname:14s} 覆盖均值 {cov:.0%} 旧残留均值 {rot:.0%} "
                  f"({last['chars']} 字) 漏={last['new_miss']} 残留={last['old_leak']}", flush=True)

    # ── 计量 ─────────────────────────────────────────────
    for aname, arm in arms.items():
        snap(results["tokens"]["final"], aname, arm)
    results["elapsed_s"] = round(time.time() - t_start, 1)

    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n[done] {results['elapsed_s']}s → {args.out}/results.json", flush=True)

    print("\n===== 汇总（Q/A pass 率，N 遍均值±std）=====")
    for phase in ("phase1", "phase3"):
        print(f"  [{phase}]")
        for k, v in results[phase].items():
            print(f"    {k:14s} {v['pass']}/{v['n']} mean={v.get('mean_rate',0):.0%} "
                  f"±{v.get('std_rate',0):.0%} 每遍={v['per_repeat_pass']} {v['layers']}")
    print("\n===== 编辑时刻成本 =====")
    for k, v in results["edit_time"].items():
        print(f"  {k:8s} 编辑路径 {v['edit_ms']:9.2f} ms | 推迟蒸馏 {v['settle_ms']:10.2f} ms")
    print("\n===== 计量（tokens，含蒸馏侧；phase3 增量 = final - phase1）=====")
    p1 = results["tokens"]["phase1"]
    for k, v in results["tokens"]["final"].items():
        base = p1.get(f"{k.split('_', 1)[1]}·base", {}) if k.startswith("inc_") else {}
        dp = v["prompt_tokens"] - base.get("prompt_tokens", 0)
        dc = v["completion_tokens"] - base.get("completion_tokens", 0)
        print(f"  {k:14s} final calls={v['calls']:3d} prompt={v['prompt_tokens']:8d} "
              f"completion={v['completion_tokens']:6d} 缓存命中={v.get('cache_hit_rate')} "
              f"| phase3增量 prompt={dp:8d} completion={dc:6d}")


if __name__ == "__main__":
    main()
