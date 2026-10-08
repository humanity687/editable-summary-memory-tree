"""cooc.py — 概括节点 Demo：使用驱动共现中介层（v2 之上的纯增量）。

设计权威：`概括节点 Demo — 设计规格 v1.0`。
核心取舍：概括节点只做匹配中介，本身不被激活、永不进组装流；off = RealEngine
（逐字节等价），on = ConceptEngine(RealEngine)；core.py / realengine.py 零改动。

三结构（内存 + JSONL 审计，非 SQLite——与 v2 内存架构一致）：
  recall_log / cooc_edges / concepts
"""
from __future__ import annotations

import json
import os
import re
from collections import deque
from itertools import combinations

from realtest.realengine import RealEngine

# ── 参数（Demo v1.0 §6，全部先拍后调；协同层 2026-09-26 拍定）─────
DEFAULTS = {
    "beta": 2.0,          # 跨查询召回共现增益（同查询内不再配对：相似度冗余）
    "gamma": 3.0,         # 编辑共现增益
    "eps": 0.1,             # 边衰减 / 轮
    "theta_coo": 5.0,       # 簇阈值（预注册默认）
    "reclaim_base": 0.5,    # 概括创建后簇内边重置值（预注册默认）
    "dead_ratio": 0.5,      # 死成员禁用阈值
    "j": 3,                 # 概括匹配数
    "k": 5,                 # 带动硬上限（预注册默认）
    "N": 6,                 # 触发：簇最小规模（2026-09-26：8→6，先看具体情况）
    "R": 10,                # 触发：需求窗口（轮）（预注册默认）
    "M": 2,                 # 触发：窗口内簇内共现事件数（usage 事件 + 跨查询召回）
    "max_concepts": 50,     # 全局概括上限（防爆炸）
    "max_new_per_pass": 1,  # 单次 maybe_create 最多新建概括数（保守值）
    "trigger_q_max": 5,   # 概括 prompt 纳入的触发问题数
    "trigger_s_max": 5,   # 概括 prompt 纳入的触发输出数（T1/T2；T3 不进）
    "trigger_q_rounds": 10,
    "theta_nr": 0.58,     # 跨查询召回对的非冗余阈值（与 usage 层同尺）
    "w_cite": 3.0,        # 使用边档权（T1 引用）
    "w_uninj": 2.0,       # （T2 分辨率不足命中）
    "w_rewrite": 1.0,     # （T3 改写命中；只进边，不进成员证据分）
}

CONCEPT_PROMPT = (
    "你是记忆概括器。下面是一组因被反复同时使用而关联的记忆卡，"
    "以及触发这些关联的输出句（系统先前的输出，可能带有证据引用）。"
    "请概括这组记忆的共同主题。\n"
    "输出严格两行：\n"
    "【标题】<不超过15字>\n"
    "【概括】<不超过150字，保留专有名词与关键事实，只写中性事实，"
    "不评价、不编造、不用第一人称>\n"
    "概括角度优先贴近这些输出所关心的方面。\n"
    "【触发输出】\n{outputs}\n"
    "【触发问题】\n{queries}\n"
    "【记忆卡】\n{members}"
)


def _ep_key(a, b):
    """端点 (id,type) 无序对的规范键。"""
    return (a, b) if a <= b else (b, a)


