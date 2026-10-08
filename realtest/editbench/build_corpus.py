#!/usr/bin/env python3
"""build_corpus.py — 从项目**已跟踪文档**拼出论文级长参考语料（3 篇主题文档）。

定位：editbench 的「第三类语料」——项目知识库（真实、无版权、实体/数字/ID 密集），
与 seed2（叙事）、ahi（对话）互补，满足多文档泛化要求。仅用 git 跟踪的文件，
保证可复现、可分发（排除 out/、corpus/、debug_review/ 等本地产物）。

输出：<outdir>/{D1_design,D2_experiments,D3_platform}.txt + report.json
"""
from __future__ import annotations

import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)

# 三篇主题分组（均为 git 跟踪的相对路径）
GROUPS = {
    "D1_design": [
        "AGENTS.md",
        "realtest/USAGE_SPEC.md",
        "ahi-bench/PERSONA_RETENTION.md",
        "ahi-bench/SPEC.md",
    ],
    "D2_experiments": [
        "realtest/editbench/EXPERIMENT_REPORT.md",
        "realtest/editbench/AHI_ACT_REVIEW.md",
        "realtest/editbench/REPORT.md",
        "realtest/editbench/INNOVATION_REVIEW.md",
        "realtest/chatbench/EXPERIMENT_REPORT.md",
        "realtest/chatbench/PLAN.md",
        "realtest/chatbench/COMPARE_REPORT.md",
        "ahi-bench/PR5_REPORT.md",
        "ahi-multi/experiments/EXPERIMENTS.md",
        "ahi-multi/experiments/ABSENCE_EXPERIMENT.md",
        "ahi-multi/experiments/ARCHIVE_CHAT.md",
        "ahi-multi/experiments/analysis/ANALYSIS_COLD_HOT.md",
        "ahi-multi/experiments/analysis/ANALYSIS_COLD_1H.md",
    ],
    "D3_platform": [
        "README.md",
        "HANDOFF.md",
        "ahi-bench/README.md",
        "ahi-multi/HANDOFF.md",
        "ahi-multi/README.md",
        "ahi-multi/CHANGES.md",
        "ahi-multi/CLAUDE.md",
        "ahi-multi/next_exp_insts.md",
    ],
}

# 自指护栏：源文件若含本基准自身的语料/出题设计标记 → 拒绝入语料（防泄题）。
# 设计文档已移至 CORPUS_QA.md（不在任何 GROUPS 中）。
_SELF_REF_MARK = ("题库与预注册", "coverage_record", "build_corpus", "gen_questions")

_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")      # |---|---| 分隔行（噪声）
_SENT_SPLIT = re.compile(r"[。！？!?\n]+")               # 粗切句（统计用，非引擎切分）
_NUM = re.compile(r"\d{2,}")
_LATIN_ID = re.compile(r"[A-Za-z0-9_./:-]{6,}")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\uFEFF]")
MAX_LINE = 600                                          # 折行阈值（防单行超长）

# 自指污染清洗：语料来自本基准自己的实验报告，正文含**旧编辑对/答案键**
# （见 D2/D3 质检）。含这些 token 的**行**一律剔除，避免锚点冲突与“语料自含答案”。
BLACKLIST = (
    "Q_rename", "Q_detail", "Q_append", "Q_passage", "Q_delete",
    "check_before", "check_after",
    "墨某", "读也算越界", "读不算越界",
    "210e489aead", "root:*:", "840 字节", "839 字节",
    "shadow readable", "这就是我的身体", "咱们仨独立验证过了",
    "枣木拐杖", "梨木拐杖", "何某笙", "何倚笙",
)


def _blacklisted(ln):
    return any(tok in ln for tok in BLACKLIST)


# 去标识化：架构名按**结构**中性化（笼统），基础模型统一为 glm-4.7-flash。
# 长/具体在前，避免子串误替换。URL/密钥里的 deepseek 不在表内（不动）。
DEID = [
    ("deepseek-v4-flash", "glm-4.7-flash"),
    ("deepseek-v4-pro", "glm-4.7-flash"),
    ("deepseek-reasoner", "glm-4.7-flash"),
    ("deepseek-flash", "glm-4.7-flash"),
    ("deepseek-chat", "glm-4.7-flash"),
    ("deepseek-r1:1.5b", "glm-4.7-flash"),
    ("gemma4:26b", "glm-4.7-flash"), ("gemma4:12b", "glm-4.7-flash"),
    ("gemma4:31b-cloud", "glm-4.7-flash"), ("gemma4:31b", "glm-4.7-flash"),
    ("gemma4:e2b", "glm-4.7-flash"), ("gemma3:4b", "glm-4.7-flash"),
    ("qwen3.8:27b-mlx", "glm-4.7-flash"), ("qwen3.8:27b", "glm-4.7-flash"),
    ("qwen3.5:397b-cloud", "glm-4.7-flash"),
    ("muse-glimmer:30b-mlx", "glm-4.7-flash"), ("muse-glimmer:30b", "glm-4.7-flash"),
    ("nemotron-3-super:cloud", "glm-4.7-flash"),
    ("glm-5.1:cloud", "glm-4.7-flash"), ("glm-5:cloud", "glm-4.7-flash"),
    # 架构名（结构导向、笼统）
    ("watercycle", "hierarchical-memory-tree"),
    ("WaterCycle", "HierarchicalMemoryTree"),
    ("水循环", "分层记忆树"),
    ("RAPTOR", "递归聚簇树"), ("raptor", "递归聚簇树"), ("Raptor", "递归聚簇树"),
    ("tree_v2", "分层树"), ("TreeArm", "分层树臂"),
    ("ConceptEngine", "概念中介层"),
]


