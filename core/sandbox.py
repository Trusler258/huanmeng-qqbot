"""
core/sandbox.py — 沙箱代码执行（Huanmeng 2.0）

在服务器独立临时目录中真实执行用户/LLM 生成的代码，返回真实运行输出与产物文件。
用于根治「声称已执行但实际只发文件」的幻觉：执行结果一律来自真实的子进程输出。

安全边界（不依赖 Prompt 约束，全部做成硬限制）：
- 独立临时目录：tempfile.mkdtemp(prefix="bot_sandbox_")，执行后清理
- 超时强杀：asyncio.wait_for + proc.kill()
- 限内存 / 限 CPU：Linux 用 resource.setrlimit 在 preexec_fn 中限制（Windows 降级跳过）
- 输出截断：stdout/stderr 各截断到 MAX_OUTPUT，避免刷屏
- 产物收集：执行后扫描目录，把生成的附件打包返回，脚本自身不返回
- 默认不开放网络（无代理、无特权）；shell 命令仅管理员/审批后可用（由上层裁决）
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from pathlib import Path

from core.logger import get_logger

logger = get_logger("sandbox")

# 单次运行最大秒数
DEFAULT_TIMEOUT: float = 10.0
# 内存上限（MB），Linux 生效
DEFAULT_MEM_MB: int = 256
# 单路输出最大字符数（截断提示放在末尾）
MAX_OUTPUT: int = 1500
# 最大产物数 / 单个产物最大字节（避免把磁盘整个打包）
MAX_ARTIFACTS: int = 10
MAX_ARTIFACT_BYTES: int = 5 * 1024 * 1024


def _limit_preexec(mem_mb: int, cpu_sec: int) -> callable | None:
    """构造限制子进程资源的 preexec_fn（仅 Linux；Windows 返回 None）。"""
    if os.name != "posix":
        return None
    import resource

    def _apply() -> None:
        try:
            # 独立进程组：超时按组杀，避免隔离包装层留下孤儿进程
            os.setsid()
        except Exception:
            pass
        try:
            # 地址空间上限（内存）
            resource.setrlimit(resource.RLIMIT_AS,
                               (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
        except Exception:
            pass
        try:
            # CPU 时间上限（秒）
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_sec, cpu_sec + 5))
        except Exception:
            pass

    return _apply


# ── 真隔离（Linux + root）：独立 mount / network namespace ──────────
#
# 为什么必须做（v2.3.63 教训）：正则只扫**源码文本**，而路径能在运行期算出来 ——
#   p = os.path.join(HOME, 'bot', 'config', '.' + 'en' + 'v')
# 源码里没有 "/root/"、也没有 ".env" 字面量，可它实测读到了整份 .env（1230 字节，
# 装着 DeepSeek Key / CF Token / Steam 令牌）。字符串黑名单对"会拼字符串的对手"
# 天然无效，真正的边界只能由内核给：
#   unshare -m → 独立 mount namespace，tmpfs 盖住含密钥的目录，代码眼里的 /root 是空的
#   unshare -n → 独立 network namespace，网卡全无（lo 也 down），彻底断外联
# 对正常代码零影响：python3/g++ 都在 /usr 下，临时目录在 /tmp，宿主机 /root 不受影响。
_MASK_DIRS: tuple[str, ...] = ("/root", "/www")


_UNSHARE_OK: bool | None = None


def _unshare_available() -> bool:
    """真跑一次 unshare 探测可用性，结果缓存。

    ⚠️ 为什么必须真探测：`unshare` 存在 + euid==0 **不代表能用**。systemd 单元若设了
    `RestrictNamespaces=`、收了 CapabilityBoundingSet（少 CAP_SYS_ADMIN）、或开了
    NoNewPrivileges，unshare 会在**调用时才失败** —— 那样 run_code 会整个变成报错。
    探测失败就静默降级为无隔离（工具本身必须可用），并记一条 warning 便于发现。
    """
    global _UNSHARE_OK
    if _UNSHARE_OK is not None:
        return _UNSHARE_OK
    _UNSHARE_OK = False
    if os.name != "posix":
        return _UNSHARE_OK
    try:
        if os.geteuid() != 0:
            return _UNSHARE_OK
    except AttributeError:
        return _UNSHARE_OK
    if shutil.which("unshare") is None:
        return _UNSHARE_OK
    try:
        import subprocess
        probe = subprocess.run(["unshare", "-m", "-n", "true"],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=3)
        _UNSHARE_OK = probe.returncode == 0
    except Exception:
        _UNSHARE_OK = False
    if not _UNSHARE_OK:
        logger.warning("沙箱 namespace 隔离不可用（unshare 探测失败），已降级为无隔离运行")
    return _UNSHARE_OK


def _unshare_prefix() -> list[str]:
    """namespace 隔离前缀命令；不可用（非 Linux / 非 root / unshare 探测失败）时返回 []。"""
    if not _unshare_available():
        return []
    mounts = "; ".join(f"mount -t tmpfs none {d} 2>/dev/null" for d in _MASK_DIRS)
    # `exec "$@"` 让真正的命令接管这个进程（不多留一层 shell，超时按进程组杀才准）
    return ["unshare", "-m", "-n", "sh", "-c", f'{mounts}; exec "$@"', "sh"]


def isolation_mode() -> str:
    """当前沙箱隔离模式，供自检/测试打印与断言。"""
    if _chroot_available():
        return "unshare(-m,-n)+chroot"
    return "unshare(-m,-n)" if _unshare_prefix() else "none"


# ── chroot 根隔离（v2.3.81）：rm -rf /* 只砸沙箱，砸不到真实系统 ──
#
# 原理：unshare -m 的 mount namespace 只隔离挂载点，不隔离文件内容——
# 沙箱里 `rm -rf /*` 会真删盘（实测沙箱内能 touch 真实 /）。
# chroot 后进程的 / 就是沙箱目录：rm -rf /* 只删沙箱里的临时文件，
# 真实系统靠 /usr 只读绑定保护（ro bind 砸不动）。

_CHROOT_OK: bool | None = None

_CHROOT_SCRIPT = (
    'SB="$1"; shift\n'
    'mkdir -p "$SB/usr" "$SB/dev" "$SB/proc" "$SB/work" "$SB/tmp" "$SB/etc"\n'
    'ln -sfn usr/bin "$SB/bin"; ln -sfn usr/lib "$SB/lib"; ln -sfn usr/lib64 "$SB/lib64"\n'
    'export PATH=/usr/sbin:/usr/bin:/sbin:/bin; CHROOT_BIN=$(command -v chroot || echo /usr/sbin/chroot)\n'
    'mount --rbind /usr "$SB/usr" 2>/dev/null || true\n'
    'mount -o remount,bind,ro "$SB/usr" 2>/dev/null || true\n'
    # ⚠️ /dev 绝不能 rbind（rw 绑定会让 rm -rf /* 删掉宿主 /dev 内容，2026-10-04 实测）：
    # mknod 只在沙箱里建设备节点，宿主 /dev 完全不接触
    'mknod -m 666 "$SB/dev/null" c 1 3 2>/dev/null; mknod -m 666 "$SB/dev/zero" c 1 5 2>/dev/null\n'
    'mknod -m 666 "$SB/dev/urandom" c 1 9 2>/dev/null; mknod -m 666 "$SB/dev/random" c 1 8 2>/dev/null\n'
    'mknod -m 666 "$SB/dev/tty" c 5 0 2>/dev/null\n'
    'mount -t proc none "$SB/proc" 2>/dev/null || true\n'
    'if [ -x "$SB/usr/bin/python3" ] || [ -x "$SB/usr/bin/g++" ]; then\n'
    '  "$CHROOT_BIN" "$SB" /bin/sh -c \'cd /work && exec "$@"\' sh "$@"\n'
    'else\n'
    '  cd "$SB/work" 2>/dev/null || true\n'
    '  exec "$@"\n'
    'fi\n'
)


def _chroot_available() -> bool:
    """chroot 根隔离可用性：unshare 可用 + chroot 存在（root）。结果缓存。"""
    global _CHROOT_OK
    if _CHROOT_OK is not None:
        return _CHROOT_OK
    _CHROOT_OK = False
    if not _unshare_available():
        return _CHROOT_OK
    if shutil.which("chroot") is None:
        return _CHROOT_OK
    _CHROOT_OK = True
    return _CHROOT_OK


def _chroot_wrap(root: Path, argv: list[str]) -> list[str]:
    """把命令包进 chroot 沙箱：/ 是沙箱目录，/usr 只读绑定，/work 是工作区。"""
    return ["unshare", "-m", "-n", "sh", "-c", _CHROOT_SCRIPT, "sh", str(root), *argv]


def _kill_tree(proc) -> None:
    """超时终止：优先杀整个进程组（隔离模式下包装层会 exec 掉自己，按组杀更稳）。"""
    pid = getattr(proc, "pid", None)
    if os.name == "posix" and pid:
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            return
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def _safe_env(cwd: Path | None = None) -> dict[str, str]:
    """最小化子进程环境变量：不把 bot 自己的凭据继承给沙箱代码。

    子进程默认继承父进程 env，于是被测代码一句 `import os; print(os.environ)`
    就能把 DEEPSEEK_KEY / STEAM_PROXY_TOKEN / ZHIPU_KEY 之类打进群里。
    这里只保留运行必需的几项；Windows 额外保留系统必需项（缺 SystemRoot 时
    部分程序会直接启动失败）。

    ⚠️ HOME 必须指向沙箱目录，**不能透传真实的 /root**：一旦透传，
    `os.path.expanduser('~')` 就直接指到 bot 目录，再拼上 'bot/config/…'
    便绕过所有字面量正则（这正是 v2.3.62 的漏洞成因）。给 cwd，让 ~ 落在沙箱内且可写。
    """
    home = str(cwd) if cwd else tempfile.gettempdir()
    env: dict[str, str] = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": home,
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.name == "nt":
        for k in ("SystemRoot", "SystemDrive", "COMSPEC", "PATHEXT",
                  "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
                  "APPDATA", "LOCALAPPDATA"):
            v = os.environ.get(k)
            if v:
                env[k] = v
    # 临时目录（g++ / Python 编译缓存需要）
    for k in ("TMPDIR", "TMP", "TEMP"):
        v = os.environ.get(k)
        if v:
            env[k] = v
    return env


async def _run_proc(cmd: list[str], cwd: Path, timeout: float, mem_mb: int,
                    stdin_data: str = "", max_output: int = MAX_OUTPUT,
                    chroot_root: Path | None = None,
                    workdir: Path | None = None) -> dict:
    """通用子进程执行：限时、限资源、清洗 env、截断输出。返回 dict。

    chroot_root/workdir（v2.3.81）：给出时命令包进 chroot 沙箱——进程的 / 是
    沙箱目录，/usr 只读绑定，/work 为工作区，`rm -rf /*` 只砸沙箱。workdir 是
    chroot 内工作区的 host 侧路径（无 chroot 时作为 cwd 回退）。
    """
    if chroot_root is not None and _chroot_available():
        cmd = _chroot_wrap(chroot_root, list(cmd))
        cwd = Path("/")
    elif workdir is not None:
        cwd = workdir
    kwargs: dict = {
        "cwd": str(cwd),
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "env": _safe_env(cwd),
    }
    if stdin_data:
        kwargs["stdin"] = asyncio.subprocess.PIPE
    preexec = _limit_preexec(mem_mb, int(timeout) + 5)
    if preexec is not None:
        kwargs["preexec_fn"] = preexec
    # 内核级隔离：chroot 路径的 wrapper 自带 unshare（见 _chroot_wrap），
    # 不再叠加旧的 namespace 前缀（避免双重包装）
    if chroot_root is None or not _chroot_available():
        cmd = _unshare_prefix() + list(cmd)
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(stdin_data.encode("utf-8") if stdin_data else None),
                timeout=timeout,
            )
            timed_out = False
        except asyncio.TimeoutError:
            timed_out = True
            _kill_tree(proc)
            try:
                await proc.wait()
            except Exception:
                pass
            stdout, stderr = b"", "[timeout] 运行超时，已强制终止".encode("utf-8")
    except FileNotFoundError as e:
        return {"returncode": -1, "stdout": "",
                "stderr": f"运行环境缺失: {e}", "timed_out": False}
    except Exception as e:
        return {"returncode": -1, "stdout": "",
                "stderr": f"执行异常: {e}", "timed_out": False}

    def _cut(b: bytes, max_output: int = MAX_OUTPUT) -> str:
        text = b.decode("utf-8", errors="replace")
        if len(text) > max_output:
            # 保留头尾：末尾常是最终结果（如 uptime / 退出信息 / 报错栈），不能整段丢
            total = len(text)
            head = max_output * 2 // 3
            tail = max_output - head - 1
            text = (text[:head] + f"\n…(输出过长，已截断 {total} 字符，末尾保留)…\n"
                    + text[-tail:])
        return text

    return {
        "returncode": getattr(proc, "returncode", -1) if not timed_out else -1,
        "stdout": _cut(stdout or b"", max_output),
        "stderr": _cut(stderr or b"", max_output),
        "timed_out": timed_out,
    }


def _pick_python() -> str:
    """选择可用的 Python 解释器：优先 python3（Linux 服务器），
    Windows 商店别名 stub（WindowsApps）不可运行，跳过回退 python。"""
    for name in ("python3", "python"):
        path = shutil.which(name)
        if not path:
            continue
        if os.name == "nt" and "WindowsApps" in path:
            continue
        return path
    return "python3"


async def run_python(code: str, timeout: float = DEFAULT_TIMEOUT,
                     mem_mb: int = DEFAULT_MEM_MB,
                     stdin_data: str = "", cwd: Path | None = None,
                     max_output: int = MAX_OUTPUT) -> dict:
    """在沙箱目录执行 Python 代码，返回运行结果。"""
    root = cwd or Path(tempfile.mkdtemp(prefix="bot_sandbox_"))
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    script = work / "main.py"
    script.write_text(code or "", encoding="utf-8")
    cmd = [_pick_python(), "main.py"]
    result = await _run_proc(cmd, root, timeout, mem_mb, stdin_data, max_output,
                             chroot_root=root, workdir=work)
    result["tmp_dir"] = str(root)
    return result


async def compile_and_run_cpp(files: dict[str, str], timeout: float = DEFAULT_TIMEOUT,
                              mem_mb: int = DEFAULT_MEM_MB,
                              stdin_data: str = "", cwd: Path | None = None,
                              max_output: int = MAX_OUTPUT) -> dict:
    """在沙箱目录编译并运行 C++（g++）。files: {文件名: 内容}。"""
    root = cwd or Path(tempfile.mkdtemp(prefix="bot_sandbox_"))
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        out = work / fname
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content or "", encoding="utf-8")
    if shutil.which("g++") is None:
        return {"returncode": -1, "stdout": "",
                "stderr": "服务器未安装 g++，无法编译 C++", "timed_out": False,
                "tmp_dir": str(root)}
    comp = await _run_proc(
        ["g++", "-std=c++14", "-O2", "-o", "a.out"] + [f for f in files],
        root, timeout, mem_mb, max_output=max_output,
        chroot_root=root, workdir=work)
    if comp["returncode"] != 0:
        comp["tmp_dir"] = str(root)
        comp["stdout"] = "[编译失败]\n" + (comp["stderr"] or "")
        return comp
    run = await _run_proc(["./a.out"], root, timeout, mem_mb, stdin_data, max_output,
                          chroot_root=root, workdir=work)
    run["tmp_dir"] = str(root)
    return run


async def run_shell(command: str, timeout: float = DEFAULT_TIMEOUT,
                    mem_mb: int = DEFAULT_MEM_MB,
                    cwd: Path | None = None,
                    max_output: int = MAX_OUTPUT) -> dict:
    """执行 shell 命令（终端模拟，如 `cd / && ls -l`）。仅管理员/审批后调用。"""
    root = cwd or Path(tempfile.mkdtemp(prefix="bot_sandbox_"))
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        cmd = ["bash", "-c", command]
    else:
        cmd = ["cmd", "/c", command]
    result = await _run_proc(cmd, root, timeout, mem_mb, max_output=max_output,
                             chroot_root=root, workdir=work)
    result["tmp_dir"] = str(root)
    return result


# ── 产物收集 ──────────────────────────────────────────────

_SKIP_NAMES = {"main.py", "main.cpp", "a.out"}
_SKIP_EXTS = {".pyc", ".o", ".obj"}
# chroot 脚手架顶层目录（usr 为只读绑定，不算产物）
_SCAFFOLD_TOP = {"usr", "dev", "proc", "bin", "lib", "lib64", "etc", "tmp"}


def collect_artifacts(tmp_dir: str | Path) -> list[Path]:
    """扫描沙箱目录中用户代码生成的文件（排除脚本自身/编译产物/缓存），按大小降序。"""
    root = Path(tmp_dir)
    out: list[Path] = []
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if p.name in _SKIP_NAMES or p.suffix in _SKIP_EXTS:
            continue
        # chroot 脚手架目录（usr 绑定/dev/proc 等）不算产物
        if rel.parts and rel.parts[0] in _SCAFFOLD_TOP:
            continue
        if "__pycache__" in rel.parts or rel.name.startswith("."):
            continue
        try:
            if p.stat().st_size == 0:
                continue
            if p.stat().st_size > MAX_ARTIFACT_BYTES:
                continue
        except OSError:
            continue
        out.append(p)
    out.sort(key=lambda p: p.stat().st_size, reverse=True)
    return out[:MAX_ARTIFACTS]


def cleanup(tmp_dir: str | Path) -> None:
    """删除沙箱临时目录（执行后调用）。"""
    try:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)
    except Exception:
        pass


def safe_display_path(path: Path) -> str:
    """产物相对路径，用于消息展示。"""
    try:
        return str(path.relative_to(path.parents[-2])) if len(path.parts) > 2 else path.name
    except Exception:
        return path.name


# ── 兼容包装：返回字符串而非 dict（保持与旧版 QQ sandbox 的接口兼容）──

async def run_python_str(code: str, timeout: float = DEFAULT_TIMEOUT,
                         max_output: int = MAX_OUTPUT, **kwargs) -> str:
    """执行 Python 代码，返回格式化字符串（兼容旧接口）。"""
    result = await run_python(code, timeout=timeout, max_output=max_output, **kwargs)
    return _dict_to_str(result)


async def run_shell_str(command: str, timeout: float = DEFAULT_TIMEOUT,
                        max_output: int = MAX_OUTPUT, **kwargs) -> str:
    """执行 shell 命令，返回格式化字符串（兼容旧接口）。"""
    result = await run_shell(command, timeout=timeout, max_output=max_output, **kwargs)
    return _dict_to_str(result)


async def compile_and_run_cpp_str(files: dict, timeout: float = DEFAULT_TIMEOUT,
                                  max_output: int = MAX_OUTPUT, stdin_data: str = "", **kwargs) -> str:
    """编译并运行 C++，返回格式化字符串（兼容旧接口）。"""
    result = await compile_and_run_cpp(files, timeout=timeout, max_output=max_output,
                                       stdin_data=stdin_data, **kwargs)
    return _dict_to_str(result)


def _dict_to_str(result: dict) -> str:
    """将 dict 执行结果转为可读字符串。"""
    if result.get("timed_out"):
        return f"[超时] 执行超时，已强制终止"
    if result["returncode"] != 0:
        err = result.get("stderr", "")
        return f"[执行失败] {err}" if err else f"[执行失败] 退出码 {result['returncode']}"
    return result.get("stdout", "") or "[无输出]"


def collect_artifacts_str(tmp_dir) -> list[str]:
    """收集产物，返回字符串路径列表（兼容旧接口）。"""
    paths = collect_artifacts(tmp_dir)
    return [str(p) for p in paths]