"""
Bot 核心类（原 bot.py 的重构版）
- WebSocket 长连接管理（接收消息）
- 事件循环主入口
- 配置热加载
- 优雅关闭
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from pathlib import Path

import websockets

from core.logger import get_logger, info, error, warning, critical
from core.config import get_config, load_bot_config, reload_config, set_debug_mode
from utils.format_lang import load_lang, format_lang
from services.sender import init_sender, close_sender
from core.dispatcher import EventDispatcher
from core.context_manager import init_context, get_context_mgr

logger = get_logger("bot")


class HuanmengBot:
    """
    幻梦 QQ Bot 核心类。
    
    负责：
    1. 启动时加载所有配置和初始化所有服务
    2. 建立 WebSocket 长连接监听事件
    3. 将事件分发到 EventDispatcher
    4. 处理断线重连
    5. 支持优雅关闭
    """

    VERSION = "1.4.2"

    def __init__(self):
        self.cfg: object = None          # type: ignore (BotConfig)
        self.dispatcher: EventDispatcher | None = None
        self._running: bool = False
        self._ws_uri: str = ""

    async def initialize(self):
        """初始化：加载配置 → 初始化各服务 → 构建分发器"""
        info("=" * 50)
        info("🐱 幻梦 QQ Bot v%s 正在启动...", self.VERSION)
        info("=" * 50)

        # 1. 加载配置
        self.cfg = load_bot_config()
        # 预加载技能文件（首次 LLM 调用更快）
        from services.llm import _load_skill_sections
        _load_skill_sections()
        info("配置已加载 | bot=%s | host=%s:%d | debug=%s",
             self.cfg.bot_name, self.cfg.host, self.cfg.port, self.cfg.debug_mode)

        # 2. 初始化语言文件
        load_lang()
        info("语言文件已加载")

        # 3. 初始化日志系统
        from core.logger import init_logger
        init_logger(debug_mode=self.cfg.debug_mode)
        info("日志系统已初始化")

        # 自动更新检查
        try:
            from modules.auto_update import check_and_update
            result = await check_and_update()
            info("自动更新: %s", result)
        except Exception as e:
            info("自动更新跳过: %s", e)

        # 4. 初始化发送器（WebSocket 连接复用）
        init_sender(self.cfg.host, self.cfg.port)
        info("消息发送器已初始化 (WS复用模式)")

        # 5. 初始化上下文管理器
        init_context()
        info("上下文管理器已初始化")

        # 5a. 恢复未完成的五子棋对局（持久化恢复）
        from modules.wzq import load_games
        load_games()

        # 6. 判断模块关键词初始化
        from modules.judge import init_keywords
        init_keywords()

        # 7. 构建事件分发器
        self.dispatcher = EventDispatcher()
        from core.dispatcher import _current_dispatcher
        import core.dispatcher as _disp
        _disp._current_dispatcher = self.dispatcher
        info("事件分发器已就绪")

        # 8. 构建 WS URI
        self._ws_uri = f"ws://{self.cfg.host}:{self.cfg.port}/"

        info("=" * 50)
        info("✅ 所有组件初始化完成，准备连接 NapCat...")
        info("=" * 50)

    async def run(self):
        """主循环：连接 → 接收事件 → 分发 → 断线重连"""
        self._running = True

        # ★ 启动后台任务：提醒轮询 & 每日统计推送
        import asyncio as _asyncio
        _asyncio.ensure_future(self._bg_remind_checker())
        _asyncio.ensure_future(self._bg_midnight_report())
        _asyncio.ensure_future(self._bg_control_watcher())
        _asyncio.ensure_future(self._bg_nickname_sync())
        _asyncio.ensure_future(self._bg_eq_poller())
        _asyncio.ensure_future(self._bg_log_server())
        _asyncio.ensure_future(self._bg_wdsj_collector())
        info("后台任务已启动: 提醒轮询 + 每日0点统计 + 控制文件 + 昵称同步 + 地震速报 + 日志控制台:58888 + 战绩采集")

        # ★ 预启动 Chromium 和渲染队列（不阻塞聊天）
        try:
            from core.queues import start_render_queue
            start_render_queue()
            from modules.changelog import _ensure_browser
            await _ensure_browser()
            info("Chromium 已预启动 + 渲染队列就绪")
        except Exception as e:
            warning("Chromium 预启动失败: %s (将在首次使用时懒加载)", e)

        while self._running:
            try:
                info("正在连接 NapCat @ %s ...", self._ws_uri)
                async with websockets.connect(self._ws_uri) as ws:
                    info("已连接到 NapCat (%s)", self._ws_uri)

                    async for message in ws:
                        if not self._running:
                            break
                        
                        try:
                            await self.dispatcher.dispatch(message)
                        except Exception as e:
                            error("消息处理异常: %s", e, exc_info=True)
                            continue

            except websockets.exceptions.ConnectionClosed as e:
                warning(f"WebSocket 连接关闭: {e}")
            except Exception as e:
                error(f"连接异常: {e}")

            if self._running:
                info("⏳ 5 秒后重新连接...")
                await asyncio.sleep(5)

    def stop(self):
        """触发停止信号"""
        self._running = False
        info("停止信号已发出")

    async def shutdown(self):
        """优雅关闭所有资源"""
        info("🛑 正在关闭...")
        
        # 关闭发送器的长连接
        await close_sender()
        
        # 输出统计
        ctx = get_context_mgr()
        stats = ctx.get_stats()
        info("运行统计: %s", stats)

        # 刷新搜索缓存
        from modules.judge import flush_search_cache
        flush_search_cache()

        # 刷新撤回缓冲区
        from modules.recall import flush_buffer
        flush_buffer()
        
        # 刷新图片缓存
        try:
            from services.image_api import _get_cache
            cache = _get_cache()
            cache.flush()
        except Exception:
            pass

        info("👋 再见！")
        print("")  # 空行让日志更清晰

    def handle_reload(self):
        """处理 /~reload 指令：重新加载所有配置"""
        new_cfg = reload_config()
        self.cfg = new_cfg
        
        # 更新 WS URI（如果地址变了）
        new_uri = f"ws://{new_cfg.host}:{new_cfg.port}/"
        if new_uri != self._ws_uri:
            self._ws_uri = new_uri
            info("WS 地址已更新: %s", new_uri)
        
        # 不清空上下文（保留对话连续性）
        # 清除并重载技能文件缓存
        from services.llm import reload_skill_cache
        reload_skill_cache()
        # 预加载（下次调用直接命中缓存）
        from services.llm import _load_skill_sections
        _load_skill_sections()
        info("配置热加载完成（上下文保留）")

    async def _bg_remind_checker(self):
        """后台任务：提醒轮询"""
        from modules.remind import remind_checker_loop
        await remind_checker_loop()

    async def _bg_midnight_report(self):
        """后台任务：每日0点群聊统计推送"""
        from modules.stats import midnight_report_loop
        await midnight_report_loop(self.cfg)

    async def _bg_wdsj_collector(self):
        """后台任务：每 4 小时采集战绩（0/4/8/12/16/20 点的第1分钟）"""
        import asyncio as _asyncio
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
            # 上次未完成也不要现在采，等下一个整4点
            logger.info("检测到上次采集未完成，重置状态等待下个整点")
            status_file.write_text(json.dumps({"status": "done", "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")

        # ★ 启动兜底：如果上次采集距今超过 4 小时，立即补采一次
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
                    await daily_stats_collect()
                    status_file.write_text(json.dumps({"status": "done", "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                logger.error("补采失败: %s", e)

        while True:
            now = datetime.now()
            next_hour = (now.hour // 4) * 4  # 从当前时隙开始找
            target = now.replace(hour=next_hour, minute=1, second=0, microsecond=0)
            while target <= now:
                target += timedelta(hours=4)
            wait = (target - now).total_seconds()
            logger.info("战绩采集将在 %s 后执行 (%s)", f"{int(wait//3600)}h{int((wait%3600)//60)}m", target.strftime("%H:%M"))
            await _asyncio.sleep(wait)
            try:
                from services.wdsj_tracker import daily_stats_collect
                status_file.write_text(json.dumps({"status": "running", "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")
                await daily_stats_collect()
                status_file.write_text(json.dumps({"status": "done", "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")

                # 采集完 → 发日报
                # 0:01 发昨天完整日榜，其他时段发今天当前累计
                try:
                    from services.wdsj_tracker import build_daily_rankings
                    from modules.commands import _build_daily_rank_html
                    from modules.changelog import _ensure_browser
                    from services.sender import send_group_msg
                    from core.config import get_config

                    now_dt = datetime.now()
                    if now_dt.hour == 0:
                        from datetime import timedelta
                        yesterday = now_dt - timedelta(days=1)
                        rows, today, new_players, t_start, t_end = build_daily_rankings(
                            label_date=yesterday.strftime("%Y-%m-%d"), cross_day=True)
                    else:
                        rows, today, new_players, t_start, t_end = build_daily_rankings()

                    if rows:
                        html = _build_daily_rank_html(rows, today, new_players, t_start, t_end)
                        import time as _time
                        ts = _time.strftime("%Y%m%d_%H%M%S")
                        from pathlib import Path as _Path
                        _tmp = _Path("data") / "img_temp"
                        _tmp.mkdir(parents=True, exist_ok=True)
                        out_path = str(_tmp / f"wdsj_daily_{ts}.png")
                        browser = await _ensure_browser()
                        page = await browser.new_page(viewport={"width": 540, "height": 600})
                        await page.set_content(html, timeout=10000)
                        await page.wait_for_timeout(500)
                        await page.screenshot(path=out_path, full_page=True)
                        await page.close()
                        cq = f"[CQ:image,file=file:///{out_path}]"
                        cfg = get_config()
                        target_groups = cfg.target_groups if hasattr(cfg, 'target_groups') else []
                        for gid in target_groups:
                            try:
                                await send_group_msg(cq, int(gid))
                            except Exception:
                                pass
                        logger.info("日榜已推送: %d 人 (%s)", len(rows), today)
                except Exception as e:
                    logger.warning("日榜推送失败: %s", e)
            except Exception as e:
                logger.warning("战绩采集失败: %s", e)
                status_file.write_text(json.dumps({"status": "done", "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")

    async def _bg_control_watcher(self):
        """后台任务：监听控制文件 data/control.txt
        支持的命令:
          reload  - 热重载配置
          stop    - 优雅关闭
          debug   - 切换 debug 模式
        用法: echo reload > data/control.txt
        """
        from pathlib import Path as _Path
        ctrl_file = _Path(__file__).resolve().parent / "data" / "control.txt"

        while self._running:
            try:
                if ctrl_file.exists():
                    cmd = ctrl_file.read_text(encoding="utf-8").strip().lower()
                    ctrl_file.unlink()

                    if cmd == "reload":
                        info("控制文件触发: reload")
                        self.handle_reload()
                    elif cmd == "stop":
                        info("控制文件触发: stop")
                        self.stop()
                    elif cmd == "debug":
                        info("控制文件触发: debug toggle")
                        set_debug_mode(not self.cfg.debug_mode)
                    elif cmd:
                        warning("控制文件未知命令: %s", cmd)
            except Exception as e:
                warning("控制文件读取异常: %s", e)

            await asyncio.sleep(1)

    async def _bg_nickname_sync(self):
        """后台任务：每天 23:59 自动同步 QQ 昵称到 roles.toml"""
        from modules.nickname_sync import nickname_sync_loop
        await nickname_sync_loop()

    async def _bg_eq_poller(self):
        """后台任务：地震速报自动轮询"""
        from modules.earthquake import start_polling
        await start_polling()

    async def _bg_log_server(self):
        """后台任务：Web 实时日志控制台端口 58888"""
        from core.log_server import start
        await start(58888)

    async def _send_reload_done(self):
        """发送重载完成回执（如果是从 /~reload 触发的重启）"""
        import json
        from pathlib import Path
        state_path = Path(__file__).resolve().parent / "data" / "reload_state.json"

        if not state_path.exists():
            return

        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state_path.unlink()
        except Exception:
            return

        chat_id = state.get("chat_id")
        is_group = state.get("is_group")
        if not chat_id:
            return

        from services.sender import send_group_msg, send_private_msg

        msg = "✅ 重载完成喵~ done! (。-`ω´-)✧"
        try:
            if is_group:
                await send_group_msg(msg, chat_id)
            else:
                await send_private_msg(msg, chat_id)
            info("重载回执已发送: chat=%d is_group=%s", chat_id, is_group)
        except Exception as e:
            error("重载回执发送失败: %s", e)
