"""
消息发送服务（原 send_message.py 中的发送部分）
- ✅ 全局 WebSocket 长连接复用（核心性能优化点）
- 支持群聊 / 私聊 / 原始消息段(卡片) 三种发送模式
- 自动重试 + fallback 消息
"""

from __future__ import annotations

import asyncio
import json
import websockets

from core.logger import get_logger


def _safe_json_load(text: str) -> dict | None:
    """安全解析 JSON，失败返回 None"""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _is_ws_closed(ws) -> bool:
    """
    兼容新旧版 websockets 的连接状态检查。
    websockets < 10: ws.closed → bool
    websockets >= 10: ws.state → enum (OPEN/CLOSING/CLOSED)
    """
    if ws is None:
        return True
    # 新版 API: state 枚举
    if hasattr(ws, 'state'):
        return ws.state not in (websockets.State.OPEN, websockets.State.CLOSING)
    # 旧版 API: .closed 布尔属性
    if hasattr(ws, 'closed'):
        return ws.closed
    # 兜底：认为未关闭（避免误判）
    return False
from core.config import get_config
from utils.format_lang import format_lang

logger = get_logger("sender")


# ── WebSocket 连接管理器 ────────────────────────────────────
class WSConnectionManager:
    """
    全局 WebSocket 长连接管理器。
    
    核心优化：所有消息发送共用一个长连接，避免每次发送都 TCP+WS 握手。
    - connect() 时建立连接并保持
    - send() 复用已有连接，若断开则自动重连
    - close() 用于优雅关闭
    
    使用方式::
        ws = WSManager("localhost", 8099)
        await ws.send({"action": "send_group_msg", ...})
        await ws.close()
    """

    def __init__(self, host: str, port: int):
        self.uri = f"ws://{host}:{port}/"
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._lock = asyncio.Lock()
        self._host = host
        self._port = port
        self._connect_count = 0   # 统计：累计连接次数

    async def _ensure_connected(self) -> bool:
        """确保连接可用，断开则重建"""
        if self._ws is not None and not _is_ws_closed(self._ws):
            return True
        
        try:
            logger.debug("建立新的 WebSocket 连接 → %s (第 %d 次)",
                        self.uri, self._connect_count + 1)
            self._ws = await websockets.connect(
                self.uri,
                ping_interval=20,   # 每 20s 发 ping 保持连接
                ping_timeout=10,    # 10s 无 pong 则判定断开
                close_timeout=5,    # 关闭超时
            )
            self._connect_count += 1
            logger.info("WebSocket 已连接 %s:%d", self._host, self._port)
            return True
        except Exception as e:
            logger.error("WebSocket 连接失败 [%s:%d]: %s", self._host, self._port, e)
            self._ws = None
            return False

    async def send(self, payload: dict, max_retries: int = 3, retry_delay: float = 5.0) -> bool:
        """
        通过长连接发送一条消息。自动重试。

        Args:
            payload: OneBot API 请求体 {"action": ..., "params": ...}
            max_retries: 最大重试次数
            retry_delay: 重试间隔秒数

        Returns:
            是否发送成功
        """
        last_exc = None
        for attempt in range(max_retries + 1):
            async with self._lock:
                ok = await self._ensure_connected()
                if not ok:
                    last_exc = Exception("无法建立连接")
                    continue
                try:
                    await self._ws.send(json.dumps(payload))
                    return True
                except Exception as e:
                    last_exc = e
                    self._ws = None  # 标记为需要重连
                    if attempt < max_retries:
                        logger.warning("发送失败 (第%d/%d次): %s, %.1fs后重试...",
                                     attempt + 1, max_retries + 1, e, retry_delay)
                        await asyncio.sleep(retry_delay)

        logger.error("发送最终失败 (已重试%d次): %s", max_retries, last_exc)
        return False

    async def call_api(self, action: str, params: dict | None = None, timeout: float = 5.0) -> dict | None:
        """
        调用 OneBot API 并等待返回结果（请求-响应模式）。

        用于需要返回值的 API 调用，如 get_msg、get_group_member_info 等。

        Args:
            action: OneBot API 动作名（如 "get_msg"）
            params: API 参数字典
            timeout: 响应超时秒数

        Returns:
            API 返回的 data 字典；失败/超时返回 None

        Example::

            result = await ws.call_api("get_msg", {"message_id": 12345})
            # → {"data": {"message_id": 12345, "raw_message": "...", ...}, "retcode": 0}
        """
        import uuid as _uuid

        async with self._lock:
            ok = await self._ensure_connected()
            if not ok:
                logger.error("[API] call_api 失败: 无法连接")
                return None

            echo = f"api_{_uuid.uuid4().hex[:12]}"
            payload = {"action": action, "echo": echo, "params": params or {}}

            try:
                # 发送请求
                await self._ws.send(json.dumps(payload))
                logger.debug("[API] 已发送: action=%s echo=%s", action, echo)

                # 等待匹配的响应（通过 echo 字段匹配）
                deadline = asyncio.get_event_loop().time() + timeout
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        logger.warning("[API] call_api 超时: action=%s (%.1fs)", action, timeout)
                        return None

                    try:
                        raw_resp = await asyncio.wait_for(
                            self._ws.recv(), timeout=min(remaining, 2.0)
                        )
                    except asyncio.TimeoutError:
                        continue

                    resp = _safe_json_load(raw_resp)
                    if resp is None:
                        continue

                    # 严格匹配 echo（不 fallback 到任意 retcode，防止消费残留响应）
                    if resp.get("echo") == echo:
                        retcode = resp.get("retcode", -1)
                        if retcode == 0:
                            logger.info("[API] ✅ %s 成功", action)
                            return resp.get("data")
                        else:
                            logger.warning("[API] ❌ %s 失败: retcode=%s msg=%s",
                                         action, retcode, resp.get("msg", ""))
                            return None

            except Exception as e:
                logger.warning("[API] call_api 异常: action=%s error=%s (将重试一次)", action, str(e)[:80])
                self._ws = None
                # 重连后重试一次
                if await self._ensure_connected():
                    try:
                        await self._ws.send(json.dumps(payload))
                        raw_resp = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
                        resp = _safe_json_load(raw_resp)
                        if resp and resp.get("retcode") == 0:
                            logger.info("[API] ✅ %s 成功 (重试)", action)
                            return resp.get("data")
                    except Exception:
                        pass
                return None

    async def close(self):
        """关闭连接"""
        async with self._lock:
            if self._ws is not None and not _is_ws_closed(self._ws):
                try:
                    await self._ws.close()
                    logger.debug("WebSocket 连接已关闭")
                except Exception as e:
                    logger.warning("关闭连接时异常: %s", e)
            self._ws = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not _is_ws_closed(self._ws)


