"""
PC 状态上报 v3 — TCP 长连接 + 详细系统信息
依赖: pip install pywin32 psutil websocket-client
可选: pip install pynvml (NVIDIA GPU), wmi (电压)
"""
import json, time, os, socket, threading, traceback, sys

SERVER = os.environ.get("BOT_SERVER", "your-server.example.com")
PORT = int(os.environ.get("BOT_PC_PORT", "58890"))
AUTH_KEY = os.environ.get("BOT_PC_KEY", "your-auth-key")

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ── 窗口标题 ──
try:
    import win32gui, win32process, psutil
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    log("WARN: pywin32/psutil 未安装，无窗口信息")

def get_window_title():
    if not HAS_WIN32: return "", ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid) if pid else None
        return title or "", proc.name() if proc else ""
    except Exception:
        return "", ""

# ── GPU (NVIDIA) ──
try:
    import pynvml
    pynvml.nvmlInit()
    _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    _GPU_NAME = pynvml.nvmlDeviceGetName(_GPU_HANDLE)
    if isinstance(_GPU_NAME, bytes):
        _GPU_NAME = _GPU_NAME.decode("utf-8", errors="replace")
    HAS_GPU = True
    log(f"GPU: {_GPU_NAME}")
except Exception:
    HAS_GPU = False
    _GPU_NAME = ""

# ── 电压 (WMI, 需要 OpenHardwareMonitor / LibreHardwareMonitor) ──
try:
    import wmi
    _WMI_VOLT = None
    for _ns in [r"root\OpenHardwareMonitor", r"root\LibreHardwareMonitor"]:
        try:
            _WMI_VOLT = wmi.WMI(namespace=_ns)
            log(f"电压监控: 启用 ({_ns})")
            break
        except Exception:
            pass
    if not _WMI_VOLT:
        log("WARN: OpenHardwareMonitor/LibreHardwareMonitor 未运行")
    HAS_VOLT = _WMI_VOLT is not None
except ImportError:
    HAS_VOLT = False
    _WMI_VOLT = None
    log("WARN: wmi 模块未安装，无电压信息")


def _get_gpu_info():
    if not HAS_GPU:
        return None
    try:
        util = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
        mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
        temp = pynvml.nvmlDeviceGetTemperature(_GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
        return {
            "name": _GPU_NAME,
            "gpu_percent": util.gpu,
            "mem_total": mem.total,
            "mem_used": mem.used,
            "mem_percent": round(mem.used / mem.total * 100, 1) if mem.total else 0,
            "temp": temp,
        }
    except Exception:
        return None


def _get_voltages():
    """从 WMI 读取所有电压传感器"""
    if not HAS_VOLT or not _WMI_VOLT:
        return None
    try:
        result = {}
        for sensor in _WMI_VOLT.Sensor(SensorType="Voltage"):
            name = sensor.Name
            val = sensor.Value
            if val is None or val == 0:
                continue
            name_lower = name.lower()

            if "vcore" in name_lower and "gpu" not in name_lower:
                result["cpu_vcore"] = round(val, 3)
            elif "gpu" in name_lower and ("vcore" in name_lower or "mv" in name_lower):
                result["gpu_vcore"] = round(val, 3)
            elif "dram" in name_lower or "vdimm" in name_lower:
                result["dram"] = round(val, 3)
            elif "3vcc" in name_lower or "+3.3v" in name_lower or name_lower == "3.3v":
                result["v33"] = round(val, 3)
            elif "5vcc" in name_lower or "+5v" in name_lower or name_lower == "5v":
                result["v5"] = round(val, 3)
            elif "12v" in name_lower:
                result["v12"] = round(val, 3)
            elif "vsb" in name_lower or "standby" in name_lower:
                result["vsb"] = round(val, 3)
            elif "vbat" in name_lower or "cmos" in name_lower:
                result["vbat"] = round(val, 3)
            else:
                key = name[:20].replace(" ", "_").replace(".", "").replace("+", "").lower()
                if key and key not in result:
                    result[key] = round(val, 3)

        return result if result else None
    except Exception as e:
        log(f"电压采集错误: {e}")
        return None


def _fmt_bytes(n):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _get_disk_info():
    if not HAS_WIN32:
        return []
    disks = []
    try:
        for part in psutil.disk_partitions(all=False):
            if part.opts.startswith("ro"):
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "drive": part.mountpoint,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": round(usage.percent, 1),
                })
            except Exception:
                pass
    except Exception:
        pass
    return disks


_last_net = None
_last_net_ts = 0

def _get_net_speed():
    global _last_net, _last_net_ts
    if not HAS_WIN32:
        return {"upload": 0, "download": 0}
    try:
        net = psutil.net_io_counters()
        now = time.time()
        if _last_net is None or now == _last_net_ts:
            _last_net = net
            _last_net_ts = now
            return {"upload": 0, "download": 0}
        dt = now - _last_net_ts
        up = (net.bytes_sent - _last_net.bytes_sent) / dt
        down = (net.bytes_recv - _last_net.bytes_recv) / dt
        _last_net = net
        _last_net_ts = now
        return {"upload": round(up), "download": round(down)}
    except Exception:
        return {"upload": 0, "download": 0}


