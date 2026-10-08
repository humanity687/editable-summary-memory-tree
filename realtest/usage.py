"""usage.py — 使用信号层（Usage Signal Layer）：从输出提取使用边（sidecar，影子模式）。

设计规格：`realtest/USAGE_SPEC.md` v0.3（Phase A 实证 2026-09-26 + 三条修正已拍）。
本模块 = 纯函数核心（切句/分档/覆盖跨度/非冗余判定）+ `UsageLayer`（记录层）。

铁律：
- 零 LLM（切句/匹配/复读/非冗余全确定性）；
- **影子模式**：只记边、不进读路径；不改引擎任何状态（HP/激活/结构/组装）；
- 运行时与离线分析同源：`chatbench/usage_replay.py` 直接 import 本模块的纯函数。

信号三档（USAGE_SPEC §3 + §5.2① 修正）：
  T1 引用句     显式 `(依据:#N6039 "摘录")` → 直接读 ID，最高权；
  T2 未注入命中 命中卡最近屏上祖先的覆盖跨度 > cover_span_max（供给分辨率不足）；
  T3 改写命中   命中卡本身在屏或覆盖跨度 ≤ cover_span_max；
  复读（bigram Jaccard ≥ reread_threshold）不记——输出由注入内容生成，属复读非使用。
派生 A↔B 边仅记**非冗余对**：树亲缘外 且 端点相似度 < θ_nonredundant。
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter

# ── 参数台账（USAGE_SPEC §8，先拍后调）────────────────────────
DEFAULTS = {
    "match_score_floor": 0.25,   # A2 先拍：匹配分 p50≈0.30–0.34
    "match_topk": 3,             # 单句最多匹配卡数
    "cover_span_max": 60,        # T2/T3 覆盖跨度分档线（A2 提案①）
    "reread_threshold": 0.8,     # 复读分割线（复用 find_similar 已校准值）
    "theta_nonredundant": 0.58,  # A↔B 非冗余阈值（A1 recall 边 p25 先拍）
    "w_cite": 3.0,               # 三档边权（先拍）
    "w_uninj": 2.0,
    "w_rewrite": 1.0,
    "max_pairs_per_unit": 12,    # 单句端点两两对上限（防组合爆炸）
    "bring_topk": 5,             # Phase C 交集带动：候选上限（沿用旧 hub k=5）
}

# ── 结构感知切句（USAGE_SPEC §3）────────────────────────────

_FENCE = "```"
_OPENERS = {"（": "）", "(": ")", "[": "]", "【": "】"}
_CLOSERS = set("）)]】")
_QUOTE_OPEN = "「『“`\""
_QUOTE_MAP = {"「": "」", "『": "』", "“": "”", "`": "`", '"': '"'}


def clean_unit(s):
    s = (s or "").strip().strip("`*_ \t")
    s = re.sub(r"^[#>\-*•]+\s*", "", s)
    s = re.sub(r"^\d+[\.、）)]\s*", "", s)
    return s.strip()


def split_units(text):
    """按句末标点/换行切分；引用括号、引号、代码块/行内代码内的标点不切。"""
    from realtest.realengine import bigrams
    units, buf = [], []
    depth, quote, in_fence = 0, None, False
    i, n = 0, len(text or "")
    while i < n:
        if in_fence:
            if text.startswith(_FENCE, i):
                in_fence = False
                buf.append(_FENCE)
                i += 3
            else:
                buf.append(text[i])
                i += 1
            continue
        if text.startswith(_FENCE, i):
            in_fence = True
            buf.append(_FENCE)
            i += 3
            continue
        ch = text[i]
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in _OPENERS:
            depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch in _CLOSERS:
            depth = max(0, depth - 1)
            buf.append(ch)
            i += 1
            continue
        if depth == 0 and ch in _QUOTE_OPEN:
            quote = _QUOTE_MAP[ch]
            buf.append(ch)
            i += 1
            continue
        if depth == 0 and ch in "。！？!?…\n；;":
            buf.append("" if ch == "\n" else ch)
            units.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        units.append("".join(buf))
    return [u for u in (clean_unit(x) for x in units) if u and len(bigrams(u)) > 0]


CITE_ID_RE = re.compile(r"#N(\d+)|#(\d+)|(?<![\w#])N(\d+)")


def parse_cite_ids(text):
    out = []
    for m in CITE_ID_RE.finditer(text or ""):
        g = m.group(1) or m.group(2) or m.group(3)
        out.append(int(g))
    return out


# ── 端点工具（ep = (id, type)）──────────────────────────────

def ep_text(eng, ep):
    i, t = ep
    if t == "atom":
        a = eng.atoms.get(i)
        return (a.value or "") if (a and a.alive) else None
    nd = eng.nodes.get(i)
    if not (nd and nd.alive) or not nd.value:
        return None
    return eng._fact_part(nd.value)


def ep_vec(eng, ep):
    i, t = ep
    try:
        if t == "atom":
            return eng._atom_vec(i) if i in eng.atoms and eng.atoms[i].alive else None
        if i in eng.nodes and eng.nodes[i].alive and eng.nodes[i].value is not None:
            return eng._node_vec(i)
    except Exception:
        return None
    return None


def cos(a, b):
    if a is None or b is None:
        return None
    return float(a @ b)


def is_ancestor(eng, anc, desc):
    """desc 的 owner/parent 链上是否有 anc（树亲缘：祖先摘要覆盖后代内容）。"""
    if anc == desc:
        return True
    if desc in eng.atoms:
        o = eng.atoms[desc].owner
    elif desc in eng.nodes:
        nd = eng.nodes.get(desc)
        o = nd.parent if nd else None
    else:
        return False
    hops = 0
    while o is not None and hops < 300:
        if o == anc:
            return True
        p = eng.nodes.get(o)
        o = p.parent if p else None
        hops += 1
    return False


def tree_related(eng, a, b):
    return is_ancestor(eng, a, b) or is_ancestor(eng, b, a)


def nearest_screen_ancestor(eng, i, screen_ids, pos):
    """供给分辨率：命中卡本身在屏 → self；否则找最近屏上祖先节点，返回其 span 跨度
    （覆盖原子数，越大 = 供给越粗；-1 = 无屏上祖先）。
    架构事实（Phase A）：clean tiling 恒覆盖全文 → T2 二值定义恒空，供给差别是分辨率。"""
    if i in screen_ids:
        return {"self": True, "cover_id": None, "cover_span": 0}
    if i in eng.atoms:
        o = eng.atoms[i].owner
    elif i in eng.nodes:
        nd = eng.nodes.get(i)
        o = nd.parent if nd else None
    else:
        o = None
    hops = 0
    while o is not None and hops < 200:
        if o in screen_ids:
            first, last = eng._first_atom(o), eng._last_atom(o)
            if first in pos and last in pos:
                sp = abs(pos[last] - pos[first]) + 1
            else:
                sp = -1
            return {"self": False, "cover_id": o, "cover_span": sp}
        p = eng.nodes.get(o)
        o = p.parent if p else None
        hops += 1
    return {"self": False, "cover_id": None, "cover_span": -1}


def screen_bigrams(eng, screen_ids):
    from realtest.realengine import bigrams
    bg = {}
    for i in screen_ids:
        if i in eng.atoms:
            a = eng.atoms[i]
            if a.alive:
                bg[i] = bigrams(a.value or "")
        elif i in eng.nodes:
            nd = eng.nodes[i]
            if nd.alive and nd.value:
                bg[i] = bigrams(eng._fact_part(nd.value))
    return bg


# ── 单元分档（运行时与离线分析共用）─────────────────────────

def classify_unit(eng, unit, *, screen_ids, scr_bg, cited_ids, pos,
                  floor=None, cover_span_max=None, topk=None, reread_threshold=None):
    """单个输出单元 → row + stats 增量（Counter）。
    row: {"s", "tier": T1|reread|match|nomatch, "cites", "cites_bad", "matches"}"""
    p = DEFAULTS
    floor = p["match_score_floor"] if floor is None else floor
    cover_span_max = p["cover_span_max"] if cover_span_max is None else cover_span_max
    topk = p["match_topk"] if topk is None else topk
    reread_threshold = p["reread_threshold"] if reread_threshold is None else reread_threshold
    from realtest.realengine import bigrams
    stats = Counter()
    ub = bigrams(unit)
    cites = [i for i in dict.fromkeys(parse_cite_ids(unit))]
    cites_ok, cites_bad = [], []
    for i in cites:
        if (i in eng.atoms and eng.atoms[i].alive) or \
           (i in eng.nodes and eng.nodes[i].alive):
            cites_ok.append(i)
        else:
            cites_bad.append(i)
    if cites_ok:
        stats["units_t1"] += 1
        stats["edges_t1"] += len(cites_ok)
        return {"s": unit, "tier": "T1", "cites": cites_ok, "cites_bad": cites_bad}, stats
    for i, bg in scr_bg.items():
        if not bg:
            continue
        j = len(ub & bg) / max(len(ub), len(bg))
        if j >= reread_threshold:
            stats["units_reread"] += 1
            return {"s": unit, "tier": "reread", "hit": i, "j": round(j, 3)}, stats
    pool = eng._retrieval_pool(unit)
    ranked = sorted(pool.items(), key=lambda kv: -kv[1])
    matches = []
    for i, sc in ranked[:max(topk * 2, 6)]:
        if sc < floor:
            break
        t = "atom" if i in eng.atoms else "node"
        cov = nearest_screen_ancestor(eng, i, screen_ids, pos)
        sp = cov["cover_span"]
        tier = "T3" if (cov["self"] or 0 <= sp <= cover_span_max) else "T2"
        stats[("m_self" if cov["self"] else
               "m_uncovered" if sp < 0 else
               "m_cover_le10" if sp <= 10 else
               "m_cover_le30" if sp <= 30 else
               "m_cover_le60" if sp <= 60 else
               "m_cover_le120" if sp <= 120 else
               "m_cover_le300" if sp <= 300 else "m_cover_gt300")] += 1
        matches.append({"id": i, "type": t, "score": round(float(sc), 4), "tier": tier,
                        "self": cov["self"], "cover": cov["cover_id"], "cover_span": sp,
                        "cited": i in cited_ids,
                        "text": (ep_text(eng, (i, t)) or "")[:60]})
        if len(matches) >= topk:
            break
    if matches:
        stats["units_t2"] += 1 if any(m["tier"] == "T2" for m in matches) else 0
        stats["units_t3"] += 1 if any(m["tier"] == "T3" for m in matches) else 0
        stats["edges_t2"] += sum(1 for m in matches if m["tier"] == "T2")
        stats["edges_t3"] += sum(1 for m in matches if m["tier"] == "T3")
        return {"s": unit, "tier": "match", "matches": matches}, stats
    stats["units_nomatch"] += 1
    return {"s": unit, "tier": "nomatch", "matches": []}, stats


def derive_unit_pairs(eng, row, theta):
    """单句内端点两两对 → 非冗余对（树亲缘外 且 sim < θ）。
    返回 [(a, b, sim, tier)]；按 sim 升序（最非冗余优先）。"""
    eps = list(row.get("cites") or []) + [m["id"] for m in (row.get("matches") or [])]
    eps = list(dict.fromkeys(eps))
    tier_of = {i: "T1" for i in (row.get("cites") or [])}
    for m in (row.get("matches") or []):
        tier_of.setdefault(m["id"], m["tier"])
    out = []
    for x in range(len(eps)):
        for y in range(x + 1, len(eps)):
            a, b = eps[x], eps[y]
            if tree_related(eng, a, b):
                continue
            key = (a, b) if a <= b else (b, a)
            s = cos(ep_vec(eng, (a, "atom" if a in eng.atoms else "node")),
                    ep_vec(eng, (b, "atom" if b in eng.atoms else "node")))
            if s is None or s >= theta:
                continue
            tiers = sorted({tier_of[a], tier_of[b]}, key=("T1", "T2", "T3").index)
            out.append((a, b, round(s, 4), "+".join(tiers)))
    out.sort(key=lambda x: x[2])
    return out


# ── 读路径：交集带动（Phase C，纯读）────────────────────────

def intersection_bring(anchors, pairs, topk=5, exclude=None):
    """direct 命中集为锚 → 沿非冗余 A↔B 边一跳取邻居（候选累计边权降序 topk）。

    item-based 协同过滤语义："上次与当前命中证据共同被引用过的卡"。
    与 query 相关性无关（不退回全局相似度排序），避开 conn_weight 的错配命门。
    纯读：不改任何状态。pairs 形如 UsageLayer.pairs 或 {key: {"weight", ...}}。"""
    anchors_set = set(anchors or ())
    ex = set(exclude or ()) | anchors_set
    score = {}
    for key, d in (pairs or {}).items():
        a, b = key
        w = float(d.get("weight", 0.0))
        if a in anchors_set and b not in ex:
            score[b] = score.get(b, 0.0) + w
        if b in anchors_set and a not in ex:
            score[a] = score.get(a, 0.0) + w
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    return [i for i, _ in ranked[:topk]]


# ── 记录层（sidecar，影子模式）──────────────────────────────

class UsageLayer:
    """输出使用信号记录层：S→A 使用边 + 派生非冗余 A↔B 边。

    影子模式：只写自己的内存结构与 JSONL 审计，**不修改引擎任何状态**，
    不进组装/检索/蒸馏任何路径（读路径 Phase C 才议）。
    live 挂载：输出已进树（source=ai 原子）→ S 引用原子 ID；
    offline 影子重放：S 为合成记录（s_ref=None → 负序号）。
    """

    def __init__(self, engine, out_dir=None, params=None, on_event=None):
        self.eng = engine
        self.p = dict(DEFAULTS)
        self.p.update(params or {})
        self.edges = {}      # (s_ref, aid) -> weight（S→A）
        self.pairs = {}      # (a, b) -> {"weight", "uses", "tiers"}
        self.outputs = []    # 内存镜像（审计）
        self.on_event = on_event   # 事件总线出口（如 cooc.note_usage）
        self._seen_s = set()
        self._seq = 0
        self.out_dir = out_dir
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        self._f_out = self._open("usage_outputs.jsonl")
        self._f_edge = self._open("usage_edges.jsonl")
        self._f_pair = self._open("usage_pairs.jsonl")

    def _open(self, name):
        if not self.out_dir:
            return None
        return open(os.path.join(self.out_dir, name), "a", encoding="utf-8")

    def _sink(self, f, row):
        if f is not None:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()   # 进程退出不保证 flush（daemon 线程/终止顺序），每写必落盘

    def close(self):
        for f in (self._f_out, self._f_edge, self._f_pair):
            try:
                if f:
                    f.close()
            except Exception:
                pass

    def _tier_weight(self, tier):
        return {"T1": self.p["w_cite"], "T2": self.p["w_uninj"],
                "T3": self.p["w_rewrite"]}.get(tier, 1.0)

    def record_output(self, units, injected_ids, pos=None, round_no=None):
        """units = [(text, s_ref|None)]；injected_ids = 本轮模型看到的屏上卡集合
        （组装 emission 的 item 集，含铺陈与影子）。返回 stats。
        s_ref 去重：同一输出原子只记一次。"""
        eng = self.eng
        r = self.eng.round if round_no is None else round_no
        if pos is None:
            pos = {item: idx for idx, item in enumerate(eng.chain_ids())}
        screen_ids = set(injected_ids or ())
        scr_bg = screen_bigrams(eng, screen_ids)
        stats = Counter()
        n_units = 0
        for text, s_ref in units:
            s_key = s_ref if s_ref is not None else -(self._seq + 1)
            if s_key in self._seen_s:
                continue
            self._seen_s.add(s_key)
            if s_ref is None:
                self._seq += 1
            n_units += 1
            row, st = classify_unit(
                eng, text, screen_ids=screen_ids, scr_bg=scr_bg, cited_ids=set(),
                pos=pos, floor=self.p["match_score_floor"],
                cover_span_max=self.p["cover_span_max"], topk=self.p["match_topk"],
                reread_threshold=self.p["reread_threshold"])
            stats.update(st)
            out_row = {"round": r, "s_ref": s_ref, "s_text": text[:300],
                       "tier": row["tier"]}
            if row["tier"] == "T1":
                out_row["cites"] = row["cites"]
                out_row["cites_bad"] = row.get("cites_bad") or []
            elif row["tier"] == "reread":
                out_row["reread_hit"] = [row["hit"], row["j"]]
            else:
                out_row["matches"] = row.get("matches") or []
            self.outputs.append(out_row)
            self._sink(self._f_out, out_row)
            # S→A 边（T1 引用 + T2/T3 匹配全记，可审计）
            endpoints = []
            for i in row.get("cites") or []:
                endpoints.append((i, "T1", None, None))
            for m in row.get("matches") or []:
                endpoints.append((m["id"], m["tier"], m["score"], m["cover_span"]))
            for i, tier, score, cov in endpoints:
                key = (s_key, i)
                w = self._tier_weight(tier)
                self.edges[key] = self.edges.get(key, 0.0) + w
                self._sink(self._f_edge, {
                    "round": r, "s_ref": s_ref, "item": i,
                    "item_type": "atom" if i in eng.atoms else "node",
                    "tier": tier, "score": score, "cover_span": cov,
                    "weight": w, "s_text": text[:120]})
            # 派生 A↔B：仅非冗余对
            for a, b, sim, tiers in derive_unit_pairs(
                    eng, row, self.p["theta_nonredundant"])[:self.p["max_pairs_per_unit"]]:
                d = self.pairs.setdefault((a, b), {"weight": 0.0, "uses": 0,
                                                   "tiers": set()})
                d["weight"] += max(self._tier_weight(t) for t in tiers.split("+"))
                d["uses"] += 1
                d["tiers"].update(tiers.split("+"))
                self._sink(self._f_pair, {
                    "round": r, "s_ref": s_ref, "a": a, "b": b, "sim": sim,
                    "tiers": tiers, "weight": d["weight"]})
            # 事件总线（协同层）：S 的端点与分档 → cooc 主题层等订阅者
            if self.on_event is not None and endpoints:
                best = {}
                order = {"T1": 3, "T2": 2, "T3": 1}
                for i, tier, _score, _cov in endpoints:
                    ep = (i, "atom" if i in eng.atoms else "node")
                    if ep not in best or order.get(tier, 0) > order.get(best[ep], 0):
                        best[ep] = tier
                self.on_event({
                    "round": r, "s_ref": s_ref, "s_text": text,
                    "endpoints": [(i, t, tier) for (i, t), tier in best.items()]})
        stats["units_total"] = n_units   # 已去重后的实际处理单元数（与分档计数同口径）
        return dict(stats)

    def bring(self, anchors, topk=None, exclude=None):
        """读路径（Phase C）：交集带动。影子模式阶段默认不挂读路径——
        由实验方显式调用（live 接入待判别实验通过后）。纯读。"""
        topk = self.p["bring_topk"] if topk is None else topk
        return intersection_bring(anchors, self.pairs, topk=topk, exclude=exclude)

    def stat(self):
        return {"outputs": len(self.outputs), "s": len(self._seen_s),
                "edges": len(self.edges), "pairs": len(self.pairs),
                "tiers": dict(Counter(e["tier"] for e in self.outputs))}
