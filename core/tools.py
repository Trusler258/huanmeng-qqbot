"""
幻梦 Function Calling 工具系统
基于 OpenAI/DeepSeek 兼容的 tool_choice + tool_calls 协议
"""

from __future__ import annotations

import json
import re
import asyncio
import logging
from typing import Any

logger = logging.getLogger("huanmeng.tools")

# ── 工具定义（OpenAI JSON Schema 格式）──────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "weather",
            "description": "查询城市天气。用户问天气/温度/下雨/穿什么时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如 北京、上海、广州"},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wdsj",
            "description": "生成战绩图片卡片。用户说'查战绩/看战绩/我的日报'时调用，返回图片。player='我'表示查发言人自己。",
            "parameters": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "玩家游戏名，'我'表示查发言人自己"},
                    "mode": {"type": "string", "enum": ["bw", "sw", "daily"], "description": "bw=起床战争, sw=空岛战争, daily=今日日报"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wdsj_query",
            "description": "查具体数字（击杀/死亡/KD/胜场）。用户问'杀了多少/死了多少/KD多少'时调用，返回文字。",
            "parameters": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "玩家游戏名，'我'表示查自己的"},
                    "mode": {"type": "string", "enum": ["bw", "sw", "ar"], "description": "bw=起床战争 sw=空岛战争 ar=竞技场"},
                    "stat": {"type": "string", "description": "kill/kills/death/deaths/kd/wins/losses/score"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wzq",
            "description": "查询五子棋排行榜。返回 TOP10 玩家积分/胜率数据。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "搜索互联网获取权威信息。必须调用的场景：① 用户问实时/会变化的事实（新闻/行情/股价/市值/汇率/最新事件）；② 用户提出一个需要核实的断言（如\"长鑫存储市值已超过Intel\"\"某公司上市3周干翻XX\"）——“X是不是真的/真的假的/属实吗”这类事实核实必须搜索后回答，禁止仅凭模型内在知识直接下结论；③ 模型不确定或不懂的概念。日常闲聊（问候/吐槽/无事实内容）不需要调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "earthquake",
            "description": "查询最新地震信息，支持按省份筛选。",
            "parameters": {
                "type": "object",
                "properties": {
                    "province": {"type": "string", "description": "省份名，留空查全国"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draw_card",
            "description": "每日抽卡，随机获取一张动漫角色卡片。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chess",
            "description": "中国象棋对局管理：加入/移动/退出/局面查看。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["join", "move", "quit", "show"],
                        "description": "操作：join=加入对局, move=走子(需from/to参数), quit=退出, show=查看局面",
                    },
                    "from_pos": {"type": "string", "description": "移动棋子：起始位置，如 e2"},
                    "to_pos": {"type": "string", "description": "移动棋子：目标位置，如 e4"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_url",
            "description": "抓取并总结网页内容。用户发送链接时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页URL"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_code",
            "description": "编写程序代码并发送文件给用户。用户让你写代码/做游戏/做网页/写脚本时必须调用此工具，不要只口头答应。",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {"type": "string", "enum": ["python", "javascript", "html", "css", "java", "c++", "c#", "go", "rust", "typescript"], "description": "编程语言"},
                    "description": {"type": "string", "description": "程序需求描述"},
                },
                "required": ["language", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_think",
            "description": "复杂分析工具。需要分析聊天记录、总结讨论、多步推理时调用。简单问题不需要调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "要分析的问题"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_status",
            "description": "查看主人电脑状态（当前窗口、在听什么歌）。仅限管理员使用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "whois",
            "description": "查询域名注册信息（注册商、注册时间、到期时间、NS、域名状态）。用户问'这个域名谁注册的/什么时候到期/注册商是谁'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "域名，如 example.com、google.com"},
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pgr",
            "description": "查询 Phigros 玩家存档数据：RKS值、Best30曲目、各难度评级统计。需要 sessionToken（通过 /~pgr login 获取）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "token": {
                        "type": "string",
                        "description": "玩家的 sessionToken（登录后获取）",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["me", "top", "song", "new"],
                        "description": "me=查存档, top=排行榜, song=搜曲, new=新曲",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calc",
            "description": (
                "执行Python代码进行数学计算。用户给出数学题、方程、方程组、计算题时必须调用此工具用代码精确求解，不要心算。"
                "可用模块: math, fractions, decimal, statistics, sympy(如已安装)。"
                "代码中用print()输出最终答案。对于方程组，检查是否有解/是否矛盾。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python代码，用print()输出最终答案"},
                },
                "required": ["code"],
            },
        },
    },
]

