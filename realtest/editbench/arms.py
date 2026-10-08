"""arms.py — 编辑风暴实验四结构臂统一接口。

12 格 = 4 结构 {full, flat, raptor, tree_v2} × {baseline, rebuilt, incremental}。

结构定义（论文单因子：结构 vs 更新策略）：
  full    全文发送（不检索；1M 上下文上界锚）
  flat    固定 400 字 chunk + bge 向量 + top-12（对齐 RAPTOR 叶子/检索深度）
  raptor  复用 chatbench.compare.Raptor（递归摘要 + collapsed top-12）
  tree_v2 RealEngine 水循环树（真实编辑路径）

更新策略：
  rebuild     从新文档无状态重建（build on new_doc）
  incremental 只保留编辑台账：full=旧文+台账；flat/raptor=旧索引+diff 追加；
              tree_v2=台账映射成真实编辑路径（replace/delete/insert）

接口：
  build(doc)           原始文档灌入
  rebuild(new_doc)     新文档重建
  apply_ledger(ledger) 延续更新，返回耗时秒（tree_v2 的编辑时刻成本 = 头条另一半）
  context(q)           给定查询 → 注入文本
  retell_context()     无 query 全局视图（复述用；预注册口径见 docbench）
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

import numpy as np

# tree_io 在 realtest/chatbench/（快照序列化）；独立导入 arms 时也能找到
_CHATBENCH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "chatbench")
if _CHATBENCH not in sys.path:
    sys.path.insert(0, _CHATBENCH)

# 编辑后最小冷却轮数（只解占位；旧值 45+30 会整树压平）。env 可覆盖以便对照。
_COOL_TICKS = int(os.environ.get("EDITBENCH_COOL_TICKS", "4"))

from realengine import RealEngine, engine_kwargs
from splitter import split_sentences


def _split_doc(doc, splitter):
    """文档 → 原子文本列表。sentence=中文句切（默认，历史口径）；line=一行一原子
    （忽略空行，用于代码/表格/英文密布的 md 语料，防超长原子）；md=chattext 的
    markdown 边界切分。"""
    if splitter == "line":
        return [ln.strip() for ln in doc.splitlines() if ln.strip()]
    if splitter == "md":
        try:
            from chattext import split_md
            return [t for t, _ in split_md(doc) if t.strip()]
        except Exception:
            pass
    return split_sentences(doc)


class MeteredLLM:
    """按臂计量的 LLM 包装（token/延迟/调用数）；base 可换 per-arm key。"""

    def __init__(self, base, tag):
        self.base = base
        self.tag = tag
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cache_hit_tokens = 0
        self.cache_miss_tokens = 0
        self.seconds = 0.0

    def chat(self, system, user):
        text, usage, lat = self.base.chat(system, user)
        self.calls += 1
        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0)
        self.cache_hit_tokens += usage.get("cache_hit_tokens", 0)
        self.cache_miss_tokens += usage.get("cache_miss_tokens", 0)
        self.seconds += lat
        return text, usage, lat

    @property
    def model(self):
        return getattr(self.base, "model", "?")

    def stats(self):
        hit, miss = self.cache_hit_tokens, self.cache_miss_tokens
        denom = hit + miss
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "cache_hit_tokens": hit, "cache_miss_tokens": miss,
                "cache_hit_rate": round(hit / denom, 4) if denom else None,
                "seconds": round(self.seconds, 2)}


def _budget_join(items, budget, sep="\n\n"):
    """把检索项按字符预算拼装（budget=0 → 不封顶，保持旧行为）。"""
    if not budget:
        return sep.join(items)
    out, total = [], 0
    for it in items:
        if out and total + len(it) + len(sep) > budget:
            break
        out.append(it)
        total += len(it) + len(sep)
    return sep.join(out)


class Arm:
    name = "arm"

    def build(self, doc):
        raise NotImplementedError

    def rebuild(self, new_doc):
        self.build(new_doc)

    def apply_ledger(self, ledger):
        return 0.0

    def settle(self):
        """推迟到蒸馏时机的后台代价（仅 tree_v2 非零）；默认 0。"""
        return 0.0

    def reset(self):
        """每题开始前清空该题专属的检索累积（仅 tree_v2 有状态）；默认 no-op。"""
        return

    def snapshot(self):
        """返回 Phase 结束点的可恢复快照（供逐遍作答前恢复 → 重复测量独立）。
        无状态臂返回 None。"""
        return None

    def restore(self, snap):
        """从 snapshot() 恢复；无状态臂 no-op。"""
        return

    def context(self, q):
        raise NotImplementedError

    def retell_context(self):
        return self.context("请概括全文的主要人物、地点、物品与事件。")


# ── full ────────────────────────────────────────────────

class FullArm(Arm):
    name = "full"

    def __init__(self, llm):
        self.llm = llm
        self.doc = ""

    def build(self, doc):
        self.doc = doc

    def apply_ledger(self, ledger):
        """edit-phase 输入 → materialize：无增量机制 → append-only，把编辑结果追加进全文
        （旧文保留 → 上下文腐烂）。每次编辑渲染成 **function-calling 式"调用+返回"块**
        （AI 原生格式），不做平铺 dump。"""
        from editstorm import tool_blocks
        t0 = time.time()
        if ledger:
            self.doc = self.doc.rstrip() + "\n\n" + tool_blocks(ledger)
        return time.time() - t0

    def context(self, q):
        return self.doc

    def retell_context(self):
        return self.doc


# ── flat RAG ────────────────────────────────────────────

class FlatArm(Arm):
    name = "flat"
    CHUNK = 400
    TOPK = 12

    def __init__(self, llm, enc, topk=12, budget=0):
        self.llm = llm
        self.enc = enc
        self.TOPK = int(topk)
        self.budget = int(budget or 0)   # 字符预算；0 = 不封顶（旧行为）
        self.doc = ""
        self.chunks = []
        self.embs = None

    def _index(self, doc):
        self.doc = doc
        self.chunks = [doc[i:i + self.CHUNK].strip()
                       for i in range(0, len(doc), self.CHUNK)]
        self.chunks = [c for c in self.chunks if c]
        self.embs = (self.enc.encode(self.chunks, normalize_embeddings=True,
                                     batch_size=32) if self.chunks else None)

    def build(self, doc):
        self._index(doc)

    def apply_ledger(self, ledger):
        """append-only：旧 chunk 保留 + 编辑结果按 **function-calling 块**追加
        （每次工具调用一个可检索 block；不重切、不删旧 → 污染测量）。"""
        from editstorm import tool_block_units
        t0 = time.time()
        new = tool_block_units(ledger)
        if new:
            self.chunks.extend(new)
            embs = self.enc.encode(new, normalize_embeddings=True, batch_size=32)
            self.embs = embs if self.embs is None else np.vstack([self.embs, embs])
        return time.time() - t0

    def _retrieve(self, q, topk):
        if self.embs is None:
            return []
        qv = self.enc.encode(q, normalize_embeddings=True)
        scores = self.embs @ qv
        idx = scores.argsort()[::-1][:topk]
        return [self.chunks[i] for i in idx]

    def context(self, q):
        return _budget_join(self._retrieve(q, self.TOPK), self.budget)

    def retell_context(self):
        return self.context("请概括全文的主要人物、地点、物品与事件。")


# ── RAPTOR ──────────────────────────────────────────────

class RaptorArm(Arm):
    name = "raptor"
    TOPK = 12

    def __init__(self, llm, enc, distill_meter=None, topk=12, budget=0):
        self.llm = llm
        self.enc = enc
        self.distill_meter = distill_meter
        self.TOPK = int(topk)
        self.budget = int(budget or 0)   # 字符预算；0 = 不封顶（旧行为）
        self.rap = None
        self._extra = []

    def build(self, doc):
        from compare import Raptor
        self.rap = Raptor([{"content": doc, "msg_id": "doc"}], self.enc,
                          self.distill_meter or self.llm,
                          cache=None, leaf_chars=400)

    def apply_ledger(self, ledger):
        """冻结旧树 + 编辑结果按 **function-calling 块**作为**新顶层 chunk**挂入
        （每次工具调用一个叶子；不重算任何摘要——最 lazy 可辩护变体）。"""
        from editstorm import tool_block_units
        t0 = time.time()
        new = tool_block_units(ledger)
        if new:
            embs = self.enc.encode(new, normalize_embeddings=True, batch_size=32)
            for text, emb in zip(new, embs):
                self.rap.nodes.append({"level": 0, "text": text, "msgs": ["doc"],
                                       "emb": emb})
        return time.time() - t0

    def context(self, q):
        text = self.rap.retrieve(q, topk=self.TOPK)
        if self.budget and len(text) > self.budget:
            cut = text.rfind("\n", 0, self.budget)
            text = text[:cut if cut > 0 else self.budget]
        return text

    def retell_context(self):
        return self.context("请概括全文的主要人物、地点、物品与事件。")


# ── tree_v2 ─────────────────────────────────────────────

class TreeArm(Arm):
    name = "tree_v2"

    def __init__(self, llm, distill_meter, cfg, budget, retr_budget, leaf_expand=False,
                 edit_signal=True, splitter="sentence"):
        self.llm = llm
        self.distill_meter = distill_meter
        self.cfg = cfg
        self.budget = budget
        self.retr_budget = retr_budget
        self.leaf_expand = leaf_expand
        self.edit_signal = edit_signal   # True=编辑新原子给热 HP（当使用信号）；False=冷 HP（当障碍）
        self.splitter = splitter         # sentence / line / md（见 _split_doc）
        self.eng = None
        self.shadows = []

    def build(self, doc):
        self.shadows = []
        kw = engine_kwargs(self.cfg, self.budget, self.retr_budget)
        self.eng = RealEngine(self.distill_meter, **kw)
        units = [(s, {"source": "doc"}) for s in _split_doc(doc, self.splitter) if s]
        for i, (s, m) in enumerate(units):
            self.eng.insert_units([(s, m)], left_id=self.eng.tail)
            if (i + 1) % 40 == 0:
                self.eng.tick(6)
                sig = self.eng.merge_signal()
                if sig == "force":
                    self.eng.merge_pass(force_out=True)
                elif sig == "regular":
                    self.eng.merge_pass()
        self.eng.tick(6)
        self.eng.merge_pass()

    def apply_ledger(self, ledger):
        """台账映射成**真实编辑路径**（replace_units/delete/insert），测编辑机制本身。
        **只测即时编辑路径**（结构变更 + 置脏，零 LLM）；蒸馏推迟到 settle()。"""
        eng = self.eng
        t0 = time.time()
        _before = set(eng.atoms.keys())
        for e in ledger:
            kind = e["kind"]
            if kind in ("rename", "detail"):
                find, repl = e["old"], e["new"]
                for aid in [aid for aid, a in eng.atoms.items()
                            if a.alive and find in (a.value or "")]:
                    a = eng.atoms[aid]
                    eng.replace_units([aid], [(a.value.replace(find, repl), dict(a.metadata or {}))])
            elif kind in ("rewrite", "passage"):
                old_set = set(e["old_units"])
                pos = eng._position_map()
                ids = [aid for aid, a in eng.atoms.items()
                       if a.alive and (a.value or "") in old_set]
                ids.sort(key=lambda aid: pos.get(aid, 0))
                if ids:
                    eng.replace_units(ids, [(s, {"source": "doc"}) for s in e["new_units"]])
                else:
                    for aid in [aid for aid, a in eng.atoms.items()
                                if a.alive and e["old"] in (a.value or "")]:
                        a = eng.atoms[aid]
                        eng.replace_units([aid], [(a.value.replace(e["old"], e["new"]),
                                                   dict(a.metadata or {}))])
            elif kind == "delete":
                # 只删目标句本身，保留同原子内其余内容（聊天列表行常被 splitter 并进一个原子；
                # 整卡墓碑会连带删掉 pid/procs/可写路径等未指定删除的事实 → 与 new_doc 不一致）
                for unit in e["old_units"]:
                    for aid in [aid for aid, a in eng.atoms.items()
                                if a.alive and unit in (a.value or "")]:
                        a = eng.atoms[aid]
                        residual = (a.value or "").replace(unit, "")
                        if residual.strip():
                            eng.replace_units([aid], [(residual, dict(a.metadata or {}))])
                        else:
                            eng.delete([aid])
            elif kind == "append":
                for s in e["new_units"]:
                    eng.insert_units([(s, {"source": "doc"})], left_id=eng.tail)
        if not self.edit_signal:
            # A/B 对照：编辑新原子给冷 HP（不进注意力窗、照常被合并）→ "修改当障碍"
            for aid in set(eng.atoms.keys()) - _before:
                eng.atoms[aid].hp = 0.0
        return time.time() - t0

    def settle(self):
        """推迟蒸馏（章边界/预算触发）+ **最小冷却**：只解占位、不整树压平。

        旧版 tick(45)+tick(30) 会把整树 HP 打到 0 → merge 把整篇压成 3 个根摘要
        （实测铺陈 50→3、~600 字），细节被逐层蒸馏丢弃 → 增量臂复述覆盖塌陷。
        新原子 initial_hp=70、hot_threshold=60、decay=8 → 约 2 tick 即降到阈值下，
        故冷却只需 `_COOL_TICKS`（默认 4）轮。"""
        eng = self.eng
        t0 = time.time()
        eng.tick(1)
        sig = eng.merge_signal()
        if sig == "force":
            eng.merge_pass(force_out=True)
        elif sig == "regular":
            eng.merge_pass()
        eng.tick(_COOL_TICKS)          # 最小冷却：让编辑热原子降温、占位解除
        eng.merge_pass()
        return time.time() - t0

    def reset(self):
        self.shadows = []

    def load(self, path):
        """从 tree_io gzip 快照加载树（复用：不重建、不编辑、不冷却）。
        新 budget/retr 只影响作答时的组装/检索，不改树状态。"""
        from tree_io import load_tree
        self.eng = RealEngine(self.distill_meter,
                              **engine_kwargs(self.cfg, self.budget, self.retr_budget))
        load_tree(self.eng, path)
        return self

    def snapshot(self):
        """树快照（Phase 结束点）：逐遍作答前 restore → 各遍互不累积 recall HP/activation/promote。
        仅序列化树状态（tree_io），瞬态缓存（向量/热量/蒸馏）重载时清空。"""
        from tree_io import save_tree
        fd, path = tempfile.mkstemp(suffix=".json.gz", prefix="editbench_snap_")
        os.close(fd)
        save_tree(self.eng, path)
        return path

    def restore(self, snap):
        if not snap:
            return
        from tree_io import load_tree
        eng = self.eng
        eng._vec_cache = {}
        eng._heat = {}
        eng._overboost = []
        with eng._distill_cache_lock:
            eng._distill_cache = {}
        load_tree(eng, snap)

    def context(self, q):
        hits = self.eng.retrieve(q, budget=self.retr_budget)
        if self.leaf_expand:
            # 叶子展开（C 机制）：命中摘要/节点 → 下钻其存活后代原子为影子（露出细节）
            items = self.eng.expand_leaves(hits)
        else:
            items = hits
        for x in items:
            if x not in self.shadows:
                self.shadows.append(x)
        # allow_merge=False：问答期纯组装+裁剪，不在评测中途蒸馏（§4.5.7 mid-loop 不 merge）
        # ——保证重复作答独立、成本只由渲染体量决定
        emitted = self.eng.assemble(self.shadows or None, budget=self.budget,
                                    allow_merge=False)
        return self.eng.render_emission(emitted)

    def retell_context(self):
        """无 query 全局视图：铺陈（含全局概述层）。"""
        emitted = self.eng.assemble(self.shadows or None, budget=self.budget,
                                    allow_merge=False)
        return self.eng.render_emission(emitted)


STRUCTURES = ("full", "flat", "raptor", "tree_v2")


def make_arm(name, distill_llm, answer_llm, cfg, budget, retr_budget, enc, doc, tag=None,
             leaf_expand=False, flat_topk=12, raptor_topk=12, build=True, edit_signal=True,
             splitter="sentence"):
    """构造臂。distill_llm=蒸馏/摘要（temp 0），answer_llm=作答（temp 0.5）。
    两路分表计量（各自 MeteredLLM），arm.llm 指向作答侧、arm.distill_meter 指向蒸馏侧。
    build=False 时不灌文档（供从 gzip 快照复用：随后 arm.load(path)）。
    flat/raptor 也接受字符预算 `budget`（0=不封顶，保持旧行为；>0 时把检索结果裁到该预算）。"""
    distill_m = MeteredLLM(distill_llm, f"{tag or name}·distill")
    qa_m = MeteredLLM(answer_llm, tag or name)
    if name == "full":
        arm = FullArm(qa_m)
    elif name == "flat":
        arm = FlatArm(qa_m, enc, topk=flat_topk, budget=budget)
    elif name == "raptor":
        arm = RaptorArm(qa_m, enc, distill_m, topk=raptor_topk, budget=budget)
    elif name == "tree_v2":
        arm = TreeArm(qa_m, distill_m, cfg, budget, retr_budget, leaf_expand=leaf_expand,
                      edit_signal=edit_signal, splitter=splitter)
    else:
        raise ValueError(name)
    arm.distill_meter = distill_m
    if build:
        arm.build(doc)
    return arm
