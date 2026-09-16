"""定位缓存不命中的元凶：对比连续两次构造的 messages/tools

线索：
  · 命中量恒为 ~6,147（输入从 15.7K 涨到 17.9K 都不变）→ 只有 system 命中，history 全失效
  · 同一时刻 (空) 调用命中 98.9%，reply_tools 只 38.2% → 差异在 tools 参数
"""
import hashlib
import json
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, '/root/bot')

from core.config import get_config                    # noqa: E402
from services.llm import _build_messages, _build_system_text  # noqa: E402
from core.tools import get_tool_schemas  # noqa: E402
from core.context_manager import get_context_mgr      # noqa: E402

cfg = get_config()


def h(o) -> str:
    return hashlib.md5(json.dumps(o, ensure_ascii=False, sort_keys=True,
                                  default=str).encode()).hexdigest()[:12]


def hraw(s) -> str:
    return hashlib.md5(str(s).encode()).hexdigest()[:12]


print("=" * 70)
print("1) tools 定义是否稳定（连续取 3 次）")
print("=" * 70)
ts = [get_tool_schemas() for _ in range(3)]
for i, t in enumerate(ts, 1):
    names = [x.get("function", {}).get("name") for x in t]
    print(f"  第{i}次: {len(t):2d} 个工具 整体hash={h(t)}  名字序hash={hraw(','.join(names))}")
print(f"  三次整体一致: {h(ts[0]) == h(ts[1]) == h(ts[2])}")
if h(ts[0]) != h(ts[1]):
    n0 = [x["function"]["name"] for x in ts[0]]
    n1 = [x["function"]["name"] for x in ts[1]]
    print(f"  !! 顺序不一致")
    print(f"     第1次: {n0}")
    print(f"     第2次: {n1}")

print()
print("=" * 70)
print("2) 真实历史：连续两次构造 messages，逐段比对")
print("=" * 70)
mgr = get_context_mgr()
# 取有历史的群
best = None
for cid in list(mgr.group_context.keys()):
    n = len(mgr.get_context(cid))
    if best is None or n > best[1]:
        best = (cid, n)
print(f"  历史最长的对话: chat={best[0]}  共 {best[1]} 条")

chat_id = best[0]
hist = mgr.get_context(chat_id)
print(f"  历史前 3 条:")
for ln in hist[:3]:
    print(f"    {hraw(ln)}  {ln[:90]!r}")
print(f"  历史末 2 条:")
for ln in hist[-2:]:
    print(f"    {hraw(ln)}  {ln[:90]!r}")

# 模拟连续两条消息，看两轮之间哪一段 hash 变了
m1 = _build_messages(hist, "测试者", "第一条消息", cfg.bot_name,
                     cfg.system_prompt, True, "记忆A")
m2 = _build_messages(hist + ["测试者: 第一条消息"], "测试者", "第二条消息",
                     cfg.bot_name, cfg.system_prompt, True, "记忆B")

print(f"\n  第1轮 messages: {len(m1)} 条   第2轮: {len(m2)} 条")
print(f"  {'段':>4s} {'role':10s} {'第1轮hash':>13s} {'第2轮hash':>13s}  {'一致':>4s}  len")
same_until = None
for i in range(min(len(m1), len(m2))):
    a, b = m1[i], m2[i]
    ha, hb = hraw(a.get("content")), hraw(b.get("content"))
    ok = "OK" if (ha == hb and a.get("role") == b.get("role")) else "!!"
    if ok == "!!" and same_until is None:
        same_until = i
    la = len(str(a.get("content") or ""))
    print(f"  {i:4d} {a.get('role'):10s} {ha:>13s} {hb:>13s}  {ok:>4s}  {la}")
print(f"\n  前 {same_until if same_until is not None else '全部'} 段一致"
      f"，从第 {same_until} 段开始不同")
print(f"  → 命中量 ≈ 前 {same_until} 段的 token 数")

# 各段 token 估算
def tc(s):
    s = str(s or "")
    cn = sum(1 for c in s if '\u4e00' <= c <= '\u9fff')
    return int(cn * 1.5 + (len(s) - cn) * 0.3)

tot = 0
for i in range(min(len(m1), len(m2))):
    la = tc(m1[i].get("content"))
    tot += la
    if same_until is not None and i == same_until:
        print(f"  到第 {i} 段累计 ≈ {tot} token（应接近实测命中量 6,147）")
        break
