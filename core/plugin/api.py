"""
Plugin API（移植自 huanmeng-kook-bot core/plugin/api.py，适配 qqbot）

插件的唯一公共入口。插件只能通过 ctx 访问公开能力，禁止直接 import Core 内部实现、
直接访问数据库或修改内部 Runtime 对象。

暴露的能力（全部可选，按需使用）：
- message   : 发送 / 回复消息（走 services.sender）
- memory    : 记忆写入 / 检索（走 modules.memory / db 检索层，异步不阻塞）
- event     : 订阅 / 发布事件（走 EventBus）
- timer     : 注册周期定时器（reload/unload 自动取消）
- capability: 注册 Command / Tool 能力（走 CapabilityRegistry，命令自动挂进 COMMAND_MAP）
- config    : 读取本插件 manifest 声明的静态配置
- economy   : 积分余额 / 权益库存读写（modules.economy，唯一锁 + 原子写）
- image     : 图片提取（从 QQ CQ 码 / 文本中提取图片 URL）
- vision    : 图片识别（复用 services.image_api 视觉 LLM 描述）
- identity  : 权限判定（is_admin，走 core.config 全局权限）
- logger    : 本插件命名空间的日志器
- llm       : 文本生成（走 services.llm，默认 reply_model，惰性导入）
- approval  : 人工审批申请 / 回传（私聊管理员，走内存 token 池）
- sandbox   : 沙箱真实执行 py/cpp/shell + 产物收集 + 清理（走 core.sandbox，惰性导入）

约束：
- 不暴露 db、core 内部 Runtime、文件系统、网络、进程执行。
- 上述 Adapter 一律惰性 import 内部实现，插件经 ctx.* 调用即与内部实现解耦。
"""
from __future__ import annotations

import asyncio
import re
import time
import secrets
import types
from typing import Any, Awaitable, Callable, Optional

from core.logger import get_logger
from core.eventbus import EventBus, get_event_bus
from pathlib import Path
from core.capability import (
    Capability, CATEGORY_COMMAND, CATEGORY_TOOL, RUNTIME_COMMAND, RUNTIME_FC,
    get_capability_registry,
)

logger = get_logger("plugin.api")

def _is_core_tool_name(tool_name: str) -> bool:
    """工具名是否已被核心占用。

    ★ v2.3.101：插件**未声明**权限时不允许占用这些名字——否则插件静默替换核心
    handler，却继续沿用核心为该名字做的权限判定，等于越权。
    """
    try:
        from core.tools import TOOLS
        for t in TOOLS or []:
            if (t.get("function") or {}).get("name") == tool_name:
                return True
    except Exception:
        pass
    return False



_HIGH_RISK_LEAVES = ("raw",)   # 通配不得覆盖的高危能力（如 napcat.raw）


def _perm_ok(manifest, perm: str) -> bool:
    """权限门控：插件只能在 manifest.permissions 声明的能力内行事。

    支持三种写法：
      "message.send"          精确
      "napcat.read"           分组
      "napcat.*" / "*"        通配（前缀通配，如 napcat.* 允许 napcat.read）
    例外：前缀通配**不覆盖高危能力**（napcat.raw 必须显式声明），
         全量 "*" 仍可覆盖（仅内置/可信插件使用）。
    manifest 为空（旧插件/未声明）时一律拒绝——安全默认。
    """
    perms = list(getattr(manifest, "permissions", None) or [])
    if not perms:
        return False
    if perm in perms or "*" in perms:
        return True
    leaf = perm.rsplit(".", 1)[-1]
    for p in perms:
        if not (p.endswith(".*") and perm.startswith(p[:-1])):
            continue
        if leaf in _HIGH_RISK_LEAVES:      # 高危能力：通配不给，必须显式声明
            continue
        return True
    return False


