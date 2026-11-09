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

from contextvars import ContextVar
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
        self.button_tag = None      # markdown 内嵌 <qqbot-cmd-input>（点击只填输入框，不 @ 机器人）
        self.last_msg_id = None     # ★ 最近一条**已发出**回复的消息 id（撤回用）


# ★ 2026-11-06：模块级全局改成 ContextVar。
#   插件（netmusic / skin / weather）都是「先回一句提示，再 asyncio.create_task
#   请求接口，拿到结果后二次发送」。原来 _CUR 是全局变量，dispatch 的 finally
#   一置 None，后台任务再调 send_text 就撞上 `assert _CUR is not None`，
#   结果全部丢失 —— 现象就是「只有提示，没有后续」。
#   create_task 会复制当前 context，异步任务因此能拿到发起这次请求的 ReplyCtx；
#   不同群的并发请求也互不干扰（全局变量会被后来的消息覆盖）。
_CUR_VAR: ContextVar[ReplyCtx | None] = ContextVar("official_reply_ctx", default=None)


def _cur() -> ReplyCtx | None:
    """当前回复上下文。异步任务内自动继承创建任务时的值。"""
    return _CUR_VAR.get()


def _next_seq() -> int:
    if _cur() is None:
        return 0
    _cur().seq += 1
    # ★ 平台硬限制：群聊每条消息最多回 5 次，单聊只有 4 次
    _limit = 4 if _cur().scene == "users" else MAX_PASSIVE_REPLIES
    if _cur().seq > _limit:
        _cur().exhausted = True
    return _cur().seq


def _post_message(payload: dict) -> tuple[int, dict]:
    assert _cur() is not None
    st, data = _api("POST", f"/v2/{_cur().scene}/{_cur().openid}/messages", payload)
    # ★ 2026-11-03：记下这条回复的消息 id，插件的 delete_msg 撤回要靠它。
    #   官方 API 撤回的是「机器人自己发出的消息」，不是用户那条入站的。
    if st in (200, 201) and isinstance(data, dict) and data.get("id"):
        _cur().last_msg_id = data["id"]
    return st, data


def _delete_message(msg_id: str) -> tuple[int, dict]:
    """撤回机器人自己发出的消息。

    官方接口：`DELETE /v2/{scene}/{openid}/messages/{message_id}`
    —— 只能撤自己发的，且有时限（约 2 分钟），超时返回 4xx。
    """
    if _cur() is None or not msg_id:
        return 0, {"err": "no_msg_id"}
    try:
        st, data = _api("DELETE", f"/v2/{_cur().scene}/{_cur().openid}/messages/{msg_id}")
        log(f"撤回消息 {msg_id} → HTTP {st}")
        return st, data
    except Exception as e:
        log("撤回失败:", type(e).__name__, e)
        return 0, {}


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
    body: dict = {"msg_id": _cur().msg_id, "msg_seq": seq}
    if use_md:
        body.update({"content": "", "msg_type": 2,
                     "markdown": {"content": chunk}})
    else:
        body.update({"content": chunk, "msg_type": 0})
    if with_kb and _cur().keyboard:
        body["keyboard"] = _cur().keyboard
    return body


def _content_with_attachments(d: dict) -> str:
    """把图片附件的 URL 并入 content。

    ★ 2026-11-08：官方图片消息的图在 attachments 里，content 往往是空的。
      插件（/~摸头 等）只会从文本里提取 URL，不并进来就永远"找不到图片来源"。
    """
    content = d.get("content", "") or ""
    for att in (d.get("attachments") or []):
        if not str(att.get("content_type", "")).startswith("image"):
            continue
        u = att.get("url") or ""
        if u and u not in content:
            content = (content + " " + u).strip()
    return content