# ── 全局单例 ────────────────────────────────────────────────
_global_ws_manager: WSConnectionManager | None = None


def get_ws_manager() -> WSConnectionManager:
    """获取全局 WS 连接管理器"""
    global _global_ws_manager
    if _global_ws_manager is None:
        cfg = get_config()
        _global_ws_manager = WSConnectionManager(cfg.host, cfg.port)
    return _global_ws_manager


def init_sender(host: str, port: int):
    """初始化全局发送器（程序启动时调用一次）"""
    global _global_ws_manager
    _global_ws_manager = WSConnectionManager(host, port)


async def close_sender():
    """关闭全局发送器（程序退出时调用）"""
    global _global_ws_manager
    if _global_ws_manager is not None:
        await _global_ws_manager.close()
        _global_ws_manager = None


# ── 发送便捷函数 ────────────────────────────────────────────

def build_local_image_cq(local_path: str) -> str:
    """把本地文件绝对路径转成 CQ:image 消息（修复 file:// 拼接 4 斜杠问题）。

    Linux 绝对路径如 /root/bot/xx.png 直接拼 file:/// 会变成 file:////root/...（4 斜杠），
    NapCat 剥掉 file:// 后得到 //root/... 报 ENOENT。这里去掉开头斜杠再拼，
    确保结果是 file:///root/bot/xx.png（协议 + 1 个根斜杠）。
    """
    normalized = str(local_path).replace("\\", "/").lstrip("/")
    return f"[CQ:image,file=file:///{normalized}]"


