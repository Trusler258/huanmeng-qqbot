"""
实验特性开关（Feature Flags）—— 新功能先灰度，随时可一键回滚

用途：把还在观察期的新行为做成可开关的"测试项"，用 /~key 激活/停用，
出问题可立刻恢复默认行为，不用改代码、不用重启。

存储：data/features.json，形如 {"face_inline": true}
  · 文件不存在 / 读失败 → 全部走默认值（安全侧）
  · 只记录**与默认值不同**的项？不——显式记录实际取值，便于排查"现在到底开着啥"

用法：
    from modules.features import is_enabled, set_enabled, list_features
    if is_enabled("face_inline"):
        ...新逻辑...
    else:
        ...默认逻辑...

v2.1.19: 首个测试项 face_inline（逐句配图）。
v2.1.20: 第二个测试项 sweet_style（私聊贴贴风格）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from core.logger import get_logger

logger = get_logger("features")

_FILE = Path(__file__).resolve().parent.parent / "data" / "features.json"
_lock = threading.RLock()
_cache: dict[str, bool] | None = None

# ── 测试项注册表 ────────────────────────────────────────────
# code: 英文标识（/~key 用）；aliases: 中文/别名；default: 默认是否开启；
# desc: 一句话说明（/~key 列表展示）；affects: 影响范围说明（关闭后回到什么行为）
FEATURES: dict[str, dict] = {
    "face_inline": {
        "aliases": ("配图", "表情", "逐句配图", "face"),
        "default": True,
        "desc": "逐句配图：表情包写在哪句后面就跟在哪句发（情绪节拍）",
        "affects": "关闭后回到旧行为：一条回复只发 1 张表情，且发在所有文字之后",
    },
    # v2.1.20: 私聊"贴贴风格"测试项（参考图逆向配方：关系自指/节奏切换/短句爆破）
    "sweet_style": {
        "aliases": ("测试风格", "贴贴风格", "养成风格", "sweet"),
        "default": False,
        "desc": "私聊贴贴风格：被养成的叙事自指、节奏切换（撩完掉下来关心）、短句爆破",
        "affects": "关闭后回到默认私聊语气（private_tone 常驻规则）",
    },
    # v2.3.26: 日榜卡改走 Pillow 绘制（服务器实测 79ms vs Chromium 872ms，快 11 倍；
    #   一比一复刻，尺寸/位置/配色对齐，平均像素差 2.5%）。渲染异常会自动回退 Chromium，
    #   所以默认开启；若想强制走 Chromium 对比观感，用 /~key 关掉即可。
    "pillow_card": {
        "aliases": ("绘制卡片", "pillow", "快速卡片", "日榜绘制"),
        "default": True,
        "desc": "日榜卡用 Pillow 直接绘制：11 倍提速（79ms vs 872ms），省 394MB 内存",
        "affects": "关闭后回到 Chromium 渲染（同样外观，但每张慢约 0.8 秒）",
    },
}


def _resolve(code: str) -> str | None:
    """把用户输入（code 或中文别名）解析成标准 code，未注册返回 None"""
    if not code:
        return None
    c = code.strip().lower()
    if c in FEATURES:
        return c
    for name, meta in FEATURES.items():
        if c in (a.lower() for a in meta.get("aliases", ())):
            return name
    return None


def _load() -> dict[str, bool]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        data: dict[str, bool] = {}
        if _FILE.exists():
            try:
                raw = json.loads(_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if k in FEATURES and isinstance(v, bool):
                            data[k] = v
            except Exception as e:
                logger.warning("features.json 读取失败，全部走默认值: %s", e)
        _cache = data
        return _cache


def _save() -> None:
    with _lock:
        try:
            _FILE.parent.mkdir(parents=True, exist_ok=True)
            _FILE.write_text(
                json.dumps(_cache or {}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("features.json 写入失败: %s", e)


def is_enabled(code: str) -> bool:
    """测试项是否开启。未注册/读取异常 → 返回注册表中的默认值（安全侧）。"""
    meta = FEATURES.get(code)
    if meta is None:
        return False
    state = _load()
    if code in state:
        return state[code]
    return bool(meta.get("default", False))


def set_enabled(code: str, enabled: bool) -> bool:
    """设置测试项开关。返回 False 表示 code 未注册。"""
    if code not in FEATURES:
        return False
    with _lock:
        state = _load()
        state[code] = bool(enabled)
        _save()
    logger.info("测试项 %s → %s", code, "开启" if enabled else "关闭")
    return True


def reset(code: str) -> bool:
    """恢复该项为默认值（从文件里删掉覆盖记录）"""
    if code not in FEATURES:
        return False
    with _lock:
        state = _load()
        state.pop(code, None)
        _save()
    logger.info("测试项 %s → 恢复默认(%s)", code, FEATURES[code].get("default"))
    return True


def list_features() -> list[dict]:
    """列出全部测试项及当前状态，供 /~key 展示"""
    out = []
    for code, meta in FEATURES.items():
        out.append({
            "code": code,
            "aliases": list(meta.get("aliases", ())),
            "on": is_enabled(code),
            "default": bool(meta.get("default", False)),
            "desc": meta.get("desc", ""),
            "affects": meta.get("affects", ""),
            "overridden": code in (_load() or {}),
        })
    return out


def resolve(code: str) -> str | None:
    """对外暴露的别名解析"""
    return _resolve(code)
