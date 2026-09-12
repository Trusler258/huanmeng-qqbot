"""系统运维：服务管理 / 端口 / 更新日志 / 配置浏览（脱敏）/ 审计日志

⚠️ 本模块包含**最高危**操作（重启服务）。所有破坏性动作都要求
   `confirm` 字段精确匹配，且记审计。
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panel import auth, config
from panel.security import sanitize_text

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/system", tags=["系统"], dependencies=[Depends(auth.require_user)])

ROOT = config.ROOT

# 允许面板管理的服务（白名单，绝不允许任意 service 名）
ALLOWED_SERVICES = {
    "bot.service", "panel.service", "napcat", "cloudflared",
    "kook-bot.service", "nginx",
}


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "命令超时"
    except Exception as e:
        return -1, "", str(e)


@router.get("/services", summary="服务状态一览")
async def services():
    out = []
    for s in sorted(ALLOWED_SERVICES):
        code, so, _ = _run(["systemctl", "is-active", s], timeout=3)
        _, sub, _ = _run(["systemctl", "show", s, "-p", "SubState", "-p", "ActiveEnterTimestamp"], timeout=3)
        props = dict(
            line.split("=", 1) for line in sub.splitlines() if "=" in line
        )
        out.append({
            "name": s,
            "active": so or "unknown",
            "in_service": code == 0,
            "sub_state": props.get("SubState", ""),
            "since": props.get("ActiveEnterTimestamp", ""),
        })
    return {"ok": True, "items": out}


class ServiceActionReq(BaseModel):
    service: str = Field(max_length=60)
    action: str = Field(max_length=20)   # restart / start / stop
    confirm: str = Field("", max_length=60)


@router.post("/services/action", summary="重启/启停服务（危险，需二次确认）")
async def service_action(
    body: ServiceActionReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    if body.service not in ALLOWED_SERVICES:
        auth.audit(user, "service_action_denied", body.service, "不在白名单")
        raise HTTPException(403, f"服务 {body.service} 不在允许列表内")
    if body.action not in ("restart", "start", "stop"):
        raise HTTPException(400, "action 只能是 restart/start/stop")

    # 二次确认：必须精确输入服务名
    if body.confirm != body.service:
        raise HTTPException(
            400,
            f"危险操作需二次确认：请在 confirm 字段中原样填入服务名 {body.service}",
        )

    code, out, err = _run(["systemctl", body.action, body.service], timeout=30)
    auth.audit(user, f"service_{body.action}", body.service,
               f"rc={code} {out} {err}")
    if code != 0:
        raise HTTPException(500, f"操作失败: {err or out}")
    return {"ok": True, "service": body.service, "action": body.action, "output": out}


@router.get("/ports", summary="监听端口一览")
async def ports():
    code, out, err = _run(["ss", "-lntp"], timeout=6)
    if code != 0:
        code, out, err = _run(["netstat", "-lntp"], timeout=6)
    rows = []
    for ln in out.splitlines()[1:]:
        parts = ln.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        addr, _, port = local.rpartition(":")
        proc = parts[-1] if len(parts) >= 6 else ""
        # 提取进程名
        m = re.search(r'users:\(\("([^"]+)"', proc)
        # 端口安全评估
        bind_all = addr in ("0.0.0.0", "*", "[::]", "::")
        rows.append({
            "addr": addr,
            "port": port,
            "process": m.group(1) if m else "",
            "exposed": bind_all,
        })
    rows.sort(key=lambda r: int(r["port"]) if r["port"].isdigit() else 0)
    exposed = [r for r in rows if r["exposed"]]
    return {"ok": True, "total": len(rows), "items": rows, "exposed_count": len(exposed)}


@router.get("/update-log", summary="更新日志（倒序）")
async def update_log(limit: int = Query(5, ge=1, le=100)):
    path = config.DATA_DIR / "update_log.md"
    if not path.exists():
        raise HTTPException(404, "update_log.md 不存在")
    raw = path.read_text(encoding="utf-8")

    # 按 "## vX.Y.Z" 切条目
    entries = []
    cur = None
    for line in raw.splitlines():
        if line.startswith("## "):
            if cur:
                entries.append(cur)
            cur = {"title": line[3:].strip(), "body": ""}
        elif cur is not None:
            cur["body"] += line + "\n"
    if cur:
        entries.append(cur)

    for e in entries:
        m = re.match(r"(v[\d.]+)", e["title"])
        e["version"] = m.group(1) if m else ""
        e["body"] = e["body"].strip()

    return {
        "ok": True, "total": len(entries),
        "current": _current_version(),
        "items": entries[:limit],
    }


def _current_version() -> str:
    vf = ROOT / "config" / "version.toml"
    if vf.exists():
        m = re.search(r'current\s*=\s*"([^"]+)"', vf.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return ""


@router.get("/config", summary="配置文件列表（脱敏）")
async def config_list():
    out = []
    cfg_dir = ROOT / "config"
    for p in sorted(cfg_dir.glob("*")):
        if not p.is_file():
            continue
        st = p.stat()
        out.append({
            "name": p.name,
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, CST).strftime("%Y-%m-%d %H:%M:%S"),
            "editable": p.name in ("lang.toml",),
            "readable": p.suffix in (".toml", ".md", ".json", ".txt"),
        })
    return {"ok": True, "items": out}


@router.get("/config/{name}", summary="读配置文件（密钥自动打码）")
async def config_read(name: str):
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    path = ROOT / "config" / name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "文件不存在")
    if path.suffix not in (".toml", ".md", ".json", ".txt"):
        raise HTTPException(403, "该类型文件不允许在线预览")

    raw = path.read_text(encoding="utf-8", errors="ignore")
    masked = sanitize_text(raw)
    return {
        "ok": True,
        "name": name,
        "size": len(raw),
        "masked": masked != raw,
        "raw": masked,
    }


@router.get("/audit", summary="审计日志")
async def audit_log(limit: int = Query(100, ge=1, le=2000)):
    path = config.AUDIT_FILE
    if not path.exists():
        return {"ok": True, "items": [], "total": 0}

    import json
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:
        raise HTTPException(500, str(e))

    rows.reverse()
    return {"ok": True, "total": len(rows), "items": rows[:limit]}


@router.get("/info", summary="系统信息")
async def sys_info():
    mem = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                mem[k.strip()] = int(v.strip().split()[0])
    except Exception:
        pass
    code, up, _ = _run(["uptime", "-p"], timeout=3)
    _, disk, _ = _run(["df", "-h", str(ROOT)], timeout=5)
    return {
        "ok": True,
        "python": __import__("sys").version.split()[0],
        "panel_version": config.PANEL_API_VERSION,
        "bot_version": _current_version(),
        "uptime": up,
        "disk": disk.splitlines()[-1] if disk else "",
        "mem_total_kb": mem.get("MemTotal", 0),
        "mem_available_kb": mem.get("MemAvailable", 0),
        "root": str(ROOT),
    }