def _get_battery():
    if not HAS_WIN32:
        return None
    try:
        bat = psutil.sensors_battery()
        if bat is None:
            return None
        return {"percent": bat.percent, "plugged": bat.power_plugged}
    except Exception:
        return None


def _get_system_info():
    """采集系统资源信息"""
    info = {}
    if not HAS_WIN32:
        return info

    try:
        # CPU
        info["cpu_percent"] = round(psutil.cpu_percent(interval=None), 1)
        info["cpu_count"] = psutil.cpu_count(logical=True)
        freq = psutil.cpu_freq()
        if freq:
            info["cpu_freq"] = round(freq.current / 1000, 2)  # GHz

        # 内存
        vm = psutil.virtual_memory()
        info["memory"] = {
            "total": vm.total,
            "used": vm.used,
            "available": vm.available,
            "percent": round(vm.percent, 1),
        }
        sm = psutil.swap_memory()
        info["swap"] = {
            "total": sm.total,
            "used": sm.used,
            "percent": round(sm.percent, 1),
        }

        # 开机时长
        info["boot_time"] = psutil.boot_time()
        info["uptime"] = int(time.time() - psutil.boot_time())

        # 磁盘
        info["disks"] = _get_disk_info()

        # 网速
        info["net"] = _get_net_speed()

        # 电池
        bat = _get_battery()
        if bat:
            info["battery"] = bat

        # 进程数
        info["proc_count"] = len(psutil.pids())
    except Exception as e:
        log(f"系统信息采集错误: {e}")

    # GPU
    gpu = _get_gpu_info()
    if gpu:
        info["gpu"] = gpu

    # 电压
    volt = _get_voltages()
    if volt:
        info["voltages"] = volt

    return info


# ── 音乐播放器检测 ──
_MUSIC_PLAYERS = {
    "spotify": "Spotify", "cloudmusic": "网易云音乐",
    "qqmusic": "QQ音乐", "kugou": "酷狗音乐",
    "foobar2000": "foobar2000", "music.ui": "酷狗音乐",
    "kwmusic": "酷我音乐", "netease": "网易云音乐",
}
_last_player = ""
_last_player_ts = 0

def detect_music_player():
    global _last_player, _last_player_ts
    now = time.time()
    if now - _last_player_ts < 5 and _last_player:
        return _last_player
    _last_player_ts = now
    if not HAS_WIN32: return _last_player
    try:
        for p in psutil.process_iter(["name"]):
            name = (p.info.get("name") or "").lower()
            for key, label in _MUSIC_PLAYERS.items():
                if key in name:
                    _last_player = label
                    return label
    except Exception:
        pass
    return _last_player

# ── LRC 解析 ──
def _parse_lrc(text):
    import re
    lines = re.findall(r'\[(\d{2}):(\d{2})\.(\d+)\](.*)', text)
    r = []
    for m, s, ms, txt in lines:
        t = int(m)*60 + int(s) + int(ms.ljust(3,'0')[:3])/1000
        r.append((t, txt.strip()))
    return sorted(r, key=lambda x: x[0])

def _current_lyric(timeline, ms):
    if not timeline or ms<0: return ""
    sec=ms/1000; cur=""
    for t, txt in timeline:
        if t<=sec: cur=txt
        else: break
    return cur

# ── WS 共享状态 ──
_shared = {
    "song":"","cover":"","duration":"","progress_ms":0,
    "playing":False,"hasSong":False,"lyric_line":"","timeline":[],
}
_shared_lock=threading.Lock()
_ws_ok=False

def _ws_loop():
    global _ws_ok
    try:
        from websocket import create_connection
    except ImportError:
        log("ERROR: websocket-client 未安装")
        return
    while True:
        try:
            log("WS: 连接 localhost:9863/api/ws/lyric ...")
            ws=create_connection("ws://localhost:9863/api/ws/lyric", timeout=5)
            log("WS: 已连接")
            while True:
                raw=ws.recv()
                msg=json.loads(raw)
                ev=msg.get("event",""); d=msg.get("data",{})
                with _shared_lock:
                    if ev=="Track":
                        au=d.get("author","") or d.get("artist","") or ""
                        ti=d.get("title","") or ""
                        _shared["song"]=f"{au} - {ti}" if au and ti else (ti or "")
                        _shared["cover"]=d.get("cover","") or ""
                        _shared["duration"]=d.get("durationHuman","") or ""
                        log(f"WS Track: {_shared['song']}")
                    elif ev=="Lyric":
                        lrc=d.get("lrc","") or d.get("karaokeLyric","") or ""
                        if lrc:
                            if lrc.startswith("{") and "[" in lrc:
                                lrc=lrc[lrc.index("["):]
                            _shared["timeline"]=_parse_lrc(lrc)
                            log(f"WS Lyric: {len(_shared['timeline'])} lines")
                    elif ev=="PlayerPauseState":
                        _shared["hasSong"]=d.get("hasSong",_shared["hasSong"])
                        _shared["playing"]=not d.get("isPaused",True)
                        log(f"WS State: hasSong={_shared['hasSong']} playing={_shared['playing']}")
                    elif ev=="PlayerProgress":
                        _shared["progress_ms"]=d.get("progress",_shared["progress_ms"])
                if not _ws_ok and _shared["song"]:
                    _ws_ok=True; log("WS: 就绪，开始上报")
                with _shared_lock:
                    _shared["lyric_line"]=_current_lyric(_shared["timeline"], _shared["progress_ms"])
        except Exception as e:
            _ws_ok=False
            log(f"WS 断开: {e}，3s 后重连...")
        time.sleep(3)

