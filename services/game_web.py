"""
棋类在线对战 Web 服务（FastAPI）

设计：
- **挂在 bot 进程的 asyncio loop 上**（uvicorn.Server 作为 task）—— 直接共享
  modules.*_game 里的棋局状态，不需要 IPC / 数据库。
- **房间号**：每个 (棋种, 群) 对应一个 4 位易读房间号（`data/game_rooms.json`）。
  对外只用房间号，不再把群号暴露在 URL 里。
- 访问：`http://127.0.0.1:59400/r/{房间号}?t=<token>`
- 身份：HMAC 签名 token（`房间号_uid_签名`），点开链接即身份，无感登录：
  - `uid == 0` → **观战**（只读：能看 state、不能走子/认输/停一手）
  - 其他 → 需要是该局参与者（先手/后手），否则 403
- 静态资源：`/static/{name}` 白名单 + 缓存头（像素字体别内联进页面）。
- 隧道：Cloudflare Tunnel 把 `game.truslerweb.dpdns.org/*` 指到 59400。
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
_ROOMS_FILE = _ROOT / "data" / "game_rooms.json"
_TEMPLATES = _ROOT / "data" / "templates"
_ASSETS = _ROOT / "data" / "web_assets"

SPECTATOR = 0                      # uid == 0 表示观战
KINDS = ("go", "wzq", "xq")
KIND_LABEL = {"go": "围棋", "wzq": "五子棋", "xq": "中国象棋"}

app = FastAPI(title="幻梦棋局", docs_url=None, redoc_url=None)

# ── 子路径挂载（https://bot.xxx/game/... 也指向本服务） ──────
# cloudflared 按路径分流时不重写 URL，/game/... 会原样打到本服务；
# 中间件把 PATH 剥掉前缀后交给原路由处理，原 game 子域不受影响。
_MOUNT_PREFIX = os.environ.get("GAME_WEB_MOUNT", "").rstrip("/")   # 如 "/game"


@app.middleware("http")
async def _strip_mount_prefix(request: Request, call_next):
    if _MOUNT_PREFIX and request.url.path.startswith(_MOUNT_PREFIX + "/"):
        request.state.mount_prefix = _MOUNT_PREFIX   # 页面生成子资源 URL 时要拼回去
        request.scope["path"] = request.url.path[len(_MOUNT_PREFIX):]
    elif _MOUNT_PREFIX and request.url.path == _MOUNT_PREFIX:
        request.state.mount_prefix = _MOUNT_PREFIX
        request.scope["path"] = "/"          # /game → 入口页
    return await call_next(request)

# 静态资源白名单：name -> (media_type, cache-control)
_STATIC = {
    "monocraft.ttf": ("font/ttf", "public, max-age=604800"),
    "game.css": ("text/css; charset=utf-8", "public, max-age=3600"),
}

# ── 房间号 ───────────────────────────────────────────────────
# 去掉易混字符 0/O/1/I/L，只留 4 位 → 读起来不会认错
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 4
_rooms: dict[str, dict] = {}


def _load_rooms() -> None:
    global _rooms
    if not _ROOMS_FILE.exists():
        return
    try:
        raw = json.loads(_ROOMS_FILE.read_text(encoding="utf-8"))
        _rooms = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    except Exception as e:
        logger.warning("房间表读取失败: %s", e)


def _save_rooms() -> None:
    try:
        _ROOMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ROOMS_FILE.write_text(json.dumps(_rooms, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except Exception as e:
        logger.warning("房间表保存失败: %s", e)


def _new_code() -> str:
    for _ in range(200):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
        if code not in _rooms:
            return code
    raise RuntimeError("房间号用尽")


def ensure_room(kind: str, chat_id: int) -> str:
    """取（或新建）该群这个棋种的房间号。

    同一群的同一种棋**始终复用同一个房间号** —— 这样链接稳定、老链接也不会失效。
    """
    _load_rooms()               # ★ 每次读盘：跨进程复用已有房间，避免重复建
    chat_id = int(chat_id)
    for code, r in _rooms.items():
        if r.get("kind") == kind and int(r.get("chat") or 0) == chat_id:
            return code
    code = _new_code()
    _rooms[code] = {"kind": kind, "chat": chat_id, "created": int(time.time())}
    _save_rooms()
    logger.info("新建棋局房间 %s（%s chat=%s）", code, kind, chat_id)
    return code


def room_of(code: str) -> Optional[dict]:
    _load_rooms()               # ★ 每次读盘：双进程下也能看到新房间
    return _rooms.get(str(code or "").upper())


def _chat_of(room: dict) -> int:
    return int(room.get("chat") or 0)


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


def make_token(code: str, uid: int) -> str:
    """生成身份 token（链接里带上，点开即身份）。uid=0 是观战"""
    code = str(code).upper()
    sig = hmac.new(_secret(), f"{code}:{int(uid)}".encode(),
                   hashlib.sha256).hexdigest()[:16]
    return f"{code}_{int(uid)}_{sig}"


def parse_token(token: str) -> Optional[tuple[str, int]]:
    """校验 token，返回 (房间号, uid)；非法返回 None"""
    try:
        code, uid_s, sig = str(token).split("_", 2)
        uid = int(uid_s)
    except Exception:
        return None
    code = code.upper()
    expect = hmac.new(_secret(), f"{code}:{uid}".encode(),
                      hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, expect):
        return None
    return code, uid


def web_base() -> str:
    """对外访问基址（隧道配好后在 .env / systemd 里设 GAME_WEB_BASE）"""
    return os.environ.get("GAME_WEB_BASE", f"http://127.0.0.1:{PORT}").rstrip("/")


def play_link(code: str, uid: int) -> str:
    """对局链接（发给对局双方，各自一条）"""
    return f"{web_base()}/r/{str(code).upper()}?t={make_token(code, uid)}"


def spectate_link(code: str) -> str:
    """观战链接（发到群里，任何拿到的人都能只读围观）"""
    return play_link(code, SPECTATOR)


def room_link(kind: str, chat_id: int, uid: int) -> str:
    """便捷入口：按 (棋种, 群) 取房间号并生成链接"""
    return play_link(ensure_room(kind, chat_id), uid)


# ── 棋局读取 ─────────────────────────────────────────────────

def _load_game(kind: str, chat_id: int):
    if kind == "go":
        from modules import go_game as G
        try:
            G.web_reload()          # 覆盖读：跨进程也能看到最新落子
        except Exception as e:
            logger.warning("覆盖读围棋棋局失败: %s", e)
        return G.get_game(chat_id)
    if kind == "wzq":
        from modules import wzq as W
        try:
            W.web_reload()          # 覆盖读：跨进程也能看到最新落子
        except Exception as e:
            logger.warning("覆盖读五子棋对局失败: %s", e)
        return W.get_game(chat_id)
    if kind == "xq":
        from modules import chinese_chess as X
        try:
            X.web_reload()          # 覆盖读：跨进程也能看到最新落子
        except Exception as e:
            logger.warning("覆盖读象棋棋局失败: %s", e)
        return X.get_game(chat_id)
    return None


def _role_of(kind: str, game, uid: int) -> str:
    """该 uid 在这局里的角色名；不是参与者返回空串。

    围棋/五子棋：black / white；象棋：red / black
    """
    if uid == SPECTATOR:
        return "spectator"
    if game is None or not uid:
        return ""
    if kind == "go":
        if uid == game.get("player_id"):
            return "black"
        if uid == game.get("opponent_id"):
            return "white"
    elif kind == "wzq":
        if uid == getattr(game, "black", None):
            return "black"
        if uid == getattr(game, "white", None):
            return "white"
    elif kind == "xq":
        if uid == game.get("player_id"):
            return "red"
        if uid == game.get("opponent_id"):
            return "black"
    return ""


def _auth(code: str, t: str):
    """返回 (room, uid, game, role, err)。

    err 非空 = 拒绝；uid == SPECTATOR 时 role 固定为 spectator（只读）。
    """
    parsed = parse_token(t)
    if not parsed or parsed[0] != str(code or "").upper():
        return None, None, None, "", "链接无效或已过期"
    room = room_of(code)
    if not room:
        return None, None, None, "", "房间不存在"
    kind = room.get("kind")
    if kind not in KINDS:
        return None, None, None, "", "房间类型异常"
    uid = parsed[1]
    game = _load_game(kind, _chat_of(room))
    if uid == SPECTATOR:
        return room, uid, game, "spectator", ""
    role = _role_of(kind, game, uid)
    if not role:
        return None, None, None, "", "你不在这个房间里"
    return room, uid, game, role, ""


# ── 响应小工具 ───────────────────────────────────────────────

def _deny(err: str, as_html: bool = False):
    if as_html:
        return HTMLResponse(
            f"<h2 style='font-family:sans-serif;padding:40px'>{err}喵~</h2>", status_code=403)
    return JSONResponse({"ok": False, "error": err}, status_code=403)


def _no_game(as_html: bool = False):
    if as_html:
        return HTMLResponse(
            "<h2 style='font-family:sans-serif;padding:40px'>这个房间还没有对局喵~</h2>",
            status_code=404)
    return JSONResponse({"ok": False, "msg": "这个房间还没有对局喵~"})


def _read_only(role: str):
    if role == "spectator":
        return JSONResponse({"ok": False, "msg": "观战模式只能看喵~"})
    return None


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


@app.get("/", response_class=HTMLResponse)
async def index():
    """入口页：告诉用户去哪儿找链接（房间号本身不构成访问凭证）"""
    return HTMLResponse(
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<div style=\"font-family:'PingFang SC','Microsoft YaHei',sans-serif;"
        "max-width:520px;margin:12vh auto;padding:0 22px;color:#1c2333;line-height:1.9\">"
        "<h2 style='margin:0 0 12px'>幻梦棋局</h2>"
        "<p style='color:#4a5568;margin:0 0 8px'>对局与观战都需要链接里的身份令牌：</p>"
        "<p style='color:#4a5568;margin:0 0 8px'>· 对局双方会收到<b>私聊</b>里的专属链接</p>"
        "<p style='color:#4a5568;margin:0 0 8px'>· 群里会发<b>观战</b>链接，点开即围观</p>"
        "<p style='color:#8794ab;margin:18px 0 0;font-size:13px'>"
        "在群里发送 <code>/~观战 房间号</code> 也可以重新取到观战链接。</p></div>")


# ── 对局页面 ─────────────────────────────────────────────────

@app.get("/r/{code}", response_class=HTMLResponse)
async def room_page(code: str, request: Request, t: str = ""):
    room, uid, game, role, err = _auth(code, t)
    if err:
        return _deny(err, as_html=True)
    if not game:
        return _no_game(as_html=True)
    # 静态资源/API 用「对外实际前缀」：挂载前缀（/game）优先，否则按本页路径推导
    # （裸子域 /r/ABCD → 前缀空串；bot 子域 /game/r/ABCD → 前缀 /game）
    prefix = getattr(request.state, "mount_prefix", "") \
        or request.url.path.rsplit("/", 2)[0]
    kind = room.get("kind")
    tmpl = (_TEMPLATES / f"{kind}_web.html").read_text(encoding="utf-8")
    return HTMLResponse(tmpl
                        .replace("${ROOM}", str(code).upper())
                        .replace("${KIND_CN}", KIND_LABEL.get(kind, ""))
                        .replace("${CSS_URL}", f"{prefix}/static/game.css")
                        .replace("${API_BASE}", prefix)
                        .replace("${TOKEN}", t))


@app.get("/api/r/{code}/state")
async def room_state(code: str, t: str = ""):
    room, uid, game, role, err = _auth(code, t)
    if err:
        return _deny(err)
    if not game:
        return JSONResponse({"ok": True, "gone": True, "role": role,
                             "room": str(code).upper(), "kind": room.get("kind")})
    kind = room.get("kind")
    chat = _chat_of(room)
    if kind == "go":
        from modules import go_game as G
        st = G.web_state(chat, uid)
    elif kind == "wzq":
        from modules import wzq as W
        st = W.web_state(chat, uid)
    else:
        from modules import chinese_chess as X
        st = X.web_state(chat, uid)
    if st is None:
        return JSONResponse({"ok": True, "gone": True, "role": role,
                             "room": str(code).upper(), "kind": kind})
    st.update({"ok": True, "room": str(code).upper(), "kind": kind,
               "role": role, "spectator": role == "spectator"})
    return JSONResponse(st)


@app.post("/api/r/{code}/move")
async def room_move(code: str, request: Request, t: str = ""):
    room, uid, game, role, err = _auth(code, t)
    if err:
        return _deny(err)
    if not game:
        return _no_game()
    ro = _read_only(role)
    if ro:
        return ro
    kind = room.get("kind")
    chat = _chat_of(room)
    try:
        body = await request.json()
    except Exception:
        body = {}

    if kind == "go":
        from modules import go_game as G
        ok, msg, _ = G.make_move(uid, chat, str(body.get("coord", "")))
        return JSONResponse({"ok": bool(ok), "msg": msg})

    if kind == "wzq":
        from modules import wzq as W
        try:
            r, c = int(body.get("r")), int(body.get("c"))
        except Exception:
            return JSONResponse({"ok": False, "msg": "坐标不对喵~"})
        ok, msg = W.web_move(chat, uid, r, c)
        if not ok:
            return JSONResponse({"ok": False, "msg": msg})
        # 人机模式：AI 立刻应一手（线程池执行，不阻塞事件循环）
        g2 = W.get_game(chat)
        if g2 and g2.status == "playing" and g2.white == 0 and g2.turn == 2:
            try:
                ai_ok, ai_msg = await W.ai_move_async(chat)
                if ai_ok:
                    g3 = W.get_game(chat)
                    lm = g3.last_move if g3 else None
                    if lm:
                        msg += f"；{W._bot_name()} 落子 {W.coord_label(lm[0], lm[1])}"
                    if ai_msg == "win":
                        msg += f"，五连！{W._bot_name()} 获胜"
            except Exception as e:
                logger.warning("五子棋 AI 应手失败: %s", e)
        return JSONResponse({"ok": True, "msg": msg})

    # 象棋
    from modules import chinese_chess as X
    notation = str(body.get("uci") or body.get("notation") or "").strip()
    if not notation:
        return JSONResponse({"ok": False, "msg": "没收到走法喵~"})
    # 象棋 AI 是同步阻塞实现（时间预算最长 3s），放线程池，别卡住 bot 事件循环
    loop = asyncio.get_running_loop()
    ok, msg = await loop.run_in_executor(None, X.web_move, uid, chat, notation)
    return JSONResponse({"ok": bool(ok), "msg": msg})


@app.post("/api/r/{code}/pass")
async def room_pass(code: str, t: str = ""):
    room, uid, game, role, err = _auth(code, t)
    if err:
        return _deny(err)
    if not game:
        return _no_game()
    ro = _read_only(role)
    if ro:
        return ro
    if room.get("kind") != "go":
        return JSONResponse({"ok": False, "msg": "这种棋不能停一手喵~"})
    from modules import go_game as G
    chat = _chat_of(room)
    ok, msg, _ = G.do_pass(uid, chat)
    if not ok:
        return JSONResponse({"ok": False, "msg": msg})
    g = G.get_game(chat)
    if g and g.get("status") == "finished":
        msg += "\n" + G.end_game(chat)
    return JSONResponse({"ok": True, "msg": msg})


@app.post("/api/r/{code}/resign")
async def room_resign(code: str, t: str = ""):
    room, uid, game, role, err = _auth(code, t)
    if err:
        return _deny(err)
    if not game:
        return _no_game()
    ro = _read_only(role)
    if ro:
        return ro
    kind = room.get("kind")
    chat = _chat_of(room)

    if kind == "go":
        from modules import go_game as G
        g = G.get_game(chat)
        if not g or g.get("status") != "playing":
            return JSONResponse({"ok": False, "msg": "这局已经结束了喵~"})
        return JSONResponse({"ok": True, "msg": G.resign_game(uid, chat)})

    if kind == "wzq":
        from modules import wzq as W
        g = W.get_game(chat)
        if not g or g.status != "playing":
            return JSONResponse({"ok": False, "msg": "当前没有进行中的对局喵~"})
        return JSONResponse({"ok": True, "msg": W.web_resign(chat, uid)[1]})

    from modules import chinese_chess as X
    g = X.get_game(chat)
    if not g or g.get("finished"):
        return JSONResponse({"ok": False, "msg": "这局已经结束了喵~"})
    ok, msg = X.web_resign(uid, chat)
    return JSONResponse({"ok": bool(ok), "msg": msg})


# ── 启动 ─────────────────────────────────────────────────────

async def start_server() -> Optional[asyncio.Task]:
    """在 bot 的事件循环里起 web 服务（失败不影响 bot 主流程）"""
    try:
        import uvicorn
        _load_rooms()
        config = uvicorn.Config(app, host="127.0.0.1", port=PORT,
                                log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        logger.info("棋局 Web 服务已启动: http://127.0.0.1:%d（%d 个房间，对外走隧道）",
                    PORT, len(_rooms))
        return task
    except Exception as e:
        logger.warning("棋局 Web 服务启动失败（不影响 bot）: %s", e)
        return None
