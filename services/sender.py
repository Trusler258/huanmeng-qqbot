"""
消息发送服务（原 send_message.py 中的发送部分）
- ✅ 全局 WebSocket 长连接复用（核心性能优化点）
- 支持群聊 / 私聊 / 原始消息段(卡片) 三种发送模式
- 自动重试 + fallback 消息
"""

from __future__ import annotations

import asyncio
import json
import re
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

    ⚠️ 这是**新增发图代码的正确入口**。但项目里仍有 10+ 处手工拼
    `f"[CQ:image,file=file:///{绝对路径}]"` 没走它 —— 见 `fix_cq_paths()` 的说明。
    """
    normalized = str(local_path).replace("\\", "/").lstrip("/")
    return f"[CQ:image,file=file:///{normalized}]"


# 匹配 CQ 码里 file= 参数中的多余斜杠（file: 后 4 个及以上 /）
_CQ_PATH_RE = re.compile(r'(\[CQ:[^\]]*?\bfile=)file:////+')


def fix_cq_paths(text: str) -> str:
    """把 CQ 码里 file= 的 `file:////`（四斜杠）规范成 `file:///`（三斜杠）。

    背景（2026-09-17 实测）：项目里多处手工拼 `file:///{绝对路径}`，而绝对路径
    本身以 `/` 开头 → `file:////root/...`。实测 msglog 里 **175 条四斜杠 vs
    108 条三斜杠**，即多数图片走了不规范路径。

    **但近 7 天日志 0 次 ENOENT** —— NapCat 会 normalize，所以这是代码卫生问题，
    不是活跃故障。

    修在**发送出口**而不是逐个改那 10+ 处手拼点，理由：
      1. 一处覆盖全部（含未来新写的漏网代码）
      2. 正则锚定在 CQ 码 `file=` 参数内，**不会误伤正文里正常的 URL/代码片段**
         （这是当初不敢在出口做粗粒度替换的原因）
    """
    if "file:////" not in text:
        return text
    return _CQ_PATH_RE.sub(r'\1file:///', text)



async def send_group_msg(message: str, group_id: int) -> bool:
    """发送群聊文本消息

    v2.0.4af(2026-09-05): fire-and-forget(mgr.send) → call_api 拿真实 message_id
    供撤回匹配（旧实现 msg_id=0 永远匹配不到撤回）。
    v2.0.4ah(2026-09-05): **去掉失败重试**——call_api 超时大多是"NapCat 已收到但响应
    回包慢/丢"，重试 = 同一条消息重复发送（实测 /sys 一次发 3 条）。改为单次 call_api
    + 8s 超时，未确认仅日志不重发不补 fallback；message_id 拿不到则 msg_id=0 兜底录制。
    """
    mgr = get_ws_manager()
    cfg = get_config()
    message = fix_cq_paths(message)          # CQ 码路径规范化（见 fix_cq_paths）
    data = await mgr.call_api("send_group_msg", {"group_id": group_id, "message": message}, timeout=8.0)
    if not data:
        # 超时/失败不重发（消息可能已送达，重发=重复），也不补 fallback（同理）
        logger.warning("send_group_msg 未确认(超时/失败) 群=%d 不重发防重复", group_id)
        _log_bot_sent(group_id, message)
        return False
    msg_id = int(data.get("message_id", 0) or 0)
    _log_bot_sent(group_id, message, msg_id=msg_id)
    return True


async def send_private_msg(message: str, user_id: int) -> bool:
    """发送私聊文本消息（v2.0.4af: call_api 确认；v2.0.4ah: 去掉重试防重复）"""
    mgr = get_ws_manager()
    cfg = get_config()
    message = fix_cq_paths(message)          # CQ 码路径规范化（见 fix_cq_paths）
    data = await mgr.call_api("send_private_msg", {"user_id": user_id, "message": message}, timeout=8.0)
    if not data:
        logger.warning("send_private_msg 未确认(超时/失败) user=%d 不重发防重复", user_id)
        _log_bot_sent(user_id, message)
        return False
    _log_bot_sent(user_id, message)
    return True


def _log_bot_sent(chat_id: int, content: str, msg_id: int = 0):
    """记录 bot 发送的消息到 msglog（全量归档，用于撤回匹配与排查）

    v2.0.4af: 增加 msg_id 参数——有真实 message_id 时以真实 id 录制（可被撤回事件
    按号匹配）；无 id(发送失败兜底)维持 0，mark_recalled 不会误匹配这类条目。
    """
    try:
        from time import time as _time
        from core.config import get_config
        cfg = get_config()
        entry = {
            "msg_id": int(msg_id or 0),
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
    faces: list[str | None] | None = None,
    face_interval: float = 0.35,
):
    """
    逐条发送句子列表，每条之间随机间隔。
    用于多句回复的分批发送效果。

    v2.1.18: 新增 faces —— 与 sentences **等长**的 CQ 码列表（None 表示该句不带图）。
    有值时按「文字 → 该句的图 → 文字 → 图」交错发送，模拟真人一边打字一边甩表情包的节奏，
    而不是把仅有的一个表情堆在所有文字之后。长度不一致时按较短的对齐，缺的视为不带图。
    """
    from core.config import get_config
    cfg = get_config()
    import random

    logger.info("开始分批发送 %d 条句子 → chat=%d is_group=%s%s",
               len(sentences), chat_id, is_group,
               f" (含 {sum(1 for f in faces if f)} 张配图)" if faces else "")

    for i, sentence in enumerate(sentences):
        if i > 0:
            delay = random.uniform(min_interval, max_interval)
            logger.debug("句间等待 %.2fs (#%d/%d)", delay, i + 1, len(sentences))
            await asyncio.sleep(delay)

        await _send_and_record(sentence, chat_id, is_group, user_id, cfg)
        logger.debug("已发送第 %d/%d 条: %s...", i + 1, len(sentences), sentence[:30])

        # 该句配图：紧跟着发，短暂停顿让它落在同一条消息的气口上
        if faces and i < len(faces) and faces[i]:
            await asyncio.sleep(face_interval)
            try:
                # ★ v2.3.21: 发表情时在日志打印表情文件名（用户要求，便于核对"发了哪个表情"）
                _face_cq = faces[i]
                _m = re.search(r'file=([^,\]]+)', _face_cq or "")
                _fname = (_m.group(1).split("/")[-1] if _m else "") or _face_cq[:40]
                _kw = re.search(r'\[FACE:([^\]]*)\]', _face_cq) if _face_cq else None
                logger.info("发表情: %s%s (sent idx=%d/%d)",
                            _fname,
                            f" 关键词={_kw.group(1)}" if _kw else "",
                            i + 1, len(sentences))
                await _send_and_record(_face_cq, chat_id, is_group, user_id, cfg)
                logger.debug("已发送第 %d/%d 条的配图", i + 1, len(sentences))
            except Exception:
                logger.warning("配图发送失败 (#%d)", i + 1, exc_info=True)

    logger.info("分批发送完成: 共 %d 条 → chat=%d", len(sentences), chat_id)


async def _send_and_record(content: str, chat_id: int, is_group: bool,
                            user_id: int | None, cfg) -> int:
    """发送消息 + 录制到 msglog/stats，返回 message_id"""
    mgr = get_ws_manager()
    content = fix_cq_paths(content)          # CQ 码路径规范化（见 fix_cq_paths）
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
        # ★ 注意：send_by_chat_type 内部（send_group_msg/send_private_msg）
        #   已经写过 msglog 了，这里**不要**再补一次 _log_bot_sent。
        #   v2.3.36 修复：原来这里又显式写了一遍，fallback 路径每条消息落盘两次。
        await send_by_chat_type(content, chat_id, is_group, user_id)
        # ★ fallback 也录 stats
        if is_group and chat_id in cfg.group_list:
            try:
                from modules.stats import record_message
                record_message(chat_id, cfg.bot_qq, content, cfg.bot_name)
            except Exception:
                pass
        return 0

    # ★ 录制到 msglog（撤回支持）与 stats（统计）
    #
    # v2.3.36 修复「同一条消息落盘两次」：
    #   _log_bot_sent（sender 侧，v2.0.4af）与 record_incoming_message（recall 侧）
    #   写的是**同一个文件**、**完全相同的 entry 结构**（msg_id/time/user_id/type/
    #   content/recalled），原来在群聊白名单里两个都被调用 → 每条 bot 消息写两遍。
    #   实测 msglog_247478659.jsonl：bot 消息 719 条，按 (msg_id,内容) 去重后仅 625 条，
    #   **94 条完全重复且 msg_id 相同** —— 确认是记录侧重复，不是真的发了两条
    #   （日志里 send_group_msg 只成功一次）。
    #   重复记录会污染记忆检索、统计计数与用户画像的消息条数。
    #
    # 分工：群聊白名单走 recall 的录制（撤回匹配依赖它按 msg_id 定位）；
    #       私聊 / 非白名单群 / recall 异常时，兜底走 sender 自己的 _log_bot_sent。
    _logged = False
    if is_group and chat_id in cfg.group_list:
        try:
            from modules.recall import record_incoming_message
            record_incoming_message(chat_id, cfg.bot_qq, msg_id, "bot", content)
            _logged = True
        except Exception as e:
            logger.debug("recall录制bot消息失败: %s", e)

    if not _logged:
        # v2.3.17: 私聊也录 msglog（此前私聊 LLM 管线不写，命令层却写，两边不一致）
        if msg_id:
            _log_bot_sent(chat_id, content, msg_id=msg_id)
        else:
            _log_bot_sent(chat_id, content)

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
