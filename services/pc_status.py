"""
PC 状态 TCP 接收端 — 长连接接收 JSON 行
"""
from __future__ import annotations

import asyncio, json, os, time
from pathlib import Path
from core.logger import get_logger

logger = get_logger("pc_status")

_PC_DATA: dict = {}
_LAST_UPDATE: float = 0
_AUTH_KEY = os.environ.get("BOT_PC_KEY", "")


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """TCP 客户端处理：读 AUTH + JSON 行"""
    global _PC_DATA, _LAST_UPDATE

    try:
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        if not line:
            writer.close(); return
        line = line.decode().strip()
        if not line.startswith("AUTH ") or line[5:] != _AUTH_KEY:
            writer.close(); return

        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                data = json.loads(line.decode().strip())
                _PC_DATA = data
                _LAST_UPDATE = time.time()
            except json.JSONDecodeError:
                pass
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass
    finally:
        writer.close()


def get_pc_status() -> dict | None:
    if not _PC_DATA: return None
    if time.time() - _LAST_UPDATE > 30: return None
    return dict(_PC_DATA)


# ════════════════════════════════════════════════════════════
#  工具函数
# ════════════════════════════════════════════════════════════

def _fmt_bytes(n: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _fmt_speed(n: int) -> str:
    if n < 1024:
        return f"{n} B/s"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB/s"
    return f"{n/1024/1024:.2f} MB/s"


def _fmt_uptime(sec: int) -> str:
    d = sec // 86400
    h = (sec % 86400) // 3600
    m = (sec % 3600) // 60
    if d > 0:
        return f"{d}d {h}h {m}m"
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def _bar_class(percent: float, warn: float = 70, danger: float = 90) -> str:
    """返回霓虹配色 CSS 类"""
    if percent >= danger:
        return "b-red"
    if percent >= warn:
        return "b-orange"
    return "b-pink"


def _mem_bar_class(percent: float) -> str:
    if percent >= 90:
        return "b-red"
    if percent >= 75:
        return "b-orange"
    return "b-cyan"


def _gpu_bar_class(percent: float) -> str:
    if percent >= 90:
        return "b-red"
    if percent >= 75:
        return "b-orange"
    return "b-green"


# ════════════════════════════════════════════════════════════
#  HTML 卡片构建
# ════════════════════════════════════════════════════════════

def _build_gpu_card(gpu: dict | None) -> str:
    """返回完整 GPU 卡片 HTML，无 GPU 返回空串"""
    if not gpu:
        return ""
    pct = gpu.get("gpu_percent", 0)
    mem_pct = gpu.get("mem_percent", 0)
    temp = gpu.get("temp", 0)
    name = gpu.get("name", "GPU")
    mem_used = _fmt_bytes(gpu.get("mem_used", 0))
    mem_total = _fmt_bytes(gpu.get("mem_total", 0))

    bar_cls = _gpu_bar_class(pct)
    mem_bar_cls = _gpu_bar_class(mem_pct)
    temp_cls = "b-green" if temp < 70 else ("b-orange" if temp < 85 else "b-red")

    return f'''
  <!-- GPU 卡 -->
  <div class="card c-gpu">
    <div class="head">
      <div class="head-icon">GPU</div>
      <div class="head-title">显卡</div>
      <div class="head-sub">{temp}C</div>
    </div>
    <div class="body">
      <div class="m-row">
        <span class="m-label">GPU</span>
        <div class="m-bar-w"><div class="m-bar {bar_cls}" style="width:{pct}%"></div></div>
        <span class="m-val">{pct}<span class="unit">%</span></span>
      </div>
      <div class="m-row">
        <span class="m-label">VRAM</span>
        <div class="m-bar-w"><div class="m-bar {mem_bar_cls}" style="width:{mem_pct}%"></div></div>
        <span class="m-val">{mem_pct}<span class="unit">%</span></span>
      </div>
      <div class="i-row"><span class="i-label">Model</span><span class="i-val">{name}</span></div>
      <div class="i-row"><span class="i-label">VRAM</span><span class="i-val">{mem_used} / {mem_total}</span></div>
    </div>
  </div>'''


def _build_disks_html(disks: list) -> str:
    if not disks:
        return '<div class="i-row"><span class="i-label">无可用磁盘</span></div>'
    rows = []
    for d in disks:
        drive = d.get("drive", "?")
        pct = d.get("percent", 0)
        free = _fmt_bytes(d.get("free", 0))
        total = _fmt_bytes(d.get("total", 0))
        bar_cls = "b-red" if pct >= 92 else ("b-orange" if pct >= 80 else "b-purple")
        rows.append(
            f'<div class="disk-row">'
            f'<span class="disk-drive">{drive}</span>'
            f'<div class="disk-bar-w"><div class="m-bar {bar_cls}" style="width:{pct}%"></div></div>'
            f'<span class="disk-val">{free} / {total}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def _build_battery_html(battery: dict | None) -> str:
    if not battery:
        return ""
    pct = battery.get("percent", 0)
    plugged = battery.get("plugged", False)
    status = "AC" if plugged else "BAT"
    return f'<div class="i-row"><span class="i-label">Battery</span><span class="i-val">{pct}% ({status})</span></div>'


def _build_voltages_card(voltages: dict | None) -> str:
    """返回完整电压卡片 HTML，无电压返回空串"""
    if not voltages:
        return ""
    labels = {
        "cpu_vcore": "CPU Vcore",
        "gpu_vcore": "GPU Vcore",
        "dram": "DRAM",
        "v33": "+3.3V",
        "v5": "+5V",
        "v12": "+12V",
    }
    cells = []
    for key, label in labels.items():
        val = voltages.get(key)
        if val is not None:
            cells.append(
                f'<div class="v-cell"><span class="v-name">{label}</span>'
                f'<span class="v-val">{val}V</span></div>'
            )
    if not cells:
        return ""
    return f'''
  <!-- 电压卡 -->
  <div class="card c-volt">
    <div class="head">
      <div class="head-icon">VLT</div>
      <div class="head-title">电压</div>
    </div>
    <div class="body">
      <div class="volt-grid">{"".join(cells)}</div>
    </div>
  </div>'''


def _build_music_html(music: dict | None) -> str:
    if not music:
        return '<div class="music-empty">未播放</div>'

    song = music.get("song", "")
    if not song:
        return '<div class="music-empty">未播放</div>'

    player = music.get("player", "")
    cover = music.get("cover", "")
    if cover:
        cover = cover.replace("128y128", "500y500")
    progress_ms = music.get("progress_ms", 0)
    duration_str = music.get("duration", "")
    playing = music.get("playing", False)
    lyric_line = music.get("lyric_line", "") or music.get("lyric", "")

    progress_pct = 0
    cur_str = "0:00"
    if duration_str and progress_ms > 0:
        parts = duration_str.split(":")
        if len(parts) == 2:
            dur_sec = int(parts[0]) * 60 + int(parts[1])
            if dur_sec > 0:
                pos = int(progress_ms / 1000)
                progress_pct = round(pos / dur_sec * 100, 1)
                cur_str = f"{pos // 60}:{pos % 60:02d}"

    status_cls = "playing" if playing else "paused"
    status_text = "PLAYING" if playing else "PAUSED"

    cover_html = f'<img class="music-cover" src="{cover}">' if cover else '<div class="music-cover" style="display:flex;align-items:center;justify-content:center;font-size:22px;color:var(--text-3)">--</div>'

    lyric_html = f'<div class="music-lyric">{lyric_line}</div>' if lyric_line else ""

    return f"""
    <div class="music-cover-wrap">
      {cover_html}
      <div class="music-info">
        <div class="music-song">{song}</div>
        <div class="music-player">{player}</div>
        <span class="music-status {status_cls}">{status_text}</span>
        <div class="music-progress-w"><div class="music-progress" style="width:{progress_pct}%"></div></div>
        <div class="music-time"><span>{cur_str}</span><span>{duration_str}</span></div>
      </div>
    </div>
    {lyric_html}"""


def build_sys_card_html(owner: str = "Trusler", bot_name: str = "幻梦") -> str | None:
    """构建系统状态 HTML 卡片。无数据返回 None。"""
    data = get_pc_status()
    if not data:
        return None

    from datetime import datetime

    tpl_path = Path(__file__).resolve().parent.parent / "data" / "templates" / "sys_card.html"
    if not tpl_path.exists():
        return None
    html = tpl_path.read_text(encoding="utf-8")

    # 基础
    hostname = data.get("hostname", "未知")
    boot_time_ts = data.get("boot_time", 0)
    uptime_sec = data.get("uptime", 0)
    if boot_time_ts:
        boot_str = datetime.fromtimestamp(boot_time_ts).strftime("%Y-%m-%d %H:%M:%S")
    else:
        boot_str = "未知"
    uptime_str = _fmt_uptime(uptime_sec) if uptime_sec else "未知"

    # CPU
    cpu_pct = data.get("cpu_percent", 0)
    cpu_count = data.get("cpu_count", 0)
    cpu_freq = data.get("cpu_freq", 0)
    cpu_bar_cls = _bar_class(cpu_pct)

    # GPU
    gpu_card = _build_gpu_card(data.get("gpu"))

    # 内存
    mem = data.get("memory", {})
    mem_pct = mem.get("percent", 0)
    mem_used = _fmt_bytes(mem.get("used", 0))
    mem_total = _fmt_bytes(mem.get("total", 0))
    mem_bar_cls = _mem_bar_class(mem_pct)
    swap_pct = data.get("swap", {}).get("percent", 0)

    # 磁盘
    disks = data.get("disks", [])
    disks_html = _build_disks_html(disks)
    disk_count = len(disks)

    # 网络
    net = data.get("net", {})
    net_up = _fmt_speed(net.get("upload", 0))
    net_down = _fmt_speed(net.get("download", 0))

    # 电池
    battery_html = _build_battery_html(data.get("battery"))

    # 电压
    voltages_card = _build_voltages_card(data.get("voltages"))

    # 进程数
    proc_count = data.get("proc_count", 0)

    # 窗口
    window_title = data.get("window", "(无活动窗口)")
    window_app = data.get("app", "")

    # 音乐
    music_html = _build_music_html(data.get("music"))

    # 时间
    now_str = datetime.now().strftime("%H:%M:%S")

    # 替换
    html = html.replace("{{HOSTNAME}}", hostname)
    html = html.replace("{{OWNER}}", owner)
    html = html.replace("{{BOOT_TIME}}", boot_str)
    html = html.replace("{{UPTIME}}", uptime_str)
    html = html.replace("{{PROC_COUNT}}", str(proc_count))
    html = html.replace("{{CPU_PERCENT}}", str(cpu_pct))
    html = html.replace("{{CPU_BAR_CLASS}}", cpu_bar_cls)
    html = html.replace("{{CPU_FREQ}}", str(cpu_freq))
    html = html.replace("{{CPU_COUNT}}", str(cpu_count))
    html = html.replace("{{GPU_CARD}}", gpu_card)
    html = html.replace("{{MEM_PERCENT}}", str(mem_pct))
    html = html.replace("{{MEM_BAR_CLASS}}", mem_bar_cls)
    html = html.replace("{{MEM_USED}}", mem_used)
    html = html.replace("{{MEM_TOTAL}}", mem_total)
    html = html.replace("{{SWAP_PERCENT}}", str(swap_pct))
    html = html.replace("{{DISK_COUNT}}", str(disk_count))
    html = html.replace("{{DISKS_HTML}}", disks_html)
    html = html.replace("{{NET_UP}}", net_up)
    html = html.replace("{{NET_DOWN}}", net_down)
    html = html.replace("{{BATTERY_HTML}}", battery_html)
    html = html.replace("{{VOLTAGES_CARD}}", voltages_card)
    html = html.replace("{{WINDOW_TITLE}}", window_title)
    html = html.replace("{{WINDOW_APP}}", window_app)
    html = html.replace("{{MUSIC_HTML}}", music_html)
    html = html.replace("{{UPDATE_TIME}}", now_str)
    html = html.replace("{{BRAND}}", f"Generated by {bot_name}")

    return html


def format_pc_status(owner: str = "Trusler") -> str:
    """纯文本格式（保留兼容）"""
    data = get_pc_status()
    if not data:
        return "暂无 PC 状态数据（可能未开机或未运行采集脚本）"

    lines = []
    hostname = data.get("hostname", "未知")
    window = data.get("window", "")
    app = data.get("app", "")
    music = data.get("music", {})

    lines.append(f"[PC] {owner}'s {hostname}")

    if window:
        parts = [f"前台: {window}"]
        if app: parts.append(f"({app})")
        lines.append(" ".join(parts))
    else:
        lines.append("前台: (无)")

    if music:
        song = music.get("song", "")
        player = music.get("player", "")
        lyric_line = music.get("lyric_line", "") or music.get("lyric", "")
        cover = music.get("cover", "")
        progress_ms = music.get("progress_ms", 0)
        duration_str = music.get("duration", "")
        playing = music.get("playing", False)
        if song:
            status = "播放" if playing else "暂停"
            player_str = f" ({player})" if player else ""
            lines.append(f"音乐: [{status}] {song}{player_str}")
            if cover:
                cover = cover.replace("128y128", "500y500")
                lines.append(f"[CQ:image,url={cover},type=show]")
            if duration_str and progress_ms > 0:
                parts = duration_str.split(":")
                dur_sec = int(parts[0])*60 + int(parts[1]) if len(parts)==2 else 0
                if dur_sec > 0:
                    pos = int(progress_ms/1000)
                    cur_str = f"{pos//60}:{pos%60:02d}"
                    bar_len = 20
                    filled = int(pos/dur_sec*bar_len) if dur_sec else 0
                    bar = "="*filled + "-"*(bar_len-filled)
                    lines.append(f"进度: {bar} {cur_str} / {duration_str}")
            if lyric_line:
                lines.append(f"歌词: {lyric_line}")
        elif not music.get("hasSong", True):
            lines.append("音乐: (未播放)")
    else:
        lines.append("音乐: (未播放)")

    return "\n".join(lines)


async def start_pc_server(port: int = 58890):
    server = await asyncio.start_server(_handle_client, "0.0.0.0", port)
    logger.info("PC 状态 TCP 接收端: 0.0.0.0:%d", port)
    return server
