"""
幻梦 Function Calling 工具系统
基于 OpenAI/DeepSeek 兼容的 tool_choice + tool_calls 协议
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("huanmeng.tools")

# ── 工具定义（OpenAI JSON Schema 格式）──────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "weather",
            "description": "查询指定城市的天气。返回当前温度/天气/风力/穿衣建议等。",
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
            "description": "查询起床战争战绩图片。要求看图/卡片/战绩总览时用这个。说'我的起床战绩'→player='我'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "玩家游戏名，'我'表示查发言人自己"},
                    "mode": {"type": "string", "enum": ["bw", "sw", "daily"], "description": "模式: bw=起床战争, sw=空岛战争, daily=今日日报"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wdsj_query",
            "description": "查询起床战争/竞技场/空岛战绩(文字)。问击杀/死亡/KD/胜场等数字时用这个。",
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
            "description": "搜索互联网获取实时信息（天气/新闻/事实查询）。聊天记录里已有上下文时不要调用此工具。",
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
            "description": "抓取并总结网页内容。用户发送链接时自动调用。",
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
            "description": "编写程序代码（仅限真正的编程任务）。不要用来生成数学题、作文、文章等非代码内容。支持Python/JS/HTML/CSS/Java/C++/C#/Go/Rust/TS。",
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
            "description": "需要多步推理/分析时调用。比如分析聊天记录、总结讨论、规划方案。会自己搜索和思考，返回最终结论。",
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
            "description": "查看主人Trusler的电脑状态: 当前窗口、正在播放的音乐、歌词等。问'在干嘛''在听什么'时用。仅限管理员使用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
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
}


def get_tool_schemas() -> list[dict]:
    """返回 DeepSeek/OpenAI 格式的工具定义列表"""
    return TOOLS

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
    cfg = get_config()

    # 优化需求描述：用户原文 → 技术规格（不限 token）
    opt_msgs = [
        {"role": "system", "content": "把下面的请求优化成一段技术规格，列出所有功能点和要求。不要修改任何功能细节，只整理格式。输出纯文本。"},
        {"role": "user", "content": description},
    ]
    spec = await call_llm(cfg.reply_model, opt_msgs, temperature=0.3)
    spec = (spec or description).strip()

    # 发送优化结果
    from services.sender import send_group_msg, send_private_msg
    preview = f"[规格] {spec[:100]}{'...' if len(spec) > 100 else ''}"
    if is_group:
        await send_group_msg(preview, group_id)
    else:
        await send_private_msg(preview, user_id)

    # 进度提示
    progress_msg = "正在生成代码喵..."
    if is_group:
        await send_group_msg(progress_msg, group_id)
    else:
        await send_private_msg(progress_msg, user_id)

    msgs = [
        {"role": "system", "content": f"你是{language}程序员。只输出代码不解释。多文件用 //FILE:name.{ext} 和 //END 分隔。"},
        {"role": "user", "content": spec},
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

    from services.sender import send_file
    if len(files) == 1:
        fname = list(files.keys())[0]
        logger.info("write_code: 发送单文件 %s", fname)
        ok = await send_file(str(tmp / fname), group_id if is_group else user_id, is_group)
        return f"已发送 {fname}" if ok else "文件发送失败"
    else:
        zip_path = tmp / "code.zip"
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for fname in sorted(files.keys()):
                zf.write(str(tmp / fname), fname)
        ok = await send_file(str(zip_path), group_id if is_group else user_id, is_group)
        return f"已发送 {len(files)} 个文件的 zip" if ok else "zip 发送失败"


async def _agent_think(question: str, chat_id: int, is_group: bool) -> str:
    """FC agent 工具：独立 LLM 循环，最多 3 轮思考+搜索，返回结论"""
    from services.llm import call_llm, call_llm_with_tools
    from core.config import get_config
    cfg = get_config()

    search_tool = [{
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "搜索互联网获取实时信息。",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
    }]

    msgs = [
        {"role": "system", "content": "你是幻梦的思考助手。分析问题，必要时搜索，最后给出简洁结论。≤100字。"},
        {"role": "user", "content": question},
    ]

    for round_idx in range(3):
        result = await call_llm_with_tools(cfg.reply_model, msgs, search_tool, max_tokens=1000, temperature=0.3)
        if not result.tool_calls:
            return (result.content or "无法分析").strip()[:200]
        # 执行搜索
        for tc in result.tool_calls:
            from modules.search import perform_search
            if tc["name"] == "search_web":
                r = await perform_search(tc["arguments"].get("query", ""), 3, "all")
                msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": str(r)})
        msgs.append({"role": "user", "content": f"第{round_idx+1}轮搜索完成，请给出最终结论（100字内）。"})
        result2 = await call_llm(cfg.reply_model, msgs, max_tokens=200, temperature=0.3)
        if result2:
            return result2.strip()[:200]
    return "分析超时，请稍后再试"


async def _system_status() -> str:
    """查询 PC 状态（从 HTTP 端点缓存读取）"""
    try:
        from services.pc_status import format_pc_status
        return format_pc_status()
    except ImportError:
        return "PC 状态模块未加载"


async def _read_url(url: str) -> str | None:
    """抓取网页正文，提取纯文本内容"""
    import requests
    from bs4 import BeautifulSoup

    if not url.startswith("http"):
        return "请提供完整链接（http/https）"

    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 移除脚本/样式
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        body = soup.find("body") or soup
        text = body.get_text(separator="\n", strip=True)

        # 压缩空行
        import re
        text = re.sub(r'\n{3,}', '\n\n', text)

        # 截断过长内容
        if len(text) > 4000:
            text = text[:4000] + "\n...(内容已截断)"

        return f"网页标题: {soup.title.string if soup.title else '无'}\n\n正文:\n{text}"
    except Exception as e:
        return f"抓取失败: {e}"


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

    if not cmd_name:
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
        args = [arguments.get("query", "")]
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

    # 调用 handler
    try:
        result = await handler(args, user_id, group_id, sender_name, is_group, bot_qq)
        if result is None:
            return None
        return str(result)
    except Exception as e:
        logger.error("工具执行失败 %s(%s): %s", tool_name, arguments, e)
        return f"工具执行出错: {e}"
