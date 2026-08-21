"""
经济系统（移植自 huanmeng-kook-bot 的 core/economy.py，并扩展为可交互功能）

数据模型（data/economy.json）:
{
  "users": {
    "<qq号>": {
      "points": 0,            # 积分余额
      "inventory": {"fav_card": 2},  # 权益库存: 物品名 -> 数量
      "last_sign": "2026-08-21"      # 上次签到日期（用于每日签到）
    }
  }
}

设计要点（沿用 kook 版本的安全约定）:
- 全局唯一 RLock，所有读写都经本模块，避免多协程并发写同一文件丢更新。
- 原子写盘: 临时文件 + os.replace，降低半写损坏风险。
- 同步、短、无 await，在 asyncio 单线程下天然不被协程打断。

对外 API（积分）:
  get_points / add_points / set_points
（库存）:
  get_inventory / add_inventory / consume_inventory
（签到）:
  get_last_sign / mark_signed
（社交）:
  transfer_points  # 转账
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from core.logger import get_logger

logger = get_logger("economy")

# 核心级唯一锁：所有读写同一数据文件的地方都必须经本模块，共用这一把。
_LOCK = threading.RLock()

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "economy.json"

# 每日签到基础分（再加随机小奖励，鼓励连续签到）
SIGN_BASE = 5
# 商店物品定义: name=展示名, price=积分价, effect=使用时的效果标识, desc=说明
ITEMS = {
    "fav_card": {
        "name": "好感券",
        "price": 50,
        "effect": "fav",
        "desc": "使用后给当前聊天中的自己 +10 好感度",
    },
}


# ── 内部：读 / 写（均持核心唯一锁）────────────────────────

def _load() -> dict:
    with _LOCK:
        if _DATA_FILE.exists():
            try:
                d = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
                if isinstance(d, dict) and isinstance(d.get("users"), dict):
                    return d
            except Exception as e:
                logger.warning("economy 读取 %s 失败: %s", _DATA_FILE, e)
        return {"users": {}}


def _save(data: dict) -> None:
    with _LOCK:
        _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _DATA_FILE.with_name(_DATA_FILE.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_DATA_FILE)


def _ensure_user(data: dict, uid) -> dict:
    u = data["users"].setdefault(str(uid), {})
    u.setdefault("points", 0)
    u.setdefault("inventory", {})
    u.setdefault("last_sign", "")
    return u


# ── 公开 API：积分 ──────────────────────────────────────

def get_points(uid) -> int:
    with _LOCK:
        return int(_ensure_user(_load(), uid)["points"])


def add_points(uid, delta: int, min_zero: bool = True) -> int:
    """给用户增减积分，返回新余额。min_zero=True 时余额不为负。"""
    with _LOCK:
        data = _load()
        u = _ensure_user(data, uid)
        u["points"] = max(0, u["points"] + delta) if min_zero else u["points"] + delta
        _save(data)
        return u["points"]


def set_points(uid, value: int) -> int:
    """设用户积分为指定值（最小 0），返回新余额。"""
    with _LOCK:
        data = _load()
        u = _ensure_user(data, uid)
        u["points"] = max(0, int(value))
        _save(data)
        return u["points"]


def transfer_points(from_uid, to_uid, amount: int) -> tuple[bool, str]:
    """从 from_uid 转账 amount 给 to_uid（from 余额不足则失败）。"""
    amount = int(amount)
    if amount <= 0:
        return False, "转账数量必须大于 0 喵~"
    with _LOCK:
        data = _load()
        src = _ensure_user(data, from_uid)
        if src["points"] < amount:
            return False, f"积分不足，你只有 {src['points']} 点喵~"
        src["points"] -= amount
        dst = _ensure_user(data, to_uid)
        dst["points"] += amount
        _save(data)
        return True, f"已转账 {amount} 点给 {to_uid}（剩余 {src['points']} 点）"


# ── 公开 API：权益库存 ──────────────────────────────────

def get_inventory(uid) -> dict:
    with _LOCK:
        return dict(_ensure_user(_load(), uid).get("inventory") or {})


def add_inventory(uid, effect: str, qty: int = 1) -> int:
    """给用户增加 N 次权益，返回当前库存。"""
    with _LOCK:
        data = _load()
        u = _ensure_user(data, uid)
        inv = u.setdefault("inventory", {})
        inv[effect] = int(inv.get(effect, 0)) + max(1, int(qty))
        _save(data)
        return inv[effect]


def consume_inventory(uid, effect: str) -> bool:
    """消耗 1 次权益；库存不足返回 False，成功扣除并返回 True。"""
    with _LOCK:
        data = _load()
        u = _ensure_user(data, uid)
        inv = u.setdefault("inventory", {})
        n = int(inv.get(effect, 0))
        if n <= 0:
            return False
        if n == 1:
            inv.pop(effect, None)
        else:
            inv[effect] = n - 1
        _save(data)
        return True


# ── 公开 API：签到 ──────────────────────────────────────

def get_last_sign(uid) -> str:
    with _LOCK:
        return _ensure_user(_load(), uid).get("last_sign", "")


def mark_signed(uid, today: str | None = None) -> int:
    """标记今日已签到，并返回本次获得的积分（仅首次签到当天给分）。"""
    today = today or date.today().isoformat()
    with _LOCK:
        data = _load()
        u = _ensure_user(data, uid)
        if u.get("last_sign") == today:
            return 0
        import random
        reward = SIGN_BASE + random.randint(0, 10)
        u["points"] = u.get("points", 0) + reward
        u["last_sign"] = today
        _save(data)
        return reward


# ── 排行榜 ─────────────────────────────────────────────

def get_top_points(limit: int = 10) -> list[tuple[str, int]]:
    """返回积分最高的前 limit 名 [(uid_str, points), ...]"""
    with _LOCK:
        data = _load()
        items = [
            (uid, int(u.get("points", 0)))
            for uid, u in data.get("users", {}).items()
            if int(u.get("points", 0)) > 0
        ]
    items.sort(key=lambda x: x[1], reverse=True)
    return items[:limit]
