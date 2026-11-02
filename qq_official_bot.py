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


def _load_env_file(path: Path) -> None:
    """补齐 systemd `EnvironmentFile=` 的等价物（Windows 备用机上没有 systemd）。

    只补**缺失**的键：服务器上 systemd 已经把 QQ_APPID/QQ_SECRET 注进环境，
    这里逐条 `k not in os.environ` 全部跳过，行为与改动前完全一致。
    """
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


_load_env_file(_ROOT / "config" / "official_bot.env")

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
    # ★ CQ 码在官方渠道不认（NapCat 会渲染成 @，这边原样显示成文字）。
    #   官方 markdown 的 @ 语法是 `<qqbot-at-user id="member_openid" />`，
    #   但文案里存的是（哈希后的）QQ 号，反查不到 openid。
    #   实际场景几乎都是「@ 当前发消息的人」→ 直接用发送者 openid 顶替。
    #   ⚠️ @ 标签只在 markdown 消息里渲染；markdown 不可用时直接剥掉。
    if _MD_STATE.get(_CUR.scene, "unknown") != "off" and _CUR.sender:
        text = re.sub(r"\[CQ:at,qq=[^\]]+\]",
                      f'<qqbot-at-user id="{_CUR.sender}" />', text)
    # 其余 CQ 码（image/face/record…）一律剥掉，别让它们露出来
    text = _CQ_ANY.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
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


# 二维码这类要扫码的图仍走富媒体：markdown 内嵌图在部分客户端长按识别不稳。
# 其余图片统一走图床（markdown 内嵌），这样能带按钮、能写引用块。
_RICH_IMAGE_PREFIXES = ("phi_qr_",)
_MD_IMAGE_ALL = True


def send_image(path: Path) -> bool:
    # ⚠️ 本函数内给 _KB_OK 赋值，必须显式 global，否则它变成局部变量，
    #    前面的读取会 UnboundLocalError（2026-10-31 踩过，整个图片发送挂掉）
    global _KB_OK
    if _CUR is None:
        return False
    if _CUR.exhausted:
        log("被动回复次数用尽，丢弃图片:", path.name)
        return False

    # ★ 图片统一走本地图床（markdown 内嵌）：markdown 图能和按钮/引用块同一条消息，
    #   富媒体气泡挂不了 keyboard；统一转 JPEG 也避开手机端不认 webp 的问题。
    if _MD_IMAGE_ALL and not Path(str(path)).name.startswith(_RICH_IMAGE_PREFIXES):
        url, w, h = to_public_jpeg(path)
        if url:
            # 攒着的按钮直接挂到这条 markdown 上（不再需要「图后补一条」）
            if _CUR.keyboard_late and not _CUR.keyboard:
                _CUR.keyboard, _CUR.keyboard_late = _CUR.keyboard_late, None
            return send_text(f"![#{w}px #{h}px]({url})")
        log("图床失败，回退富媒体:", Path(str(path)).name)

    info = upload_image(path)
    if not info:
        return False
    seq = _next_seq()
    body = {"content": "", "msg_type": 7, "media": {"file_info": info},
            "msg_id": _CUR.msg_id, "msg_seq": seq}

    # 图片消息也带 keyboard：客户端若认，按钮就和图在同一个气泡里
    # （2026-10-31 用户要求「图和按钮在同一个消息」）。
    kb = (_CUR.keyboard_late or _CUR.keyboard) if _KB_OK else None
    if kb:
        body["keyboard"] = kb

    st, data = _post_message(body)
    kb_used = bool(kb)
    if st not in (200, 201) and kb_used:
        # 带按钮失败 → 平台可能不允许富媒体带键盘，去掉重试
        _KB_OK = False
        log("图片带按钮失败，去掉按钮重试:", st, str(data)[:140])
        body.pop("keyboard", None)
        st, data = _post_message(body)
        kb_used = False

    if st not in (200, 201):
        log("图片发送失败 HTTP", st, str(data)[:200])
        return False

    if kb_used:
        # 按钮已经随图片发出去了，不再补一条文本
        _CUR.keyboard = None
        _CUR.keyboard_late = None
    elif _CUR.keyboard_late and _KB_OK:
        # 没带上（比如按钮能力被关）→ 退回「补一条短文本」，至少按钮还在
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