# ── 工具名 → 命令名 映射 ──────────────────────────────────

_TOOL_CMD_MAP: dict[str, str] = {
    "weather":     "weather",
    "wdsj":        "wdsj",
    "wdsj_query":  "wdsj",  # 复用 handler，无 img 参数
    "wzq":         "wzq",
    "search_web":  "search",
    "earthquake":  "eq",
    "draw_card":   "抽",
    "chess":       "xq",
    "read_url":    "",  # 自有实现
    "write_code":  "",  # 自有实现
    "agent_think": "",  # 自有实现
    "system_status": "",  # 自有实现
    "whois":       "whois",  # ★ 域名查询
    "pgr":         "pgr",
    "calc":        "",  # 自有实现（沙箱 Python 执行）
}


def get_tool_schemas() -> list[dict]:
    """返回 DeepSeek/OpenAI 格式的工具定义列表（内置 + 插件动态注册的工具）。

    插件经 ctx.capability.register_tool 注册后，其 OpenAI Schema 自动并入，
    让 LLM 在普通聊天中也能发现并调用插件能力（always_on 常驻）。
    """
    schemas = list(TOOLS)
    try:
        from core.capability import get_capability_registry, CATEGORY_TOOL
        registry = get_capability_registry()
        builtin_names = {
            (t or {}).get("function", {}).get("name", "") for t in TOOLS if t
        }
        for cap in registry.all():
            if cap.category != CATEGORY_TOOL or not cap.source.startswith("plugin:"):
                continue
            if cap.name in builtin_names:
                continue  # 与内置工具同名 → 内置优先
            schema = registry.get_tool_schema(cap.id)
            if schema and schema not in schemas:
                schemas.append(schema)
    except Exception:
        pass
    return schemas


# ── 单工具超时（移植 kook 67dd501：工具级超时表，防止慢工具拖死整轮）──
DEFAULT_TOOL_TIMEOUT: float = 60.0
TOOL_TIMEOUTS: dict[str, float] = {
    "search_web":  30.0,
    "read_url":    30.0,
    "write_code":  120.0,
    "agent_think": 90.0,
    "weather":     15.0,
    "wdsj":        20.0,
    "wdsj_query":  20.0,
    "wzq":         10.0,
    "earthquake":  15.0,
    "draw_card":   10.0,
    "chess":       10.0,
    "whois":       15.0,
    "pgr":         20.0,
    "calc":        10.0,
    "system_status": 10.0,
}


def get_tool_timeout(tool_name: str, default: float = DEFAULT_TOOL_TIMEOUT) -> float:
    """解析工具超时：工具默认 > 全局默认。插件工具未配置则用全局默认。"""
    return TOOL_TIMEOUTS.get(tool_name, default)


def _find_plugin_tool_handler(tool_name: str):
    """按工具名查找插件动态注册的工具 handler（未注册返回 None）。"""
    try:
        from core.capability.registry import get_capability_registry
        registry = get_capability_registry()
        cap = registry.find_plugin_tool(tool_name)
        if cap is None:
            return None
        return registry.get_handler(cap.id)
    except Exception:
        return None

