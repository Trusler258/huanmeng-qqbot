"""后台任务插件：提醒轮询、控制监控、地震轮询、战绩采集、PC状态、TTS、节假日、通知、更新Webhook、版本状态"""
from __future__ import annotations
import asyncio
import toml
from core.logger import get_logger
logger = get_logger("plugin.bg_tasks")


class Plugin:
    def __init__(self, ctx):
        self.ctx = ctx
        self._pending_failed = []  # v2.0.4an: 最近一轮采集失败玩家, 待 0 点并入日报说说

    async def on_load(self):
        pass

    async def on_enable(self):
        self.ctx.background.add(self._bg_remind_checker())
        self.ctx.background.add(self._bg_control_watcher())
        self.ctx.background.add(self._bg_wdsj_collector())
        self.ctx.background.add(self._bg_holiday())

    async def on_disable(self):
        pass

    async def on_unload(self):
        pass

    async def _bg_remind_checker(self):
        from modules.remind import remind_checker_loop
        await remind_checker_loop()

    async def _bg_control_watcher(self):
        from pathlib import Path as _Path
        ctrl_file = _Path(__file__).resolve().parent.parent.parent / "data" / "control.txt"
        from core.config import get_config, reload_config, set_debug_mode
        from core.logger import info, warning
        while True:
            try:
                if ctrl_file.exists():
                    cmd = ctrl_file.read_text(encoding="utf-8").strip().lower()
                    ctrl_file.unlink()
                    if cmd == "reload":
                        info("控制文件触发: reload")
                        reload_config()
                    elif cmd == "stop":
                        info("控制文件触发: stop")
                    elif cmd == "debug":
                        info("控制文件触发: debug toggle")
                        cfg = get_config()
                        set_debug_mode(not cfg.debug_mode)
                    elif cmd:
                        warning("控制文件未知命令: %s", cmd)
            except Exception as e:
                warning("控制文件读取异常: %s", e)
            await asyncio.sleep(1)



    async def _bg_holiday(self):
        from modules.holiday import start_holiday_service
        await start_holiday_service()




    async def _bg_wdsj_collector(self):
        from datetime import datetime, timedelta
        from pathlib import Path
        import json
        status_file = Path("data") / "wdsj_collect_status.json"
        status = "done"
        if status_file.exists():
            try:
                status = json.loads(status_file.read_text(encoding="utf-8")).get("status", "done")
            except Exception:
                pass
        if status == "running":
            logger.info("检测到上次采集未完成，重置状态等待下个整点")
            status_file.write_text(json.dumps({"status": "done", "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")
        last_ts = ""
        if status_file.exists():
            try:
                last_ts = json.loads(status_file.read_text(encoding="utf-8")).get("ts", "")
            except Exception:
                pass
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                if (datetime.now() - last_dt).total_seconds() > 4 * 3600:
                    logger.info("上次采集 %s 距今超过 4h，启动时立即补采", last_ts[:16])
                    from services.wdsj_tracker import daily_stats_collect
                    status_file.write_text(json.dumps({"status": "running", "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")
                    failed = await daily_stats_collect()
                    status_file.write_text(json.dumps({"status": "done", "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")
                    # v2.0.4an: 失败说明不再单独发说说, 并入 0 点日报图片那条
                    self._pending_failed = failed or []
            except Exception as e:
                logger.error("补采失败: %s", e)
        while True:
            now = datetime.now()
            next_hour = (now.hour // 4) * 4
            target = now.replace(hour=next_hour, minute=1, second=0, microsecond=0)
            while target <= now:
                target += timedelta(hours=4)
            wait = (target - now).total_seconds()
            logger.info("战绩采集将在 %s 后执行 (%s)", f"{int(wait//3600)}h{int((wait%3600)//60)}m", target.strftime("%H:%M"))
            await asyncio.sleep(wait)
            try:
                from services.wdsj_tracker import daily_stats_collect
                status_file.write_text(json.dumps({"status": "running", "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")
                failed = await daily_stats_collect()
                status_file.write_text(json.dumps({"status": "done", "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")
                # v2.0.4an: 失败记录暂存, 待 0 点日报推送时并入说说文字, 不再单独发文字说说
                self._pending_failed = failed or []
                # 只有 0 点时段才推送日榜，其他时段仅采集累计数据
                if datetime.now().hour != 0:
                    logger.info("非 0 点时段仅采集，不推送日榜")
                    continue
                try:
                    from services.wdsj_tracker import build_daily_rankings, build_arena_daily_rankings
                    from modules.commands import _build_daily_rank_html
                    from core.config import get_config
                    now_dt = datetime.now()
                    if now_dt.hour == 0:
                        yesterday = now_dt - timedelta(days=1)
                        _label = yesterday.strftime("%Y-%m-%d")
                        rows, today, t_start, t_end, new_players, _fb = build_daily_rankings(
                            label_date=_label, cross_day=True)
                        arena_rows, _, a_start, a_end, _fb_a = build_arena_daily_rankings(label_date=_label, cross_day=True)
                    else:
                        rows, today, t_start, t_end, new_players, _fb = build_daily_rankings()
                        arena_rows, _, a_start, a_end, _fb_a = build_arena_daily_rankings()
                    # 收集所有模式的日报图
                    _daily_pngs = []
                    if rows:
                        from modules.commands import _render_html_to_png, _daily_rank_payload
                        # ★ v2.3.26: 与 /~wdsj daily 同款开关——优先 Pillow（快 11 倍），
                        #   失败或关闭时回退 Chromium。两条路径共用同一份载荷与开关，
                        #   避免"手动查快、定时推慢"的不一致。
                        _p = None
                        try:
                            from pathlib import Path as _P_
                            from modules.features import is_enabled as _feat_on
                            if _feat_on("pillow_card"):
                                from services.wdsj_card_pillow import save_daily_rank_card
                                _pl = _daily_rank_payload(rows, today, new_players, t_start, t_end)
                                _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                                _outp = str(_P_(__file__).resolve().parent.parent.parent
                                            / "data" / "img_temp" / f"wdsj_daily_{_ts}.jpg")
                                _loop = asyncio.get_running_loop()
                                await _loop.run_in_executor(
                                    None, lambda: save_daily_rank_card(_pl, _outp))
                                _p = _outp
                        except Exception as _e:
                            logger.warning("定时日报 Pillow 绘制失败 → 回退 Chromium: %s", _e)
                            _p = None
                        if not _p:
                            html = _build_daily_rank_html(rows, today, new_players, t_start, t_end)
                            _p = await _render_html_to_png(html, "wdsj_daily")
                        if _p:
                            _daily_pngs.append(_p)
                    if arena_rows:
                        from modules.commands import _build_arena_daily_html
                        a_html = _build_arena_daily_html(arena_rows, today, a_start, a_end)
                        a_png = await _render_html_to_png(a_html, "wdsj_arena")
                        if a_png:
                            _daily_pngs.append(a_png)
                    if _daily_pngs:
                        # 读 target_groups（直接读 toml，不依赖 cfg.config）
                        _cfg_data = toml.load(Path(__file__).resolve().parent.parent.parent / "config" / "bot_config.toml")
                        _tg = _cfg_data.get("wdsj", {}).get("target_groups", [])
                        if not _tg:
                            logger.info("日榜未推送: target_groups 为空")
                        from services.sender import send_group_msg, build_local_image_cq
                        for gid in _tg:
                            for _p in _daily_pngs:
                                try:
                                    await send_group_msg(build_local_image_cq(_p), int(gid))
                                except Exception as e:
                                    logger.warning("日榜发送群 %s 失败: %s", gid, e)
                        # 同步发 QQ 空间（多图合并一条说说）
                        try:
                            from services.sender import get_ws_manager as _get_ws
                            _mgr = _get_ws()
                            # v2.0.4an: 失败说明并入日报说说文字(非0点不单独发文字说说)
                            _content = f"{today} 战绩日报（wdsj 绑定玩家）"
                            _fail_mark = Path("data") / "wdsj_qzone_fail_notify.json"
                            _today = datetime.now().strftime("%Y-%m-%d")
                            _notified_today = False
                            if _fail_mark.exists():
                                try:
                                    _notified_today = json.loads(_fail_mark.read_text(encoding="utf-8")).get("date") == _today
                                except Exception:
                                    pass
                            if getattr(self, "_pending_failed", None) and not _notified_today:
                                _players = sorted({p for p, _t in self._pending_failed})
                                if _players:
                                    _content += "\n\n⚠️ 战绩采集失败说明\n上游连接超时，重试 3 轮后以下玩家当日战绩未采集到：\n" + "\n".join(f"· {p}" for p in _players)
                                    # 记录当日已并入, 防重复
                                    try:
                                        _fail_mark.write_text(json.dumps(
                                            {"date": _today, "players": _players, "label": _today},
                                            ensure_ascii=False), encoding="utf-8")
                                    except Exception:
                                        pass
                            _qz_body = {
                                "action": "send_qzone_msg",
                                "params": {
                                    "content": _content,
                                    "images": [f"file://{_p}" for _p in _daily_pngs],
                                    "ugc_right": 1,
                                    "target_uins": [],
                                }
                            }
                            _ok = await _mgr.send(_qz_body)
                            if _ok:
                                logger.info("日榜已同步到 QQ 空间 (%d 张图)", len(_daily_pngs))
                            else:
                                logger.warning("日榜 QZone 同步失败: send 返回 False")
                        except Exception as _e:
                            logger.warning("日榜 QZone 同步失败: %s", _e)
                        logger.info("日榜已推送: %d 人 (%s), %d 张图", len(rows), today, len(_daily_pngs))
                except Exception as e:
                    logger.warning("日榜推送失败: %s", e)
            except Exception as e:
                logger.warning("战绩采集失败: %s", e)
                status_file.write_text(json.dumps({"status": "done", "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")