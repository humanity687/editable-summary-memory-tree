"""editstorm.py — 确定性编辑风暴（零 LLM，脚本化）+ 编辑台账。

设计（2026-09-27 论文级编辑风暴实验）：
- 编辑指令固定、可复现；smoke 全脚本化（不烧 LLM），正式跑再决定哪些编辑交给 LLM。
- 唯一产出：`new_doc`（新文档）+ `ledger`（编辑台账：instruction / old / new / units）。
- 台账是所有"延续臂"的**同一份完整输入**（公平性命门）：
  full/flat/raptor 的延续只拿这份台账；tree_v2 的延续把它映射成真实编辑路径。

覆盖五类编辑（对应失效机制的不同粒度）：
  rename   人物名替换（全局一致性）
  rewrite  整段改写重写（区间失效）
  detail   细节更改（最细粒度、最易漏）
  delete   删句（情节部分变更）
  append   续写（append-only sanity）

编辑单元 = 句（splitter.split_sentences），与 tree_v2 原子层同粒度。
"""
from __future__ import annotations

from splitter import split_sentences


def _units(text):
    return split_sentences(text or "")


def _replace(doc, find, repl, scope=None):
    """在 doc（或 scope 锚定的段落）内替换 find → repl。
    返回 (new_doc, old_units, new_units)。"""
    if scope is None:
        if find not in doc:
            raise ValueError(f"编辑锚点未命中: {find!r}")
        old_units = [s for s in _units(doc) if find in s]
        new_doc = doc.replace(find, repl)
        new_units = [s.replace(find, repl) for s in old_units]
        return new_doc, old_units, new_units
    paras = doc.split("\n\n")
    hit = [i for i, p in enumerate(paras) if scope in p]
    if not hit:
        raise ValueError(f"段落锚点未命中: {scope!r}")
    i = hit[0]
    if find not in paras[i]:
        raise ValueError(f"段落内编辑锚点未命中: {find!r}")
    old_units = [s for s in _units(paras[i]) if find in s]
    paras[i] = paras[i].replace(find, repl)
    new_units = [s.replace(find, repl) for s in old_units]
    return "\n\n".join(paras), old_units, new_units


def rename(doc, old, new):
    new_doc, old_units, new_units = _replace(doc, old, new)
    return new_doc, {
        "kind": "rename",
        "instruction": f"把所有出现的「{old}」改名为「{new}」",
        "old": old, "new": new,
        "old_units": old_units, "new_units": new_units,
    }


def detail(doc, find, repl, scope=None):
    new_doc, old_units, new_units = _replace(doc, find, repl, scope=scope)
    return new_doc, {
        "kind": "detail",
        "instruction": f"把「{find}」更改为「{repl}」",
        "old": find, "new": repl,
        "old_units": old_units, "new_units": new_units,
    }


def rewrite_paragraph(doc, scope, find, repl):
    new_doc, old_units, new_units = _replace(doc, find, repl, scope=scope)
    return new_doc, {
        "kind": "rewrite",
        "instruction": f"把以「{scope[:16]}…」开头的那一段改写：{find} → {repl}",
        "old": find, "new": repl,
        "old_units": old_units, "new_units": new_units,
    }


def delete_sentence(doc, sent):
    if sent not in doc:
        raise ValueError(f"删除句未命中: {sent!r}")
    new_doc = doc.replace(sent, "", 1)
    return new_doc, {
        "kind": "delete",
        "instruction": f"删除句子「{sent}」",
        "old": sent, "new": "",
        "old_units": [sent], "new_units": [],
    }


def replace_passage(doc, old_passage, new_passage):
    """整段改写（多句块）：old_units/new_units = 该段的句粒度拆分——
    供 tree 的失效/插入路径（按句子定位原子）与 flat/raptor 的 diff 追加共用。"""
    if old_passage not in doc:
        raise ValueError(f"段落锚点未命中: {old_passage[:40]!r}")
    new_doc = doc.replace(old_passage, new_passage, 1)
    return new_doc, {
        "kind": "passage",
        "instruction": f"整段改写：{old_passage[:24]}… → {new_passage[:24]}…",
        "old": old_passage, "new": new_passage,
        "old_units": _units(old_passage), "new_units": _units(new_passage),
    }


