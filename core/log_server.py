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
    return r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>幻梦 QQ Bot · 控制台</title>
<style>
:root{
--bg:#0d0d1a;--panel:#12122a;--border:#1e1e3a;
--time:#6b7280;--debug:#7dd3fc;--info:#c4b5fd;--warn:#fb923c;--error:#ef4444;
--source:#9ca3af;--symbol:#f9a8d4;--module:#4ade80;--num:#fde047;
--text:#e5e7eb;--highlight:#67e8f9;--admin:#f472b6;--sep:#a78bfa;
--banner:#e11d48;--sub:#f9a8d4;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:"Cascadia Code","Fira Code","JetBrains Mono","Consolas",monospace;font-size:13px;overflow:hidden;height:100vh;display:flex;flex-direction:column}
.header{background:var(--panel);border-bottom:1px solid var(--border);padding:10px 16px;display:flex;align-items:center;gap:12px;flex-shrink:0}
.header h1{color:var(--banner);font-size:15px;font-weight:700}
.header .dot{width:8px;height:8px;border-radius:50%;background:#22c55e;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.filters{display:flex;gap:8px;margin-left:auto;align-items:center}
.filters label{display:flex;align-items:center;gap:4px;cursor:pointer;font-size:12px;opacity:.6;padding:2px 6px;border-radius:4px;transition:.2s}
.filters label:hover,.filters label.on{opacity:1;background:rgba(255,255,255,.06)}
.filters input{margin:0;accent-color:var(--module)}
#search{background:var(--panel);border:1px solid var(--border);color:var(--text);padding:4px 10px;border-radius:6px;font-size:12px;width:180px;outline:none}
#search:focus{border-color:var(--module)}
#log-container{flex:1;overflow-y:auto;padding:8px 0}
.log-line{padding:3px 16px;border-bottom:1px solid rgba(255,255,255,.02);display:flex;gap:8px;white-space:pre-wrap;word-break:break-all;line-height:1.5}
.log-line:hover{background:rgba(255,255,255,.02)}
.log-line.hidden{display:none}
.time{color:var(--time);min-width:90px;flex-shrink:0}
.level{min-width:40px;flex-shrink:0;font-weight:700;text-align:center}
.level.debug{color:var(--debug)}
.level.info{color:var(--info);font-weight:700}
.level.warning{color:var(--warn);font-weight:700}
.level.error{color:var(--error);font-weight:700}
.source{color:var(--source);min-width:140px;flex-shrink:0;font-size:11px}
.msg{flex:1}
.msg .sym{color:var(--symbol);font-weight:700}
.msg .mod{color:var(--module)}
.msg .num{color:var(--num)}
.msg .admin{color:var(--admin);font-weight:700}
.msg .hi{color:var(--highlight);font-weight:700}
.msg .sep{color:var(--sep)}
.footer{background:var(--panel);border-top:1px solid var(--border);padding:6px 16px;font-size:11px;color:var(--source);display:flex;gap:16px;flex-shrink:0}
@media(max-width:768px){
body{font-size:12px}
.header{flex-wrap:wrap;gap:6px;padding:8px 10px}
.header h1{font-size:13px}
.filters{gap:4px;flex-wrap:wrap}
.filters label{font-size:10px;padding:1px 4px}
#search{width:120px;font-size:11px;padding:3px 8px}
.log-line{flex-wrap:wrap;gap:4px;padding:3px 8px}
.time{min-width:70px;font-size:10px}
.level{min-width:30px;font-size:10px}
.source{min-width:0;font-size:9px}
}
#filter-info{color:var(--symbol);font-size:11px;margin-left:4px}
</style></head><body>
<div class="header">
<span class="dot"></span>
<h1>幻梦 QQ Bot · 实时控制台</h1><span id="filter-info"></span>
<div class="filters">
<label class="on"><input type="checkbox" data-lv="INFO" checked> INFO</label>
<label class="on"><input type="checkbox" data-lv="DEBUG" checked> DEBUG</label>
<label class="on"><input type="checkbox" data-lv="WARNING" checked> WARN</label>
<label class="on"><input type="checkbox" data-lv="ERROR" checked> ERROR</label>
<input id="search" placeholder="搜索...">
<span style="font-size:11px;display:flex;align-items:center;gap:4px;margin-left:auto">
<label style="color:var(--text);cursor:pointer"><input type="checkbox" id="auto-scroll" checked> 自动滚动</label>
</span>
</div>
</div>
<div id="log-container"></div>
<div class="footer">
<span id="count">0 条</span><span id="status">已连接</span>
</div>
<script>
const CONTAINER=document.getElementById('log-container');
const COUNT=document.getElementById('count');
const STATUS=document.getElementById('status');
const SEARCH=document.getElementById('search');
const FILTER_INFO=document.getElementById('filter-info');
let total=0,maxLines=500;
let hiddenLv=new Set();
let searchText='';
let chatFilter='';

// ?num=<群号或QQ号>
let p=new URLSearchParams(location.search);
chatFilter=p.get('num')||'';
if(chatFilter)FILTER_INFO.textContent='[群/私 '+chatFilter+']';

document.querySelectorAll('.filters input[data-lv]').forEach(cb=>{
cb.addEventListener('change',()=>{
if(cb.checked){hiddenLv.delete(cb.dataset.lv);cb.parentElement.classList.add('on')}
else{hiddenLv.add(cb.dataset.lv);cb.parentElement.classList.remove('on')}
applyFilters();
});
});
SEARCH.addEventListener('input',()=>{searchText=SEARCH.value.toLowerCase();applyFilters()});

const AUTO_SCROLL=document.getElementById('auto-scroll');

let ws;
function connectWS(){
STATUS.textContent='连接中...';
ws=new WebSocket('ws://'+location.host+'/ws');
ws.onopen=()=>STATUS.textContent='已连接';
ws.onclose=()=>{STATUS.textContent='断开';setTimeout(connectWS,3000)};
ws.onerror=()=>ws.close();
ws.onmessage=e=>{
let d=JSON.parse(e.data);
if(Array.isArray(d))d.forEach(addLine);
else addLine(d);
};
}

// 加载历史日志
async function loadHistory(){
if(total>0)return;
try{
let r=await fetch('/api/logs?lines=300');
if(!r.ok)return;
let lines=await r.text();
lines.split('\n').filter(Boolean).forEach(l=>{
l=l.replace(/\x1b\[[0-9;]*m/g,''); // 去除 ANSI 颜色码
let div=document.createElement('div');
div.className='log-line';
div.dataset.level='INFO';
div.dataset.chat='';
div.dataset.text=l.toLowerCase();
div.innerHTML='<span class="msg">'+colorMsg(l)+'</span>';
CONTAINER.appendChild(div);
applyFilter(div);
total++;
});
COUNT.textContent=total+' 条';
CONTAINER.scrollTop=CONTAINER.scrollHeight;
}catch(e){}
}

connectWS();
loadHistory();

function colorMsg(msg){
return msg
.replace(/(📩|✅|🐱|📎|⚠️|🔒|🎨|⚙️|📂)/g,'<span class="sym">$1</span>')
.replace(/\[([^\]]+)\]/g,(m,c)=>'[<span class="mod">'+c+'</span>]')
.replace(/(\d{5,})(?=[\s,<)])/g,'<span class="num">$1</span>')
.replace(/([\d.]+s|[\d.]+KB|[\d]+字|[\d]+条|[\d]+次)/g,'<span class="num">$1</span>')
.replace(/(\/~[\w]+)/g,'<span class="admin">$1</span>')
.replace(/Trusler|admin|管理员/g,'<span class="admin">$&</span>')
.replace(/(启动成功|连接完成|重载完成|准备就绪|初始化完成)/g,'<span class="hi">$1</span>');
}

function addLine(r){
total++;
let div=document.createElement('div');
div.className='log-line';
div.dataset.level=r.lv||'INFO';
div.dataset.chat=r.chat||'';
div.dataset.text=(r.time+' '+r.msg).toLowerCase();

let lvClass=r.lv?r.lv.toLowerCase():'info';
// 单 span 结构（与 loadHistory 一致），避免 flex 列导致的复制换行
div.innerHTML='<span class="msg">'+
  '<span class="time">['+r.time+']</span> '+
  '<span class="level '+lvClass+'">['+r.lv.padEnd(8)+']</span> '+
  '<span class="source">('+r.src+')</span> '+
  colorMsg(r.msg)+'</span>';

CONTAINER.appendChild(div);
while(CONTAINER.children.length>maxLines)CONTAINER.firstChild.remove();
if(AUTO_SCROLL.checked)CONTAINER.scrollTop=CONTAINER.scrollHeight;
COUNT.textContent=total+' 条';
applyFilter(div);
}

function applyFilter(el){
let lv=el.dataset.level;
let hidden=hiddenLv.has(lv)||(searchText&&!el.dataset.text.includes(searchText));
if(!hidden&&chatFilter&&el.dataset.chat&&el.dataset.chat!==chatFilter)hidden=true;
el.classList.toggle('hidden',hidden);
}
function applyFilters(){
document.querySelectorAll('.log-line').forEach(applyFilter);
}
</script></body></html>"""


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