class PluginMessage:
    """消息能力：发送 / 回复 / 撤回。走 services.sender（底层 NapCat WS）。"""

    def __init__(self, plugin_name: str, manifest=None):
        self._plugin = plugin_name
        self._manifest = manifest

    async def send(self, text: str, chat_id: int, is_group: bool = True) -> bool:
        """发送文本消息到群聊/私聊。失败返回 False（不抛异常）。"""
        try:
            from core.plugin.kook_compat import strip_kook_text
            from services.sender import send_by_chat_type
            await send_by_chat_type(
                str(strip_kook_text(text)), chat_id, is_group,
                user_id=chat_id if not is_group else None,
            )
            return True
        except Exception as e:
            logger.warning("Plugin %s 发送失败: %s", self._plugin, e)
            return False

    async def send_file(self, file_path: str, chat_id: int, is_group: bool = True) -> bool:
        """发送本地文件到群聊/私聊。失败返回 False（不抛异常）。"""
        try:
            from services.sender import send_file as _send_file
            return bool(await _send_file(file_path, chat_id, is_group))
        except Exception as e:
            logger.warning("Plugin %s 发送文件失败: %s", self._plugin, e)
            return False

    # ── v2.3.82：需要 message_id 的场景（撤回二维码、编辑提示等） ───────────
    # 原有 send/send_file 只回 bool，插件拿不到 message_id，导致「发出去就撤不回」。
    # 下面三个方法是"只增不改"：send/send_file 行为与签名一律不动。

    async def send_ex(self, text: str, chat_id: int, is_group: bool = True) -> Optional[dict]:
        """发送文本并返回 NapCat 原始响应（含 data.message_id）。失败返回 None。"""
        if not _perm_ok(self._manifest, "message.send"):
            logger.warning("Plugin %s 未声明 message.send，拒绝发送", self._plugin)
            return None
        try:
            from core.plugin.kook_compat import strip_kook_text
            from services.sender import get_ws_manager, fix_cq_paths
            msg = fix_cq_paths(str(strip_kook_text(text)))
            params = {"message": msg}
            params["group_id" if is_group else "user_id"] = chat_id
            action = "send_group_msg" if is_group else "send_private_msg"
            return await get_ws_manager().call_api(action, params, timeout=8.0)
        except Exception as e:
            logger.warning("Plugin %s send_ex 失败: %s", self._plugin, e)
            return None

    async def send_file_ex(self, file_path: str, chat_id: int, is_group: bool = True) -> Optional[dict]:
        """发送图片/文件并尽量返回含 message_id 的响应（图片走 CQ，文件走 upload）。"""
        if not _perm_ok(self._manifest, "message.send"):
            logger.warning("Plugin %s 未声明 message.send，拒绝发送", self._plugin)
            return None
        try:
            from pathlib import Path as _P
            from services.sender import get_ws_manager
            p = _P(file_path)
            cq = f"[CQ:image,file={p.resolve().as_uri()}]"
            params = {"message": cq}
            params["group_id" if is_group else "user_id"] = chat_id
            action = "send_group_msg" if is_group else "send_private_msg"
            return await get_ws_manager().call_api(action, params, timeout=30.0)
        except Exception as e:
            logger.warning("Plugin %s send_file_ex 失败: %s", self._plugin, e)
            return None

    async def delete(self, message_id) -> bool:
        """撤回一条消息（NapCat delete_msg）。需要权限 message.delete。"""
        if not _perm_ok(self._manifest, "message.delete"):
            logger.warning("Plugin %s 未声明 message.delete，拒绝撤回", self._plugin)
            return False
        try:
            mid = int(message_id)
        except (TypeError, ValueError):
            logger.warning("Plugin %s delete 收到非法 message_id=%r", self._plugin, message_id)
            return False
        try:
            from services.sender import get_ws_manager
            resp = await get_ws_manager().call_api("delete_msg", {"message_id": mid}, timeout=8.0)
            ok = bool(resp) and str(resp.get("status", "")).lower() in ("ok", "", "failed")
            if not ok:
                logger.warning("Plugin %s 撤回失败 msg_id=%s resp=%s", self._plugin, mid, resp)
            return ok
        except Exception as e:
            logger.warning("Plugin %s delete 异常 msg_id=%s: %s", self._plugin, mid, e)
            return False

    @staticmethod
    def message_id_of(resp: Optional[dict]):
        """从 send_ex/send_file_ex 的响应里取 message_id，取不到返回 None。"""
        if not isinstance(resp, dict):
            return None
        data = resp.get("data")
        if isinstance(data, dict) and data.get("message_id") is not None:
            return data.get("message_id")
        return None


class PluginMemory:
    """记忆能力：异步写入（不阻塞） + 检索。"""

    def __init__(self, plugin_name: str):
        self._plugin = plugin_name

    async def remember(self, content: str, memory_type: str = "knowledge",
                       chat_id: int = 0) -> None:
        """写入一条长期记忆（异步，不阻塞响应）。"""
        try:
            from modules.memory import append_memory
            from datetime import datetime
            line = f"- [plugin] {content} ({datetime.now().strftime('%Y-%m-%d')})"
            append_memory(chat_id, line)
        except Exception as e:
            logger.warning("Plugin %s 记忆写入降级: %s", self._plugin, e)

    async def recall(self, query: str, chat_id: Optional[int] = None, limit: int = 5) -> list:
        """检索记忆，返回结构化列表（不可用返回空）。优先走 SQLite 检索层。"""
        try:
            from db.store import get_search_store
            store = get_search_store()
            if store.available:
                return store.search_memory(query, chat_id=chat_id, limit=limit)
        except Exception:
            pass
        try:
            from modules.memory import search_long_memory
            cid = chat_id or 0
            text = search_long_memory(cid, query, limit=limit)
            if text and "未找到" not in text:
                return [l for l in text.split("\n") if l.strip()]
        except Exception as e:
            logger.warning("Plugin %s 记忆检索降级: %s", self._plugin, e)
        return []


class PluginEvent:
    """事件能力：订阅 / 发布。reload/unload 时自动清理。"""

    def __init__(self, plugin_name: str, bus: EventBus):
        self._plugin = plugin_name
        self._bus = bus
        self._handlers: list[tuple[str, Callable]] = []

    def on(self, event_name: str):
        """装饰器订阅事件。"""
        def deco(fn):
            self._handlers.append((event_name, fn))
            self._bus.subscribe(event_name, fn)
            return fn
        return deco

    def subscribe(self, event_name: str, handler: Callable) -> None:
        self._handlers.append((event_name, handler))
        self._bus.subscribe(event_name, handler)

    async def publish(self, event_name: str, data: Optional[dict] = None) -> None:
        await self._bus.publish(event_name, data)

    def clear(self) -> None:
        for name, h in self._handlers:
            self._bus.unsubscribe(name, h)
        self._handlers.clear()


