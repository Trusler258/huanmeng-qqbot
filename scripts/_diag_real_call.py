"""拦截真实 LLM 请求，抓 messages/tools，对比连续两次的差异

方法：monkeypatch services.llm._create_client，返回一个假 client，
      它的 chat.completions.create 记录请求参数后返回假响应。
      这样能拿到**真实调用**的完整 messages，不动生产代码。
"""
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, '/root/bot')

import services.llm as L                      # noqa: E402
from core.config import get_config            # noqa: E402

cfg = get_config()
CAPTURED = []


def _h(o):
    return hashlib.md5(json.dumps(o, ensure_ascii=False, sort_keys=True,
                                  default=str).encode()).hexdigest()[:10]


def _tc(s):
    """token 粗估"""
    s = str(s or "")
    cn = sum(1 for c in s if '\u4e00' <= c <= '\u9fff')
    return int(cn * 0.9 + (len(s) - cn) * 0.3)


class _FakeCompletions:
    def create(self, **kw):
        msgs = kw.get("messages") or []
        tools = kw.get("tools")
        seg = [_h(m.get("content")) for m in msgs]
        CAPTURED.append({
            "model": kw.get("model"),
            "n_seg": len(msgs),
            "seg": seg,
            "content_lens": [len(str(m.get("content") or "")) for m in msgs],
            "tok_est": [_tc(m.get("content")) for m in msgs],
            "has_tools": tools is not None,
            "tools_hash": _h(tools) if tools else None,
            "n_tools": len(tools) if tools else 0,
        })
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"replies":["收到喵"],"fav":0,"calls":[]}',
                                        reasoning_content=None, tool_calls=None),
                finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0,
                                  prompt_tokens_details=None),
        )


class _FakeClient:
    def __init__(self, *a, **k):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


L._create_client = _FakeClient


async def main():
    from core.pipeline import process_message
    from core.context_manager import get_context_mgr
    import core.context_manager as CM

    mgr = get_context_mgr()
    # 找一个有历史的群
    chat = max(mgr.group_context.keys(), key=lambda c: len(mgr.get_context(c)))
    print(f"用群 {chat}（历史 {len(mgr.get_context(chat))} 条）连续发 3 条消息\n")

    for i, msg in enumerate(["第一条测试消息", "第二条测试消息", "第三条测试消息"], 1):
        CAPTURED.clear()
        try:
            await process_message(
                msg_type="text", msg_content=msg, chat_id=chat,
                sender_name=cfg.get_display_name(cfg.admin_qq) or "测试者",
                user_id=cfg.admin_qq, is_group=True, bot_qq=cfg.bot_qq)
        except Exception as e:
            print(f"  [第{i}次] process_message 异常: {type(e).__name__}: {e}")
        # 只看带 tools 的（主回复 FC 调用）
        fcs = [c for c in CAPTURED if c["has_tools"]]
        others = [c for c in CAPTURED if not c["has_tools"]]
        print(f"[第{i}次] 共 {len(CAPTURED)} 次 LLM 调用"
              f"（带tools {len(fcs)} / 不带 {len(others)}）")
        for c in CAPTURED:
            tag = f"tools×{c['n_tools']}" if c["has_tools"] else "无tools"
            # 末段是动态内容（当前消息），单独标出
            print(f"    {tag:10s} 段数={c['n_seg']:5d} "
                  f"估算token={sum(c['tok_est']):6d} "
                  f"toolshash={c['tools_hash']}")
        # 记录最后一次带 tools 的完整段 hash 序列
        if fcs:
            globals().setdefault("LAST_FC", {})[i] = fcs[-1]

    # 对比两次 FC 调用
    fc = globals().get("LAST_FC") or {}
    if len(fc) >= 2:
        ks = sorted(fc)
        a, b = fc[ks[0]], fc[ks[1]]
        print(f"\n=== 对比第{ks[0]}次 vs 第{ks[1]}次 的 FC 调用 ===")
        print(f"  段数: {a['n_seg']} → {b['n_seg']}")
        print(f"  tools hash: {a['tools_hash']} → {b['tools_hash']}"
              f"  {'一致' if a['tools_hash'] == b['tools_hash'] else '!! 不一致'}")
        diff = -1
        for i in range(min(a["n_seg"], b["n_seg"])):
            if a["seg"][i] != b["seg"][i]:
                diff = i
                break
        print(f"  首个不同的段: {diff}")
        if diff >= 0:
            keep = sum(a["tok_est"][:diff])
            print(f"  前 {diff} 段可复用 ≈ {keep} token"
                  f"（占第1次 {keep / max(1, sum(a['tok_est'])) * 100:.1f}%）")
            print(f"  第 {diff} 段内容长度: "
                  f"第1次={a['content_lens'][diff]}  第2次={b['content_lens'][diff]}")


asyncio.run(main())