def _get_message(msg_id: str) -> tuple[int, dict]:
    """查单条消息，返回 OneBot 风格的 data。

    官方接口：`GET /v2/{scene}/{openid}/messages/{message_id}`
    ★ 2026-11-08：插件的 fetch_quote_images 依赖 get_msg 取被引用消息里的图片，
      而这个 action 原先不在转发白名单，静默返回空 data —— 于是「引用一张图再用
      /~摸头」在官方渠道永远提示"找不到图片来源"。这里把官方 attachments 转成
      插件期待的 message 段格式（type=image / data.url）。
    """
    if _cur() is None or not msg_id:
        return 0, {}
    try:
        st, data = _api("GET", f"/v2/{_cur().scene}/{_cur().openid}/messages/{msg_id}")
    except Exception as e:
        log("查询消息失败:", type(e).__name__, str(e))
        return 0, {}
    if st != 200:
        return st, {}
    segs = []
    for att in (data.get("attachments") or []):
        if str(att.get("content_type", "")).startswith("image"):
            segs.append({"type": "image",
                         "data": {"url": att.get("url", ""),
                                  "file": att.get("filename", "")}})
    if data.get("content"):
        segs.append({"type": "text", "data": {"text": data["content"]}})
    return st, {"message": segs,
                "raw_message": data.get("content", ""),
                "message_id": msg_id,
                "sender": (data.get("author") or {}).get("id", "")}


def _send_chunk(chunk: str) -> tuple[int, dict]:
    """发一段文本：优先 markdown+按钮，逐级降级到纯文本。"""
    global _KB_OK
    assert _cur() is not None
    scene = _cur().scene
    state = _MD_STATE.get(scene, "unknown")
    use_md = state != "off"
    use_kb = bool(_cur().keyboard) and _KB_OK

    st, data = _post_message(_msg_body(chunk, use_md, use_kb))
    if st in (200, 201):
        if use_md and state == "unknown":
            _MD_STATE[scene] = "on"
            log(f"markdown 可用（{scene}），后续回复切富文本")
        _cur().keyboard = None
        return st, data

    # ① 带按钮失败 → 多半是没开「消息按钮」权限，去掉重试并永久关闭
    if use_kb:
        _KB_OK = False
        log(f"按钮发送失败（{scene} HTTP {st}），后续不再带按钮:", str(data))
        st, data = _post_message(_msg_body(chunk, use_md, False))
        if st in (200, 201):
            _cur().keyboard = None
            return st, data

    # ② markdown 失败 → 回退纯文本
    if use_md:
        _MD_STATE[scene] = "off"
        log(f"markdown 不可用（{scene} HTTP {st}），回退纯文本:", str(data))
        st, data = _post_message(_msg_body(chunk, False, False))

    _cur().keyboard = None
    return st, data


def md_enabled(scene: str | None = None) -> bool:
    """markdown 是否已确认可用（scene 为空则任一群/私聊可用即算）。"""
    if scene:
        return _MD_STATE.get(scene) == "on"
    return any(v == "on" for v in _MD_STATE.values())


def send_text(text: str) -> bool:
    """发文本（被动回复）。markdown 可用时自动走富文本。超长按 1800 字切块。"""
    if _cur() is None or not text:
        return False
    text = str(text).strip()
    # ★ CQ 码在官方渠道不认（NapCat 会渲染成 @，这边原样显示成文字）。
    #   官方 markdown 的 @ 语法是 `<qqbot-at-user id="member_openid" />`，
    #   但文案里存的是（哈希后的）QQ 号，反查不到 openid。
    #   实际场景几乎都是「@ 当前发消息的人」→ 直接用发送者 openid 顶替。
    #   ⚠️ @ 标签只在 markdown 消息里渲染；markdown 不可用时直接剥掉。
    if _MD_STATE.get(_cur().scene, "unknown") != "off" and _cur().sender:
        text = re.sub(r"\[CQ:at,qq=[^\]]+\]",
                      f'<qqbot-at-user id="{_cur().sender}" />', text)
    # 其余 CQ 码（image/face/record…）一律剥掉，别让它们露出来
    text = _CQ_ANY.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if not text:
        return False
    ok = True
    while text:
        chunk, text = text[:1800], text[1800:]
        if _cur().exhausted:
            log("被动回复次数用尽，丢弃文本:", chunk)
            return False
        st, data = _send_chunk(chunk)
        if st not in (200, 201):
            log("文本回复失败 HTTP", st, str(data))
            ok = False
    return ok