threading.Thread(target=_ws_loop, daemon=True).start()

# ── TCP 上报 ──
# ── 截屏功能 (需要 Pillow) ──
try:
    from PIL import ImageGrab, Image
    import io
    import base64
    HAS_SHOT = True
except ImportError:
    HAS_SHOT = False
    log("WARN: Pillow 未安装, 截屏功能不可用 (pip install Pillow)")


def _take_screenshot() -> str:
    """截取全屏, 返回 base64 JPEG 字符串"""
    if not HAS_SHOT:
        return ""
    try:
        img = ImageGrab.grab(all_screens=True)
        # 缩放到合理尺寸 (宽度最大 1920)
        w, h = img.size
        if w > 1920:
            ratio = 1920 / w
            img = img.resize((1920, int(h * ratio)), Image.LANCZOS)
        # 转 JPEG 压缩
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        log(f"截屏完成: {img.size[0]}x{img.size[1]}, {len(b64)} chars")
        return b64
    except Exception as e:
        log(f"截屏错误: {e}")
        return ""


_last_good_music={}

def connect_tcp():
    while True:
        try:
            sock=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((SERVER, PORT))
            log(f"TCP: 已连接 {SERVER}:{PORT}")
            sock.sendall(f"AUTH {AUTH_KEY}\n".encode())
            return sock
        except Exception as e:
            log(f"TCP: 连接失败 {e}，3s 重试...")
            time.sleep(3)

def run():
    global _last_good_music
    log("=== PC 状态上报 v3 (TCP) ===")
    if HAS_GPU: log(f"GPU 监控: 启用 ({_GPU_NAME})")
    if HAS_VOLT: log("电压监控: 启用 (OpenHardwareMonitor WMI)")
    if not HAS_WIN32: log("WARN: pywin32/psutil 未安装，仅基础信息")
    sock=connect_tcp()

    while True:
        try:
            # 采集窗口
            title,proc=get_window_title()
            player=detect_music_player()
            music={}
            if _ws_ok:
                with _shared_lock:
                    music={
                        "song":_shared["song"],"cover":_shared["cover"],
                        "duration":_shared["duration"],"progress_ms":_shared["progress_ms"],
                        "playing":_shared["playing"],"hasSong":_shared["hasSong"],
                        "lyric_line":_shared["lyric_line"],
                    }
                music["player"]=player

            if music.get("song"):
                _last_good_music=music.copy()
            elif _last_good_music:
                music=_last_good_music.copy()

            # 构建
            data={"hostname":socket.gethostname()}
            if title: data["window"]=title
            if proc: data["app"]=proc
            if music: data["music"]=music

            # 系统信息
            sys_info = _get_system_info()
            data.update(sys_info)

            # TCP 发送
            payload=json.dumps(data, ensure_ascii=False)+"\n"
            sock.sendall(payload.encode("utf-8"))

            # 检查服务器是否下发命令 (非阻塞, 1s 内检查)
            import select
            rlist, _, _ = select.select([sock], [], [], 1.0)
            if rlist:
                try:
                    cmd = sock.recv(4096).decode().strip()
                    if cmd == "CMD:SHOT":
                        log("收到截屏命令")
                        shot_b64 = _take_screenshot()
                        if shot_b64:
                            header = f"SHOT:{len(shot_b64)}\n"
                            sock.sendall(header.encode("utf-8") + shot_b64.encode("utf-8"))
                            log(f"截屏结果已回传 ({len(shot_b64)} chars)")
                        else:
                            sock.sendall(b"SHOT:0\n")
                            log("截屏失败, 已回传空结果")
                except Exception as e:
                    log(f"处理命令错误: {e}")
            else:
                time.sleep(0.1)

        except (BrokenPipeError, ConnectionResetError, OSError):
            log("TCP: 连接断开，重连...")
            try: sock.close()
            except: pass
            sock=connect_tcp()
        except Exception as e:
            log(f"ERR: {e}")
            traceback.print_exc()
            time.sleep(1)

if __name__=="__main__":
    run()
