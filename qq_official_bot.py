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


def send_text(text: str) -> bool:
    """发纯文本（被动回复）。超长按 1800 字切块。"""
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
        seq = _next_seq()
        body = {"content": chunk, "msg_type": 0, "msg_id": _CUR.msg_id, "msg_seq": seq}
        st, data = _post_message(body)
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
    if st in (200, 201):
        return True
    log("图片发送失败 HTTP", st, str(data)[:200])
    return False


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
        reply = await handle_command(
            text,
            user_id=_stable_id(ctx.sender or ctx.openid),
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


# ────────────────────────── 网关 ──────────────────────────
INTENTS = 1 << 25      # GROUP_AND_C2C_EVENT


async def run_gateway() -> None:
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
