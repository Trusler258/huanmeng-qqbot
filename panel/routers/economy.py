"""经济系统：积分 / 库存 / 签到"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panel import auth, config
from panel.security import atomic_write_json, read_json

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/economy", tags=["经济"], dependencies=[Depends(auth.require_user)])

ECON_FILE = config.DATA_DIR / "economy.json"


def _load() -> dict:
    obj = read_json(ECON_FILE, {})
    return obj if isinstance(obj, dict) else {}


@router.get("", summary="经济系统概览")
async def economy_overview():
    obj = _load()
    meta = obj.get("_meta", {}) if isinstance(obj.get("_meta"), dict) else {}
    users = {k: v for k, v in obj.items() if k != "_meta"}

    rows = []
    for k, v in users.items():
        if not isinstance(v, dict):
            continue
        rows.append({
            "uid": k,
            "points": int(v.get("points", v.get("score", 0)) or 0),
            "items": v.get("items", v.get("inventory", {})) or {},
            "sign_in_days": v.get("sign_days", v.get("signin_days", 0)) or 0,
            "last_sign": v.get("last_sign", v.get("last_signin", "")) or "",
        })
    rows.sort(key=lambda r: r["points"], reverse=True)

    total_points = sum(r["points"] for r in rows)
    total_items = sum(
        sum(int(c or 0) for c in (r["items"].values() if isinstance(r["items"], dict) else []))
        for r in rows
    )

    return {
        "ok": True,
        "meta": meta,
        "user_count": len(rows),
        "total_points": total_points,
        "total_items": total_items,
        "items": rows,
        "file": str(ECON_FILE),
        "raw_keys": list(obj.keys())[:50],
    }


class PointAdjustReq(BaseModel):
    uid: str = Field(max_length=30)
    delta: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field("", max_length=200)


@router.post("/points", summary="增减积分（写操作，记审计）")
async def adjust_points(
    body: PointAdjustReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    obj = _load()
    rec = obj.get(body.uid)
    if not isinstance(rec, dict):
        rec = {}
        obj[body.uid] = rec

    key = "points" if "points" in rec else ("score" if "score" in rec else "points")
    old = int(rec.get(key, 0) or 0)
    new = max(0, old + body.delta)
    rec[key] = new
    atomic_write_json(ECON_FILE, obj)

    auth.audit(user, "economy_adjust", body.uid,
               f"{key}: {old} -> {new} ({body.delta:+d}) {body.reason}")
    return {"ok": True, "uid": body.uid, "field": key, "old": old, "new": new}


class ItemGrantReq(BaseModel):
    uid: str = Field(max_length=30)
    item: str = Field(max_length=60)
    count: int = Field(ge=-1000, le=1000)


@router.post("/items", summary="发放/扣除物品")
async def grant_item(
    body: ItemGrantReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    obj = _load()
    rec = obj.get(body.uid)
    if not isinstance(rec, dict):
        rec = {}
        obj[body.uid] = rec
    ik = "items" if "items" in rec else ("inventory" if "inventory" in rec else "items")
    inv = rec.get(ik)
    if not isinstance(inv, dict):
        inv = {}
        rec[ik] = inv

    old = int(inv.get(body.item, 0) or 0)
    new = old + body.count
    if new <= 0:
        inv.pop(body.item, None)
    else:
        inv[body.item] = new
    atomic_write_json(ECON_FILE, obj)

    auth.audit(user, "economy_item", body.uid,
               f"{body.item}: {old} -> {max(new,0)} ({body.count:+d})")
    return {"ok": True, "uid": body.uid, "item": body.item, "old": old, "new": max(new, 0)}
