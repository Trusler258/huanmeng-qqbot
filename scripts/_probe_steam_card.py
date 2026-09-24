# -*- coding: utf-8 -*-
"""Steam 卡片 Pillow 渲染实测（服务器上跑）

对比口径：
  · 耗时：render_card 端到端（含全部 API 拉取）与纯渲染两段分开计时
  · 尺寸/主题：输出 PNG 宽高与是否 ingame
  · 与 HTML 版对照：同时生成 HTML 供人工比对

用法：python3 scripts/_probe_steam_card.py [steamid]
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/bot")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SID = sys.argv[1] if len(sys.argv) > 1 else "76561199427581023"


async def main():
    from services import steam_card as SC

    print("=" * 60)
    t0 = time.perf_counter()
    payload = await SC.build_payload(SID)
    t_fetch = time.perf_counter() - t0
    if not payload:
        print("取数失败（资料不可见？）")
        return 1

    P, S = payload["profile"], payload["stats"]
    print("取数耗时 %.2fs" % t_fetch)
    print("  昵称=%s  状态=%s  游戏中=%r" % (P["name"], P["state_text"], P["game_now"]))
    print("  统计: 游戏=%s 时长=%s 成就=%s/%s 徽章=%s 好友=%s xp=%s/%s(+%s)"
          % (S["games"], S["hours"], S["ach_got"], S["ach_total"],
             S["badges"], S["friends"], S["xp"], S["xp_cur"], S["xp_need"]))
    print("  最近两周 %d 项 | 时长榜 %d 项 | 成就 %d 项"
          % (len(payload["recent"]), len(payload["top"]), len(payload["achievements"])))
    for a in payload["achievements"]:
        print("    - %s (%s)" % (a["name"], a["date"]))

    # 纯渲染耗时
    from services import steam_card_pillow as CP
    t1 = time.perf_counter()
    img = CP.render_steam_card(payload, root=Path("/root/bot"))
    t_render = time.perf_counter() - t1
    out = Path("/tmp/_steam_card_pil.png")
    CP.save_steam_card(img, out)
    print()
    print("纯渲染 %.0f ms  尺寸 %dx%d  输出 %s (%.0f KB)"
          % (t_render * 1000, img.size[0], img.size[1], out,
             out.stat().st_size / 1024))

    # 对照 HTML（人工比对用）
    html = SC.render_html(payload)
    hp = Path("/tmp/_steam_card_pil_ref.html")
    hp.write_text(html, encoding="utf-8")
    print("对照 HTML -> %s (%.0f KB)" % (hp, len(html) / 1024))

    # 端到端（含取数）
    t2 = time.perf_counter()
    p = await SC.render_card(SID, "/tmp/_steam_card_e2e.png")
    print("端到端 %.2fs -> %s" % (time.perf_counter() - t2, p))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
