"""
PC 状态上报 v3 — TCP 长连接 + 详细系统信息
依赖: pip install pywin32 psutil websocket-client
可选: pip install pynvml (NVIDIA GPU), wmi (电压)
"""
import json, time, os, socket, threading, traceback, sys

SERVER = os.environ.get("BOT_SERVER", "01240820.xyz")
PORT = int(os.environ.get("BOT_PC_PORT", "58890"))
AUTH_KEY = os.environ.get("BOT_PC_KEY", "huanmeng_pc_2026")

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
    if not HAS_WIN32: return {}, "", ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid) if pid else None
        info = {}
        if proc:
            try:
                mem = proc.memory_info()
                info["proc_handle_count"] = proc.num_handles()
                info["proc_mem_rss"] = mem.rss
                info["proc_mem_vms"] = mem.vms
                info["proc_cpu_percent"] = round(proc.cpu_percent(interval=0), 1)
                info["proc_pid"] = pid
            except Exception:
                pass
        return info, title or "", proc.name() if proc else ""
    except Exception:
        return {}, "", ""

# ── FPS 检测 (DWM 帧计时) ──
_fps_history = []  # 最近 5 次帧时间

def get_fps():
    """DXGI 帧统计 — 读取实际渲染帧率（非显示器刷新率）"""
    try:
        import ctypes
        from ctypes import wintypes, byref, sizeof, POINTER, c_void_p
        
        # ── DXGI 接口定义 ──
        dxgi = ctypes.windll.dxgi
        
        IID_IDXGIFactory = ctypes.c_ubyte * 16
        IID_IDXGISwapChain = ctypes.c_ubyte * 16
        
        # IDXGISwapChain::GetFrameStatistics 结构
        class DXGI_FRAME_STATISTICS(ctypes.Structure):
            _fields_ = [
                ("PresentCount", ctypes.c_uint),
                ("PresentRefreshCount", ctypes.c_uint),
                ("SyncRefreshCount", ctypes.c_uint),
                ("SyncQPCTime", ctypes.c_longlong),
                ("SyncGPUTime", ctypes.c_longlong),
            ]
        
        class DXGI_RATIONAL(ctypes.Structure):
            _fields_ = [("Numerator", ctypes.c_uint), ("Denominator", ctypes.c_uint)]
        
        class DXGI_MODE_DESC(ctypes.Structure):
            _fields_ = [
                ("Width", ctypes.c_uint), ("Height", ctypes.c_uint),
                ("RefreshRate", DXGI_RATIONAL),
                ("Format", ctypes.c_uint),
                ("ScanlineOrdering", ctypes.c_uint),
                ("Scaling", ctypes.c_uint),
            ]
        
        # CreateDXGIFactory
        factory = c_void_p()
        hr = dxgi.CreateDXGIFactory(
            ctypes.c_char_p(b"\x7b\x71\x66\x3c\xb0\x60\x4f\x70\xb7\xd7\x05\x7a\xb0\x4e\x85\xee"),
            byref(factory)
        )
        if hr != 0:
            return 0
        
        # EnumAdapters → 第一个显卡
        adapter = c_void_p()
        vtbl_adapter = POINTER(c_void_p)()
        
        vtable = ctypes.cast(factory, POINTER(POINTER(c_void_p))).contents
        _EnumAdapters = ctypes.cast(vtable[7], ctypes.CFUNCTYPE(ctypes.c_long, c_void_p, ctypes.c_uint, POINTER(c_void_p)))
        hr = _EnumAdapters(factory, 0, byref(adapter))
        if hr != 0:
            return 0
            
        # EnumOutputs → 显示器 0
        vtable_a = ctypes.cast(adapter, POINTER(POINTER(c_void_p))).contents
        _EnumOutputs = ctypes.cast(vtable_a[7], ctypes.CFUNCTYPE(ctypes.c_long, c_void_p, ctypes.c_uint, POINTER(c_void_p)))
        output = c_void_p()
        hr = _EnumOutputs(adapter, 0, byref(output))
        if hr != 0:
            return 0
            
        # GetFrameStatistics
        vtable_o = ctypes.cast(output, POINTER(POINTER(c_void_p))).contents
        _GetFrameStatistics = ctypes.cast(vtable_o[16], ctypes.CFUNCTYPE(ctypes.c_long, c_void_p, POINTER(DXGI_FRAME_STATISTICS)))
        
        stats = DXGI_FRAME_STATISTICS()
        hr = _GetFrameStatistics(output, byref(stats))
        
        # 释放 COM 对象
        _Release = ctypes.cast(vtable_o[2], ctypes.CFUNCTYPE(ctypes.c_ulong, c_void_p))
        _Release(output)
        _Release_a = ctypes.cast(vtable_a[2], ctypes.CFUNCTYPE(ctypes.c_ulong, c_void_p))
        _Release_a(adapter)
        _Release_f = ctypes.cast(vtable[2], ctypes.CFUNCTYPE(ctypes.c_ulong, c_void_p))
        _Release_f(factory)
        
        if hr != 0 or stats.SyncRefreshCount == 0:
            return 0
            
        global _fps_history
        now = time.time()
        _fps_history.append((now, stats.SyncRefreshCount))
        if len(_fps_history) > 5:
            _fps_history = _fps_history[-5:]

        if len(_fps_history) >= 2:
            t0, f0 = _fps_history[0]
            t1, f1 = _fps_history[-1]
            dt = t1 - t0
            if dt > 0.5 and f1 > f0:
                fps = (f1 - f0) / dt
                return round(fps)

        return 0
    except Exception:
        return 0

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
_last_music_query=0

