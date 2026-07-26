"""
Web 实时日志控制台 — 端口 58888
- WebSocket 推送日志记录到浏览器
- 彩色暗色主题，支持按级别/关键词过滤
- 随 bot 自动启动
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

# ── 连接池 ────────────────────────────────────────────────
_clients: set[asyncio.Queue] = set()


async def broadcast(record: dict):
    """推送日志记录到所有连接的客户端"""
    for q in list(_clients):
        try:
            q.put_nowait(record)
        except asyncio.QueueFull:
            pass


# ── HTTP Handler ──────────────────────────────────────────

_HTML = ""

def _tail_file(path: Path, n: int) -> str:
    """读取文件尾部 n 行，去除 ANSI"""
    import re
    try:
        with open(path, "rb") as f:
            if f.seek(0, 2) == 0:
                return ""
            buf_size = 8192
            blocks = []
            f.seek(0, 2)
            remaining = f.tell()
            while remaining > 0 and len(blocks) < n:
                read_size = min(buf_size, remaining)
                f.seek(remaining - read_size)
                blocks.append(f.read(read_size).decode("utf-8", errors="ignore"))
                remaining -= read_size
            text = "".join(reversed(blocks))
            text = re.sub(r'\x1b\[[0-9;]*m', '', text)  # 去 ANSI
            lines = text.split("\n")
            return "\n".join(lines[-n:]).strip()
    except Exception:
        return ""


def _load_html() -> str:
    global _HTML
    if _HTML:
        return _HTML
    here = Path(__file__).resolve().parent.parent
    path = here / "data" / "templates" / "console.html"
    if path.exists():
        _HTML = path.read_text(encoding="utf-8")
    else:
        _HTML = _build_html()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_HTML, encoding="utf-8")
    return _HTML


def _load_static(name: str) -> bytes | None:
    here = Path(__file__).resolve().parent.parent
    path = here / "data" / "templates" / name
    if path.exists():
        return path.read_bytes()
    return None


_CONTENT_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".html": "text/html",
}


def _build_html() -> str:
    """控制台 HTML 不存在时的最小后备页面"""
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>幻梦 QQ Bot · 控制台</title></head>
<body style="background:#0d0d1a;color:#e5e7eb;font-family:sans-serif;display:grid;place-items:center;height:100vh">
<div style="text-align:center"><h1 style="color:#67e8f9">幻梦 QQ Bot</h1>
<p style="color:#6b7280">控制台模板文件缺失，请检查部署</p></div></body></html>"""



async def _http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """极简 HTTP 服务器"""
    try:
        data = await asyncio.wait_for(reader.read(4096), timeout=5)
        request = data.decode("utf-8", errors="ignore")
        lines = request.split("\r\n")

        # WebSocket 升级
        if any("Upgrade: websocket" in l for l in lines):
            key = ""
            for l in lines:
                if l.lower().startswith("sec-websocket-key:"):
                    key = l.split(":", 1)[1].strip()
                    break
            if key:
                import hashlib, base64
                accept = base64.b64encode(
                    hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
                ).decode()
                writer.write(
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
                )
                await writer.drain()
                await _ws_handler(reader, writer)
                return

        # API: 历史日志
        if request.startswith("GET /api/logs"):
            import urllib.parse
            qs = urllib.parse.urlparse(lines[0].split()[1]).query
            params = urllib.parse.parse_qs(qs)
            n = int(params.get("lines", [200])[0])
            log_path = Path(__file__).resolve().parent.parent / "logs" / "huanmeng.log"
            if log_path.exists():
                tail = _tail_file(log_path, n)
                body = tail.encode("utf-8")
            else:
                body = b""
            resp = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode() + body
            writer.write(resp)
            await writer.drain()
            return

        # 静态文件 CSS/JS
        if request.startswith("GET /"):
            path = lines[0].split()[1]
            name = path.lstrip("/")
            if name in ("console.css", "console.js", "guard.js"):
                body = _load_static(name)
                if body:
                    ext = "." + name.rsplit(".", 1)[-1]
                    ct = _CONTENT_TYPES.get(ext, "application/octet-stream")
                    resp = (
                        "HTTP/1.1 200 OK\r\n"
                        f"Content-Type: {ct}; charset=utf-8\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode() + body
                    writer.write(resp)
                    await writer.drain()
                    return

        # 普通 HTTP → 返回 HTML
        html = _load_html()
        body = html.encode("utf-8")
        resp = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + body
        writer.write(resp)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


async def _ws_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """WebSocket 帧处理"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    _clients.add(queue)
    try:
        while True:
            try:
                record = await asyncio.wait_for(queue.get(), timeout=2)
                data = json.dumps(record, ensure_ascii=False)
                await _ws_send(writer, data)
            except asyncio.TimeoutError:
                # 心跳
                await _ws_send(writer, "", opcode=0x9)
    except Exception:
        pass
    finally:
        _clients.discard(queue)
        try:
            writer.close()
        except Exception:
            pass


async def _ws_send(writer: asyncio.StreamWriter, text: str, opcode: int = 0x1):
    """发送 WebSocket 文本帧"""
    payload = text.encode("utf-8")
    length = len(payload)
    frame = bytearray()
    frame.append(0x80 | opcode)
    if length < 126:
        frame.append(length)
    elif length < 65536:
        frame.append(126)
        frame.extend(length.to_bytes(2, "big"))
    else:
        frame.append(127)
        frame.extend(length.to_bytes(8, "big"))
    frame.extend(payload)
    try:
        writer.write(bytes(frame))
        await writer.drain()
    except Exception:
        raise


# ── 自定义 Logging Handler ───────────────────────────────

class WebSocketLogHandler(logging.Handler):
    """将日志记录推送到 WebSocket 客户端"""

    def emit(self, record: logging.LogRecord):
        try:
            ts = datetime.now(CST).strftime("%H:%M:%S") + f".{int(record.created % 1 * 1000):03d}"
            msg = self.format(record)
            # 提取 chat ID（群号/QQ号）
            chat = ""
            for pattern in [
                r'chat[:=\s]*(\d{5,})',    # chat=1058782600, chat:1058782600
                r'群(\d{5,})',              # 群1058782600
                r'\[(\d{5,})\]',            # [1058782600]
                r'(\d{5,})',                # 任意 5+ 位数字
            ]:
                m = re.search(pattern, msg)
                if m and m.group(1) not in ('8099', '58888'):  # 排除端口号
                    chat = m.group(1)
                    break
            payload = {
                "time": ts,
                "lv": record.levelname,
                "src": f"{record.module}:{record.funcName}:{record.lineno}",
                "msg": msg,
                "chat": chat,
            }
            # 仅在事件循环运行时推送（避免初始化时崩溃）
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(broadcast(payload))
            except RuntimeError:
                pass  # 事件循环尚未启动，跳过
        except Exception:
            pass


# ── 服务器启动 ───────────────────────────────────────────

_server_task: asyncio.Task | None = None


async def start(port: int = 58888):
    """启动日志 Web 服务器"""
    global _server_task
    server = await asyncio.start_server(_http_handler, "0.0.0.0", port)
    addr = server.sockets[0].getsockname()
    logging.getLogger("huanmeng").info("🌐 日志控制台已启动 → http://0.0.0.0:%d", addr[1])
    _server_task = asyncio.current_task()
    async with server:
        await server.serve_forever()
