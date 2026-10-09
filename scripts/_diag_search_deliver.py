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
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / "config" / ".env")

_FAKE_FILES: list[str] = []


def _extra_info() -> str:
    """复刻 pipeline 注入的 extra_info 里的时间行。

    ⚠️ 不自测时**必须带上**：生产环境 core/pipeline.py:628 会注入
    「当前时间：2026年10月10日 ... 周六」。不带的话模型不知道今年是哪年，
    会瞎猜年份（实测它把"10月9日的新闻"搜成了 2025 年）。
    """
    from datetime import datetime

    now = datetime.now()
    weekdays = "日一二三四五六"
    return f"当前时间：{now.strftime('%Y年%m月%d日 %H:%M:%S')} 周{weekdays[int(now.strftime('%w'))]}"


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
    cases = [
        "整理今日的新闻并总结成md文档发送出来",
        "整理近期AI新闻，并总结成md文档发出来",
    ]
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
            extra_info=_extra_info(),
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
                if not p.exists():
                    print("       文件: %s (已不存在)" % p.name)
                    continue
                txt = p.read_text(encoding="utf-8")
                bullets = sum(1 for ln in txt.splitlines() if ln.strip()[:2] in ("- ", "· ", "* "))
                print(
                    "       文件: %s | %d 字节 | 来源链接 %d 个 | 条目 %d 条"
                    % (p.name, len(txt.encode("utf-8")), txt.count("http"), bullets)
                )
                print("       正文预览: %s" % txt[:400].replace("\n", " | "))
                if txt.count("http") == 0:
                    print("       ⚠️ 一个来源链接都没有——文档应逐条附来源")
                if bullets < 3:
                    print("       ⚠️ 条目太少（<3）——素材可能没被用尽")
        else:
            print("       FAIL —— 没生成文件（模型只回了聊天文字）")
        print("       回复: %s" % (out[0] or [])[:2])
        ok = ok and bool(_FAKE_FILES)
    return ok


async def test_fc_legacy() -> bool:
    """确定性回归：模型走 legacy 协议时（content 里带 calls，tool_calls=0）仍要交付文件。

    模型有时不用原生 function-calling，而把调用写进 content 的 {"replies":...,"calls":[...]}。
    此前这条路会绕过整个先找后写机制（素材/多轮/兜底全失效），只回一句"我这就整理成md发出来"。
    这里 monkeypatch 把**第 1 轮响应固定成 legacy 格式**，避开"模型随机选哪种协议"的不确定性，
    稳定复现该场景。第 2 轮起走真实 LLM（应产出 write_code）。
    """
    import services.llm as llm
    from core.config import get_config

    cfg = get_config()
    msg = "整理今日的新闻并总结成md文档发出来"
    legacy_json = json.dumps({
        "replies": ["好呀，我去扒一圈新闻～", "搜完整理成 md 文档发你"],
        "fav": 2,
        "calls": [
            {"name": "search_web", "args": "今日要闻 国内新闻"},
            {"name": "search_web", "args": "今日国际新闻"},
        ],
    }, ensure_ascii=False)

    _orig = llm.call_llm_with_tools
    _n = {"i": 0}

    async def _patched(*a, **kw):
        _n["i"] += 1
        if _n["i"] == 1:
            print("       [注入] 第1轮固定返回 legacy 协议（content 带 calls，tool_calls=0，reasoning 为空）")
            return llm.ToolCallResult(legacy_json, [])
        return await _orig(*a, **kw)

    llm.call_llm_with_tools = _patched
    _FAKE_FILES.clear()
    try:
        t = time.time()
        out = await llm.generate_multi_reply_with_tools(
            msg_history=["（群聊）自测: 在吗"],
            speaker_name="自测",
            current_msg=msg,
            bot_name=cfg.bot_name,
            system_prompt=cfg.system_prompt,
            reply_model=cfg.reply_model,
            is_group=False,
            extra_info=_extra_info(),
            user_id=0,
            group_id=0,
            bot_qq=0,
        )
        cost = time.time() - t
    finally:
        llm.call_llm_with_tools = _orig

    print("[先找后写/legacy] %s" % msg)
    print("       耗时 %.1fs | 生成文件 %d 个" % (cost, len(_FAKE_FILES)))
    if _FAKE_FILES:
        for fp in _FAKE_FILES:
            p = Path(fp)
            if p.exists():
                txt = p.read_text(encoding="utf-8")
                print("       文件: %s | %d 字节 | 来源链接 %d 个"
                      % (p.name, len(txt.encode("utf-8")), txt.count("http")))
    else:
        print("       FAIL —— legacy 协议下没生成文件")
    print("       回复: %s" % (out[0] or [])[:2])
    return bool(_FAKE_FILES)


async def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    _install_fake_sender()

    results = []
    if mode in ("all", "search"):
        results.append(("搜索兜底", await test_search()))
    if mode in ("all", "fc"):
        results.append(("先找后写", await test_fc()))
    if mode in ("all", "legacy"):
        results.append(("先找后写(legacy协议)", await test_fc_legacy()))

    print("=" * 56)
    for name, ok in results:
        print("%s %s" % ("PASS" if ok else "FAIL", name))
    sys.exit(0 if all(ok for _, ok in results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
