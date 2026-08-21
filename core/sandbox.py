"""
沙箱真实执行（移植自 huanmeng-kook-bot core/sandbox.py 的思路，适配 qqbot）

供插件 ctx.sandbox 使用：py / cpp / shell 真实执行 + 产物收集 + 清理。
安全策略沿用 qqbot core/tools.py 的 _python_eval 黑名单思路：
- 静态正则拦截危险 import/调用（os/sys/subprocess/网络/文件等）
- 最小化环境变量
- 严格超时 + 输出截断（保头尾折叠中间，避免截断丢尾部结果）

所有函数失败返回结构化错误文本，不抛异常。
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from core.logger import get_logger

logger = get_logger("sandbox")

_FORBIDDEN_RE = re.compile(
    r'\b(?:import|from)\s+(?:os|sys|subprocess|shutil|socket|urllib|http'
    r'|pathlib|ctypes|pickle|marshal|tempfile|glob|platform|inspect'
    r'|importlib|threading|multiprocessing|asyncio|signal|resource'
    r'|pty|builtins)\b'
    r'|\b(?:__import__|exec|eval|compile|open|globals|locals|vars|input'
    r'|getattr|setattr|delattr)\s*\('
    r'|os\.system\s*\('
    r'|subprocess\.'
    r'|__class__|__subclasses__|__bases__|__mro__'
    r'|__globals__|__builtins__|__code__|__func__',
    re.MULTILINE,
)

_SAFE_ENV = {
    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", "/tmp"),
    "LANG": "en_US.UTF-8",
    "LC_ALL": "en_US.UTF-8",
}


def _truncate(text: str, max_output: int) -> str:
    """保留头尾、折叠中间（避免只保头丢尾导致 LLM 编造末尾结果）。"""
    if len(text) <= max_output:
        return text
    head = max_output // 2
    tail = max_output - head
    return text[:head] + f"\n...[中间省略 {len(text) - max_output} 字符]...\n" + text[-tail:]


async def run_python(code: str, timeout: float = 15.0, max_output: int = 1500) -> str:
    """执行 Python 代码，返回 stdout（失败返回错误文本）。"""
    code = (code or "").strip()
    if not code:
        return "[错误] 代码为空"
    if _FORBIDDEN_RE.search(code):
        return "[错误] 代码包含禁止操作（文件/系统/网络访问被禁止）"
    if len(code) > 8000:
        return "[错误] 代码过长，最大 8000 字符"
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_SAFE_ENV,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        if proc.returncode != 0:
            return f"[执行失败] {_truncate(err or '程序异常退出', max_output)}"
        if not out and err:
            return f"[执行失败] {_truncate(err, max_output)}"
        return _truncate(out or "[无输出]", max_output)
    except asyncio.TimeoutError:
        proc.kill()
        return f"[超时] 执行超过 {timeout:.0f} 秒"
    except Exception as e:
        return f"[错误] {e}"


async def run_shell(command: str, timeout: float = 15.0, max_output: int = 1500) -> str:
    """执行 shell 命令，返回 stdout（失败返回错误文本）。"""
    command = (command or "").strip()
    if not command:
        return "[错误] 命令为空"
    # shell 是高风险通道，禁止明显危险的命令
    if re.search(r'\b(?:rm\s+-rf|mkfs|dd\s+of=|shutdown|reboot|:\(\)\s*\{|curl|wget)\b', command):
        return "[错误] 命令包含危险操作，已拦截"
    if len(command) > 2000:
        return "[错误] 命令过长"
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_SAFE_ENV,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        if proc.returncode != 0:
            return f"[执行失败] {_truncate(err or '退出码 %d' % proc.returncode, max_output)}"
        if not out and err:
            return f"[执行失败] {_truncate(err, max_output)}"
        return _truncate(out or "[无输出]", max_output)
    except asyncio.TimeoutError:
        proc.kill()
        return f"[超时] 执行超过 {timeout:.0f} 秒"
    except Exception as e:
        return f"[错误] {e}"


async def compile_and_run_cpp(files: dict, timeout: float = 30.0,
                              max_output: int = 1500, stdin_data: str = "") -> str:
    """编译并运行 C++（files: {文件名: 源码}）。返回运行输出或错误。"""
    if not files or not all(isinstance(files.get(k), str) and files[k].strip() for k in files):
        return "[错误] C++ 源文件为空"
    if shutil.which("g++") is None:
        return "[编译失败] 服务器未安装 g++"
    tmp = Path(tempfile.mkdtemp(prefix="bot_cpp_"))
    try:
        for fname, code in files.items():
            safe_name = re.sub(r'[^\w.\-]', '_', Path(fname).name)
            (tmp / safe_name).write_text(code, encoding="utf-8")
        sources = [str(p) for p in tmp.glob("*.cpp")]
        exe = tmp / "a.out"
        proc = await asyncio.create_subprocess_exec(
            "g++", "-std=c++14", "-O2", "-o", str(exe), *sources,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=min(timeout, 20))
        if proc.returncode != 0:
            return f"[编译失败]\n{_truncate(stderr.decode(errors='replace').strip() or '编译错误', max_output)}"
        run_proc = await asyncio.create_subprocess_exec(
            str(exe), stdin=asyncio.subprocess.PIPE if stdin_data else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            run_proc.communicate(stdin_data.encode() if stdin_data else None),
            timeout=min(timeout, 15),
        )
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        if err:
            return f"[运行输出]\n{_truncate(out, max_output)}\n\n[stderr]\n{_truncate(err, max_output)}"
        return f"[运行输出]\n{_truncate(out or '[无输出]', max_output)}"
    except asyncio.TimeoutError:
        return f"[超时] 编译/运行超过 {timeout:.0f} 秒"
    except Exception as e:
        return f"[错误] {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def collect_artifacts(tmp_dir: str) -> list:
    """收集沙箱临时目录里的产物文件路径。"""
    p = Path(tmp_dir)
    if not p.is_dir():
        return []
    return [str(f) for f in sorted(p.rglob("*")) if f.is_file()]


def cleanup(tmp_dir: str) -> None:
    """清理沙箱临时目录。"""
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as e:
        logger.warning("sandbox cleanup 失败: %s", e)