async def _write_code(
    language: str, description: str,
    user_id: int, group_id: int, sender_name: str, is_group: bool, bot_qq: int,
) -> str:
    """FC 代码生成：单文件发送，多文件 zip"""
    import re, zipfile, tempfile
    from pathlib import Path
    from core.logger import get_logger
    logger = get_logger("tools")

    ext_map = {
        "python": "py", "javascript": "js", "html": "html", "css": "css",
        "java": "java", "c++": "cpp", "c#": "cs", "go": "go",
        "rust": "rs", "typescript": "ts",
    }
    ext = ext_map.get(language, "txt")

    from services.llm import call_llm
    from core.config import get_config
    from services.sender import send_group_msg, send_private_msg
    cfg = get_config()
    msgs = [
        {"role": "system", "content": f"你是{language}程序员。下面是程序设计题，写出完整解法代码。只输出代码不写注释，多文件用 //FILE:name.{ext} 和 //END 分隔。"},
        {"role": "user", "content": description[:4000]},
    ]
    code = await call_llm(cfg.reply_model, msgs, temperature=0.3, timeout=120.0)
    if not code:
        logger.error("write_code: 代码生成 LLM 返回空")
        return "代码生成失败，请稍后重试"

    logger.info("write_code: LLM 返回 %d 字符", len(code))

    files = {}
    parts = re.split(r'//FILE:\s*(.+?)\s*\n', code.strip())
    if len(parts) > 1:
        for i in range(1, len(parts), 2):
            fname = parts[i].strip()
            content = parts[i + 1].replace("//END", "").strip() if i + 1 < len(parts) else ""
            if content:
                files[fname] = content
    else:
        clean = code.strip().removeprefix("```").removesuffix("```").strip()
        lines = clean.split("\n")
        title = lines[0].lstrip("# ").strip() if lines else "main"
        safe = re.sub(r'[<>:"/\\|?*]', '', title)[:30]
        files[f"{safe}.{ext}"] = clean

    if not files:
        return "代码解析失败"

    logger.info("write_code: 解析到 %d 个文件: %s", len(files), list(files.keys()))

    tmp = Path(tempfile.mkdtemp(prefix="bot_code_"))
    for fname, content in files.items():
        (tmp / fname).write_text(content, encoding="utf-8")
        logger.info("write_code: 写入 %s (%d 字节)", fname, len(content.encode("utf-8")))

    # ── 编译 + 运行 ──
    run_result = ""
    cpp_files = sorted([f for f in files if f.endswith(".cpp")])
    if cpp_files and language in ("c++", "cpp"):
        try:
            run_result = await _compile_and_run(tmp, cpp_files, group_id if is_group else user_id, is_group, description)
        except Exception as e:
            logger.warning("编译运行异常: %s", e)
            run_result = f"[编译异常] {e}"

    from services.sender import send_file
    send_msgs = []
    if len(files) == 1:
        fname = list(files.keys())[0]
        logger.info("write_code: 发送单文件 %s", fname)
        ok = await send_file(str(tmp / fname), group_id if is_group else user_id, is_group)
        send_msgs.append(f"已发送 {fname}" if ok else "文件发送失败")
    else:
        zip_path = tmp / "code.zip"
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for fname in sorted(files.keys()):
                zf.write(str(tmp / fname), fname)
        ok = await send_file(str(zip_path), group_id if is_group else user_id, is_group)
        send_msgs.append(f"已发送 {len(files)} 个文件的 zip" if ok else "zip 发送失败")

    if run_result:
        send_msgs.append(run_result)
    return "\n".join(send_msgs)


