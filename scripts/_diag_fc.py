"""直接调用 FC 主函数两次，对比真实 messages/tools 差异"""
import asyncio
import hashlib
import json
import sys
from types import SimpleNamespace

sys.path.insert(0, '/root/bot')

import services.llm as L                       # noqa: E402
from core.config import get_config             # noqa: E402
from core.context_manager import get_context_mgr  # noqa: E402

cfg = get_config()
CAP = []


def _h(o):
    return hashlib.md5(json.dumps(o, ensure_ascii=False, sort_keys=True,
                                  default=str).encode()).hexdigest()[:10]


def _tc(s):
    s = str(s or "")
    cn = sum(1 for c in s if '\u4e00' <= c <= '\u9fff')
    return int(cn * 0.9 + (len(s) - cn) * 0.3)


class _FC:
    def create(self, **kw):
        msgs = kw.get("messages") or []
        tools = kw.get("tools")
        CAP.append({
            "n": len(msgs),
            "seg": [_h(m.get("content")) for m in msgs],
            "lens": [len(str(m.get("content") or "")) for m in msgs],
            "tok": [_tc(m.get("content")) for m in msgs],
            "roles": [m.get("role") for m in msgs],
            "has_tools": tools is not None,
            "tools_h": _h(tools) if tools else None,
            "n_tools": len(tools) if tools else 0,
            "tools_json_len": len(json.dumps(tools, ensure_ascii=False)) if tools else 0,
        })
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"replies":["好的喵"],"fav":0,"calls":[]}',
                                        reasoning_content=None, tool_calls=None),
                finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0,
                                  prompt_tokens_details=None))


class _Client:
    def __init__(self, *a, **k):
        self.chat = SimpleNamespace(completions=_FC())


L._create_client = _Client


async def one(msg: str, hist: list, tag: str):
    CAP.clear()
    try:
        await L.generate_multi_reply_with_tools(
            msg_history=hist, speaker_name="测试者", current_msg=msg,
            bot_name=cfg.bot_name, system_prompt=cfg.system_prompt,
            reply_model=cfg.reply_model, is_group=True,
            extra_info="【测试记忆】blah", max_tokens=None,
            user_id=cfg.admin_qq, group_id=247478659, bot_qq=cfg.bot_qq,
            thinking=False,
        )
    except Exception as e:
        print(f"  [{tag}] 异常: {type(e).__name__}: {e}")
    fcs = [c for c in CAP if c["has_tools"]]
    print(f"[{tag}] 共 {len(CAP)} 次调用（带tools {len(fcs)}）")
    for c in CAP:
        t = f"tools×{c['n_tools']}({c['tools_json_len']}字符)" if c["has_tools"] else "无tools"
        print(f"    {t:28s} 段数={c['n']:5d} 估算token={sum(c['tok']):6d} tools_h={c['tools_h']}")
    return fcs


async def main():
    mgr = get_context_mgr()
    chat = max(mgr.group_context.keys(), key=lambda c: len(mgr.get_context(c)))
    hist = mgr.get_context(chat)
    print(f"群 {chat}，历史 {len(hist)} 条\n")

    a = await one("第一条：幻梦你在吗", hist, "第1次")
    b = await one("第二条：幻梦你在吗", hist, "第2次")

    A = a[-1] if a else None
    B = b[-1] if b else None
    if not (A and B):
        print("\n未捕获到带 tools 的调用")
        return

    print("\n=== 对比两次 FC 调用 ===")
    print(f"  段数: {A['n']} → {B['n']}")
    print(f"  tools: {A['n_tools']} vs {B['n_tools']} 个, "
          f"hash {A['tools_h']} vs {B['tools_h']} "
          f"{'一致' if A['tools_h'] == B['tools_h'] else '!! 不一致'}")
    diff = -1
    for i in range(min(A["n"], B["n"])):
        if A["seg"][i] != B["seg"][i]:
            diff = i
            break
    print(f"  首个不同段: {diff}")
    if diff >= 0:
        print(f"  前 {diff} 段 ≈ {sum(A['tok'][:diff])} token 可复用"
              f"（占 {sum(A['tok'][:diff]) / max(1, sum(A['tok'])) * 100:.1f}%）")
        for j in range(max(0, diff - 1), min(A["n"], diff + 2)):
            print(f"    段{j} role={A['roles'][j]:9s} "
                  f"len {A['lens'][j]} vs {B['lens'][j]}  "
                  f"tok {A['tok'][j]} vs {B['tok'][j]}")
        # 展示首个不同段的内容开头
        print(f"\n  段{diff} 前 200 字符（第1次）：")
        print(f"    (见下方日志)")


asyncio.run(main())