# ────────────────────── 本地图床（markdown 内嵌图专用） ──────────────────────
# wdsj 只给 webp，手机 QQ 不渲染 webp；素材库上传又要多花 1.4s。
# 改成：下载 → 转 JPEG → 存到 nginx 的 /bot-img/ → 直接给 URL。
# nginx 侧：at.01240820.xyz.conf 里 `location ^~ /bot-img/`（^~ 必须，
# 否则被宝塔的 `location ~ .*\.(jpg|png)$` 正则抢走 → 404）。
# ★ 2026-11-02：IMG_DIR / IMG_BASE 收口到 core.hostenv —— 这是全项目唯一的
#   平台差异点（Linux 走 /www 静态目录，Windows 走项目内目录 + 隧道）。
#   服务器不设 BOT_IMG_DIR / BOT_IMG_BASE 时取 Linux 默认值，与改动前完全一致。
from core.hostenv import IMG_BASE, IMG_DIR  # noqa: E402

IMG_KEEP = 500                    # 所有图片都进图床，留 500 张（约 200MB）

_MEDIA_CACHE_FILE = _ROOT / "data" / "wdsj_media_cache.json"


def _load_media_cache() -> dict:
    try:
        return json.loads(_MEDIA_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_media_cache(d: dict) -> None:
    try:
        _MEDIA_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MEDIA_CACHE_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log("图床缓存写入失败:", type(e).__name__, e)


def to_public_jpeg(path, *, max_long_side: int = 1440, quality: int = 88) -> tuple[str, int, int]:
    """任意图片 → JPEG → 本地图床，返回 (url, w, h)。失败返回 ("", 0, 0)。

    ★ 2026-11-01 用户要求「图片都走图床，可玩性高」：
      markdown 内嵌图能和按钮、引用块落在同一条消息里，富媒体气泡做不到；
      统一转 JPEG 顺带解决手机端不认 webp 的老问题。
    按内容 md5 命名 → 同一张图只存一份，天然去重。
    """
    import hashlib
    import io as _io

    p = Path(str(path))
    raw = p.read_bytes()
    key = "img_" + hashlib.md5(raw).hexdigest()[:16]
    out = IMG_DIR / f"{key}.jpg"
    url = f"{IMG_BASE}/{key}.jpg"

    try:
        from PIL import Image
        if out.exists():
            with Image.open(out) as im:
                return url, im.width, im.height
        im = Image.open(_io.BytesIO(raw))
        w, h = im.size
        if max(w, h) > max_long_side:
            r = max_long_side / max(w, h)
            im = im.resize((max(1, int(w * r)), max(1, int(h * r))), Image.LANCZOS)
            w, h = im.size
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        im.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
    except Exception as e:
        log("图床转换失败:", p.name, type(e).__name__, str(e)[:120])
        return "", 0, 0

    _trim_images()
    log(f"图床已就绪 {key} {w}x{h} {(out.stat().st_size / 1024):.0f}KB")
    return url, w, h


def _trim_images() -> None:
    """文件太多时删最旧的，别把系统盘撑满。"""
    try:
        files = sorted(IMG_DIR.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
        for f in files[:max(0, len(files) - IMG_KEEP)]:
            f.unlink(missing_ok=True)
    except Exception as e:
        log("图床清理失败:", type(e).__name__, e)


async def resolve_wdsj_image(snapshot: str, image_url: str) -> tuple[str, int, int]:
    """把战绩图变成「手机电脑都能看」的 JPEG 公网地址。

    缓存命中 → 直接返回（连下载都跳过，整条指令 ~0.9s）
    未命中   → 下载 webp → 转 JPEG → 落盘 → 返回 URL（约 2.4s）
    """
    key = f"wdsj_{snapshot}"
    out = IMG_DIR / f"{key}.jpg"
    cache = _load_media_cache()
    hit = cache.get(key)
    if hit and hit.get("url") and out.exists():
        log("图床缓存命中:", key)
        return hit["url"], hit.get("w", 0), hit.get("h", 0)

    from services import wdsj_api as api
    tmp = _ROOT / "data" / "img_temp" / f"{key}.webp"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if not await api.download_stats_image(image_url, str(tmp)):
        log("战绩图下载失败:", image_url)
        return "", 0, 0

    try:
        from PIL import Image
        im = Image.open(tmp)
        w, h = im.size
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        im.convert("RGB").save(out, "JPEG", quality=88, optimize=True)
    except Exception as e:
        log("转 JPEG 失败:", type(e).__name__, e)
        return "", 0, 0
    finally:
        tmp.unlink(missing_ok=True)

    url = f"{IMG_BASE}/{key}.jpg"
    cache[key] = {"url": url, "w": w, "h": h, "ts": int(time.time())}
    _save_media_cache(cache)
    _trim_images()
    log(f"图床已就绪 {key} {w}x{h} {(out.stat().st_size / 1024):.0f}KB")
    return url, w, h


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
# 这些子指令跟「查某个人的战绩」无关，一律不挂按钮。
# ⚠️ 2026-11-01：漏了 daily/rank 等，日榜下面也被挂了「查查我的」（用户实测指出）。
# 改这个集合时必须把 cmd_wdsj 的 action 分支全部过一遍。
_WDSJ_SKIP_ACTIONS = {
    # 卡片/图表
    "me", "dual", "double", "双模式", "双段",
    "trend", "趋势", "走势", "折线",
    # 榜单
    "lb", "ldb", "榜", "榜单", "排行榜", "boards", "rank", "排名", "群排名",
    "daily", "今日", "今天",
    # 其它
    "list", "列表", "collect", "bd", "bind", "绑定", "help", "?",
}


def build_wdsj_keyboard(cmd_text: str, user_id: int) -> dict | None:
    """给「查别人战绩」挂一个「查查我的」按钮。

    - 模式沿用原消息：`/~wdsj arena X` → 按钮发 `/~wdsj arena <绑定>`
    - 玩家名取该用户的绑定；没绑定就把按钮改成引导绑定
    - 原消息没点名（走的就是绑定）、或属于 trend/lb/me 这类子指令 → 不挂
    """
    try:
        from services.wdsj_api import resolve_template
    except Exception:
        return None

    parts = (cmd_text or "").strip().lstrip("/").lstrip("~#").split()
    if len(parts) < 2 or parts[0].lower() != "wdsj":
        return None
    if parts[1].lower() in _WDSJ_SKIP_ACTIONS:
        return None

    mode = "bw"
    for tk in parts[1:]:
        low = tk.lower()
        if low in ("text", "img", "图片", "文字"):
            continue
        if resolve_template(low):
            mode = low
    # ★ 按钮 data 绝对不能带玩家名。
    #   QQ 把指令按钮的内容填进输入框（不是直接发），带名字等于把「原消息发送者」
    #   写死——任何人点都变成查别人（2026-11-01 实测：A 发 /~wdsj bw img，
    #   B 点「查查我的」，输入框里填的是 A 的名字，B 当场以为按钮坏了）。
    #   统一发 `/~wdsj <模式>`，点了发送就是查「点击者自己」的绑定；
    #   没绑定会走 m59 的引导文案。
    label, data = "查查我的", f"/~wdsj {mode}"

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


def _kb_to_cmd_input(kb: dict | None) -> str:
    """把 keyboard 按钮转成 markdown 内嵌的 `<qqbot-cmd-input>`。

    官方文档「文本交互」：点击后把 text 插进输入框（用户可改），
    参数需要 urlencode。keyboard 在群里不渲染，这个标签可能是群里的替代方案。
    """
    if not kb:
        return ""
    try:
        btn = kb["content"]["rows"][0]["buttons"][0]
        label = btn["render_data"]["label"]
        data = btn["action"]["data"]
    except Exception:
        return ""
    from urllib.parse import quote
    return ('<qqbot-cmd-input text="{0}" show="{1}" />'
            .format(quote(str(data), safe=""), quote(str(label), safe="")))


async def dispatch(content: str, ctx: ReplyCtx) -> None:
    """指令 → handle_command；非指令直接静默。"""
    global _CUR
    text = (content or "").strip()
    # 群里 @ 之后常带空格/CQ，先剥
    # 官方群里 @ 机器人时，content 里会带 `<@3E40895D3C0C6805...>` 这样的记号
    # （2026-10-31 实测：`<@3E40...> 晚安+1`）。不剥掉的话，@ 之后的指令
    # 永远被判成"非指令"——这正是"某个群必须 @"却指令无效的原因。
    text = re.sub(r"^(\s|\[CQ:[^\]]+\]|<@[^>]*>)+", "", text).strip()

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
        # 记原文：排查"按钮没出现""指令没认出来"时，必须看到实际收到的字符串
        log(f"指令原文[{ctx.scene}]:", repr(text))
        # 战绩查询挂「查查我的」按钮（点了自动填 /~wdsj <模式> <绑定>）
        # 挂 keyboard_late：等图片发完再补，按钮才会出现在图片下面
        if _cmd_name(text) == "wdsj":
            kb = build_wdsj_keyboard(text, uid)
            if kb:
                from modules.commands import MD_INLINE_IMAGE as _mdimg
                if _mdimg:
                    ctx.keyboard = kb        # 图在 markdown 里，按钮挂同一条消息
                else:
                    ctx.keyboard_late = kb   # 图是富媒体，只能等图发完再补
        reply = await handle_command(
            text,
            user_id=uid,
            group_id=_stable_id(ctx.openid) if is_group else 0,
            sender_name="官方渠道用户",
            is_group=is_group,
            bot_qq=int(APPID) if APPID.isdigit() else 0,
        )
        if reply:
            body = str(reply)
            # 按钮同时用 markdown 内嵌标签给一份：keyboard 在群里不渲染，
            # <qqbot-cmd-input> 可能可以（文档没写群聊不支持）
            if ctx.keyboard and _MD_STATE.get(ctx.scene, "unknown") != "off":
                tag = _kb_to_cmd_input(ctx.keyboard)
                if tag:
                    body = body + "\n\n" + tag
            send_text(body)
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
        # 渠道行为（不显示进度提示、战绩图走 markdown）由 BOT_CHANNEL=official
        # 在 modules/commands.py 里静态决定，这里不再运行期改全局变量。
        # 只剩这个函数对象必须注入（环境变量传不了 callable）。
        # 战绩图走本地图床（nginx /bot-img/）：转 JPEG + 直接给 URL，
        # 比素材库少一次 1.4s 上传；手机上也能显示。
        _cmds.MD_IMAGE_RESOLVER = resolve_wdsj_image
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
                        # ★ 2026-11-01：这里原来只打四个字，close code 全丢了。
                        #   实际上平台是「WS 握手放行 → IDENTIFY 时校验来源 IP →
                        #   不匹配就 op=9 + close 4924」。不记 close code 的话，
                        #   日志里只有「会话失效」，看起来像 token 问题，会白查很久。
                        # 平台是先发 op=9、再发 close frame，这里读太早会拿到 None
                        # （2026-11-01 实测日志全是 close=None）。主动等一下再读。
                        try:
                            await asyncio.wait_for(ws.recv(), timeout=1.5)
                        except Exception:
                            pass
                        _code = getattr(ws, "close_code", None)
                        _reason = getattr(ws, "close_reason", "") or ""
                        if _code == 4924 or "ip forbidden" in str(_reason):
                            log(f"会话失效：close={_code} {_reason}"
                                " —— IP 白名单不匹配，去开放平台加当前出口 IP")
                        else:
                            log(f"会话失效（op=9）close={_code} {_reason}")
                        break
                    elif op == 0:
                        t = ev.get("t")
                        d = ev.get("d") or {}
                        if t == "READY":
                            user = d.get("user", {})
                            log("READY:", user.get("username"), user.get("id"))
                            backoff = 3
                        elif t in ("GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"):
                            # GROUP_MESSAGE_CREATE 是「群内全量消息」（本应用有这个权限）。
                            # 只处理它的好处：群里发 /~ 指令不用先 @；非指令一律静默，
                            # 不会变成"什么都插嘴"。两者数据结构一致。
                            gid = d.get("group_openid", "")
                            au = d.get("author") or {}
                            author = au.get("member_openid") or au.get("id") or gid
                            if au.get("bot"):
                                continue          # 机器人自己发的，别回自己
                            tag = "群@" if t.endswith("AT_MESSAGE_CREATE") else "群"
                            log(f"{tag} 收到 group={gid[:8]}…")
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
