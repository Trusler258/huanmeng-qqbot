"""认证：登录签发 JWT + 路由依赖校验（默认拒绝）"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from panel import config
from panel.security import atomic_write_text

CST = timezone(timedelta(hours=8))

# auto_error=False：没带 token 时不直接 403，由我们自己返回统一 401
_bearer = HTTPBearer(auto_error=False)

# ── 登录失败限速（内存计数，重启即清，够用） ──────────────
#
# v2.3.0 起从"固定阈值"升级为"递增封禁"。
# 原因：去掉 nginx BasicAuth 后应用层成了唯一防线，固定 5 次/5 分钟
# 挡不住慢速爆破（每 5 分钟试 5 次，一天 1440 次）。
#
# 现在按**连续失败轮次**递增：每再触发一轮，锁定时间翻倍。
#   第 1 轮 → 5 分钟
#   第 2 轮 → 10 分钟
#   第 3 轮 → 20 分钟 …直到上限 24 小时
# 成功登录立刻清零 —— 正常用户打错一次密码不受影响。
_fails: dict[str, list[float]] = {}
_rounds: dict[str, int] = {}          # ip -> 已触发过几轮封禁
_banned_until: dict[str, float] = {}  # ip -> 解封时间戳


def _client_ip(request: Request) -> str:
    """取真实客户端 IP——经 CF Tunnel + nginx 后要读 X-Forwarded-For"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip", "")
    if xri:
        return xri.strip()
    return request.client.host if request.client else "unknown"


# 递增封禁的上限（秒）。24 小时 —— 再往上就没意义了，攻击者换个 IP 成本更低
_MAX_LOCK = 24 * 3600


def login_locked(ip: str) -> int:
    """返回剩余锁定秒数，0 表示未锁定"""
    now = time.time()
    cfg = config.load()

    # 递增封禁优先
    until = _banned_until.get(ip, 0)
    if until > now:
        return int(until - now) + 1
    if until:
        _banned_until.pop(ip, None)

    rec = [t for t in _fails.get(ip, []) if now - t < cfg.login_lock_seconds]
    _fails[ip] = rec
    if len(rec) >= cfg.login_max_fail:
        return int(cfg.login_lock_seconds - (now - rec[0])) + 1
    return 0


def record_fail(ip: str) -> int:
    """记一次失败。达到阈值时升级为递增封禁，返回封禁秒数（0 表示未封）。"""
    cfg = config.load()
    now = time.time()
    _fails.setdefault(ip, []).append(now)

    recent = [t for t in _fails[ip] if now - t < cfg.login_lock_seconds]
    if len(recent) < cfg.login_max_fail:
        return 0

    # 触发一轮封禁
    rounds = _rounds.get(ip, 0) + 1
    _rounds[ip] = rounds
    lock = min(cfg.login_lock_seconds * (2 ** (rounds - 1)), _MAX_LOCK)
    _banned_until[ip] = now + lock
    _fails[ip] = []          # 清空计数，下一轮重新累积
    return int(lock)


def clear_fails(ip: str) -> None:
    """登录成功：计数与轮次全清。正常用户不该被历史拖累。"""
    _fails.pop(ip, None)
    _rounds.pop(ip, None)
    _banned_until.pop(ip, None)


def fail_stats(ip: str) -> dict:
    """给登录接口用，返回当前这个 IP 的状态（便于前端提示还要等多久）"""
    cfg = config.load()
    now = time.time()
    return {
        "recent_fails": len([t for t in _fails.get(ip, [])
                             if now - t < cfg.login_lock_seconds]),
        "rounds": _rounds.get(ip, 0),
        "locked_seconds": login_locked(ip),
    }


# ── Token ────────────────────────────────────────────────

def verify_password(password: str) -> bool:
    cfg = config.load()
    if not cfg.password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), cfg.password_hash.encode())
    except Exception:
        return False


def issue_token(username: str = "admin") -> tuple[str, int]:
    """签发 JWT，返回 (token, 过期时间戳)"""
    cfg = config.load()
    exp = datetime.now(timezone.utc) + timedelta(hours=cfg.token_ttl_hours)
    payload = {
        "sub": username,
        "sv": cfg.session_version,   # 改密后 +1 → 旧 token 全部失效
        "iat": int(time.time()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, cfg.jwt_secret, algorithm="HS256")
    return token, int(exp.timestamp())


def decode_token(token: str) -> dict:
    cfg = config.load()
    payload = jwt.decode(token, cfg.jwt_secret, algorithms=["HS256"])
    if int(payload.get("sv", -1)) != cfg.session_version:
        raise jwt.InvalidTokenError("会话已失效")
    return payload


# ── FastAPI 依赖（默认拒绝） ──────────────────────────────

class CurrentUser:
    __slots__ = ("name", "ip")

    def __init__(self, name: str, ip: str):
        self.name = name
        self.ip = ip


async def require_user(
    request: Request,
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    """所有受保护接口的依赖。无 token / token 无效 → 401。"""
    if cred is None or not cred.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(cred.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except Exception:
        raise HTTPException(status_code=401, detail="凭据无效")
    return CurrentUser(payload.get("sub", "admin"), _client_ip(request))


# ── 审计日志 ──────────────────────────────────────────────

def audit(user: CurrentUser | None, action: str, target: str, detail: str = "", request: Request | None = None):
    """写审计日志（JSONL，一行一条，append-only）

    写操作必须调用本函数。detail 里禁止放密钥——用 security.sanitize_obj 兜底。
    """
    from panel.security import sanitize_text
    rec = {
        "ts": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "user": user.name if user else "anonymous",
        "ip": user.ip if user else (_client_ip(request) if request else "unknown"),
        "action": action,
        "target": sanitize_text(str(target))[:300],
        "detail": sanitize_text(str(detail))[:1000],
    }
    try:
        with open(config.AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