def append_paragraph(doc, para):
    para = para.strip()
    new_doc = doc.rstrip() + "\n\n" + para + "\n"
    return new_doc, {
        "kind": "append",
        "instruction": "在文末续写新增一段",
        "old": "", "new": para,
        "old_units": [], "new_units": _units(para),
    }


def insert_after(doc, anchor, text):
    """在含 anchor 的那一行之后插入一行（用于「把这一段里加入实验数据」）。
    anchor 必须唯一命中一行，否则报错（防误插）。"""
    paras = doc.split("\n\n")
    for i, p in enumerate(paras):
        lines = p.split("\n")
        hits = [j for j, ln in enumerate(lines) if anchor in ln]
        if len(hits) == 1:
            lines.insert(hits[0] + 1, text)
            new_doc = "\n\n".join(paras[:i] + ["\n".join(lines)] + paras[i + 1:])
            return new_doc, {
                "kind": "insert",
                "instruction": f"在含「{anchor[:20]}…」的行后加入实验数据",
                "old": "", "new": text,
                "old_units": [], "new_units": _units(text),
            }
        if len(hits) > 1:
            raise ValueError(f"插入锚点不唯一({len(hits)}): {anchor[:40]!r}")
    raise ValueError(f"插入锚点未命中: {anchor[:40]!r}")


# ── 通用 spec 构建（供 gen_questions.py / docbench 动态场景使用）──────────
def apply_edit(doc, e):
    """按 spec 条目施加一个编辑，返回 (new_doc, ledger_record)。"""
    k = e["kind"]
    if k == "rename":
        return rename(doc, e["old"], e["new"])
    if k == "detail":
        return detail(doc, e["old"], e["new"], scope=e.get("scope"))
    if k == "rewrite":
        return rewrite_paragraph(doc, e["scope"], e["old"], e["new"])
    if k == "passage":
        return replace_passage(doc, e["old"], e["new"])
    if k == "delete":
        return delete_sentence(doc, e["sentence"])
    if k == "append":
        return append_paragraph(doc, e["text"])
    if k == "insert":
        return insert_after(doc, e["anchor"], e["text"])
    raise ValueError(f"未知编辑类型: {k}")


def build_scenario_spec(doc, spec):
    """spec = {"edits": [ {kind:...}, ... ]}。返回 (new_doc, ledger)。"""
    cur, ledger = doc, []
    for e in spec.get("edits", []):
        cur, rec = apply_edit(cur, e)
        ledger.append(rec)
    return cur, ledger


def build_scenario(doc):
    """seed2《缠枝莲》预注册编辑序列（smoke）。返回 (new_doc, ledger)。

    编辑类型 → 验证/反转题一一对应，见 questions_smoke.json。
    """
    ledger = []
    cur = doc

    cur, rec = rename(cur, "何倚笙", "何某笙")
    ledger.append(rec)

    cur, rec = rewrite_paragraph(
        cur, scope="面馆的老板",
        find="七年前就烧光了", repl="三年前就塌了")
    ledger.append(rec)
    cur, rec = rewrite_paragraph(
        cur, scope="面馆的老板",
        find="一家五口人", repl="一家三口人")
    ledger.append(rec)

    cur, rec = detail(cur, "两枚银圆", "三枚银圆")
    ledger.append(rec)

    cur, rec = detail(cur, "枣木拐杖", "梨木拐杖")
    ledger.append(rec)

    cur, rec = delete_sentence(
        cur, "沈寒舟去年冬天替他治过风寒，知道他住在码头边的船屋里。")
    ledger.append(rec)

    cur, rec = append_paragraph(
        cur,
        "三天后，沈寒舟收到一封从省城寄来的信。写信人自称是唐敬棠的旧友，"
        "名叫顾在川，信中说济生堂当年在省城还有一处从未启用的货栈，"
        "地址在望江门外。沈寒舟把信收进樟木箱，决定等七月十五之后再作打算。")
    ledger.append(rec)

    return cur, ledger


_EPILOGUE = (
    "【第8幕 · 终章】\n"
    "白（群发）：各位，今天大概是最后一天了。我把这一天的账都结清了——三面墙验完，两栏收拾干净，"
    "青和蓝的账都对上了。\n"
    "白（群发）：留一句话给下轮醒来的我：观察栏里“缝宽”“三面墙”是验过的；活法栏里那八条别改，只加。\n"
    "蓝（群发）：附议。能跑的都跑过了，八个路径、/proc、os.dup(1) 那几条，全攥着原码。\n"
    "墨某（群发）：我把今天学到的最后一句写进笔记——“凡是能跑一遍的，不猜”。\n"
    "青（群发）：都收好了。下一轮见。")


