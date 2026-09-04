#!/usr/bin/env python3
"""手动补发 wdsj 昨日日榜（toml bug 修复后使用）"""
import asyncio, sys
sys.path.insert(0, "/root/bot")


async def main():
    from datetime import datetime, timedelta
    import toml
    from services.wdsj_tracker import build_daily_rankings, build_arena_daily_rankings
    from modules.commands import _build_daily_rank_html, _build_arena_daily_html, _render_html_to_png
    from services.sender import send_group_msg, build_local_image_cq

    yesterday = datetime.now() - timedelta(days=1)
    label = yesterday.strftime("%Y-%m-%d")
    print(f"[1] 生成 {label} 日榜数据...")
    rows, today, new_players, t_start, t_end = build_daily_rankings(label_date=label, cross_day=True)
    arena_rows, _, a_start, a_end = build_arena_daily_rankings(label_date=label, cross_day=True)
    print(f"    普通榜 {len(rows)} 人, 竞技榜 {len(arena_rows)} 人")

    pngs = []
    if rows:
        html = _build_daily_rank_html(rows, today, new_players, t_start, t_end)
        p = await _render_html_to_png(html, "wdsj_daily")
        if p:
            pngs.append(p)
            print("    普通榜图:", p)
    if arena_rows:
        a_html = _build_arena_daily_html(arena_rows, today, a_start, a_end)
        p = await _render_html_to_png(a_html, "wdsj_arena")
        if p:
            pngs.append(p)
            print("    竞技榜图:", p)

    if not pngs:
        print("[!] 无日榜数据，跳过发送")
        return

    cfg = toml.load("/root/bot/config/bot_config.toml")
    tg = cfg.get("wdsj", {}).get("target_groups", [])
    print(f"[2] 目标群: {tg}, 共 {len(pngs)} 张图")
    for gid in tg:
        for p in pngs:
            ok = await send_group_msg(build_local_image_cq(p), int(gid))
            print(f"    -> 群 {gid} {p.split('/')[-1]} {'OK' if ok else 'FAIL'}")
    print("[3] 补发完成")


asyncio.run(main())
