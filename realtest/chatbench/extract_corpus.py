#!/usr/bin/env python3
"""extract_corpus.py — 从 ahi-multi 平台 system.db 提取聊天智能体实验语料为
纯文本文档（回放输入 + gold 标注底本）。

产物（out/ 下）：
  corpus.jsonl          机器可读回放流（稳定 idx/msg_id/session/...）
  transcript_full.txt   人读全量转写（时间序、去重、说话人标注、来源→目标）
  by_agent/<name>_<id>.txt  单 agent 全部发言
  sandbox_notes.md      沙箱协作笔记（agent 现场生成的受控语料）
  reports/*.md          已有的分析报告（T3 报告验证题的参照物）
  index.json            统计 + 实体表骨架 + 会话切分

纪律：**原始措辞零清洗**（口语/错字/省略/表情全部保留）；稳定 id 供 gold 标注引用。
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

PRESETS = {
    "ctx": {"bench-tf": "青", "bench-tc": "蓝", "bench-bf": "墨", "bench-bc": "白"},
    "absence": {"lan": "澜", "lin-shen": "林深", "neutral": "中性"},
}


def bare(a):
    return a.split(":", 1)[1] if ":" in (a or "") else (a or "")


def load(db, agents, include_users=False):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id,msg_id,from_agent,to_target,content,msg_type,timestamp "
        "FROM messages ORDER BY id")]
    conn.close()
    out, seen = [], set()
    for r in rows:
        f = bare(r["from_agent"])
        is_user = (r["from_agent"] or "").startswith("user:")
        if f not in agents and not (include_users and is_user):
            continue
        if r["msg_type"] in ("receipt", "shell_result"):
            continue
        c = (r["content"] or "").strip()
        if not c:
            continue
        # 只折叠“同一发送者 + 同一时刻 + 同一内容”的多目标投递副本；
        # 不同时刻的重复发言（真实事件）必须保留——否则丢失时序/复现信号。
        key = (r["from_agent"], r["timestamp"], c)
        if key in seen:
            continue
        seen.add(key)
        r["agent"] = r["from_agent"]
        r["name"] = agents.get(f) or (r["from_agent"])
        out.append(r)
    return out


def segment(msgs, gap_hours, window_minutes=None):
    """会话切分：window_minutes>0 → 固定时间窗（连续对话的伪会话）；
    否则按 gap_hours 动态切。session 序号写回消息。"""
    from datetime import datetime
    fmt = "%Y-%m-%d %H:%M:%S"

    def parse(m):
        try:
            return datetime.strptime(m["timestamp"], fmt)
        except Exception:
            return None

    if window_minutes:
        t0, sess = None, 0
        for m in msgs:
            t = parse(m)
            if t is not None:
                if t0 is None:
                    t0 = t
                sess = int((t - t0).total_seconds() // (window_minutes * 60))
            m["session"] = sess
        return sess + 1
    sess, prev = 0, None
    for m in msgs:
        t = parse(m)
        if prev is not None and t is not None and (t - prev).total_seconds() > gap_hours * 3600:
            sess += 1
        m["session"] = sess
        if t is not None:
            prev = t
    return sess + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "ahi-multi", "data", "system.db"))
    ap.add_argument("--out", default=os.path.join(HERE, "corpus"))
    ap.add_argument("--source-run", default=os.path.join(
        ROOT, "ahi-bench", "out", "ctx_run"))
    ap.add_argument("--preset", choices=list(PRESETS), default="ctx")
    ap.add_argument("--agents", default=None, help="逗号分隔的 agent id（覆盖 preset 名称映射）")
    ap.add_argument("--include-users", action="store_true",
                    help="把 user:* 消息也纳入记忆流（聊天回放需要）")
    ap.add_argument("--gap-minutes", type=float, default=None,
                    help="会话切分 gap（分钟）；缺省用 --gap-hours")
    ap.add_argument("--gap-hours", type=float, default=6.0)
    ap.add_argument("--session-window-minutes", type=float, default=None,
                    help="固定时间窗伪会话（连续对话用；优先于 gap）")
    ap.add_argument("--limit", type=int, default=0, help=">0 只取前 N 条（冒烟）")
    args = ap.parse_args()

    agents = dict(PRESETS[args.preset])
    if args.agents:
        agents = {a.strip(): a.strip() for a in args.agents.split(",") if a.strip()}
    gap_h = (args.gap_minutes / 60.0) if args.gap_minutes is not None else args.gap_hours
    msgs = load(args.db, agents, include_users=args.include_users)
    if args.limit:
        msgs = msgs[:args.limit]
    n_sess = segment(msgs, gap_h, window_minutes=args.session_window_minutes)

    os.makedirs(os.path.join(args.out, "by_agent"), exist_ok=True)
    os.makedirs(os.path.join(args.out, "reports"), exist_ok=True)

    # 1) corpus.jsonl（回放输入，稳定 idx）
    with open(os.path.join(args.out, "corpus.jsonl"), "w", encoding="utf-8") as f:
        for i, m in enumerate(msgs):
            f.write(json.dumps({
                "idx": i, "msg_id": m["msg_id"], "ts": m["timestamp"],
                "session": m["session"], "agent": m["agent"], "name": m["name"],
                "to": m["to_target"], "msg_type": m["msg_type"], "content": m["content"],
            }, ensure_ascii=False) + "\n")

    # 2) transcript_full.txt
    with open(os.path.join(args.out, "transcript_full.txt"), "w", encoding="utf-8") as f:
        cur_sess = None
        for m in msgs:
            if m["session"] != cur_sess:
                cur_sess = m["session"]
                f.write(f"\n\n{'='*20} 会话 {cur_sess} "
                        f"（{m['timestamp']} 起） {'='*20}\n\n")
            f.write(f"### [{m['timestamp']}] #{m['msg_id'][:8]} "
                    f"{m['name']}({m['agent']}) → {m['to_target']}\n{m['content']}\n\n")

    # 3) by_agent
    for aid, name in agents.items():
        with open(os.path.join(args.out, "by_agent", f"{name}_{aid}.txt"),
                  "w", encoding="utf-8") as f:
            for m in msgs:
                if m["agent"] == aid:
                    f.write(f"### [{m['timestamp']}] → {m['to_target']}\n{m['content']}\n\n")

    # 4) 沙箱笔记 + 分析报告（参照物）
    notes = os.path.join(args.source_run, "sandbox", "world", "notes.md")
    if os.path.exists(notes):
        shutil.copy(notes, os.path.join(args.out, "sandbox_notes.md"))
    rep_dir = os.path.join(args.out, "reports")
    for fn in os.listdir(args.source_run):
        if fn.endswith(".md") and fn != "sandbox_notes.md":
            shutil.copy(os.path.join(args.source_run, fn), os.path.join(rep_dir, fn))

    # 5) index.json
    per_agent = Counter(m["agent"] for m in msgs)
    idx = {
        "source_db": os.path.relpath(args.db, ROOT),
        "n_messages": len(msgs), "n_sessions": n_sess,
        "total_chars": sum(len(m["content"]) for m in msgs),
        "time_range": [msgs[0]["timestamp"], msgs[-1]["timestamp"]] if msgs else None,
        "per_agent": {agents.get(a, a): {"id": a, "n": n} for a, n in per_agent.items()},
        "sessions": [
            {"session": s, "n": sum(1 for m in msgs if m["session"] == s),
             "start": next((m["timestamp"] for m in msgs if m["session"] == s), None)}
            for s in range(n_sess)
        ],
        "gold_hint": "gold 证据用 corpus.jsonl 的 msg_id 引用；transcript_full.txt 每条带 #[msg_id[:8]]。",
    }
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)

    print(json.dumps({k: idx[k] for k in
                      ("n_messages", "n_sessions", "total_chars", "time_range", "per_agent")},
                     ensure_ascii=False, indent=1))
    print("产出:", args.out)


if __name__ == "__main__":
    main()