class CoocLayer:
    """共现层：边积累/衰减、概括创建/收割/冻结、检索匹配与带动。

    所有写操作都通过事件钩子（召回/编辑）驱动；读操作只在 ConceptEngine.retrieve
    末尾调用 bring，绝不修改位置树状态。"""

    def __init__(self, engine, out_dir=None, params=None):
        self.engine = engine
        self.p = dict(DEFAULTS)
        self.p.update(params or {})
        self.edges = {}                 # key -> affinity
        self.concepts = {}              # cid -> dict
        self.recall_log = []            # 内存镜像（审计同时落 JSONL）
        self._cooc_events = deque()     # (round, key) 共现事件（需求判定：召回/使用）
        self._pending_edit = set()      # 本编辑轮触碰的卡端点
        self._pending_recall = set()    # 本编辑轮召回命中卡端点（广义编辑）
        # 协同层（2026-09-26 拍定）：会话边界 + 使用事件 + 成员证据
        self._session = None            # 显式 begin_session 的会话键；None = 查询独立
        self._session_hits = {}         # query_text -> set(ep)（会话内跨查询配对）
        self._output_log = deque(maxlen=500)   # (round, s_text, epset, tier_map)
        self._evidence = {}             # ep -> 成员证据分（被共同使用累计；T1/T2）
        self.last_bring_detail = []     # 最近一次 bring 的 [(item_id, via, edge_weight)]
        self._seq = 0
        self.out_dir = out_dir
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        self._f_recall = self._open("recall_log.jsonl")
        self._f_edge = self._open("cooc_edges.jsonl")
        self._f_concept = self._open("cooc_concepts.jsonl")

    def _open(self, name):
        if not self.out_dir:
            return None
        return open(os.path.join(self.out_dir, name), "a", encoding="utf-8")

    def _sink(self, f, row):
        if f is not None:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()

    def close(self):
        for f in (self._f_recall, self._f_edge, self._f_concept):
            try:
                if f:
                    f.close()
            except Exception:
                pass

    # ── 端点工具 ────────────────────────────────────────

    def _etype(self, item):
        return "atom" if self.engine._is_atom(item) else "node"

    def _alive(self, ep):
        i, t = ep
        if t == "atom":
            a = self.engine.atoms.get(i)
            return bool(a and a.alive)
        n = self.engine.nodes.get(i)
        return bool(n and n.alive)

    def _bringable(self, ep):
        """非脏、非占位（原子永可带动）。"""
        if not self._alive(ep):
            return False
        if ep[1] == "node":
            nd = self.engine.nodes[ep[0]]
            return (not nd.dirty) and (not nd.placeholder) and (nd.value is not None)
        return True

    # ── Step A/B：召回事件埋点 + β 边（2026-09-26 收紧：仅跨查询对）──

    def begin_session(self, key):
        """显式会话边界：同一问题下的多角度检索。
        会话内**不同查询文本**的命中集两两配对（可能承载真联系）；同查询文本不配对
        （同查询命中集内 = 语义相似冗余）。未开会话 → 查询独立，不产生 recall 边。"""
        self._session = key
        self._session_hits = {}

    def end_session(self):
        self._session = None
        self._session_hits = {}

    def _cross_pair_ok(self, a, b):
        """跨查询对非冗余闸：端点相似度 < theta_nr；算不出相似度则保守记录。"""
        from realtest.usage import ep_vec, cos
        s = cos(ep_vec(self.engine, a), ep_vec(self.engine, b))
        return s is None or s < self.p["theta_nr"]

    def log_recall(self, q, direct, pool):
        r = self.engine.round
        for i in direct:
            self._recall_entry({
                "round": r, "query": (q or "")[:200], "hit_id": i,
                "hit_type": self._etype(i),
                "score": round(float(pool.get(i, 0.0)), 4), "via": "direct",
            })
        endpoints = [(i, self._etype(i)) for i in direct]
        eps = set(endpoints)
        if self._session is not None and endpoints:
            qtext = (q or "")[:200]
            prior = set()
            for qt, hits in self._session_hits.items():
                if qt != qtext:
                    prior |= hits
            for a in endpoints:
                for b in prior:
                    if a != b and self._cross_pair_ok(a, b):
                        self._add_pair(a, b, self.p["beta"], "recall_cross")
            self._session_hits.setdefault(qtext, set()).update(eps)
        # 需求事件 = 一次召回（一个共现“会话”），不与 pair 数混淆
        if endpoints:
            self._cooc_events.append((r, frozenset(endpoints)))
        self._pending_recall |= set(endpoints)
        return endpoints

    # ── Step B2：使用事件（UsageLayer on_event）────────────────

    def note_usage(self, event):
        """输出使用事件 → 边 / 成员证据 / 触发输出 / 需求事件。
        event = {round, s_ref, s_text, endpoints:[(id,type,tier)]}。
        T3 只进边（最低权），不进成员证据分（防低信噪匹配抬高代表性）。"""
        r = int(event.get("round", self.engine.round))
        s_text = (event.get("s_text") or "").strip()
        order = {"T1": 3, "T2": 2, "T3": 1}
        best = {}
        for i, t, tier in event.get("endpoints") or []:
            ep = (i, t)
            if ep not in best or order.get(tier, 0) > order.get(best[ep], 0):
                best[ep] = tier
        if not best:
            return
        epset = set(best)
        self._output_log.append((r, s_text, frozenset(epset), dict(best)))
        if len(epset) < 2:
            return
        self._cooc_events.append((r, frozenset(epset)))
        wmap = {"T1": self.p["w_cite"], "T2": self.p["w_uninj"],
                "T3": self.p["w_rewrite"]}
        for ep, tier in best.items():
            if tier in ("T1", "T2"):
                self._evidence[ep] = self._evidence.get(ep, 0.0) + wmap[tier]
        for a, b in combinations(sorted(epset), 2):
            w = max(wmap.get(best[a], 1.0), wmap.get(best[b], 1.0))
            self._add_pair(a, b, w, "usage")

    def _recall_entry(self, row):
        self.recall_log.append(row)
        self._sink(self._f_recall, row)

    def _log_hub(self, q, items):
        for i in items:
            self._recall_entry({
                "round": self.engine.round, "query": (q or "")[:200], "hit_id": i,
                "hit_type": self._etype(i), "score": None, "via": "hub",
            })

    # ── Step B：边写路径（增/衰减/删除/收割）─────────────

    def _add_pair(self, a, b, delta, reason):
        if a == b:
            return
        key = _ep_key(a, b)
        self.edges[key] = self.edges.get(key, 0.0) + delta
        self._sink(self._f_edge, {
            "round": self.engine.round, "op": "add", "reason": reason,
            "a": key[0], "b": key[1], "delta": delta,
            "affinity": round(self.edges[key], 3),
        })

    def note_edit(self, endpoints):
        if self.engine._rollback:
            return
        self._pending_edit |= set(endpoints)

    def flush_edit_batch(self):
        """轮边界 flush：仅当本轮有编辑时，对 广义编辑集（触碰原子 ∪ 召回命中卡）
        两两 +γ——这正是 (c) 层“伏笔被召回→编辑回收”的跨位置连接来源。"""
        if self._pending_edit:
            union = set(self._pending_edit) | set(self._pending_recall)
            for a, b in combinations(sorted(union), 2):
                self._add_pair(a, b, self.p["gamma"], "edit")
        self._pending_edit.clear()
        self._pending_recall.clear()

    def decay(self, n=1):
        d = self.p["eps"] * n
        if d <= 0:
            return
        dead = [k for k, v in self.edges.items() if v - d <= 0]
        for k in dead:
            del self.edges[k]
        for k in list(self.edges):
            self.edges[k] -= d
        self._sink(self._f_edge, {
            "round": self.engine.round, "op": "decay", "n": n,
            "pruned": len(dead), "edges": len(self.edges),
        })

    # ── Step C：概括创建（簇 → 蒸馏 → 冻结 → 收割）───────

    def _components(self, thr):
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for (a, b), v in self.edges.items():
            if v >= thr:
                union(a, b)
        comps = {}
        for x in list(parent):
            comps.setdefault(find(x), []).append(x)
        return [c for c in comps.values() if len(c) >= 2]

    def _demand(self, comp):
        """近 R 轮内，命中集合与簇交集 ≥2 的召回次数（一次召回算一次共现会话）。"""
        r0 = self.engine.round - self.p["R"]
        cs = set(comp)
        return sum(1 for (rd, hits) in self._cooc_events
                   if rd > r0 and len(hits & cs) >= 2)

    def maybe_create(self):
        """创建所有满足条件（规模 + 需求）的簇，单次最多 max_new_per_pass 个。
        每次创建后簇边软衰减 → 该簇解体，下一轮扫描自然落到别的簇。"""
        created = 0
        for _ in range(self.p["max_new_per_pass"]):
            if len(self.concepts) >= self.p["max_concepts"]:
                break
            comps = [c for c in self._components(self.p["theta_coo"])
                     if len(c) >= self.p["N"] and self._demand(c) >= self.p["M"]]
            if not comps:
                break
            comps.sort(key=len, reverse=True)
            self._create(comps[0])
            created += 1
        return created

    def _member_text(self, ep):
        i, t = ep
        if t == "atom":
            return (self.engine.atoms[i].value or "").strip()
        nd = self.engine.nodes[i]
        return self.engine._fact_part(nd.value or "")

    def _trigger_queries(self, comp):
        ids = {i for i, _ in comp}
        r0 = self.engine.round - self.p["trigger_q_rounds"]
        qs = []
        for row in reversed(self.recall_log):
            if row["round"] < r0:
                break
            if row.get("hit_id") in ids and row.get("query"):
                if row["query"] not in qs:
                    qs.append(row["query"])
            if len(qs) >= self.p["trigger_q_max"]:
                break
        return qs

    def _trigger_outputs(self, comp):
        """触发输出（概括角度参考）：窗口中命中簇成员的 S 文本；
        T3 不进（低信噪匹配），T1/T2 按档优先，上限 trigger_s_max。"""
        ids = {i for i, _ in comp}
        r0 = self.engine.round - self.p["trigger_q_rounds"]
        order = {"T1": 0, "T2": 1}
        cand = []
        for (r, s_text, _epset, tiers) in reversed(self._output_log):
            if r < r0:
                break
            hit_tiers = [tiers[ep] for ep in tiers if ep[0] in ids]
            if not hit_tiers:
                continue
            rank = min((order.get(t, 9) for t in hit_tiers), default=9)
            if rank >= 9:
                continue   # 仅 T3 命中 → 不进
            if s_text:
                cand.append((rank, s_text))
        cand.sort(key=lambda x: x[0])
        out = []
        for _rank, s_text in cand:
            if s_text not in out:
                out.append(s_text)
            if len(out) >= self.p["trigger_s_max"]:
                break
        return out

    def _create(self, comp):
        members = []
        # 成员序 = 使用证据分（被共同使用累计；T1/T2）主序 → conn_weight 次序
        for ep in comp:
            w = sum(v for (a, b), v in self.edges.items()
                    if (a == ep or b == ep) and (a in comp and b in comp))
            members.append({"id": ep[0], "type": ep[1], "conn_weight": round(w, 3),
                            "evidence": round(self._evidence.get(ep, 0.0), 3)})
        members.sort(key=lambda m: (-m["evidence"], -m["conn_weight"], m["id"]))
        parts = [self._member_text((m["id"], m["type"])) for m in members]
        parts = [p for p in parts if p]
        queries = self._trigger_queries(comp)
        outputs = self._trigger_outputs(comp)
        title, body = self.engine._concept_summarize(parts, queries, outputs=outputs)
        if not body:
            return
        emb = self.engine._concept_embed(title + "\n" + body)
        self._seq += 1
        cid = self._seq
        self.concepts[cid] = {
            "id": cid, "title": title, "content": body, "embedding": emb,
            "members": members, "status": "active",
            "trigger_queries": queries, "trigger_outputs": outputs,
            "created_round": self.engine.round,
        }
        # 收割：簇内边重置回基线（< θ_coo → 簇解体，后续增长触发新概括，防反复蒸馏）
        reclaimed = 0
        for (a, b) in list(self.edges):
            if a in comp and b in comp:
                if self.edges[(a, b)] > self.p["reclaim_base"]:
                    reclaimed += 1
                self.edges[(a, b)] = self.p["reclaim_base"]
        self._sink(self._f_concept, {
            "round": self.engine.round, "op": "create", "concept": cid,
            "title": title, "member_count": len(members),
            "members": members, "trigger_queries": queries,
            "trigger_outputs": outputs, "reclaimed": reclaimed,
        })

    # ── Step D：读路径（概括分池匹配 → 按 conn_weight 带动）──

    def _refresh_status(self, cid):
        c = self.concepts[cid]
        alive = sum(1 for m in c["members"] if self._alive((m["id"], m["type"])))
        ratio_dead = 1.0 - (alive / max(1, len(c["members"])))
        new = "disabled" if ratio_dead > self.p["dead_ratio"] else "active"
        if new != c["status"]:
            c["status"] = new
            self._sink(self._f_concept, {
                "round": self.engine.round, "op": new, "concept": cid,
                "dead_ratio": round(ratio_dead, 3),
            })

    def _on_screen(self):
        return {item for _kind, item, _v in self.engine._emit()}

    def bring(self, q, direct):
        """概括只做匹配中介：按 embedding 相似度选 top-j；成员选择两层——
        ① 交集层：与 direct 锚有非冗余边的成员（边权降序）；
        ② 补齐层：不足 k 时按冻结证据序补齐。
        区分只进日志（via），渲染保持干净。返回补齐的成员 id（按位置序）。"""
        if not self.concepts:
            return []
        qv = self.engine._query_vec(q)
        if qv is None:
            return []
        direct_ids = set(direct)
        on_screen = self._on_screen()
        scored = []
        for cid, c in self.concepts.items():
            self._refresh_status(cid)
            if c["status"] != "active" or c["embedding"] is None:
                continue
            scored.append((float(c["embedding"] @ qv), cid))
        scored.sort(key=lambda x: -x[0])
        committed, seen = [], set()
        detail = []   # 观测：[(item_id, via, edge_weight)]，供实验结果 via 归因
        for score, cid in scored[:self.p["j"]]:
            c = self.concepts[cid]
            self._sink(self._f_concept, {
                "round": self.engine.round, "op": "match", "concept": cid,
                "score": round(score, 4), "query": (q or "")[:80],
            })
            member_eps = [(m["id"], m["type"]) for m in c["members"]]
            member_set = set(member_eps)
            inter = {}
            for (a, b), v in self.edges.items():
                if a[0] in direct_ids and b in member_set and b[0] not in direct_ids:
                    inter[b] = inter.get(b, 0.0) + v
                elif b[0] in direct_ids and a in member_set and a[0] not in direct_ids:
                    inter[a] = inter.get(a, 0.0) + v
            ordered = [ep for ep, _ in sorted(inter.items(),
                                              key=lambda kv: (-kv[1], kv[0]))]
            for ep in member_eps:
                if ep not in ordered:
                    ordered.append(ep)
            for ep in ordered:
                if len(committed) >= self.p["k"]:
                    break
                if ep in seen or ep[0] in direct_ids or ep[0] in on_screen:
                    continue
                if not self._bringable(ep):
                    continue
                via = "intersection" if ep in inter else "fallback"
                seen.add(ep)
                committed.append(ep)
                detail.append((ep[0], via,
                               round(inter[ep], 3) if via == "intersection" else None))
                self._sink(self._f_concept, {
                    "round": self.engine.round, "op": "bring", "concept": cid,
                    "item": ep[0], "type": ep[1], "via": via,
                    "edge_weight": round(inter[ep], 3) if via == "intersection" else None,
                })
            if len(committed) >= self.p["k"]:
                break
        pos = self.engine._position_map()
        out = sorted((i for i, _t in committed), key=lambda i: pos.get(i, -1))
        self._log_hub(q, out)
        self.last_bring_detail = detail
        return out