class PluginTimer:
    """定时器能力：注册周期任务。reload/unload 自动取消，防止热更新后重复执行。"""

    def __init__(self, plugin_name: str):
        self._plugin = plugin_name
        self._tasks: list[asyncio.Task] = []

    def every(self, seconds: float):
        """装饰器：每 seconds 秒执行一次。"""
        def deco(fn):
            async def _loop():
                while True:
                    try:
                        await asyncio.sleep(seconds)
                        await fn()
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.warning("Plugin %s 定时器任务异常: %s", self._plugin, e)
            task = asyncio.create_task(_loop())
            self._tasks.append(task)
            return fn
        return deco

    def cancel_all(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()


def _emit_cap_event(kind: str, cap) -> None:
    """能力注册/注销事件（★ v2.3.102）。事件只做通知，权限授予不走事件。"""
    try:
        from core.eventbus import emit_event
        emit_event(f"capability.{kind}", {
            "capability_id": cap.id, "name": cap.name,
            "category": cap.category, "runtime": cap.runtime,
            "permissions": list(cap.permissions or []),
        }, source="core.plugin", plugin_id=cap.source.split(":", 1)[-1] if cap.source else None)
    except Exception:
        pass


class PluginCapability:
    """能力注册：让插件向 Core 注册 Command/Tool 能力。

    register_command 额外把指令挂进 qqbot 的 COMMAND_MAP（/~name 直接可用），
    卸载时一并移除。handler 推荐签名与 qqbot 指令一致：
        async def handler(args, user_id, group_id, sender_name, is_group, bot_qq) -> str | None
    """

    def __init__(self, plugin_name: str, manifest=None):
        self._plugin = plugin_name
        self._manifest = manifest
        self._registry = get_capability_registry()
        self._registered: list[str] = []
        self._command_names: list[str] = []
        # G1（v2.3.83）：被本插件遮蔽的内置指令 → 卸载时还原，保证"插件禁用后内置指令不消失"
        self._shadowed: dict[str, Callable] = {}

    def register_command(self, name: str, description: str = "",
                         handler: Optional[Callable] = None,
                         permissions: Optional[list[str]] = None) -> None:
        """注册一个命令能力，并挂进 COMMAND_MAP（/~name 可调）。"""
        cap = Capability(
            id=f"plugin.{self._plugin}.{name}",
            name=name,
            description=description,
            category=CATEGORY_COMMAND,
            runtime=RUNTIME_COMMAND,
            permissions=permissions or ["message.read", "message.send"],
            source=f"plugin:{self._plugin}",
        )
        self._registry.register(cap)
        _emit_cap_event("registered", cap)
        if handler is not None:
            self._registry.bind_handler(cap.id, handler)
            self._bind_command(name, handler)
        self._registered.append(cap.id)
        self._command_names.append(name)

    def register_tool(self, name: str, description: str = "",
                      schema: Optional[dict] = None,
                      handler: Optional[Callable] = None,
                      permissions: Optional[list[str]] = None,
                      always_on: bool = False) -> None:
        """注册一个 Function Calling 工具能力，供 LLM 对话时自动发现并调用（无需指令）。

        - schema : 完整 OpenAI 工具定义 {"type":"function","function":{...}}。
          缺省时按 name/description 自动生成一个无参 schema。
        - handler: async (arguments: dict, user_id, group_id, sender_name,
                    is_group, bot_qq) -> str | None，返回自然语言结果文本。
        - always_on: True 时该工具成为核心常驻能力，普通聊天也会把它的 Schema
          交给 LLM，让模型始终知道插件拥有这项能力。
        """
        if not schema:
            schema = {"type": "function", "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": {}, "required": []},
            }}
        # ★ v2.3.101：权限声明收紧（注册资格 / 能力声明 / 执行授权是三件事）
        #   ① 声明了 → 必须是 manifest.permissions 的子集，否则拒绝注册
        #   ② 没声明 + 占用核心工具名 → 拒绝注册
        #   ③ 没声明 + 新工具名 → 放行但告警（迁移期）
        owner_perms = list(getattr(self._manifest, "permissions", None) or [])
        declared = permissions is not None and len(permissions) > 0
        if declared:
            need = [str(x) for x in permissions]
            missing = [x for x in need if x not in owner_perms]
            if missing:
                logger.error(
                    "插件 %s 拒绝注册工具 %s：所需权限 %s 不在 manifest.permissions 内（已授权: %s）",
                    self._plugin, name, missing, owner_perms)
                return
        else:
            need = ["message.read", "message.send"]
            if _is_core_tool_name(name):
                logger.error(
                    "插件 %s 拒绝注册工具 %s：未声明 permissions 却占用了核心工具名，"
                    "如需接管必须显式声明所需权限", self._plugin, name)
                return
            logger.warning(
                "插件 %s 注册工具 %s 未声明 permissions（迁移期按 %s 放行，请补声明）",
                self._plugin, name, need)
        cap = Capability(
            id=f"plugin.{self._plugin}.{name}",
            name=name,
            description=description or schema.get("function", {}).get("description", ""),
            category=CATEGORY_TOOL,
            runtime=RUNTIME_FC,
            permissions=need,
            source=f"plugin:{self._plugin}",
            always_on=always_on,
            perm_declared=declared,
            owner_permissions=owner_perms,
        )
        self._registry.register(cap)
        _emit_cap_event("registered", cap)
        self._registry.bind_tool_schema(cap.id, schema)
        if handler is not None:
            self._registry.bind_handler(cap.id, handler)
        self._registered.append(cap.id)

    def _bind_command(self, name: str, handler: Callable) -> None:
        """把插件命令挂进 qqbot COMMAND_MAP（/~name 直接可调）。

        兼容两种 handler 签名：
        - qqbot 风格: async (args: list, user_id, group_id, sender_name, is_group, bot_qq)
        - KOOK 风格:  async (msg: dict)  msg 含 args/author/sender/chat_id/is_group/mentions/quote_id
        """
        try:
            from modules.commands import COMMAND_MAP
            async def _bridge(args, user_id, group_id, sender_name, is_group, bot_qq, raw_message=""):
                async def _run() -> Any:
                    try:
                        # ★ G8（v2.3.85）：qqbot 风格 handler 若显式声明 raw_message 形参，
                        #   则回传原始 CQ 消息（agnes 的 /~draw 引用图、/~img2video 图生视频需要）。
                        #   仅"签名里真有该形参"时才注入 —— 其余 handler 行为与从前完全一致。
                        _kw: dict = {}
                        try:
                            import inspect as _inspect
                            if "raw_message" in _inspect.signature(handler).parameters:
                                _kw["raw_message"] = raw_message
                        except (TypeError, ValueError):
                            pass
                        return await handler(args, user_id, group_id, sender_name, is_group, bot_qq, **_kw)
                    except TypeError:
                        try:
                            # KOOK 风格：handler 只收一个 msg 字典
                            # ★ 从 raw_message(CQ码/array格式) 解析 @的QQ列表 mentions 和 引用消息id quote_id
                            mentions = []
                            quote_id = None
                            if raw_message:
                                import re as _re
                                import json as _json
                                for m in _re.finditer(r'\[CQ:at,qq=(\d+)\]', raw_message):
                                    if m.group(1) not in mentions:
                                        mentions.append(m.group(1))
                                # array 格式: [{"type":"at","data":{"qq":"123"}}, ...]
                                if not mentions:
                                    for m in _re.finditer(r'"type"\s*:\s*"at"\s*,\s*"data"\s*:\s*\{[^}]*"qq"\s*:\s*"?(\d+)"?', raw_message):
                                        if m.group(1) not in mentions:
                                            mentions.append(m.group(1))
                                qm = _re.search(r'"message_id":(\d+)', raw_message) or \
                                     _re.search(r'\[CQ:reply,id=(\d+)\]', raw_message) or \
                                     _re.search(r'"type"\s*:\s*"reply"\s*,\s*"data"\s*:\s*\{[^}]*"id"\s*:\s*"?(\d+)"?', raw_message)
                                if qm:
                                    quote_id = qm.group(1)
                            logger.debug("插件bridge [%s] mentions=%s quote_id=%s raw=%.120s",
                                         self._plugin, mentions, quote_id, raw_message)
                            return await handler({
                                "args": list(args or []),
                                "author": user_id,
                                "sender": sender_name,
                                "chat_id": group_id if is_group else user_id,
                                "is_group": bool(is_group),
                                "bot_qq": bot_qq,
                                "raw": " ".join(args or []),
                                "raw_message": raw_message,
                                "mentions": mentions,
                                "quote_id": quote_id,
                            })
                        except TypeError:
                            return await handler()
                try:
                    from core.plugin.kook_compat import strip_kook_text
                    result = await _run()
                    if isinstance(result, str):
                        return strip_kook_text(result)
                    return result
                except Exception as e:
                    logger.warning("插件 %s 命令 %s 执行失败: %s", self._plugin, name, e)
                    return None
            # 标注来源：日志/调试用，handle_command 检测 __plugin__ 显示插件名
            _bridge.__plugin__ = self._plugin
            _bridge.__cmd_name__ = name
            # G1（v2.3.83）：若该名已被"非插件"指令占用（内置模块），先记下原值，
            # 卸载/禁用时还原 —— 否则 unregister_all 的 pop 会把内置指令一起删掉。
            prev = COMMAND_MAP.get(name)
            if prev is not None and getattr(prev, "__plugin__", None) is None:
                self._shadowed[name] = prev
                logger.warning("插件 %s 的命令 %s 遮蔽了内置指令，卸载时将自动还原",
                               self._plugin, name)
            COMMAND_MAP[name] = _bridge
            # G7（v2.3.83）：manifest 声明 limits.heavy=true 的命令登记为重命令，
            # 保持"重命令走旁路并发、不占用同群 worker"的原有语义。
            try:
                if isinstance(getattr(self._manifest, "limits", None), dict) \
                        and self._manifest.limits.get("heavy"):
                    from modules.commands import EXTRA_HEAVY_COMMANDS
                    EXTRA_HEAVY_COMMANDS.add(name)
            except Exception:
                pass
        except Exception as e:
            logger.warning("插件 %s 命令 %s 挂接 COMMAND_MAP 失败: %s", self._plugin, name, e)

    def unregister_all(self) -> None:
        for cid in self._registered:
            # ★ v2.3.102：注销事件（拿不到 cap 对象时跳过，不影响注销）
            try:
                from core.capability.registry import get_capability_registry as _gcr
                _cap = _gcr()._caps.get(cid)
                if _cap is not None:
                    _emit_cap_event("unregistered", _cap)
            except Exception:
                pass
            self._registry.unregister(cid)
        # 从 COMMAND_MAP 移除挂接的命令（★ 只移除仍属于本插件的 bridge）
        try:
            from modules.commands import COMMAND_MAP
            for name in self._command_names:
                cur = COMMAND_MAP.get(name)
                if getattr(cur, "__plugin__", None) != self._plugin:
                    continue                       # 已被他人接管 → 不动
                if name in self._shadowed:
                    COMMAND_MAP[name] = self._shadowed.pop(name)   # 还原被遮蔽的内置指令
                else:
                    COMMAND_MAP.pop(name, None)
            self._shadowed.clear()
        except Exception:
            pass
        # G7：撤销重命令登记
        try:
            from modules.commands import EXTRA_HEAVY_COMMANDS
            for name in self._command_names:
                EXTRA_HEAVY_COMMANDS.discard(name)
        except Exception:
            pass
        self._registered.clear()
        self._command_names.clear()


