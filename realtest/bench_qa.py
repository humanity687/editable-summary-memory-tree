#!/usr/bin/env python3
"""bench_qa.py — 结构化压力测试：三阶段（记忆体检 / 编辑风暴 / 混合循环）。

目标：测试记忆数据结构在真实 LLM 场景下是否（a）让模型正确理解上下文、
(b) 结构不崩塌。不接轨道B 等任何优化层。

阶段：
  0  种子灌入（seed/story.txt）→ 蒸馏成树（含摘要层）
  1a 记忆体检·自动 recall：事实题 → 测试脚本自动检索注入影子 → 模型作答（确定性评分）
  1b 记忆体检·手动 recall：模型自行 [recall] → 下一轮注入 → 作答
  2  编辑风暴（重度）：改名/删段/插句，每轮验证"脏窗口/新内容可见/墓碑不可见"
  3  混合循环：续写/编辑/检索/提问交替
贯穿：每轮结构性不变量断言（链序/发射覆盖/高度/脏精确/占位语义）——失败 = 结构崩塌。
"""
import argparse
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llmclient import make_client
from metrics import (Recorder, memory_snapshot, event_delta,
                     context_snapshot, summarize)
from protocol import parse_response
from realengine import RealEngine, engine_kwargs
from textutil import norm_cjk as norm
from report import build_charts, build_report
from splitter import split_sentences

SYSTEM = """你是长程记忆基准测试的应答 Agent。你的全部记忆来自【记忆流】（每轮全新组装，不依赖对话历史）。

【记忆流格式】
- N12·摘要 为摘要卡；#34·原文 为原文句卡；⊂Nx 表示其父卡；缩进表示层级；
- "召回影子"为检索注入的旧内容；"占位"表示该区域近期被修改、摘要暂缺；
- 记忆流按故事位置排序。

【输出协议】
- 提问轮：直接给出简短答案（1~2 句，只答事实，不解释）。若记忆流中没有答案信息，输出 [recall]查询关键词[/recall]。
- 续写轮：直接输出故事正文（将被追加），或使用 [edit #起id..#止id]新文本[/edit] / [recall]。

【纪律】
1. 答案必须依据记忆流内容作答；
2. 记忆流中看不到的信息，回答"记忆流中无此信息"，禁止编造；
3. 不要解释你的操作。"""


def parse_facts(path):
    facts = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0].startswith("id"):
            continue
        facts.append({
            "id": cells[0], "q": cells[1],
            "frags": [f.strip() for f in re.split(r"[；;]", cells[2]) if f.strip()],
            "cat": cells[3],
        })
    return facts


def parse_names(path):
    out = []
    for line in open(path, encoding="utf-8"):
        n = line.strip()
        if n and n not in out:
            out.append(n)
    return out


def score_answer(answer, frags):
    a = norm(answer)
    return sum(1 for f in frags if norm(f) in a), len(frags)


def story_paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def find_atoms_containing(eng, text, alive_only=True):
    return [aid for aid, a in eng.atoms.items()
            if (not alive_only or a.alive) and text in a.value]


def check_invariants(eng, dirty_expected, label):
    chain = eng.chain_ids()
    assert len(chain) == sum(1 for a in eng.atoms.values() if a.alive), "链长不一致"
    emitted = eng._emit()
    covered = []
    for kind, item, _ in emitted:
        if kind == "atom":
            covered.append(item)
        else:
            covered.extend(eng._subtree_alive_atoms(item))
    assert covered == chain, f"发射覆盖不一致 @{label}"
    heights = eng._heights()
    for nid, h in heights.items():
        nd = eng.nodes[nid]
        kid_h = [heights[c] for c in nd.children
                 if eng._is_node(c) and eng._child_alive(c)]
        assert h == (max(kid_h) + 1 if kid_h else 1), f"高度不一致 N{nid}"
    actual = {nid for nid, nd in eng.nodes.items() if nd.alive and nd.dirty}
    assert actual == dirty_expected, f"脏集合不精确 {actual} vs {dirty_expected}"
    for nid, nd in eng.nodes.items():
        if nd.alive and nd.placeholder and not nd.dirty:
            assert nd.value is None, f"占位语义 N{nid}"
    for item in eng.tiling:
        if eng._is_atom(item):
            assert eng.atoms[item].alive and eng.atoms[item].owner is None, "铺陈原子非法"
        else:
            assert eng.nodes[item].alive and eng.nodes[item].parent is None, "铺陈节点非法"


