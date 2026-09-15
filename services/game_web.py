"""
棋类在线对战 Web 服务（FastAPI）

设计：
- **挂在 bot 进程的 asyncio loop 上**（uvicorn.Server 作为 task）—— 直接共享
  modules.*_game 里的棋局状态，不需要 IPC / 数据库。
- 访问：`http://127.0.0.1:59400/go/{room}?t=<token>`（room = chat_id）
- 身份：HMAC 签名 token（`chat_id_userid_sig`），点开链接即身份，无感登录。
- 隧道：由 Cloudflare Tunnel 把 `truslerweb.dpdns.org/game/*` 指到 59400。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.logger import get_logger

logger = get_logger("gameweb")

PORT = int(os.environ.get("GAME_WEB_PORT", "59400"))
_ROOT = Path(__file__).resolve().parent.parent
_SECRET_FILE = _ROOT / "data" / "game_web_secret"

app = FastAPI(title="幻梦棋局", docs_url=None, redoc_url=None)


# ── token ────────────────────────────────────────────────────

def _secret() -> bytes:
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_bytes()
    s = secrets.token_bytes(32)
    try:
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_bytes(s)
    except Exception as e:
        logger.warning("写入 token 密钥失败: %s", e)
    return s


def make_token(chat_id: int, user_id: int) -> str:
    """生成身份 token（链接里带上，点开即身份）"""
    msg = f"{chat_id}:{user_id}".encode()
    sig = hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:16]
    return f"{chat_id}_{user_id}_{sig}"


def parse_token(token: str) -> Optional[tuple[int, int]]:
    """校验 token，返回 (chat_id, user_id)；非法返回 None"""
    try:
        chat_s, user_s, sig = token.split("_", 2)
        chat_id, user_id = int(chat_s), int(user_s)
    except Exception:
        return None
    expect = hmac.new(_secret(), f"{chat_id}:{user_id}".encode(),
                      hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, expect):
        return None
    return chat_id, user_id


def web_base() -> str:
    """对外访问基址（隧道配好后在 .env 里设 GAME_WEB_BASE）"""
    return os.environ.get("GAME_WEB_BASE", f"http://127.0.0.1:{PORT}").rstrip("/")


def game_link(chat_id: int, user_id: int, kind: str = "go") -> str:
    """生成对局链接（发给 QQ 用户）"""
    return f"{web_base()}/{kind}/{chat_id}?t={make_token(chat_id, user_id)}"


def _get_game(room: int):
    """取棋局；内存里没有就从文件补一次（跨进程 / bot 重启后仍能访问）"""
    from modules import go_game as G
    g = G.get_game(room)
    if g is None:
        try:
            G._load()
        except Exception as e:
            logger.warning("补读棋局失败: %s", e)
        g = G.get_game(room)
    return g


# ── 围棋 ─────────────────────────────────────────────────────

@app.get("/go/{room}", response_class=HTMLResponse)
async def go_page(room: int, t: str = ""):
    from modules import go_game as G
    who = parse_token(t)
    if not who or who[0] != room:
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:40px'>链接无效或已过期喵~</h2>",
                            status_code=403)
    game = _get_game(room)
    if not game:
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:40px'>这局已经结束了喵~</h2>",
                            status_code=404)
    tmpl = (_ROOT / "data" / "templates" / "go_web.html").read_text(encoding="utf-8")
    return HTMLResponse(tmpl
                        .replace("${ROOM}", str(room))
                        .replace("${TOKEN}", t))


@app.get("/api/go/{room}/state")
async def go_state(room: int, t: str = ""):
    from modules import go_game as G
    who = parse_token(t)
    if not who or who[0] != room:
        return JSONResponse({"ok": False, "error": "链接无效"}, status_code=403)
    game = _get_game(room)
    if not game:
        return JSONResponse({"ok": True, "finished": True, "board": None})
    caps = game.get("captures", {})
    return JSONResponse({
        "ok": True,
        "finished": game.get("status") != "playing",
        "board": game["board"],
        "turn": game.get("turn"),
        "player_color": game.get("player_color"),
        "move_count": game.get("move_count", 0),
        "last_move": game.get("last_move"),
        "captures": {"black": caps.get(G.BLACK, 0), "white": caps.get(G.WHITE, 0)},
        "difficulty": G.DIFFICULTIES.get(game.get("difficulty", "normal"), {}).get("label", "普通"),
        "bot": G._bot_name(),
    })


@app.post("/api/go/{room}/move")
async def go_move(room: int, request: Request, t: str = ""):
    from modules import go_game as G
    who = parse_token(t)
    if not who or who[0] != room:
        return JSONResponse({"ok": False, "error": "链接无效"}, status_code=403)
    _get_game(room)              # 确保内存里有该局（跨进程时从文件补）
    body = await request.json()
    ok, msg, _ = G.make_move(who[1], room, str(body.get("coord", "")))
    return JSONResponse({"ok": ok, "msg": msg})


@app.post("/api/go/{room}/pass")
async def go_pass(room: int, t: str = ""):
    from modules import go_game as G
    who = parse_token(t)
    if not who or who[0] != room:
        return JSONResponse({"ok": False, "error": "链接无效"}, status_code=403)
    _get_game(room)
    ok, msg, _ = G.do_pass(who[1], room)
    if not ok:
        return JSONResponse({"ok": False, "msg": msg})
    game = _get_game(room)
    if game and game.get("status") == "finished":
        msg += "\n" + G.end_game(room)
    return JSONResponse({"ok": True, "msg": msg})


@app.post("/api/go/{room}/resign")
async def go_resign(room: int, t: str = ""):
    from modules import go_game as G
    who = parse_token(t)
    if not who or who[0] != room:
        return JSONResponse({"ok": False, "error": "链接无效"}, status_code=403)
    _get_game(room)
    return JSONResponse({"ok": True, "msg": G.resign_game(who[1], room)})


# ── 启动 ─────────────────────────────────────────────────────

async def start_server() -> Optional[asyncio.Task]:
    """在 bot 的事件循环里起 web 服务（失败不影响 bot 主流程）"""
    try:
        import uvicorn
        config = uvicorn.Config(app, host="127.0.0.1", port=PORT,
                                log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        logger.info("棋局 Web 服务已启动: http://127.0.0.1:%d（对外走隧道）", PORT)
        return task
    except Exception as e:
        logger.warning("棋局 Web 服务启动失败（不影响 bot）: %s", e)
        return None
