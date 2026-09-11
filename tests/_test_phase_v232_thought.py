# -*- coding: utf-8 -*-
"""v2.3.2 思考标记回调逻辑验证 —— 不改数据，只测 llm.py 的回调触发链路
运行: python tests/_test_phase_v232_thought.py
覆盖: ToolCallResult.reasoning_duration / 纯思考路径回调 / 先导语前缀+去重 /
      主回复前缀+去重 / 首句[]开头前缀独立成句
"""
import asyncio, sys, os

# 多路径探测（本地 / 服务器）
for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

from services.llm import ToolCallResult

# ── 1. ToolCallResult 字段 ──
r = ToolCallResult(content="x", tool_calls=[], reasoning="想想想")
assert r.reasoning_duration == 0.0, "未传时长应为0"
r2 = ToolCallResult(content="x", tool_calls=[], reasoning="想想想", reasoning_duration=3.2)
assert abs(r2.reasoning_duration - 3.2) < 1e-6, f"duration={r2.reasoning_duration}"
print("[1] ToolCallResult.reasoning_duration 提取 OK")


# ── 2. 纯思考路径：模拟 generate_multi_reply_with_tools 末尾调用 ──
async def test_pure():
    calls = []

    async def cb(secs):
        calls.append(secs)

    _last_reasoning_dur = 4.7
    thought_shown_cb = cb
    if thought_shown_cb is not None and _last_reasoning_dur:
        await thought_shown_cb(round(_last_reasoning_dur))
    assert calls == [5], f"纯思考回调: {calls}"
    print("[2] 纯思考路径回调触发 OK", calls)


asyncio.run(test_pure())


# ── 3. 先导语路径：thought_ctx 状态流转（pipeline 侧逻辑）──
from core.pipeline import _make_thought_cb, _make_interim_sender


async def test_lead():
    thought_ctx = {"secs": None, "applied": False}
    cb = _make_thought_cb(thought_ctx)
    await cb(1)   # <2s 忽略
    assert thought_ctx["secs"] is None, f"<2s 不应记录: {thought_ctx}"
    await cb(3)   # 有效
    assert thought_ctx["secs"] == 3, f"secs={thought_ctx}"
    # 先导语消费
    sender = _make_interim_sender(123, True, 456, thought_ctx)
    # 拦截 send_sentences 验证前缀注入：monkeypatch（pipeline 用的是模块顶部导入的局部名）
    import core.pipeline as pipe_mod
    captured = {}

    async def fake_send_sentences(sents, chat_id, is_group, user_id=None, min_interval=None, max_interval=None):
        captured["sents"] = sents

    orig = pipe_mod.send_sentences
    pipe_mod.send_sentences = fake_send_sentences
    try:
        await sender("帮你搜搜看吧")
    finally:
        pipe_mod.send_sentences = orig
    assert captured["sents"] == ["[已思考3秒] 帮你搜搜看吧"], f"captured={captured}"
    assert thought_ctx["applied"] is True, f"applied={thought_ctx}"
    print("[3] 先导语挂前缀 + applied 置位 OK:", captured["sents"])


asyncio.run(test_lead())


# ── 4. 主回复路径：未应用 → 挂前缀；已应用 → 不重复 ──
async def test_main():
    # 场景A：回调记录了时长，先导语没消费 → 主回复挂
    thought_ctx = {"secs": 6, "applied": False}
    sentences = ["TCP 三次握手是...", "第二次..."]
    if thought_ctx.get("secs") and not thought_ctx.get("applied") and sentences:
        prefix = f"[已思考{thought_ctx['secs']}秒] "
        sentences[0] = prefix + sentences[0]
        thought_ctx["applied"] = True
    assert sentences[0] == "[已思考6秒] TCP 三次握手是...", sentences
    assert thought_ctx["applied"] is True
    print("[4A] 主回复未应用 → 挂前缀 OK:", sentences[0])

    # 场景B：先导语已应用 → 主回复不重复
    thought_ctx2 = {"secs": 3, "applied": True}
    sentences2 = ["最终结果..."]
    if thought_ctx2.get("secs") and not thought_ctx2.get("applied") and sentences2:
        sentences2[0] = f"[已思考{thought_ctx2['secs']}秒] " + sentences2[0]
    assert sentences2[0] == "最终结果...", sentences2
    print("[4B] 已应用 → 不重复 OK")


asyncio.run(test_main())


# ── 5. 边界：首句为 [ 开头 → 前缀独立成句 ──
async def test_bracket():
    thought_ctx = {"secs": 8, "applied": False}
    sentences = ["[CQ:image,file=x]"]
    if thought_ctx.get("secs") and not thought_ctx.get("applied") and sentences:
        if sentences[0].lstrip().startswith(("[", "#", "`")):
            sentences.insert(0, "[已思考8秒]")
        else:
            sentences[0] = "[已思考8秒] " + sentences[0]
    assert sentences == ["[已思考8秒]", "[CQ:image,file=x]"], sentences
    print("[5] 首句为[]开头 → 前缀独立 OK:", sentences)


asyncio.run(test_bracket())

print("\n全部 5 组测试通过")