def _query_music():
    """按需连接 WS 获取当前歌曲（不常驻）"""
    try:
        from websocket import create_connection
    except ImportError:
        return {}
    try:
        ws=create_connection("ws://localhost:9863/api/ws/lyric", timeout=3)
        ws.settimeout(2)
        result={}
        start=time.time()
        while time.time()-start < 2:
            raw=ws.recv()
            msg=json.loads(raw)
            ev=msg.get("event",""); d=msg.get("data",{})
            ev=ev.lower()
            if ev=="track":
                au=d.get("author","") or d.get("artist","") or ""
                ti=d.get("title","") or ""
                result["song"]=f"{au} - {ti}" if au and ti else (ti or "")
                result["cover"]=d.get("cover","") or ""
                result["duration"]=d.get("durationHuman","") or d.get("duration","")
            elif ev=="state":
                result["playing"]=d.get("playing",False)
                result["hasSong"]=d.get("hasSong",False)
                result["progress_ms"]=d.get("progress",0)
            elif ev=="lyric":
                lrc=d.get("lrc","") or d.get("karaokeLyric","") or ""
                result["timeline"]=parse_lyric_lines(lrc) if lrc else []
                result["lyric_line"]=get_lyric_line(result["timeline"], result.get("progress_ms",0))
            if result.get("song") and result.get("playing") is not False:
                break
        ws.close()
        return result
    except Exception:
        return {}


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
    # 先解析 IP，避免 getaddrinfo 在循环中反复失败
    ip = None
    for i in range(10):
        try:
            ip = socket.getaddrinfo(SERVER, PORT, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
            break
        except Exception:
            time.sleep(3)
    if not ip:
        log(f"TCP: DNS 解析失败 {SERVER}，退出")
        sys.exit(1)

    while True:
        try:
            sock=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((ip, PORT))
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
    # KOOK: 第二路 TCP → 62002
    KOOK_PORT = 62002
    sock_kook = None
    try:
        sock_kook = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock_kook.settimeout(5)
        kook_ip = socket.getaddrinfo(SERVER, KOOK_PORT, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        sock_kook.connect((kook_ip, KOOK_PORT))
        sock_kook.sendall(f"AUTH {AUTH_KEY}\n".encode())
        log(f"TCP: 已连接 {SERVER}:{KOOK_PORT} (KOOK)")
    except Exception as e:
        log(f"TCP: KOOK 端口 {KOOK_PORT} 连接失败 {e}，跳过")
        sock_kook = None

    def send_both(data: bytes):
        """同时发给 QQ 和 KOOK"""
        try:
            sock.sendall(data)
        except: pass
        if sock_kook:
            try:
                sock_kook.sendall(data)
            except: pass

    while True:
        try:
            # 采集窗口
            proc_info, title, proc = get_window_title()
            fps = get_fps()
            player=detect_music_player()
            # 音乐：每 30s 轮询一次 WS，用缓存
            global _last_music_query
            music={}
            if time.time()-_last_music_query > 30:
                raw=_query_music()
                with _shared_lock:
                    if raw: _shared.update(raw)
                _last_music_query=time.time()
            with _shared_lock:
                if _shared.get("song"):
                    music={
                        "song":_shared["song"],"cover":_shared["cover"],
                        "duration":_shared["duration"],"progress_ms":_shared["progress_ms"],
                        "playing":_shared["playing"],"hasSong":_shared["hasSong"],
                        "lyric_line":_shared["lyric_line"],
                        "player":player,
                    }

            # 有歌就更新缓存
            if music.get("song"):
                _last_good_music=music.copy()
            elif _last_good_music:
                music=_last_good_music.copy()

            # 构建
            data={"hostname":socket.gethostname()}
            if title: data["window"]=title
            if proc: data["app"]=proc
            if proc_info: 
                data["app_detail"]=proc_info
                data["app_handles"]=proc_info.get("proc_handle_count", 0)
                data["app_mem_mb"]=round(proc_info.get("proc_mem_rss", 0) / 1048576, 1)
            if fps: data["fps"]=fps
            if music: data["music"]=music

            # 系统信息
            sys_info = _get_system_info()
            data.update(sys_info)

            # TCP 发送
            payload=json.dumps(data, ensure_ascii=False)+"\n"
            send_both(payload.encode("utf-8"))

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
                    elif cmd == "CMD:MUSIC":
                        log("收到音乐查询命令")
                        raw = _query_music()
                        if raw:
                            resp = json.dumps(raw, ensure_ascii=False) + "\n"
                            sock.sendall(f"MUSIC:{len(resp)}\n".encode() + resp.encode())
                        else:
                            sock.sendall(b"MUSIC:0\n")
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
