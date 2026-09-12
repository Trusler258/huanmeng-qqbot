"""实验开关（Feature Flags）管理

数据源：modules/features.py 的 FEATURES 注册表 + data/features.json 当前值。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from panel import auth, config
from panel.security import atomic_write_json, read_json

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/features", tags=["实验开关"], dependencies=[Depends(auth.require_user)])

FEATURES_FILE = config.DATA_DIR / "features.json"


def _load_registry() -> dict:
    """从 modules/features.py 读注册表。失败则降级为只读 features.json。

    用 import 而非解析源码——保证与 Bot 真实定义**同源**，
    不会出现"面板显示的说明和代码里的行为不一致"。
    """
    try:
        import sys
        root = str(config.ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from modules import features as F  # type: ignore
        reg = getattr(F, "FEATURES", {})
        out = {}
        for k, v in reg.items():
            if isinstance(v, dict):
                out[k] = dict(v)
            else:
                out[k] = {"desc": str(v)}
        return out
    except Exception as e:
        return {"__error__": {"desc": f"无法加载 modules/features.py: {e}"}}


@router.get("", summary="所有实验开关及当前值")
async def list_features():
    reg = _load_registry()
    cur = read_json(FEATURES_FILE, {}) or {}
    items = []
    for k, meta in reg.items():
        if k == "__error__":
            continue
        items.append({
            "key": k,
            "enabled": bool(cur.get(k, meta.get("default", False))),
            "default": bool(meta.get("default", False)),
            "desc": meta.get("desc", ""),
            "impact": meta.get("impact", ""),
            "since": meta.get("since", ""),
        })
    # 注册表里没有但 json 里有的（历史遗留）
    for k in cur:
        if k not in reg:
            items.append({
                "key": k, "enabled": bool(cur[k]), "default": False,
                "desc": "(未注册，仅存在于 features.json)", "impact": "", "since": "",
            })
    return {
        "ok": True,
        "count": len(items),
        "items": sorted(items, key=lambda x: x["key"]),
        "raw": cur,
        "registry_error": reg.get("__error__", {}).get("desc", ""),
        "file": str(FEATURES_FILE),
    }


class FeatureToggleReq(BaseModel):
    key: str = Field(max_length=60)
    enabled: bool
    confirm: str = Field("", max_length=20)


@router.post("", summary="开/关某个实验项（写操作，记审计）")
async def toggle_feature(
    body: FeatureToggleReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    reg = _load_registry()
    if body.key not in reg:
        raise HTTPException(400, f"未注册的实验项: {body.key}")

    cur = read_json(FEATURES_FILE, {}) or {}
    old = cur.get(body.key)
    cur[body.key] = body.enabled
    bak = atomic_write_json(FEATURES_FILE, cur)

    auth.audit(user, "feature_toggle", body.key, f"{old} -> {body.enabled}")
    return {
        "ok": True, "key": body.key, "old": old, "new": body.enabled,
        "backup": bak.name if bak else None,
        "note": "该改动需重启 bot.service 才完全生效（提示词章节在启动时构建）",
    }


@router.post("/reset", summary="恢复某个开关到默认值")
async def reset_feature(
    key: str,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    reg = _load_registry()
    if key not in reg:
        raise HTTPException(400, f"未注册的实验项: {key}")
    default = bool(reg[key].get("default", False))
    cur = read_json(FEATURES_FILE, {}) or {}
    old = cur.get(key)
    cur[key] = default
    atomic_write_json(FEATURES_FILE, cur)
    auth.audit(user, "feature_reset", key, f"{old} -> {default}(默认)")
    return {"ok": True, "key": key, "old": old, "new": default}