class PluginImage:
    """图片提取能力：从 QQ CQ 码 / 文本里拿图片 URL。"""

    _CQ_IMG_RE = re.compile(r'\[CQ:image[^\]]*url=([^,\]]+)')

    @staticmethod
    def extract_cq_images(text: str) -> list[str]:
        """从 QQ CQ 码文本提取所有图片 URL。"""
        return [m for m in PluginImage._CQ_IMG_RE.findall(text or "") if m]

    @staticmethod
    def extract_urls(text: str) -> list[str]:
        """从文本提取裸 http(s) URL（兼容反引号 / Markdown [text](url) 包裹）。"""
        return [m.rstrip(")") for m in re.findall(r"https?://[^\s)\]>\uFF09]+", text or "")]

    @staticmethod
    async def fetch_user_avatar(user_id: int) -> str:
        """按 QQ 号返回头像 URL。

        QQ 头像有固定公开 URL：https://q1.qlogo.cn/g?b=qq&nk=<QQ号>&s=640
        无需调 API（get_stranger_info 不返回头像字段）。先试 s=640 高清，
        再试 s=100（部分号段 640 无图时 100 有）。
        """
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return ""
        base = "https://q1.qlogo.cn/g?b=qq&nk={}&s={}"
        # 640 是标准高清头像，QQ 客户端通用；失败场景极少，直接返回 640
        return base.format(uid, 640)

    @staticmethod
    async def fetch_quote_images(message_id) -> list[str]:
        """获取被引用消息中的图片 URL 列表（供插件取原图，不调视觉模型）。

        motou 等 KOOK 移植插件依赖此方法；QQ 端用 NapCat get_msg 按 message_id
        从 message 段 + CQ 码两级提取，与 dispatcher._fetch_quoted_image 同源。
        失败/无图返回空列表。
        """
        import re
        try:
            from services.sender import get_ws_manager
            mgr = get_ws_manager()
            data = await mgr.call_api("get_msg", {"message_id": int(message_id)})
            if not data:
                return []
            urls: list[str] = []
            msg_segments = data.get("message", [])
            if isinstance(msg_segments, list):
                for seg in msg_segments:
                    if seg.get("type") == "image":
                        u = (seg.get("data") or {}).get("url", "")
                        if u and u not in urls:
                            urls.append(u)
            if not urls:
                raw = data.get("raw_message", "") or ""
                for m in re.finditer(r'\[CQ:image[^\]]*url=([^,\]]+)', raw):
                    u = m.group(1)
                    if u and u not in urls:
                        urls.append(u)
            return urls
        except Exception as e:
            logger.warning("PluginImage 获取引用图片失败 %s: %s", message_id, e)
            return []

    # ── G5（v2.3.83）：卡片渲染能力（薄封装 modules.changelog，插件不必直接 import 渲染层） ──
    @staticmethod
    async def render_html(full_html: str, filename: Optional[str] = None,
                          width: int = 800, html_file: Optional[str] = None) -> Optional[str]:
        """任意完整 HTML → Playwright 截图 → 返回图片绝对路径（失败返回 None）。

        html_file: 页面引用了本地 ``file://`` 图片时必须传（走 goto(file://)，
                   否则 set_content 的 about:blank 源会被 Chromium 拒绝加载本地子资源）。
        """
        try:
            from modules.changelog import render_card_to_image
            return await render_card_to_image(full_html, filename, width=width, html_file=html_file)
        except Exception as e:
            logger.warning("PluginImage.render_html 失败: %s", e)
            return None


