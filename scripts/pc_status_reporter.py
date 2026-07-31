"""
PC 状态上报 — 本机运行，定期采集窗口标题+音乐信息+歌词 → POST 到服务器
依赖: pip install pywin32 requests
"""
import json, time, os, socket, threading, urllib.request, urllib.error

SERVER = os.environ.get("BOT_SERVER", "01240820.xyz")
PORT = int(os.environ.get("BOT_PC_PORT", "58890"))
INTERVAL = int(os.environ.get("BOT_PC_INTERVAL", "5"))
AUTH_KEY = os.environ.get("BOT_PC_KEY", "huanmeng_pc_2026")

# ── 窗口标题 ──
try:
    import win32gui, win32process, psutil
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

def get_window_title():
    """获取当前活动窗口标题"""
    if not HAS_WIN32:
        return ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        # 获取进程名
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid) if pid else None
        proc_name = proc.name() if proc else ""
        return f"{title} [{proc_name}]" if proc_name else title
    except Exception:
        return ""

# ── 音乐信息 ──
MUSIC_API = "http://localhost:9863/apiPage"

def get_music_info():
    """从本地音乐播放器 API 获取当前歌曲+歌词"""
    try:
        req = urllib.request.Request(MUSIC_API)
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}

    result = {}
    # 歌手+歌名
    artist = data.get("artist", "")
    title = data.get("title", "")
    if title:
        result["song"] = f"{artist} - {title}" if artist else title

    # 歌词（如果有 lrc 字段）
    lrc = data.get("lrc", "") or data.get("lyric", "") or data.get("lyrics", "")
    if lrc:
        result["lyric"] = lrc[:500]  # 截断

    # 播放状态
    result["playing"] = data.get("playing", False)
    return result

# ── 上报 ──
def report():
    data = {
        "window": get_window_title(),
        "hostname": socket.gethostname(),
    }
    music = get_music_info()
    if music:
        data["music"] = music

    try:
        req = urllib.request.Request(
            f"http://{SERVER}:{PORT}/pc_status",
            data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Auth": AUTH_KEY,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            pass
    except Exception:
        pass

def main():
    print(f"[PC Status] 上报到 {SERVER}:{PORT} / {INTERVAL}s 间隔")
    while True:
        try:
            report()
        except Exception:
            pass
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
