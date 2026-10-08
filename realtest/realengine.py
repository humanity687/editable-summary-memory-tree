"""realengine.py — 真实文本版引擎：LLM 蒸馏摘要 + 混合检索（向量 + 关键词双池）。"""

import json
import os
import re
import threading
import time
import concurrent.futures as cf
from concurrent.futures import as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError

# 强制离线：必须在 sentence_transformers / transformers 导入前设置，
# 禁止每次加载都去 HF Hub 检查更新（网络问题会挂死）。全局单例 + 离线三连。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from core import (Atom, Engine, ATOM_WEIGHT_CHAR,
                  ATTENTION_ATOM_CAP, ATTENTION_NODE_CAP, ATTENTION_OUT,
                  ATTENTION_IN_DECAY, ATTENTION_OUT_DECAY, MERGE_FANOUT_CAP,
                  ATTENTION_PRESSURE, MERGE_PRESSURE_THRESHOLD)
from textutil import bigrams   # 单一真源（保持 from realengine import bigrams 兼容）

# 蒸馏 prompt 单一模板（中性/染色共用一个规则块，仅 intro/事实规则/【注】分支不同）。
# 2026-10-03 收口：删除"来源标注规则""时序分流规则"——只留中立概括说明。
# 来源由程序维护（_node_sources / 分段渲染），不要求模型保留标签、也不禁止。
SUMMARIZE_PROMPT = (
    "你是记忆蒸馏器。{intro}\n"
    "{fact_rule}"
    "{note_clause}{style}"
    "【内容】\n{content}"
)

SUMMARIZE_INTRO_NEUTRAL = "把以下内容概括成中性摘要卡（只输出一行）："
SUMMARIZE_INTRO_STYLED = "把以下内容概括成一张摘要卡："
# 事实规则：中性 α=0 用 NEUTRAL；染色 α>0 用 STYLED（允许人格锚点影响措辞/重心，
# 严禁新增/改变事件——"只影响怎么说，不影响说什么"）。
SUMMARIZE_FACT_NEUTRAL = (
    "【事实】中性概括：必须保留专有名词（人名/地名/物品名）、数字、因果链；"
    "禁止编造、禁止评价；禁止第一人称视角（不用\"我\"）。\n"
)
SUMMARIZE_FACT_STYLED = (
    "【事实】概括：必须保留专有名词（人名/地名/物品名）、数字、因果链；"
    "可按下方\"人格锚点\"调整**措辞、展开顺序与重心**（先展开你在意的、折叠其余），"
    "但**禁止新增或改变事件、数字、专有名词**——锚点只影响\"怎么说\"，"
    "不影响\"说什么\"；禁止把锚点中的人格主张当作事实塞入摘要。\n"
)
SUMMARIZE_NOTE_CLAUSE = (
    "如需保留一句主观评注（可选，不加也不违规），在【事实】下另起一行写"
    "【注】（40 字内）：用你自己的认知习惯与口吻写，但不引入新事实、不改变事件与专名。\n"
)

SUMMARIZE_CHAR_CAP = 3000

# 早期标记（新语义，2026-10-03）：判据从"HP 低于合并阈值"改为"与失效参照集
# （墓碑原子 + 脏节点旧文本）的 bigram Jaccard ≥ 阈值"——即内容与已被编辑失效的
# 内容高度相似 → 可能无效/历史态。参照集取最近 EARLY_REF_CAP 条控成本。
EARLY_MATCH_THRESHOLD = 0.5
EARLY_REF_CAP = 300

# 锚点展示清洗（2026-10-03）：摘要节点整卡里会带蒸馏源脚手架标注
# 〔ai〕/〔user:x〕/〔tool〕/〔早期〕/〔另有X条…〕——作为人格锚点样本时应剥离，
# 否则稀释人格文本、把机制痕迹喂给模型。
_ANCHOR_PREFIX_RE = re.compile(r"〔[^〕]*〕")


def anchor_clean(text):
    """锚点样本清洗：去掉〔来源/早期/索引〕脚手架标注并压空白。"""
    t = _ANCHOR_PREFIX_RE.sub("", text or "")
    return re.sub(r"\s+", " ", t).strip()


VEC_MODEL_ORDER = ["BAAI/bge-small-zh-v1.5", "sentence-transformers/all-MiniLM-L12-v2",
                   "sentence-transformers/all-MiniLM-L6-v2"]

# 三通道权重（先拍后调）：摘要·向量 / 摘要·关键词 / 原子·粗筛+向量精排
W_VEC_NODE = 0.4
W_BM_NODE = 0.3
W_BM_ATOM = 0.3
ATOM_MIN_LEN = 8
ATOM_RERANK_CAP = 60
ATOM_VEC_MIN = 0.2
# 批量编码（2026-10-03 内存修复）：逐条 enc.encode 会在 MPS 上堆积幽灵内存
# （实测同一批 316 条：逐条 +460MB vs 单次 batch +69MB）。候选合并为一次 encode。
EMBED_BATCH = 32

# 区间全原子回调（[span] 工具）上限（先拍后调）
SPAN_ATOM_CAP = 40      # 单次回调最多原子数
SPAN_CHAR_CAP = 1500    # 单次回调最多字符数

# 叶子展开（命中概括卡 → 展开全部后代原子为影子）上限（先拍后调）
EXPAND_ATOM_CAP = 80    # 单轮展开最多原子数
EXPAND_CHAR_CAP = 3000  # 单轮展开最多字符数

# 硬截断松弛（2026-09-27 拍定）：溢出超过 budget*(1+此比例) 才硬截断
HARD_TRIM_SLACK = 0.1

# heat burst 自动下钻披露（2026-09-27 重设计；**默认关**——论文实验 tree_v2 臂冻结，
# 首秀放 ahi-bench ablation 臂）。信号 = 回血封顶**溢出量**（`min(hp+recall_boost, max_hp)`
# 被截断的部分，"热度超过当前分辨率上限"）：节点热量 ≥ 阈值且预算富余时，把视野里该节点的
# 直接子卡"炸开"——全装得下则替换节点项（子并集=父区间，覆盖不丢），否则保留节点+追加。
# 只动视野、不动树/账本；每轮重组装（冷却自动合回）。热量 tick 衰减、不随爆炸消耗。
AUTO_EXPAND = False             # 拔出接口：默认关（config `auto_expand: true` 打开）
EXPAND_RATIO = 0.85             # 组装预算使用率低于此 → 富余，允许炸开
EXPAND_HEAT_THRESHOLD = 60.0    # 热量阈值（≈2 次封顶命中的溢出；口径随 tick/检索节奏，见 §11）
HEAT_PARENT_FACTOR = 0.5        # 子命中溢出 → 直接父半权热量（同轮自身命中的祖先不重复计）

# 笔记本（轨道B）注入上限（先拍后调）
NB_CONSTRAINT_CHARS = 400   # 常驻约束总字符上限
NB_STATE_CHARS = 600        # 状态条目总字符上限
NB_WORKLOG_CHARS = 400      # 工作记录总字符上限

# self_quota 实验操控（2026-10-04，**默认关**）：组装层"自我可见性地板"——
# 保证 agent 自身原话（`source=="ai"` 原子）占组装预算至少 `self_quota_ratio`。
# 位置序注入（复用影子槽，`kind="self_quota"` 渲染成正常自身发言段），超预算时按
# 挤占序腾位（检索影子/远端摘要先让、热区最后）。**只动视野、纯计算、零 LLM、不写树**。
# 计数只认原始自我原子（自我来源摘要折算不进）；候选 recency-first。
# 这是**实验旋钮**（测"自我模仿→人格保持"的因果），不是 tree 修复。
SELF_QUOTA_RATIO = 0.0             # 0 = 关；目标占比（× budget）
SELF_QUOTA_MAX = 0.5               # 占比上限（防自我垄断）
SELF_QUOTA_MIN_LEN = ATOM_MIN_LEN  # 候选自我原子最小长度


# 蒸馏批量超时（秒）：SDK read_timeout 之上加总时长兜底——httpx read 是字节
# 间隔超时，服务器 trickle（持续心跳字节）可绕过；future 超时 → 丢弃标记重试
DISTILL_BATCH_TIMEOUT = 150.0

