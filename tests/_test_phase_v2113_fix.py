# -*- coding: utf-8 -*-
"""v2.1.13 思考误触发 + 图片时序幻觉 回归测试
运行: python tests/_test_phase_v233_fix.py
覆盖:
  1. deep 意图判定 — 闲聊(干啥/干嘛)不再误开 thinking；知识提问照常触发
  2. 图片异步识别注入格式 — 带 [历史图片描述](HH:MM) 时间戳 + 发送者
  3. 00_core.md 图片规则 — 含新标记 + 时间戳语义说明
"""
import asyncio, sys, os, re

for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

from services.llm import _detect_skill_needs

# ── 1. deep 意图判定 ──
CASES = [
    ("你在干啥", False, "闲聊→不 deep"),
    ("你在干嘛呢", False, "闲聊→不 deep"),
    ("咋了", False, "闲聊→不 deep"),
    ("什么是TCP三次握手", True, "知识→deep"),
    ("讲讲量子纠缠原理", True, "知识→deep"),
    ("详细解释一下堆和栈的区别", True, "对比知识→deep"),
    ("帮我算下积分", False, "工具→tools 非 deep"),
    ("今天天气咋样", False, "闲聊→不 deep"),
]
ok = True
for msg, want_deep, desc in CASES:
    needs = _detect_skill_needs(msg, True)
    got = "deep" in needs
    if got != want_deep:
        ok = False
        print(f"[FAIL] {msg!r} → deep={got} 期望 {want_deep} ({desc}) needs={sorted(needs)}")
    else:
        print(f"[OK]   {msg!r} → deep={got} ({desc})")
assert ok, "deep 意图判定有失败"
print("[1] deep 意图判定 OK")

# ── 2. 图片异步识别注入格式（dispatcher 源码检查）──
src = open(os.path.join(sys.path[0] if sys.path[0] != r"G:\py\qqbot" else r"G:\py\qqbot", "core", "dispatcher.py"), encoding="utf-8").read() if sys.path[0] == r"G:\py\qqbot" else open(os.path.join([p for p in sys.path if os.path.isdir(os.path.join(p, "core"))][0], "core", "dispatcher.py"), encoding="utf-8").read()
# 放宽：从实际文件读
_disp_path = None
for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    cand = os.path.join(p, "core", "dispatcher.py")
    if os.path.isfile(cand):
        _disp_path = cand
        break
src = open(_disp_path, encoding="utf-8").read()

assert "[历史图片描述]" in src, "dispatcher 应注入 [历史图片描述] 标记"
assert "发送者:" in src, "应带发送者"
assert "_sent_ts" in src or "strftime" in src, "应带时间戳"
# 确认死代码已清理（不再有重复的 ensure_future 双 return）
assert src.count("asyncio.ensure_future(_bg_recognize())") == 1, f"死代码未清理: {src.count('asyncio.ensure_future(_bg_recognize())')} 处"
print("[2] dispatcher 图片注入格式 OK:", _disp_path)

# ── 3. 图片时序规则（v2.1.14 精简重写后从 00_core.md 移到了 10_format_group.md）──
#    断言方式改为：在 skills 目录里搜索，规则在哪个体现在都算通过（避免改文件位置就误报）
_skills_dir = None
for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    cand = os.path.join(p, "data", "skills")
    if os.path.isdir(cand):
        _skills_dir = cand
        break
assert _skills_dir, "找不到 data/skills 目录"
_all_skills = ""
for _fn in os.listdir(_skills_dir):
    if _fn.endswith(".md") and not _fn.endswith(".bak") and ".bak_" not in _fn:
        with open(os.path.join(_skills_dir, _fn), encoding="utf-8") as _f:
            _all_skills += _f.read() + "\n"
assert "[历史图片描述]" in _all_skills, "skills 里应提 [历史图片描述] 标记"
assert "刚在看你发的仓鼠" in _all_skills, "skills 里应含仓鼠反例"
print("[3] 图片时序规则 OK（在 data/skills 中找到）:", _skills_dir)

print("\n全部通过: v2.1.13 思考误触发 + 图片时序幻觉 修复")