class PluginVision:
    """图片识别能力：用视觉 LLM 把图片内容描述成文字。"""

    def __init__(self, plugin_name: str):
        self._plugin = plugin_name

    async def describe(self, image_url: str, chat_id: int = 0) -> str:
        """识别图片内容，返回文字描述（视觉模型关闭/失败时返回空串）。"""
        try:
            from core.config import get_config
            from services.image_api import recognize_image
            cfg = get_config()
            im = getattr(cfg, "image_model", None)
            if im is None or not getattr(im, "switch", False):
                return ""
            return (await recognize_image(image_url, im, chat_id=chat_id) or "").strip()
        except Exception as e:
            logger.warning("Plugin %s 图片识别失败: %s", self._plugin, e)
        return ""


class PluginIdentity:
    """身份 / 权限判定：is_admin / is_op 走 core.config 全局权限。"""

    @staticmethod
    def is_admin(user_id, group_id: int = 0) -> bool:
        try:
            from core.config import get_config
            return bool(get_config().is_admin(user_id, group_id))
        except Exception as e:
            logger.warning("PluginIdentity.is_admin 失败: %s", e)
            return False

    @staticmethod
    def is_op(user_id) -> bool:
        """全局 OP（roles 配置里的 op 名单，与 is_admin 相互独立）。"""
        try:
            from core.config import get_config
            cfg = get_config()
            fn = getattr(cfg, "is_op", None)
            return bool(fn(user_id)) if callable(fn) else False
        except Exception as e:
            logger.warning("PluginIdentity.is_op 失败: %s", e)
            return False


class PluginLLM:
    """文本生成：走 services.llm.call_llm，默认用机器人回复主模型 reply_model。"""

    def __init__(self, plugin_name: str):
        self._plugin = plugin_name

    async def generate(self, messages: list[dict], temperature: float = 0.3,
                       timeout: float = 60.0) -> str:
        """生成文本。messages 为 [{role,content},...]，缺省模型=reply_model，返回原始文本。"""
        try:
            from core.config import get_config
            from services.llm import call_llm
            model_cfg = getattr(get_config(), "reply_model", None)
            if model_cfg is None:
                logger.warning("Plugin %s llm.generate: 未配置 reply_model", self._plugin)
                return ""
            return (await call_llm(model_cfg, messages,
                                   temperature=temperature, timeout=timeout) or "").strip()
        except Exception as e:
            logger.warning("Plugin %s llm.generate 失败: %s", self._plugin, e)
            return ""


class PluginI18n:
    """i18n 能力：读 config/lang.toml（与内置模块同一份），插件不直接 import utils.format_lang。

    · get("weather.searching")            → lang.toml 里的文案（缺失返回 default 或 [LANG:key]）
    · get("box.header", cp_name="顺丰")   → 同时替换 ${cp_name} 占位符
    · has("weather.searching")            → 判断某 key 是否存在
    """

    def __init__(self, plugin_name: str):
        self._plugin = plugin_name

    def get(self, key_path: str, default: Optional[str] = None, **fmt) -> str:
        try:
            from utils.format_lang import format_lang
            data = format_lang(key_path)          # 缺失时 format_lang 返回 "[LANG:key]"
            if data.startswith("[LANG:") and default is not None:
                data = default
            for k, v in fmt.items():
                data = data.replace("${" + k + "}", str(v))
            return data
        except Exception as e:
            logger.warning("Plugin %s i18n.get(%s) 失败: %s", self._plugin, key_path, e)
            return default if default is not None else ""

    def has(self, key_path: str) -> bool:
        try:
            from utils.format_lang import has_key
            return bool(has_key(key_path))
        except Exception:
            return False