def upload_image(path: Path) -> str:
    """上传图片拿 file_info（file_type=1 图片）。失败返回空串。"""
    if _cur() is None or not path.is_file():
        return ""
    if path.stat().st_size > 8 * 1024 * 1024:
        log("图片过大，跳过:", path.name)
        return ""
    body = {
        "file_type": 1,
        "file_data": base64.b64encode(path.read_bytes()).decode(),
        "srv_send_msg": False,
    }
    st, data = _api("POST", f"/v2/{_cur().scene}/{_cur().openid}/files", body, timeout=60)
    if st in (200, 201) and data.get("file_info"):
        return data["file_info"]
    log("图片上传失败 HTTP", st, str(data))
    return ""


# 二维码这类要扫码的图仍走富媒体：markdown 内嵌图在部分客户端长按识别不稳。
# ★ 2026-11-03：phi 卡片也改走富媒体 —— 用户明确要求「纯发图片」，
#   而 markdown 内嵌会在 QQ 客户端外面套一层消息气泡（"聊天框"）。
#   走富媒体还能绕过 JPEG 压缩，卡片按原图 PNG 直发，清晰度不损失。
# ★ 2026-11-06：加 skin_ —— 皮肤动图（skin_spin_*.gif / skin_<action>_*.gif）
#   走富媒体原图直发才能保住动画；万一素材库权限没开，仍会自动退回图床
#   （to_public_image 对 gif 原样落盘，退回后也是动的）。
# ★ 2026-11-07：加 motou_ —— 摸头 GIF 同理，10 帧动画不能压成静态图。
_RICH_IMAGE_PREFIXES = ("phi_", "skin_", "motou_")
_MD_IMAGE_ALL = True


def _is_face_image(path) -> bool:
    """是否表情素材（data/faces/ 下的图）。

    ★ 2026-11-09：表情文件名是 `<标签>_<编号>.png`（sleeping_026.png），没有统一前缀，
      不能靠 _RICH_IMAGE_PREFIXES 匹配。按父目录判断最稳。
    """
    try:
        return Path(str(path)).parent.name == "faces"
    except Exception:
        return False


def _shrink_face(path, max_side: int = 240):
    """表情按“小图”发：webp 转 png/gif，超尺寸缩到 max_side 以内。

    - 动图（多帧 webp）→ gif；静图 → png（保留透明）
    - 产物写到 data/img_temp/，不污染 data/faces/（face_lib 会扫描该目录）
    - 任何异常都回退原路径，保证表情不至于发不出去
    """
    src = Path(str(path))
    try:
        from PIL import Image

        suffix = src.suffix.lower()
        need_fmt = suffix == ".webp"
        with Image.open(src) as im:
            n_frames = getattr(im, "n_frames", 1)
            w, h = im.size
            need_resize = max(w, h) > max_side
            if not need_fmt and not need_resize:
                return src
            if need_resize:
                r = max_side / max(w, h)
                size = (max(1, int(w * r)), max(1, int(h * r)))
            else:
                size = (w, h)
            out_dir = Path(__file__).resolve().parent / "data" / "img_temp"
            out_dir.mkdir(parents=True, exist_ok=True)
            if need_fmt and n_frames > 1:
                out = out_dir / (src.stem + "_sm.gif")
                frames, durations = [], []
                for i in range(n_frames):
                    im.seek(i)
                    durations.append(im.info.get("duration", 100))
                    frames.append(im.convert("RGBA").resize(size, Image.LANCZOS)
                                  .convert("P", palette=Image.ADAPTIVE))
                frames[0].save(out, save_all=True, append_images=frames[1:],
                               duration=durations, loop=0, disposal=2)
            elif need_fmt:
                out = out_dir / (src.stem + "_sm.png")
                im.convert("RGBA").resize(size, Image.LANCZOS).save(out, "PNG")
            else:
                out = out_dir / (src.stem + "_sm" + suffix)
                im.convert("RGBA").resize(size, Image.LANCZOS).save(out)
        log(f"表情小图已生成: {out.name} {size[0]}x{size[1]}")
        return out
    except Exception as e:
        log("表情图处理失败，按原图发送:", src.name, type(e).__name__, e)
        return src


