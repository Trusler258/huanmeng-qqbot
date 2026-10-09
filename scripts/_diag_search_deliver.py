"""
自测：搜索兜底 + "先找后写"文件交付（v2.3.81c）

用途：不经过 QQ 群，直接在服务器上验这两条链路是否真的通。
    1) 搜索兜底——DeepSeek 原生搜索没真联网时必须回退 AnySearch/百度，
       而不是把"我无法实时联网"当成搜索结果
    2) 先找后写——"整理/汇总成文档发出来"要真的调 write_code 生成文件

用法（在 /root/bot 目录下执行）：
    python3 scripts/_diag_search_deliver.py            # 全跑
    python3 scripts/_diag_search_deliver.py search     # 只测搜索兜底
    python3 scripts/_diag_search_deliver.py fc         # 只测先找后写

安全：send_file 被替换成假实现，**不会真的往 QQ 发文件**；
      user_id/chat_id 传 0，不会写进任何群的记忆。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / "config" / ".env")

_FAKE_FILES: list[str] = []


def _install_fake_sender() -> None:
    """把 send_file 换成只记录路径的假实现，避免自测时真的发文件到 QQ。"""
    import services.sender as sender

    async def fake_send_file(fp, chat_id, is_group):
        _FAKE_FILES.append(str(fp))
        return True

    sender.send_file = fake_send_file


async def test_search() -> bool:
    """搜索兜底：必须拿到真实长文本，不能是拒答。"""
    from modules.search import perform_search
    from modules.web_search import is_no_search_text

    queries = ["今日要闻 2026年10月9日", "国际新闻 2026年10月9日"]
    ok = True
    for q in queries:
        t = time.time()
        r = await perform_search(q, user_id=0, chat_id=0, limit=4)
        cost = time.time() - t
        bad = (not r) or is_no_search_text(r)
        print("[搜索] %s" % q)
        print("       耗时 %.1fs | %d 字 | %s" % (cost, len(r or ""), "疑似拒答 FAIL" if bad else "OK"))
        print("       预览: %s" % (r or "(空)")[:160].replace("\n", " | "))
        ok = ok and not bad
    return ok


async def test_fc() -> bool:
    """先找后写：必须真的生成文件（_FAKE_FILES 非空）。"""
    from core.config import get_config
    from services.llm import generate_multi_reply_with_tools

    cfg = get_config()
    cases = ["整理今日的新闻并总结成md文档发送出来"]
    ok = True
    for msg in cases:
        _FAKE_FILES.clear()
        t = time.time()
        out = await generate_multi_reply_with_tools(
            msg_history=["（群聊）自测: 在吗"],
            speaker_name="自测",
            current_msg=msg,
            bot_name=cfg.bot_name,
            system_prompt=cfg.system_prompt,
            reply_model=cfg.reply_model,
            is_group=False,
            user_id=0,
            group_id=0,
            bot_qq=0,
        )
        cost = time.time() - t
        print("[先找后写] %s" % msg)
        print("       耗时 %.1fs | 生成文件 %d 个" % (cost, len(_FAKE_FILES)))
        if _FAKE_FILES:
            for fp in _FAKE_FILES:
                p = Path(fp)
                size = p.stat().st_size if p.exists() else -1
                print("       文件: %s (%d 字节)" % (p.name, size))
                if p.exists():
                    print("       正文预览: %s" % p.read_text(encoding="utf-8")[:160].replace("\n", " | "))
        else:
            print("       FAIL —— 没生成文件（模型只回了聊天文字）")
        print("       回复: %s" % (out[0] or [])[:2])
        ok = ok and bool(_FAKE_FILES)
    return ok


async def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    _install_fake_sender()

    results = []
    if mode in ("all", "search"):
        results.append(("搜索兜底", await test_search()))
    if mode in ("all", "fc"):
        results.append(("先找后写", await test_fc()))

    print("=" * 56)
    for name, ok in results:
        print("%s %s" % ("PASS" if ok else "FAIL", name))
    sys.exit(0 if all(ok for _, ok in results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
