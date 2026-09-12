"""实时日志：HTTP 拉取历史 + WebSocket 实时推送

与 `core/log_server.py` 的区别：那边是给"看图"的，这边是给面板用的结构化 API。
面板**不直接复用** log_server 的 WebSocket（它在 bot 进程里、端口 58888），
改为自己 tail 日志文件 + WS 推送，好处是 panel 独立进程、崩了不影响 bot。
"""

from __future__ import annotations

import asyncio
import re
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from panel import auth, config
from panel.security import sanitize_text

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/logs", tags=["日志"])

LOG_DIR = config.ROOT / "logs"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# 形如 2026-09-12 17:49:57,123 | INFO | module | message
_LINE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})[,.](\d{3})?\s*"
    r"[|\s]\s*(?P<level>[A-Z]+)\s*[|\s]\s*(?P<src>[^|\s]+)\s*[|\s]?\s*(?P<msg>.*)$"
)


def _log_files() -> list[dict]:
    if not LOG_DIR.exists():
        return []
    out = []
    for p in sorted(LOG_DIR.glob("*.log*"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            st = p.stat()
            if not p.is_file():
                continue
            out.append({
                "name": p.name,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, CST).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception:
            pass
    return out


def _tail(path: Path, lines: int, keyword: str = "", level: str = "") -> list[dict]:
    """读文件尾部若干行并解析"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, 512 * 1024)
            f.seek(max(0, size - block))
            raw = f.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    raw = _ANSI.sub("", raw)
    out: list[dict] = []
    for ln in raw.split("\n"):
        ln = ln.rstrip()
        if not ln:
            continue
        if keyword and keyword not in ln:
            continue
        m = _LINE.match(ln)
        if m:
            lv = m.group("level").upper()
            if level and lv != level.upper():
                continue
            out.append({
                "ts": f'{m.group("time")}',
                "date": m.group("date"),
                "lv": lv,
                "src": m.group("src"),
                "msg": sanitize_text(m.group("msg"))[:2000],
                "raw": False,
            })
        else:
            if level:
                continue
            out.append({
                "ts": "", "date": "", "lv": "INFO", "src": "raw",
                "msg": sanitize_text(ln)[:2000], "raw": True,
            })
    return out[-lines:]


@router.get("/files", summary="日志文件列表",
            dependencies=[Depends(auth.require_user)])
async def files():
    return {"ok": True, "files": _log_files(), "dir": str(LOG_DIR)}


@router.get("", summary="拉取日志尾部",
            dependencies=[Depends(auth.require_user)])
async def get_logs(
    file: str = Query("huanmeng.log", max_length=100),
    lines: int = Query(300, ge=1, le=5000),
    keyword: str = Query("", max_length=100),
    level: str = Query("", max_length=20),
):
    """读指定日志文件尾部。文件名做了严格白名单校验防目录穿越。"""
    if "/" in file or "\\" in file or ".." in file:
        return {"ok": False, "error": "非法文件名", "logs": []}
    path = LOG_DIR / file
    if not path.exists() or not path.is_file():
        return {"ok": False, "error": f"日志文件不存在: {file}", "logs": []}
    return {"ok": True, "file": file, "logs": _tail(path, lines, keyword, level)}


# ── WebSocket 实时推送 ────────────────────────────────────

@router.websocket("/ws")
async def ws_logs(ws: WebSocket):
    """实时日志推送。

    认证：WS 无法带 Authorization header（浏览器限制），改用 query 参数 `token`。
    校验失败直接 close(4401)。
    """
    token = ws.query_params.get("token", "")
    try:
        auth.decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return

    await ws.accept()
    path = LOG_DIR / "huanmeng.log"
    if not path.exists():
        await ws.send_text(json.dumps({"type": "error", "msg": "日志文件不存在"}))
        await ws.close()
        return

    # 先补最近 100 行，再进入增量 tail
    try:
        for rec in _tail(path, 100):
            await ws.send_text(json.dumps({"type": "log", **rec}, ensure_ascii=False))
    except Exception:
        pass

    pos = path.stat().st_size
    last_ping = asyncio.get_event_loop().time()

    try:
        while True:
            await asyncio.sleep(0.6)
            try:
                size = path.stat().st_size
            except Exception:
                continue
            # 日志轮转
            if size < pos:
                pos = 0
            if size > pos:
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
                    pos = f.tell()
                text = _ANSI.sub("", chunk.decode("utf-8", errors="ignore"))
                for ln in text.split("\n"):
                    ln = ln.rstrip()
                    if not ln:
                        continue
                    m = _LINE.match(ln)
                    if m:
                        rec = {
                            "type": "log",
                            "ts": m.group("time"),
                            "date": m.group("date"),
                            "lv": m.group("level").upper(),
                            "src": m.group("src"),
                            "msg": sanitize_text(m.group("msg"))[:2000],
                        }
                    else:
                        rec = {
                            "type": "log", "ts": "", "date": "", "lv": "INFO",
                            "src": "raw", "msg": sanitize_text(ln)[:2000],
                        }
                    await ws.send_text(json.dumps(rec, ensure_ascii=False))
            # 心跳
            now = asyncio.get_event_loop().time()
            if now - last_ping > 25:
                await ws.send_text(json.dumps({"type": "ping"}))
                last_ping = now
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass
