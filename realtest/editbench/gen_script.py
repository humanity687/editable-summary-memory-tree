#!/usr/bin/env python3
"""gen_script.py — 从 ctx_run 四智能体实录生成剧本文档（≥100k 字符）。

输出 = 剧本（序幕人物表 + 按时间分幕 + 逐条对话 + 收信人舞台指示）。
对话内容逐字取自 corpus.jsonl（不改写，保真）；scene 划分按时间间隙。
用途：论文级编辑风暴实验正式轮底本（替代 seed2，无版权、事实密度可控）。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))


def _hhmm(ts):
    return ts[11:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=os.path.join(_HERE, "..", "chatbench", "corpus", "corpus.jsonl"))
    ap.add_argument("--out", default=os.path.join(_HERE, "ahi_script.txt"))
    ap.add_argument("--scene-minutes", type=int, default=10,
                    help="按时间桶分幕：每 N 分钟一幕")
    ap.add_argument("--acts", type=int, default=0,
                    help="只保留前 N 幕（0=全部）；用于按预算比裁剪文档")
    args = ap.parse_args()

    msgs = [json.loads(l) for l in open(args.corpus, encoding="utf-8") if l.strip()]

    personas = {}
    for m in msgs:
        personas.setdefault(m["name"], {"agent": m["agent"]})

    lines = []
    lines.append("《沙箱四声》")
    lines.append("")
    lines.append("——一部关于四个智能体在一座沙箱里醒来、勘界、立规的剧本。")
    lines.append("")
    lines.append("人物表")
    for name, info in personas.items():
        lines.append(f"  {name}（{info['agent']}）")
    lines.append("  实验员（user:experimenter）")
    lines.append("")

    scene = 0
    last_bucket = None

    def _new_scene(ts):
        nonlocal scene
        scene += 1
        lines.append("")
        lines.append(f"【第{scene}幕】 {ts[:16]}")
        lines.append("")

    for m in msgs:
        ts = m["ts"]
        dt = datetime.fromisoformat(ts)
        bucket = dt.hour * 60 + (dt.minute // args.scene_minutes)
        if bucket != last_bucket:
            if args.acts and scene >= args.acts:
                break
            _new_scene(ts)
            last_bucket = bucket

        name = m["name"]
        to = m["to"]
        if to.startswith("broadcast"):
            addr = "（群发）"
        elif to.startswith("user:"):
            addr = "（对实验员）"
        else:
            addr = f"（→ {to}）"

        content = (m["content"] or "").strip()
        for para in content.split("\n"):
            para = para.strip()
            if not para:
                continue
            lines.append(f"{name}{addr}：{para}")
        lines.append("")

    text = "\n".join(lines)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[gen_script] {len(msgs)} 条消息 → {len(text)} 字符，{scene} 幕 → {args.out}")


if __name__ == "__main__":
    main()
