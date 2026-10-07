# -*- coding: utf-8 -*-
"""发日榜卡同款格式的服务器异常补位公告（群聊日报替换）
用法: python3 _send_notice_card.py [group_id] [label_date] [downtime]
"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")

GROUP = int(sys.argv[1]) if len(sys.argv) > 1 else 1058782600
LABEL = sys.argv[2] if len(sys.argv) > 2 else ""
DOWN = sys.argv[3] if len(sys.argv) > 3 else ""


async def main():
    from modules.commands import _build_outage_notice_html, _render_html_to_png
    from services.sender import send_group_msg, send_private_msg
    html = _build_outage_notice_html(today=LABEL, downtime=DOWN)
    out = await _render_html_to_png(html, "wdsj_outage")
    if not out:
        print("RENDER-FAIL")
        return
    cq = f"[CQ:image,file=file:///{out}]"
    await (send_group_msg(cq, GROUP) if GROUP else send_private_msg(cq, 3483585417))
    print("sent:", out, os.path.getsize(out), "bytes -> group", GROUP)


asyncio.run(main())