# ── 引擎参数装配（唯一真源）────────────────────────────────
# 所有 app / harness 的 RealEngine 构造统一经此函数，杜绝 kwargs 复制漂移
# （同一实验各臂必须逐字节同参；见 AGENTS §9 工程纪律）。
# 场景默认值取 ahi/写作实测配置（decay=8.0 等），与 core 桩默认（5.0）有意分离。
ENGINE_KWARG_KEYS = (
    # (key, cast, default)
    ("decay", float, 8.0),
    ("hp_merge_threshold", float, 30.0),
    ("parent_init_hp", float, 70.0),
    ("parent_hp_group_factor", float, 1.0),
    ("hot_threshold", float, 60.0),
    ("recall_boost", float, 30.0),
    ("max_hp", float, 150.0),
    ("initial_hp", float, 70.0),
    ("merge_cap_atoms", int, 500),
    ("merge_depth_diff", int, 1),
    ("promote_threshold", int, 3),
    ("attention_atom_cap", int, ATTENTION_ATOM_CAP),
    ("attention_node_cap", int, ATTENTION_NODE_CAP),
    ("attention_out", float, ATTENTION_OUT),
    ("attention_in_decay", float, ATTENTION_IN_DECAY),
    ("attention_out_decay", float, ATTENTION_OUT_DECAY),
    ("merge_fanout_cap", int, MERGE_FANOUT_CAP),
    ("pressure_threshold", float, ATTENTION_PRESSURE),
    ("merge_pressure_threshold", int, MERGE_PRESSURE_THRESHOLD),
    ("expand_atom_cap", int, EXPAND_ATOM_CAP),
    ("expand_char_cap", int, EXPAND_CHAR_CAP),
    ("expand_ratio", float, EXPAND_RATIO),
    ("expand_heat_threshold", float, EXPAND_HEAT_THRESHOLD),
    ("heat_parent_factor", float, HEAT_PARENT_FACTOR),
    ("distill_parallel", int, 4),
    ("hard_trim_slack", float, HARD_TRIM_SLACK),
    ("style_alpha", float, 0.0),
    ("early_match_threshold", float, EARLY_MATCH_THRESHOLD),
    ("auto_expand", bool, AUTO_EXPAND),
    ("render_clean", bool, False),
    ("placeholder_transparent", bool, True),
    ("self_quota_ratio", float, SELF_QUOTA_RATIO),
    ("self_quota_max", float, SELF_QUOTA_MAX),
    ("self_quota_min_len", int, SELF_QUOTA_MIN_LEN),
)


def engine_kwargs(cfg, budget=None, retr_budget=None, **overrides):
    """从 config dict 装配 RealEngine 参数（唯一真源）。None 值回落默认；
    overrides 仅覆盖非 None 项（便于 app 注入 render_clean/distill_snap_path 等）。"""
    out = {}
    for key, cast, default in ENGINE_KWARG_KEYS:
        v = cfg.get(key, default)
        out[key] = cast(default if v is None else v)
    if budget is not None:
        out["assembly_budget"] = budget
    if retr_budget is not None:
        out["retrieval_budget"] = retr_budget
    out.update({k: v for k, v in overrides.items() if v is not None})
    return out


class _DaemonPool:
    """极简 daemon 线程池（自实现，2026-08-28）：
    - submit 永不阻塞：内部队列 + 固定 max_workers 个 daemon worker（挂起的 LLM
      调用占槽 → 队列积压，主循环不卡、并发不膨胀）；
    - 返回标准 concurrent.futures.Future（as_completed(timeout) 兼容）；
    - 线程 daemon：挂起 worker 不阻塞进程退出（Python 线程无法强杀）。"""

    def __init__(self, max_workers):
        import queue
        self._q = queue.Queue()
        self._workers = [threading.Thread(target=self._drain, daemon=True,
                                          name=f"distill-w{i}")
                         for i in range(max(1, int(max_workers)))]
        for w in self._workers:
            w.start()

    def _drain(self):
        while True:
            fut, fn, args = self._q.get()
            if fut is None:
                return
            try:
                fut.set_result(fn(*args))
            except BaseException as e:
                fut.set_exception(e)

    def submit(self, fn, *args):
        fut = cf.Future()
        self._q.put((fut, fn, args))
        return fut


def render_notebook(rows, schedule_label="待办"):
    """笔记本注入渲染（唯一实现）：rows = nb_render() 的 (kind, key, content)。
    小说场景 schedule = 伏笔（传 schedule_label="伏笔"）；dismiss 懒标记显示为冲突待决。"""
    if not rows:
        return ""
    parts = []
    for kind, key, content in rows:
        if kind == "constraint":
            parts.append(f"[约束] {content}")
        elif kind == "state":
            parts.append(f"[状态·{key}] {content}")
        elif kind == "schedule":
            tag = "冲突待决" if (key or "").startswith("dismiss_") else schedule_label
            parts.append(f"[{tag}·{key}] {content}")
        elif kind == "worklog":
            parts.append(f"[工作记录·{key}] {content}")
    return "\n".join(parts)


_ENCODER_SINGLETON = None
_ENCODER_INIT = False
_ENCODER_LOCK = threading.Lock()


def _vec_model_candidates():
    """向量模型候选序。环境变量 `WATERCYCLE_EMBED_MODEL` 指定时**只认它**
    （实验换模型探针：失败即报错，不静默退化到 bge-small——否则臂悄悄跑错模型）；
    未指定时用内置默认序（诚实降级）。"""
    env = os.environ.get("WATERCYCLE_EMBED_MODEL", "").strip()
    if env:
        return [env]
    return list(VEC_MODEL_ORDER)


def _load_encoder():
    """全局单例：多 RealEngine 实例（多 agent）共享同一份 SentenceTransformer，
    避免重复加载同一模型（显存/内存浪费、重复权重加载日志）。离线加载（只读本地
    缓存，不检查更新）；失败只尝试一次（_ENCODER_INIT 缓存失败态，避免反复重试）。
    实验指定 WATERCYCLE_EMBED_MODEL 时，加载失败直接抛出（不吞成 None/退化）。"""
    global _ENCODER_SINGLETON, _ENCODER_INIT
    if _ENCODER_INIT:
        return _ENCODER_SINGLETON
    env_model = os.environ.get("WATERCYCLE_EMBED_MODEL", "").strip()
    with _ENCODER_LOCK:
        if _ENCODER_INIT:
            return _ENCODER_SINGLETON
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            if env_model:
                _ENCODER_INIT = True
                raise
            _ENCODER_INIT = True
            return _ENCODER_SINGLETON
        for name in _vec_model_candidates():
            try:
                _ENCODER_SINGLETON = SentenceTransformer(name, local_files_only=True)
                break
            except Exception:
                if env_model:
                    _ENCODER_INIT = True
                    raise RuntimeError(
                        f"WATERCYCLE_EMBED_MODEL={env_model} 加载失败（本地缓存缺失？）"
                    ) from None
                continue
        _ENCODER_INIT = True
        return _ENCODER_SINGLETON


def _empty_mps_cache():
    """释放 MPS 分配器缓存的空闲块（2026-10-03 内存修复）。

    sentence-transformers 的 encode 反复在 MPS 上分配中间张量，PyTorch 的 MPS
    分配器会缓存这些已释放块 → 进程 RSS 跨轮单调上涨（实测 tree agent ~75MB/min）。
    每次批量编码后清一次，压平曲线。CPU 后端 / 无 torch 时 no-op。"""
    try:
        import torch
        mps = getattr(torch, "mps", None)
        if mps is not None and torch.backends.mps.is_available():
            mps.empty_cache()
    except Exception:
        pass


