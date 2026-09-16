"""XML 卡片发送方式排查（NTQQ 3.2.25 对 XML 有白名单限制）

逐个尝试不同的构造/发送方式，找出哪种能在当前 QQ 版本发出 XML 卡。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.sender import init_sender, get_ws_manager  # noqa: E402
from core.config import get_config  # noqa: E402

MIN_XML = (
    "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
    '<msg serviceID="1" templateID="1" action="web" brief="[分享] 测试" '
    'url="https://github.com/Trusler258/huanmeng-qqbot" flag="0" adverSign="0" multiMsgFlag="0">'
    '<item layout="2"><title>极简 XML 测试</title><summary>仅最小字段</summary></item>'
    '<source name="幻梦" icon="" url="" action="web" appid="100497308"/>'
    '</msg>'
)

VARIANTS = [
    # 1) 标准消息段（之前失败的那种）
    ("段形式 xml", "send_private_msg",
     {"message": [{"type": "xml", "data": {"data": MIN_XML}}]}),
    # 2) 带 id 字段（部分 NapCat 版本需要）
    ("段形式 xml + id", "send_private_msg",
     {"message": [{"type": "xml", "data": {"data": MIN_XML, "id": "1"}}]}),
    # 3) CQ 码字符串形式
    ("CQ 码 xml", "send_private_msg",
     {"message": f"[CQ:xml,data={MIN_XML}]"}),
    # 4) 消息字符串里直接放 XML（部分实现会当作 xml 段）
    ("裸 XML 字符串", "send_private_msg", {"message": MIN_XML}),
    # 5) 转义为 HTML 实体再传（有些实现要求）
    ("段形式 xml(转义)", "send_private_msg",
     {"message": [{"type": "xml", "data": {"data": MIN_XML.replace("'", "&#39;")}}]}),
]


async def main():
    cfg = get_config()
    target = cfg.admin_qq
    init_sender("127.0.0.1", 8099)
    mgr = get_ws_manager()
    print(f"目标: {target}\n")

    for name, action, params in VARIANTS:
        p = dict(params)
        p["user_id"] = target
        try:
            resp = await mgr.call_api(action, p, timeout=8.0)
            if isinstance(resp, dict) and resp.get("message_id"):
                print(f"[{name:18s}] OK  message_id={resp['message_id']}")
            else:
                print(f"[{name:18s}] 失败  {json.dumps(resp, ensure_ascii=False)[:220]}")
        except Exception as e:
            print(f"[{name:18s}] 异常  {type(e).__name__}: {e}")
        await asyncio.sleep(1.2)

    # 顺带问一下 NapCat 版本
    try:
        v = await mgr.call_api("get_version_info", {}, timeout=8.0)
        print(f"\nNapCat 版本: {json.dumps(v, ensure_ascii=False)[:300]}")
    except Exception as e:
        print(f"\n版本查询失败: {e}")

    from services.sender import close_sender
    await close_sender()


asyncio.run(main())
