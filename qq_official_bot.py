#!/usr/bin/env python3
"""QQ 官方机器人入口（只接指令，不接聊天、不推送）。

## 定位

2026-10-31 官方机器人审核通过。第三方协议端（NapCat）有封号风险且刚被封 7 天，
官方这条路是合规的，但能力有限：只能收 @ 它的消息、主动消息有配额。

所以这里只做一件事：**把官方渠道的消息翻译成内部指令调用，复用主 bot 的
COMMAND_MAP 与 26 个插件**，不重复实现业务。

    /~wdsj bw trusler     →  modules.commands.handle_command(...)
    /~ba 碧蓝档案          →  同上
    /~help                →  同上

非指令消息（不含 /~ 或裸 /）**直接忽略**，不回任何东西——LLM 功能此前已公告关闭。

## 怎么复用而不串台

这是个独立进程，所以在进程内把 `services.sender` 的对外函数打桩成「发到官方 API」：
命令 handler 照旧调 `send_group_msg(cq, chat_id)`，实际走的是官方被动回复。
主 bot 进程完全不受影响（各进程各的模块实例）。

## 环境变量

    QQ_APPID    应用 AppID
    QQ_SECRET   应用 Secret（**只从环境变量读，不写死**）

跑法：`python3 qq_official_bot.py`（systemd: qq-official-listen.service）
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

APPID = os.environ.get("QQ_APPID", "").strip()
SECRET = os.environ.get("QQ_SECRET", "").strip()
GATEWAY = "wss://api.sgroup.qq.com/websocket"
API_BASE = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

# 每条收到的消息最多被动回复 5 次（官方限制），第 6 次起要主动消息配额
MAX_PASSIVE_REPLIES = 5

# ── AGC（应用生成内容）限制 ──────────────────────────────
# 官方平台对机器人产出的内容有审核要求：AI 生图、二次元图（含 R18）这类
# 在官方渠道一律不提供。第三方协议端（NapCat）没有这条平台规则，不受影响。
# 2026-10-31 用户要求：这些指令在官方机器人上禁用。
BANNED_COMMANDS = {
    "img", "img18", "pic", "图片", "图", "色图",
    "draw", "绘画", "生图", "画图",
    "video", "视频", "img2video", "图生视频",
}
BANNED_REPLY = "官方渠道不做图片生成和二次元图啦，换个指令试试喵。"

# 图片发完后补的那句（带按钮）。要短，不要抢图片的戏。
KEYBOARD_HINT = "顺手看看你自己的？"

_CQ_IMAGE = re.compile(r"\[CQ:image,[^\]]*?file=([^,\]]+)[^\]]*\]")
_CQ_ANY = re.compile(r"\[CQ:[^\]]+\]")

log_prefix = time.strftime("%m-%d")
_TOKEN = {"val": "", "exp": 0.0}


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


# ────────────────────────── 令牌 ──────────────────────────
def get_token(force: bool = False) -> str:
    """取 access_token（带本地缓存，提前 60s 过期）。"""
    now = time.time()
    if not force and _TOKEN["val"] and now < _TOKEN["exp"] - 60:
        return _TOKEN["val"]
    body = json.dumps({"appId": APPID, "clientSecret": SECRET}).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    _TOKEN["val"] = data["access_token"]
    _TOKEN["exp"] = now + int(data.get("expires_in", 7200))
    return _TOKEN["val"]


def _api(method: str, path: str, body: dict | None = None, timeout: float = 20.0):
    """调官方 HTTP API，返回 (status, json)。失败抛异常由调用方处理。"""
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(
        API_BASE + path,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Authorization": "QQBot " + get_token(),
            "X-Union-Appid": APPID,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        return e.code, {"error": detail}


# ────────────────────────── 回复上下文 ──────────────────────────
class ReplyCtx:
    """一条入站消息的回复上下文（当前处理中的那一条）。"""

    def __init__(self, scene: str, openid: str, msg_id: str, sender: str):
        self.scene = scene          # "groups" / "users"
        self.openid = openid        # group_openid 或 user_openid
        self.msg_id = msg_id
        self.sender = sender
        self.seq = 0
        self.exhausted = False
        self.keyboard = None        # 下一条文本消息要带的按钮（用一次就清）
        self.keyboard_late = None   # 等图片发完之后再补一条带按钮的短文本


_CUR: ReplyCtx | None = None


def _next_seq() -> int:
    global _CUR
    if _CUR is None:
        return 0
    _CUR.seq += 1
    if _CUR.seq > MAX_PASSIVE_REPLIES:
        _CUR.exhausted = True
    return _CUR.seq


def _post_message(payload: dict) -> tuple[int, dict]:
    assert _CUR is not None
    return _api("POST", f"/v2/{_CUR.scene}/{_CUR.openid}/messages", payload)


# ── markdown 能力探测 ───────────────────────────────────
# 官方 API 的 msg_type=2 走 markdown，但要「markdown 消息」权限；群聊和私聊
# 是两套权限，所以按 scene 分别记状态。
#   unknown → 第一次尝试，成功记 on、失败记 off 并原地回退纯文本
#   on      → 后续回复一律富文本（**加粗**、列表、代码块都能渲染）
#   off     → 不再尝试，省一次失败请求
_MD_STATE: dict[str, str] = {}
# 按钮（keyboard）能力：官方要单独开「消息按钮」权限，没开时带 keyboard 会整条失败。
# 失败一次就永久关掉，避免每条回复都浪费一次请求。
_KB_OK = True


def _msg_body(chunk: str, use_md: bool, with_kb: bool) -> dict:
    seq = _next_seq()
    body: dict = {"msg_id": _CUR.msg_id, "msg_seq": seq}
    if use_md:
        body.update({"content": "", "msg_type": 2,
                     "markdown": {"content": chunk}})
    else:
        body.update({"content": chunk, "msg_type": 0})
    if with_kb and _CUR.keyboard:
        body["keyboard"] = _CUR.keyboard
    return body


def _send_chunk(chunk: str) -> tuple[int, dict]:
    """发一段文本：优先 markdown+按钮，逐级降级到纯文本。"""
    global _KB_OK
    assert _CUR is not None
    scene = _CUR.scene
    state = _MD_STATE.get(scene, "unknown")
    use_md = state != "off"
    use_kb = bool(_CUR.keyboard) and _KB_OK

    st, data = _post_message(_msg_body(chunk, use_md, use_kb))
    if st in (200, 201):
        if use_md and state == "unknown":
            _MD_STATE[scene] = "on"
            log(f"markdown 可用（{scene}），后续回复切富文本")
        _CUR.keyboard = None
        return st, data

    # ① 带按钮失败 → 多半是没开「消息按钮」权限，去掉重试并永久关闭
    if use_kb:
        _KB_OK = False
        log(f"按钮发送失败（{scene} HTTP {st}），后续不再带按钮:", str(data)[:140])
        st, data = _post_message(_msg_body(chunk, use_md, False))
        if st in (200, 201):
            _CUR.keyboard = None
            return st, data

    # ② markdown 失败 → 回退纯文本
    if use_md:
        _MD_STATE[scene] = "off"
        log(f"markdown 不可用（{scene} HTTP {st}），回退纯文本:", str(data)[:140])
        st, data = _post_message(_msg_body(chunk, False, False))

    _CUR.keyboard = None
    return st, data


def md_enabled(scene: str | None = None) -> bool:
    """markdown 是否已确认可用（scene 为空则任一群/私聊可用即算）。"""
    if scene:
        return _MD_STATE.get(scene) == "on"
    return any(v == "on" for v in _MD_STATE.values())


def send_text(text: str) -> bool:
    """发文本（被动回复）。markdown 可用时自动走富文本。超长按 1800 字切块。"""
    if _CUR is None or not text:
        return False
    text = str(text).strip()
    if not text:
        return False
    ok = True
    while text:
        chunk, text = text[:1800], text[1800:]
        if _CUR.exhausted:
            log("被动回复次数用尽，丢弃文本:", chunk[:40])
            return False
        st, data = _send_chunk(chunk)
        if st not in (200, 201):
            log("文本回复失败 HTTP", st, str(data)[:200])
            ok = False
    return ok


def upload_image(path: Path) -> str:
    """上传图片拿 file_info（file_type=1 图片）。失败返回空串。"""
    if _CUR is None or not path.is_file():
        return ""
    if path.stat().st_size > 8 * 1024 * 1024:
        log("图片过大，跳过:", path.name)
        return ""
    body = {
        "file_type": 1,
        "file_data": base64.b64encode(path.read_bytes()).decode(),
        "srv_send_msg": False,
    }
    st, data = _api("POST", f"/v2/{_CUR.scene}/{_CUR.openid}/files", body, timeout=60)
    if st in (200, 201) and data.get("file_info"):
        return data["file_info"]
    log("图片上传失败 HTTP", st, str(data)[:200])
    return ""


def send_image(path: Path) -> bool:
    if _CUR is None:
        return False
    if _CUR.exhausted:
        log("被动回复次数用尽，丢弃图片:", path.name)
        return False
    info = upload_image(path)
    if not info:
        return False
    seq = _next_seq()
    body = {"content": "", "msg_type": 7, "media": {"file_info": info},
            "msg_id": _CUR.msg_id, "msg_seq": seq}
    st, data = _post_message(body)
    if st not in (200, 201):
        log("图片发送失败 HTTP", st, str(data)[:200])
        return False

    # 图片发完再把按钮补上：按钮只能挂文本消息，挂在「正在查询」那条会在图片上方，
    # 观感不对（2026-10-31 用户实测）。
    if _CUR.keyboard_late:
        _CUR.keyboard, _CUR.keyboard_late = _CUR.keyboard_late, None
        send_text(KEYBOARD_HINT)
    return True


def send_cq(cq_or_text: str) -> bool:
    """把 CQ 码字符串拆成「文本 + 图片」依次发出。"""
    if _CUR is None or not cq_or_text:
        return False
    raw = str(cq_or_text)
    images = _CQ_IMAGE.findall(raw)
    text = _CQ_ANY.sub("", raw).strip()
    ok = send_text(text) if text else True
    for f in images:
        p = Path(f.replace("file:///", "").replace("file://", ""))
        ok = send_image(p) and ok
    if not images and not text:
        return True
    return ok


# ────────────────────────── 给指令层打桩 ──────────────────────────
def install_sender_shim() -> None:
    """让命令 handler 的 send_* 走官方 API（仅本进程生效）。"""
    import services.sender as S

    async def _send_group(msg, chat_id, *a, **k):
        return send_cq(msg)

    async def _send_private(msg, chat_id, *a, **k):
        return send_cq(msg)

    async def _send_file(path, chat_id, *a, **k):
        p = Path(str(path))
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            return send_image(p)
        send_text(f"（文件：{p.name}）")
        return False

    S.send_group_msg = _send_group            # type: ignore[assignment]
    S.send_private_msg = _send_private        # type: ignore[assignment]
    S.send_by_chat_type = _send_group         # type: ignore[assignment]
    S.send_file = _send_file                  # type: ignore[assignment]

    # 插件里少数地方（ctx.message.send_ex / 直接 call_api）绕开上面四个，
    # 走 get_ws_manager() 拿原生 WS。这里给个假客户端，避免它们去连
    # 127.0.0.1:8099（本进程没有 NapCat，连了就是超时重试）。
    class _DummyWS:
        ws = None

        async def call_api(self, action, params=None, timeout=8.0):
            log("拦截原生 API 调用:", action)
            return {"status": "ok", "retcode": 0, "data": {}, "echo": "official"}

        async def send(self, *a, **k):
            return True

        async def ensure_connected(self):
            return False

    S.get_ws_manager = lambda: _DummyWS()     # type: ignore[assignment]
    log("sender 已接管：所有回复走官方 API")


# ────────────────────────── 指令分派 ──────────────────────────
def _stable_id(s: str) -> int:
    """openid 是字符串，指令层要 int。取稳定哈希，避免 0 让所有人共用一个身份。"""
    h = hashlib.sha1((s or "").encode()).hexdigest()[:9]
    return int(h, 16)


def _cmd_name(text: str) -> str:
    """从 `/~img 甘雨` 里取出 `img`。"""
    raw = (text or "").strip().lstrip("/")
    raw = raw.lstrip("~#").strip()
    return raw.split()[0].lower() if raw else ""


def _is_banned(text: str) -> bool:
    """AGC 限制的指令（生图 / 二次元图 / 视频生成）。"""
    return _cmd_name(text) in BANNED_COMMANDS


def _is_command(text: str) -> bool:
    t = (text or "").strip()
    if not t.startswith("/"):
        return False
    if t.startswith(("/~", "/#")):
        return True
    return True     # 裸 / 交给 handle_command 自己判断是否已注册


# 「查查我的」按钮：官方 API 的 action.type=2 是指令按钮，点了客户端替用户
# 发出 data 里的指令，等于一键自动填充。
_WDSJ_SKIP_ACTIONS = {
    "me", "trend", "趋势", "走势", "折线", "lb", "榜", "排行榜", "boards", "list",
    "collect", "bd", "绑定", "help", "列表", "?",
}


def build_wdsj_keyboard(cmd_text: str, user_id: int) -> dict | None:
    """给「查别人战绩」挂一个「查查我的」按钮。

    - 模式沿用原消息：`/~wdsj arena X` → 按钮发 `/~wdsj arena <绑定>`
    - 玩家名取该用户的绑定；没绑定就把按钮改成引导绑定
    - 原消息没点名（走的就是绑定）、或属于 trend/lb/me 这类子指令 → 不挂
    """
    try:
        from modules.commands import _get_bound_player
        from services.wdsj_api import resolve_template
    except Exception:
        return None

    parts = (cmd_text or "").strip().lstrip("/").lstrip("~#").split()
    if len(parts) < 2 or parts[0].lower() != "wdsj":
        return None
    if parts[1].lower() in _WDSJ_SKIP_ACTIONS:
        return None

    mode, named = "bw", False
    for tk in parts[1:]:
        low = tk.lower()
        if low in ("text", "img", "图片", "文字"):
            continue
        if resolve_template(low):
            mode = low
        else:
            named = True
    if not named:
        return None                      # 本来就是查自己

    bound = _get_bound_player(user_id)
    if bound:
        label, data = "查查我的", f"/~wdsj {mode} {bound}"
    else:
        label, data = "先绑定玩家名", "/~wdsj bd"

    return {"content": {"rows": [{"buttons": [{
        "id": "wdsj_mine",
        "render_data": {"label": label, "visited_label": label, "style": 1},
        "action": {
            "type": 2,                   # 2 = 指令按钮（点击替用户发消息）
            "permission": {"type": 2},   # 2 = 所有人可点
            "data": data,
            "unsupport_tips": "请升级到最新版 QQ 再点这个按钮喵",
        },
    }]}]}}


async def dispatch(content: str, ctx: ReplyCtx) -> None:
    """指令 → handle_command；非指令直接静默。"""
    global _CUR
    text = (content or "").strip()
    # 群里 @ 之后常带空格/CQ，先剥
    text = re.sub(r"^(\s|\[CQ:[^\]]+\])+", "", text).strip()

    if not _is_command(text):
        log("非指令，忽略:", text[:40])
        return

    if _is_banned(text):
        log("AGC 限制指令，拒绝:", text[:30])
        _CUR = ctx
        try:
            send_text(BANNED_REPLY)
        finally:
            _CUR = None
        return

    _CUR = ctx
    try:
        from modules.commands import handle_command
        is_group = ctx.scene == "groups"
        uid = _stable_id(ctx.sender or ctx.openid)
        # 战绩查询挂「查查我的」按钮（点了自动填 /~wdsj <模式> <绑定>）
        # 挂 keyboard_late：等图片发完再补，按钮才会出现在图片下面
        if _cmd_name(text) == "wdsj":
            kb = build_wdsj_keyboard(text, uid)
            if kb:
                ctx.keyboard_late = kb
        reply = await handle_command(
            text,
            user_id=uid,
            group_id=_stable_id(ctx.openid) if is_group else 0,
            sender_name="官方渠道用户",
            is_group=is_group,
            bot_qq=int(APPID) if APPID.isdigit() else 0,
        )
        if reply:
            send_text(str(reply))
    except Exception as e:
        log("指令执行异常:", type(e).__name__, e)
        try:
            send_text("这条指令执行出错了，等会再试喵。")
        except Exception:
            pass
    finally:
        _CUR = None


# ────────────────────── 插件（只到 on_load，不启用） ──────────────────────
async def load_plugin_commands() -> list[str]:
    """加载插件到「注册完指令」为止，**不调 on_enable**。

    ★ 为什么不用 PluginManager.load_all()：load_all 会走 enable → on_enable，
      而 plugins/bg_tasks 的 on_enable 里起了 6 个后台循环（含每 12h 的 wdsj 采集、
      每 300s 的故障巡检）。官方进程是独立进程，再跑一遍会和主 bot 抢同一份
      wdsj_history.json —— 并发采集、上游压力翻倍，正是 v2.0.4s 那次
      「日志双进度流 + 批量 ConnectTimeout」的成因。

    ★ 为什么跳过 enable 不丢指令：全部 24 个插件都在 on_load 里 register_command
      （2026-10-31 逐个核对过），on_enable 只负责起后台任务。所以只走到 init，
      指令一条不少，后台任务一个不起。
    """
    from core.plugin import get_plugin_manager
    pm = get_plugin_manager()
    pm.discover()
    loaded: list[str] = []
    for name in pm.load_order():
        miss = pm.missing_deps(name)
        if miss:
            log(f"插件 {name} 跳过：缺依赖 {miss}")
            continue
        ok, err = await pm.load(name)
        if not ok:
            log(f"插件 {name} 加载失败: {err}")
            continue
        ok2, err2 = await pm.init(name)      # 只跑到 on_load
        if not ok2:
            log(f"插件 {name} on_load 失败: {err2}")
            continue
        loaded.append(name)
    return loaded


# ────────────────────────── 网关 ──────────────────────────
INTENTS = 1 << 25      # GROUP_AND_C2C_EVENT


async def run_gateway() -> None:
    # 插件指令（后台任务一律不启，见 load_plugin_commands 的说明）
    try:
        names = await load_plugin_commands()
        import modules.commands as _cmds
        # 官方渠道不显示「正在查询…」这类进度提示：它会横在战绩图和按钮之间，
        # 观感是按钮跑到图片上面。NapCat 那边保持原样。
        _cmds.SUPPRESS_PROGRESS_TIP = True
        log(f"插件已加载 {len(names)} 个（仅指令）: {', '.join(names)}")
        log(f"COMMAND_MAP 共 {len(_cmds.COMMAND_MAP)} 条指令")
    except Exception as e:
        log("插件加载失败，只用内置指令:", type(e).__name__, e)

    backoff = 3
    while True:
        try:
            token = get_token()
            async with websockets.connect(
                GATEWAY,
                additional_headers={"Authorization": f"QQBot {token}",
                                    "X-Union-Appid": APPID},
                max_size=8 * 1024 * 1024,
            ) as ws:
                await ws.send(json.dumps({"op": 2, "d": {
                    "token": f"QQBot {token}",
                    "intents": INTENTS,
                    "shard": [0, 1],
                }}))
                log("IDENTIFY 已发送")
                heartbeat = 30.0
                last_hb = 0.0
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        if time.time() - last_hb >= heartbeat:
                            await ws.send(json.dumps({"op": 1, "d": int(time.time() * 1000)}))
                            last_hb = time.time()
                        continue

                    ev = json.loads(raw)
                    op = ev.get("op", 0)
                    if op == 10:                     # HELLO
                        heartbeat = (ev.get("d", {}).get("heartbeat_interval", 30000)) / 1000
                        await ws.send(json.dumps({"op": 1, "d": int(time.time() * 1000)}))
                        last_hb = time.time()
                        log("HELLO，心跳", heartbeat, "s")
                    elif op == 11:
                        pass
                    elif op == 7:                    # 要求重连
                        log("网关要求重连")
                        break
                    elif op == 9:                    # 会话失效
                        log("会话失效，重新 IDENTIFY")
                        break
                    elif op == 0:
                        t = ev.get("t")
                        d = ev.get("d") or {}
                        if t == "READY":
                            user = d.get("user", {})
                            log("READY:", user.get("username"), user.get("id"))
                            backoff = 3
                        elif t == "GROUP_AT_MESSAGE_CREATE":
                            gid = d.get("group_openid", "")
                            author = (d.get("author") or {}).get("member_openid") or gid
                            log(f"群@ 收到 group={gid[:8]}…")
                            await dispatch(d.get("content", ""),
                                           ReplyCtx("groups", gid, d.get("id", ""), author))
                        elif t == "C2C_MESSAGE_CREATE":
                            uid = (d.get("author") or {}).get("user_openid", "")
                            log(f"私聊 收到 user={uid[:8]}…")
                            await dispatch(d.get("content", ""),
                                           ReplyCtx("users", uid, d.get("id", ""), uid))
                        elif t:
                            log("其他事件:", t)
        except Exception as e:
            log("网关异常:", type(e).__name__, e)

        log(f"{backoff}s 后重连")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


def main() -> int:
    if not APPID or not SECRET:
        log("缺少 QQ_APPID / QQ_SECRET 环境变量，拒绝启动")
        return 2
    log(f"官方机器人启动 appid={APPID}")
    install_sender_shim()
    try:
        asyncio.run(run_gateway())
    except KeyboardInterrupt:
        log("收到中断，退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