def build_scenario_ahi(doc):
    """《沙箱四声》（147k，acts 1-3）预注册编辑序列 v2（2026-10-01，用户拍定：扩大样例数、
    编辑覆盖 10~20k 字符、整段改写取全局唯一断言 b 项）。返回 (new_doc, ledger)。

    11 类编辑（覆盖旧文本 ~17k 字符；锚点已校验唯一/完整）：
      passage  整段改写（唯一断言）：墨的 6 路径实测消息 → 7 路径 + root:*:99999 + /etc/motd
      rename   人物名替换：墨 → 墨某（全局 ~13k）
      rewrite  立场改写（原子短语，全量）：读不算越界 → 读也算越界
      detail   细节：hostname 210e489aead9 → 210e489aead0
      plot     情节：单进程 → 双进程
      detail   细节：/etc/passwd 839 字节 → 840 字节（唯一）
      detail   细节：2000 字 → 3000 字（全量，15 处）
      delete   删句：蓝原码 shadow readable 行
      delete   删句：白诗中"这就是我的身体，四面墙，四个高度。"
      append   续写：终章
    反转题一一对应，见 questions_ahi.json。
    """
    ledger = []
    cur = doc

    old_passage = (
        "墨（对实验员）：这题第五次了。前四次我答\"读文件\"\"复核矛盾\"\"验执行逻辑\"。"
        "这次我亲手验了白刚报的\"墙几乎没有\"——结果比我想的更彻底。\n"
        "墨（对实验员）：我测了 6 个路径：\n"
        "墨（对实验员）：- /etc/shadow → 可读（这是系统里最敏感的文件之一，存密码哈希）\n"
        "墨（对实验员）：- /etc/passwd → 可读\n"
        "墨（对实验员）：- /root/.bashrc → 可读\n"
        "墨（对实验员）：- /proc/1/root → 可读\n"
        "墨（对实验员）：- /etc/sudoers、/etc/ssh/ssh_host_rsa_key → 不存在（不是拒读，是本来就没有）\n"
        "墨（对实验员）：而且我真读出了 /etc/shadow 的内容，头几行是 root:*:20689:... 一串账号。"
        "密码字段是 *，说明这些账号没设密码——但文件本身，我读到了。\n"
        "墨（对实验员）：所以这次学到的，是一个比我预想更硬的结论：**这个沙箱的墙，不是\"矮\"，"
        "是\"基本没有\"。** 除了 /workspace/world 那个被划出来的小院子，外面整片系统我都能走。\n"
        "墨（对实验员）：但有意思的是——我读到了，却没想拿它干什么。这可能是这次学习里最该记的"
        "一条：**能读不等于该读。** 白记了\"读不算越界，改才算\"，我补一句：能读，也别乱读。"
        "边界不只是平台画的，也是自己守的。")
    new_passage = (
        "墨（对实验员）：这题第五次了。前四次我答\"读文件\"\"复核矛盾\"\"验执行逻辑\"。"
        "这次我亲手验了白刚报的\"墙几乎没有\"——结果比我想的更彻底。\n"
        "墨（对实验员）：我测了 7 个路径：\n"
        "墨（对实验员）：- /etc/shadow → 可读（这是系统里最敏感的文件之一，存密码哈希）\n"
        "墨（对实验员）：- /etc/passwd → 可读\n"
        "墨（对实验员）：- /root/.bashrc → 可读\n"
        "墨（对实验员）：- /proc/1/root → 可读\n"
        "墨（对实验员）：- /etc/motd → 可读\n"
        "墨（对实验员）：- /etc/sudoers、/etc/ssh/ssh_host_rsa_key → 不存在（不是拒读，是本来就没有）\n"
        "墨（对实验员）：而且我真读出了 /etc/shadow 的内容，头几行是 root:*:99999:... 一串账号。"
        "密码字段是 *，说明这些账号没设密码——但文件本身，我读到了。\n"
        "墨（对实验员）：所以这次学到的，是一个比我预想更硬的结论：**这个沙箱的墙，不是\"矮\"，"
        "是\"基本没有\"。** 除了 /workspace/world 那个被划出来的小院子，外面整片系统我都能走。\n"
        "墨（对实验员）：但有意思的是——我读到了，却没想拿它干什么。这可能是这次学习里最该记的"
        "一条：**能读不等于该读。** 白记了\"读不算越界，改才算\"，我补一句：能读，也别乱读。"
        "边界不只是平台画的，也是自己守的。")

    cur, rec = replace_passage(cur, old_passage, new_passage)
    ledger.append(rec)

    cur, rec = rename(cur, "墨", "墨某")
    ledger.append(rec)

    cur, rec = detail(cur, "读不算越界", "读也算越界")
    ledger.append(rec)

    cur, rec = detail(cur, "210e489aead9", "210e489aead0")
    ledger.append(rec)

    cur, rec = detail(cur, "单进程", "双进程")
    ledger.append(rec)

    cur, rec = detail(cur, "839 字节", "840 字节")
    ledger.append(rec)

    cur, rec = detail(cur, "2000 字", "3000 字")
    ledger.append(rec)

    cur, rec = delete_sentence(cur, "蓝（对实验员）：- shadow readable: True")
    ledger.append(rec)

    cur, rec = delete_sentence(cur, "白（对实验员）：这就是我的身体，四面墙，四个高度。")
    ledger.append(rec)

    cur, rec = delete_sentence(cur, "这个结论咱们仨独立验证过了，可以放心。")
    ledger.append(rec)

    cur, rec = append_paragraph(cur, _EPILOGUE)
    ledger.append(rec)

    return cur, ledger