def send_image(path: Path) -> bool:
    # ⚠️ 本函数内给 _KB_OK 赋值，必须显式 global，否则它变成局部变量，
    #    前面的读取会 UnboundLocalError（2026-10-31 踩过，整个图片发送挂掉）
    global _KB_OK
    if _cur() is None:
        return False
    if _cur().exhausted:
        log("被动回复次数用尽，丢弃图片:", path.name)
        return False

    # ★ 图片统一走本地图床（markdown 内嵌）：markdown 图能和按钮/引用块同一条消息，
    #   富媒体气泡挂不了 keyboard；统一转 JPEG 也避开手机端不认 webp 的问题。
    # ★ 2026-11-09：表情素材强制走富媒体（纯图片），并按小图发送
    _is_face = _is_face_image(path)
    if _is_face:
        path = _shrink_face(path)
    if _MD_IMAGE_ALL and not _is_face and not Path(str(path)).name.startswith(_RICH_IMAGE_PREFIXES):
        url, w, h = to_public_image(path)
        if url:
            # 攒着的按钮直接挂到这条 markdown 上（不再需要「图后补一条」）
            if _cur().keyboard_late and not _cur().keyboard:
                _cur().keyboard, _cur().keyboard_late = _cur().keyboard_late, None
            return send_text(f"![#{w}px #{h}px]({url})")
        log("图床失败，回退富媒体:", Path(str(path)).name)

    info = upload_image(path)
    if not info:
        # ★ 2026-11-03：富媒体上传失败时退回图床 + markdown，
        #   否则（如素材库权限没开）图片会彻底发不出去，用户只看到空气。
        if _MD_IMAGE_ALL:
            url, w, h = to_public_image(path)
            if url:
                log("富媒体失败，退回 markdown:", Path(str(path)).name)
                return send_text(f"![#{w}px #{h}px]({url})")
        return False
    seq = _next_seq()
    body = {"content": "", "msg_type": 7, "media": {"file_info": info},
            "msg_id": _cur().msg_id, "msg_seq": seq}

    # 图片消息也带 keyboard：客户端若认，按钮就和图在同一个气泡里
    # （2026-10-31 用户要求「图和按钮在同一个消息」）。
    kb = (_cur().keyboard_late or _cur().keyboard) if _KB_OK else None
    if kb:
        body["keyboard"] = kb

    st, data = _post_message(body)
    kb_used = bool(kb)
    if st not in (200, 201) and kb_used:
        # 带按钮失败 → 平台可能不允许富媒体带键盘，去掉重试
        _KB_OK = False
        log("图片带按钮失败，去掉按钮重试:", st, str(data))
        body.pop("keyboard", None)
        st, data = _post_message(body)
        kb_used = False

    if st not in (200, 201):
        log("图片发送失败 HTTP", st, str(data))
        return False

    if kb_used:
        # 按钮已经随图片发出去了，不再补一条文本
        _cur().keyboard = None
        _cur().keyboard_late = None
    elif _cur().keyboard_late and _KB_OK:
        # 没带上（比如按钮能力被关）→ 退回「补一条短文本」，至少按钮还在
        _cur().keyboard, _cur().keyboard_late = _cur().keyboard_late, None
        send_text(KEYBOARD_HINT)
    return True


def send_cq(cq_or_text: str) -> bool:
    """把 CQ 码字符串拆成「文本 + 图片」依次发出。"""
    if _cur() is None or not cq_or_text:
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