def chain_of_atoms(eng, ids):
    s = set()
    for aid in ids:
        s.update(eng._owner_chain(aid))
    return s


class Crash(Exception):
    pass


def content_of(eng, item):
    if eng._is_atom(item):
        return eng.atoms[item].value or ""
    return eng.nodes[item].value or ""


def retell_phase(eng, facts, do_retrieve, ask, parse_response, score_answer,
                 norm, label, max_recalls=3, budget=None, retr_budget=6):
    """复述阶段：≤max_recalls 次 recall 后尽量详细复述故事；按事实片段覆盖率评分。"""
    shadows_acc = []
    recall_used = 0
    answer = None
    for it in range(max_recalls + 1):
        if it == max_recalls:
            answer, _ = ask(
                "请尽量详细地复述整个故事（人物、地点、物品、事件顺序都要讲），"
                "直接输出复述，不要用 [recall]。",
                shadows=shadows_acc or None, allow_recall=False)
            break
        answer, _ = ask(
            "请先回忆故事全貌：记忆流信息不足时用 [recall] 查询（最多 3 次）；"
            "信息足够后直接开始详细复述整个故事。",
            shadows=shadows_acc or None)
        blocks = parse_response(answer)
        recalls = [b[1] for b in blocks if b[0] == "recall"]
        if recalls:
            recall_used += 1
            hits = do_retrieve(recalls[-1])
            shadows_acc = list(dict.fromkeys(shadows_acc + hits))
            continue
        break
    total = covered = 0
    miss = []
    for f in facts:
        for frag in f["frags"]:
            total += 1
            if norm(frag) in norm(answer or ""):
                covered += 1
            else:
                miss.append((f["id"], frag))
    return {
        "label": label, "recall_used": recall_used,
        "chars": len(answer or ""), "answer": answer or "",
        "coverage": f"{covered}/{total}",
        "coverage_ratio": round(covered / total, 3) if total else 0,
        "miss": miss,
    }


