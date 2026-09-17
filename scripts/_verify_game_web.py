"""服务器端综合验证：房间号 + 三棋 + 人人对战 + 观战 + 三页截图量测

跑法：python3 _probe_verify.py（服务器 /root/bot 下，服务已在 59400 监听）
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path("/root/bot")
sys.path.insert(0, str(ROOT))

import httpx

from modules import go_game as G
from modules import wzq as W
from modules import chinese_chess as X
from services.game_web import ensure_room, make_token, spectate_link

PORT = 59400
BASE = f"http://127.0.0.1:{PORT}"
OUT = ROOT / "data" / "img_temp"
OUT.mkdir(parents=True, exist_ok=True)

# chat（房间）
GO_ROOM, WZQ_ROOM, XQ_ROOM, XQ2_ROOM = 991001, 991002, 991003, 991004
# 玩家
GO_B, GO_W = 4001, 4005
WZQ_B, WZQ_W = 4002, 4006
XQ_R, XQ_K = 4003, 4007
XQ2_R = 4004

fail = []


def chk(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fail.append(name)


def seed():
    # 围棋：人机一局（截图用）+ 双人一局
    G._games.pop(GO_ROOM, None)
    G.start_game(GO_B, GO_ROOM, "hard", 19)
    for mv in ("D4", "Q16", "Q4", "D16", "R14", "F3"):
        G.make_move(GO_B, GO_ROOM, mv)

    # 五子棋：双人一局
    W._games.pop(WZQ_ROOM, None)
    W.create_duel(WZQ_ROOM, WZQ_B, WZQ_W)
    W.accept_duel(WZQ_ROOM, WZQ_W)
    for uid_, r, c in ((WZQ_B, 7, 7), (WZQ_W, 7, 8), (WZQ_B, 8, 7),
                       (WZQ_W, 8, 8), (WZQ_B, 6, 6), (WZQ_W, 6, 8)):
        W.make_move(WZQ_ROOM, uid_, r, c)

    # 象棋：人机一局（截图用，红方）+ 双人一局
    games = X._load_games()
    for room in (XQ_ROOM, XQ2_ROOM):
        games.pop(str(room), None)
    X._save_games(games)
    X.start_game(XQ_R, XQ_ROOM, "困难")
    # 人机模式：红方走一手，AI 自动应手（只走一步，别连走两步）
    X.make_move(XQ_R, XQ_ROOM, "h2e2", render=False)


DIAG = """() => {
  const q = s => document.querySelector(s);
  const R = s => { const e = q(s); if (!e) return null; const r = e.getBoundingClientRect();
    return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  return {
    hits: document.querySelectorAll('.hit').length,
    stones: document.querySelectorAll('.hit .stone').length,
    pieces: document.querySelectorAll('.hit .pce').length,
    lines: document.querySelectorAll('#grid line').length,
    circles: document.querySelectorAll('#grid circle').length,
    rects: document.querySelectorAll('#grid rect').length,
    moves: document.querySelectorAll('#moves .mv').length,
    fontMono: (() => { try { return document.fonts.check('12px Monocraft'); } catch (e) { return null; } })(),
    cssLoaded: !!getComputedStyle(document.documentElement).getPropertyValue('--ink').trim(),
    brokenImgs: [...document.images].filter(i => !i.complete || i.naturalWidth === 0).length,
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
    rect: { area: R('#barea'), status: R('#status'), btns: R('.btns'), card: R('.col .card') },
    status: (q('#status') ? q('#status').textContent.trim() : ''),
    chips: [...document.querySelectorAll('.chip')].map(e => e.textContent.trim()),
    role: (q('#chip-role') ? q('#chip-role').textContent.trim() : ''),
    btnsHidden: (q('#btns') ? getComputedStyle(q('#btns')).display === 'none' : null),
  };
}"""


async def shoot():
    from playwright.async_api import async_playwright
    cases = [
        ("go", GO_ROOM, GO_B, "go_web"),
        ("wzq", WZQ_ROOM, WZQ_B, "wzq_web"),
        ("xq", XQ_ROOM, XQ_R, "xq_web"),
    ]
    errors = []
    async with async_playwright() as p:
        br = await p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        for kind, room, uid, tag in cases:
            code = ensure_room(kind, room)
            url = f"{BASE}/r/{code}?t={make_token(code, uid)}"
            pg = await br.new_page(viewport={"width": 1440, "height": 1200}, device_scale_factor=2)
            pg.on("pageerror", lambda e: errors.append(f"{tag}: {e}"))
            pg.on("console", lambda m: errors.append(f"{tag}: {m.text}") if m.type == "error" else None)
            # ⚠️ 不能用 networkidle：棋局页从 v2.3.39 起用长轮询（/state?wait=25），
            #    会一直挂着一条请求 → 网络永不 idle → 必然超时。
            r = await pg.goto(url, wait_until="domcontentloaded", timeout=30000)
            await pg.wait_for_timeout(2200)
            d = await pg.evaluate(DIAG)
            print(f"DIAG_{tag.upper()}", json.dumps({"http": r.status, **d}, ensure_ascii=False))
            await pg.screenshot(path=str(OUT / f"{tag}_desktop.png"), full_page=True)
            await pg.set_viewport_size({"width": 430, "height": 1400})
            await pg.wait_for_timeout(1500)
            d2 = await pg.evaluate(DIAG)
            print(f"MOBILE_{tag.upper()}", json.dumps(
                {k: d2[k] for k in ("hits", "stones", "pieces", "scrollW", "clientW", "rect")},
                ensure_ascii=False))
            await pg.screenshot(path=str(OUT / f"{tag}_mobile.png"), full_page=True)
            await pg.close()
        # 观战页面：三页都用观战 token 打开，检查按钮隐藏
        for kind, room, tag in (("go", GO_ROOM, "go_web"), ("wzq", WZQ_ROOM, "wzq_web"),
                                ("xq", XQ_ROOM, "xq_web")):
            code = ensure_room(kind, room)
            url = f"{BASE}/r/{code}?t={make_token(code, 0)}"
            pg = await br.new_page(viewport={"width": 1280, "height": 900})
            # ⚠️ 不能用 networkidle：棋局页从 v2.3.39 起用长轮询（/state?wait=25），
            #    会一直挂着一条请求 → 网络永不 idle → 必然超时。
            r = await pg.goto(url, wait_until="domcontentloaded", timeout=30000)
            await pg.wait_for_timeout(1500)
            d = await pg.evaluate(DIAG)
            print(f"SPEC_{tag.upper()}", json.dumps(
                {"http": r.status, "role": d["role"], "btnsHidden": d["btnsHidden"],
                 "status": d["status"][:40]}, ensure_ascii=False))
            await pg.close()
        await br.close()
    print("JS_ERRORS:", json.dumps(errors, ensure_ascii=False))


def http_checks():
    c = httpx.Client(timeout=25, trust_env=False)
    H = lambda uid, code: {"t": make_token(code, uid)}  # noqa: E731

    def post(path, params, payload):
        r = c.post(f"{BASE}{path}", params=params, json=payload)
        try:
            return r.json()
        except Exception:
            return {"ok": False, "msg": f"HTTP {r.status_code} :: {r.text[:200]}"}

    ph = ("$" + "{ROOM}", "$" + "{TOKEN}", "$" + "{CSS_URL}")

    # ── 房间号体系 ──
    cgo = ensure_room("go", GO_ROOM)
    cwz = ensure_room("wzq", WZQ_ROOM)
    cxq = ensure_room("xq", XQ_ROOM)
    chk("房间号 4 位", len(cgo) == len(cwz) == len(cxq) == 4, f"{cgo},{cwz},{cxq}")
    chk("不同棋种房间号不同", len({cgo, cwz, cxq}) == 3)
    chk("观战链接 uid=0", spectate_link(cgo).endswith(f"/r/{cgo}?t={make_token(cgo, 0)}"))

    # ── 象棋双人 ──
    X.start_game(XQ2_R, XQ2_ROOM, "easy", opponent_id=XQ_K)
    x2r = H(XQ2_R, ensure_room("xq", XQ2_ROOM))
    x2k = H(XQ_K, ensure_room("xq", XQ2_ROOM))
    cx2 = ensure_room("xq", XQ2_ROOM)
    st = c.get(f"{BASE}/api/r/{cx2}/state", params=x2r).json()
    chk("象棋双人 state ok", st.get("ok") is True)
    chk("象棋双人 pvp=True", st.get("pvp") is True)
    chk("象棋双人 role=red", st.get("role") == "red", str(st.get("role")))
    chk("象棋双人 turn=red（红先）", st.get("turn") == "red", str(st.get("turn")))
    res = post(f"/api/r/{cx2}/move", x2k, {"uci": "b9c7"})
    chk("黑方抢走被拒（不该轮到）", not res.get("ok"), json.dumps(res, ensure_ascii=False)[:100])
    res = post(f"/api/r/{cx2}/move", x2r, {"uci": "h2e2"})
    chk("红方走炮二平五 ok", res.get("ok"), json.dumps(res, ensure_ascii=False)[:100])
    st2 = c.get(f"{BASE}/api/r/{cx2}/state", params=x2k).json()
    chk("轮到黑方", st2.get("turn") == "black", str(st2.get("turn")))
    res = post(f"/api/r/{cx2}/move", x2k, {"uci": "b9c7"})
    chk("黑方应子 ok", res.get("ok"), json.dumps(res, ensure_ascii=False)[:100])
    st3 = c.get(f"{BASE}/api/r/{cx2}/state", params=x2k).json()
    chk("象棋双人 AI 不介入（回合=2）", st3.get("move_count") == 2, str(st3.get("move_count")))
    res = post(f"/api/r/{cx2}/resign", x2k, None)
    chk("黑方认输 ok", res.get("ok"), json.dumps(res, ensure_ascii=False)[:100])
    stf = c.get(f"{BASE}/api/r/{cx2}/state", params=x2r).json()
    chk("象棋双人终局 finished", stf.get("finished") is True)
    chk("象棋双人红方获胜", "红" in stf.get("result", "") or str(XQ2_R) in stf.get("result", ""),
        str(stf.get("result")))

    # ── 围棋双人 ──
    G._games.pop(GO_ROOM, None)
    G.start_game(GO_B, GO_ROOM, "normal", 9, opponent_id=GO_W)
    cg = ensure_room("go", GO_ROOM)
    gh = H(GO_B, cg)
    st = c.get(f"{BASE}/api/r/{cg}/state", params=gh).json()
    chk("围棋双人 pvp=True", st.get("pvp") is True)
    chk("围棋双人 role=black", st.get("role") == "black", str(st.get("role")))
    chk("围棋双人 turn=black（黑先）", st.get("turn") == 1, str(st.get("turn")))
    res = post(f"/api/r/{cg}/move", H(GO_W, cg), {"coord": "E5"})
    chk("白方抢下被拒", not res.get("ok"), json.dumps(res, ensure_ascii=False)[:100])
    res = post(f"/api/r/{cg}/move", gh, {"coord": "E5"})
    chk("黑方落子 ok", res.get("ok"), json.dumps(res, ensure_ascii=False)[:100])
    res = post(f"/api/r/{cg}/move", H(GO_W, cg), {"coord": "F6"})
    chk("白方应子 ok", res.get("ok"), json.dumps(res, ensure_ascii=False)[:100])
    chk("围棋双人 AI 不介入（手数=2）",
        c.get(f"{BASE}/api/r/{cg}/state", params=gh).json()["move_count"] == 2)

    # ── 五子棋双人 ──
    cw = ensure_room("wzq", WZQ_ROOM)
    st = c.get(f"{BASE}/api/r/{cw}/state", params=H(WZQ_B, cw)).json()
    chk("五子棋双人 pvp=True", st.get("pvp") is True)
    chk("五子棋双人 role=black", st.get("role") == "black", str(st.get("role")))
    chk("五子棋盘上有 6 子", st["black"]["stones"] + st["white"]["stones"] == 6,
        str(st["black"]["stones"] + st["white"]["stones"]))
    chk("五子棋轮到黑方（偶数手）", st.get("turn") == 1, str(st.get("turn")))

    # ── 观战只读 ──
    for code, kind in ((cg, "go"), (cw, "wzq"), (cxq, "xq")):
        sh = H(0, code)
        sts = c.get(f"{BASE}/api/r/{code}/state", params=sh).json()
        chk(f"{kind} 观战 role=spectator", sts.get("role") == "spectator", str(sts.get("role")))
        chk(f"{kind} 观战 spectator=True", sts.get("spectator") is True)
        res = post(f"/api/r/{code}/move", sh, {"coord": "D4", "uci": "a0a9", "r": 0, "c": 0})
        chk(f"{kind} 观战落子被拒", not res.get("ok"), json.dumps(res, ensure_ascii=False)[:80])
        res = post(f"/api/r/{code}/resign", sh, None)
        chk(f"{kind} 观战认输被拒", not res.get("ok"), json.dumps(res, ensure_ascii=False)[:80])
    # 越权
    chk("非参与者 403",
        c.get(f"{BASE}/api/r/{cg}/state", params=H(9999, cg)).status_code == 403)
    chk("无 token 403", c.get(f"{BASE}/r/{cg}").status_code == 403)
    chk("未知房间 403", c.get(f"{BASE}/r/ZZZZ", params={"t": "x"}).status_code == 403)
    chk("房间号与 token 不匹配 403",
        c.get(f"{BASE}/r/{cg}", params=H(4001, cw)).status_code == 403)

    # ── 页面 ──
    for kind, room, uid, tag in (("go", GO_ROOM, GO_B, "go_web"),
                                 ("wzq", WZQ_ROOM, WZQ_B, "wzq_web"),
                                 ("xq", XQ_ROOM, XQ_R, "xq_web")):
        code = ensure_room(kind, room)
        rr = c.get(f"{BASE}/r/{code}", params=H(uid, code))
        chk(f"{kind} 页面 200", rr.status_code == 200, str(rr.status_code))
        chk(f"{kind} 占位符全替换",
            not [k for k in ph if k in rr.text], str([k for k in ph if k in rr.text]))
        chk(f"{kind} 引用共享样式", "/static/game.css" in rr.text)
        chk(f"{kind} 页面含房间号", code in rr.text)

    r = c.get(f"{BASE}/static/game.css")
    chk("服务器 game.css 200", r.status_code == 200 and ".barea" in r.text, str(r.status_code))
    r = c.get(f"{BASE}/static/monocraft.ttf")
    chk("服务器字体 200", r.status_code == 200 and len(r.content) > 150_000, str(r.status_code))


seed()
http_checks()
asyncio.run(shoot())

print("\n" + ("全部通过" if not fail else f"失败 {len(fail)} 项: {fail}"))
sys.exit(1 if fail else 0)
