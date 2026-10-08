#!/usr/bin/env python3
"""replay.py — ChatBench 回放器：多会话/单文档灌入 + 带引用分析作答。

核心协议（公平性）：
  Phase 0  灌入（时间序，按会话边界衰减 + 蒸馏）
  Stage W  会话边界跑 usage 查询（独立于测试题的分析式问题，累积 β 边）
  Stage T  全部会话后跑测试题（带原子引用作答；确定性校验引用）

用法：
  python3 realtest/chatbench/replay.py --corpus .../corpus.jsonl \
      --questions .../questions_absence.json --max-sessions 5 \
      --cooc off --provider deepseek --out .../out/ms_off
"""
import argparse
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _REALTEST)

from llmclient import make_client
from realengine import RealEngine, engine_kwargs
from textutil import norm_cjk as norm
from cooc import ConceptEngine
from splitter import split_sentences
from protocol import parse_response
from tree_io import save_tree, load_tree

SYSTEM = """你是长程记忆的分析助手。你的全部记忆来自【记忆流】（每轮全新组装，不依赖对话历史）。
【记忆流格式】N12·摘要 为摘要卡；#34·原文 为原文句卡；缩进表示层级；"召回影子"为检索注入的旧内容。
【作答纪律】
1. 只依据记忆流内容作答，禁止编造；找不到就明确说"记忆流中无此信息"。
2. 每个结论后面标注来源：**优先引用原文原子**，格式 (依据:#原子ID "不超过30字原文摘录")；
   只有当依据只在摘要卡里时才引用摘要卡 (依据:N卡ID "摘录")，并注明"（摘要）"。
3. 简洁、分点。"""


CITE_RE = re.compile(r"[#N](\d+)")


def cite_check(eng, answer):
    """确定性引用校验：答案中的 #原子id / N摘要id 必须存活；分类原子/摘要引用。"""
    cited = CITE_RE.findall(answer or "")
    atom_ok = node_ok = 0
    bad = []
    for cid in cited:
        iid = int(cid)
        a, nd = eng.atoms.get(iid), eng.nodes.get(iid)
        if a and a.alive:
            atom_ok += 1
        elif nd and nd.alive:
            node_ok += 1
        else:
            bad.append(iid)
    return {"citations": len(cited), "valid_ids": atom_ok + node_ok,
            "atom": atom_ok, "node": node_ok, "bad": bad[:20]}


