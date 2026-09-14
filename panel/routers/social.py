"""社交数据：好感度 / 用户画像 / 昵称 / 群统计"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panel import auth, config
from panel.security import atomic_write_json, read_json

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/social", tags=["社交"], dependencies=[Depends(auth.require_user)])

DATA = config.DATA_DIR
FAV_FILE = DATA / "fav.json"
PROFILE_FILE = DATA / "user_profiles.json"

# fav.json 的键格式： "g<群号>:<QQ>"  或  "<QQ>"（私聊）
_FAV_KEY = re.compile(r"^g?(\d+):(\d+)$|^(\d+)$")


def _parse_fav_key(k: str) -> dict:
    m = _FAV_KEY.match(k)
    if not m:
        return {"key": k, "group": "", "qq": k, "raw": k}
    if m.group(1):
        return {"key": k, "group": m.group(1), "qq": m.group(2), "raw": k}
    return {"key": k, "group": "", "qq": m.group(3), "raw": k}


# ── 好感度 ────────────────────────────────────────────────

@router.get("/fav", summary="好感度列表")
async def fav_list(
    group: str = Query("", max_length=20),
    q: str = Query("", max_length=50),
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    order: str = Query("desc"),
):
    fav = read_json(FAV_FILE, {}) or {}
    rows = []
    for k, v in fav.items():
        item = _parse_fav_key(str(k))
        item["value"] = v
        if group and item["group"] != group:
            continue
        if q and q not in item["qq"] and q not in item["group"]:
            continue
        rows.append(item)

    rows.sort(key=lambda r: r["value"] if isinstance(r["value"], (int, float)) else 0,
              reverse=(order != "asc"))

    # 分组统计
    groups: dict[str, int] = {}
    for k in fav:
        g = _parse_fav_key(str(k))["group"] or "(私聊)"
        groups[g] = groups.get(g, 0) + 1

    return {
        "ok": True,
        "total": len(rows),
        "groups": groups,
        "items": rows[offset: offset + limit],
    }


class FavSetReq(BaseModel):
    key: str = Field(max_length=50)
    value: int = Field(ge=-9999, le=99999)


@router.post("/fav", summary="设置好感度（写操作，记审计）")
async def fav_set(body: FavSetReq, user: auth.CurrentUser = Depends(auth.require_user)):
    if not _FAV_KEY.match(body.key):
        raise HTTPException(400, "键格式应为 <QQ> 或 g<群号>:<QQ>")
    fav = read_json(FAV_FILE, {}) or {}
    old = fav.get(body.key)
    fav[body.key] = body.value
    bak = atomic_write_json(FAV_FILE, fav)
    auth.audit(user, "fav_set", body.key, f"{old} -> {body.value}", )
    return {"ok": True, "key": body.key, "old": old, "new": body.value,
            "backup": bak.name if bak else None}


@router.delete("/fav", summary="删除好感度记录")
async def fav_del(key: str = Query(..., max_length=50),
                  user: auth.CurrentUser = Depends(auth.require_user)):
    fav = read_json(FAV_FILE, {}) or {}
    if key not in fav:
        raise HTTPException(404, "记录不存在")
    old = fav.pop(key)
    atomic_write_json(FAV_FILE, fav)
    auth.audit(user, "fav_delete", key, f"删除 原值={old}")
    return {"ok": True, "key": key, "deleted": old}


# ── 用户画像 ──────────────────────────────────────────────

@router.get("/profiles", summary="用户画像列表")
async def profiles(
    q: str = Query("", max_length=60),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    obj = read_json(PROFILE_FILE, {}) or {}
    rows = []
    for k, v in obj.items():
        if q and q not in str(k) and q not in str(v):
            continue
        rows.append({"qq": k, "data": v})
    rows.sort(key=lambda r: str(r["qq"]))
    return {"ok": True, "total": len(rows), "items": rows[offset: offset + limit]}


@router.get("/profiles/{qq}", summary="单个用户画像")
async def profile_one(qq: str):
    obj = read_json(PROFILE_FILE, {}) or {}
    if qq not in obj:
        raise HTTPException(404, "无该用户画像")
    return {"ok": True, "qq": qq, "data": obj[qq]}


class ProfileSetReq(BaseModel):
    qq: str = Field(max_length=20)
    data: dict


@router.post("/profiles", summary="新建/覆盖用户画像")
async def profile_set(body: ProfileSetReq, user: auth.CurrentUser = Depends(auth.require_user)):
    obj = read_json(PROFILE_FILE, {}) or {}
    old = obj.get(body.qq)
    obj[body.qq] = body.data
    atomic_write_json(PROFILE_FILE, obj)
    auth.audit(user, "profile_set", body.qq, "更新画像")
    return {"ok": True, "qq": body.qq, "had_old": old is not None}


@router.delete("/profiles/{qq}", summary="删除用户画像")
async def profile_del(qq: str, user: auth.CurrentUser = Depends(auth.require_user)):
    obj = read_json(PROFILE_FILE, {}) or {}
    if qq not in obj:
        raise HTTPException(404, "无该用户画像")
    obj.pop(qq)
    atomic_write_json(PROFILE_FILE, obj)
    auth.audit(user, "profile_delete", qq, "删除画像")
    return {"ok": True, "qq": qq}


# ── 群统计 ────────────────────────────────────────────────

@router.get("/stats/groups", summary="有统计的群列表")
async def stats_groups():
    # 文件名格式：stats_<群号>_<YYYYMMDD>.json  ← 日期是紧凑格式，不带横线
    files = sorted(DATA.glob("stats_*_*.json"), reverse=True)
    groups: dict[str, dict] = {}
    pat = re.compile(r"^stats_(\d+)_(\d{8})\.json$")
    for f in files:
        m = pat.match(f.name)
        if not m:
            continue
        gid = m.group(1)
        info = groups.setdefault(gid, {"group": gid, "days": 0, "latest": ""})
        info["days"] += 1
        if not info["latest"] or m.group(2) > info["latest"]:
            info["latest"] = m.group(2)
    # latest 转成 ISO 便于前端显示
    for info in groups.values():
        if info["latest"]:
            info["latest_iso"] = (
                f'{info["latest"][:4]}-{info["latest"][4:6]}-{info["latest"][6:8]}'
            )
    return {"ok": True, "items": sorted(groups.values(), key=lambda x: x["latest"], reverse=True)}


@router.get("/stats/{group}", summary="群某日统计详情")
async def stats_detail(
    group: str,
    date: str = Query("", max_length=10),
    limit: int = Query(100, ge=1, le=2000),
):
    if not group.isdigit():
        raise HTTPException(400, "群号必须是数字")
    if not date:
        date = datetime.now(CST).strftime("%Y%m%d")
    # 允许 YYYY-MM-DD 或 YYYYMMDD 两种写法，统一转紧凑格式再拼文件名
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        compact = date.replace("-", "")
    elif re.match(r"^\d{8}$", date):
        compact = date
    else:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD 或 YYYYMMDD")

    path = DATA / f"stats_{group}_{compact}.json"
    obj = read_json(path, None)
    if obj is None:
        raise HTTPException(404, f"无 {group} 在 {date} 的统计数据")

    meta = obj.get("_meta", {})
    members = []
    hour_totals = [0] * 24
    for k, v in obj.items():
        if k == "_meta" or not isinstance(v, dict):
            continue
        hours = v.get("hours", {}) or {}
        for h, c in hours.items():
            try:
                hi = int(h)
                if 0 <= hi < 24:
                    hour_totals[hi] += int(c or 0)
            except Exception:
                pass
        members.append({
            "qq": k,
            "name": v.get("name", ""),
            "count": int(v.get("count", 0) or 0),
            "hours": hours,
        })
    members.sort(key=lambda x: x["count"], reverse=True)

    return {
        "ok": True,
        "group": group,
        "date": date,
        "meta": meta,
        "member_count": len(members),
        "hour_distribution": hour_totals,
        "members": members[:limit],
    }


# ── 幸运值 luck（{"日期": {"QQ": 值}}，与 modules/admin.py 同源）────

LUCK_FILE = DATA / "luck.json"


@router.get("/luck", summary="幸运值列表（按日期）")
async def luck_list(
    date: str = Query("", max_length=20, description="留空 = 全部日期"),
    q: str = Query("", max_length=20, description="按 QQ 过滤"),
    limit: int = Query(30, ge=1, le=365),
):
    obj = read_json(LUCK_FILE, {}) or {}
    if not isinstance(obj, dict):
        obj = {}
    today = datetime.now(CST).date().isoformat()
    days = []
    if date:
        src = {date: obj.get(date, {})} if date in obj else {}
    else:
        src = obj
    for day, users in sorted(src.items(), reverse=True)[:limit]:
        if not isinstance(users, dict):
            continue
        rows = []
        for qq, val in users.items():
            if q and q not in str(qq):
                continue
            rows.append({"qq": str(qq), "value": val})
        rows.sort(key=lambda x: x["qq"])
        days.append({"date": day, "is_today": day == today, "count": len(rows), "rows": rows})
    return {
        "ok": True,
        "today": today,
        "days": days,
        "total_days": len(obj),
        "file": str(LUCK_FILE),
    }


class LuckSetReq(BaseModel):
    qq: str = Field(..., max_length=20)
    value: str = Field(..., max_length=30, description="数值或任意文本")
    date: str = Field("", max_length=20, description="留空 = 今天")
    confirm: str = Field("", max_length=20)


@router.post("/luck", summary="设置某人某天的幸运值（写操作，记审计）")
async def luck_set(
    body: LuckSetReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    if body.confirm not in ("luck", body.qq):
        raise HTTPException(400, "写操作需确认：confirm 填 luck 或该 QQ 号")

    day = body.date.strip() or datetime.now(CST).date().isoformat()
    # 值：纯数字存数字，否则原样存字符串（admin.py 的 _parse_value 允许文本）
    raw = body.value.strip()
    val: object = raw
    try:
        num = float(raw)
        val = int(num) if num.is_integer() else num
    except Exception:
        pass

    obj = read_json(LUCK_FILE, {}) or {}
    if not isinstance(obj, dict):
        obj = {}
    day_map = obj.get(day)
    if not isinstance(day_map, dict):
        day_map = {}
    old = day_map.get(body.qq)
    day_map[body.qq] = val
    obj[day] = day_map
    bak = atomic_write_json(LUCK_FILE, obj)

    auth.audit(user, "luck_set", f"{day}:{body.qq}", f"{old} -> {val}")
    return {
        "ok": True, "date": day, "qq": body.qq, "old": old, "value": val,
        "backup": bak.name if bak else None,
    }


@router.delete("/luck", summary="删除某人某天的幸运值（写操作，记审计）")
async def luck_del(
    qq: str = Query(..., max_length=20),
    date: str = Query("", max_length=20),
    user: auth.CurrentUser = Depends(auth.require_user),
):
    day = date.strip() or datetime.now(CST).date().isoformat()
    obj = read_json(LUCK_FILE, {}) or {}
    if not isinstance(obj, dict) or day not in obj:
        raise HTTPException(404, f"{day} 没有记录")
    day_map = obj.get(day)
    if not isinstance(day_map, dict) or qq not in day_map:
        raise HTTPException(404, f"{qq} 在 {day} 没有记录")
    old = day_map.pop(qq)
    atomic_write_json(LUCK_FILE, obj)
    auth.audit(user, "luck_del", f"{day}:{qq}", f"was {old}")
    return {"ok": True, "date": day, "qq": qq, "deleted": old}
