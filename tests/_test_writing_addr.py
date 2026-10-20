"""写作收尾称谓（v2.3.104）：主人 vs 群友 必须给不同身份提示。

跑法：python tests/_test_writing_addr.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def run_for(user_id: int, is_group: bool = True):
    """跑一次 generate_and_send_file，返回 (followup_prompt, selfcheck)。"""
    import services.llm as L
    import services.sender as S
    import core.context_manager as CM
    import utils.writing as W

    captured = {"prompt": ""}
    real_llm, real_send = L.call_llm, S.send_by_chat_type
    real_ctx = CM.get_context_mgr

    class _Ctx:
        def append_to_context(self, *a, **k):
            pass

    calls = {"n": 0}

    async def fake_llm(model, messages, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return ("测试标题\n\n这是正文第一段。\n这是正文第二段。")
        captured["prompt"] = messages[-1]["content"]
        return "写好啦喵~"

    async def fake_send(*a, **k):
        return True

    L.call_llm = fake_llm
    S.send_by_chat_type = fake_send
    CM.get_context_mgr = lambda: _Ctx()
    try:
        ok = asyncio.run(W.generate_and_send_file(
            "写一封信", ["最近聊了点啥"], "测试用户",
            123456, is_group, user_id))
    finally:
        L.call_llm, S.send_by_chat_type = real_llm, real_send
        CM.get_context_mgr = real_ctx
    return captured["prompt"], ok


def main():
    print("[1] 收尾提示词按身份区分")
    from core.config import get_config
    cfg = get_config()

    p_stranger, ok1 = run_for(10001, True)
    ck("流程跑通", ok1 is True, str(ok1))
    ck("非主人提示含「不是主人」", "不是主人" in p_stranger, p_stranger[-160:])
    ck("非主人提示禁止叫主人", "禁止叫主人" in p_stranger, p_stranger[-160:])

    p_owner, ok2 = run_for(cfg.admin_qq, True)
    ck("主人提示含「是主人」", "是主人" in p_owner, p_owner[-160:])
    ck("主人提示不含禁止语", "禁止叫主人" not in p_owner, p_owner[-160:])

    print("[2] 模板文案已改（不再要求一律叫主人）")
    from utils.writing import _writing_sections
    _, tpl = _writing_sections()
    ck("模板不含『不管对方是谁』", "不管对方是谁" not in tpl, tpl[:120])
    ck("模板含身份分支", "是主人" in tpl and "不是主人" in tpl, tpl[:160])

    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