# ── 人工审批：私聊管理员 + 内存 token 池 ─────────────────────
_approval_pending: dict[str, str] = {}      # token → 描述
_approval_results: dict[str, bool] = {}     # token → 是否放行


def resolve_approval(token: str, approved: bool) -> str:
    """回传审批结果（/~apy 指令调用）。返回提示文本。"""
    if token in _approval_pending:
        _approval_results[token] = approved
        return "已放行" if approved else "已拒绝"
    return "审批 token 不存在或已过期"


class PluginApproval:
    """人工审批：申请 → 私聊管理员确认 → 放行/拒绝。"""

    def __init__(self, plugin_name: str):
        self._plugin = plugin_name

    async def request(self, plan_desc: str, timeout: float = 120.0) -> bool:
        """发起审批：私聊管理员，等待确认；放行返回 True，取消/超时返回 False。"""
        try:
            from core.config import get_config
            from services.sender import send_private_msg
            cfg = get_config()
            admin_qq = getattr(cfg, "admin_qq", 0)
            if not admin_qq:
                logger.warning("Plugin %s approval.request: 未配置 admin_qq", self._plugin)
                return False
            token = secrets.token_hex(6)
            _approval_pending[token] = plan_desc
            _approval_results.pop(token, None)
            msg = (f"【插件审批请求】\n插件: {self._plugin}\n请求: {plan_desc}\n"
                   f"回复 /~apy {token} 同意 或 /~apy {token} 拒绝")
            await send_private_msg(msg, admin_qq)
            deadline = time.time() + timeout
            while time.time() < deadline:
                if token in _approval_results:
                    _approval_pending.pop(token, None)
                    return bool(_approval_results.pop(token))
                await asyncio.sleep(1.0)
            _approval_pending.pop(token, None)
            return False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Plugin %s approval.request 失败: %s", self._plugin, e)
            return False

    def resolve(self, token: str, approved: bool) -> str:
        return resolve_approval(token, approved)


class PluginSandbox:
    """沙箱真实执行：py/cpp/shell + 产物收集 + 清理，走 core.sandbox（惰性导入）。"""

    @staticmethod
    async def run_python(code: str, **kwargs) -> str:
        from core import sandbox
        return await sandbox.run_python_str(code, **kwargs)

    @staticmethod
    async def run_cpp(files: dict, **kwargs) -> str:
        from core import sandbox
        return await sandbox.compile_and_run_cpp_str(files, **kwargs)

    @staticmethod
    async def run_shell(command: str, **kwargs) -> str:
        from core import sandbox
        return await sandbox.run_shell_str(command, **kwargs)

    @staticmethod
    def collect_artifacts(tmp_dir) -> list:
        from core import sandbox
        return sandbox.collect_artifacts_str(tmp_dir)

    @staticmethod
    def cleanup(tmp_dir) -> None:
        from core import sandbox
        sandbox.cleanup(tmp_dir)


class PluginPipeline:
    """消息管道钩子：允许插件在消息处理流程中插入逻辑。

    Hook 类型：
    - on_message: 收到消息后、管道处理前调用。返回 str 可跳过后续管道直接回复。
    - on_reply: 管道生成回复后、发送前调用。可修改/替换/拦截回复。
    """

    def __init__(self, plugin_name: str):
        self._plugin = plugin_name
        self._pre_hooks: list[Callable] = []
        self._post_hooks: list[Callable] = []
        _get_hook_registry()._register_plugin(plugin_name, self)

    def on_message(self, fn: Callable):
        """注册消息预处理钩子。fn(msg_dict) -> str|None。
        返回非 None 字符串则跳过管道直接作为回复发送。"""
        self._pre_hooks.append(fn)
        return fn

    def on_reply(self, fn: Callable):
        """注册回复后处理钩子。fn(reply_text, msg_dict) -> str|None。
        返回 None 保持原回复；返回空字符串拦截不发送；返回其他字符串替换原回复。"""
        self._post_hooks.append(fn)
        return fn

    def get_pre_hooks(self) -> list:
        return list(self._pre_hooks)

    def get_post_hooks(self) -> list:
        return list(self._post_hooks)

    def clear(self) -> None:
        self._pre_hooks.clear()
        self._post_hooks.clear()
        _get_hook_registry()._unregister_plugin(self._plugin)


