"""游戏与娱乐：五子棋战绩 / 洛花星雨战绩 / 倒计时 / 搜索缓存"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panel import auth, config
from panel.security import atomic_write_json, read_json

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/games", tags=["游戏"], dependencies=[Depends(auth.require_user)])

DATA = config.DATA_DIR


# ── 五子棋 ────────────────────────────────────────────────

@router.get("/wzq", summary="五子棋战绩")
async def wzq(
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    obj = read_json(DATA / "wzq_results.json", {})
    if isinstance(obj, dict):
        rows = [{"key": k, "value": v} for k, v in obj.items()]
    elif isinstance(obj, list):
        rows = [{"index": i, "value": v} for i, v in enumerate(obj)]
    else:
        rows = []
    rows.reverse()  # 新的在前
    return {
        "ok": True, "total": len(rows),
        "items": rows[offset: offset + limit],
        "type": type(obj).__name__,
    }


# ── 洛花星雨 ──────────────────────────────────────────────

@router.get("/wdsj", summary="洛花星雨战绩历史")
async def wdsj(
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    hist = read_json(DATA / "wdsj_history.json", {})
    names = read_json(DATA / "wdsj_player_name.json", {})

    items = []
    if isinstance(hist, dict):
        for k, v in hist.items():
            if isinstance(v, list):
                items.append({"key": k, "records": v, "count": len(v)})
            else:
                items.append({"key": k, "records": v, "count": 1})
    elif isinstance(hist, list):
        items = [{"index": i, "records": v} for i, v in enumerate(hist)]

    return {
        "ok": True,
        "history_total": len(items),
        "history": items[offset: offset + limit],
        "playernames": names,
    }


# ── 倒计时 ────────────────────────────────────────────────

@router.get("/countdown", summary="倒计时列表")
async def countdown_list():
    obj = read_json(DATA / "countdown.json", {})
    now = datetime.now(CST)
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            rec = dict(v) if isinstance(v, dict) else {"value": v}
            target = rec.get("time") or rec.get("target") or rec.get("date") or ""
            left = None
            if target:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(str(target)[:19], fmt).replace(tzinfo=CST)
                        left = int((dt - now).total_seconds())
                        break
                    except Exception:
                        continue
            items.append({"key": k, "target": target, "seconds_left": left, **rec})
    items.sort(key=lambda x: (x.get("seconds_left") is None, x.get("seconds_left") or 0))
    return {"ok": True, "items": items, "now": now.strftime("%Y-%m-%d %H:%M:%S")}


# ── 搜索缓存 ──────────────────────────────────────────────

@router.get("/search-cache", summary="搜索缓存浏览")
async def search_cache(limit: int = Query(50, ge=1, le=500)):
    obj = read_json(DATA / "search_cache.json", {})
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            rec = {"key": k}
            if isinstance(v, dict):
                rec["chars"] = len(str(v.get("result", "")))
                rec["cached_at"] = v.get("time", v.get("ts", ""))
                rec["preview"] = str(v.get("result", ""))[:150]
            else:
                rec["preview"] = str(v)[:150]
            items.append(rec)
    items.sort(key=lambda x: str(x.get("cached_at", "")), reverse=True)
    return {"ok": True, "total": len(items), "items": items[:limit]}


@router.delete("/search-cache", summary="清空搜索缓存")
async def clear_search_cache(user: auth.CurrentUser = Depends(auth.require_user)):
    path = DATA / "search_cache.json"
    old = read_json(path, {}) or {}
    n = len(old) if isinstance(old, (dict, list)) else 0
    bak = atomic_write_json(path, {})
    auth.audit(user, "search_cache_clear", "search_cache.json", f"清空 {n} 条")
    return {"ok": True, "cleared": n, "backup": bak.name if bak else None}


# ── 抽奖 ──────────────────────────────────────────────────

@router.get("/lottery", summary="抽奖记录（若存在）")
async def lottery(limit: int = Query(100, ge=1, le=1000)):
    for name in ("lottery.json", "draw.json", "choujiang.json"):
        p = DATA / name
        if p.exists():
            obj = read_json(p, {})
            return {"ok": True, "file": name, "data": obj}
    return {"ok": True, "file": "", "data": {}, "note": "未找到抽奖数据文件"}
