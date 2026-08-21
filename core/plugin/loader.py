"""
Plugin Loader（移植自 huanmeng-kook-bot core/plugin/loader.py）

从 plugins/ 目录发现并加载插件：
- discover()：扫描目录，读取 manifest.json，校验得到 PluginManifest
- load_module()：为 python runtime 动态导入 entrypoint 模块
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Optional

from core.logger import get_logger
from core.plugin.manifest import PluginManifest, validate_manifest, RUNTIME_LUA
from core.plugin import kook_compat

logger = get_logger("plugin.loader")

# 加载插件前注入 KOOK 专属模块 stub（.hmp 插件常 import khl，剥离 KOOK 格式依赖）
kook_compat.install_kook_stubs()

MANIFEST_FILE = "manifest.json"

# 目录名前缀：带此前缀的插件文件夹会被跳过（改名即停用，重启后不再加载）
DISABLE_PREFIX = "[DISABLE]"


def discover_plugins(plugins_dir: str) -> list[PluginManifest]:
    """扫描目录，返回所有合法插件的 manifest。非法插件跳过并告警。"""
    manifests: list[PluginManifest] = []
    base = Path(plugins_dir)
    try:
        if not base.is_dir():
            return manifests
    except OSError:
        return manifests

    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        # [DISABLE] 前缀：视为已停用，跳过加载
        if child.name.startswith(DISABLE_PREFIX):
            logger.debug("跳过 %s：目录名带 [DISABLE] 禁用前缀", child.name)
            continue
        mf_path = child / MANIFEST_FILE
        if not mf_path.is_file():
            logger.debug("跳过 %s：缺少 %s", child.name, MANIFEST_FILE)
            continue
        try:
            data = json.loads(mf_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("插件 %s manifest 解析失败: %s", child.name, e)
            continue
        manifest, err = validate_manifest(data)
        if err:
            logger.warning("插件 %s 校验失败: %s", child.name, err)
            continue
        manifest.base_dir = str(child)
        manifests.append(manifest)
    return manifests


def load_module(manifest: PluginManifest) -> Optional[object]:
    """加载 python runtime 插件的 entrypoint 模块，返回模块对象。"""
    if manifest.runtime == RUNTIME_LUA:
        # Lua 插件暂不支持，不在 python 层动态导入
        return None
    entry = Path(manifest.base_dir) / manifest.entrypoint
    if not entry.is_file():
        logger.warning("插件 %s 入口不存在: %s", manifest.name, entry)
        return None
    module_name = f"_hm_plugin_{manifest.name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(entry))
        if spec is None or spec.loader is None:
            logger.warning("插件 %s 无法创建 spec", manifest.name)
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        logger.warning("插件 %s 加载失败: %s", manifest.name, e)
        sys.modules.pop(module_name, None)
        return None


def locate_plugin_classes(module: object) -> list[type]:
    """从模块中找出 Plugin 类（名为 Plugin 或自身 dict 含 on_load/on_unload 的类）。

    用 vars(obj)（类自身 __dict__）而非 hasattr 判定钩子，避免 KOOK stub 类
    （元类 __getattr__ 兜底导致 hasattr 恒 True）被误判为 Plugin 类。
    """
    classes: list[type] = []
    for attrn in dir(module):
        if attrn.startswith("_"):
            continue
        obj = getattr(module, attrn)
        if not isinstance(obj, type):
            continue
        if getattr(obj, "_kook_stub", False):
            # KOOK 兼容层注入的 stub 类，跳过
            continue
        if attrn == "Plugin" or (
            "on_load" in vars(obj) and "on_unload" in vars(obj)
        ):
            classes.append(obj)
    return classes