async def _compile_and_run(tmp: Path, cpp_files: list[str], chat_id: int, is_group: bool, description: str = "") -> str:
    """编译 C++ 文件并运行，返回结果（限时 5s，限内存 256MB）"""
    import subprocess, shutil

    exe = tmp / "a.out"
    # 编译
    if shutil.which("g++") is None:
        return "[编译失败] 服务器未安装 g++"
    cmd = ["g++", "-std=c++14", "-O2", "-o", str(exe)] + [str(tmp / f) for f in cpp_files]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(tmp),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")[:500].strip()
            return f"[编译失败]\n{err}"
    except asyncio.TimeoutError:
        return "[编译超时]"
    except Exception as e:
        return f"[编译异常] {e}"

    # 运行（从 description 中提取输入数据）
    stdin_data = _extract_input(description)
    try:
        proc = await asyncio.create_subprocess_exec(
            str(exe), cwd=str(tmp),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(stdin_data.encode() if stdin_data else None),
            timeout=5,
        )
        out = stdout.decode(errors="replace")[:500].strip()
        err_out = stderr.decode(errors="replace")[:200].strip()
        if err_out:
            return f"[运行输出]\n{out}\n\n[stderr]\n{err_out}"
        return f"[运行输出]\n{out}"
    except asyncio.TimeoutError:
        proc.kill()
        return "[运行超时] 超过 5 秒"
    except Exception as e:
        return f"[运行异常] {e}"


def _extract_input(text: str) -> str:
    """从题目描述中提取样例输入"""
    import re
    # 匹配 "输入 #1" 后面的代码块
    for pat in [r'输入\s*#\d+\s*\n```\s*\n?(.*?)```', r'输入样例.*?\n```\s*\n?(.*?)```']:
        m = re.search(pat, text, re.DOTALL)
        if m:
            return m.group(1).strip()
    # 匹配题目中的第一组数字行（典型输入格式，如 "0 2 197\n26 121"）
    m = re.search(r'(\d[\d\s]+\d)\s*$', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ""


async def _optimize_search_keywords(query: str) -> str:
    """用 LLM 把用户口语转换为精炼搜索关键词"""
    # 短查询或纯英文/数字不优化
    if len(query) <= 3:
        return query

    from services.llm import call_llm
    from core.config import get_config
    cfg = get_config()

    prompt = f"""把以下搜索词优化为精炼关键词（3-5个词，空格分隔）。
规则：
- 去掉口语词（帮我查/是什么/搜一下/怎么/为什么）
- 展开缩写（5090D→RTX 5090D, 4090→RTX 4090）
- 保留核心名词，不要堆砌
- 只输出关键词，不要解释

搜索词: {query}
优化后:"""

    try:
        result = await call_llm(
            cfg.reply_model,
            [{"role": "user", "content": prompt}],
            max_tokens=50,
            temperature=0.2,
            timeout=8.0,
        )
        if result and result.strip():
            optimized = result.strip().split("\n")[0].strip()
            # 去掉可能的引号/前缀
            optimized = optimized.strip("\"'""''")
            if optimized and len(optimized) < len(query) * 3:
                logger.info("搜索词优化: '%s' → '%s'", query, optimized)
                return optimized
    except Exception as e:
        logger.debug("搜索词优化失败，用原文: %s", e)
    return query


async def _agent_think(question: str, chat_id: int, is_group: bool) -> str:
    """FC agent 工具：独立 LLM 循环，最多 3 轮思考+搜索，返回结论"""
    from services.llm import call_llm, call_llm_with_tools
    from core.config import get_config
    cfg = get_config()

    search_tool = [{
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "搜索互联网。如果第一轮结果不够回答，换关键词重新搜索。",
            "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "精炼搜索关键词"}}, "required": ["query"]},
        },
    }]

    msgs = [
        {"role": "system", "content": (
            "你是幻梦的思考助手。你的任务是回答用户的问题。\n"
            "流程：\n"
            "1. 分析问题，判断需要搜索什么\n"
            "2. 调用 search_web 搜索（用精炼关键词，不要用完整句子）\n"
            "3. 如果第一轮结果不够回答，换关键词重新搜索（最多3轮）\n"
            "4. 综合所有搜索结果，给出完整结论\n"
            "结论要求：200字以内，包含具体信息，不要泛泛而谈。"
        )},
        {"role": "user", "content": question},
    ]

    for round_idx in range(3):
        result = await call_llm_with_tools(cfg.reply_model, msgs, search_tool, max_tokens=1000, temperature=0.3)
        if not result.tool_calls:
            return (result.content or "无法分析").strip()[:500]

        # 插入 assistant tool_calls 消息（DeepSeek API 要求 tool 消息前必须有对应的 tool_calls）
        msgs.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                for tc in result.tool_calls
            ],
        })

        # 执行搜索
        for tc in result.tool_calls:
            from modules.search import perform_search
            if tc["name"] == "search_web":
                raw_q = tc["arguments"].get("query", "")
                optimized_q = await _optimize_search_keywords(raw_q)
                r = await perform_search(optimized_q, limit=6, source="all")
                msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": str(r)})

        if round_idx < 2:
            # 还有轮次 → 让 LLM 决定是否继续搜索
            msgs.append({"role": "user", "content": (
                f"第{round_idx+1}轮搜索完成。如果结果足够回答，直接给结论。"
                f"如果不够，换关键词重新搜索。"
            )})
        else:
            # 最后一轮 → 强制给结论
            msgs.append({"role": "user", "content": "搜索已完成，请综合所有结果给出最终结论（200字内）。"})
            result2 = await call_llm(cfg.reply_model, msgs, max_tokens=400, temperature=0.3)
            if result2:
                return result2.strip()[:500]

    return "分析超时，请稍后再试"


