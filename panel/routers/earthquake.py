"""地震模块：订阅管理 / 推送记录 / 手动拉取测试"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panel import auth, config
from panel.security import atomic_write_json, read_json

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/earthquake", tags=["地震"], dependencies=[Depends(auth.require_user)])

DATA = config.DATA_DIR


def _sub_files():
    """找出订阅相关的数据文件（命名可能随版本变化，做兼容探测）"""
    found = {}
    for pat in ("eq_subscribe*.json", "earthquake*.json", "*eq*.json"):
        for p in DATA.glob(pat):
            found[p.name] = p
    return found


@router.get("", summary="地震模块状态与订阅")
async def eq_status():
    files = _sub_files()
    out = {}
    for name, p in files.items():
        out[name] = read_json(p, None)

    # 最近的推送记录
    pushes = []
    log_dir = config.ROOT / "logs"
    for f in sorted(log_dir.glob("*.log*")):
        try:
            with open(f, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 300_000))
                raw = fh.read().decode("utf-8", errors="ignore")
        except Exception:
            continue
        for ln in raw.split("\n"):
            if ("地震" in ln or "地震台网" in ln) and ("推送" in ln or "发送" in ln or "M" in ln):
                pushes.append(ln.strip()[-300:])
    return {
        "ok": True,
        "data_files": {k: v for k, v in out.items()},
        "files": list(files.keys()),
        "recent_pushes": pushes[-30:],
    }


class SubReq(BaseModel):
    group: str = Field(max_length=20)
    enabled: bool = True
    min_magnitude: float = Field(4.0, ge=0, le=10)
    provinces: list[str] = Field(default_factory=list)


@router.post("/subscribe", summary="设置群地震订阅")
async def set_subscribe(
    body: SubReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    if not body.group.isdigit():
        raise HTTPException(400, "群号必须是数字")

    path = DATA / "eq_subscribe.json"
    obj = read_json(path, {}) or {}
    if not isinstance(obj, dict):
        obj = {}
    old = obj.get(body.group)
    obj[body.group] = {
        "enabled": body.enabled,
        "min_magnitude": body.min_magnitude,
        "provinces": body.provinces,
        "updated": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
    }
    bak = atomic_write_json(path, obj)
    auth.audit(user, "eq_subscribe", body.group,
               f"{old} -> {obj[body.group]}")
    return {"ok": True, "group": body.group, "config": obj[body.group],
            "backup": bak.name if bak else None}


@router.delete("/subscribe/{group}", summary="取消群地震订阅")
async def del_subscribe(group: str, user: auth.CurrentUser = Depends(auth.require_user)):
    path = DATA / "eq_subscribe.json"
    obj = read_json(path, {}) or {}
    if not isinstance(obj, dict) or group not in obj:
        raise HTTPException(404, "该群无订阅记录")
    old = obj.pop(group)
    atomic_write_json(path, obj)
    auth.audit(user, "eq_unsubscribe", group, str(old))
    return {"ok": True, "group": group, "removed": old}


@router.get("/recent", summary="最近地震记录（走 CENC 公开接口）")
async def recent_eq(limit: int = Query(20, ge=1, le=100)):
    """直接读 CENC 公开接口，不触碰 Bot 的轮询状态"""
    import json
    import urllib.request

    url = "https://news.ceic.ac.cn/ajax/google?rand=1"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (huanmeng-panel)",
            "Referer": "https://news.ceic.ac.cn/",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("utf-8", errors="ignore")
        raw = raw.strip()
        # CENC 返回形如 ({"result":[...]}) 的 JSONP
        if raw.startswith("("):
            raw = raw[1:-1]
        data = json.loads(raw)
        items = data if isinstance(data, list) else data.get("result", [])
        return {"ok": True, "count": len(items), "items": items[:limit]}
    except Exception as e:
        return {"ok": False, "error": f"拉取失败: {e}", "items": []}
