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
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

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


def _auth_player(room: int, t: str):
    """校验链接令牌，并确认持有者就是这局的主人。

    返回 (user_id, game, err)；err 非空表示拒绝访问。
    """
    who = parse_token(t)
    if not who or who[0] != room:
        return None, None, "链接无效或已过期"
    game = _get_game(room)
    if game is not None and game.get("player_id") != who[1]:
        return None, None, "这条链接不属于你"
    return who[1], game, None


# ── 静态资源 ─────────────────────────────────────────────────

@app.get("/static/monocraft.ttf")
async def monocraft():
    """像素字体（MC 风格），单独成文件便于浏览器缓存"""
    f = _ROOT / "data" / "web_assets" / "monocraft.ttf"
    if not f.exists():
        return JSONResponse({"ok": False, "error": "font missing"}, status_code=404)
    return FileResponse(str(f), media_type="font/ttf",
                        headers={"Cache-Control": "public, max-age=604800"})


# ── 围棋 ─────────────────────────────────────────────────────

@app.get("/go/{room}", response_class=HTMLResponse)
async def go_page(room: int, request: Request, t: str = ""):
    uid, game, err = _auth_player(room, t)
    if err:
        return HTMLResponse(
            f"<h2 style='font-family:sans-serif;padding:40px'>{err}喵~</h2>", status_code=403)
    if not game:
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:40px'>这局已经结束了喵~</h2>",
                            status_code=404)
    # 静态资源用相对本页的前缀，兼容隧道挂在子路径的情况
    prefix = request.url.path.rsplit("/", 2)[0]
    tmpl = (_ROOT / "data" / "templates" / "go_web.html").read_text(encoding="utf-8")
    return HTMLResponse(tmpl
                        .replace("${ROOM}", str(room))
                        .replace("${FONT_URL}", f"{prefix}/static/monocraft.ttf")
                        .replace("${TOKEN}", t))


@app.get("/api/go/{room}/state")
async def go_state(room: int, t: str = ""):
    from modules import go_game as G
    uid, game, err = _auth_player(room, t)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=403)
    if not game:
        return JSONResponse({"ok": True, "finished": True, "board": None})
    caps = game.get("captures", {})
    board = game.get("board") or []
    size = len(board) or G.BOARD_SIZE
    st_b = sum(row.count(G.BLACK) for row in board) if board else 0
    st_w = sum(row.count(G.WHITE) for row in board) if board else 0
    moves = list(game.get("moves") or [])
    return JSONResponse({
        "ok": True,
        "finished": game.get("status") != "playing",
        "board": game["board"],
        "size": size,
        "letters": G.letters_of(size),
        "stars": sorted(f"{r},{c}" for r, c in G.star_points(size)),
        "turn": game.get("turn"),
        "player_color": game.get("player_color"),
        "move_count": game.get("move_count", 0),
        "last_move": game.get("last_move"),
        "captures": {"black": caps.get(G.BLACK, 0), "white": caps.get(G.WHITE, 0)},
        "stones": {"black": st_b, "white": st_w},
        "moves": moves[-60:],
        "passes": game.get("passes", 0),
        "elapsed": max(0, int(time.time()) - int(game.get("start_time") or 0)),
        "final_score": game.get("final_score"),
        "result_text": game.get("result_text"),
        "difficulty": G.DIFFICULTIES.get(game.get("difficulty", "normal"), {}).get("label", "普通"),
        "bot": G._bot_name(),
    })


@app.post("/api/go/{room}/move")
async def go_move(room: int, request: Request, t: str = ""):
    from modules import go_game as G
    uid, game, err = _auth_player(room, t)   # _auth_player 内部会补读棋局
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=403)
    if not game:
        return JSONResponse({"ok": False, "msg": "这局已经结束了喵~"})
    body = await request.json()
    ok, msg, _ = G.make_move(uid, room, str(body.get("coord", "")))
    return JSONResponse({"ok": ok, "msg": msg})


@app.post("/api/go/{room}/pass")
async def go_pass(room: int, t: str = ""):
    from modules import go_game as G
    uid, game, err = _auth_player(room, t)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=403)
    if not game:
        return JSONResponse({"ok": False, "msg": "这局已经结束了喵~"})
    ok, msg, _ = G.do_pass(uid, room)
    if not ok:
        return JSONResponse({"ok": False, "msg": msg})
    game = _get_game(room)
    if game and game.get("status") == "finished":
        msg += "\n" + G.end_game(room)
    return JSONResponse({"ok": True, "msg": msg})


@app.post("/api/go/{room}/resign")
async def go_resign(room: int, t: str = ""):
    from modules import go_game as G
    uid, game, err = _auth_player(room, t)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=403)
    if not game or game.get("status") != "playing":
        return JSONResponse({"ok": False, "msg": "这局已经结束了喵~"})
    return JSONResponse({"ok": True, "msg": G.resign_game(uid, room)})


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