async def _system_status() -> str:
    """查询 PC 状态（从 HTTP 端点缓存读取）"""
    try:
        from services.pc_status import format_pc_status
        return format_pc_status(owner="Trusler")
    except ImportError:
        return "PC 状态模块未加载"


# ── Python 沙箱计算 ──────────────────────────────────────

def _fold_truncate(text: str, max_len: int) -> str:
    """截断时保留头尾、折叠中间（移植 kook 6cda8e0：只保头会丢尾部结果）。"""
    if len(text) <= max_len:
        return text
    head = max_len // 2
    tail = max_len - head
    return text[:head] + f"\n...[中间省略 {len(text) - max_len} 字符]...\n" + text[-tail:]


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


async def _python_eval(code: str) -> str:
    """沙箱执行 Python 代码，返回 stdout 输出。限时 5 秒，禁止文件/系统/网络操作。"""
    import sys
    import os

    # 安全检查
    if _FORBIDDEN_RE.search(code):
        return "[执行失败] 代码包含禁止操作（文件/系统/网络访问被禁止）"

    if len(code) > 5000:
        return "[执行失败] 代码过长，最大 5000 字符"

    # 最小化环境变量
    safe_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        # 保头尾折叠中间（移植 kook 6cda8e0：只保头会丢尾部结果致 LLM 编造）
        out = _fold_truncate(stdout.decode(errors="replace").strip(), 2000)
        err = stderr.decode(errors="replace")[:500].strip()

        if proc.returncode != 0:
            if err:
                return f"[执行失败] {err}"
            return "[执行失败] 程序异常退出"

        if not out and err:
            return f"[执行失败] {err}"

        return out or "[无输出]"
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return "[执行失败] 执行超时（超过 5 秒）"
    except Exception as e:
        return f"[执行失败] {e}"