async def send_group_msg(message: str, group_id: int) -> bool:
    """发送群聊文本消息"""
    mgr = get_ws_manager()
    payload = {
        "action": "send_group_msg",
        "params": {"group_id": group_id, "message": message},
        "echo": "chat",
    }
    success = await mgr.send(payload, max_retries=3, retry_delay=5.0)
    if not success:
        # 发送 fallback 提示
        cfg = get_config()
        fallback = format_lang("bot.fallback_reply", name=cfg.bot_name)
        fallback_payload = {
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": fallback},
            "echo": "chat_fallback",
        }
        await mgr.send(fallback_payload, max_retries=0)
    # ★ 记录 bot 所有群聊回复到 msglog（排查回复质量）
    _log_bot_sent(group_id, message if success else fallback)
    return success


async def send_private_msg(message: str, user_id: int) -> bool:
    """发送私聊文本消息"""
    mgr = get_ws_manager()
    payload = {
        "action": "send_private_msg",
        "params": {"user_id": user_id, "message": message},
        "echo": "chat",
    }
    success = await mgr.send(payload, max_retries=3, retry_delay=5.0)
    if not success:
        cfg = get_config()
        fallback = format_lang("bot.fallback_reply", name=cfg.bot_name)
        fallback_payload = {
            "action": "send_private_msg",
            "params": {"user_id": user_id, "message": fallback},
            "echo": "chat_fallback",
        }
        await mgr.send(fallback_payload, max_retries=0)
    if success:
        _log_bot_sent(user_id, message)
    return success


def _log_bot_sent(chat_id: int, content: str):
    """记录 bot 发送的消息到 msglog（全量归档，用于排查回复质量）"""
    try:
        from time import time as _time
        from core.config import get_config
        cfg = get_config()
        entry = {
            "msg_id": 0,  # fire-and-forget 无 message_id
            "time": int(_time()),
            "user_id": cfg.bot_qq,
            "type": "bot",
            "content": content,
            "recalled": False,
        }
        from pathlib import Path as _Path
        log_dir = _Path(__file__).resolve().parent.parent / "data" / "msglog"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"msglog_{chat_id}.jsonl"
        import json as _json
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("_log_bot_sent写msglog失败: %s", e)
        pass  # 静默失败，不影响发送


async def send_raw_group(raw_obj: dict, group_id: int) -> bool:
    """发送自定义 OneBot 消息段到群（如 markdown 卡片）"""
    if not isinstance(group_id, int) or group_id <= 0:
        logger.warning("无效的群号: %s (type=%s)", group_id, type(group_id))
        return False
    mgr = get_ws_manager()
    req = {
        "action": "send_group_msg",
        "params": {"group_id": group_id, "message": [raw_obj]},
        "echo": "card",
    }
    success = await mgr.send(req)
    if success:
        logger.info("卡片消息已发送到群 %d", group_id)
    else:
        logger.warning("卡片消息发送失败 群=%d", group_id)
    return success


async def send_raw_user(raw_obj: dict, user_id: int) -> bool:
    """发送自定义 OneBot 消息段私聊"""
    if not isinstance(user_id, int) or user_id <= 0:
        logger.warning("无效的用户ID: %s", user_id)
        return False
    mgr = get_ws_manager()
    req = {
        "action": "send_private_msg",
        "params": {"user_id": user_id, "message": [raw_obj]},
        "echo": "card",
    }
    success = await mgr.send(req)
    if success:
        logger.info("卡片消息已发送给用户 %d", user_id)
    else:
        logger.warning("卡片消息发送失败 用户=%d", user_id)
    return success


async def send_by_chat_type(
    message: str,
    chat_id: int,
    is_group: bool,
    user_id: int | None = None,
) -> bool:
    """
    根据聊天类型选择群聊/私聊发送。
    
    Args:
        message: 消息文本
        chat_id: 群号或用户ID
        is_group: 是否群聊
        user_id: 私聊时的用户ID（群聊时可省略）
    """
    if is_group:
        return await send_group_msg(message, chat_id)
    else:
        assert user_id is not None, "私聊发送必须提供 user_id"
        return await send_private_msg(message, user_id)


