"""XML / JSON 富卡片消息发送测试（发到管理员私聊）

QQ/NapCat 支持的富卡片消息段：
    {"type": "xml",  "data": {"data": "<xml 字符串>"}}
    {"type": "json", "data": {"data": "<json 字符串>"}}
本脚本逐个尝试常见卡片形态，打印每个的发送结果，便于判断哪些在当前 QQ 版本可用。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.sender import init_sender, get_ws_manager, send_raw_user  # noqa: E402
from core.config import get_config  # noqa: E402


# ── 1. XML：图文卡片（news 型，最通用的分享卡）──
XML_NEWS = (
    "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
    '<msg serviceID="1" templateID="1" action="web" brief="[分享] 幻梦测试卡片" '
    'url="https://github.com/Trusler258/huanmeng-qqbot" flag="0" adverSign="0" multiMsgFlag="0">'
    '<item layout="2">'
    '<picture cover="https://p.qpic.cn/qqconnect/0/logo.png"/>'
    '<title>幻梦 · XML 图文卡片测试</title>'
    '<summary>这是一条 XML 富卡片消息，由 bot 通过 NapCat 直接发送</summary>'
    '</item>'
    '<source name="幻梦 Bot" icon="https://p.qpic.cn/qqconnect/0/logo.png" '
    'url="https://github.com/Trusler258/huanmeng-qqbot" action="web" appid="100497308"/>'
    '</msg>'
)

# ── 2. XML：QQ 音乐分享卡 ──
XML_MUSIC = (
    "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
    '<msg serviceID="2" templateID="1" action="web" brief="[分享] 音乐" '
    'url="https://y.qq.com/n/ryqq/songDetail/0039MnYb0qxYhV" flag="0" adverSign="0" multiMsgFlag="0">'
    '<item layout="2">'
    '<audio cover="https://y.gtimg.cn/music/photo_new/T002R300x300M000004X9WKj2JEd6K.jpg" '
    'src="" duration="0"/>'
    '<title>测试曲目 · XML 音乐卡</title>'
    '<summary>幻梦 Bot</summary>'
    '</item>'
    '<source name="QQ音乐" icon="https://url.cn/5oVZ3vj" '
    'url="https://y.qq.com" action="web" appid="100497308"/>'
    '</msg>'
)

# ── 3. JSON：小程序卡片（miniapp 型）──
JSON_MINIAPP = json.dumps({
    "app": "com.tencent.miniapp_01",
    "view": "view_1",
    "ver": "0.0.0.1",
    "prompt": "[应用] 幻梦 Bot",
    "config": {"type": "normal", "autoSize": 0},
    "meta": {"detail_1": {
        "title": "幻梦 · JSON 小程序卡片",
        "desc": "这是 JSON 富卡片消息，走 NapCat send_private_msg",
        "icon": "https://p.qpic.cn/qqconnect/0/logo.png",
        "url": "https://github.com/Trusler258/huanmeng-qqbot",
        "appid": "1109937557",
        "scene": 1035,
    }},
}, ensure_ascii=False)

# ── 4. JSON：ark 卡片（news 型）──
JSON_ARK = json.dumps({
    "app": "com.tencent.structmsg",
    "desc": "幻梦 Bot 测试",
    "view": "news",
    "ver": "0.0.0.1",
    "prompt": "[分享] JSON Ark 卡片",
    "meta": {"news": {
        "action": "",
        "android_pkg_name": "",
        "app_type": 1,
        "appid": 1109937557,
        "ctime": 1700000000,
        "desc": "这是 JSON Ark 卡片消息",
        "jumpUrl": "https://github.com/Trusler258/huanmeng-qqbot",
        "preview": "预览文字",
        "tag": "幻梦",
        "title": "幻梦 · JSON Ark 图文卡",
    }},
}, ensure_ascii=False)

# ── 5. JSON：B站小程序（常见三方案例）──
JSON_BILI = json.dumps({
    "app": "com.tencent.miniapp_01",
    "view": "view_1",
    "ver": "0.0.0.1",
    "prompt": "[应用]哔哩哔哩",
    "config": {"type": "normal", "autoSize": 1},
    "meta": {"detail_1": {
        "title": "哔哩哔哩",
        "desc": "幻梦 · JSON 卡片（B站样式）",
        "icon": "https://p.qpic.cn/qqconnect/0/logo.png",
        "url": "https://b23.tv/BV1xx411c7mD",
        "appid": "1109937557",
        "scene": 1035,
    }},
}, ensure_ascii=False)


CASES = [
    ("XML 图文卡(news)", {"type": "xml", "data": {"data": XML_NEWS}}),
    ("XML 音乐卡(QQ音乐)", {"type": "xml", "data": {"data": XML_MUSIC}}),
    ("JSON 小程序卡", {"type": "json", "data": {"data": JSON_MINIAPP}}),
    ("JSON Ark(news)", {"type": "json", "data": {"data": JSON_ARK}}),
    ("JSON B站样式", {"type": "json", "data": {"data": JSON_BILI}}),
]


async def main():
    cfg = get_config()
    target = cfg.admin_qq
    init_sender("127.0.0.1", 8099)
    print(f"目标私聊: {target}\n")

    # WS 原始响应（send_raw_user 只回 bool，这里直接看 NapCat 的返回体）
    mgr = get_ws_manager()
    for name, seg in CASES:
        # 直接走 call_api 拿原始返回，便于看报错原因
        try:
            resp = await mgr.call_api(
                "send_private_msg",
                {"user_id": target, "message": [seg]},
                timeout=8.0,
            )
            status = resp.get("status") if isinstance(resp, dict) else resp
            retcode = resp.get("retcode") if isinstance(resp, dict) else "?"
            print(f"[{name}] status={status} retcode={retcode}")
            if isinstance(resp, dict) and resp.get("status") != "ok":
                print(f"        返回: {json.dumps(resp, ensure_ascii=False)[:300]}")
        except Exception as e:
            print(f"[{name}] 异常: {type(e).__name__}: {e}")
        await asyncio.sleep(1.5)

    from services.sender import close_sender
    await close_sender()


asyncio.run(main())