def cited_texts(eng, answer):
    """保存被引卡原文（T2 claim 支持度判定需要确定性证据文本）。"""
    out = []
    for cid in sorted({int(x) for x in CITE_RE.findall(answer or "")}):
        a, nd = eng.atoms.get(cid), eng.nodes.get(cid)
        if a and a.alive:
            out.append([cid, "atom", (a.value or "")[:400]])
        elif nd and nd.alive:
            out.append([cid, "node", (nd.value or "")[:400]])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--corpus", default=os.path.join(_HERE, "corpus", "corpus.jsonl"))
    ap.add_argument("--questions", default=None)
    ap.add_argument("--max-sessions", type=int, default=0, help=">0 只回放前 N 个会话")
    ap.add_argument("--slice-chars", type=int, default=0, help=">0 按字符切片（单文档模式）")
    ap.add_argument("--cooc", choices=["on", "off"], default="off")
    ap.add_argument("--selforg", action="store_true",
                    help="协同层：输出使用信号 → cooc 主题层（需 --cooc on；默认关）")
    ap.add_argument("--provider", choices=["deepseek", "ollama"], default="deepseek")
    ap.add_argument("--model", default=None)
    ap.add_argument("--temp", type=float, default=0.0,
                    help="作答温度（公平性：两臂应一致；默认 0）")
    ap.add_argument("--budget", type=int, default=16000)
    ap.add_argument("--retr-budget", type=int, default=8)
    ap.add_argument("--merge-every", type=int, default=40, help="会话内每 N 条消息衰减+蒸馏")
    ap.add_argument("--tick", type=int, default=6)
    ap.add_argument("--usage-passes", type=int, default=3, help="Stage W：usage 查询重复轮数")
    ap.add_argument("--max-recall-rounds", type=int, default=10,
                    help="作答期自助检索上限（[recall] 迭代轮数）")
    ap.add_argument("--save-tree", default=None, help="Phase 0 后把树缓存到该路径（避免重复建树）")
    ap.add_argument("--load-tree", default=None, help="存在则直接载入树，跳过 Phase 0")
    ap.add_argument("--progress-every", type=int, default=50, help="每 N 条消息打印一次进度")
    ap.add_argument("--out", default=None)
    ap.add_argument("--minutes", type=float, default=180.0)
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg["provider"] = args.provider
    if args.model:
        cfg[args.provider]["model"] = args.model
    cfg.setdefault(args.provider, {})["temperature"] = args.temp
    llm = make_client(cfg)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    outdir = args.out or os.path.join(_HERE, "out",
                                      f"{args.cooc}_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(outdir, exist_ok=True)

    # ── 载入消息（会话切片 or 字符切片）──
    msgs = [json.loads(l) for l in open(args.corpus, encoding="utf-8")]
    if args.max_sessions:
        use = [m for m in msgs if m.get("session", 0) < args.max_sessions]
    elif args.slice_chars:
        use, chars = [], 0
        for m in msgs:
            if chars >= args.slice_chars:
                break
            use.append(m)
            chars += len(m["content"])
    else:
        use = msgs
    n_sess = (max(m.get("session", 0) for m in use) + 1) if use else 0
    print(f"[replay] {len(use)} 条消息 / {n_sess} 会话 / "
          f"{sum(len(m['content']) for m in use)} 字", flush=True)

    qspec = {}
    if args.questions and os.path.exists(args.questions):
        qspec = json.load(open(args.questions, encoding="utf-8"))
    usage = qspec.get("usage_queries", [])

    kw = engine_kwargs(cfg, args.budget, args.retr_budget)
    if args.cooc == "on":
        eng = ConceptEngine(llm, cooc_out=os.path.join(outdir, "cooc"),
                            cooc_params=dict(cfg.get("cooc", {})),
                            selforg=args.selforg,
                            usage_out=(os.path.join(outdir, "usage")
                                       if args.selforg else None),
                            **kw)
    else:
        eng = RealEngine(llm, **kw)

    t0 = time.time()
    usage_calls = 0

    # ── Phase 0：灌入（可缓存/复用树）──
    loaded = bool(args.load_tree and os.path.exists(args.load_tree))
    if loaded:
        load_tree(eng, args.load_tree)
        print(f"[tree] 载入缓存树 {args.load_tree}: 原子 {len(eng.atoms)}, "
              f"节点 {eng.stat()['nodes']['alive']}, 轮 {eng.round}", flush=True)
    else:
        cur = None
        for i, m in enumerate(use):
            if m.get("session") != cur:
                if cur is not None:
                    eng.tick(args.tick)
                    eng.merge_pass()            # 会话边界蒸馏
                    print(f"  [边界] 会话 {cur} 完成：原子 {len(eng.atoms)}, "
                          f"节点 {eng.stat()['nodes']['alive']}, "
                          f"蒸馏 {eng.distill['calls']}, {time.time()-t0:.0f}s", flush=True)
                cur = m.get("session")
            units = [(s, {"source": m["name"]}) for s in split_sentences(m["content"]) if s]
            if units:
                eng.insert_units(units, left_id=eng.tail)
            if (i + 1) % args.merge_every == 0:
                eng.tick(args.tick)
                sig = eng.merge_signal()
                if sig == "force":
                    eng.merge_pass(force_out=True)
                elif sig == "regular":
                    eng.merge_pass()
            if (i + 1) % args.progress_every == 0:
                print(f"  [ingest {i+1}/{len(use)}] 会话 {m.get('session')} | "
                      f"原子 {len(eng.atoms)} 节点 {eng.stat()['nodes']['alive']} "
                      f"蒸馏 {eng.distill['calls']} | {time.time()-t0:.0f}s", flush=True)
        eng.tick(args.tick)
        eng.merge_pass()
        if args.save_tree:
            save_tree(eng, args.save_tree)
            print(f"[tree] 已缓存 → {args.save_tree}", flush=True)

    def do_usage():
        nonlocal usage_calls
        for q in usage:
            eng.retrieve(q, budget=args.retr_budget)
            usage_calls += 1

    # ── Stage W：密集使用（真实“反复查”，公平地长共现边）──
    for _p in range(args.usage_passes):
        do_usage()
        eng.tick(1)
        eng.merge_pass()                        # on 侧触发概括创建
    h = eng._heights()
    print(f"[Phase 0+W] 原子 {len(eng.atoms)}, 节点 {eng.stat()['nodes']['alive']}, "
          f"高 {max(h.values(), default=0)}, 铺陈 {eng.stat()['tiling_size']}, "
          f"usage×{usage_calls}（{args.usage_passes} 轮）, "
          f"蒸馏 {eng.distill['calls']} 次/{eng.distill['seconds']:.0f}s", flush=True)

    # ── Stage T：测试题（带引用）──
    cooc_t_created = 0

    def _tap_concepts():
        """T 阶段检索后触发概括创建：检索本身即使用，多查询把共现边连成簇，
        让“检索对检索”在同一场实验里长出主题节点（on 侧有效，off 侧无 cooc 为 no-op）。"""
        nonlocal cooc_t_created
        if hasattr(eng, "cooc"):
            cooc_t_created += eng.cooc.maybe_create()

    def answer(q, min_claims=0, extra="", probe=None):
        """带自助检索的作答：模型可 [recall] 迭代（≤max_recall_rounds，支持一次多个），影子累加。"""
        if hasattr(eng, "cooc"):
            eng.cooc.begin_session(q)   # 会话边界：同一问题下的多角度检索
        shadows, direct_all, hub_all, hub_detail_all = [], [], [], []
        hits = eng.retrieve(probe or q, budget=args.retr_budget)
        _tap_concepts()
        direct_all += list(getattr(eng, "last_direct", None) or hits)
        hub_all += list(getattr(eng, "last_hub", []) or [])
        hub_detail_all += list(getattr(eng, "last_hub_detail", []) or [])
        shadows += list(hits)
        recall_used, text = 0, ""
        recall_left = args.max_recall_rounds
        for _round in range(args.max_recall_rounds + 2):
            allow = recall_left > 0
            emitted = eng.assemble(shadows or None, budget=args.budget)
            flow = eng.render_emission(emitted)
            rule = (f"记忆流不足时可**一次给出多个** [recall]关键词[/recall] 再检索"
                    f"（剩余检索次数 {recall_left}）；"
                    if allow else "请直接作答，不要再使用 recall。")
            body = f"至少给出 {min_claims} 条，逐条带引用。" if min_claims else "直接作答，逐条带引用。"
            ask = f"{flow}\n\n【当前任务】\n问题：{q}\n{extra}\n{rule}{body}"
            text, _u, _l = llm.chat(SYSTEM, ask)
            recalls = [b[1] for b in parse_response(text) if b[0] == "recall"]
            use = recalls[:recall_left]
            if use:
                for rq in use:
                    h2 = eng.retrieve(rq, budget=args.retr_budget)
                    _tap_concepts()
                    direct_all += list(getattr(eng, "last_direct", None) or h2)
                    hub_all += list(getattr(eng, "last_hub", []) or [])
                    hub_detail_all += list(getattr(eng, "last_hub_detail", []) or [])
                    shadows = list(dict.fromkeys(shadows + list(h2)))
                    recall_used += 1
                    recall_left -= 1
                continue
            break
        # 协同层：答案文本 → 使用事件（S 端点/分档 → 主题层）；随后检查概念触发
        if getattr(eng, "usage", None) is not None and text:
            from realtest.usage import split_units
            eng.usage.record_output([(u, None) for u in split_units(text)],
                                    injected_ids=shadows or [])
            _tap_concepts()
        return {"q": q, "answer": text, "recall_used": recall_used,
                "direct": list(dict.fromkeys(direct_all)),
                "hub": list(dict.fromkeys(hub_all)),
                "hub_detail": list({d[0]: d for d in hub_detail_all}.values()),
                "n_retrieved": len(shadows),
                "concepts": len(eng.cooc.concepts) if hasattr(eng, "cooc") else 0,
                "cite_check": cite_check(eng, text),
                "cited_texts": cited_texts(eng, text)}

    answers = []
    for it in qspec.get("t1", []):
        if (time.time() - t0) / 60 >= args.minutes:
            break
        r = answer(it["q"]); r["id"] = it["id"]; r["layer"] = it.get("type", "a")
        r["gold_frags"] = it.get("gold_frags", [])
        r["gold_text"] = it.get("gold_text", "")
        r["exact_hit"] = any(norm(f) in norm(r["answer"]) for f in r["gold_frags"])
        answers.append(r)
        print(f"  T1 {it['id']}({r['layer']}): exact={r['exact_hit']} "
              f"cite={r['cite_check']['valid_ids']}/{r['cite_check']['citations']}", flush=True)
    for it in qspec.get("t2", []):
        if (time.time() - t0) / 60 >= args.minutes:
            break
        r = answer(it["q"], min_claims=it.get("require_min", 3))
        r["id"] = it["id"]; r["layer"] = "t2"; r["require_min"] = it.get("require_min", 3)
        answers.append(r)
        print(f"  T2 {it['id']}: cite={r['cite_check']['valid_ids']}/{r['cite_check']['citations']}",
              flush=True)
    # T3 报告验证：逐条判定 支持/反驳/无据 + 引用（待验证陈述冻结在题库）
    for it in qspec.get("t3", []):
        if (time.time() - t0) / 60 >= args.minutes:
            break
        claims = it.get("claims", [])
        numbered = "\n".join(f"{i+1}. {c['claim']}" for i, c in enumerate(claims))
        q = it.get("q", "请逐条核查下列陈述与记忆流是否一致。")
        probe = q + " " + " ".join(c["claim"][:30] for c in claims[:3])
        extra = (f"\n【待验证陈述】\n{numbered}\n\n"
                 f"对每条输出一行：`编号. 判定`（判定只能是 支持/反驳/无据），"
                 f"并在其后用 (依据:#卡ID \"摘录\") 标注依据；依据不足写 无据。")
        r = answer(q, extra=extra, probe=probe)
        r["id"] = it["id"]; r["layer"] = "t3"
        r["claims"] = [c["claim"] for c in claims]
        r["labels"] = [c.get("label", "support") for c in claims]
        answers.append(r)
        print(f"  T3 {it['id']}: claims={len(claims)} recall×{r.get('recall_used')} "
              f"cite={r['cite_check']['valid_ids']}/{r['cite_check']['citations']}", flush=True)

    # 审计流显式收尾（usage/cooc 的 JSONL 句柄；不依赖进程退出 flush）
    for _obj in (getattr(eng, "usage", None), getattr(eng, "cooc", None)):
        try:
            if _obj is not None:
                _obj.close()
        except Exception:
            pass

    result = {
        "mode": args.cooc, "provider": args.provider, "model": getattr(llm, "model", "?"),
        "n_messages": len(use), "n_sessions": n_sess,
        "session_window": qspec.get("_meta", {}),
        "atoms": len(eng.atoms), "nodes": eng.stat()["nodes"]["alive"],
        "max_height": max(h.values(), default=0),
        "elapsed_s": round(time.time() - t0, 1),
        "usage_calls": usage_calls,
        "answers": answers,
    }
    if args.cooc == "on":
        result["cooc"] = {"edges": len(eng.cooc.edges), "concepts": len(eng.cooc.concepts),
                          "concepts_created_in_T": cooc_t_created,
                          "member_sizes": [len(c["members"]) for c in eng.cooc.concepts.values()],
                          "hub_calls": sum(1 for r in eng.cooc.recall_log if r["via"] == "hub")}
        if getattr(eng, "usage", None) is not None:
            result["cooc"]["usage"] = eng.usage.stat()
            result["cooc"]["concept_titles"] = [c["title"]
                                                for c in eng.cooc.concepts.values()]
            via_counts = {}
            cp = os.path.join(outdir, "cooc", "cooc_concepts.jsonl")
            if os.path.exists(cp):
                with open(cp, encoding="utf-8") as f:
                    for line in f:
                        row = json.loads(line)
                        if row.get("op") == "bring":
                            via_counts[row.get("via")] = via_counts.get(row.get("via"), 0) + 1
            result["cooc"]["via_counts"] = via_counts
    with open(os.path.join(outdir, "replay_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    with open(os.path.join(outdir, "answers.txt"), "w", encoding="utf-8") as f:
        for a in answers:
            f.write(f"===== {a['id']} [{a.get('layer')}] {a['q']} =====\n{a['answer']}\n"
                    f"[引用校验] {a['cite_check']} | exact={a.get('exact_hit')}\n\n")
    print(f"\n[完成] {result['elapsed_s']}s → {outdir}", flush=True)


if __name__ == "__main__":
    main()
