# -*- coding: utf-8 -*-
"""v2.1.15 代码层回归测试 —— 空消息 / 私聊图片 / 转发重试 / 防复读

运行: python tests/_test_phase_v2115_core.py
覆盖:
  1. 空消息拦截（pipeline 源码断言 + 逻辑等价验证）
  2. 私聊图片强制同步识别（dispatcher 源码断言）
  3. 合并转发 id/message_id 双参数重试（dispatcher 源码断言）
  4. 防复读提取器 _recent_bot_snippets（扁平/多轮两形态 + 去重 + 不能误取他人发言）
  5. _build_reminder 占位符免疫（未赋值清空 + 空行删除）
"""
import os
import sys

for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

# 定位项目根（用于读源码做断言）
ROOT = None
for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if os.path.isfile(os.path.join(p, "core", "pipeline.py")):
        ROOT = p
        break
assert ROOT, "找不到项目根目录"


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── 1. 空消息拦截 ──
pipe = read("core/pipeline.py")
assert "空消息（无有效内容），已跳过" in pipe, "pipeline 缺少空消息拦截"
# 拦截必须位于回复判断之前，否则照样会走到 LLM
_i_guard = pipe.index("空消息（无有效内容），已跳过")
_i_judge = pipe.index("------回复判断------")
assert _i_guard < _i_judge, "空消息拦截必须在回复判断之前"
assert "and not (msg_content or \"\").strip()" in pipe, "空消息判定条件缺失"
print("[1] 空消息拦截 OK（位于回复判断之前）")

# ── 2. 私聊图片同步识别 ──
disp = read("core/dispatcher.py")
assert "is_img_mentioned = (not is_group) or bool(" in disp, "私聊图片未强制同步识别"
print("[2] 私聊图片强制同步识别 OK")

# ── 3. 合并转发双参数重试 ──
assert '"message_id": forward_id' in disp, "转发缺少 message_id 重试"
assert '"id": forward_id' in disp, "转发 id 参数缺失"
print("[3] 合并转发 id/message_id 双参数重试 OK")

# ── 4. 防复读提取器 ──
from services.llm import _recent_bot_snippets

# 4a 扁平形态：只能取 bot 自己的话
hist = [
    "[群友] A[fav=50]: 你手上有薯片味哦",
    "幻梦: 你手上是不是有薯片味儿…刚偷吃了什么",
    "[群友] A[fav=50]: 嗯",
    "幻梦: 薯片味的小鱼干？这什么神奇口味诶",
]
r = _recent_bot_snippets(hist, "幻梦")
assert r.startswith("你最近说过"), r
assert "[群友]" not in r, f"误把他人发言当成 bot 自己的话: {r}"
assert "薯片" in r
# 最近的排最前
assert r.index("小鱼干") < r.index("刚偷吃了什么"), r
print(f"[4a] 扁平形态 OK → {r[:60]}...")

# 4b 多轮消息形态 + 去重
turns = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "喵~在的"},
    {"role": "user", "content": "x"},
    {"role": "assistant", "content": "喵~在的"},
]
r2 = _recent_bot_snippets(turns, "幻梦")
assert r2.count("喵~在的") == 1, f"未去重: {r2}"
print(f"[4b] 多轮形态+去重 OK → {r2}")

# 4c limit 生效（最多 3 条）
many = [f"幻梦: 第{i}句话" for i in range(10)]
r3 = _recent_bot_snippets(many, "幻梦")
assert r3.count("「") == 3, f"limit 未生效: {r3}"
assert "第9句话" in r3 and "第0句话" not in r3, f"应取最近的: {r3}"
print(f"[4c] limit=3 且取最近 OK → {r3[:50]}...")

# 4d 无历史 → 空串
assert _recent_bot_snippets([], "幻梦") == ""
assert _recent_bot_snippets(None, "幻梦") == ""
assert _recent_bot_snippets(["群友: 你好"], "幻梦") == "", "无 bot 发言应返回空"
print("[4d] 空历史/无 bot 发言 → 空串 OK")

# ── 5. _build_reminder 占位符免疫 ──
from services.llm import _build_reminder

on = _build_reminder("reply_reminder", ctx_hint="H", max_chars="40", no_repeat=r)
off = _build_reminder("reply_reminder", ctx_hint="H", max_chars="40", no_repeat="")
assert "你最近说过" in on, "有内容时防复读应出现"
assert "你最近说过" not in off, "无内容时防复读整行应消失"
assert "${" not in off, f"不应残留字面量占位符: {off}"
assert "\n\n\n" not in off, "不应出现连续空行"
# 未传 no_repeat（旧调用方）也不应崩、不应残留占位符
noparam = _build_reminder("reply_reminder", ctx_hint="H", max_chars="40")
assert "${no_repeat}" not in noparam, "未传参时不应残留占位符"
print(f"[5] _build_reminder 占位符免疫 OK（开={len(on)}字 / 关={len(off)}字 / 缺参={len(noparam)}字）")

print("\n全部 5 组通过: v2.1.15 代码层修复")