class ConceptEngine(RealEngine):
    """on 模式：RealEngine + 共现中介层。所有钩子都是 super() 后追加，
    core.py / realengine.py 不受影响；概括带动不修改任何树状态（HP/激活/结构）。"""

    def __init__(self, llm, cooc_out=None, cooc_params=None, selforg=False,
                 usage_out=None, usage_params=None, **kwargs):
        super().__init__(llm, **kwargs)
        self.cooc = CoocLayer(self, out_dir=cooc_out, params=cooc_params)
        # 协同层（2026-09-26）：使用信号事件总线 → 主题层（S 端点/分档 → note_usage）
        self.usage = None
        if selforg:
            from realtest.usage import UsageLayer
            self.usage = UsageLayer(self, out_dir=usage_out, params=usage_params,
                                    on_event=self.cooc.note_usage)

    # 轮边界：ε 衰减 + 编辑批次 flush
    def tick(self, n=1):
        super().tick(n)
        self.cooc.decay(n)
        self.cooc.flush_edit_batch()

    # 蒸馏时机后检查概括触发
    def merge_pass(self, force_out=False):
        super().merge_pass(force_out)
        self.cooc.maybe_create()

    # 编辑钩子：记录触碰原子（广义编辑 flush 时并入）。
    # 铁律：写入/追加（write/ingest，尾部 append）不计 γ——§7.2 明确不做写入共现 α，
    # 否则初始灌入会把整篇的相邻句两两连边（位置相邻无信息量）。
    def delete(self, atom_ids):
        dead = super().delete(atom_ids)
        if dead:
            self.cooc.note_edit((i, "atom") for i in dead)
        return dead

    def insert(self, left_id, right_id, values=None, atoms=None):
        ids = super().insert(left_id, right_id, values=values, atoms=atoms)
        # 仅中插（right_id 非空）= 编辑；尾部追加（right_id is None）= 写入，排除
        if ids and right_id is not None:
            self.cooc.note_edit((i, "atom") for i in ids)
        return ids

    def replace(self, old_ids, new_values):
        new = super().replace(old_ids, new_values)   # 内部 delete/insert 已记旧卡
        if new:
            self.cooc.note_edit((i, "atom") for i in new)   # 兜底：尾部替换也能记新卡
        return new

    # 检索钩子：先跑 v2 直接检索（HP/激活照旧），再记日志 + 概括带动补影子
    def retrieve(self, q, budget=None):
        direct = super().retrieve(q, budget=budget)
        try:
            pool = self._retrieval_pool(q)
        except Exception:
            pool = {}
        self.cooc.log_recall(q, direct, pool)
        hub = self.cooc.bring(q, direct)
        self.last_direct = list(direct)   # 观测：供 bench 区分直接命中 vs 带动
        self.last_hub = list(hub)
        self.last_hub_detail = list(getattr(self.cooc, "last_bring_detail", []) or [])
        return (direct + hub) if hub else direct

    # ── 概括创建所需（复用 RealEngine 的 llm / encoder）──

    def _concept_summarize(self, parts, queries, outputs=None):
        content = "\n".join(f"- {p}" for p in parts if p)
        if len(content) > 3000:
            content = content[:3000] + "……（截断）"
        qs = "\n".join(f"- {q}" for q in queries) if queries else "（无）"
        outs = "\n".join(f"- {s[:120]}" for s in (outputs or [])) if outputs else "（无）"
        prompt = CONCEPT_PROMPT.format(outputs=outs, queries=qs, members=content)
        try:
            text, _usage, _lat = self.llm.chat("你是一个可靠的中文记忆概括器。", prompt)
        except Exception:
            return "", ""
        self.distill["calls"] += 1
        return self._parse_concept(text or "")

    @staticmethod
    def _parse_concept(text):
        m = re.search(r"【标题】\s*(.*?)\s*(?:【概括】|$)", text, re.S)
        b = re.search(r"【概括】\s*(.*?)(?:【触发问题】|$)", text, re.S)
        title = (m.group(1).strip() if m else "").splitlines()[0][:30] if m else ""
        body = (b.group(1).strip() if b else "").strip()
        if not body:
            body = text.strip()
        if not title:
            title = (body.splitlines()[0][:30] if body else "概括")
        return title, body

    def _concept_embed(self, text):
        enc = self._encoder_model()
        if enc is None:
            return None
        try:
            return enc.encode(text, normalize_embeddings=True)
        except Exception:
            return None
