"""按需加载插件目录里的核心实现（plugin core）。

## 为什么需要这个

插件目录不是 Python package（`plugins/` 下没有 `__init__.py`，加载器按文件路径 exec），
所以插件外部（`modules/commands.py`、`core/pipeline.py`、`bot.py`、`services/llm.py`）
没法写 `from plugins.reward import ...`。

2026-10-26 起定下规矩：**已经在插件里实现的逻辑，不再在 `modules/` 留一份**
（用户明确要求「已经在插件里的功能就不要再放到仓库了」「薄壳就完整移植」）。
插件把自己的实现放在 `plugins/<name>/<name>_core.py`，外部统一通过本模块取用：

    from core.plugin.cores import load_plugin_core
    reward = load_plugin_core("reward")
    reward.sponsors_hint(cfg)

- 同一个进程内只 exec 一次，之后走缓存
- 找不到文件时抛 FileNotFoundError（调用方按需 try/except 降级）
- 文件名默认 `<name>_core.py`，也可以用第二参数指定（如 voice 用的是 `voice_core.py`）
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins"
_CACHE: dict[str, object] = {}


def plugin_dir(name: str) -> Path:
    return _PLUGIN_ROOT / name


def load_plugin_core(name: str, filename: str | None = None):
    """加载 `plugins/<name>/<filename>`（默认 `<name>_core.py`）并缓存。

    同一个插件名多次调用返回同一个模块对象，模块级状态（缓存、计数器）因此共享。
    """
    key = name if not filename else f"{name}:{filename}"
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    path = plugin_dir(name) / (filename or f"{name}_core.py")
    if not path.is_file():
        raise FileNotFoundError(f"插件核心不存在: {path}")

    mod_name = f"_hm_core_{name}" + ("" if not filename else "_" + Path(filename).stem)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件核心: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    _CACHE[key] = module
    return module
