"""v2.3.65 戳一戳（摸头）回应改造 回归测试

验证：
  1. poke_reminder 章节存在且内容完整（要求读当下气氛、不复述上文专有词、不随机抽情绪）
     ★ 2026-09-30 追加：规则里**不许出现具名字词**（列举即污染，实测会被照抄）
  2. 不再残留旧的「随机语气」抽签说明与「不要展开话题」的禁结合条款
  3. _build_reminder(..., append_plain=False) 不追加 plain_text_rule
  4. _build_reminder(...) 默认仍追加 plain_text_rule（防回归）
  5. core/pipeline.py 里已无硬编码的戳一戳规则（改走提示词文件）
  6. 戳一戳请求真的把上下文喂给 LLM，且**只喂最近 2 条**；自己上次的回应走 extra_info 清单

用法（本地）:
  python tests/_test_v2365_poke.py
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  \033[32mOK\033[0m   {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  \033[31mFAIL\033[0m {msg}")


def main() -> None:
    from services import llm

    sec = llm._load_skill_sections()
    print("=== 1. poke_reminder 章节 ===")
    poke = sec.get("poke_reminder", "")
    if poke:
        ok(f"章节存在（{len(poke)} 字）")
    else:
        bad("章节缺失 —— 戳一戳会走 _REMINDER_FALLBACK 兜底，规则全丢")

    must = ["气氛", "1 句", "专有词"]
    for k in must:
        (ok if k in poke else bad)(f"含关键词 {k!r}")

    # ★ 2026-09-30 实测教训：**规则里写具体字/句作例子，模型就会照抄**。
    #   把「X 就 X」列为禁止句式 → 它反而复现 3 次；
    #   10_format_group 里点名「唔/诶」是语气词 → 首字「诶」占 18.3%、短上下文时「唔」占 7/8。
    for tainted in ("卷子", "披风", "摸头就摸头", "X 就 X", "唔", "诶"):
        if tainted in poke:
            bad(f"规则里出现具名字词 {tainted!r} —— 列举即污染，会被照抄")
        else:
            ok(f"规则里不含具名字词 {tainted!r}")

    print("\n=== 2. 旧病灶已移除 ===")
    bad_markers = [
        ("随机语气", "旧的「随机语气：从 6 种情绪中随机选一种」抽签说明"),
        ("可以从疑惑", "旧的固定情绪清单（抽签池）"),
        ("不要展开话题", "旧的「不要展开话题」（直接禁止结合上下文）"),
    ]
    for marker, why in bad_markers:
        (ok if marker not in poke else bad)(f"不再出现 {marker!r}（{why}）")

    print("\n=== 3/4. _build_reminder 的 append_plain ===")
    plain = sec.get("plain_text_rule", "")
    marker = "输出必须是纯文本"
    if not plain:
        bad("plain_text_rule 章节缺失，无法验证")
    else:
        no_plain = llm._build_reminder("poke_reminder", append_plain=False)
        if marker not in no_plain:
            ok("append_plain=False → 不追加 plain_text_rule")
        else:
            bad("append_plain=False 仍追加了 plain_text_rule")
        if no_plain.strip() == poke.strip():
            ok("append_plain=False 返回的就是章节原文")
        else:
            bad("append_plain=False 的返回值与章节原文不一致")
        # 回归：默认行为不能变
        with_plain = llm._build_reminder("poke_reminder")
        if marker in with_plain:
            ok("默认（append_plain=True）仍追加 plain_text_rule")
        else:
            bad("默认行为被改坏：plain_text_rule 没追加")
        # reply_reminder 的既有用法不受影响
        rr = llm._build_reminder("reply_reminder", ctx_hint="H", max_chars="40", no_repeat="")
        if marker in rr:
            ok("reply_reminder 既有调用仍未受影响")
        else:
            bad("reply_reminder 不再追加 plain_text_rule（回归！）")

    print("\n=== 5. pipeline 里不再硬编码戳一戳规则 ===")
    import core.pipeline as pl
    src = inspect.getsource(pl.handle_poke_event)
    # 注意：inspect 会带上注释，所以这里比对**旧规则的完整原文**，
    # 不能用「不要展开话题」这种短词（新加的说明注释里会引用它）。
    old_rules = [
        "戳一戳规则：只用 1 句简短回应",
        "只用 1 句简短回应，不要展开话题",
        "禁止重复：绝对不要说摸头很舒服",
        "随机语气：可以从疑惑、开心、害羞",
    ]
    for marker in old_rules:
        (ok if marker not in src else bad)(f"handle_poke_event 不再硬编码 {marker!r}")
    if '_build_reminder("poke_reminder"' in src:
        ok("handle_poke_event 改从提示词文件读取 poke_reminder")
    else:
        bad("handle_poke_event 未引用 poke_reminder")

    print("\n=== 6. 戳一戳确实把聊天上下文喂给 LLM ===")
    if "ctx.get_context(chat_id)" in src:
        ok("msg_history 使用 ctx.get_context(chat_id)（会话上下文）")
    else:
        bad("msg_history 未使用会话上下文")
    # ★ 2026-09-30：自己上次的回应**不再拼进 msg_history**（那等于给模型一份句式模仿样本，
    #   是首字「诶」占 18.3% 的成因之一），改为 extra_info 清单；上下文只喂最近 2 条。
    if "msg_history=_poke_context_for_llm(chat_id)" in src:
        ok("msg_history 走 _poke_context_for_llm（只喂最近几条）")
    else:
        bad("msg_history 未走裁剪函数")
    if "_POKE_CTX_TAIL" in src and "_poke_history(" not in src:
        ok("裁剪参数存在，且旧的 _poke_history（拼进对话历史）已彻底移除")
    else:
        bad("裁剪参数缺失，或 _poke_history 仍残留")
    if "_poke_said_note(chat_id)" in src and "extra_parts.append(_said_note)" in src:
        ok("自己上次的回应改走 extra_info 清单（防复读保留、不当模仿样本）")
    else:
        bad("未找到 _poke_said_note 的注入")

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