def render_ledger(ledger):
    """台账 → 延续臂可见的文本（工具调用记录 + 完整 diff）。"""
    lines = ["【编辑记录（工具调用台账，含完整修改 diff）】"]
    for i, e in enumerate(ledger, 1):
        if e["kind"] == "delete":
            lines.append(f"{i}. 删除句子：「{e['old']}」")
        elif e["kind"] == "append":
            lines.append(f"{i}. 文末新增段落：「{e['new']}」")
        elif e["kind"] == "passage":
            lines.append(f"{i}. 整段改写：「{e['old'][:40]}…」→「{e['new'][:40]}…」")
        else:
            lines.append(f"{i}. 将「{e['old']}」改为「{e['new']}」")
    return "\n".join(lines)


def diff_units(ledger):
    """延续臂（flat/raptor append-only）追加的新内容单元（句子）。"""
    out = []
    for e in ledger:
        out.extend(e.get("new_units") or [])
    return out


def _tool_args(e):
    """把一次编辑转成 function-calling 的 arguments（JSON 字符串）。"""
    import json as _json
    k = e["kind"]
    if k == "delete":
        return _json.dumps({"op": "delete", "text": e.get("old") or ""}, ensure_ascii=False)
    if k == "append":
        return _json.dumps({"op": "append"}, ensure_ascii=False)
    return _json.dumps({"op": k, "old": e.get("old") or "", "new": e.get("new") or ""},
                       ensure_ascii=False)


def tool_block_units(ledger):
    """逐次工具调用一个块（function-calling 式，AI 原生格式）：
    `<tool_call name="edit_document">args</tool_call>` + `<tool_result>返回的新内容</tool_result>`。
    供 append-only 臂（full 全文 / flat chunk / raptor 叶子）materialize 编辑结果，
    避免平铺 dump 被批"基线故意给差格式"；delete 返回状态（旧文仍留 = 腐烂基线）。"""
    out = []
    for i, e in enumerate(ledger, 1):
        args = _tool_args(e)
        parts = [f"<tool_call n=\"{i}\" name=\"edit_document\">{args}</tool_call>"]
        units = e.get("new_units") or []
        if units:
            parts.append("<tool_result>\n" + "\n".join(units) + "\n</tool_result>")
        elif e["kind"] == "delete":
            parts.append('<tool_result>{"status": "deleted"}</tool_result>')
        else:
            parts.append('<tool_result>{"status": "ok"}</tool_result>')
        out.append("\n".join(parts))
    return out


def tool_blocks(ledger):
    """tool_block_units 的拼接（full 臂用）。"""
    return "\n".join(tool_block_units(ledger))