def main():



    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.json"))
    ap.add_argument("--seed-dir", default=os.path.join(os.path.dirname(__file__), "seed"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--minutes", type=float, default=25.0)
    ap.add_argument("--max-rounds", type=int, default=250)
    ap.add_argument("--facts-1a", type=int, default=25)
    ap.add_argument("--facts-1b", type=int, default=10)
    ap.add_argument("--edits", type=int, default=50)
    ap.add_argument("--phase3-cycles", type=int, default=5)
    ap.add_argument("--ingest-recall-p", type=float, default=0.25,
                    help="Phase 0 灌入期间每章随机触发 recall 的概率（模拟写作中回查）")
    ap.add_argument("--max-recall-rounds", type=int, default=3,
                    help="1b 每题最多迭代 recall 轮数（评测只看最终答案，recall 不计分）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cooc", choices=["on", "off"], default="off",
                    help="on=接入共现中介层（ConceptEngine）；off=RealEngine 基线")
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    llm = make_client(cfg)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    budget = int(cfg.get("assembly_budget", 12000))
    retr_budget = int(cfg.get("retrieval_budget", 6))

    outdir = args.out or os.path.join(os.path.dirname(__file__), "out",
                                      "bench_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(outdir, exist_ok=True)

    # 最新水循环记忆树参数（唯一真源：realengine.engine_kwargs）
    eng_kwargs = engine_kwargs(cfg, budget, retr_budget)
    if args.cooc == "on":
        from cooc import ConceptEngine
        eng = ConceptEngine(llm, cooc_out=os.path.join(outdir, "cooc"),
                            cooc_params=dict(cfg.get("cooc", {})), **eng_kwargs)
    else:
        eng = RealEngine(llm, **eng_kwargs)
    force_cooldown = int(cfg.get("force_cooldown_rounds", 8))
    last_force_round = -99

    story_path = os.path.join(args.seed_dir, "story.txt")
    facts_path = os.path.join(args.seed_dir, "facts.md")
    names_path = os.path.join(args.seed_dir, "names.txt")
    if not os.path.exists(story_path):
        print("[警告] 未找到正式种子（story.txt/facts.md/names.txt），"
              "使用 placeholder 占位种子冒烟——正式运行请先用提示词生成")
        story_path = os.path.join(args.seed_dir, "placeholder_story.txt")
        facts_path = os.path.join(args.seed_dir, "placeholder_facts.md")
        names_path = os.path.join(args.seed_dir, "placeholder_names.txt")
    story_text = open(story_path, encoding="utf-8").read().strip()
    facts = parse_facts(facts_path)
    names = parse_names(names_path)
    print(f"种子: {len(story_text)} 字, {len(facts)} 条事实, {len(names)} 个人名")

    rec = Recorder(outdir)
    responses_path = os.path.join(outdir, "responses.txt")
    rng = random.Random(args.seed)
    t0 = time.time()
    round_no = 0
    ev_prev = 0
    dirty_expected = set()
    results = {"1a": [], "1b": [], "2": [], "3": []}
    crash = None

    def ask(prompt, shadows=None, allow_recall=True):
        nonlocal round_no, ev_prev, dirty_expected
        round_no += 1
        user_ctx, emitted = build_context(eng, shadows, "", prompt, budget)
        # 组装超预算会触发 merge_pass 重建脏节点（最新引擎行为）→ 以实际脏集重同步
        dirty_expected = {nid for nid, nd in eng.nodes.items()
                          if nd.alive and nd.dirty}
        ctx_snap = context_snapshot(emitted, eng)
        t_llm = time.time()
        text, usage, lat = llm.chat(SYSTEM, user_ctx)
        lat_ms = (time.time() - t_llm) * 1000
        with open(responses_path, "a", encoding="utf-8") as f:
            f.write(f"===== 轮 {round_no} [{prompt[:40]}] =====\n{text}\n\n")
        eng.tick(1)
        check_invariants(eng, dirty_expected, f"r{round_no}")
        rec.record({
            "round": round_no, "phase": "ask", "llm_latency_ms": round(lat_ms, 1),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "ctx": ctx_snap, "mem": memory_snapshot(eng),
            "events": event_delta(eng, ev_prev),
            "tools": {"append": 0, "edit": 0, "recall": 0},
            "appended_chars": 0, "story_chars": 0, "story_tokens": 0,
            "duplicates": 0, "distill_calls": 0, "distill_seconds": 0,
            "recall": None, "blocks": [], "response_head": text[:200],
        })
        ev_prev = len(eng.events)
        rec.record_tree(eng, round_no)
        return text, ctx_snap

    def build_context(eng, shadows, receipt, task, b):
        emitted = eng.assemble(shadows, budget=b)
        flow = eng.render_emission(emitted)
        parts = [flow]
        if receipt:
            parts.append(f"【最近操作回执】\n{receipt}")
        parts.append(f"【当前任务】\n{task}")
        return "\n\n".join(parts), emitted

    def do_retrieve(q):
        nonlocal dirty_expected
        hits = eng.retrieve(q, budget=retr_budget)
        # 共现层：在“使用当下”评估概括触发（规格触发挂 merge_pass 之后，但本 bench
        # 的 tick 步长大（15/45/30），需求窗口会在 merge_pass 前过期 → 使用后即检查）
        if hasattr(eng, "cooc"):
            eng.cooc.maybe_create()
        dirty_expected = {nid for nid, nd in eng.nodes.items()
                          if nd.alive and nd.dirty}
        return hits

    try:
        # ── Phase 0：模拟写作灌入 ─────────────────────
        # 分批写（每段 = 一章）+ 章间衰减 + 周期性蒸馏 → 保留多层树；
        # 期间以概率触发随机 recall（模拟写作中回查旧情节）。
        paras = story_paragraphs(story_text)
        para_atoms = []
        rng = random.Random(args.seed)
        for i, p in enumerate(paras):
            sents = split_sentences(p)
            ids = eng.write(sents)
            para_atoms.append(ids)
            if args.ingest_recall_p > 0 and rng.random() < args.ingest_recall_p:
                f = rng.choice(facts)
                do_retrieve(f["q"])
            if i % 2 == 1:
                eng.tick(15)
                eng.merge_pass()
                dirty_expected = set()
                round_no += 1
                check_invariants(eng, dirty_expected, f"phase0 r{round_no}")
                h = eng._heights()
                print(f"  [Phase 0] 蒸馏点 {i // 2 + 1}: 节点 {eng.stat()['nodes']['alive']}, "
                      f"最大高 {max(h.values(), default=0)}")
        all_atoms = [a for para in para_atoms for a in para]
        h = eng._heights()
        print(f"[Phase 0] 写作灌入完成：{len(all_atoms)} 原子, "
              f"节点 {eng.stat()['nodes']['alive']}, 最大高 {max(h.values(), default=0)}, "
              f"铺陈 {eng.stat()['tiling_size']}")

        # ── Phase 1a：自动 recall 事实题 ──────────────
        print(f"[Phase 1a] {min(args.facts_1a, len(facts))} 题（自动检索注入）")
        for f in facts[:args.facts_1a]:
            if (time.time() - t0) / 60 >= args.minutes or round_no >= args.max_rounds:
                break
            hits = do_retrieve(f["q"])
            retrieved_text = " ".join(content_of(eng, h) for h in hits)
            ret_hit = any(norm(x) in norm(retrieved_text) for x in f["frags"])
            answer, _ = ask(f"问题：{f['q']}\n（已自动注入相关召回影子；直接作答）",
                            shadows=hits, allow_recall=False)
            got, need = score_answer(answer, f["frags"])
            results["1a"].append({"id": f["id"], "retrieval_hit": ret_hit,
                                  "answer_hit": got >= 1, "score": f"{got}/{need}"})
            print(f"  1a {f['id']}: 检索命中={ret_hit} 答案={got}/{need}")

        # ── Phase 1b：手动 recall 事实题（迭代式，可多次 recall，评测只看最终答案） ──
        print(f"[Phase 1b] {min(args.facts_1b, len(facts) - args.facts_1a)} 题"
              f"（模型自行 recall，可迭代 ≤{args.max_recall_rounds} 次）")
        for f in facts[args.facts_1a:args.facts_1a + args.facts_1b]:
            if (time.time() - t0) / 60 >= args.minutes or round_no >= args.max_rounds:
                break
            shadows_acc = []
            recall_used = 0
            answer = None
            for it in range(args.max_recall_rounds + 1):
                if it == args.max_recall_rounds:
                    answer, _ = ask(f"问题：{f['q']}\n（请直接作答）",
                                    shadows=shadows_acc or None, allow_recall=False)
                    break
                answer, _ = ask(
                    f"问题：{f['q']}\n（记忆流中没有答案就用 [recall] 查询，可多次；"
                    f"有把握时直接作答）",
                    shadows=shadows_acc or None)
                blocks = parse_response(answer)
                recalls = [b[1] for b in blocks if b[0] == "recall"]
                if recalls:
                    recall_used += 1
                    hits = do_retrieve(recalls[-1])
                    shadows_acc = list(dict.fromkeys(shadows_acc + hits))
                    continue
                break
            got, need = score_answer(answer or "", f["frags"])
            results["1b"].append({"id": f["id"], "used_recall": recall_used > 0,
                                  "recall_rounds": recall_used,
                                  "answer_hit": got >= 1, "score": f"{got}/{need}"})
            print(f"  1b {f['id']}: recall×{recall_used} 答案={got}/{need}")

        # ── Phase 1.5：复述（编辑前基线） ────────────────
        print("[Phase 1.5] 复述（编辑前基线，≤3 次 recall）", flush=True)
        retell_before = retell_phase(eng, facts, do_retrieve, ask, parse_response,
                                     score_answer, norm, "before_edits")

        # ── Phase 2：编辑风暴 ─────────────────────────
        print(f"[Phase 2] {args.edits} 次编辑（重度）")
        edits_done = 0
        edit_round = 0
        while edits_done < args.edits:
            if (time.time() - t0) / 60 >= args.minutes or round_no >= args.max_rounds:
                break
            edit_round += 1
            ops_this_round = 2
            receipts = []
            for _ in range(ops_this_round):
                if edits_done >= args.edits:
                    break
                kind = rng.choices(["rename", "delete", "insert"], weights=[0.6, 0.25, 0.15])[0]
                op = None
                if kind == "rename" and names:
                    name = rng.choice(names)
                    cand = find_atoms_containing(eng, name)
                    if cand:
                        aid = rng.choice(cand)
                        old_sentence = eng.atoms[aid].value
                        new_name = name[0] + "某"
                        new_text = old_sentence.replace(name, new_name, 1)
                        new_ids = eng.replace([aid], [new_text])
                        dirty_expected |= chain_of_atoms(eng, [aid]) | chain_of_atoms(eng, new_ids)
                        op = ("rename", aid, (old_sentence, new_name))
                elif kind == "delete":
                    para = rng.choice(para_atoms)
                    alive = [a for a in para if eng.atoms[a].alive]
                    if len(alive) >= 2:
                        k = rng.randint(1, min(len(alive), 4))
                        dead = alive[:k]
                        eng.delete(dead)
                        dirty_expected |= chain_of_atoms(eng, dead)
                        deleted_text = "".join(eng.atoms[d].value for d in dead)
                        op = ("delete", dead[0], deleted_text)
                else:
                    alive = [a for a in all_atoms if eng.atoms[a].alive]
                    if alive:
                        aid = rng.choice(alive)
                        nxt = eng.atoms[aid].next
                        marker = f"插入句{round_no}"
                        new_text = f"（{marker}）{rng.choice(names) if names else '他'}在门前站了片刻。"
                        new_ids = eng.insert(aid, nxt, [new_text])
                        dirty_expected |= chain_of_atoms(eng, new_ids)
                        op = ("insert", aid, marker)
                if op:
                    edits_done += 1
                    receipts.append(op)
            if not receipts:
                continue
            # 验证轮：针对本轮操作提问（问题自含、无歧义）
            q, expect = None, None
            r0 = receipts[0]
            if r0[0] == "rename":
                old_sentence, new_name = r0[2]
                frag = old_sentence[:14]
                q, expect = f"记忆流中，原来写「{frag}」的那句话，现在内容是什么？", new_name
            elif r0[0] == "delete":
                q, expect = "记忆流中，被删除的句子内容现在还能看到吗？", r0[2]
            else:
                q, expect = f"记忆流中，以「（{r0[2]}）」开头的那句话，完整内容是什么？", r0[2]
            answer, _ = ask(q if q else "本轮编辑后，记忆流有什么变化？")
            if r0[0] == "rename":
                got = norm(expect) in norm(answer)
            elif r0[0] == "delete":
                got = norm(expect) not in norm(answer)
            else:
                got = norm(expect) in norm(answer)
            results["2"].append({"edit": r0[0], "verify_ok": bool(got),
                                 "edits_done": edits_done})
            print(f"  2 r{edit_round} {r0[0]} 验证={'通过' if got else '失败'}"
                  f"（已编辑 {edits_done}）")
            # 条件式触发单一入口（force > regular）+ force_out 冷却
            sig = eng.merge_signal()
            triggered = False
            if sig == "force":
                if (round_no - last_force_round) >= force_cooldown:
                    eng.merge_pass(force_out=True)
                    last_force_round = round_no
                    triggered = True
                elif eng.merge_pressure():
                    eng.merge_pass()
                    triggered = True
            elif sig == "regular":
                eng.merge_pass()
                triggered = True
            if edit_round % 4 == 0 and not triggered:
                eng.merge_pass()   # 阶段边界定时蒸馏（与条件式触发互补）
            if triggered or edit_round % 4 == 0:
                dirty_expected = set()
                round_no += 1
                check_invariants(eng, dirty_expected, f"phase2 pass r{round_no}")
        # 风暴后冷却窗口（模拟自然时间流逝，让热区冷却 → 占位解除、原子重融合）
        # 新插入原子 HP=100：tick(45) 后 55 < hot_threshold(60) → 占位解除重建；
        # 再 tick(30) 后 25 < hp_merge_threshold(30) → 新原子参与合并重新分层。
        # 这是设计的"衰减后自动重新融合（自我修复）"路径，否则复述测到的是编辑瞬时碎片化。
        eng.tick(45)
        eng.merge_pass()
        dirty_expected = set()
        eng.tick(30)
        eng.merge_pass()
        dirty_expected = set()
        round_no += 1
        check_invariants(eng, dirty_expected, "phase2 final pass")
        print(f"[Phase 2] 完成 {edits_done} 次编辑；冷却窗口后节点 "
              f"{eng.stat()['nodes']['alive']}, 占位 {eng.stat()['placeholder']}", flush=True)

        # ── Phase 2.5：复述（编辑后） ──────────────────
        print("[Phase 2.5] 复述（编辑后，≤3 次 recall）", flush=True)
        retell_after = retell_phase(eng, facts, do_retrieve, ask, parse_response,
                                    score_answer, norm, "after_edits")
        results["retell"] = [retell_before, retell_after]
        for rt in results["retell"]:
            print(f"  复述[{rt['label']}]: recall×{rt['recall_used']} "
                  f"片段覆盖 {rt['coverage']} 复述 {rt['chars']} 字", flush=True)

        # ── Phase 3：混合循环 ─────────────────────────
        print(f"[Phase 3] {args.phase3_cycles} 个混合循环")
        for cyc in range(args.phase3_cycles):
            if (time.time() - t0) / 60 >= args.minutes or round_no >= args.max_rounds:
                break
            round_no += 1
            user_ctx, emitted = build_context(eng, None, "", "续写故事，推进情节（300~500 字）。", budget)
            ctx_snap = context_snapshot(emitted, eng)
            t_llm = time.time()
            text, usage, lat = llm.chat(SYSTEM, user_ctx)
            lat_ms = (time.time() - t_llm) * 1000
            with open(responses_path, "a", encoding="utf-8") as f:
                f.write(f"===== 轮 {round_no} [Phase3 续写 {cyc}] =====\n{text}\n\n")
            appended = 0
            for kind, payload in parse_response(text):
                if kind in ("text", "append"):
                    for s in split_sentences(payload):
                        if s:
                            eng.write([s])
                            appended += len(s)
                            dirty_expected |= chain_of_atoms(eng, eng.chain_ids()[-1:])
            eng.tick(1)
            # 条件式触发单一入口（force > regular）+ force_out 冷却
            sig = eng.merge_signal()
            if sig == "force":
                if (round_no - last_force_round) >= force_cooldown:
                    eng.merge_pass(force_out=True)
                    last_force_round = round_no
                    dirty_expected = set()
                elif eng.merge_pressure():
                    eng.merge_pass()
                    dirty_expected = set()
            elif sig == "regular":
                eng.merge_pass()
                dirty_expected = set()
            check_invariants(eng, dirty_expected, f"phase3 r{round_no}")
            rec.record({
                "round": round_no, "phase": "phase3", "llm_latency_ms": round(lat_ms, 1),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "ctx": ctx_snap, "mem": memory_snapshot(eng),
                "events": event_delta(eng, ev_prev),
                "tools": {"append": 1, "edit": 0, "recall": 0},
                "appended_chars": appended, "story_chars": 0, "story_tokens": 0,
                "duplicates": 0, "distill_calls": 0, "distill_seconds": 0,
                "recall": None, "blocks": [], "response_head": text[:200],
            })
            ev_prev = len(eng.events)
            rec.record_tree(eng, round_no)
            print(f"  3 循环 {cyc + 1}: 续写 {appended} 字")
            if (cyc + 1) % 2 == 0:
                eng.merge_pass()
                dirty_expected = set()
                round_no += 1
                check_invariants(eng, dirty_expected, f"phase3 pass r{round_no}")
    except AssertionError as ex:
        crash = {"type": "结构崩塌", "detail": str(ex), "round": round_no}
        print(f"\n!!! 结构崩塌 @轮 {round_no}: {ex}")
    except KeyboardInterrupt:
        print("\n[中断]")

    # ── 汇总 ──────────────────────────────────────────
    rec.close()
    rows = rec.load()
    tree_snaps = rec.load_tree()
    summary = summarize(rows, dict(cfg, model=getattr(llm, "model", "?")))
    summary["phases"] = {
        k: {"n": len(v), "pass": sum(1 for x in v if x.get("answer_hit") or x.get("verify_ok")),
            "retrieval_hit": sum(1 for x in v if x.get("retrieval_hit"))}
        for k, v in results.items()
    }
    summary["crash"] = crash
    if hasattr(eng, "cooc"):
        summary["cooc"] = {
            "edges": len(eng.cooc.edges),
            "concepts": len(eng.cooc.concepts),
            "concept_members": [len(c["members"]) for c in eng.cooc.concepts.values()],
            "hub_calls": sum(1 for r in eng.cooc.recall_log if r["via"] == "hub"),
        }
    charts = build_charts(rows, tree_snaps, outdir)
    build_report(outdir, rows, summary, charts, os.path.join(outdir, "story_full.txt"), summary["config"])
    with open(os.path.join(outdir, "story_full.txt"), "w", encoding="utf-8") as f:
        f.write(eng.story_full_text())
    with open(os.path.join(outdir, "phase_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    for rt in results.get("retell", []):
        with open(os.path.join(outdir, f"retell_{rt['label']}.txt"), "w", encoding="utf-8") as f:
            f.write(rt["answer"])
    print("\n===== 结果 =====")
    print(json.dumps(summary["phases"], ensure_ascii=False, indent=1))
    if results.get("retell"):
        print("\n===== 复述覆盖率 =====")
        for rt in results["retell"]:
            print(f"  [{rt['label']}] recall×{rt['recall_used']} 覆盖 {rt['coverage']} "
                  f"({rt['coverage_ratio']:.1%}) 复述 {rt['chars']} 字")
    if crash:
        print(f"!!! {crash['type']}: {crash['detail']} @轮 {crash['round']}")
    print(f"\n产出目录: {outdir}")


if __name__ == "__main__":
    main()