async def send_sentences(
    sentences: list[str],
    chat_id: int,
    is_group: bool,
    user_id: int | None = None,
    min_interval: float = 0.5,
    max_interval: float = 1.5,
):
    """
    逐条发送句子列表，每条之间随机间隔。
    用于多句回复的分批发送效果。
    """
    from core.config import get_config
    cfg = get_config()
    import random

    logger.info("开始分批发送 %d 条句子 → chat=%d is_group=%s",
               len(sentences), chat_id, is_group)
    
    for i, sentence in enumerate(sentences):
        if i > 0:
            delay = random.uniform(min_interval, max_interval)
            logger.debug("句间等待 %.2fs (#%d/%d)", delay, i + 1, len(sentences))
            await asyncio.sleep(delay)

        await _send_and_record(sentence, chat_id, is_group, user_id, cfg)
        logger.debug("已发送第 %d/%d 条: %s...", i + 1, len(sentences), sentence[:30])
    
    logger.info("分批发送完成: 共 %d 条 → chat=%d", len(sentences), chat_id)


async def _send_and_record(content: str, chat_id: int, is_group: bool,
                            user_id: int | None, cfg) -> int:
    """发送消息 + 录制到 msglog/stats，返回 message_id"""
    mgr = get_ws_manager()
    if is_group:
        action = "send_group_msg"
        params = {"group_id": chat_id, "message": content}
    else:
        action = "send_private_msg"
        params = {"user_id": user_id, "message": content}

    msg_id = 0
    try:
        resp = await mgr.call_api(action, params, timeout=5.0)
        if resp:
            msg_id = int(resp.get("message_id", 0))
    except Exception:
        logger.debug("call_api 发送失败，回退到 fire-and-forget")
        await send_by_chat_type(content, chat_id, is_group, user_id)
        # ★ fallback 也录 stats
        if is_group and chat_id in cfg.group_list:
            try:
                from modules.stats import record_message
                record_message(chat_id, cfg.bot_qq, content, cfg.bot_name)
            except Exception:
                pass
        return 0

    # ★ 录制到 msglog（撤回支持）和 stats（统计）
    if msg_id and is_group and chat_id in cfg.group_list:
        try:
            from modules.recall import record_incoming_message
            record_incoming_message(chat_id, cfg.bot_qq, msg_id, "bot", content)
        except Exception as e:
            logger.debug("recall录制bot消息失败: %s", e)
            pass

    if is_group and chat_id in cfg.group_list:
        try:
            from modules.stats import record_message
            record_message(chat_id, cfg.bot_qq, content, cfg.bot_name)
        except Exception:
            pass

    return msg_id


async def send_file(file_path: str, chat_id: int, is_group: bool) -> bool:
    """发送文件到群或私聊"""
    from pathlib import Path
    path = Path(file_path)
    if not path.exists():
        logger.warning("文件不存在: %s", file_path)
        return False

    # ★ 图片扩展名走图片消息（CQ:image 本地路径），避免 gif/png 等以文件形式发送
    _IMG_EXTS = {".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    if path.suffix.lower() in _IMG_EXTS:
        cq = build_local_image_cq(str(path.resolve()))
        try:
            # ★ 必须用 call_api（请求-响应）等待 NapCat 确认收到图片后再返回。
            #   若用 send()（fire-and-forget），调用方(motou)会立即删临时文件，
            #   NapCat 随后读文件报 ENOENT，图片丢失。
            ws_mgr = get_ws_manager()
            params = {"message": cq}
            api_name = "send_group_msg" if is_group else "send_private_msg"
            params["group_id" if is_group else "user_id"] = chat_id
            resp = await ws_mgr.call_api(api_name, params, timeout=30)
            ok = bool(resp)
            if ok:
                logger.info("图片发送成功: %s (chat=%d)", path.name, chat_id)
            else:
                logger.warning("图片发送失败(走文件兜底): %s", path.name)
            return ok
        except Exception as e:
            logger.warning("图片发送异常[%s]: %s，走文件兜底", path.name, e)

    api_name = "upload_group_file" if is_group else "upload_private_file"
    abs_path = str(path.resolve())
    
    logger.info("发送文件: %s → chat=%d api=%s", path.name, chat_id, api_name)
    try:
        ws_mgr = get_ws_manager()
        await ws_mgr.call_api(api_name, {
            "group_id" if is_group else "user_id": chat_id,
            "file": abs_path,
            "name": path.name,
        }, timeout=30)
        logger.info("文件发送成功: %s", path.name)
        return True
    except Exception as e:
        logger.warning("文件发送失败 [%s]: %s", path.name, e)
        return False
