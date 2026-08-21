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
from typing import Any, Awaitable, Callable, Optional

from core.logger import get_logger
from core.eventbus import EventBus, get_event_bus
from core.capability import (
    Capability, CATEGORY_COMMAND, CATEGORY_TOOL, RUNTIME_COMMAND, RUNTIME_FC,
    get_capability_registry,
)

logger = get_logger("plugin.api")


class PluginMessage:
    """消息能力：发送 / 回复。走 services.sender。"""

    def __init__(self, plugin_name: str):
        self._plugin = plugin_name

    async def send(self, text: str, chat_id: int, is_group: bool = True) -> bool:
        """发送文本消息到群聊/私聊。失败返回 False（不抛异常）。"""
        try:
            from services.sender import send_by_chat_type
            await send_by_chat_type(
                str(text), chat_id, is_group,
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


class PluginCapability:
    """能力注册：让插件向 Core 注册 Command/Tool 能力。

    register_command 额外把指令挂进 qqbot 的 COMMAND_MAP（/~name 直接可用），
    卸载时一并移除。handler 推荐签名与 qqbot 指令一致：
        async def handler(args, user_id, group_id, sender_name, is_group, bot_qq) -> str | None
    """

    def __init__(self, plugin_name: str):
        self._plugin = plugin_name
        self._registry = get_capability_registry()
        self._registered: list[str] = []
        self._command_names: list[str] = []

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
        cap = Capability(
            id=f"plugin.{self._plugin}.{name}",
            name=name,
            description=description or schema.get("function", {}).get("description", ""),
            category=CATEGORY_TOOL,
            runtime=RUNTIME_FC,
            permissions=permissions or ["message.read", "message.send"],
            source=f"plugin:{self._plugin}",
            always_on=always_on,
        )
        self._registry.register(cap)
        self._registry.bind_tool_schema(cap.id, schema)
        if handler is not None:
            self._registry.bind_handler(cap.id, handler)
        self._registered.append(cap.id)

    def _bind_command(self, name: str, handler: Callable) -> None:
        """把插件命令挂进 qqbot COMMAND_MAP（/~name 直接可调）。"""
        try:
            from modules.commands import COMMAND_MAP
            async def _bridge(args, user_id, group_id, sender_name, is_group, bot_qq):
                try:
                    return await handler(args, user_id, group_id, sender_name, is_group, bot_qq)
                except TypeError:
                    try:
                        return await handler(args)
                    except TypeError:
                        return await handler()
            COMMAND_MAP[name] = _bridge
        except Exception as e:
            logger.warning("插件 %s 命令 %s 挂接 COMMAND_MAP 失败: %s", self._plugin, name, e)

    def unregister_all(self) -> None:
        for cid in self._registered:
            self._registry.unregister(cid)
        # 从 COMMAND_MAP 移除挂接的命令
        try:
            from modules.commands import COMMAND_MAP
            for name in self._command_names:
                COMMAND_MAP.pop(name, None)
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
        """按 QQ 号拉取其头像 URL（失败返回空串）。走 NapCat get_stranger_info。"""
        try:
            from services.sender import get_ws_manager
            data = await get_ws_manager().call_api("get_stranger_info", {"user_id": int(user_id)})
            qq_avatar = (data or {}).get("qlogo_url") or ""
            return str(qq_avatar)
        except Exception as e:
            logger.warning("PluginImage 拉取头像失败 %s: %s", user_id, e)
        return ""


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
    """身份 / 权限判定：is_admin 走 core.config 全局权限。"""

    @staticmethod
    def is_admin(user_id, group_id: int = 0) -> bool:
        try:
            from core.config import get_config
            return bool(get_config().is_admin(user_id, group_id))
        except Exception as e:
            logger.warning("PluginIdentity.is_admin 失败: %s", e)
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
        return await sandbox.run_python(code, **kwargs)

    @staticmethod
    async def run_cpp(files: dict, **kwargs) -> str:
        from core import sandbox
        return await sandbox.compile_and_run_cpp(files, **kwargs)

    @staticmethod
    async def run_shell(command: str, **kwargs) -> str:
        from core import sandbox
        return await sandbox.run_shell(command, **kwargs)

    @staticmethod
    def collect_artifacts(tmp_dir) -> list:
        from core import sandbox
        return sandbox.collect_artifacts(tmp_dir)

    @staticmethod
    def cleanup(tmp_dir) -> None:
        from core import sandbox
        sandbox.cleanup(tmp_dir)


class PluginContext:
    """插件上下文：插件唯一的 API 入口。"""

    def __init__(self, plugin_name: str, manifest, bus: Optional[EventBus] = None):
        self.name = plugin_name
        self.manifest = manifest
        self.bus = bus or get_event_bus()
        self.message = PluginMessage(plugin_name)
        self.memory = PluginMemory(plugin_name)
        self.event = PluginEvent(plugin_name, self.bus)
        self.timer = PluginTimer(plugin_name)
        self.capability = PluginCapability(plugin_name)
        self.image = PluginImage()
        self.vision = PluginVision(plugin_name)
        self.identity = PluginIdentity()
        self.llm = PluginLLM(plugin_name)
        self.approval = PluginApproval(plugin_name)
        self.sandbox = PluginSandbox()
        self.logger = get_logger(plugin_name)

    @property
    def economy(self):
        """经济系统（积分/库存）：惰性导入 modules.economy（与 qqbot 存储一致）。"""
        from modules import economy as _m
        return _m

    def config(self, key: str, default: Any = None) -> Any:
        """读取本插件 manifest.config 里的静态配置。"""
        return self.manifest.config.get(key, default)

    def cleanup(self) -> None:
        """卸载时清理：事件订阅 + 定时器 + 能力注册，防止热更新后重复执行。"""
        self.event.clear()
        self.timer.cancel_all()
        self.capability.unregister_all()
