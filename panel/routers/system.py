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


# ── 操作栈与崩溃自愈（v2.3.0） ────────────────────────────

@router.get("/ops", summary="操作栈：可以回滚到哪几步")
async def list_ops(limit: int = Query(30, ge=1, le=200)):
    """列出面板做过的写操作（新的在前）。

    这是崩溃自愈的依据，也是你手动后悔药：
    哪一步改坏了，直接回滚那一步，不用去翻 .bak 文件。
    """
    from panel import security as _sec
    ops = _sec.peek_ops(limit=limit)
    return {"ok": True, "count": len(ops), "ops": ops}


class RollbackReq(BaseModel):
    confirm: str = Field("", max_length=20)
    steps: int = Field(1, ge=1, le=10)


class ClearOpsReq(BaseModel):
    confirm: str = Field("", max_length=20)


@router.post("/ops/clear", summary="清空操作栈（不回滚，只清记录）")
async def clear_ops(
    body: ClearOpsReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """清空操作栈记录。

    ⚠️ 只清"可回滚"的记录，**不会还原任何文件** —— 想还原请用 /ops/rollback。
    用途：调试完留下一堆无关记录，或者你已经手动确认过改动没问题了，
    不想让自愈以后误回滚这些陈年操作。
    """
    from panel import security as _sec

    if body.confirm != "CLEAR":
        raise HTTPException(400, "清空需确认：请在 confirm 里填入 CLEAR")

    before = len(_sec.peek_ops(limit=999))
    _sec.clear_ops()
    auth.audit(user, "ops_clear", f"清掉 {before} 条", "未改动任何文件")
    return {"ok": True, "cleared": before, "note": "只清了记录，文件未变动"}


@router.post("/ops/rollback", summary="回滚最近 N 步写操作")
async def rollback_ops(
    body: RollbackReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """手动回滚。自愈只回滚一步，这里允许连退多步 —— 但同样克制，
    最多 10 步，且每一步都独立报告成功与否。"""
    from panel import security as _sec

    if body.confirm != "ROLLBACK":
        raise HTTPException(400, "回滚需确认：请在 confirm 里填入 ROLLBACK")

    results = []
    for _ in range(body.steps):
        rec = _sec.pop_op()
        if not rec:
            break
        ok, msg = _sec.rollback_op(rec)
        results.append({"op": rec, "ok": ok, "msg": msg})

    if not results:
        raise HTTPException(404, "操作栈是空的，没有可回滚的操作")

    auth.audit(user, "ops_rollback", f"{len(results)} 步",
               " | ".join(r["msg"] for r in results)[:300])
    return {
        "ok": all(r["ok"] for r in results),
        "rolled_back": len(results),
        "results": results,
        "need_restart": True,
    }


class ArmReq(BaseModel):
    on: bool = True


@router.post("/selfheal/toggle", summary="开关崩溃自愈")
async def selfheal_toggle(
    body: ArmReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    from panel import selfheal
    st = selfheal.get_state()
    st.enabled = body.on
    auth.audit(user, "selfheal_toggle", "on" if body.on else "off", "")
    return {"ok": True, "enabled": st.enabled}


@router.post("/selfheal/arm", summary="布防（前端在改动后调用）")
async def selfheal_arm(user: auth.CurrentUser = Depends(auth.require_user)):
    """手动布防 —— 前端在用户点了"重启 bot"之后调用。

    布防后进入高频探测档（5s 一次）；不布防则完全不探测。
    """
    from panel import selfheal
    selfheal.get_state().arm()
    auth.audit(user, "selfheal_arm", "", "手动布防")
    return {"ok": True, "phase": selfheal.get_state().phase()}


@router.post("/selfheal/keepalive", summary="心跳（延长高频探测窗口）")
async def selfheal_keepalive(
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """前端每隔一段时间调一次，表示"我还在盯着，别掉档"。

    走的是认证通道，所以有额外开销 —— 前端每 30s 调一次足够。
    未布防时这个接口是空操作（不布防就继续静默）。
    """
    from panel import selfheal
    st = selfheal.get_state()
    st.keep_alive()
    return {"ok": True, "phase": st.phase(),
            "armed": st._armed_op_ts is not None}


@router.post("/selfheal/disarm", summary="解除布防（确认改动没问题了）")
async def selfheal_disarm(user: auth.CurrentUser = Depends(auth.require_user)):
    """改完确认没事后点这个，回到静默不再探测。

    其实不点也行 —— bot 恢复正常后自动解除。但显式一点更清楚。
    """
    from panel import selfheal
    selfheal.get_state().disarm()
    auth.audit(user, "selfheal_disarm", "", "")
    return {"ok": True, "phase": "idle"}


@router.get("/selfheal", summary="崩溃自愈状态")
async def selfheal_status():
    from panel import selfheal
    return {"ok": True, **selfheal.get_state().snapshot()}


class ClearEventsReq(BaseModel):
    confirm: str = Field("", max_length=20)


@router.post("/selfheal/events/clear", summary="清空自愈事件记录")
async def selfheal_clear_events(
    body: ClearEventsReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """清掉自愈事件列表（只清展示记录，不改文件、不影响布防）。"""
    if body.confirm != "CLEAR":
        raise HTTPException(400, "清空需确认：请在 confirm 里填入 CLEAR")

    from panel import selfheal
    st = selfheal.get_state()
    n = len(st.events)
    st.events.clear()
    auth.audit(user, "selfheal_events_clear", f"{n} 条", "")
    return {"ok": True, "cleared": n}


class HealTestReq(BaseModel):
    confirm: str = Field("", max_length=20)


@router.post("/selfheal/test", summary="演练：模拟一次自愈（不真的回滚）")
async def selfheal_test(
    body: HealTestReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """演练模式：走一遍判定逻辑但不改动任何文件。

    为什么需要：自愈是"出事才会跑"的代码，平时没机会验证。
    演练让你确认阈值、冷却、栈顶判断都是对的 ——
    否则真出事时第一次运行就是最后一次机会。
    """
    if body.confirm != "TEST":
        raise HTTPException(400, "演练需确认：请在 confirm 里填入 TEST")

    from panel import security as _sec
    from panel import selfheal
    st = selfheal.get_state()

    ops = _sec.peek_ops(limit=1)
    # ⚠️ 判据要看 NRestarts / MainPID，不能看 is-active（Restart=always 下恒为
    # active，见 selfheal 模块头部的实测记录）。这里复用同一套判定逻辑，
    # 避免演练结论和真实自愈判定不一致。
    snap = st._unit_snapshot()
    healthy, reason = st._judge(snap)
    failed = st._unit_failed()

    return {
        "ok": True,
        "dry_run": True,
        "bot_alive": healthy,
        "bot_healthy_reason": reason,
        "unit_failed": failed,
        "unit_active_state": snap.get("ActiveState"),
        "unit_main_pid": snap.get("_pid"),
        "unit_n_restarts": snap.get("_n"),
        "crash_loop": st._in_crash_loop(),
        "armed": st._armed_op_ts is not None,
        "enabled": st.enabled,
        "consecutive_fail": st.consecutive_fail,
        "fail_threshold": selfheal.FAIL_THRESHOLD,
        "cooling": st._is_cooling(),
        "would_rollback": ops[0] if ops else None,
        "rollback_scope": (
            "只回滚布防时记住的那一条操作，不是无脑弹栈顶"
            if st._armed_op_ts not in (None, "__manual__")
            else "当前未绑定具体操作，若触发将回滚栈顶一条"
        ),
        "conclusion": (
            "若此刻 bot 连续失败 "
            f"{selfheal.FAIL_THRESHOLD} 次，将会回滚上表这一条操作"
            if ops else
            "操作栈为空，即使 bot 崩了也不会自动回滚（需要人工介入）"
        ),
    }
