# -*- coding: utf-8 -*-
"""Minecraft 服务器 MOTD 状态卡片（v2.3.78，PIL 渲染版）。

数据链路：
  1) 原生 MC Java 协议 Server List Ping（socket，零依赖）——首选，能拿到真实延迟
  2) 失败时回退 api.mcsrvstat.us v3
  3) SRV 跟随（阿里 DoH 查 _minecraft._tcp，很多服只在 SRV 端口监听）

渲染：Pillow 直绘液态玻璃卡（色球高斯模糊模拟 backdrop-filter、圆角玻璃面板、
Monocraft/CJK 混排、原版 ping 信号条）。渲染 ~1-2s，无浏览器依赖。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import random
import re
import socket
import struct
import time
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

CARD_W = 1200

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
    """查 `_minecraft._tcp.<host>` SRV 记录（阿里 DoH，免依赖）。"""
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
    """返回 (status_json, 应用层往返 ms, TCP 建连 ms)。延迟口径：TCP 建连之后。"""
    addr = handshake_host or host
    payload = (
        _varint(0x00) + _varint(765) + _pack_str(addr)
        + struct.pack(">H", port) + _varint(0x01)
    )
    t_conn = time.perf_counter()
    with socket.create_connection((host, port), timeout=2) as sock:
        sock.settimeout(2)
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


def motd_segments(description) -> list[list[dict]]:
    """MOTD → [[{text,color,bold,...}]] 按行分组（空行保留）。"""
    if description is None:
        return []
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
    for line in lines:
        if line and not line[0]["text"].strip():
            line[0] = dict(line[0], text=line[0]["text"].lstrip())
        if line and not line[-1]["text"].strip():
            line[-1] = dict(line[-1], text=line[-1]["text"].rstrip())
    return [[s for s in line if s["text"]] for line in lines]


def motd_color_weights(description) -> list[str]:
    """MOTD 用到的颜色，按文本长度加权排序（去重，过滤纯黑）。"""
    if description is None:
        return []
    weight: dict[str, int] = {}
    for line in motd_segments(description):
        for seg in line:
            c = str(seg.get("color") or "#FFFFFF").upper()
            n = len(seg.get("text", "").strip())
            if n:
                weight[c] = weight.get(c, 0) + n
    return [c for c, _ in sorted(weight.items(), key=lambda kv: -kv[1]) if c != "#000000"]


def _hex_rgb(hex_color: str) -> tuple[int, int, int]:
    h = (hex_color or "").lstrip("#")
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        return 255, 255, 255


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


def favicon_image(status: dict, host: str):
    """服务器图标 → RGBA Image（失败返回 None）。"""
    from PIL import Image
    icon = status.get("favicon") or status.get("icon")
    if isinstance(icon, str) and icon.startswith("data:image"):
        try:
            payload = icon.split(",", 1)[-1]
            return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGBA")
        except Exception:
            return None
    try:
        url = f"https://api.mcsrvstat.us/icon/{host}"
        req = urllib.request.Request(url, headers={"User-Agent": "bot-motd-card/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        logger.debug("motd favicon 获取失败: %s", exc)
        return None


# --------------------------------------------------------------------------
# 3. PIL 渲染
# --------------------------------------------------------------------------
def _load_font(size: int, bold: bool = False, mono: bool = False):
    from PIL import ImageFont
    if mono:
        try:
            return ImageFont.truetype(str(MONOCRAFT), size)
        except Exception:
            pass
    paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


_FONTS: dict[tuple, object] = {}


def _font(size: int, bold: bool = False, mono: bool = False):
    key = (size, bold, mono)
    if key not in _FONTS:
        _FONTS[key] = _load_font(size, bold, mono)
    return _FONTS[key]


def _text_w(draw, text: str, font) -> int:
    try:
        return int(draw.textlength(text, font=font))
    except Exception:
        try:
            return int(font.getlength(text))
        except Exception:
            return len(text) * int(getattr(font, "size", 12))


def _has_glyph(ch: str) -> bool:
    """Monocraft 只覆盖 ASCII 可打印区，非 ASCII 走 CJK 字体。"""
    return 0x20 <= ord(ch) <= 0x7E


def _split_runs(text: str) -> list[tuple[str, bool]]:
    runs: list[tuple[str, bool]] = []
    run, cur = "", None
    for ch in text:
        m = _has_glyph(ch)
        if cur is not None and m != cur:
            runs.append((run, cur))
            run = ""
        run += ch
        cur = m
    if run:
        runs.append((run, cur))
    return runs


def _draw_mixed(draw, x: int, y: int, text: str, size: int, fill, bold: bool = False,
                shadow: bool = True) -> int:
    """Monocraft 画 ASCII、CJK 字体画非 ASCII（同 run 拆分混排）。返回结束 x。"""
    f_mono = _font(size, bold, mono=True)
    f_cjk = _font(size, bold)
    cx = x
    for seg_text, is_mono in _split_runs(text):
        f = f_mono if is_mono else f_cjk
        try:
            w = int(draw.textlength(seg_text, font=f))
        except Exception:
            w = len(seg_text) * size
        if shadow:
            draw.text((cx + 1, y + 2), seg_text, font=f, fill=(8, 10, 14))
        draw.text((cx, y), seg_text, font=f, fill=fill)
        cx += w
    return cx


def _mixed_w(draw, text: str, size: int) -> int:
    total = 0
    for seg_text, is_mono in _split_runs(text):
        f = _font(size, mono=is_mono)
        try:
            total += int(draw.textlength(seg_text, font=f))
        except Exception:
            total += len(seg_text) * size
    return total


def _round_panel(base, box: tuple, radius: int, fill_alpha: int = 110, border_alpha: int = 66):
    """液态玻璃面板：抠出面板区域高斯模糊（模拟 backdrop-filter），
    再叠半透明深色 + 白边 + 顶部镜面高光。原地修改 base。"""
    from PIL import Image, ImageDraw, ImageFilter
    x0, y0, x1, y1 = box
    region = base.crop(box).filter(ImageFilter.GaussianBlur(14))
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), radius=radius, fill=255)
    base.paste(region, (x0, y0), mask)

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(box, radius=radius, fill=(13, 17, 22, fill_alpha))
    od.rounded_rectangle(box, radius=radius, outline=(255, 255, 255, border_alpha), width=1)
    od.line((x0 + radius, y0 + 2, x1 - radius, y0 + 2), fill=(255, 255, 255, 150), width=1)
    base.alpha_composite(overlay)


def _draw_orbs(w: int, h: int, colors: list[str]):
    """暗底 + 鲜艳色球（高斯模糊）+ 暗角。colors 为空时用默认配色。"""
    from PIL import Image, ImageDraw, ImageFilter
    spots = [
        (660, 500, int(w * 0.86), int(h * 0.02), 200),
        (620, 520, int(w * 0.02), int(h * 0.88), 168),
        (560, 460, int(w * 0.42), int(h * 0.42), 128),
        (520, 430, int(w * 0.99), int(h * 0.94), 133),
        (420, 380, int(w * 0.18), int(h * 0.08), 87),
    ]
    default_palette = ["#4CFF88", "#00C6FF", "#A868FF", "#FF9642", "#FF5CBE"]
    pool = (colors + default_palette)[: len(spots)] if colors else default_palette
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for (rw, rh, cx, cy, alpha), c in zip(spots, pool):
        r, g, b = _hex_rgb(c)
        ld.ellipse((cx - rw // 2, cy - rh // 2, cx + rw // 2, cy + rh // 2),
                   fill=(r, g, b, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(110))
    base = Image.new("RGBA", (w, h), (11, 13, 16, 255))
    base.alpha_composite(layer)
    return base


def _seg_bar(draw, x: int, y: int, w: int, seg_h: int, pct: float, online: bool) -> int:
    """40 段玩家占用条（在线>0 至少 1 格，离线全灰）。返回结束 y。"""
    n = 40
    gap = 4
    seg_w = (w - (n - 1) * gap) // n
    on = max(1, round(n * pct / 100)) if (online and pct > 0) else 0
    for i in range(n):
        sx = x + i * (seg_w + gap)
        draw.rounded_rectangle((sx, y, sx + seg_w, y + seg_h), radius=max(2, seg_h // 2 - 1),
                               fill=(0, 0, 0, 80), outline=(255, 255, 255, 33), width=1)
        if i < on:
            top = (157, 255, 157) if online else (139, 149, 161)
            bot = (49, 201, 60) if online else (75, 85, 99)
            for yy in range(seg_h):
                t = yy / max(1, seg_h - 1)
                c = tuple(int(top[k] + (bot[k] - top[k]) * t) for k in range(3)) + (255,)
                draw.line((sx + 1, y + yy, sx + seg_w - 1, y + yy), fill=c)
    return y + seg_h


def _pill(draw, x: int, y: int, text: str, size: int = 11) -> int:
    """胶囊标签，返回结束 x。"""
    f = _font(size)
    tw = _text_w(draw, text, f)
    pad_x, pad_y = 14, 5
    draw.rounded_rectangle((x, y, x + tw + pad_x * 2, y + size + pad_y * 2),
                           radius=(size + pad_y * 2) // 2,
                           fill=(255, 255, 255, 38), outline=(255, 255, 255, 66), width=1)
    draw.text((x + pad_x, y + pad_y), text, font=f, fill=(235, 240, 246))
    return x + tw + pad_x * 2


def render_card_png(status: dict, host: str, port: int, out_png: Path) -> Path:
    """PIL 直绘状态卡。纯 CPU ~1-2s，走线程调用。"""
    from PIL import Image, ImageDraw

    version = status.get("version")
    version_name = version.get("name") if isinstance(version, dict) else version
    protocol = str(version.get("protocol") if isinstance(version, dict) else (status.get("protocol") or "—"))
    players = status.get("players") or {}
    online_n = int(players.get("online") or 0)
    maxp = int(players.get("max") or 0)
    pct = (online_n / maxp * 100.0) if maxp else 0.0
    ms = status.get("_latency_ms")
    ms_txt = f"{ms:.0f}" if isinstance(ms, (int, float)) else "—"
    is_online = bool(status.get("online", True))
    lat_word, lat_hex, lat_tier = latency_label(ms) if is_online else ("离线", "#FF5555", "unknown")
    lat_rgb = _hex_rgb(lat_hex)
    state_txt = "ONLINE 在线" if is_online else "OFFLINE 离线"
    state_rgb = (85, 255, 85) if is_online else (255, 85, 85)

    W = CARD_W
    M, GAP = 34, 16
    inner_w = W - M * 2

    lines = [l for l in motd_segments(status.get("description"))[:2]] or [[]]
    motd_fs = 22
    motd_line_h = int(motd_fs * 1.45)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    # ---- 布局计算（先算总高再画）----
    hero_pad = 24
    icon_sz = 104
    name_fs, addr_fs = 38, 17
    name_h = int(name_fs * 1.2)
    motd_lines_n = max(1, len(lines))
    motd_box_h = max(104, motd_lines_n * motd_line_h + 30)
    hero_main_h = int(name_fs * 1.2) + 6 + int(addr_fs * 1.5) + 16 + motd_box_h
    hero_h = max(icon_sz, hero_main_h) + hero_pad * 2
    hero_y = 28 + 22 + GAP
    hero_box = (M, hero_y, W - M, hero_y + hero_h)

    bar_y = hero_y + hero_h + GAP
    bar_box = (M, bar_y, W - M, bar_y + 150)

    tiles_y = bar_y + 150 + GAP
    tiles_h = 120
    tiles_box = (M, tiles_y, W - M, tiles_y + tiles_h)

    foot_y = tiles_y + tiles_h + GAP + 6
    H = foot_y + 30 + 20

    # ---- 底图：色球（跟随 MOTD 颜色）----
    img = _draw_orbs(W, H, motd_color_weights(status.get("description")))

    # ---- 玻璃面板 ----
    _round_panel(img, hero_box, radius=34)
    _round_panel(img, bar_box, radius=30)
    _round_panel(img, tiles_box, radius=30)

    draw = ImageDraw.Draw(img)

    # ---- 品牌行 ----
    draw.text((M, 28), "MINECRAFT SERVER STATUS", font=_font(15, mono=True), fill=(191, 245, 200))
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    _fs = _font(12)
    draw.text((W - M - _text_w(draw, stamp, _fs), 30), stamp, font=_fs, fill=(158, 168, 178))

    # ---- hero 面板内容 ----
    hx = M + hero_pad
    hy = hero_y + hero_pad
    # 图标槽（玻璃圆角 + favicon）
    slot_box = (hx, hy + (hero_h - hero_pad * 2 - icon_sz) // 2,
                hx + icon_sz, hy + (hero_h - hero_pad * 2 - icon_sz) // 2 + icon_sz)
    draw.rounded_rectangle(slot_box, radius=28, fill=(255, 255, 255, 30),
                           outline=(255, 255, 255, 60), width=1)
    icon = favicon_image(status, host)
    if icon is not None:
        icon = icon.resize((icon_sz - 24, icon_sz - 24), Image.LANCZOS)
        mask = Image.new("L", icon.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, icon.size[0] - 1, icon.size[1] - 1), radius=20, fill=255)
        img.paste(icon.convert("RGB"), (slot_box[0] + 12, slot_box[1] + 12), mask)
    else:
        d2 = ImageDraw.Draw(img)
        for yy in range(0, icon_sz - 24, 16):
            for xx in range(0, icon_sz - 24, 16):
                c = (58, 107, 58) if (xx // 16 + yy // 16) % 2 == 0 else (44, 82, 48)
                d2.rectangle((slot_box[0] + 12 + xx, slot_box[1] + 12 + yy,
                              slot_box[0] + 12 + min(xx + 16, icon_sz - 24) - 1,
                              slot_box[1] + 12 + min(yy + 16, icon_sz - 24) - 1), fill=c)

    tx = slot_box[2] + 24
    ty = hy
    # 服务器名 + 状态胶囊
    name = display_name_local(host)
    _draw_mixed(draw, tx, ty, name, name_fs, (255, 255, 255), bold=True)
    pill_w = 150
    px1 = W - M - hero_pad - 8
    p_y = ty + 4
    draw.rounded_rectangle((px1 - pill_w, p_y, px1, p_y + 34), radius=17,
                           fill=(255, 255, 255, 34), outline=(255, 255, 255, 66), width=1)
    _draw_mixed(draw, px1 - pill_w + 16, p_y + 8, state_txt, 15, state_rgb, bold=True)
    # ping 信号条（原版 icons.png 素材 10x8，x5 放大）
    bars = PING_DIR / f"ping_{lat_tier}.png"
    if bars.exists():
        try:
            from PIL import Image as _I
            bimg = _I.open(bars).resize((50, 40), _I.NEAREST)
            img.alpha_composite(bimg.convert("RGBA"), (px1 - pill_w + 16 + _mixed_w(probe, state_txt, 15) + 14,
                                                       p_y - 2))
        except Exception:
            pass

    # 地址
    _draw_mixed(draw, tx, ty + name_h + 6, f"{host}:{port}", addr_fs, (188, 196, 206))

    # MOTD 深色内嵌盒（居中彩色文字）
    motd_box = (tx, ty + name_h + 6 + int(addr_fs * 1.5) + 16,
                W - M - hero_pad - 8, ty + name_h + 6 + int(addr_fs * 1.5) + 16 + motd_box_h)
    draw.rounded_rectangle(motd_box, radius=22, fill=(3, 6, 10, 158),
                           outline=(255, 255, 255, 40), width=1)
    my = motd_box[1] + (motd_box[3] - motd_box[1] - motd_lines_n * motd_line_h) // 2
    for line in lines:
        if not line:
            my += motd_line_h
            continue
        total_w = sum(_mixed_w(probe, s["text"], motd_fs) for s in line)
        cx = motd_box[0] + max(12, (motd_box[2] - motd_box[0] - total_w) // 2)
        for seg in line:
            rgb = _hex_rgb(seg.get("color") or "#FFFFFF")
            cx = _draw_mixed(draw, cx, my, seg["text"], motd_fs, rgb, bold=seg.get("bold", False))

    # ---- 在线玩家面板 ----
    bx0, by0 = bar_box[0] + 24, bar_y + 20
    _draw_mixed(draw, bx0, by0, "在线玩家 ONLINE PLAYERS", 13, (200, 208, 218))
    cnt = f"{online_n:,} / {maxp:,}"
    _draw_mixed(draw, W - M - 24 - _mixed_w(probe, cnt, 28), by0 - 8, cnt, 28, (141, 255, 146), bold=True)
    bar_end = _seg_bar(draw, bx0, by0 + 34, inner_w - 48, 28, pct, is_online)
    pct_txt = f"占用率 {pct:.2f}% · 延迟状态 "
    _draw_mixed(draw, bx0, bar_end + 11, pct_txt, 12, (150, 160, 172))
    _draw_mixed(draw, bx0 + _mixed_w(probe, pct_txt, 12), bar_end + 11, lat_word, 12, lat_rgb)

    # ---- 四指标块 ----
    _vparts = [p.strip() for p in (version_name or "").replace("Requires MC", "").split("/") if p.strip()]
    version_short = " – ".join(_vparts) if len(_vparts) > 1 else (_vparts[0] if _vparts else "—")
    tiles = [
        ("服务器版本", version_short, "VERSION"),
        ("在线玩家", f"{online_n:,} / {maxp:,}", "PLAYERS"),
        ("网络延迟", f"{ms_txt} ms", "PING"),
        ("协议版本", protocol or "—", "PROTOCOL"),
    ]
    col_w = inner_w // 4
    for i, (k, v, tag) in enumerate(tiles):
        cx = M + i * col_w + 20
        if i:
            draw.line((M + i * col_w, tiles_box[1] + 16, M + i * col_w, tiles_box[3] - 16),
                      fill=(255, 255, 255, 36), width=1)
        _draw_mixed(draw, cx, tiles_box[1] + 17, k, 12, (196, 204, 214))
        vfs = 15 if len(v) > 20 else (19 if len(v) > 15 else 24)
        _draw_mixed(draw, cx, tiles_box[1] + 40, v, vfs, (255, 255, 255), bold=True)
        _draw_mixed(draw, cx, tiles_box[3] - 24, tag, 10, (120, 128, 138))

    # ---- 页脚 ----
    fx = _pill(draw, M, foot_y, "SRV" if status.get("_srv") else "直连")
    fx += 12
    _draw_mixed(draw, fx, foot_y + 5, f"{host}:{port}", 12, (188, 196, 206))
    fx += _mixed_w(probe, f"{host}:{port}", 12) + 12
    _draw_mixed(draw, fx, foot_y + 5, f"PING {ms_txt} ms", 12, lat_rgb)

    img.convert("RGB").save(out_png, "PNG")
    return out_png


def display_name_local(host: str) -> str:
    labels = host.split(".")
    if len(labels) > 2 and labels[0].lower() in {"mc", "play", "srv", "game", "join", "cn"}:
        labels = labels[1:]
    return ".".join(labels).upper()


# --------------------------------------------------------------------------
# 4. 异步入口
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
            return None, None, f"{type(exc).__name__}: {exc}"
        try:
            slug = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{host}_{port}")
            png = OUT / f"mc_card_{slug}_1x.png"
            render_card_png(status, host, port, png)
            return png, status, ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("motd %s 渲染失败: %s", host, exc)
            return None, None, f"{type(exc).__name__}: {exc}"

    return await asyncio.to_thread(_sync)
