"""许可码管理（License Codes）

数据源：modules/licenses.py（与 bot 同源——面板生成、bot 校验，同一份 json）。
三种类型：once（一次性）/ day（一天）/ forever（无期限）。

设计要点：
  - 与 features.py 一样直接 import bot 的模块，保证行为同源
  - 生成/删除/撤销都是写操作，全部记审计
  - 码是敏感凭据，列表里全量返回（管理员要看得见才能发出去），
    但审计日志只记前 6 位，避免日志里泄露完整码
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panel import auth, config

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/licenses", tags=["许可码"], dependencies=[Depends(auth.require_user)])


def _lic():
    """加载 bot 的 modules/licenses.py（同源，避免两处实现漂移）"""
    root = str(config.ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from modules import licenses as L  # type: ignore
    return L


def _fmt_ts(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), CST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


@router.get("", summary="许可码列表 + 已发放授权 + 类型说明")
async def list_licenses():
    L = _lic()
    codes = L.list_codes()
    grants = L.list_grants()
    for c in codes:
        c["created_str"] = _fmt_ts(c.get("created_at"))
        c["redeemed_str"] = _fmt_ts(c.get("redeemed_at"))
    for g in grants:
        g["granted_str"] = _fmt_ts(g.get("granted_at"))
        g["expires_str"] = _fmt_ts(g.get("expires_at"))
    types = [
        {"key": k, "label": v["label"], "desc": v["desc"]}
        for k, v in L.TYPES.items()
    ]
    return {
        "ok": True,
        "codes": codes,
        "grants": grants,
        "types": types,
        "stats": {
            "total_codes": len(codes),
            "unused_codes": sum(1 for c in codes if not c["redeemed"]),
            "redeemed_codes": sum(1 for c in codes if c["redeemed"]),
            "active_grants": sum(1 for g in grants if not g["expired"]),
        },
    }


class CreateReq(BaseModel):
    type: str = Field("once", max_length=20)
    note: str = Field("", max_length=200)
    count: int = Field(1, ge=1, le=50, description="一次生成几个")


@router.post("", summary="生成许可码（写操作，记审计）")
async def create_license(
    body: CreateReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    L = _lic()
    if body.type not in L.TYPES:
        raise HTTPException(400, f"未知类型 {body.type}，可选：{'/'.join(L.TYPES)}")
    made = []
    for _ in range(body.count):
        r = L.create_code(body.type, body.note, created_by="panel")
        if not r.get("ok"):
            raise HTTPException(500, r.get("error", "生成失败"))
        made.append(r["code"])
    # 审计只记前 6 位，避免日志里泄露完整凭据
    shown = ", ".join(c[:6] + "…" for c in made)
    auth.audit(user, "license_create", body.type, f"{len(made)} 个: {shown}")
    return {"ok": True, "codes": made, "count": len(made), "type": body.type}


@router.delete("", summary="删除许可码（写操作，记审计）")
async def delete_license(
    code: str = Query(..., max_length=40),
    user: auth.CurrentUser = Depends(auth.require_user),
):
    L = _lic()
    ok = L.delete_code(code.strip().upper())
    if not ok:
        raise HTTPException(404, "许可码不存在")
    auth.audit(user, "license_delete", code[:6] + "…", "deleted")
    return {"ok": True, "code": code}


class RevokeReq(BaseModel):
    qq: str = Field(..., max_length=20)


@router.post("/revoke", summary="撤销某人的使用权（写操作，记审计）")
async def revoke_license(
    body: RevokeReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    L = _lic()
    ok = L.revoke(body.qq.strip())
    if not ok:
        raise HTTPException(404, "该用户没有授权记录")
    auth.audit(user, "license_revoke", body.qq, "revoked")
    return {"ok": True, "qq": body.qq}