class RealEngine(Engine):
    def __init__(self, llm, **kwargs):
        super().__init__(**kwargs)
        self.llm = llm
        self.distill = {"calls": 0, "prompt_chars": 0, "completion_tokens": 0, "seconds": 0.0}
        self._encoder = kwargs.get("encoder", None)
        self._vec_cache = {}
        self.expand_atom_cap = kwargs.get("expand_atom_cap", EXPAND_ATOM_CAP)
        self.expand_char_cap = kwargs.get("expand_char_cap", EXPAND_CHAR_CAP)
        self.expand_ratio = kwargs.get("expand_ratio", EXPAND_RATIO)
        # 硬截断松弛（2026-09-27 拍定）：仅当溢出超过 budget*(1+slack) 才硬截断（尾砍），
        # 轻微超预算容忍（避免为削几个字符砍掉尾部热区）
        self.hard_trim_slack = kwargs.get("hard_trim_slack", HARD_TRIM_SLACK)
        # 自动下钻披露（heat burst，2026-09-27 重设计；默认关，见 §4.7）
        self.auto_expand = bool(kwargs.get("auto_expand", AUTO_EXPAND))
        self.expand_heat_threshold = float(kwargs.get("expand_heat_threshold",
                                                      EXPAND_HEAT_THRESHOLD))
        self.heat_parent_factor = float(kwargs.get("heat_parent_factor",
                                                   HEAT_PARENT_FACTOR))
        self.heat_decay = float(kwargs.get("heat_decay", self.decay))
        self._heat = {}          # node_id -> 热量（瞬态，tick 衰减；不落盘=重载即失）
        self._overboost = []     # 单次 retrieve 的溢出缓冲 [(item, over)]
        self.overheat_atoms = 0  # 原子封顶溢出计数（不可炸，仅观测）
        # 笔记本（轨道B 最小实现）：kind ∈ constraint / state / schedule
        # entry = {"kind": str, "content": str, "status": str, "updated_round": int}
        self.notebook = {}
        # 预蒸馏（决策 ③）：预测下轮 merge 候选 → 后台先做 LLM 摘要 → merge 命中复用
        # key = 组内卡 id 有序列表 tuple；编辑/墓碑必改卡 id → key 天然失效（无脏复用）
        self._distill_cache = {}
        self._distill_cache_lock = threading.Lock()
        self.distill_cache_hits = 0    # 指标：命中复用次数
        self.distill_cache_misses = 0  # 指标：启动但未用次数（token 浪费）
        # 并行蒸馏（迭代：一轮 merge 的多低 HP 区间并行 LLM 摘要，替代串行）
        self.distill_parallel = kwargs.get("distill_parallel", 4)  # 并发上限
        self.distill_batch_timeout = kwargs.get("distill_batch_timeout",
                                                DISTILL_BATCH_TIMEOUT)
        # 共享 daemon 线程池：并发被 max_workers 限死（挂起 worker 不膨胀、不挡退出）
        self._distill_pool = _DaemonPool(max_workers=max(1, int(self.distill_parallel)))
        # 人格染色（迭代）：style_provider 由 agent 层注入（快照+自我样本），
        # 未注入（neutral）→ 纯中性蒸馏
        self.style_provider = None   # callable -> str | None
        self.style_alpha = kwargs.get("style_alpha", 1.0)   # 染色强度：0/0.5/1（染色 v3）
        # 早期标记（2026-10-03 新语义）：与失效参照集匹配度阈值（见 _is_early）
        self.early_match_threshold = float(kwargs.get("early_match_threshold",
                                                      EARLY_MATCH_THRESHOLD))
        self.fact_drift = 0          # 指标：事实校验失败的蒸馏次数（只留日志不重蒸馏）
        self.first_person_violations = 0  # 指标：人称-来源绑定违规次数（只留日志）
        self.distill_snap_path = kwargs.get("distill_snap_path", None)  # 蒸馏卡快照 JSONL 路径
        self._src_cache = {}         # 渲染用：节点子树来源聚合缓存
        # 渲染模式（2026-09-25 拍定）：clean=True → 原子层渲染成干净对话
        # （段首来源标注，无 #id/·原文/已回复标签），树痕迹只出现在卡片层；
        # clean=False → indexed（保留 #id，bench/chatbench 的引用协议依赖）。
        self.render_clean = bool(kwargs.get("render_clean", False))
        # 展示层来源标签映射（2026-10-03）：source key → 名字/self，供模型直观识别
        # 发言者（如 ai→林深、agent:tree-p2→墨白）。空 = 原样（旧行为，测试不受影响）。
        self.source_labels = {}
        # self_quota 实验操控（2026-10-04，默认关；见常量块）
        self.self_quota_ratio = float(kwargs.get("self_quota_ratio", SELF_QUOTA_RATIO))
        self.self_quota_max = float(kwargs.get("self_quota_max", SELF_QUOTA_MAX))
        self.self_quota_min_len = int(kwargs.get("self_quota_min_len", SELF_QUOTA_MIN_LEN))
        self._last_self_ratio = 0.0   # metrics：最近一次组装的原始自我原子字符占比

    def _disp_src(self, src):
        """展示层来源标签：映射到名字（未配置则原样返回）。"""
        if not src:
            return src
        return self.source_labels.get(src, src)

    # ── heat burst 热量累积（回血封顶溢出；tick 衰减）──────────────

    def _on_overboost(self, item, over):
        """回血封顶溢出：节点入缓冲（稍后聚合为热量）；原子不可炸 → 只计数。"""
        if self._is_atom(item):
            self.overheat_atoms += 1
            return
        self._overboost.append((item, over))

    def _accumulate_heat(self, overboost):
        """聚合一次检索的溢出：自身全额；直接父半权（同轮自身命中的祖先不重复计）。"""
        boosted = {}
        for item, over in overboost:
            boosted[item] = boosted.get(item, 0.0) + over
        for item, over in boosted.items():
            if item not in self.nodes or not self.nodes[item].alive:
                continue
            self._heat[item] = self._heat.get(item, 0.0) + over
            p = self.nodes[item].parent
            if p is not None and p not in boosted and p in self.nodes \
                    and self.nodes[p].alive:
                self._heat[p] = self._heat.get(p, 0.0) + over * self.heat_parent_factor

    def tick(self, n=1):
        super().tick(n)
        d = self.heat_decay * n
        if d > 0:
            for k in list(self._heat):
                self._heat[k] -= d
                if self._heat[k] <= 0:
                    del self._heat[k]

    def retrieve(self, q, budget=None):
        self._overboost = []
        hits = super().retrieve(q, budget=budget)
        if self._overboost:
            self._accumulate_heat(self._overboost)
            self._overboost = []
        return hits

    # ── 并行蒸馏（迭代：多低 HP 区间并行摘要 → 提高单轮蒸馏吞吐）──

    def _merge_runs(self, force_out: bool = False) -> None:
        """覆写 core._merge_runs：合并前把本轮全部候选 chunk **并行**蒸馏
        （上限 distill_parallel 并发），完成后由父类走原有合并路径
        （_weighted_avg 命中缓存 → 跳过现场串行蒸馏）。
        正确性：不同 run/chunk 区间互不重叠（父区间 = 子区间并集不变量），
        摘要彼此独立，并行安全；失败 chunk 标 failed → 合并时现场串行兜底。"""
        runs = self._find_merge_runs(force_out)
        # 收集全部候选 chunk（与父类 _merge_run→_chunk_run 同口径）
        todo = []
        with self._distill_cache_lock:
            for run in runs:
                for chunk in self._chunk_run(run):
                    key = tuple(chunk)
                    cached = self._distill_cache.get(key)
                    if cached is None:
                        parts = self._group_parts(chunk)
                        if not parts:
                            continue
                        self._distill_cache[key] = {"text": None, "ready": False,
                                                  "failed": False}
                        todo.append((key, parts))
        # 分批并行蒸馏（批内并发 ≤ distill_parallel；future 超时 → 丢弃标记，
        # 现场串行兜底——join 无超时是 2026-08-28 主实验 neutral 卡死 8 分钟的根因）
        limit = max(1, int(self.distill_parallel))
        for i in range(0, len(todo), limit):
            batch = todo[i:i + limit]
            futs = {self._distill_pool.submit(self._distill_worker, key, parts):
                    (key, parts) for key, parts in batch}
            done = set()
            try:
                for fut in as_completed(futs, timeout=self.distill_batch_timeout):
                    done.add(fut)
            except FutureTimeoutError:
                pass
            # 超时未完成的 → 标 failed（merge 现场 _weighted_avg 串行蒸馏兜底）；
            # 线程让它自生自灭（daemon + 池槽有限，并发不膨胀）
            if len(done) < len(futs):
                with self._distill_cache_lock:
                    for fut, (key, _p) in futs.items():
                        if fut not in done:
                            cached = self._distill_cache.get(key)
                            if cached is not None and not cached.get("ready"):
                                cached["failed"] = True
        super()._merge_runs(force_out)

    def _invalidated_refs(self):
        """失效参照集（程序维护，零 LLM）：墓碑原子 + 脏节点旧文本——代表"已被
        编辑失效、可能不该再作为当前事实"的内容。返回最近 EARLY_REF_CAP 条的 bigram 集。"""
        vals = []
        for a in self.atoms.values():
            if not a.alive and (a.value or "").strip():
                vals.append(a.value)
        for nd in self.nodes.values():
            if nd.dirty and not nd.placeholder and (nd.value or "").strip():
                vals.append(self._split_fact_note(nd.value)[0])
        return [b for b in (bigrams(v) for v in vals[-EARLY_REF_CAP:]) if b]

    def _is_early(self, text, refs=None):
        """早期（新语义，2026-10-03）：内容与失效参照集高度相似 → 可能无效/历史态。
        判据 = 与参照（墓碑/脏节点旧文本）的 bigram Jaccard ≥ early_match_threshold。
        refs = bigram 集列表（_invalidated_refs 预计算）。"""
        b = bigrams(text or "")
        if not b:
            return False
        refs = self._invalidated_refs() if refs is None else refs
        for rb in refs:
            if len(b & rb) / max(len(b), len(rb)) >= self.early_match_threshold:
                return True
        return False

    def _group_parts(self, run):
        """组内卡 → 蒸馏源文本（干净分段渲染，2026-10-03）：
        - 原子：与主模型同款 `▸〔来源〕` 分段 + 原文（**无逐句内联标签**）；
          该段原子与失效参照集匹配 ≥ 阈值 → 段头追加〔早期〕（可能无效/历史态）。
        - 节点：取摘要【事实】+【注】（option 2：风格向蒸馏器传递）。"""
        parts = []
        refs = self._invalidated_refs()
        seg_lines = []
        seg_key = None

        def flush():
            if seg_lines:
                parts.append("\n".join(seg_lines))

        for c in run:
            if self._is_atom(c):
                a = self.atoms.get(c)
                if a is None or not a.alive:
                    continue
                src = (a.metadata or {}).get("source") or ""
                early = self._is_early(a.value, refs)
                key = (src, early)
                if key != seg_key:
                    flush()
                    seg_key = key
                    hdr = f"▸〔{self._disp_src(src)}〕" if src else "▸"
                    if early:
                        hdr += "〔早期〕"
                    seg_lines = [hdr]
                seg_lines.append(f"  {a.value}")
            else:
                nd = self.nodes.get(c)
                if nd and nd.alive and nd.value is not None and not nd.placeholder:
                    flush()
                    seg_lines = []
                    seg_key = None
                    fact, note = self._split_fact_note(nd.value)
                    parts.append(f"{fact}\n{note}".strip() if note else fact)
        flush()
        return parts

    def _distill_worker(self, key, parts):
        """后台蒸馏：LLM 摘要 → 写缓存（锁）。失败标 failed（merge 时现场蒸馏兜底）。
        key = 组内卡 id 列表，作为 children 传入（蒸馏快照溯源需要）。"""
        try:
            text = self._summarize(parts, children=list(key))
            with self._distill_cache_lock:
                cached = self._distill_cache.get(key)
                if cached is not None:
                    cached["text"] = text
                    cached["ready"] = True
        except Exception:
            with self._distill_cache_lock:
                cached = self._distill_cache.get(key)
                if cached is not None:
                    cached["failed"] = True

    def distill_cache_take(self, children):
        """merge/rebuild 构建摘要时命中缓存 → 取走复用。key = 组内卡 id 列表。"""
        key = tuple(children)
        with self._distill_cache_lock:
            cached = self._distill_cache.pop(key, None)
        if cached and cached.get("ready") and cached.get("text"):
            self.distill_cache_hits += 1
            return cached["text"]
        if cached is not None:
            self.distill_cache_misses += 1
        return None

    # ── 笔记本（轨道B）───────────────────────────────

    def nb_set(self, key, kind, content, status="current"):
        key = key.strip()
        if not key:
            return None
        if kind == "worklog":
            key = "current"   # 固定 key 覆盖 = "当前状态"语义（旧工作记录被去重）
        self.notebook[key] = {
            "key": key, "kind": kind, "content": content.strip(),
            "status": status, "updated_round": self.round,
        }
        self.events.append(("notebook", key, kind))
        return key

    def nb_get(self, key):
        return self.notebook.get(key)

    def nb_render(self, goal=""):
        """组装注入（去重发送，聊天拍定 2026-08-14）：
        - 去重：存储层同 key 覆盖，每个 key 只发最新条目；旧版本不注入；
        - 不滞留：每轮组装时从 notebook 现场独立生成，注入段不进任何持久结构；
        - 位置：调用方把本段拼接在卡片流**后面**（组装顺序微调，见 AGENTS.md §10）；
        - 选取：constraint 恒在（每 key 最新）；state 按 goal 关键词精确选取；
          schedule 仅注入 planted/advancing 活动项；worklog 按 updated_round 排序注入
          （固定 key 覆盖 = "当前状态"语义，旧工作记录自然被去重）。
        返回 [(kind, key, content), ...]。"""
        out = []
        constraints = [e for e in self.notebook.values() if e["kind"] == "constraint"]
        for e in constraints:
            out.append(("constraint", None, e["content"]))
        goal_bigrams = bigrams(goal) if goal else set()
        used = 0
        states = sorted(
            [e for e in self.notebook.values() if e["kind"] == "state"],
            key=lambda e: -e["updated_round"],
        )
        if goal_bigrams:
            matched = []
            for e in states:
                kb = bigrams(e["content"][:60]) | bigrams(e["key"]) if e["key"] else bigrams(e["content"][:60])
                if kb & goal_bigrams:
                    matched.append(e)
            # goal 过滤是次要优化：无命中时回退全量（去重发送 + cap 兜底），不静默清空
            if matched:
                states = matched
        for e in states:
            used += len(e["content"])
            if used > NB_STATE_CHARS:
                break
            out.append(("state", e["key"], e["content"]))
        for e in self.notebook.values():
            if e["kind"] == "schedule" and e.get("status") in ("planted", "advancing"):
                out.append(("schedule", e["key"], e["content"]))
        used = 0
        worklogs = sorted(
            [e for e in self.notebook.values() if e["kind"] == "worklog"],
            key=lambda e: -e["updated_round"],
        )
        for e in worklogs:
            used += len(e["content"])
            if used > NB_WORKLOG_CHARS:
                break
            out.append(("worklog", e["key"], e["content"]))
        return out

    def _new_atom(self, value, weight=None, metadata=None):
        if weight is None:
            weight = 2 if len(value or "") > ATOM_WEIGHT_CHAR else 1
        a = Atom(id=self._nid(), value=value, hp=self.initial_hp, created_round=self.round,
                 weight=weight, metadata=dict(metadata or {}))
        self.atoms[a.id] = a
        return a

    def insert_units(self, units, left_id=None, right_id=None):
        """插入带元数据的原子单元列表（聊天适配：media 切分产物）。
        units = [(text, meta), ...]；meta 写入 Atom.metadata。
        语义与 core.insert 一致：left_id=None 且 right_id=None 时插入链头；
        追加到链尾请显式传 left_id=self.tail。"""
        new = []
        for text, meta in units:
            new.append(self._new_atom(text, metadata=meta))
        return self.insert(left_id, right_id, atoms=new)

    def replace_units(self, old_ids, units):
        """卡级替换（编辑路径）：墓碑旧原子 + 插入带元数据的新卡。
        旧卡定位用锚点（prev/next），区间失效/置脏/stale/commits 全走编辑路径。"""
        alive_old = [aid for aid in old_ids if aid in self.atoms and self.atoms[aid].alive]
        if not alive_old:
            return []
        left = self.atoms[alive_old[0]].prev
        right = self.atoms[alive_old[-1]].next
        self.delete(alive_old)
        new = []
        for text, meta in units:
            new.append(self._new_atom(text, metadata=meta))
        return self.insert(left, right, atoms=new)

    def _atom_text(self, aid):
        """检索/渲染用文本：代码原子优先 docstring（P0-3），其余为原文。"""
        a = self.atoms[aid]
        doc = (a.metadata or {}).get("doc")
        return doc if doc else (a.value or "")

    # ── 向量编码（懒加载，按节点 version 缓存失效）────────

    def _encoder_model(self):
        if self._encoder is None:
            self._encoder = _load_encoder()
        return self._encoder

    def _ensure_node_vecs(self, nids):
        """批量编码节点向量（内存修复 2026-10-03）：cache-miss 节点合并为一次
        `enc.encode` 调用——逐节点编码在 MPS 上堆积幽灵内存（460MB vs 69MB）。"""
        enc = self._encoder_model()
        if enc is None:
            return
        todo = []
        for nid in nids:
            nd = self.nodes.get(nid)
            if nd is None or nd.value is None:
                continue
            c = self._vec_cache.get(nid)
            if c is not None and c[0] == nd.version:
                continue
            todo.append((nid, nd.version, self._fact_part(nd.value)))
        if not todo:
            return
        try:
            embs = enc.encode([t for _, _, t in todo], normalize_embeddings=True,
                              batch_size=EMBED_BATCH)
        except Exception:
            return
        for (nid, ver, _), emb in zip(todo, embs):
            self._vec_cache[nid] = (ver, emb)
        _empty_mps_cache()

    def _node_vec(self, nid):
        nd = self.nodes.get(nid)
        if nd is None or nd.value is None:
            return None
        cached = self._vec_cache.get(nid)
        if cached is not None and cached[0] == nd.version:
            return cached[1]
        self._ensure_node_vecs([nid])
        cached = self._vec_cache.get(nid)
        return cached[1] if cached is not None else None

    def _query_vec(self, q):
        enc = self._encoder_model()
        if enc is None:
            return None
        return enc.encode(q, normalize_embeddings=True)

    # ── 内容生成（LLM 蒸馏）────────────────────────────

    def _weighted_avg(self, children):
        """merge 建父节点/脏重建的摘要入口：先查预蒸馏缓存（决策 ③），
        命中直接用（跳过现场 LLM）；未命中 → 现场蒸馏（降级）。"""
        cached = self.distill_cache_take(children)
        if cached is not None:
            return cached
        # 防复合（染色 v3）：子卡入上层摘要只用【事实】段（_group_parts 统一处理）
        parts = self._group_parts(children)
        if not parts:
            return None
        return self._summarize(parts, children=children)

    def _summarize(self, parts, children=None):
        content = "\n".join(parts)
        if len(content) > SUMMARIZE_CHAR_CAP:
            content = content[:SUMMARIZE_CHAR_CAP] + "……（截断）"
        style = ""
        if self.style_alpha > 0 and self.style_provider is not None:
            # 锚点依赖当前待蒸馏内容做检索（并传 children 供排除自引用）；
            # 旧式 provider 兼容回退：2参 → 1参 → 0参。
            for _args in ((content, children), (content,), ()):
                try:
                    style = (self.style_provider(*_args) or "").strip()
                    break
                except TypeError:
                    continue
                except Exception:
                    style = ""
                    break
        if self.style_alpha > 0:
            prompt = SUMMARIZE_PROMPT.format(
                intro=SUMMARIZE_INTRO_STYLED, fact_rule=SUMMARIZE_FACT_STYLED,
                note_clause=SUMMARIZE_NOTE_CLAUSE,
                style=style + "\n" if style else "", content=content)
        else:
            prompt = SUMMARIZE_PROMPT.format(
                intro=SUMMARIZE_INTRO_NEUTRAL, fact_rule=SUMMARIZE_FACT_NEUTRAL,
                note_clause="", style="", content=content)
        t0 = time.time()
        try:
            text, usage, _lat = self.llm.chat(
                "你是一个可靠的中文记忆蒸馏器。", prompt
            )
        except Exception:
            # 长跑健壮性：单次 API 超时/抖动不得击穿 merge_pass（诚实降级为失败占位）
            text, usage = "", {"completion_tokens": 0}
        self.distill["calls"] += 1
        self.distill["prompt_chars"] += len(prompt)
        self.distill["completion_tokens"] += usage.get("completion_tokens", 0)
        self.distill["seconds"] += time.time() - t0
        text = (text or "").strip()
        # 双字段解析（容错：无标记 → 全当事实，安全方向）+ 【注】程序硬截断
        fact, note = self._parse_summary(text)
        if self.style_alpha <= 0:
            note = ""   # α=0 中性基线：程序强制无【注】（prompt 不听话也拦）
        if not fact:
            fact = "【摘要生成失败】"
        # 事实校验：数字保留率低于阈值 → 只记日志/指标（不重蒸馏）
        self._fact_check(content, fact)
        # 人称-来源强制绑定（染色 v3）：第一人称句缺来源主语 → 只记日志
        self._first_person_check(fact)
        # 差集索引行已删除（2026-10-03）：〔另有N条〕是机制脚手架，不渲染给模型
        # 蒸馏卡快照落盘（观测层 P0：展开比指纹的测量地基）
        self._distill_snapshot(parts, children, fact, note)
        return fact if not note else f"【事实】{fact}\n【注】{note}"

    def _distill_snapshot(self, parts, children, fact, note):
        """每次蒸馏 append 一行 JSONL（含源 parts/摘要/索引/α），供指纹分析。零行为影响。"""
        if not self.distill_snap_path:
            return
        row = {
            "ts": round(time.time(), 2),
            "round": self.round,
            "children": children if children is not None else None,
            "parts": [p[:400] for p in parts],
            "fact": fact[:300],
            "note": note,
            "style_alpha": self.style_alpha,
            "style_on": bool(self.style_provider is not None),
        }
        try:
            with open(self.distill_snap_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _first_person_check(self, fact):
        """人称-来源强制绑定（染色 v3，只记日志不干预）：第一人称句必须含来源主语
        （人名/用户/管理员/agent 名）。违规计数进 events，与 fact_drift 同待遇。"""
        src_toks = ("probe", "0xbf5d36", "BF5D36", "管理员", "用户", "澜", "林深",
                    "neutral", "lan", "lin-shen", "agent", "你")
        n_bad = 0
        for sent in re.split(r"[。；！？\n]", fact or ""):
            if re.search(r"我(们|的|注意到|觉得|看到|记录|听见)", sent):
                if not any(t in sent for t in src_toks):
                    n_bad += 1
        if n_bad:
            self.first_person_violations += n_bad
            self.events.append(("person_source_violation", n_bad))

    def dump_tree(self, path):
        """树快照落盘（观测层）：全树序列化为一行 JSONL。
        原子含原文/HP/owner/weight/source/replied；节点含内容/children/parent/
        span/dirty/placeholder/version/激活计数；外加 tiling 与 round。零行为影响。"""
        def atom_dict(a):
            d = {"id": a.id, "text": (a.value or "")[:500], "hp": round(a.hp, 1),
                 "alive": a.alive, "owner": a.owner, "weight": a.weight,
                 "created_round": a.created_round}
            md = a.metadata or {}
            if md.get("source"):
                d["source"] = md["source"]
            if md.get("replied"):
                d["replied"] = True
            if md.get("pending_dismiss"):
                d["pending_dismiss"] = True
            return d

        def node_dict(n):
            return {"id": n.id, "parent": n.parent, "children": list(n.children),
                    "content": (n.value or "")[:600], "hp": round(n.hp, 1),
                    "dirty": n.dirty, "placeholder": n.placeholder,
                    "version": n.version, "created_round": n.created_round,
                    "activation_count": n.activation_count,
                    "span": [self._first_atom(n.id), self._last_atom(n.id)]}

        snap = {
            "ts": round(time.time(), 2),
            "round": self.round,
            "atoms": {aid: atom_dict(a) for aid, a in self.atoms.items()},
            "nodes": {nid: node_dict(n) for nid, n in self.nodes.items() if n.alive},
            "tiling": list(self.tiling),
            "stat": self.stat(),
        }
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snap, ensure_ascii=False) + "\n")
        except Exception:
            pass

    @staticmethod
    def _split_fact_note(text):
        """双字段共享解析（不截断）：返回 (fact, note)。无标记 → 全文本为 fact。"""
        m = re.match(r"\s*【事实】\s*(.*?)\s*(?:【注】\s*(.*))?\s*$", text or "", re.S)
        if m:
            return m.group(1).strip(), (m.group(2) or "").strip()
        return (text or "").strip(), ""

    @staticmethod
    def _parse_summary(text):
        """双字段解析 + 程序硬截断（蒸馏落库用）。"""
        fact, note = RealEngine._split_fact_note(text)
        return fact[:200], note[:80]

    def _fact_check(self, source_text, fact_text):
        """事实校验（只留日志）：源内容数字保留率 < 60% → 记 fact_drift。
        效率约束：纯正则零 LLM，不重蒸馏。"""
        nums = set(re.findall(r"\d+(?:[\.\-/年]\d+)*", source_text or ""))
        if not nums:
            return
        keep = sum(1 for n in nums if n in (fact_text or ""))
        ratio = keep / len(nums)
        if ratio < 0.6:
            self.fact_drift += 1
            self.events.append(("fact_drift", f"数字保留率 {ratio:.0%}（{keep}/{len(nums)}）"))

    def _fact_part(self, text):
        """检索用：只取【事实】段（染色【注】与〔另有X条〕索引行不参与打分，
        防风格词与索引噪声干扰检索）。共享 _split_fact_note 解析，不截断。"""
        fact, _ = self._split_fact_note(text)
        return re.sub(r"〔另有[^〕]*〕", "", fact).strip()

    def _node_sources(self, nid):
        """节点子树来源聚合（程序维护，不依赖蒸馏）：渲染摘要卡时附〔源〕标注。
        缓存一轮组装（assemble 开头清空，重建后自动失效）。"""
        cached = self._src_cache.get(nid)
        if cached is not None:
            return cached
        srcs = set()
        stack = [nid]
        while stack:
            c = stack.pop()
            if self._is_atom(c):
                if self.atoms[c].alive:
                    srcs.add((self.atoms[c].metadata or {}).get("source") or "?")
            else:
                nd = self.nodes.get(c)
                if nd and nd.alive:
                    stack.extend(nd.children)
        self._src_cache[nid] = srcs
        return srcs

    # ── 混合检索池（三通道：摘要·向量 / 摘要·关键词 / 原子·粗筛+向量精排）────

    def _bm_ratio(self, text, q):
        qb = bigrams(q)
        if not qb:
            return 0.0
        tb = bigrams(text)
        if not tb:
            return 0.0
        return len(tb & qb) / len(qb)

    def _ensure_atom_vecs(self, aids):
        """批量编码原子向量（内存修复 2026-10-03，同 _ensure_node_vecs）。"""
        enc = self._encoder_model()
        if enc is None:
            return
        todo = [(aid, self._atom_text(aid)) for aid in aids if aid not in self._vec_cache]
        if not todo:
            return
        try:
            embs = enc.encode([t for _, t in todo], normalize_embeddings=True,
                              batch_size=EMBED_BATCH)
        except Exception:
            return
        for (aid, _), emb in zip(todo, embs):
            self._vec_cache[aid] = emb
        _empty_mps_cache()

    def _atom_vec(self, aid):
        cached = self._vec_cache.get(aid)
        if cached is not None:
            return cached
        self._ensure_atom_vecs([aid])
        return self._vec_cache.get(aid)

    def _retrieval_pool(self, q):
        pool = {}
        qv = self._query_vec(q)
        qb = bigrams(q)
        elig = [nid for nid, nd in self.nodes.items()
                if nd.alive and not nd.dirty and not nd.placeholder and nd.value is not None]
        if qv is not None:
            self._ensure_node_vecs(elig)          # 批量编码，防 MPS 幽灵内存
        for nid in elig:
            nd = self.nodes[nid]
            bm = self._bm_ratio(self._fact_part(nd.value), q)
            vec = 0.0
            if qv is not None:
                c = self._vec_cache.get(nid)
                if c is not None:
                    vec = float(c[1] @ qv)
            pool[nid] = W_VEC_NODE * vec + W_BM_NODE * bm
        if qb:
            # 原子通道：代码原子只用 docstring 打分（P0-3）；文本不可变 → 向量缓存永久有效
            cands = [aid for aid, a in self.atoms.items()
                     if a.alive and len(self._atom_text(aid)) >= ATOM_MIN_LEN
                     and bigrams(self._atom_text(aid)) & qb]
            if qv is not None:
                window = cands[:ATOM_RERANK_CAP]
                self._ensure_atom_vecs(window)    # 批量编码
                for aid in window:
                    av = self._vec_cache.get(aid)
                    if av is None:
                        continue
                    vec = float(av @ qv)
                    if vec >= ATOM_VEC_MIN:
                        pool[aid] = W_BM_ATOM * self._bm_ratio(self._atom_text(aid), q) \
                                    + W_VEC_NODE * vec
            else:
                for aid in cands:
                    pool[aid] = W_BM_ATOM * self._bm_ratio(self._atom_text(aid), q)
        return pool

    def retrieve_style_samples(self, q, top_k=3, exclude_ids=None,
                               include_atoms=True, include_nodes=True):
        """人格锚点检索（2026-10-03 拍定）：从 agent **自身材料**里按语义/词面混合
        相似度取 top_k，替代旧的"最近 6 句"recency 池。零 LLM。
        - 池①自身原话原子：存活且 metadata['source']=='ai'（长度 ≥ ATOM_MIN_LEN）
        - 池②摘要节点：存活/干净/非占位/value 非空，贡献**整卡**（含【注】）
        - 匹配：bigram 粗筛 → embedding 精排（节点按【事实】语义，A 决策接受）；
          无编码器时退化为关键词单通道（诚实降级）。
        - exclude_ids：当前正在蒸馏的卡（含其子树）→ 防自蒸馏自引用。
        返回 [{type, id, text, score}]，按分降序。"""
        q = (q or "").strip()
        if not q or top_k <= 0:
            return []
        ex_atoms, ex_nodes = set(), set()
        for it in (exclude_ids or ()):
            if self._is_atom(it):
                ex_atoms.add(it)
            elif it in self.nodes:
                ex_nodes.add(it)
                stack = list(self.nodes[it].children)
                while stack:
                    c = stack.pop()
                    if self._is_atom(c):
                        ex_atoms.add(c)
                    else:
                        ex_nodes.add(c)
                        nd = self.nodes.get(c)
                        if nd is not None:
                            stack.extend(nd.children)
        qv = self._query_vec(q)
        qb = bigrams(q)
        scored = []
        if include_atoms:
            cands = []
            for aid, a in self.atoms.items():
                if not a.alive or aid in ex_atoms:
                    continue
                if (a.metadata or {}).get("source") != "ai":
                    continue
                txt = self._atom_text(aid)
                if len(txt) < ATOM_MIN_LEN:
                    continue
                bm = self._bm_ratio(txt, q)
                if qb and not (bigrams(txt) & qb):
                    continue
                cands.append((aid, txt, bm))
            if qv is not None:
                cands.sort(key=lambda x: -x[2])
                window = cands[:ATOM_RERANK_CAP]
                self._ensure_atom_vecs([aid for aid, _, _ in window])   # 批量编码
                for aid, txt, bm in window:
                    av = self._vec_cache.get(aid)
                    if av is None:
                        continue
                    sc = W_VEC_NODE * float(av @ qv) + W_BM_ATOM * bm
                    if sc > 0:
                        scored.append((sc, "atom", aid, txt))
            else:
                for aid, txt, bm in cands:
                    if bm > 0:
                        scored.append((bm, "atom", aid, txt))
        if include_nodes:
            elig = [nid for nid, nd in self.nodes.items()
                    if nd.alive and not nd.dirty and not nd.placeholder
                    and nd.value is not None and nid not in ex_nodes]
            if qv is not None:
                self._ensure_node_vecs(elig)      # 批量编码，防 MPS 幽灵内存
            for nid in elig:
                nd = self.nodes[nid]
                bm = self._bm_ratio(self._fact_part(nd.value), q)
                vec = 0.0
                if qv is not None:
                    c = self._vec_cache.get(nid)
                    if c is not None:
                        vec = float(c[1] @ qv)
                sc = (W_VEC_NODE * vec + W_BM_NODE * bm) if qv is not None else bm
                if sc > 0:
                    scored.append((sc, "node", nid, nd.value))
        scored.sort(key=lambda x: -x[0])
        return [{"type": t, "id": i, "text": anchor_clean(txt), "score": round(sc, 4)}
                for sc, t, i, txt in scored[:top_k]]

    def expand_self_atoms(self, node_id, cap=5):
        """节点 → 其区间内 'source==ai' 的存活后代原子原文（B 决策，控成本取 cap 条）。
        用于锚点展示逐字自我原话，而非只有整卡概括。"""
        out = []
        for aid in (self.node_atoms(node_id) or ()):
            a = self.atoms.get(aid)
            if a is None or not a.alive:
                continue
            if (a.metadata or {}).get("source") != "ai":
                continue
            txt = self._atom_text(aid)
            if not (txt or "").strip():
                continue
            out.append(anchor_clean(txt))
            if len(out) >= cap:
                break
        return out

    def _verbatim_match(self, a, q):
        q = (q or "").strip()
        return len(q) >= 2 and q in a.value

    # ── 重复输入检测（聊天适配 P2-8）──────────────────

    def find_similar(self, text, threshold=0.8, topk=3, exclude_ids=None):
        """新文本与**存活卡**的 bigram 重叠率（Jaccard，分母取 max）≥ 阈值 → 命中。
        命中卡并入本轮 shadows（活一轮影子），让 AI 知道"之前也听过/写过"。
        只搜存活卡：墓碑 = 已被编辑替换，AI 编辑前刚见过，塞回墓碑违背墓碑语义。
        exclude_ids：排除本次刚写入的新卡（防自己匹配自己）。纯计算（零 LLM）。"""
        qb = bigrams(text)
        if not qb:
            return []
        ex = set(exclude_ids or ())
        scored = []
        for aid, a in self.atoms.items():
            if not a.alive or aid in ex:
                continue
            tb = bigrams(self._atom_text(aid))
            if not tb:
                continue
            j = len(tb & qb) / max(len(tb), len(qb))
            if j >= threshold:
                scored.append((j, aid))
        scored.sort(key=lambda x: -x[0])
        return [aid for _, aid in scored[:topk]]

    # ── DISMISS（聊天适配：事实冲突的延迟裁决）────────────────

    def dismiss_pending(self):
        """全部挂起中的懒标记（notebook kind=schedule 且 status=planted）。"""
        return [e for e in self.notebook.values()
                if e["kind"] == "schedule" and e.get("status") == "planted"]

    def dismiss_plant(self, text, budget=None, exclude_ids=None):
        """挂懒标记（零破坏）：立即检索冲突描述 → 命中项展开为后代原子并标
        pending_dismiss 元数据；懒标记存轨道B（schedule 类，planted），每轮由调用方
        持续预检索追踪。返回 (key, 待失效原子列表，供注入影子)。
        排除集 = exclude_ids ∪ 本轮及之后写入的新卡（防新信息原子命中自己）。"""
        text = (text or "").strip()
        if not text:
            return None, []
        seq = sum(1 for e in self.notebook.values() if e["kind"] == "schedule")
        key = f"dismiss_{seq + 1}"
        self.nb_set(key, "schedule", text, status="planted")
        hits = self.retrieve(text, budget=budget if budget is not None else self.retrieval_budget)
        atoms = self.expand_leaves(hits)
        ex = set(exclude_ids or ())
        ex |= {aid for aid in self.atoms if self.atoms[aid].created_round >= self.round}
        atoms = [aid for aid in atoms if aid not in ex]
        for aid in atoms:
            self.atoms[aid].metadata["pending_dismiss"] = True
        return key, atoms

    def dismiss_track(self, budget=None):
        """每轮持续追踪：对全部 planted 懒标记预检索，命中项展开为后代原子并标
        pending_dismiss（注入影子）。**只标挂起之前已存在的旧卡**（created_round <
        挂起轮次）——挂起后的新信息/讨论不标，防标注扩散误导裁决。纯计算。"""
        out = []
        for e in self.dismiss_pending():
            hits = self.retrieve(e["content"],
                                 budget=budget if budget is not None else self.retrieval_budget)
            atoms = self.expand_leaves(hits)
            planted_round = e.get("updated_round", 0)
            for aid in atoms:
                if self.atoms[aid].created_round < planted_round:
                    self.atoms[aid].metadata["pending_dismiss"] = True
                    out.append(aid)
        return out

    def dismiss_confirm(self, atom_ids=None):
        """裁决成立：墓碑指定原子（编辑路径，可回滚），懒标记全部 payback 结案，
        清除残留的 pending_dismiss 标注（存活卡的标注随结案失效）。
        atom_ids=None（2026-09-25 拍定）：确认**当前全部待失效卡**——干净渲染下
        原子层不再显示 #id，寻址收回程序层（模型只需 @dismiss confirm）。"""
        if atom_ids is None:
            atom_ids = [aid for aid, a in self.atoms.items()
                        if a.alive and (a.metadata or {}).get("pending_dismiss")]
        dead = [aid for aid in atom_ids if aid in self.atoms and self.atoms[aid].alive]
        if not dead:
            return []
        result = self.delete(dead)
        for e in self.dismiss_pending():
            e["status"] = "payback"
        for a in self.atoms.values():
            a.metadata.pop("pending_dismiss", None)
        return result

    def dismiss_cancel(self):
        """裁决不成立：关闭懒标记 + 清除 pending_dismiss 标注。"""
        for e in self.dismiss_pending():
            e["status"] = "closed"
        for a in self.atoms.values():
            a.metadata.pop("pending_dismiss", None)

    # ── 区间全原子回调（[span] 工具）───────────────────

    def _cap_atoms(self, atoms, cap_atoms, cap_chars):
        """区间原子列表按数量/字符双上限截断（span 与 expand 共用的唯一截断实现）。"""
        out, chars = [], 0
        for aid in atoms:
            if len(out) >= cap_atoms or chars >= cap_chars:
                break
            out.append(aid)
            chars += len(self.atoms[aid].value or "")
        return out

    def span_atoms(self, nid):
        """回调摘要卡 nid 覆盖区间内的全部存活原子（按位置序、原文全分辨率），
        受 SPAN_ATOM_CAP/SPAN_CHAR_CAP 限制。节点不存在/已死 → None；否则列表（可空）。
        唯一遍历入口 = core.Engine.node_atoms。"""
        atoms = self.node_atoms(nid)
        if atoms is None:
            return None
        return self._cap_atoms(atoms, SPAN_ATOM_CAP, SPAN_CHAR_CAP)

    # ── 叶子展开（C：命中概括卡 → 展开全部后代原子为影子）──────────

    def expand_leaves(self, items, budget=None):
        """把检索命中的节点展开为其**全部后代存活原子**（按位置序、原文全分辨率），
        原子命中保持原样。用于影子注入：概括卡被激活 → 其下原子段整体可见（活一轮）。
        受 EXPAND_ATOM_CAP/EXPAND_CHAR_CAP 限制（先拍后调）。"""
        cap_atoms = self.expand_atom_cap
        cap_chars = self.expand_char_cap
        if budget is not None:
            cap_chars = min(cap_chars, budget)
        out, seen = [], set()
        chars = 0
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            if self._is_atom(item):
                if self.atoms[item].alive:
                    out.append(item)
                    chars += len(self.atoms[item].value or "")
                continue
            if not (self.nodes[item].alive and not self.nodes[item].dirty):
                continue
            for aid in self.node_atoms(item):
                if aid in seen:
                    continue
                seen.add(aid)
                if self.atoms[aid].alive:
                    out.append(aid)
                    chars += len(self.atoms[aid].value or "")
                    if len(out) >= cap_atoms or chars >= cap_chars:
                        return out
        return out

    # ── 组装（按字符预算，真实文本；budget = 渲染字符数）────────

    def assemble(self, shadows=None, budget=None, allow_merge=True) -> list:
        """allow_merge=False（2026-09-25 拍定）：loop 内"组装 per 工具调用"用——
        超预算只 trim 不蒸馏，防 mid-loop 蒸馏风暴；merge 留在轮末统一走
        attention_pressure/merge_pressure 条件。"""
        self._src_cache = {}   # 来源聚合缓存每次组装重算（节点重建后自动失效）
        if budget is None:
            budget = self.assembly_budget
        emitted = self._emit()
        if shadows:
            emitted = self._inject_shadows(emitted, shadows)
        # 自动下钻披露（预算富余时，披露视野内"新鲜/本轮被命中"节点的直接子卡）
        emitted = self._auto_expand(emitted, budget)
        if self._emission_chars(emitted) > budget and allow_merge:
            self.merge_pass()
            self._src_cache = {}   # 合并/重建改变子树来源 → 来源聚合缓存失效
            emitted = self._emit()
            if shadows:
                emitted = self._inject_shadows(emitted, shadows)
            emitted = self._auto_expand(emitted, budget)
        if self._emission_chars(emitted) > budget:
            emitted = self._trim_chars(emitted, budget)
        # self_quota（默认关）：trim 后补足自我可见性地板（超预算时内部挤占腾位）
        emitted = self._apply_self_quota(emitted, budget)
        return emitted

    def _auto_expand(self, emitted, budget) -> list:
        """heat burst 自动下钻披露（2026-09-27 重设计，默认关）：视野里的节点若
        **热量 ≥ 阈值**（热量 = 回血封顶溢出累积；节点自身全额 + 直接父半权；
        tick 衰减、不随爆炸消耗）且组装预算富余（使用率 < expand_ratio），把其
        **直接子卡**（节点/原子）"炸开"——**全部子卡装得下 → 替换节点项**（子并集=父
        区间，覆盖不丢信息）；否则保留节点 + 追加装得下的子卡（追加走预算检查）。
        脏/墓碑子卡不入（防旧内容以高分辨率复活，且阻断替换以免覆盖缺口）。
        只动视野、一层/轮、每轮重算（冷却自动合回）。纯计算零 LLM。"""
        if not self.auto_expand or not emitted or not self._heat:
            return emitted
        cur = self._emission_chars(emitted)
        if cur >= budget * self.expand_ratio:
            return emitted
        allow = budget - cur
        have = {e[1] for e in emitted}
        out = []
        for kind, item, _v in emitted:
            if not (self._is_node(item) and item in self.nodes):
                out.append((kind, item, _v))
                continue
            nd = self.nodes[item]
            if not nd.alive or self._heat.get(item, 0.0) < self.expand_heat_threshold:
                out.append((kind, item, _v))
                continue
            kids, blocked = [], 0
            for ch in nd.children:
                if ch in have:
                    continue                      # 已在视野 → 覆盖仍成立（含 expand_leaves 先到先得）
                if self._is_atom(ch):
                    if self.atoms[ch].alive:
                        kids.append(("expand", ch, self.atoms[ch].value))
                    else:
                        blocked += 1              # 墓碑原子
                elif ch in self.nodes and self.nodes[ch].alive and not self.nodes[ch].dirty:
                    kids.append(("expand", ch, self.nodes[ch].value))
                else:
                    blocked += 1                  # 脏/死节点：不披露
            if not kids:
                out.append((kind, item, _v))
                continue
            lens = [len(self.render_emission([e])) + 1 for e in kids]
            if blocked == 0 and sum(lens) <= allow:
                # 替换：子并集=父区间 → 不丢信息（分辨率 +1）
                for e in kids:
                    have.add(e[1])
                allow -= sum(lens)
                out.extend(kids)
                self.events.append(("heat_burst", item, f"replace:{len(kids)}"))
            else:
                # 追加部分（保留父卡作为覆盖锚；走预算检查）
                out.append((kind, item, _v))
                added = 0
                for e, ln in zip(kids, lens):
                    if ln > allow:
                        continue
                    allow -= ln
                    have.add(e[1])
                    out.append(e)
                    added += 1
                if added:
                    self.events.append(("heat_burst", item, f"append:{added}"))
        return out

    def _emission_chars(self, emitted) -> int:
        return len(self.render_emission(emitted))

    def _trim_chars(self, emitted, budget) -> list:
        """字符预算裁剪（2026-09-27 拍定）：
        ① 先裁低 HP 节点/影子（同池）；② 仍超 → 裁**旧原子**（低 HP 优先；本轮新建原子
        保护一轮——热区语义由"新近性"而非"卡种类"保证）；③ 仍超 → 硬截断记日志。
        裁剪只作用于组装输出（视野），不动树状态（旧原子仍可被检索/合并）。"""
        lens = [len(self.render_emission([e])) + 1 for e in emitted]
        total = sum(lens)
        # 阶段一：node/shadow/expand 低 HP 先裁
        idxs = [i for i, e in enumerate(emitted) if e[0] in ("node", "shadow", "expand")]
        idxs.sort(key=lambda i: self._item_hp(emitted[i][1]))
        drop = set()
        for i in idxs:
            if total <= budget:
                break
            drop.add(i)
            total -= lens[i]
        out = [e for i, e in enumerate(emitted) if i not in drop]
        # 阶段二：旧原子按 HP 裁（新原子活一轮）
        if total > budget:
            lens2 = [len(self.render_emission([e])) + 1 for e in out]
            aidxs = [i for i, e in enumerate(out)
                     if e[0] == "atom" and self.atoms[e[1]].created_round < self.round]
            aidxs.sort(key=lambda i: self._item_hp(out[i][1]))
            atom_drop = set()
            for i in aidxs:
                if total <= budget:
                    break
                atom_drop.add(i)
                total -= lens2[i]
            if atom_drop:
                out = [e for i, e in enumerate(out) if i not in atom_drop]
                self.events.append(("trim_atoms", len(atom_drop)))
        # 阶段三：硬截断（头保留、尾砍）——仅当溢出超过 budget*(1+slack) 才触发；
        # 轻微超预算容忍（热区优先于预算的字符级精确）
        if total > budget * (1 + self.hard_trim_slack):
            kept, t = [], 0
            for e in out:
                ln = len(self.render_emission([e])) + 1
                if kept and t + ln > budget:
                    break
                kept.append(e)
                t += ln
            if len(kept) < len(out):
                self.events.append(("hard_trim", len(out) - len(kept)))
            out = kept
        return out

    # ── self_quota 实验操控（组装层，默认关）──────────────

    def _is_self_atom(self, item) -> bool:
        """自身原话原子：存活且 metadata.source == "ai"（不含 persona/tool/他人）。"""
        return (self._is_atom(item) and item in self.atoms
                and self.atoms[item].alive
                and (self.atoms[item].metadata or {}).get("source") == "ai")

    def _self_rendered_chars(self, emitted) -> int:
        """自身原话在当前**渲染流**中实际占用的字符数（按段聚合，与 render_emission
        的 clean 段折叠口径一致）。2026-10-04 修：旧实现逐条 `render_emission([e])`
        求和，每条都带一个段头，而分母（整段渲染）只保留一个共享段头 → 占比可 >1
        （实测 1.014），导致 H-SQ1 判据失真。此处把连续同（来源,轮次）自身卡作为
        一个段渲染，得到与分母同口径的字符数。"""
        total, i, n = 0, 0, len(emitted)
        while i < n:
            kind, item, _ = emitted[i]
            if kind in ("atom", "self_quota") and self._is_self_atom(item):
                a = self.atoms[item]
                src = (a.metadata or {}).get("source") or ""
                rnd = a.created_round
                run = []
                while i < n:
                    k2, it2, _v = emitted[i]
                    if (k2 in ("atom", "self_quota") and self._is_self_atom(it2)
                            and ((self.atoms[it2].metadata or {}).get("source") or "") == src
                            and self.atoms[it2].created_round == rnd):
                        run.append(emitted[i])
                        i += 1
                    else:
                        break
                total += len(self.render_emission(run))
            else:
                i += 1
        return total

    def _apply_self_quota(self, emitted, budget) -> list:
        """保证自身原话（source=="ai" 原子）在视野里占 budget 的 `self_quota_ratio`
        （cap `self_quota_max`）。位置序注入（复用影子槽）；超预算时按挤占序腾位
        （检索影子/expand 低 HP 先让 → 远端低 HP 节点 → 非自我旧原子；热区与本轮
        新建原子 PROTECTED）。只动视野、纯计算、零 LLM、不写树。ratio<=0 → 原样。"""
        self._last_self_ratio = 0.0
        ratio = self.self_quota_ratio
        if not emitted:
            return emitted

        present = {e[1] for e in emitted}
        covered = self._self_rendered_chars(emitted)
        total = self._emission_chars(emitted)
        # 始终记录真实占比（口径统一）——基线臂（ratio=0）也需要该指标
        self._last_self_ratio = (covered / total) if total else 0.0
        if ratio <= 0:
            return emitted
        target = min(ratio, self.self_quota_max) * budget

        # 候选：存活自身原话原子、不在视野、长度达标；recency-first（created_round 降序）
        cands = [a for a in self.atoms.values()
                 if a.alive and (a.metadata or {}).get("source") == "ai"
                 and a.id not in present and len(a.value or "") >= self.self_quota_min_len]
        cands.sort(key=lambda a: (a.created_round, a.id), reverse=True)

        out = list(emitted)
        add_ids = []
        for a in cands:
            covered = self._self_rendered_chars(out)   # 逐次按段口径重算（精确）
            if covered >= target:
                break
            total = self._emission_chars(out)
            ln = len(self.render_emission([("self_quota", a.id, a.value)])) + 1
            if total + ln > budget:
                total -= self._evict_for_quota(out, need=(total + ln - budget))
            if total + ln > budget:
                break
            out = self._inject_self_quota(out, [a.id])
            add_ids.append(a.id)
        if add_ids:
            self.events.append(("self_quota", len(add_ids), round(ratio, 3)))
        final_total = self._emission_chars(out)
        covered = self._self_rendered_chars(out)
        self._last_self_ratio = (covered / final_total) if final_total else 0.0
        return out

    def _evict_for_quota(self, out, need) -> int:
        """为 self_quota 腾位（原地改 out）：按挤占序裁最低优先级项——
        ① 检索影子/expand（低 HP 先）② 远端低 HP 节点 ③ 非自我、非本轮的旧原子。
        自身原话、self_quota、本轮新建原子（热区）不动。返回释放字符数。"""
        if need <= 0:
            return 0

        def rlen(e):
            return len(self.render_emission([e])) + 1

        def prio(e):
            return 0 if e[0] in ("shadow", "expand") else (1 if e[0] == "node" else 2)

        cand = [i for i, e in enumerate(out)
                if e[0] in ("shadow", "expand", "node")
                or (e[0] == "atom" and not self._is_self_atom(e[1])
                    and self.atoms[e[1]].created_round < self.round)]
        cand.sort(key=lambda i: (prio(out[i]), self._item_hp(out[i][1])))
        drop, freed = set(), 0
        for i in cand:
            if freed >= need:
                break
            drop.add(i)
            freed += rlen(out[i])
        if drop:
            out[:] = [e for i, e in enumerate(out) if i not in drop]
        return freed

    def _inject_self_quota(self, emitted, ids) -> list:
        """把自身原话原子按**位置槽**插回组装流（kind="self_quota"，位置序，
        复用 core `_shadow_slot`）；槽不可定位时退回末尾（罕见，仅当 owner 被裁）。"""
        emitted_index = {e[1]: i for i, e in enumerate(emitted)}
        slots = []
        for item in ids:
            if item in emitted_index or not self._is_atom(item):
                continue
            idx = self._shadow_slot(emitted_index, item)
            if idx is None:
                idx = len(emitted)
            slots.append((idx, item))
        if not slots:
            return emitted
        slots.sort(key=lambda t: t[0])
        grouped = []
        for idx, item in slots:
            if grouped and grouped[-1][0] == idx:
                grouped[-1][1].append(item)
            else:
                grouped.append([idx, [item]])
        out = list(emitted)
        for idx, group in sorted(grouped, key=lambda g: -g[0]):
            group.sort(reverse=True)
            for item in group:
                out.insert(idx + 1, ("self_quota", item, self.atoms[item].value))
        return out

    # ── 渲染（给 LLM 看的记忆流，含从属标注）────────────

    def render_emission(self, emitted, fold_files=False, unfold_ids=None, clean=None):
        """fold_files=True（代码场景拍定 2026-08-30）：文件原子折叠为"首行索引"
        （AST 骨架语义：只显示 class/def 签名行，正文必须 read 获取——View B 唯一
        内容真源，消除"槽位第二抄本"的锚点抄写污染）。
        unfold_ids：召回命中的文件原子（在 tiling 中、被影子注入跳过）→ 展开逐字，
        召回语义 = 把原文拉回视野。
        clean=True（2026-09-25 拍定）：原子层 = 干净对话——连续同（来源,轮次）原子
        折叠为一段，段首只标〔来源〕；无 #id/·原文/〔已回复〕（机制地址收回程序层，
        模型读到的应该是一份对话，不是数据结构转储）。卡片层（摘要/影子节点）与
        代码折叠保留从属标注（§4.5 硬要求：层级歧义处必须标注）。"""
        clean = self.render_clean if clean is None else clean
        unfold = set(unfold_ids or ())
        lines = []
        seg_key = None
        for kind, item, _value in emitted:
            if kind == "atom":
                a = self.atoms[item]
                src = (a.metadata or {}).get("source") or ""
                if fold_files and src.startswith("file:") and item not in unfold:
                    first_line = (a.value or "").splitlines()[0] if (a.value or "").splitlines() else ""
                    if not first_line.strip():
                        first_line = "…"
                    elif len(first_line) > 90:
                        first_line = first_line[:90] + "…"
                    fname = src.split("file:", 1)[1].rsplit("/", 1)[-1]
                    lines.append(f"[#{item}·文件索引〔{fname}〕] {first_line}")
                    seg_key = None
                    continue
                parent = a.owner
                depth = len(self._owner_chain(item))
                mark = f" ⊂N{parent}" if parent is not None else ""
                pend = "〔待失效〕" if (a.metadata or {}).get("pending_dismiss") else ""
                if clean:
                    key = (src, a.created_round)
                    if key != seg_key:
                        lines.append(f"▸〔{self._disp_src(src)}〕" if src else "▸")
                        seg_key = key
                    lines.append(f"  {mark}{pend}{a.value}")
                else:
                    src_tag = f"〔{self._disp_src(src)}〕" if src else ""
                    lines.append(f"{'  ' * depth}[#{item}·原文{mark}{src_tag}{pend}] {a.value}")
            elif kind in ("shadow", "expand") and self._is_atom(item):
                a = self.atoms[item]
                src = (a.metadata or {}).get("source") or ""
                pend = "〔待失效〕" if (a.metadata or {}).get("pending_dismiss") else ""
                word = "召回" if kind == "shadow" else "展开"
                if clean:
                    lines.append(f"  〔{word}·{self._disp_src(src) or '?'}〕{pend}{a.value}")
                else:
                    parent = a.owner
                    depth = len(self._owner_chain(item))
                    mark = f" ⊂N{parent}" if parent is not None else ""
                    src_tag = f"〔{self._disp_src(src)}〕" if src else ""
                    lines.append(f"{'  ' * depth}[#{item}·{word}原文{mark}{src_tag}{pend}] {a.value}")
                seg_key = None
            elif kind == "self_quota" and self._is_atom(item):
                # self_quota（2026-10-04）：注入的自身原话——渲染成**正常自身发言段**
                # （与 atom 分支同款 `▸〔名字〕`，参与段去重），不作"召回"标注，
                # 便于与 flat 的"连续自身发言"对照。
                a = self.atoms[item]
                src = (a.metadata or {}).get("source") or ""
                pend = "〔待失效〕" if (a.metadata or {}).get("pending_dismiss") else ""
                if clean:
                    key = (src, a.created_round)
                    if key != seg_key:
                        lines.append(f"▸〔{self._disp_src(src)}〕" if src else "▸")
                        seg_key = key
                    lines.append(f"  {pend}{a.value}")
                else:
                    parent = a.owner
                    depth = len(self._owner_chain(item))
                    mark = f" ⊂N{parent}" if parent is not None else ""
                    src_tag = f"〔{self._disp_src(src)}〕" if src else ""
                    lines.append(f"{'  ' * depth}[#{item}·自身原文{mark}{src_tag}{pend}] {a.value}")
                    seg_key = None
            else:
                nd = self.nodes[item]
                parent = nd.parent
                depth = self._depth(item)
                tag = {"shadow": "召回影子", "expand": "展开子卡"}.get(kind, "摘要")
                mark = f" ⊂N{parent}" if parent is not None else ""
                # 程序维护的区间来源标注（不依赖蒸馏；防"来源漂移"在展示层可见）
                srcs = self._node_sources(item)
                src_tag = ""
                if srcs:
                    clean_srcs = sorted(self._disp_src(s) for s in srcs if s and s != "?")
                    if clean_srcs:
                        src_tag = f"〔源:{'/'.join(clean_srcs)}〕"
                if nd.value is None:
                    content = "【占位：近期修改，概要暂缺】"
                else:
                    # 【注】不渲染（2026-10-03 拍定）：独立生成并保留在 value 中供锚点/分析，
                    # 但组装给模型的卡片只呈现【事实】（保留〔另有X条〕索引行）——避免出戏。
                    content = self._split_fact_note(nd.value)[0]
                lines.append(f"{'  ' * depth}[N{item}·{tag}{mark}{src_tag}] {content}")
                seg_key = None
        return "\n".join(lines)

    def story_full_text(self):
        parts = []
        cur = self.head
        while cur is not None:
            parts.append(self.atoms[cur].value)
            cur = self.atoms[cur].next
        return "\n".join(parts)
