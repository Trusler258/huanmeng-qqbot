# -*- coding: utf-8 -*-
"""Minecraft 服务器 MOTD 状态卡片（v2.3.77，移植自独立项目）。

数据链路：
  1) 原生 MC Java 协议 Server List Ping（socket，零依赖）——首选，能拿到真实延迟
  2) 失败时回退 api.mcsrvstat.us v3
  3) SRV 跟随（阿里 DoH 查 _minecraft._tcp，很多服只在 SRV 端口监听）

渲染：自包含 HTML（Monocraft base64 内联 + iOS 液态玻璃风）→ Chromium headless 截图 2x → 1x 导出。
"""
from __future__ import annotations

import asyncio
import base64
import html as _html
import json
import mimetypes
import random
import re
import shutil
import socket
import struct
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

from core.logger import get_logger

logger = get_logger("motd")

_ROOT = Path(__file__).resolve().parent.parent
ASSETS = _ROOT / "data" / "motd_assets"
PING_DIR = ASSETS / "ping"
OUT = _ROOT / "data" / "img_temp"
MONOCRAFT = ASSETS / "Monocraft.ttf"

CARD_W, CARD_H = 1200, 762

# Linux 服务器 + Windows 本地都能渲染
BROWSER_CANDIDATES = [
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
    shutil.which("chromium-browser") or "",
    shutil.which("chromium") or "",
    shutil.which("google-chrome") or "",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str | None:
    for p in BROWSER_CANDIDATES:
        if p and os_path_exists(p):
            return p
    return None


def os_path_exists(p: str) -> bool:
    try:
        import os
        return os.path.exists(p)
    except Exception:
        return False


# --------------------------------------------------------------------------
# 1. 原生协议 ping
# --------------------------------------------------------------------------
def _varint(value: int) -> bytes:
    out = b""
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def _read_varint(sock) -> int:
    num = shift = 0
    while True:
        chunk = sock.recv(1)
        if not chunk:
            raise IOError("连接中断")
        byte = chunk[0]
        num |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return num
        shift += 7
        if shift > 35:
            raise IOError("VarInt 过长")


def _pack_str(text: str) -> bytes:
    raw = text.encode("utf-8")
    return _varint(len(raw)) + raw


def _read_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise IOError("连接提前关闭")
        buf += chunk
    return buf


def resolve_srv(host: str) -> tuple[str, int] | None:
    """查 `_minecraft._tcp.<host>` SRV 记录（阿里 DoH，免依赖）。

    很多服务器只在 SRV 指定端口监听，直连 25565 会漏判。
    """
    try:
        query = urllib.parse.urlencode({"name": f"_minecraft._tcp.{host}", "type": "SRV"})
        req = urllib.request.Request(
            f"https://dns.alidns.com/resolve?{query}",
            headers={"User-Agent": "bot-motd-card/1.0"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for ans in data.get("Answer") or []:
            if ans.get("type") == 33:
                parts = str(ans.get("data", "")).split()
                if len(parts) == 4:
                    return parts[3].rstrip("."), int(parts[2])
    except Exception as exc:  # noqa: BLE001
        logger.debug("SRV 查询失败(%s)，按 %s:25565 直连", exc, host)
    return None


def ping_once(host: str, port: int, handshake_host: str | None = None) -> tuple[dict, float, float]:
    """返回 (status_json, 应用层往返 ms, TCP 建连 ms)。

    延迟口径必须从 TCP 建连之后开始计时，否则读数偏高数百 ms。
    handshake_host：跟随 SRV 时实际连代理节点，但握手地址仍填玩家输入的域名。
    """
    addr = handshake_host or host
    payload = (
        _varint(0x00) + _varint(765) + _pack_str(addr)
        + struct.pack(">H", port) + _varint(0x01)
    )
    t_conn = time.perf_counter()
    with socket.create_connection((host, port), timeout=3) as sock:
        sock.settimeout(3)
        connect_ms = (time.perf_counter() - t_conn) * 1000.0
        t0 = time.perf_counter()
        sock.sendall(_varint(len(payload)) + payload)
        sock.sendall(_varint(1) + _varint(0x00))
        _read_varint(sock)
        if _read_varint(sock) != 0x00:
            raise IOError("packet id 异常")
        raw = _read_exact(sock, _read_varint(sock))
        rtt_ms = (time.perf_counter() - t0) * 1000.0
    return json.loads(raw.decode("utf-8")), rtt_ms, connect_ms


def fetch_status(host: str, port: int, port_explicit: bool = False) -> dict:
    """原生 ping 3 次取均值；失败回退 mcsrvstat.us。阻塞，走线程调用。"""
    srv = None if port_explicit else resolve_srv(host)
    node_host, node_port = srv if srv else (host, port)
    logger.info("motd %s: SRV=%s", host, f"{node_host}:{node_port}" if srv else "无")

    samples = []
    data = None
    last_err = None
    for _ in range(3):
        try:
            data, rtt, conn = ping_once(node_host, node_port, handshake_host=host)
            samples.append((rtt, conn))
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(0.15)
    if samples and data is not None:
        data["_latency_ms"] = round(sum(s[0] for s in samples) / len(samples), 1)
        data["_connect_ms"] = round(sum(s[1] for s in samples) / len(samples), 1)
        data["_srv"] = f"{node_host}:{node_port}" if srv else None
        data["_source"] = "原生协议 Server List Ping"
        return data

    logger.warning("motd %s: 原生 ping 失败(%s)，回退 mcsrvstat.us", host, last_err)
    req = urllib.request.Request(
        f"https://api.mcsrvstat.us/3/{host}",
        headers={"User-Agent": "bot-motd-card/1.0"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    data["_latency_ms"] = None
    data["_connect_ms"] = None
    data["_source"] = "api.mcsrvstat.us v3"
    data["description"] = "\n".join((data.get("motd") or {}).get("raw") or [])
    data["favicon"] = data.get("icon")
    return data


# --------------------------------------------------------------------------
# 2. MOTD 解析（§ 码 + JSON 组件两种格式，含颜色名映射）
# --------------------------------------------------------------------------
MC_COLORS = {
    "0": "#000000", "1": "#0000AA", "2": "#00AA00", "3": "#00AAAA",
    "4": "#AA0000", "5": "#AA00AA", "6": "#FFAA00", "7": "#AAAAAA",
    "8": "#555555", "9": "#5555FF", "a": "#55FF55", "b": "#55FFFF",
    "c": "#FF5555", "d": "#FF55FF", "e": "#FFFF55", "f": "#FFFFFF",
}
MC_NAMED_COLORS = {
    "black": "#000000", "dark_blue": "#0000AA", "dark_green": "#00AA00",
    "dark_aqua": "#00AAAA", "dark_red": "#AA0000", "dark_purple": "#AA00AA",
    "gold": "#FFAA00", "gray": "#AAAAAA", "grey": "#AAAAAA",
    "dark_gray": "#555555", "dark_grey": "#555555",
    "blue": "#5555FF", "green": "#55FF55", "aqua": "#55FFFF",
    "red": "#FF5555", "light_purple": "#FF55FF", "yellow": "#FFFF55",
    "white": "#FFFFFF",
}
DEFAULT_STATE = {
    "color": "#FFFFFF", "bold": False, "italic": False,
    "underline": False, "strike": False, "obf": False,
}
_RAND = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz0123456789"


def _state_from_code(code: str, state: dict) -> dict:
    new = dict(state)
    low = code.lower()
    if low in MC_COLORS:
        new = dict(DEFAULT_STATE)
        new["color"] = MC_COLORS[low]
    elif low == "l":
        new["bold"] = True
    elif low == "o":
        new["italic"] = True
    elif low == "n":
        new["underline"] = True
    elif low == "m":
        new["strike"] = True
    elif low == "k":
        new["obf"] = True
    elif low == "r":
        new = dict(DEFAULT_STATE)
    return new


def parse_legacy(text: str, state: dict | None = None) -> list[dict]:
    state = dict(state or DEFAULT_STATE)
    segments: list[dict] = []
    buf = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\u00a7" and i + 1 < len(text):
            if buf:
                segments.append({"text": buf, **state})
                buf = ""
            state = _state_from_code(text[i + 1], state)
            i += 2
            continue
        buf += ch
        i += 1
    if buf:
        segments.append({"text": buf, **state})
    return segments


def flatten_component(node, inherited: dict | None = None) -> list[dict]:
    """递归展开 JSON 组件格式（{text, color, bold, extra:[...]}）"""
    inherited = dict(inherited or DEFAULT_STATE)
    if isinstance(node, str):
        return parse_legacy(node, inherited)
    if isinstance(node, list):
        out: list[dict] = []
        for item in node:
            out += flatten_component(item, inherited)
        return out
    if not isinstance(node, dict):
        return []

    state = dict(inherited)
    color = node.get("color")
    if isinstance(color, str):
        key = color.lower()
        if color.startswith("#") and len(color) == 7:
            state["color"] = color
        elif key in MC_NAMED_COLORS:
            state["color"] = MC_NAMED_COLORS[key]
        elif key in MC_COLORS:
            state["color"] = MC_COLORS[key]
        elif key in ("reset", "none"):
            state = dict(DEFAULT_STATE)
    for key in ("bold", "italic", "underlined", "strikethrough", "obfuscated"):
        if isinstance(node.get(key), bool):
            state[{"underlined": "underline", "strikethrough": "strike",
                   "obfuscated": "obf"}.get(key, key)] = node[key]

    out = parse_legacy(str(node.get("text", "")), state)
    for extra in (node.get("extra") or []):
        out += flatten_component(extra, state)
    return out


def motd_html_lines(description) -> list[str]:
    """MOTD → 每行 HTML（分段 span）。空行保留占位。"""
    if description is None:
        return []

    def render(segment: dict) -> str:
        text = segment["text"]
        if segment.get("obf"):
            # §k 在游戏里是逐帧随机的乱码；静态卡片用块字符表达，随机 ASCII 会像漏打的字母
            text = "".join("▓" if c != " " else " " for c in text)
        style = [f"color:{segment['color']}"]
        if segment.get("bold"):
            style.append("font-weight:700")
        if segment.get("italic"):
            style.append("font-style:italic")
        deco = [d for d, k in (("underline", "underline"), ("line-through", "strike")) if segment.get(k)]
        if deco:
            style.append("text-decoration:" + " ".join(deco))
        if segment.get("obf"):
            style.append("opacity:.85")

        return f'<span style="{";".join(style)}">{_html.escape(text)}</span>'

    segs = flatten_component(description) if not isinstance(description, str) else parse_legacy(description)
    lines: list[list[dict]] = [[]]
    for seg in segs:
        parts = seg["text"].split("\n")
        for idx, part in enumerate(parts):
            if idx:
                lines.append([])
            if part:
                piece = dict(seg)
                piece["text"] = part
                lines[-1].append(piece)
    # 逐行剥首尾空白：老服靠塞空格手工居中，先剥再 CSS 居中最稳
    for line in lines:
        if line and not line[0]["text"].strip():
            line[0] = dict(line[0], text=line[0]["text"].lstrip())
        if line and not line[-1]["text"].strip():
            line[-1] = dict(line[-1], text=line[-1]["text"].rstrip())
    lines = [[s for s in line if s["text"]] for line in lines]
    return ["".join(render(s) for s in line) for line in lines]


# --------------------------------------------------------------------------
# 3. 资源 + HTML
# --------------------------------------------------------------------------
def b64_of(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def favicon_data_uri(status: dict, host: str) -> str:
    icon = status.get("favicon") or status.get("icon")
    if isinstance(icon, str) and icon.startswith("data:image"):
        return icon
    try:
        url = f"https://api.mcsrvstat.us/icon/{host}"
        req = urllib.request.Request(url, headers={"User-Agent": "bot-motd-card/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
        return "data:image/png;base64," + base64.b64encode(raw).decode()
    except Exception as exc:  # noqa: BLE001
        logger.warning("motd favicon 获取失败: %s", exc)
        return ""




# ── 背景光晕跟随 MOTD 颜色（v2.3.78）──────────────────────────
def _hex_to_rgba_str(hex_color: str, alpha: float) -> str:
    h = (hex_color or "").lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    except Exception:
        return f"rgba(76,255,136,{alpha})"


def motd_anchor_colors(description, card_w: int = CARD_W) -> list[tuple]:
    """从 MOTD 段估算颜色质心（px）→ [(hex, cx, cy, weight)]。

    哪个颜色的字多，光晕就打在它所在位置的底下。
    motd 盒几何与 HTML 布局一致：左 186 / 右 1134，首行 y≈214，行高 32，平均字宽估 14px。
    """
    if description is None:
        return []
    segs = flatten_component(description) if not isinstance(description, str) else parse_legacy(description)
    lines: list[list[dict]] = [[]]
    for seg in segs:
        parts = seg["text"].split("\n")
        for idx, part in enumerate(parts):
            if idx:
                lines.append([])
            if part.strip():
                piece = dict(seg)
                piece["text"] = part
                lines[-1].append(piece)
    box_l, box_r = 186, card_w - 66
    box_t, line_h, char_w = 214, 32, 14.0
    agg: dict[str, list] = {}
    for li, line in enumerate(lines[:2]):
        total = sum(len(s["text"]) for s in line)
        if not total:
            continue
        x0 = (box_l + box_r) / 2 - total * char_w / 2
        y = box_t + li * line_h + line_h / 2
        cum = 0
        for s in line:
            w = len(s["text"]) * char_w
            c = str(s.get("color") or "#FFFFFF").upper()
            n = len(s["text"].strip())
            cum += w
            if not n or c == "#000000":
                continue
            e = agg.setdefault(c, [0, 0.0, 0.0])
            e[0] += n
            e[1] += (x0 + cum - w / 2) * n
            e[2] += y * n
    return [(c, int(sx / wt), int(sy / wt), wt)
            for c, (wt, sx, sy) in sorted(agg.items(), key=lambda kv: -kv[1][0])][:5]


def build_bg_css(anchors: list[tuple]) -> str:
    """背景光晕打在 MOTD 颜色的同位置底下；无锚点时回退默认角落配色。"""
    sizes = [(660, 500, 0.78), (560, 460, 0.60), (520, 430, 0.50), (460, 400, 0.45), (420, 380, 0.38)]
    if anchors:
        grads = [
            f"radial-gradient({rw}px {rh}px at {cx}px {cy}px, {_hex_to_rgba_str(c, a)}, transparent 62%)"
            for (c, cx, cy, _w), (rw, rh, a) in zip(anchors, sizes)
        ]
    else:
        default_palette = ["#4CFF88", "#00C6FF", "#A868FF", "#FF9642", "#FF5CBE"]
        default_spots = ["86% 2%", "2% 88%", "42% 42%", "99% 94%", "18% 8%"]
        grads = [
            f"radial-gradient({rw}px {rh}px at {pos}, {c}, transparent 62%)"
            for (rw, rh, _a), pos, c in zip(sizes, default_spots, default_palette)
        ]
    grads.append("linear-gradient(160deg,#08150f 0%,#080d16 46%,#06070c 100%)")
    return " ,\n    ".join(grads)


def latency_label(ms):
    if not isinstance(ms, (int, float)):
        return "未知", "#AAAAAA", "unknown"
    if ms < 150:
        return "极佳", "#55FF55", "5"
    if ms < 300:
        return "良好", "#55FF55", "4"
    if ms < 600:
        return "一般", "#FFFF55", "3"
    if ms < 1000:
        return "较差", "#FFAA00", "2"
    return "很差", "#FF5555", "1"


def ping_icon(tier: str) -> str:
    path = PING_DIR / f"ping_{tier}.png"
    if path.exists():
        return b64_of(path)
    return ""


def display_name(host: str) -> str:
    labels = host.split(".")
    if len(labels) > 2 and labels[0].lower() in {"mc", "play", "srv", "game", "join", "cn"}:
        labels = labels[1:]
    return ".".join(labels).upper()


def scale_class(text: str) -> str:
    n = len(text)
    if n > 20:
        return " xs"
    if n > 15:
        return " sm"
    return ""


def build_html(status: dict, host: str, port: int, measure: bool = False) -> str:
    version = status.get("version")
    version_name = version.get("name") if isinstance(version, dict) else version
    protocol = version.get("protocol") if isinstance(version, dict) else status.get("protocol")
    players = status.get("players") or {}
    online = int(players.get("online") or 0)
    maxp = int(players.get("max") or 0)
    pct = (online / maxp * 100.0) if maxp else 0.0
    ms = status.get("_latency_ms")
    ms_txt = f"{ms:.0f}" if isinstance(ms, (int, float)) else "—"
    is_online = bool(status.get("online", True))
    if is_online:
        lat_word, lat_color, lat_tier = latency_label(ms)
    else:
        lat_word, lat_color, lat_tier = "离线", "#FF5555", "unknown"
    ping_uri = ping_icon(lat_tier)
    state_txt = "ONLINE 在线" if is_online else "OFFLINE 离线"
    state_color = "#55FF55" if is_online else "#FF5555"

    lines = motd_html_lines(status.get("description"))
    motd_html = "".join(
        f'<div class="motd-line">{line or "&nbsp;"}</div>' for line in lines
    )
    icon = favicon_data_uri(status, host)
    icon_html = (
        f'<img class="favicon" src="{icon}" alt="icon">' if icon
        else '<div class="favicon favicon-fallback"></div>'
    )
    font_b64 = b64_of(MONOCRAFT) if MONOCRAFT.exists() else ""
    font_css = (
        '@font-face{font-family:"Monocraft";src:url(' + font_b64
        + ') format("truetype");font-weight:400 700;font-display:block;}'
    ) if font_b64 else ""

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    _vparts = [p.strip() for p in (version_name or "").replace("Requires MC", "").split("/") if p.strip()]
    version_short = " – ".join(_vparts) if len(_vparts) > 1 else (_vparts[0] if _vparts else "—")

    ping_img = f'<img class="pingbars" src="{ping_uri}" alt="ping {lat_tier}">' if ping_uri else ""
    ping_img_small = f'<img class="tile-ping" src="{ping_uri}" alt="ping {lat_tier}">' if ping_uri else ""

    tiles = [
        ("服务器版本", version_short, version_short, "VERSION"),
        ("在线玩家", f'{online:,} <em>/ {maxp:,}</em>', f"{online:,} / {maxp:,}", "PLAYERS"),
        ("网络延迟", f'{ping_img_small}{ms_txt} <em>ms</em>', f"{ms_txt} ms", "PING"),
        ("协议版本", _html.escape(str(protocol or "—")), str(protocol or "—"), "PROTOCOL"),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="tile-k">{k}</div>'
        f'<div class="tile-v{scale_class(plain)}">{v}</div>'
        f'<div class="tile-tag">{t}</div></div>'
        for k, v, plain, t in tiles
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
{font_css}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{CARD_W}px;{"height:auto" if measure else f"height:{CARD_H}px"};overflow:hidden;}}
{"html,body{height:auto!important;overflow:visible!important}.stage{position:static!important}" if measure else ""}
body{{
  font-family:"Monocraft","Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;
  background:#0b0d10;color:#e8edf2;position:relative;
}}
.bg{{position:absolute;inset:0;background:#0b0d10;}}

.noise{{position:absolute;inset:0;opacity:.16;mix-blend-mode:overlay;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3'/></filter><rect width='160' height='160' filter='url(%23n)' opacity='.55'/></svg>");}}
.vign{{position:absolute;inset:0;box-shadow:inset 0 0 210px rgba(0,0,0,.62);}}
.stage{{position:absolute;inset:0;padding:28px 34px 22px;display:flex;flex-direction:column;gap:16px;}}
.panel{{position:relative;border-radius:34px;overflow:hidden;
  background:linear-gradient(180deg,rgba(16,22,28,.44),rgba(8,11,15,.34));
  backdrop-filter:blur(34px) saturate(190%);
  -webkit-backdrop-filter:blur(34px) saturate(190%);
  border:1px solid rgba(255,255,255,.26);
  box-shadow:
    inset 0 1.5px 0 rgba(255,255,255,.66),
    inset 0 -1px 0 rgba(255,255,255,.14),
    inset 0 0 50px rgba(255,255,255,.06),
    0 24px 52px rgba(0,0,0,.46),
    0 2px 8px rgba(0,0,0,.32);
}}
.panel::before{{content:"";position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(115deg,
    rgba(255,255,255,.32) 0%, rgba(255,255,255,.09) 24%,
    rgba(255,255,255,0) 50%, rgba(255,255,255,.07) 100%);}}
.panel > *{{position:relative;z-index:1}}
.top{{display:flex;align-items:center;gap:12px;padding:0 6px;}}
.brand{{font-size:15px;letter-spacing:.16em;color:#bff5c8;
  text-shadow:0 1px 10px rgba(85,255,140,.5),0 1px 2px rgba(0,0,0,.6);}}
.brand i{{font-style:normal;color:rgba(255,255,255,.42)}}
.spacer{{flex:1}}
.upd{{font-size:12px;color:rgba(255,255,255,.46);text-shadow:0 1px 2px rgba(0,0,0,.55)}}
.pingbars{{width:50px;height:40px;image-rendering:pixelated;display:block;
  filter:drop-shadow(0 2px 10px rgba(85,255,85,.30))}}
.tile-ping{{width:30px;height:24px;image-rendering:pixelated;
  vertical-align:-3px;margin-right:7px}}
.hero{{display:flex;gap:24px;padding:24px;align-items:center}}
.namerow{{display:flex;align-items:center;gap:18px}}
.hero-side{{display:flex;align-items:center;gap:12px;margin-left:auto;flex:0 0 auto;
  padding:6px 16px;border-radius:999px;
  background:linear-gradient(180deg,rgba(255,255,255,.18),rgba(255,255,255,.06));
  backdrop-filter:blur(18px) saturate(170%);
  -webkit-backdrop-filter:blur(18px) saturate(170%);
  border:1px solid rgba(255,255,255,.26);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.55),0 6px 18px rgba(0,0,0,.32);}}
.online-txt{{font-size:15px;letter-spacing:.1em;white-space:nowrap;
  text-shadow:0 1px 6px rgba(0,0,0,.5)}}
.slot{{width:128px;height:128px;flex:0 0 128px;display:grid;place-items:center;
  border-radius:28px;
  background:linear-gradient(180deg,rgba(255,255,255,.14),rgba(255,255,255,.04));
  border:1px solid rgba(255,255,255,.24);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.5),inset 0 -14px 30px rgba(0,0,0,.28),
             0 10px 26px rgba(0,0,0,.34);}}
.favicon{{width:104px;height:104px;image-rendering:pixelated;border-radius:20px;
  filter:drop-shadow(0 4px 12px rgba(0,0,0,.45));}}
.favicon-fallback{{background:repeating-conic-gradient(#3a6b3a 0 25%,#2c5230 0 50%) 0 0/16px 16px;}}
.hero-main{{flex:1;min-width:0}}
.srv-name{{font-size:38px;font-weight:700;letter-spacing:.01em;color:#fff;
  line-height:1.12;text-shadow:0 2px 14px rgba(0,0,0,.55),0 1px 2px rgba(0,0,0,.7)}}
.srv-addr{{margin-top:6px;font-size:17px;color:rgba(255,255,255,.62);
  letter-spacing:.06em;text-shadow:0 1px 3px rgba(0,0,0,.6)}}
.motd{{position:relative;margin-top:16px;border-radius:22px;padding:15px 18px;min-height:104px;
  display:flex;flex-direction:column;justify-content:center;gap:6px;
  background:linear-gradient(180deg,rgba(3,6,10,.62),rgba(3,6,10,.48));
  backdrop-filter:blur(14px) saturate(140%);
  -webkit-backdrop-filter:blur(14px) saturate(140%);
  border:1px solid rgba(255,255,255,.16);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.22),inset 0 -18px 34px rgba(0,0,0,.30);}}
.motd-glow{{position:absolute;inset:15px 18px;display:flex;flex-direction:column;
  justify-content:center;gap:6px;filter:blur(9px);opacity:.9;pointer-events:none;}}
.motd-glow .motd-line{{font-size:22px;line-height:1.45;white-space:pre;text-align:center;}}
.motd-line{{font-size:22px;line-height:1.45;white-space:pre;overflow:hidden;
  text-align:center;text-shadow:0 2px 7px rgba(0,0,0,.85);position:relative;}}
.barhead{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px}}
.barhead .l{{font-size:13px;color:rgba(255,255,255,.68);letter-spacing:.14em;
  text-shadow:0 1px 3px rgba(0,0,0,.6)}}
.barhead .r{{font-size:28px;font-weight:700;color:#8dff92;
  text-shadow:0 2px 12px rgba(85,255,85,.5),0 1px 3px rgba(0,0,0,.7)}}
.barhead .r em{{font-style:normal;font-size:17px;color:rgba(255,255,255,.5)}}
.seg{{display:flex;gap:4px;height:28px;padding:4px;border-radius:999px;
  background:rgba(0,0,0,.30);border:1px solid rgba(255,255,255,.13);
  box-shadow:inset 0 2px 6px rgba(0,0,0,.5)}}
.seg i{{flex:1;border-radius:999px;background:rgba(255,255,255,.10);}}
.seg i.on{{background:linear-gradient(180deg,#9dff9d,#31c93c);
  box-shadow:0 0 12px rgba(85,255,85,.55),inset 0 1px 0 rgba(255,255,255,.65)}}
.seg.off i.on{{background:linear-gradient(180deg,#8b95a1,#4b5563);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.3)}}
.barbox{{padding:20px 24px}}
.pct{{font-size:12px;color:rgba(255,255,255,.52);margin-top:11px;letter-spacing:.1em;
  text-shadow:0 1px 3px rgba(0,0,0,.6)}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr)}}
.tile{{padding:17px 20px}}
.tile + .tile{{border-left:1px solid rgba(255,255,255,.14)}}
.tile-k{{font-size:12px;color:rgba(255,255,255,.66);letter-spacing:.1em;
  text-shadow:0 1px 3px rgba(0,0,0,.6)}}
.tile-v{{margin-top:9px;font-size:24px;font-weight:700;color:#fff;letter-spacing:.01em;
  white-space:nowrap;text-shadow:0 1px 8px rgba(0,0,0,.5)}}
.tile-v em{{font-style:normal;font-size:14px;color:rgba(255,255,255,.5)}}
.tile-v.sm{{font-size:19px}}
.tile-v.xs{{font-size:15px;letter-spacing:0}}
.tile-v.sm em,.tile-v.xs em{{font-size:13px}}
.tile-tag{{margin-top:7px;font-size:10px;color:rgba(255,255,255,.34);letter-spacing:.24em}}
.foot{{margin-top:auto;display:flex;align-items:center;gap:12px;font-size:11.5px;
  color:rgba(255,255,255,.56);padding:0 6px;text-shadow:0 1px 3px rgba(0,0,0,.6)}}
.foot .dot{{width:4px;height:4px;border-radius:50%;background:rgba(255,255,255,.34)}}
.pill{{padding:5px 14px;border-radius:999px;font-size:11px;color:rgba(255,255,255,.82);
  background:linear-gradient(180deg,rgba(255,255,255,.20),rgba(255,255,255,.07));
  backdrop-filter:blur(16px) saturate(160%);
  -webkit-backdrop-filter:blur(16px) saturate(160%);
  border:1px solid rgba(255,255,255,.26);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.55),0 4px 14px rgba(0,0,0,.28);}}
</style></head>
<body>
<div class="bg"></div><div class="vign"></div>
<div class="stage">
  <div class="top">
    <div class="brand">MINECRAFT <i>SERVER STATUS</i></div>
    <div class="spacer"></div>
    <div class="upd">{stamp}</div>
  </div>
  <div class="panel hero">
    <div class="slot">{icon_html}</div>
    <div class="hero-main">
      <div class="namerow">
        <div class="srv-name">{_html.escape(display_name(host))}</div>
        <div class="hero-side">
          <div class="online-txt" style="color:{state_color}">{state_txt}</div>
          {ping_img}
        </div>
      </div>
      <div class="srv-addr">{_html.escape(host)}:{port}</div>
      <div class=motd><div class=motd-glow aria-hidden=true>{motd_html}</div>{motd_html}</div>
    </div>
  </div>
  <div class="panel barbox">
    <div class="barhead">
      <div class="l">在线玩家 ONLINE PLAYERS</div>
      <div class="r">{online:,} <em>/ {maxp:,}</em></div>
    </div>
    <div class="seg{'' if is_online else ' off'}" id="seg"></div>
    <div class="pct">占用率 {pct:.2f}% · 延迟状态 <span style="color:{lat_color}">{lat_word}</span></div>
  </div>
  <div class="panel tiles">{tiles_html}</div>
  <div class="foot">
    <span class="pill">{"SRV" if status.get("_srv") else "直连"}</span>
    <span class="dot"></span>
    <span>{_html.escape(host)}:{port}</span>
    <span class="dot"></span>
    <span style="color:{lat_color}">PING {ms_txt} ms</span>
  </div>
</div>
<script>
  var pct = {pct:.2f}, total = {online};
  var seg = document.getElementById('seg'), n = 40;
  var on = total > 0 ? Math.max(1, Math.round(n * pct / 100)) : 0;
  for (var i = 0; i < n; i++) {{
    var e = document.createElement('i');
    if (i < on) e.className = 'on';
    seg.appendChild(e);
  }}
  document.title = 'ready';
  try {{ document.title = 'H' + Math.ceil(document.body.getBoundingClientRect().height); }} catch (e) {{}}
</script>
</body></html>"""


# --------------------------------------------------------------------------
# 4. 渲染（阻塞，走线程）
# --------------------------------------------------------------------------
def _find_browser() -> str | None:
    for p in BROWSER_CANDIDATES:
        if p and os_path_exists(p):
            return p
    return None


def _measure_height(html_path: Path) -> int | None:
    exe = _find_browser()
    if not exe:
        return None
    url = "file:///" + str(html_path).replace(chr(92), "/")
    try:
        import subprocess
        proc = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
             "--dump-dom", url],
            capture_output=True, timeout=35)
        dom = proc.stdout.decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        logger.warning("motd 高度测量失败: %s", exc)
        return None
    m = re.search(r"<title>H(\d+)</title>", dom)
    return int(m.group(1)) if m else None


def _render(html_path: Path, png_path: Path, w: int, h: int, scale: int = 2) -> bool:
    exe = _find_browser()
    if not exe:
        logger.warning("motd 未找到 Chromium/Edge，跳过渲染")
        return False
    url = "file:///" + str(html_path).replace(chr(92), "/")
    import subprocess
    headless_tiers = [["--headless=new"], ["--headless"]]
    for headless in headless_tiers:
        base = [exe] + headless + ["--disable-gpu", "--hide-scrollbars",
                "--no-first-run", "--disable-extensions", "--no-sandbox",
                f"--force-device-scale-factor={scale}",
                f"--window-size={w},{h}",
                f"--screenshot={png_path}", url]
        try:
            proc = subprocess.run(base, capture_output=True, timeout=35)
            if png_path.exists() and png_path.stat().st_size > 1000:
                return True
            logger.warning("motd 渲染无输出: %s", proc.stderr.decode("utf-8", "ignore")[-300:])
        except Exception as exc:  # noqa: BLE001
            logger.warning("motd 渲染异常: %s", exc)
    return False


# --------------------------------------------------------------------------
# 5. 异步入口
# --------------------------------------------------------------------------
async def make_card(address: str, port_arg: int | None = None) -> tuple[Path | None, dict | None, str]:
    """查询 + 渲染，返回 (1x png 路径, status, 错误详情)；失败 png/status 为 None。"""
    address = (address or "").strip()
    port_explicit = False
    host, port = address, 25565
    if ":" in address:
        host, _, p = address.rpartition(":")
        if p.isdigit():
            port = int(p)
            port_explicit = True
        else:
            host = address
    if port_arg:
        port = port_arg
        port_explicit = True
    if not host:
        return None, None, "地址为空"

    def _sync() -> tuple[Path | None, dict | None, str]:
        OUT.mkdir(parents=True, exist_ok=True)
        try:
            status = fetch_status(host, port, port_explicit=port_explicit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("motd %s 查询失败: %s", host, exc)
            return None, None, traceback.format_exc()

        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{host}_{port}")
        # 先用 measure 模式量真实内容高度，再按实测高度重建（不靠猜）
        measure_path = OUT / f"motd_card_{slug}_measure.html"
        measure_path.write_text(build_html(status, host, port, measure=True), encoding="utf-8")
        measured = _measure_height(measure_path)
        measure_path.unlink(missing_ok=True)
        card_h = measured or CARD_H
        html_path = OUT / f"motd_card_{slug}.html"
        html_path.write_text(_build_html_with_h(status, host, port, card_h), encoding="utf-8")

        png2x = OUT / f"mc_card_{slug}.png"
        if not _render(html_path, png2x, CARD_W, card_h):
            return None, None, "渲染失败（未找到浏览器或 Chromium 输出为空）"
        png1x = OUT / f"mc_card_{slug}_1x.png"
        try:
            from PIL import Image
            with Image.open(png2x) as im:
                w, h = im.size
                im.resize((w // 2, h // 2), Image.LANCZOS).save(png1x, optimize=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("motd 1x 导出失败: %s", exc)
            return png2x, status, ""
        return png1x, status, ""

    return await asyncio.to_thread(_sync)


def _build_html_with_h(status: dict, host: str, port: int, h: int) -> str:
    """build_html 但画布高度用实测值（模块级替换 CARD_H）。"""
    global CARD_H
    old = CARD_H
    CARD_H = h
    try:
        return build_html(status, host, port)
    finally:
        CARD_H = old
