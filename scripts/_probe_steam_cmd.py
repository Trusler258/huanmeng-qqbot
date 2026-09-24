#!/usr/bin/env python3
"""/~steam 指令功能验证（服务器上跑，不发任何消息）"""
import asyncio
import sys

sys.path.insert(0, "/root/bot")

from modules import steam as M          # noqa: E402
from services import steam_api as S     # noqa: E402
from services import steam_card as SC   # noqa: E402


async def main():
    print("=" * 66)
    print("[1] 帮助文本")
    print(M.HELP)

    print("")
    print("[2] price（完整版：价格 + 史低 + 地区对比）")
    print(await M._do_price("双人成行", short=False))

    print("")
    print("[3] px（快捷版）")
    print(await M._do_price("Hades", short=True))

    print("")
    print("[4] price 直接给商店链接")
    print(await M._do_price("https://store.steampowered.com/app/977950/A_Dance_of_Fire_and_Ice/",
                            short=True))

    print("")
    print("[5] 绑定解析（不落盘）")
    for t in ("76561199427581023",
              "https://steamcommunity.com/profiles/76561199427581023",
              "trusler",
              "随便打的字"):
        sid, err = await S.resolve_steamid(t)
        print("  %-52s -> %s %s" % (t[:50], sid or "(失败)", err))

    print("")
    print("[6] 卡片构建")
    html = await SC.build_profile_html("76561199427581023")
    left = ("__CARD_DATA__" in html) or ("__FONT_B64__" in html)
    print("  HTML = %d KB | 占位符残留 = %s" % (len(html) // 1024, left))
    if html:
        with open("/tmp/_steam_cmd_card.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("  已写到 /tmp/_steam_cmd_card.html（可交给 Playwright 截图验收）")

    print("")
    print("[7] 绑定读写（用测试 QQ，验完删除）")
    S.set_bind(10000, "76561199427581023")
    print("  set 后读回 =", S.get_bind(10000))
    print("  解绑 =", S.del_bind(10000), "| 再读 =", repr(S.get_bind(10000)))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
