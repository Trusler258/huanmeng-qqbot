"""
手机状态 TCP 接收端 — 长连接接收 JSON 行（端口 58892）
协议与 PC 状态服务 (pc_status.py) 一致:
  1. 客户端连接后发送 `AUTH <BOT_PC_KEY>\n`
  2. 鉴权通过后保持稳定连接，持续发送每行一个 JSON 对象 + '\n'
  3. 服务端只保留最新一份快照（60s 超时，手机可能息屏/断网）
  4. `/~phone` 指令读取该快照并格式化返回

手机端上报字段约定（缺省字段自动跳过）:
  device_type : "phone"            # 标识设备类型
  model       : "Pixel 7"          # Build.MODEL
  brand       : "google"           # Build.BRAND
  android_version: "14"            # 系统版本
  sdk_int     : 34                 # Build.VERSION.SDK_INT
  hostname    : "Huang's Phone"    # 用户自定义标识（可选）
  battery     : {percent, plugged, temperature}
  cpu         : {percent, cores}
  memory      : {total, used, percent}   # 字节
  storage     : {total, free, percent}   # 字节
  network     : {type, ssid|operator, upload, download}
  screen_on   : true/false
  uptime      : 秒（开机时长）
  timestamp   : 上报时的 Unix 时间戳
"""
from __future__ import annotations

import asyncio, json, os, time
from core.logger import get_logger

logger = get_logger("phone_status")

_PHONE_DATA: dict = {}
_LAST_UPDATE: float = 0
_AUTH_KEY = os.environ.get("BOT_PC_KEY", "")
_client_writer: asyncio.StreamWriter | None = None
_TIMEOUT = 60  # 手机可能息屏/断网，放宽超时


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """TCP 客户端处理：读 AUTH + JSON 行"""
    global _PHONE_DATA, _LAST_UPDATE, _client_writer

    peer = writer.get_extra_info("peername")
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        if not line:
            writer.close()
            return
        line = line.decode(errors="replace").strip()
        if not line.startswith("AUTH ") or line[5:] != _AUTH_KEY:
            logger.warning("手机端鉴权失败，关闭连接 %s", peer)
            writer.close()
            return

        _client_writer = writer
        logger.info("手机客户端已连接 %s", peer)

        while True:
            line = await reader.readline()
            if not line:
                break
            line_str = line.decode(errors="replace").strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
                # 仅接受手机上报（防御性：忽略异常数据）
                if not isinstance(data, dict):
                    continue
                _PHONE_DATA = data
                _LAST_UPDATE = time.time()
            except json.JSONDecodeError:
                pass
    except asyncio.TimeoutError:
        logger.warning("手机端鉴权超时 %s", peer)
    except (ConnectionResetError, BrokenPipeError):
        pass
    except Exception as e:
        logger.warning("手机端连接异常 %s: %s", peer, e)
    finally:
        if _client_writer is writer:
            _client_writer = None
        writer.close()


def get_phone_status() -> dict | None:
    """返回最新手机状态快照；无数据或超时返回 None"""
    if not _PHONE_DATA:
        return None
    if time.time() - _LAST_UPDATE > _TIMEOUT:
        return None
    return dict(_PHONE_DATA)


# ════════════════════════════════════════════════════════════
#  格式化工具
# ════════════════════════════════════════════════════════════

def _fmt_bytes(n) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _fmt_speed(n) -> str:
    if not n:
        return "0 B/s"
    n = int(n)
    if n < 1024:
        return f"{n} B/s"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB/s"
    return f"{n / 1024 / 1024:.2f} MB/s"


def _fmt_uptime(sec) -> str:
    if not sec:
        return "未知"
    sec = int(sec)
    d = sec // 86400
    h = (sec % 86400) // 3600
    m = (sec % 3600) // 60
    if d > 0:
        return f"{d}d {h}h {m}m"
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def format_phone_status() -> str:
    """纯文本格式（/~phone）"""
    data = get_phone_status()
    if not data:
        return "暂无手机状态数据（App 未连接或未运行）"

    lines: list[str] = []
    model = data.get("model") or "未知机型"
    brand = data.get("brand") or ""
    android = data.get("android_version") or "?"
    hostname = data.get("hostname", "")
    title = f"{brand} {model}".strip() if brand else model
    if hostname:
        title += f" {hostname}"
    lines.append(f"[手机] {title} (Android {android})")

    # 电量
    bat = data.get("battery") or {}
    if bat:
        pct = bat.get("percent")
        plugged = bat.get("plugged")
        status = "充电中" if plugged else "未充电"
        temp = bat.get("temperature")
        bat_line = f"电量: {pct}% ({status})" if pct is not None else "电量: ?"
        if temp:
            bat_line += f" {temp}°C"
        lines.append(bat_line)

    # CPU
    cpu = data.get("cpu") or {}
    if cpu:
        pct = cpu.get("percent")
        cores = cpu.get("cores")
        cpu_line = f"CPU: {pct}%" if pct is not None else "CPU: ?"
        if cores:
            cpu_line += f" ({cores}核)"
        lines.append(cpu_line)

    # 内存
    mem = data.get("memory") or {}
    if mem:
        total = mem.get("total")
        used = mem.get("used")
        pct = mem.get("percent")
        if total and used:
            lines.append(f"内存: {_fmt_bytes(used)} / {_fmt_bytes(total)} ({pct}%)")
        elif pct is not None:
            lines.append(f"内存: {pct}%")

    # 存储
    st = data.get("storage") or {}
    if st:
        total = st.get("total")
        free = st.get("free")
        pct = st.get("percent")
        if total and free:
            lines.append(f"存储: {_fmt_bytes(free)} 可用 / {_fmt_bytes(total)} ({pct}%)")
        elif pct is not None:
            lines.append(f"存储: {pct}% 已用")

    # 网络
    net = data.get("network") or {}
    if net:
        ntype = net.get("type", "?")
        ssid = net.get("ssid") or net.get("operator") or ""
        net_line = f"网络: {ntype}"
        if ssid:
            net_line += f" ({ssid})"
        up = net.get("upload")
        down = net.get("download")
        if up or down:
            net_line += f" ↑{_fmt_speed(up)} ↓{_fmt_speed(down)}"
        lines.append(net_line)

    # 屏幕
    screen_on = data.get("screen_on")
    if screen_on is not None:
        lines.append(f"屏幕: {'亮屏' if screen_on else '息屏'}")

    # 开机时长
    uptime = data.get("uptime")
    if uptime:
        lines.append(f"开机: {_fmt_uptime(uptime)}")

    # 更新时间
    ts = data.get("timestamp")
    if ts:
        from datetime import datetime
        lines.append(f"更新: {datetime.fromtimestamp(ts).strftime('%H:%M:%S')}")

    return "\n".join(lines)


async def start_phone_server(port: int = 58892):
    server = await asyncio.start_server(_handle_client, "0.0.0.0", port, limit=4_194_304)
    logger.info("手机状态 TCP 接收端: 0.0.0.0:%d", port)
    return server