class PluginBackground:
    """后台任务注册：允许插件注册长时间运行的后台协程。

    用法：在 on_enable 中调用 ctx.background.add(my_coro())，
    在 on_disable/on_unload 时自动取消所有已注册任务。
    """

    def __init__(self, plugin_name: str):
        self._plugin = plugin_name
        self._tasks: list[asyncio.Task] = []

    def add(self, coro) -> asyncio.Task:
        """注册一个后台协程，返回 asyncio.Task。插件卸载时自动取消。"""
        async def _wrapper():
            try:
                await coro
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("Plugin %s 后台任务异常: %s", self._plugin, e)
        task = asyncio.create_task(_wrapper())
        self._tasks.append(task)
        return task

    def cancel_all(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()


class _HookRegistry:
    """全局管道钩子注册表（单例）。pipeline.py 从此查询所有插件的钩子。"""

    def __init__(self):
        self._plugins: dict[str, "PluginPipeline"] = {}

    def _register_plugin(self, name: str, pipeline: "PluginPipeline") -> None:
        self._plugins[name] = pipeline

    def _unregister_plugin(self, name: str) -> None:
        self._plugins.pop(name, None)

    def all_pre_hooks(self) -> list[Callable]:
        hooks: list[Callable] = []
        for p in self._plugins.values():
            hooks.extend(p.get_pre_hooks())
        return hooks

    def all_post_hooks(self) -> list[Callable]:
        hooks: list[Callable] = []
        for p in self._plugins.values():
            hooks.extend(p.get_post_hooks())
        return hooks


_hook_registry: Optional[_HookRegistry] = None


def _get_hook_registry() -> _HookRegistry:
    global _hook_registry
    if _hook_registry is None:
        _hook_registry = _HookRegistry()
    return _hook_registry


def get_pipeline_hooks() -> _HookRegistry:
    """供 pipeline.py 获取全局钩子注册表。"""
    return _get_hook_registry()


class PluginNapCat:
    """NapCat（OneBot v11）端点直调 —— 受白名单 + manifest 权限双重约束。

    设计意图（对应"插件能用什么 = 平台暴露什么"）：把 NapCat 的 HTTP/WS API 面
    以"能力"形式开放给插件，但**不做无差别透传**：
      · 只放行白名单里的 action（未知 action 一律拒绝）
      · 每个 action 绑定一个权限名，插件必须在 manifest.permissions 里声明
      · 需要完全自由调用时声明 napcat.raw（高危，建议仅内置/可信插件使用）

    权限分组：
      napcat.read    读类：群/好友/成员信息、取消息、取图、OCR 等
      napcat.send    发类：send_msg / send_group_msg / send_private_msg / delete_msg
      napcat.admin   管类：禁言、改名片、踢人、退群、审批等
      napcat.raw     任意 action（绕过白名单，仍走同一鉴权管道）
    """

    READ_ACTIONS = frozenset({
        "get_login_info", "get_status", "get_version_info", "get_group_list",
        "get_group_info", "get_group_member_info", "get_group_member_list",
        "get_friend_list", "get_stranger_info", "get_group_honor_info",
        "get_msg", "get_image", "get_record", "get_forward_msg",
        "can_send_image", "can_send_record", "ocr_image",
    })
    SEND_ACTIONS = frozenset({
        "send_msg", "send_group_msg", "send_private_msg", "delete_msg",
    })
    ADMIN_ACTIONS = frozenset({
        "set_group_ban", "set_group_whole_ban", "set_group_card", "set_group_kick",
        "set_group_leave", "set_group_name", "set_group_add_request",
        "set_friend_add_request", "set_essence_msg", "delete_essence_msg",
        "set_group_special_title", "set_group_admin",
    })

    def __init__(self, plugin_name: str, manifest=None):
        self._plugin = plugin_name
        self._manifest = manifest

    @classmethod
    def required_permission(cls, action: str) -> Optional[str]:
        if action in cls.READ_ACTIONS:
            return "napcat.read"
        if action in cls.SEND_ACTIONS:
            return "napcat.send"
        if action in cls.ADMIN_ACTIONS:
            return "napcat.admin"
        return None

    @property
    def connected(self) -> bool:
        try:
            from services.sender import get_ws_manager
            return bool(getattr(get_ws_manager(), "_ws", None))
        except Exception:
            return False

    async def call(self, action: str, params: Optional[dict] = None) -> Optional[dict]:
        """调用白名单内的 NapCat action。未授权/未知 action 返回 None 并记日志。"""
        need = self.required_permission(action)
        if need is None:
            logger.warning("Plugin %s 调用了白名单外的 action=%s（需 napcat.raw）",
                           self._plugin, action)
            return None
        if not _perm_ok(self._manifest, need):
            logger.warning("Plugin %s 未声明 %s，拒绝 action=%s", self._plugin, need, action)
            return None
        try:
            from services.sender import get_ws_manager
            return await get_ws_manager().call_api(action, params or {}, timeout=8.0)
        except Exception as e:
            logger.warning("Plugin %s napcat.call(%s) 异常: %s", self._plugin, action, e)
            return None

    async def raw(self, action: str, params: Optional[dict] = None) -> Optional[dict]:
        """任意 action（需 napcat.raw）。仅建议内置/可信插件使用。"""
        if not _perm_ok(self._manifest, "napcat.raw"):
            logger.warning("Plugin %s 未声明 napcat.raw，拒绝 raw(%s)", self._plugin, action)
            return None
        try:
            from services.sender import get_ws_manager
            return await get_ws_manager().call_api(action, params or {}, timeout=8.0)
        except Exception as e:
            logger.warning("Plugin %s napcat.raw(%s) 异常: %s", self._plugin, action, e)
            return None


class PluginFS:
    """受限文件系统：插件只能读写自己的数据目录 data/plugins/<插件名>/。

    · 路径越界（../、绝对路径指向根外、符号链接逃逸）一律拒绝
    · 写类操作（write/append/remove/mkdir）需要 manifest 声明 fs.write
    · 读类操作（read/list/exists/stat）需要声明 fs.read
    这是"Python 侧沙箱"的第一步：不做进程隔离，但把可触达范围收敛到插件自己的目录。
    """

    def __init__(self, plugin_name: str, manifest=None, root: Optional[str] = None):
        self._plugin = plugin_name
        self._manifest = manifest
        self._root = Path(root) if root else (Path("data") / "plugins" / plugin_name)

    @property
    def root(self) -> Path:
        return self._root.resolve()

    def path(self, rel: str = "") -> Path:
        """把插件给的相对路径解析到沙箱内；越界抛 ValueError。"""
        base = self.root
        p = (base / str(rel).lstrip("/\\")).resolve() if rel else base
        if p != base and base not in p.parents:
            raise ValueError(f"路径越界（仅允许插件数据目录内）：{rel}")
        return p

    def _need(self, perm: str) -> bool:
        if not _perm_ok(self._manifest, perm):
            logger.warning("Plugin %s 未声明 %s，拒绝文件操作", self._plugin, perm)
            return False
        return True

    def read_text(self, rel: str, encoding: str = "utf-8") -> Optional[str]:
        if not self._need("fs.read"):
            return None
        try:
            return self.path(rel).read_text(encoding=encoding)
        except Exception as e:
            logger.warning("Plugin %s fs.read_text(%s) 失败: %s", self._plugin, rel, e)
            return None

    def read_bytes(self, rel: str) -> Optional[bytes]:
        if not self._need("fs.read"):
            return None
        try:
            return self.path(rel).read_bytes()
        except Exception as e:
            logger.warning("Plugin %s fs.read_bytes(%s) 失败: %s", self._plugin, rel, e)
            return None

    def write_text(self, rel: str, data: str, encoding: str = "utf-8") -> bool:
        if not self._need("fs.write"):
            return False
        try:
            p = self.path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data, encoding=encoding)
            return True
        except Exception as e:
            logger.warning("Plugin %s fs.write_text(%s) 失败: %s", self._plugin, rel, e)
            return False

    def write_bytes(self, rel: str, data: bytes) -> bool:
        if not self._need("fs.write"):
            return False
        try:
            p = self.path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            return True
        except Exception as e:
            logger.warning("Plugin %s fs.write_bytes(%s) 失败: %s", self._plugin, rel, e)
            return False

    def listdir(self, rel: str = "") -> list[str]:
        if not self._need("fs.read"):
            return []
        try:
            return sorted(p.name for p in self.path(rel).iterdir())
        except Exception as e:
            logger.warning("Plugin %s fs.listdir(%s) 失败: %s", self._plugin, rel, e)
            return []

    def exists(self, rel: str) -> bool:
        if not self._need("fs.read"):
            return False
        try:
            return self.path(rel).exists()
        except Exception:
            return False

    def remove(self, rel: str) -> bool:
        if not self._need("fs.write"):
            return False
        try:
            p = self.path(rel)
            if p.is_dir():
                import shutil as _sh
                _sh.rmtree(p)
            elif p.exists():
                p.unlink()
            return True
        except Exception as e:
            logger.warning("Plugin %s fs.remove(%s) 失败: %s", self._plugin, rel, e)
            return False


