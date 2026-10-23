"""内部提示词读取（config/prompts.toml）。

项目规矩：**提示词不写进 .py**。
- 角色 / 风格 / 能力类 → data/skills/*.md（按节注入）
- 流程性短提示（长度约束、纠错话术、子任务 system prompt）→ config/prompts.toml

放独立模块是为了避免循环导入：services/llm.py 会 import core.tools，
core.tools 又要读提示词，两边都从这里取就没事。

按 mtime 缓存，改 toml 不用重启。
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts.toml"
_cache: tuple[float, dict] | None = None


def _load() -> dict:
    global _cache
    try:
        mtime = _PATH.stat().st_mtime
    except OSError:
        return {}
    if _cache and _cache[0] == mtime:
        return _cache[1]
    try:
        import toml
        data = toml.load(_PATH)
        _cache = (mtime, data)
        return data
    except Exception as e:
        logger.warning("读取 config/prompts.toml 失败（用空表）: %s", e)
        return {}


def prompt(section: str, key: str = "", default: str = "") -> str:
    """取提示词：prompt("len_hint", "short")；不传 key 返回整节。"""
    sec = _load().get(section) or {}
    if not key:
        return sec if isinstance(sec, dict) else default
    val = sec.get(key) if isinstance(sec, dict) else None
    return val if isinstance(val, str) and val else default


def render(section: str, key: str, default: str = "", **kwargs) -> str:
    """取提示词并填充 {占位符}。"""
    tpl = prompt(section, key, default)
    if not tpl:
        return ""
    try:
        return tpl.format(**kwargs)
    except Exception as e:
        logger.warning("提示词 %s.%s 填充失败: %s", section, key, e)
        return tpl