def _deid(text):
    for a, b in DEID:
        text = text.replace(a, b)
    return text


def _clean_lines(text, blacklist=True):
    """去 BOM/控制符、CRLF 归一、丢纯表格分隔行与自指污染行、超长行折行。"""
    out = []
    for ln in _CTRL.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).split("\n"):
        if _TABLE_SEP.match(ln):
            continue
        if blacklist and _blacklisted(ln):
            continue
        ln = ln.rstrip()
        while len(ln) > MAX_LINE:           # 折行（不改字符，只插换行；句子切分不受影响）
            out.append(ln[:MAX_LINE])
            ln = ln[MAX_LINE:]
        out.append(ln)
    return out


def _dedup_paras(text, min_len=40):
    """跨文件精确段去重（保留首次出现）；只对 >=min_len 的段生效。"""
    paras = text.split("\n\n")
    seen, kept, removed = set(), [], 0
    for p in paras:
        key = p.strip()
        if len(key) >= min_len:
            if key in seen:
                removed += 1
                continue
            seen.add(key)
        kept.append(p)
    return "\n\n".join(kept), removed


def _stats(sents, lines):
    lines20 = [l.strip() for l in lines if len(l.strip()) >= 20]
    sents = [s.strip() for s in sents if len(s.strip()) >= 12]
    def dup_ratio(items):
        if not items:
            return 0.0
        return round(1 - len(set(items)) / len(items), 4)
    anchors = [l for l in lines20 if len(l) >= 24 and ("：" in l or "是" in l or "为" in l)]
    return {
        "dup_line_ratio": dup_ratio(lines20),
        "dup_sent_ratio": dup_ratio(sents),
        "n_lines_ge20": len(lines20),
        "unique_lines_ge20": len(set(lines20)),
        "n_sents": len(sents),
        "unique_sents": len(set(sents)),
        "n_distinct_numbers": len(set(_NUM.findall("\n".join(lines)))),
        "n_distinct_latin_ids": len({m for m in _LATIN_ID.findall("\n".join(lines))}),
        "n_anchor_like_lines": len(anchors),
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=os.path.join(_HERE, "out", "corpus_proj"))
    ap.add_argument("--sep", default="\n\n===== 文件：{path} =====\n\n")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    report = {}
    for name, files in GROUPS.items():
        parts, missing, sizes = [], [], {}
        for rel in files:
            p = os.path.join(_ROOT, rel)
            if not os.path.exists(p):
                missing.append(rel)
                continue
            txt = open(p, encoding="utf-8", errors="ignore").read().strip()
            hit = [m for m in _SELF_REF_MARK if m in txt]
            if hit:
                raise SystemExit(f"[自指护栏] {rel} 含基准设计标记 {hit}，禁止入语料。"
                                 f"（把该内容移到 CORPUS_QA.md）")
            sizes[rel] = len(txt)
            parts.append(args.sep.format(path=rel) + _deid(txt))
        raw = "\n\n".join(parts)
        lines = _clean_lines(raw)
        cleaned, n_dup_paras = _dedup_paras("\n".join(lines))
        st = _stats(_SENT_SPLIT.split(cleaned), cleaned.split("\n"))
        st["chars"] = len(cleaned)
        st["raw_chars"] = len(raw)
        st["dedup_paras_removed"] = n_dup_paras
        st["files"] = sizes
        st["missing"] = missing
        st["n_files"] = len(sizes)
        report[name] = st
        out = os.path.join(args.outdir, f"{name}.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(cleaned)
        print(f"[{name}] {st['chars']:,} 字符 / {st['n_files']} 文件 | "
              f"去重段 {n_dup_paras} | 重复行率 {st['dup_line_ratio']:.1%} "
              f"重复句率 {st['dup_sent_ratio']:.1%} | "
              f"唯一长行 {st['unique_lines_ge20']:,} 数字 {st['n_distinct_numbers']:,} "
              f"ID {st['n_distinct_latin_ids']:,} → {out}")
        if missing:
            print(f"   [缺] {missing}")

    rp = os.path.join(args.outdir, "report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"[done] report → {rp}")


if __name__ == "__main__":
    main()