async def _read_url(url: str) -> str | None:
    """抓取网页正文，提取纯文本内容（统一用 PageScraper + LLM 摘要）"""
    if not url.startswith("http"):
        return "请提供完整链接（http/https）"

    # 用统一的 PageScraper 提取正文（readability + 智能截断）
    try:
        from modules.local_search import get_scraper
        scraper = get_scraper()
        loop = asyncio.get_running_loop()
        raw_text = await loop.run_in_executor(None, lambda: scraper.scrape(url, max_chars=6000))
    except Exception as e:
        return f"抓取失败: {e}"

    if not raw_text:
        return f"无法读取该页面: {url}"

    # LLM 摘要：把 6000 字压缩成 800 字核心信息
    from services.llm import call_llm
    from core.config import get_config
    cfg = get_config()

    summary_prompt = f"""提取以下网页正文的核心信息（800字以内）。
规则：
1. 保留关键事实、数据、步骤、结论
2. 去掉广告、导航、重复内容
3. 保持原文的客观性，不要添加自己的理解
4. 如果是技术文章，保留代码示例和关键参数
5. 如果是新闻，保留时间、地点、人物、事件

网页内容：
{raw_text}

核心信息："""

    try:
        summary = await call_llm(
            cfg.reply_model,
            [{"role": "user", "content": summary_prompt}],
            max_tokens=1200,
            temperature=0.2,
            timeout=20.0,
        )
        if summary and summary.strip():
            logger.info("网页摘要完成: %s (%d→%d字)", url[:50], len(raw_text), len(summary))
            return summary.strip()
    except Exception as e:
        logger.warning("LLM 摘要失败，返回原文: %s", e)

    # LLM 摘要失败 → 返回原文（已截断）
    return raw_text


async def _resolve_player(user_id: int, game: str) -> str | None:
    """从玩家绑定数据中查找用户对应游戏ID"""
    import json
    from pathlib import Path
    bind_file = Path(__file__).resolve().parent.parent / "data" / "player_bindings.json"
    if bind_file.exists():
        data = json.loads(bind_file.read_text(encoding="utf-8"))
        uid = str(user_id)
        return data.get(uid, {}).get(game)
    return None


def _save_binding(user_id: int, game: str, player_name: str):
    import json
    from pathlib import Path
    bind_file = Path(__file__).resolve().parent.parent / "data" / "player_bindings.json"
    data = {}
    if bind_file.exists():
        data = json.loads(bind_file.read_text(encoding="utf-8"))
    uid = str(user_id)
    data.setdefault(uid, {})[game] = player_name
    bind_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_stats(player: str, stat: str, raw: str) -> str:
    """从全量数据提取，返回精简键值对"""
    import re
    key_map = {
        "kill": "击杀", "kills": "击杀",
        "death": "死亡", "deaths": "死亡",
        "kd": "KD",
        "win": "胜利", "wins": "胜利",
        "loss": "失败", "losses": "失败",
        "score": "积分",
    }
    keyword = key_map.get(stat.lower(), "")
    if keyword:
        m = re.search(rf'(?:^|\n)\s*{re.escape(keyword)}[：:]\s*([\d.]+)', raw)
        if m:
            return f"{keyword}: {m.group(1)}"
        return f"{keyword}: 无数据"
    lines = []
    for k in ["击杀", "死亡", "KD"]:
        m = re.search(rf'(?:^|\n)\s*{k}[：:]\s*([\d.]+)', raw)
        if m:
            lines.append(f"{k} {m.group(1)}")
    return " | ".join(lines) if lines else "无数据"


