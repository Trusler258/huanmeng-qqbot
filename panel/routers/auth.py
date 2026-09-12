"""认证接口：登录 / 登出 / 会话信息 / 改密"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from panel import auth, config

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/auth", tags=["认证"])


class LoginReq(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class ChangePwdReq(BaseModel):
    old_password: str = Field(max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


@router.get("/status", summary="面板初始化状态（免认证，仅暴露布尔值）")
async def auth_status():
    """供前端在登录页判断面板是否已初始化。只回布尔，不泄露任何凭据信息。"""
    cfg = config.load()
    return {
        "ok": True,
        "initialized": cfg.secret_configured,
        "token_ttl_hours": cfg.token_ttl_hours,
    }


@router.post("/login", summary="登录")
async def login(body: LoginReq, request: Request):
    cfg = config.load()
    ip = auth._client_ip(request)

    if not cfg.secret_configured:
        raise HTTPException(503, "面板尚未初始化，请先执行 python -m panel.cli init")

    locked = auth.login_locked(ip)
    if locked:
        auth.audit(None, "login_blocked", ip, f"锁定中，剩余{locked}秒", request)
        raise HTTPException(429, f"尝试过于频繁，请 {locked} 秒后再试")

    if not auth.verify_password(body.password):
        auth.record_fail(ip)
        auth.audit(None, "login_fail", ip, "密码错误", request)
        left = cfg.login_max_fail - len(auth._fails.get(ip, []))
        raise HTTPException(401, f"密码错误（还可尝试 {max(left, 0)} 次）")

    auth.clear_fails(ip)
    token, exp = auth.issue_token("admin")
    auth.audit(auth.CurrentUser("admin", ip), "login_ok", ip, "登录成功", request)
    return {
        "ok": True,
        "token": token,
        "expires_at": exp,
        "expires_in": cfg.token_ttl_hours * 3600,
        "username": "admin",
    }


@router.get("/me", summary="当前会话信息")
async def me(user: auth.CurrentUser = Depends(auth.require_user)):
    cfg = config.load()
    return {
        "ok": True,
        "username": user.name,
        "ip": user.ip,
        "server_time": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "token_ttl_hours": cfg.token_ttl_hours,
    }


@router.post("/logout", summary="登出（前端清 token 即可，此处仅记审计）")
async def logout(user: auth.CurrentUser = Depends(auth.require_user)):
    auth.audit(user, "logout", user.name, "主动登出")
    return {"ok": True}


@router.post("/password", summary="修改密码（会使所有旧登录失效）")
async def change_password(
    body: ChangePwdReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    if not auth.verify_password(body.old_password):
        auth.audit(user, "passwd_fail", user.name, "原密码错误")
        raise HTTPException(401, "原密码错误")

    import re
    import bcrypt

    pw_hash = bcrypt.hashpw(body.new_password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()
    text = config.SECRET_FILE.read_text(encoding="utf-8")
    text = re.sub(r'password_hash\s*=\s*".*?"', f'password_hash = "{pw_hash}"', text)

    m = re.search(r"session_version\s*=\s*(\d+)", text)
    if m:
        new_v = int(m.group(1)) + 1
        text = text[: m.start()] + f"session_version = {new_v}" + text[m.end():]
    else:
        text = re.sub(r"(token_ttl_hours\s*=\s*\d+)", r"\1\nsession_version = 2", text)

    config.SECRET_FILE.write_text(text, encoding="utf-8")
    try:
        import os
        os.chmod(config.SECRET_FILE, 0o600)
    except Exception:
        pass
    config.reload()

    auth.audit(user, "passwd_change", user.name, "密码已修改，所有旧 token 失效")
    return {"ok": True, "message": "密码已修改，请重新登录"}