def to_public_image(path, *, max_long_side: int = 2560, quality: int = 95) -> tuple[str, int, int]:
    """任意图片 → 本地图床，返回 (url, w, h)。失败返回 ("", 0, 0)。

    ★ 2026-11-01 用户要求「图片都走图床，可玩性高」：
      markdown 内嵌图能和按钮、引用块落在同一条消息里，富媒体气泡做不到。

    ★ 2026-11-06：改成**按格式分派**，不再一律转 JPEG。
      原来全转 JPEG 的后果：gif 只剩第一帧（动图没了）、png 透明层变白底。
      现在：
        webp → JPEG   （手机端不认 webp，这是唯一必须转的）
        gif  → 原样落盘（保留动画，动图不缩放，缩了会丢帧）
        png  → 原样落盘（保留 alpha）
        jpg/jpeg/bmp → 原样落盘，仅在超过 max_long_side 时重编码
      按内容 md5 命名 → 同一张图只存一份，天然去重。
    """
    import hashlib
    import io as _io

    p = Path(str(path))
    raw = p.read_bytes()
    suffix = p.suffix.lower()
    key = "img_" + hashlib.md5(raw).hexdigest()[:16]

    if suffix in (".png", ".gif", ".jpg", ".jpeg", ".bmp"):
        target_ext = suffix
    else:
        target_ext = ".jpg"          # webp 及其他：转 JPEG
    out = IMG_DIR / f"{key}{target_ext}"
    url = f"{IMG_BASE}/{key}{target_ext}"

    try:
        from PIL import Image
        if out.exists():
            with Image.open(out) as im:
                return url, im.width, im.height

        im = Image.open(_io.BytesIO(raw))
        w, h = im.size
        animated = bool(getattr(im, "is_animated", False))
        IMG_DIR.mkdir(parents=True, exist_ok=True)

        need_resize = max(w, h) > max_long_side and not animated

        # ⚠️ 是否「原样拷贝」必须看**源格式**，不能看目标扩展名。
        #   webp 的目标扩展名是 .jpg，若按目标判断就会把 webp 字节写进 .jpg 文件，
        #   浏览器按 JPEG 解析直接裂图（2026-11-06 实测踩到）。
        passthrough = suffix in (".png", ".gif", ".jpg", ".jpeg", ".bmp")

        if not need_resize and passthrough:
            # 原样落盘：不重编码，无损、快，gif 动画与 png 透明都保住
            out.write_bytes(raw)
        else:
            if need_resize:
                r = max_long_side / max(w, h)
                im = im.resize((max(1, int(w * r)), max(1, int(h * r))), Image.LANCZOS)
                w, h = im.size
            fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".gif": "GIF",
                   ".bmp": "BMP", ".png": "PNG"}.get(target_ext, "JPEG")
            if fmt == "JPEG":
                im.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
            else:
                im.save(out, fmt)
    except Exception as e:
        log("图床转换失败:", p.name, type(e).__name__, str(e))
        return "", 0, 0

    _trim_images()
    log(f"图床已就绪 {key} {w}x{h} {target_ext} {(out.stat().st_size / 1024):.0f}KB")
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

        # ⚠️ 2026-11-03：插件的 `ctx.message.send_ex()` 走的是原生 WS API
        #   （services/sender.send_group_msg → get_ws_manager().call_api），
        #   绕开上面那四个打桩函数。原先这里一律返回空 data，于是
        #   `/~phi login` 出二维码的那条消息被静默吞掉 —— 指令执行了、
        #   日志有「拦截原生 API 调用: send_group_msg」，但群里什么都没收到。
        #   现在把发送类 action 转成官方被动回复，其余（撤回/禁言/表情等）仍拦截。
        _SEND_ACTIONS = ("send_group_msg", "send_private_msg", "send_msg")

        async def call_api(self, action, params=None, timeout=8.0):
            params = params or {}
            if action in self._SEND_ACTIONS:
                msg = params.get("message", "")
                if isinstance(msg, list):     # OneBot 段格式 → CQ 码
                    buf = []
                    for seg in msg:
                        stype = seg.get("type")
                        sdata = seg.get("data") or {}
                        if stype == "text":
                            buf.append(str(sdata.get("text", "")))
                        else:
                            kv = ",".join(f"{k}={v}" for k, v in sdata.items())
                            buf.append(f"[CQ:{stype},{kv}]")
                    msg = "".join(buf)
                ok = send_cq(str(msg)) if msg else True
                return {"status": "ok" if ok else "failed", "retcode": 0,
                        "data": {"message_id": _cur().last_msg_id if _cur() else 0},
                        "echo": "official"}
            if action == "get_msg":
                # 官方有单条消息查询接口，转成 OneBot 段格式给插件用
                _st, _data = _get_message(str(params.get("message_id") or ""))
                return {"status": "ok" if _st == 200 else "failed",
                        "retcode": 0 if _st == 200 else 1404,
                        "data": _data, "echo": "official"}
            if action in ("delete_msg", "delete_group_msg", "delete_private_msg"):
                # 插件拿到的 message_id 多半是上面回填的那个；拿不到就撤最近一条
                mid = params.get("message_id") or (_cur().last_msg_id if _cur() else None)
                st, _ = _delete_message(str(mid) if mid else "")
                return {"status": "ok" if st in (200, 204) else "failed",
                        "retcode": 0, "data": {}, "echo": "official"}
            if action in ("upload_group_file", "upload_private_file"):
                # ★ 2026-11-06：phi 的二维码走的是 send_file_ex → 这个 action。
                #   原先进下面的兜底被静默吞掉，二维码图从来没到过群里
                #   （11-03 用户报「没有图片」，当时误判成 markdown 分流问题）。
                #   官方没有独立文件消息，图片按富媒体发，非图片明确回失败。
                fp = params.get("file") or params.get("path") or ""
                p = Path(str(fp).replace("file:///", "").replace("file://", ""))
                if p.is_file() and p.suffix.lower() in (
                        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                    ok = send_image(p)
                    return {"status": "ok" if ok else "failed",
                            "retcode": 0 if ok else 1,
                            "data": {"message_id": _cur().last_msg_id if _cur() else 0},
                            "echo": "official"}
                log("拦截原生 API 调用（非图片文件，官方渠道不支持）:", action, p.name)
                return {"status": "failed", "retcode": 1404, "data": {}, "echo": "official"}
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


# ── 入站正文清理（指令/聊天分流的前提）────────────────────────
# 零宽与方向控制符：QQ 会在 @ 与正文之间插这些，肉眼看不见，`strip()` 也不动它们。
_INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
# 行首 @ 记号：CQ at、`<@openid>`、markdown 的 `<qqbot-at-user id="..." />`
_MENTION_HEAD_RE = re.compile(r"^(\s|\[CQ:[^\]]+\]|<@[^>]*>|<qqbot-at-user[^>]*>)+")
# 客户端把 @机器人 拼成纯文本：`@织梦 /~wdsj bw`
_AT_TEXT_HEAD_RE = re.compile(r"^@\S+\s+(?=/)")
# 「这条消息真的 @ 了机器人」——必须是真的 mention 记号，光有前导空格不算
_REAL_MENTION_HEAD_RE = re.compile(
    r"^\s*(?:\[CQ:at,[^\]]*\]|<@[^>]*>|<qqbot-at-user[^>]*>)")


def _clean_incoming(text: str) -> str:
    """把入站正文清理成「用户真正打出来的字」。

    顺序很重要：
      1. 先剔零宽字符——它们在 @ 和正文之间，肉眼看不见，`strip()` 清不掉；
         不清的话 `startswith("/")` 判假，指令会被当成聊天送进 LLM。
      2. 再剥行首 @ 记号（三种形态，可能交替出现，反复剥到稳定）。
      3. 最后处理客户端拼出来的纯文本 `@名字 /~cmd`。
    """
    t = _INVISIBLE_RE.sub("", text or "")
    prev = None
    while prev != t:
        prev = t
        t = _MENTION_HEAD_RE.sub("", t).strip()
    m = _AT_TEXT_HEAD_RE.match(t)
    if m:
        t = t[m.end():]
    return t.strip()



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


_GROUP_NAME_CACHE: dict[str, str] = {}


def _group_name(gid: str) -> str:
    """取群名（带内存缓存）。失败返回空串。"""
    if gid in _GROUP_NAME_CACHE:
        return _GROUP_NAME_CACHE[gid]
    try:
        st, d = _api("GET", f"/v2/groups/{gid}/info")
        if st == 200:
            name = str(d.get("group_name") or "")
            _GROUP_NAME_CACHE[gid] = name
            return name
        log("查群名失败 HTTP", st, str(d))
    except Exception as e:
        log("查群名异常:", type(e).__name__, e)
    return ""


def _group_allowed(ctx: ReplyCtx) -> bool:
    """官方渠道群白名单。名单为空 → 放行（兼容旧行为）。

    ★ 2026-11-09：公共服务上线后，机器人可能被拉进任意群。名单外一律静默，
      避免指令/聊天能力外泄。支持填群名或 group_openid。
    """
    try:
        from core.config import get_config
        allow = get_config().official_enabled_groups or []
    except Exception:
        return True
    if not allow:
        return True
    if ctx.scene != "groups":
        return True
    if ctx.openid in allow:
        return True
    name = _group_name(ctx.openid)
    return bool(name) and name in allow


async def _enqueue_chat(text: str, ctx: ReplyCtx) -> None:
    """把一条聊天消息投入 pipeline 队列（不阻塞网关循环）。

    ⚠️ 不能在网关里直接 await process_message：心跳挂在 ws.recv() 的 5 秒超时
       分支上，LLM 一次十几秒会把心跳拖停，平台判掉线。
    上下文经 `_chat_ctx` 传给队列 worker，由其用 ContextVar 设/复位。
    """
    try:
        from core.queues import enqueue_message

        is_group = ctx.scene == "groups"
        uid = _stable_id(ctx.sender or ctx.openid)
        gid = _stable_id(ctx.openid) if is_group else 0
        await enqueue_message(
            # ★ 2026-11-09：必须是「文字」——pipeline 用中文类型名判定
            #   （`if msg_type != "文字": return`）。这里写 "text" 会让每条
            #   聊天消息在回复管道入口被静默丢弃，且只有 debug 日志，
            #   INFO 级别完全看不出问题（本次「@ 了没反应」的根因）。
            msg_type="文字",
            msg_content=text,
            chat_id=gid if is_group else uid,
            sender_name=(f"群友{str(uid)[-4:]}" if is_group else "对方"),
            user_id=uid,
            is_group=is_group,
            bot_qq=int(APPID) if str(APPID).isdigit() else 0,
            raw_event=None,
            raw_message=text,
            quoted_msg="",
            error_report=None,
            error_report_kind="mc",
            is_command=False,
            _detached=False,
            _chat_ctx=ctx,
            # 能入队就代表「@了机器人」或私聊，回复判断不必再让模型猜
            force_reply=True,
        )
        log(f"聊天入队[{ctx.scene}]:", text)
    except Exception as e:
        log("聊天入队失败:", type(e).__name__, e)


async def dispatch(content: str, ctx: ReplyCtx, is_at: bool = False) -> None:
    """指令 → handle_command；非指令且被 @（或私聊）→ 交给 LLM 管道。

    ★ 2026-11-09 隔离约定：**@ 之后只有一条指令时，这条消息永远不进 LLM 管道**。
      指令层和聊天层是两条独立的路：指令直接 handle_command，聊天才入队跑模型。
      判定放在最前面、并先把各种「看不见的前缀」清干净 —— 只要有一个零宽字符或
      一段 @ 记号没剥掉，`startswith("/")` 就会假，指令就会被当成聊天丢给模型。
    """
    text = _clean_incoming(content)

    # ★ 2026-11-09：对话的唯一入口是「真的 @ 了机器人」（事件类型 AT）。
    #   曾经加过一条兜底「内容开头有 mention 记号也算 @」，为的是救一类被平台
    #   按全量消息投递的 @ 消息；但它分不清 mention 的是谁 —— 群友 @ 别人也会命中，
    #   表现为「没 @ 它却插嘴」。用户明确要求「@ 了才是对话，其余一律静止」，
    #   所以这条兜底已撤。若某群确实把 @ 投递成 GROUP_MESSAGE_CREATE，
    #   网关日志会打出 `原始正文（含 mention）`，按那条日志再决定要不要精确比对 openid。

    # ★ 2026-11-09：群白名单闸。名单外的群一律静默（连指令也不响应），
    #   避免机器人被拉进别的群后能力外泄。
    if not _group_allowed(ctx):
        _nm = _group_name(ctx.openid) if ctx.scene == "groups" else ""
        log(f"群不在启用名单，忽略[{ctx.scene}]:", ctx.openid, f"群名={_nm!r}", text)
        return

    if not _is_command(text):
        # ★ 2026-11-09：官方渠道转公共服务后，群内「仅 @ 触发」聊天；
        #   私聊等同于 @。非 @ 的消息一律静默，避免 bot 变成话痨。
        if (is_at or ctx.scene == "users"):
            await _enqueue_chat(text, ctx)
        else:
            log("非指令，忽略:", text)
        return

    # 走到这里就是「只有指令」—— 下面全程不碰 LLM。日志写明，排查时一眼可见。
    log("指令隔离（不进 LLM）:", text)

    if _is_banned(text):
        log("AGC 限制指令，拒绝:", text)
        _CUR_VAR.set(ctx)
        try:
            send_text(BANNED_REPLY)
        finally:
            _CUR_VAR.set(None)
        return

    _CUR_VAR.set(ctx)
    try:
        from modules.commands import handle_command
        is_group = ctx.scene == "groups"
        uid = _stable_id(ctx.sender or ctx.openid)
        # 记原文：排查"按钮没出现""指令没认出来"时，必须看到实际收到的字符串
        log(f"指令原文[{ctx.scene}]:", repr(text))
        # 战绩查询挂「查查我的」按钮（点了自动填 /~wdsj <模式>）
        # ★ 2026-11-09 用户要求：按钮不要带「@机器人」。
        #   keyboard 的 action.type=2 是「指令按钮」，客户端会替用户发一条
        #   `@机器人 /~wdsj ...`；只要那个 @ 有任何残留没被剥干净，消息就会掉进
        #   聊天管道（表现为「点按钮它开始跟我闲聊」）。
        #   改成只发 markdown 内嵌的 <qqbot-cmd-input>：点击只把文字填进输入框，
        #   不产生 @ 消息；用户自己按发送。
        _kb = None
        if _cmd_name(text) == "wdsj":
            _kb = build_wdsj_keyboard(text, uid)
            if _kb:
                ctx.button_tag = _kb_to_cmd_input(_kb)
        reply = await handle_command(
            text,
            user_id=uid,
            group_id=_stable_id(ctx.openid) if is_group else 0,
            sender_name="官方渠道用户",
            is_group=is_group,
            bot_qq=int(APPID) if APPID.isdigit() else 0,
            # ★ 2026-11-08：把原文传下去，插件 bridge 才能从中解析 @ 的 openid
            #   （/~摸头 @某人 要靠它取对方头像）。官方没有 CQ 码，
            #   bridge 里对应加了 <@openid> / <qqbot-at-user id="..."> 的解析。
            raw_message=text,
        )
        if reply:
            body = str(reply)
            # ★ 2026-11-09：优先用 markdown 内嵌 <qqbot-cmd-input>（点击只填输入框、
            #   不带 @）。markdown 被判不可用时退回原键盘按钮 —— 那种会带 @，但至少按钮还在。
            if ctx.button_tag and _MD_STATE.get(ctx.scene, "unknown") != "off":
                body = body + "\n\n" + ctx.button_tag
            elif _kb:
                ctx.keyboard = _kb
            send_text(body)
    except Exception as e:
        log("指令执行异常:", type(e).__name__, e)
        try:
            send_text("这条指令执行出错了，等会再试喵。")
        except Exception:
            pass
    finally:
        _CUR_VAR.set(None)


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
                            log(f"{tag} 收到 group={gid}")
                            # ★ 2026-11-09 诊断：确认「@ 消息」在不同群里到底以哪种
                            #   事件投递、mention 里的 openid 长什么样。
                            #   只在正文含 mention 记号时打印，平时不吵；原文完整不截断。
                            _raw_c = d.get("content", "") or ""
                            if "<@" in _raw_c or "<qqbot-at-user" in _raw_c or "[CQ:at" in _raw_c:
                                log(f"{tag} 原始正文（含 mention） t={t}", repr(_raw_c))
                            await dispatch(_content_with_attachments(d),
                                           ReplyCtx("groups", gid, d.get("id", ""), author),
                                           is_at=t.endswith("AT_MESSAGE_CREATE"))
                        elif t == "C2C_MESSAGE_CREATE":
                            uid = (d.get("author") or {}).get("user_openid", "")
                            log(f"私聊 收到 user={uid}")
                            await dispatch(_content_with_attachments(d),
                                           ReplyCtx("users", uid, d.get("id", ""), uid),
                                           is_at=True)
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
    # ★ 2026-11-09：把 ReplyCtx 的设/复位能力交给队列 worker，
    #   否则 LLM 在 worker 里回复时 _cur() 为空，全部撞 assert。
    try:
        import core.queues as _Q
        _Q.set_chat_ctx_hooks(lambda c: _CUR_VAR.set(c), lambda t: _CUR_VAR.reset(t))
    except Exception as e:
        log("注册队列上下文钩子失败:", type(e).__name__, e)
    try:
        asyncio.run(run_gateway())
    except KeyboardInterrupt:
        log("收到中断，退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
