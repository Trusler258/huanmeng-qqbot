"""
KOOK 兼容层：加载 .hmp 插件时自动剥离 KOOK 专属格式。

背景：插件库（01240820.xyz:20030）里的插件是给 KOOK 机器人（huanmeng-kook-bot）
写的，可能 import `khl`（KOOK SDK）、KOOK 卡片（khl.Card / khl.MessageTypes）等
专属依赖。qqbot 在加载这些 .hmp 插件时，通过本模块向 sys.modules 注入 KOOK 模块
的 stub，让：
- `import khl` / `from khl import ...` 不再 ModuleNotFoundError（插件可加载）；
- KOOK 专属调用（发卡片、khl API）在 stub 上优雅降级（返回空/None），不炸主流程；
- 插件与 qqbot 的真实能力（ctx.message / ctx.vision / ctx.economy 等）仍正常可用。

原理：纯内存注入，不改插件源文件、不落盘。插件卸载不影响（stub 常驻无害）。
"""
from __future__ import annotations

import sys
import types
from typing import Any

# 需要 stub 的 KOOK 专属顶层模块
_KOOK_MODULES: tuple[str, ...] = ("khl", "kook", "kaiheila")

# khl.api 下常见 API 类名（实例化/属性访问均安全返回空）
_API_CLASSES: tuple[str, ...] = (
    "Message", "User", "Channel", "Guild", "Role", "Asset", "Reaction",
    "Game", "Intimacy", "Invite", "GuildRole", "GuildMute", "Badge", "Card",
)

# khl 顶层常见类名
_TOP_CLASSES: tuple[str, ...] = (
    "Message", "Card", "CardMessage", "MessageTypes", "MessageTypes2",
    "Audio", "File", "Markdown", "Section", "Element", "Kmarkdown",
)

_injected = False


class _StubMeta(type):
    """元类：让类属性访问（如 khl.MessageTypes.TEXT）也返回安全 noop。

    普通类只定义实例 __getattr__，`ClassName.ATTR` 的类属性访问不会触发它；
    KOOK 插件常见 `khl.MessageTypes.TEXT` 这类用法，元类 __getattr__ 兜底。
    """

    def __getattr__(cls, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        return _noop_sync


def _make_stub_class(name: str) -> type:
    """创建一个可实例化、任意属性/类属性访问都返回安全 noop 的 stub 类。"""

    class _Stub(metaclass=_StubMeta):
        def __init__(self, *args: Any, **kwargs: Any):
            pass

        def __getattr__(self, item: str) -> Any:
            # 实例方法调用/属性链式访问：返回同步 noop，不产生未 await 的协程
            if item.startswith("_"):
                raise AttributeError(item)
            return _noop_sync

        def __repr__(self) -> str:
            return f"<kook-stub {name}>"

    _Stub.__name__ = name
    # 标记：供 locate_plugin_classes 跳过，避免 stub 类被误判为 Plugin 类
    _Stub._kook_stub = True
    return _Stub


def _noop_sync(*args: Any, **kwargs: Any) -> Any:
    """KOOK stub 方法/函数的默认实现：不做事，安全返回 None。"""
    return None


async def _noop(*args: Any, **kwargs: Any) -> Any:
    """异步 noop（用于 khl.api 的模块级异步函数 stub）。"""
    return None


def install_kook_stubs(force: bool = False) -> None:
    """向 sys.modules 注入 KOOK 专属模块 stub（幂等）。"""
    global _injected
    if _injected and not force:
        return
    _injected = True

    for mod_name in _KOOK_MODULES:
        mod = sys.modules.get(mod_name)
        if mod is None:
            mod = types.ModuleType(mod_name)
            sys.modules[mod_name] = mod
        # 顶层常用类
        for cls in _TOP_CLASSES:
            if not hasattr(mod, cls):
                setattr(mod, cls, _make_stub_class(f"{mod_name}.{cls}"))
        # 常用常量
        if not hasattr(mod, "MessageTypes"):
            setattr(mod, "MessageTypes", _make_stub_class(f"{mod_name}.MessageTypes"))

    # khl.api 子模块（插件常见 from khl import api / from khl.api import Message）
    api_mod = sys.modules.get("khl.api")
    if api_mod is None:
        api_mod = types.ModuleType("khl.api")
        sys.modules["khl.api"] = api_mod
        khl_mod = sys.modules.get("khl")
        if khl_mod is not None:
            khl_mod.api = api_mod
    for cls in _API_CLASSES:
        if not hasattr(api_mod, cls):
            setattr(api_mod, cls, _make_stub_class(f"khl.api.{cls}"))
    if not hasattr(api_mod, "MessageTypes"):
        setattr(api_mod, "MessageTypes", _make_stub_class("khl.api.MessageTypes"))

    # khl.api 下常见模块级函数
    for fn in ("fetch", "gate"):
        if not hasattr(api_mod, fn):
            setattr(api_mod, fn, _noop)


def strip_kook_text(text: str) -> str:
    """剥离文本中的 KOOK 专属格式（KMarkdown / 卡片占位）。

    目前处理：
    - (met)xx(met) 提及
    - (rol)xx(rol) 角色
    - (chn)xx(chn) 频道
    - (emj)xx(emj) 表情
    仅用于把 KOOK 插件返回的文本转成 QQ 可读的纯文本。
    """
    if not text:
        return text
    import re
    for tag in ("met", "rol", "chn", "emj", "file"):
        text = re.sub(rf"\({tag}\)[^()]*(?:\({tag}\))", "", text)
    # 兜底：删掉残留的 (xxx) 标记
    text = re.sub(r"\((?:met|rol|chn|emj|file)\)", "", text)
    return text
