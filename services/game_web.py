"""
棋类在线对战 Web 服务（FastAPI）

设计：
- **挂在 bot 进程的 asyncio loop 上**（uvicorn.Server 作为 task）—— 直接共享
  modules.*_game 里的棋局状态，不需要 IPC / 数据库。
- 访问：`http://127.0.0.1:59400/{kind}/{room}?t=<token>`（room = chat_id）
  kind ∈ go（围棋）/ wzq（五子棋）/ xq（中国象棋）
- 身份：HMAC 签名 token（`chat_id_userid_sig`），点开链接即身份，无感登录；
  另外还要求 token 持有者确实是这局的参与者，否则 403。
- 隧道：由 Cloudflare Tunnel 把 `game.truslerweb.dpdns.org/*` 指到 59400。
- 静态资源：`/static/{name}`，白名单 + 缓存头（像素字体别内联进页面）。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
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
_TEMPLATES = _ROOT / "data" / "templates"
_ASSETS = _ROOT / "data" / "web_assets"

app = FastAPI(title="幻梦棋局", docs_url=None, redoc_url=None)

# 静态资源白名单：name -> (media_type, cache-control)
_STATIC = {
    "monocraft.ttf": ("font/ttf", "public, max-age=604800"),
    "game.css": ("text/css; charset=utf-8", "public, max-age=3600"),
}


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


# ── 鉴权（每种棋局归属不同） ──────────────────────────────────

def _auth_player(room: int, t: str):
    """围棋：只有房主本人能访问。返回 (user_id, game, err)"""
    from modules import go_game as G
    who = parse_token(t)
    if not who or who[0] != room:
        return None, None, "链接无效或已过期"
    game = G.get_game(room)
    if game is None:
        try:
            G._load()
        except Exception as e:
            logger.warning("补读围棋棋局失败: %s", e)
        game = G.get_game(room)
    if game is not None and game.get("player_id") != who[1]:
        return None, None, "这条链接不属于你"
    return who[1], game, None


def _auth_wzq(room: int, t: str):
    """五子棋：黑方和白方都能访问（各有自己的 token）"""
    from modules import wzq as W
    who = parse_token(t)
    if not who or who[0] != room:
        return None, None, "链接无效或已过期"
    game = W.get_game(room)
    if game is None:
        try:
            W.web_load()
        except Exception as e:
            logger.warning("补读五子棋对局失败: %s", e)
        game = W.get_game(room)
    if game is not None and who[1] not in (game.black, game.white):
        return None, None, "这条链接不属于你"
    return who[1], game, None


def _auth_xq(room: int, t: str):
    """象棋：只有房主本人（对手是 AI）"""
    from modules import chinese_chess as X
    who = parse_token(t)
    if not who or who[0] != room:
        return None, None, "链接无效或已过期"
    game = X.get_game(room)
    if game is not None and game.get("player_id") != who[1]:
        return None, None, "这条链接不属于你"
    return who[1], game, None


# ── 页面渲染 ─────────────────────────────────────────────────

def _page(tmpl_name: str, room: int, t: str, request: Request) -> HTMLResponse:
    """读模板并替换占位符。

    静态资源用「相对本页的前缀」，兼容隧道挂在子路径的情况。
    """
    prefix = request.url.path.rsplit("/", 2)[0]
    tmpl = (_TEMPLATES / tmpl_name).read_text(encoding="utf-8")
    return HTMLResponse(tmpl
                        .replace("${ROOM}", str(room))
                        .replace("${CSS_URL}", f"{prefix}/static/game.css")
                        .replace("${TOKEN}", t))


def _deny(err: str, as_html: bool = False):
    if as_html:
        return HTMLResponse(f"<h2 style='font-family:sans-serif;padding:40px'>{err}喵~</h2>",
                            status_code=403)
    return JSONResponse({"ok": False, "error": err}, status_code=403)


def _no_game(as_html: bool = False):
    if as_html:
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:40px'>这局已经结束了喵~</h2>",
                            status_code=404)
    return JSONResponse({"ok": False, "msg": "这局已经结束了喵~"})


# ── 静态资源 ─────────────────────────────────────────────────

@app.get("/static/{name}")
async def static_asset(name: str):
    meta = _STATIC.get(name)
    if not meta:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    f = _ASSETS / name
    if not f.exists():
        return JSONResponse({"ok": False, "error": "missing"}, status_code=404)
    return FileResponse(str(f), media_type=meta[0], headers={"Cache-Control": meta[1]})


# ── 围棋 ─────────────────────────────────────────────────────

@app.get("/go/{room}", response_class=HTMLResponse)
async def go_page(room: int, request: Request, t: str = ""):
    uid, game, err = _auth_player(room, t)
    if err:
        return _deny(err, as_html=True)
    if not game:
        return _no_game(as_html=True)
    return _page("go_web.html", room, t, request)


@app.get("/api/go/{room}/state")
async def go_state(room: int, t: str = ""):
    from modules import go_game as G
    uid, game, err = _auth_player(room, t)
    if err:
        return _deny(err)
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
    uid, game, err = _auth_player(room, t)
    if err:
        return _deny(err)
    if not game:
        return _no_game()
    body = await request.json()
    ok, msg, _ = G.make_move(uid, room, str(body.get("coord", "")))
    return JSONResponse({"ok": ok, "msg": msg})


@app.post("/api/go/{room}/pass")
async def go_pass(room: int, t: str = ""):
    from modules import go_game as G
    uid, game, err = _auth_player(room, t)
    if err:
        return _deny(err)
    if not game:
        return _no_game()
    ok, msg, _ = G.do_pass(uid, room)
    if not ok:
        return JSONResponse({"ok": False, "msg": msg})
    game = G.get_game(room)
    if game and game.get("status") == "finished":
        msg += "\n" + G.end_game(room)
    return JSONResponse({"ok": True, "msg": msg})


@app.post("/api/go/{room}/resign")
async def go_resign(room: int, t: str = ""):
    from modules import go_game as G
    uid, game, err = _auth_player(room, t)
    if err:
        return _deny(err)
    if not game or game.get("status") != "playing":
        return JSONResponse({"ok": False, "msg": "这局已经结束了喵~"})
    return JSONResponse({"ok": True, "msg": G.resign_game(uid, room)})


# ── 五子棋 ───────────────────────────────────────────────────

@app.get("/wzq/{room}", response_class=HTMLResponse)
async def wzq_page(room: int, request: Request, t: str = ""):
    uid, game, err = _auth_wzq(room, t)
    if err:
        return _deny(err, as_html=True)
    if not game:
        return _no_game(as_html=True)
    return _page("wzq_web.html", room, t, request)


@app.get("/api/wzq/{room}/state")
async def wzq_state(room: int, t: str = ""):
    from modules import wzq as W
    uid, game, err = _auth_wzq(room, t)
    if err:
        return _deny(err)
    st = W.web_state(room, uid)
    if st is None:
        return JSONResponse({"ok": True, "gone": True})
    st["ok"] = True
    st["finished"] = game.status == "finished"
    return JSONResponse(st)


@app.post("/api/wzq/{room}/move")
async def wzq_move(room: int, request: Request, t: str = ""):
    from modules import wzq as W
    uid, game, err = _auth_wzq(room, t)
    if err:
        return _deny(err)
    if not game:
        return _no_game()
    if game.status != "playing":
        return JSONResponse({"ok": False, "msg": "对局还没开始或已经结束了喵~"})
    body = await request.json()
    try:
        r, c = int(body.get("r")), int(body.get("c"))
    except Exception:
        return JSONResponse({"ok": False, "msg": "坐标不对喵~"})

    ok, msg = W.web_move(room, uid, r, c)
    if not ok:
        return JSONResponse({"ok": False, "msg": msg})

    # 人机模式：AI 立刻应一手（放线程池，不阻塞事件循环）
    g2 = W.get_game(room)
    if g2 and g2.status == "playing" and g2.white == 0 and g2.turn == 2:
        try:
            ai_ok, ai_msg = await W.ai_move_async(room)
            if ai_ok:
                g3 = W.get_game(room)
                lm = g3.last_move if g3 else None
                if lm:
                    msg += f"；{W._bot_name()} 落子 {W.coord_label(lm[0], lm[1])}"
                if ai_msg == "win":
                    msg += f"，五连！{W._bot_name()} 获胜"
        except Exception as e:
            logger.warning("五子棋 AI 应手失败: %s", e)

    return JSONResponse({"ok": True, "msg": msg})


@app.post("/api/wzq/{room}/resign")
async def wzq_resign(room: int, t: str = ""):
    from modules import wzq as W
    uid, game, err = _auth_wzq(room, t)
    if err:
        return _deny(err)
    if not game or game.status != "playing":
        return JSONResponse({"ok": False, "msg": "当前没有进行中的对局喵~"})
    return JSONResponse({"ok": True, "msg": W.web_resign(room, uid)[1]})


# ── 中国象棋 ─────────────────────────────────────────────────

@app.get("/xq/{room}", response_class=HTMLResponse)
async def xq_page(room: int, request: Request, t: str = ""):
    uid, game, err = _auth_xq(room, t)
    if err:
        return _deny(err, as_html=True)
    if not game:
        return _no_game(as_html=True)
    return _page("xq_web.html", room, t, request)


@app.get("/api/xq/{room}/state")
async def xq_state(room: int, t: str = ""):
    from modules import chinese_chess as X
    uid, game, err = _auth_xq(room, t)
    if err:
        return _deny(err)
    st = X.web_state(room, uid)
    if st is None:
        return JSONResponse({"ok": True, "gone": True})
    st["ok"] = True
    return JSONResponse(st)


@app.post("/api/xq/{room}/move")
async def xq_move(room: int, request: Request, t: str = ""):
    from modules import chinese_chess as X
    uid, game, err = _auth_xq(room, t)
    if err:
        return _deny(err)
    if not game:
        return _no_game()
    if game.get("finished"):
        return JSONResponse({"ok": False, "msg": "这局已经结束了喵~"})
    body = await request.json()
    notation = str(body.get("uci") or body.get("notation") or "").strip()
    if not notation:
        return JSONResponse({"ok": False, "msg": "没收到走法喵~"})
    # 象棋 AI 是同步阻塞实现（时间预算最长 3s），放线程池执行，别卡住 bot 事件循环
    loop = asyncio.get_running_loop()
    ok, msg = await loop.run_in_executor(None, X.web_move, uid, room, notation)
    return JSONResponse({"ok": bool(ok), "msg": msg})


@app.post("/api/xq/{room}/resign")
async def xq_resign(room: int, t: str = ""):
    from modules import chinese_chess as X
    uid, game, err = _auth_xq(room, t)
    if err:
        return _deny(err)
    if not game:
        return _no_game()
    ok, msg = X.web_resign(uid, room)
    return JSONResponse({"ok": bool(ok), "msg": msg})


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
