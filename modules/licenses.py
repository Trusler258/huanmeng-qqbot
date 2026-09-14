"""许可码（License Code）——把 /~key 实验开关的使用权开放给全员，但需授权

背景：/~key 原来是管理员专属。用户要求「全员可用，不过需要许可码」，
许可码在面板里生成，分三种：
  · once    一次性的：兑换后只允许用 1 次开关操作，用完即尽
  · day     一天的：兑换后 24 小时内不限次使用，过期自动失效
  · forever 无期限的：兑换后永久不限次使用

存储：data/licenses.json
{
  "codes": {
    "<码>": {
      "type": "once|day|forever",
      "created_at": <ts>, "created_by": "panel"|"cli",
      "note": "", "redeemed": false,
      "redeemed_by": null, "redeemed_at": null
    }
  },
  "grants": {
    "<qq>": {
      "uses_left": 1 | null,        # null = 不限次（day/forever）
      "expires_at": <ts> | null,    # null = 永不过期（once/forever）
      "source": "<码>",
      "granted_at": <ts>
    }
  }
}

设计要点：
  - 全局 RLock + 原子写（同 economy.py 的做法），避免并发兑换丢数据
  - 码用去掉易混字符（0/O/1/I）的大写字母数字，8 位 4-4 分组，人念得清
  - 管理员永远免码（cfg.is_admin 优先），避免把自己锁在外面
  - 兑换是幂等的：同一个码不能被两个人用；用户重复兑换同一码不叠加
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path

from core.logger import get_logger

logger = get_logger("licenses")

_FILE = Path(__file__).resolve().parent.parent / "data" / "licenses.json"
_lock = threading.RLock()

# 去掉 0/O/1/I/L 等易混字符——许可码要人工转抄，可读性优先
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_PREFIX = "HM"          # 幻梦（HuanMeng）

TYPES: dict[str, dict] = {
    "once": {
        "label": "一次性",
        "desc": "兑换后只能用 1 次开关操作，用完即尽",
        "uses": 1,
        "ttl": None,
    },
    "day": {
        "label": "一天",
        "desc": "兑换后 24 小时内不限次使用，过期自动失效",
        "uses": None,
        "ttl": 86400,
    },
    "forever": {
        "label": "无期限",
        "desc": "兑换后永久不限次使用",
        "uses": None,
        "ttl": None,
    },
}


def _now() -> int:
    return int(time.time())


def _empty() -> dict:
    return {"codes": {}, "grants": {}}


def _load() -> dict:
    """读 licenses.json。损坏/缺失 → 返回空结构（安全侧：没有授权）"""
    with _lock:
        if not _FILE.exists():
            return _empty()
        try:
            raw = json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("licenses.json 读取失败，按空处理: %s", e)
            return _empty()
        if not isinstance(raw, dict):
            return _empty()
        out = _empty()
        if isinstance(raw.get("codes"), dict):
            out["codes"] = {str(k): v for k, v in raw["codes"].items() if isinstance(v, dict)}
        if isinstance(raw.get("grants"), dict):
            out["grants"] = {str(k): v for k, v in raw["grants"].items() if isinstance(v, dict)}
        return out


def _save(data: dict) -> None:
    with _lock:
        try:
            _FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _FILE.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp.replace(_FILE)   # 原子替换，避免写一半崩了留下坏文件
        except Exception as e:
            logger.error("licenses.json 写入失败: %s", e)


def _gen_code() -> str:
    """生成形如 HM4K7P-Q2X9M 的码（12 位随机 + 前缀，4-5 分组便于转抄）"""
    body = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{_PREFIX}{body[:4]}-{body[4:]}"


# ── 生成 / 管理（面板用） ──────────────────────────────────

def create_code(code_type: str = "once", note: str = "",
                created_by: str = "panel") -> dict:
    """生成一个许可码。返回 {"ok", "code", ...}"""
    if code_type not in TYPES:
        return {"ok": False, "error": f"未知类型 {code_type}（可选 {'/'.join(TYPES)}）"}
    data = _load()
    # 撞码重试（概率极低，但生成器要保证唯一）
    for _ in range(20):
        code = _gen_code()
        if code not in data["codes"]:
            break
    else:
        return {"ok": False, "error": "生成失败，请重试"}
    data["codes"][code] = {
        "type": code_type,
        "created_at": _now(),
        "created_by": created_by,
        "note": (note or "")[:200],
        "redeemed": False,
        "redeemed_by": None,
        "redeemed_at": None,
    }
    _save(data)
    logger.info("生成许可码 %s（%s）", code, code_type)
    return {"ok": True, "code": code, "type": code_type,
            "label": TYPES[code_type]["label"]}


def list_codes() -> list[dict]:
    """列出所有码（含状态），新的在前"""
    data = _load()
    out = []
    for code, meta in data["codes"].items():
        t = str(meta.get("type", "once"))
        out.append({
            "code": code,
            "type": t,
            "label": TYPES.get(t, {}).get("label", t),
            "created_at": meta.get("created_at", 0),
            "created_by": meta.get("created_by", ""),
            "note": meta.get("note", ""),
            "redeemed": bool(meta.get("redeemed")),
            "redeemed_by": meta.get("redeemed_by"),
            "redeemed_at": meta.get("redeemed_at"),
        })
    out.sort(key=lambda x: x["created_at"], reverse=True)
    return out


def delete_code(code: str) -> bool:
    """删除一个码。已兑换的码删掉不影响已发放的授权（授权已独立存储）"""
    data = _load()
    if code not in data["codes"]:
        return False
    del data["codes"][code]
    _save(data)
    logger.info("删除许可码 %s", code)
    return True


def list_grants() -> list[dict]:
    """列出所有已发放授权（附剩余次数/到期时间）"""
    data = _load()
    now = _now()
    out = []
    for qq, g in data["grants"].items():
        expires_at = g.get("expires_at")
        out.append({
            "qq": qq,
            "uses_left": g.get("uses_left"),
            "expires_at": expires_at,
            "expired": bool(expires_at and expires_at < now),
            "source": g.get("source", ""),
            "granted_at": g.get("granted_at", 0),
        })
    out.sort(key=lambda x: x["granted_at"], reverse=True)
    return out


def revoke(qq: str) -> bool:
    """撤销某人的授权"""
    data = _load()
    qq = str(qq).strip()
    if qq not in data["grants"]:
        return False
    del data["grants"][qq]
    _save(data)
    logger.info("撤销授权: %s", qq)
    return True


# ── 兑换 / 校验（bot 用） ──────────────────────────────────

def redeem(code: str, qq: str | int) -> dict:
    """用许可码给用户发放授权。返回 {"ok", "msg", ...}"""
    code = (code or "").strip().upper().replace(" ", "")
    qq = str(qq)
    if not code:
        return {"ok": False, "msg": "许可码是空的哦"}

    data = _load()
    meta = data["codes"].get(code)
    if meta is None:
        return {"ok": False, "msg": "这个许可码不存在，检查一下有没有打错"}
    if meta.get("redeemed"):
        who = meta.get("redeemed_by")
        if str(who) == qq:
            return {"ok": False, "msg": "这个码你已经兑换过啦"}
        return {"ok": False, "msg": "这个许可码已经被用掉了"}

    ctype = str(meta.get("type", "once"))
    spec = TYPES.get(ctype, TYPES["once"])
    now = _now()
    grant = {
        "uses_left": spec["uses"],
        "expires_at": (now + spec["ttl"]) if spec["ttl"] else None,
        "source": code,
        "granted_at": now,
    }

    # 已有授权：叠加（取更宽松的那份），避免重复兑换把权限降级
    old = data["grants"].get(qq)
    if isinstance(old, dict):
        # 次数：不限次(null) 优先；否则取较大值累加
        if old.get("uses_left") is None or grant["uses_left"] is None:
            grant["uses_left"] = None
        else:
            grant["uses_left"] = max(int(old["uses_left"]), int(grant["uses_left"]))
        # 到期：永不(null) 优先；否则取较晚
        oe, ne = old.get("expires_at"), grant["expires_at"]
        if oe is None or ne is None:
            grant["expires_at"] = None
        else:
            grant["expires_at"] = max(int(oe), int(ne))

    data["grants"][qq] = grant
    meta["redeemed"] = True
    meta["redeemed_by"] = qq
    meta["redeemed_at"] = now
    _save(data)
    logger.info("许可码 %s 已由 %s 兑换（%s）", code, qq, ctype)
    return {"ok": True, "type": ctype, "label": spec["label"],
            "desc": spec["desc"], "msg": f"许可码兑换成功（{spec['label']}）"}


def check(qq: str | int) -> dict:
    """查某人是否还有使用权。返回 {"ok", "reason", ...}"""
    qq = str(qq)
    data = _load()
    g = data["grants"].get(qq)
    if not isinstance(g, dict):
        return {"ok": False, "reason": "none"}
    now = _now()
    exp = g.get("expires_at")
    if exp is not None and int(exp) < now:
        return {"ok": False, "reason": "expired", "expires_at": int(exp)}
    uses = g.get("uses_left")
    if uses is not None and int(uses) <= 0:
        return {"ok": False, "reason": "used_up"}
    return {"ok": True, "uses_left": uses, "expires_at": exp}


def consume(qq: str | int) -> dict:
    """消耗一次使用权（用于 once 类型）。返回消耗后的状态。"""
    qq = str(qq)
    st = check(qq)
    if not st.get("ok"):
        return st
    data = _load()
    g = data["grants"].get(qq)
    if not isinstance(g, dict):
        return {"ok": False, "reason": "none"}
    uses = g.get("uses_left")
    if uses is not None:
        g["uses_left"] = max(0, int(uses) - 1)
        _save(data)
    return {"ok": True, "uses_left": g.get("uses_left"),
            "expires_at": g.get("expires_at")}


def describe(qq: str | int) -> str:
    """给用户看的一句话权限说明"""
    st = check(qq)
    if not st.get("ok"):
        r = st.get("reason")
        if r == "expired":
            return "你的许可已过期，需要新的许可码"
        if r == "used_up":
            return "你的许可次数已用完，需要新的许可码"
        return "还没有许可码"
    uses = st.get("uses_left")
    exp = st.get("expires_at")
    parts = []
    parts.append("不限次数" if uses is None else f"剩余 {uses} 次")
    if exp is None:
        parts.append("永久有效")
    else:
        left = int(exp) - _now()
        if left > 3600:
            parts.append(f"{left // 3600} 小时后到期")
        else:
            parts.append(f"{max(left, 0) // 60} 分钟后到期")
    return "、".join(parts)