async def execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    user_id: int,
    group_id: int,
    sender_name: str,
    is_group: bool,
    bot_qq: int,
    original_msg: str = "",  # 用户原始消息，用于 write_code 不受 FC 截断
) -> str | None:
    """
    执行单个工具调用，返回自然语言结果文本。
    返回 None 表示没有数据。
    """
    cmd_name = _TOOL_CMD_MAP.get(tool_name)

    # 自有实现（不走 COMMAND_MAP）
    if tool_name == "read_url":
        return await _read_url(arguments.get("url", ""))
    if tool_name == "write_code":
        desc = original_msg or arguments.get("description", "")
        return await _write_code(
            arguments.get("language", "python"),
            desc,
            user_id, group_id, sender_name, is_group, bot_qq,
        )
    if tool_name == "agent_think":
        return await _agent_think(arguments.get("question", ""), group_id if is_group else user_id, is_group)
    if tool_name == "system_status":
        return await _system_status()
    if tool_name == "calc":
        return await _python_eval(arguments.get("code", ""))

    if not cmd_name:
        # 插件动态注册的工具：LLM 对话自动发现并调用，回退到插件 handler
        plugin_handler = _find_plugin_tool_handler(tool_name)
        if plugin_handler:
            try:
                result = await plugin_handler(
                    arguments, user_id, group_id, sender_name, is_group, bot_qq)
                if isinstance(result, str):
                    from core.plugin.kook_compat import strip_kook_text
                    return strip_kook_text(result)
                return result
            except Exception as e:
                logger.error("插件工具 %s 执行失败: %s", tool_name, e)
                return f"插件工具 {tool_name} 执行出错: {e}"
        logger.warning("未知工具调用: %s args=%s", tool_name, arguments)
        return None

    # 从 commands 模块获取 handler
    from modules.commands import COMMAND_MAP
    handler = COMMAND_MAP.get(cmd_name)
    if not handler:
        logger.warning("工具命令未注册: %s → %s", tool_name, cmd_name)
        return None

    # 构建参数列表
    args = []
    if tool_name == "weather":
        args = [arguments.get("city", "")]
    elif tool_name == "wdsj":
        # WDSJ 发图：强制用绑定名
        player = await _resolve_player(user_id, "wdsj")
        if not player:
            return "你还未绑定起床战绩账号。"
        mode = arguments.get("mode", "bw")
        from modules.commands import COMMAND_MAP
        handler = COMMAND_MAP.get("wdsj")
        if handler:
            await handler([mode, player, "img"], user_id, group_id, sender_name, is_group, bot_qq)
            return f"起床战绩图片已生成 (玩家: {player}, 模式: {mode})"
        return "wdsj 指令未注册"
    elif tool_name == "wdsj_query":
        # WDSJ 文字数据：提取指定项
        player = await _resolve_player(user_id, "wdsj")
        if not player:
            return "你还未绑定起床战绩账号。"
        mode = arguments.get("mode", "bw")
        stat = arguments.get("stat", "")
        from modules.commands import COMMAND_MAP
        handler = COMMAND_MAP.get("wdsj")
        if handler:
            result = await handler([mode, player], user_id, group_id, sender_name, is_group, bot_qq)
            if not result:
                return f"未找到玩家 {player} 的数据"
            # 从全量数据中提取相关行
            return _extract_stats(player, stat, result)
        return "wdsj 指令未注册"
    elif tool_name == "search_web":
        # ★ FC 路径搜索词优化：LLM 传来的 query 可能是完整句子，先优化成关键词
        raw_query = arguments.get("query", "")
        if raw_query:
            optimized = await _optimize_search_keywords(raw_query)
            args = [optimized]
        else:
            args = [""]
    elif tool_name == "earthquake":
        prov = arguments.get("province", "")
        if prov:
            args = ["sub"] + ([prov] if prov else [])
        else:
            args = []
    elif tool_name == "draw_card":
        args = []
    elif tool_name == "chess":
        action = arguments.get("action", "show")
        if action == "join":
            args = ["join"]
        elif action == "move":
            frm = arguments.get("from_pos", "")
            to = arguments.get("to_pos", "")
            args = ["move", frm, to]
        elif action == "quit":
            args = ["quit"]
        else:
            args = []
    elif tool_name == "whois":
        domain = arguments.get("domain", "")
        args = [domain] if domain else []

    # 调用 handler
    try:
        result = await handler(args, user_id, group_id, sender_name, is_group, bot_qq)
        if result is None:
            return None
        return str(result)
    except Exception as e:
        logger.error("工具执行失败 %s(%s): %s", tool_name, arguments, e)
        return f"工具执行出错: {e}"