class PluginPlugins:
    """插件间协作（v2.3.84）：查询其它插件的加载状态。

    用于软依赖（manifest.requires.optional_plugins / extra.optional）：
    本插件可先 `ctx.plugins.has("points")` 判断邻居在不在，再决定走哪条分支，
    而不是直接 import 对方代码（那会破坏"可拆"）。

    · loaded() -> 已启用插件名列表
    · has(name) -> 该插件是否已启用
    """

    def __init__(self, plugin_name: str):
        self._plugin = plugin_name

    def loaded(self) -> list[str]:
        try:
            from core.plugin.manager import get_plugin_manager
            return [r["name"] for r in get_plugin_manager().list()
                    if r.get("state") == "enabled"]
        except Exception as e:
            logger.warning("Plugin %s ctx.plugins.loaded 失败: %s", self._plugin, e)
            return []

    def has(self, name: str) -> bool:
        return str(name) in self.loaded()


class PluginContext:
    """插件上下文：插件唯一的 API 入口。"""

    def __init__(self, plugin_name: str, manifest, bus: Optional[EventBus] = None):
        self.name = plugin_name
        self.manifest = manifest
        self.bus = bus or get_event_bus()
        self.message = PluginMessage(plugin_name, manifest)
        self.napcat = PluginNapCat(plugin_name, manifest)
        self.fs = PluginFS(plugin_name, manifest)
        self.memory = PluginMemory(plugin_name)
        self.event = PluginEvent(plugin_name, self.bus)
        self.timer = PluginTimer(plugin_name)
        self.capability = PluginCapability(plugin_name, manifest)
        self.pipeline = PluginPipeline(plugin_name)
        self.background = PluginBackground(plugin_name)
        self.image = PluginImage()
        self.vision = PluginVision(plugin_name)
        self.identity = PluginIdentity()
        self.llm = PluginLLM(plugin_name)
        self.i18n = PluginI18n(plugin_name)
        self.plugins = PluginPlugins(plugin_name)
        self.approval = PluginApproval(plugin_name)
        self.sandbox = PluginSandbox()
        self.logger = get_logger(plugin_name)

    @property
    def economy(self):
        """经济系统（积分/库存）：惰性导入 modules.economy。

        自 v2.0.1 起内置经济已迁移为插件（points/shop），modules.economy 可能不存在，
        此处返回空模块（所有函数 no-op），让旧插件（如 dice 积分奖励）不崩溃。
        """
        try:
            from modules import economy as _m
            return _m
        except ImportError:
            _empty = types.ModuleType("economy_stub")
            def _noop(*a, **k): return None
            for fn in ("get_points", "add_points", "set_points", "transfer_points",
                       "get_inventory", "add_inventory", "consume_inventory",
                       "get_last_sign", "mark_signed", "get_top_points"):
                setattr(_empty, fn, _noop)
            _empty.ITEMS = {}
            return _empty

    def config(self, key: str, default: Any = None) -> Any:
        """读取本插件 manifest.config 里的静态配置。"""
        return self.manifest.config.get(key, default)

    def has(self, perm: str) -> bool:
        """查询本插件是否具备某项能力（依据 manifest.permissions）。

        例：ctx.has("message.delete") / ctx.has("napcat.admin") / ctx.has("fs.write")
        """
        return _perm_ok(self.manifest, perm)

    def cleanup(self) -> None:
        """卸载时清理：事件订阅 + 定时器 + 能力注册，防止热更新后重复执行。"""
        self.event.clear()
        self.timer.cancel_all()
        self.capability.unregister_all()
