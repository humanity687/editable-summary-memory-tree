"""tree_io.py — 记忆树持久化（避免重复灌入/蒸馏）。

只序列化 v2 树状态（原子/节点/铺陈/链/账本/事件），不序列化 cooc 层——
保证 off/on 两臂从**同一棵树**出发，只差共现层开关（公平）。
用法：
    from tree_io import save_tree, load_tree
    save_tree(eng, "out/tree.json.gz")
    load_tree(eng, "out/tree.json.gz")   # 原地恢复
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import re

from core import Atom, Node


def save_tree(eng, path, include_cooc=False, agent_state=None):
    data = {
        "round": eng.round, "idc": eng._idc, "head": eng.head, "tail": eng.tail,
        "tiling": list(eng.tiling),
        "atoms": {str(a.id): {
            "value": a.value, "alive": a.alive, "prev": a.prev, "next": a.next,
            "owner": a.owner, "hp": a.hp, "created_round": a.created_round,
            "weight": a.weight, "metadata": a.metadata,
        } for a in eng.atoms.values()},
        "nodes": {str(n.id): {
            "parent": n.parent, "children": list(n.children), "value": n.value,
            "hp": n.hp, "dirty": n.dirty, "placeholder": n.placeholder,
            "version": n.version, "created_round": n.created_round,
            "alive": n.alive, "activation_count": n.activation_count,
        } for n in eng.nodes.values()},
        "ledger": eng.ledger, "archive": eng.archive, "events": eng.events[:500],
        # 轨道B 笔记本（RealEngine；桩引擎无此属性 → getattr 兜底）
        # 含 dismiss 懒标记（kind=schedule status=planted）——加载后追踪可继续
        "notebook": getattr(eng, "notebook", {}),
        "stats": eng.stat(),
        # agent 级状态 sidecar（重启恢复用：轮次/persona 淡出态/风格快照等）
        # 与树同文件保存 → 原子写入，杜绝 sidecar 与树不同步
        "agent_state": agent_state,
    }
    if include_cooc and hasattr(eng, "cooc"):
        c = eng.cooc
        data["cooc"] = {
            "edges": [[a[0], a[1], b[0], b[1], v] for (a, b), v in c.edges.items()],
            "concepts": [{
                "id": x["id"], "title": x["title"], "content": x["content"],
                "embedding": (x["embedding"].tolist() if x["embedding"] is not None else None),
                "members": x["members"], "status": x["status"],
                "trigger_queries": x["trigger_queries"], "created_round": x["created_round"],
            } for x in c.concepts.values()],
            "seq": c._seq,
        }
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def load_tree(eng, path, with_cooc=True):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    eng.atoms = {}
    for k, d in data["atoms"].items():
        a = Atom(id=int(k), value=d["value"], alive=d["alive"], prev=d["prev"],
                 next=d["next"], owner=d["owner"], hp=d["hp"],
                 created_round=d["created_round"], weight=d.get("weight", 1),
                 metadata=d.get("metadata") or {})
        eng.atoms[a.id] = a
    eng.nodes = {}
    for k, d in data["nodes"].items():
        n = Node(id=int(k), parent=d["parent"], children=list(d["children"]),
                 value=d["value"], hp=d["hp"], dirty=d["dirty"],
                 placeholder=d["placeholder"], version=d["version"],
                 created_round=d["created_round"], alive=d["alive"],
                 activation_count=d.get("activation_count", 0))
        eng.nodes[n.id] = n
    eng.round = data["round"]
    eng._idc = data["idc"]
    eng.head, eng.tail = data["head"], data["tail"]
    eng.tiling = list(data["tiling"])
    eng.ledger = data.get("ledger", [])
    eng.archive = data.get("archive", [])
    eng.events = data.get("events", [])
    if hasattr(eng, "notebook"):
        eng.notebook = dict(data.get("notebook") or {})
    if with_cooc and "cooc" in data and hasattr(eng, "cooc"):
        import numpy as np
        c = eng.cooc
        c.edges = {}
        for a_id, a_t, b_id, b_t, v in data["cooc"]["edges"]:
            c.edges[((a_id, a_t), (b_id, b_t))] = v
        c.concepts = {}
        for x in data["cooc"]["concepts"]:
            emb = x.get("embedding")
            x["embedding"] = np.array(emb, dtype=float) if emb is not None else None
            c.concepts[x["id"]] = x
        c._seq = data["cooc"].get("seq", len(c.concepts))
    # agent 级状态（重启恢复）：调用方从 eng._restored_agent_state 读取
    eng._restored_agent_state = data.get("agent_state")
    return eng


def latest_tree_snapshot(save_dir, agent_id):
    """在 save_dir 中挑该 agent 的最新树快照路径（重启恢复用）。

    优先周期性快照 `{agent_id}_tree_r<N>.json.gz`（取最大 N，N 相同取 mtime 新）；
    无周期性快照时回退到任意 `{agent_id}_tree_*.json.gz`（如 `_tree_final`）中
    mtime 最新者。无快照返回 None。
    """
    if not save_dir or not os.path.isdir(save_dir):
        return None
    cands = glob.glob(os.path.join(save_dir, f"{agent_id}_tree_*.json.gz"))

    def _rnum(p):
        m = re.search(r"_tree_r(\d+)\.json\.gz$", os.path.basename(p))
        return int(m.group(1)) if m else -1

    if not cands:
        return None
    periodics = [p for p in cands if _rnum(p) >= 0]
    if periodics:
        return max(periodics, key=lambda p: (_rnum(p), os.path.getmtime(p)))
    return max(cands, key=os.path.getmtime)
