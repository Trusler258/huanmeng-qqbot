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
            "description": "查询起床战争单项数据(文字)。只问击杀/死亡/KD/胜场等具体数字时用这个，别用wdsj。",
            "parameters": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "玩家游戏名，'我'表示自身"},
                    "mode": {"type": "string", "enum": ["bw", "sw"]},
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
            "description": "搜索互联网，回答需要实时信息的问题。",
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
            "description": "根据需求编写代码。支持Python/JS/HTML/CSS/Java/C++/C#/Go/Rust/TS。单文件直接发送，多文件打包zip。",
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

    ext_map = {
        "python": "py", "javascript": "js", "html": "html", "css": "css",
        "java": "java", "c++": "cpp", "c#": "cs", "go": "go",
        "rust": "rs", "typescript": "ts",
    }
    ext = ext_map.get(language, "txt")

    from services.llm import call_llm
    from core.config import get_config
    cfg = get_config()
    msgs = [
        {"role": "system", "content": f"你是{language}程序员。只输出代码不解释。多文件用 //FILE:name.{ext} 和 //END 分隔。"},
        {"role": "user", "content": description},
    ]
    code = await call_llm(cfg.reply_model, msgs, max_tokens=4096, temperature=0.3)
    if not code:
        return "代码生成失败"

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

    tmp = Path(tempfile.mkdtemp(prefix="bot_code_"))
    for fname, content in files.items():
        (tmp / fname).write_text(content, encoding="utf-8")

    from services.sender import send_file
    if len(files) == 1:
        fname = list(files.keys())[0]
        ok = await send_file(str(tmp / fname), group_id if is_group else user_id, is_group)
        return f"已发送 {fname}" if ok else "文件发送失败"
    else:
        zip_path = tmp / "code.zip"
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for fname in sorted(files.keys()):
                zf.write(str(tmp / fname), fname)
        ok = await send_file(str(zip_path), group_id if is_group else user_id, is_group)
        return f"已发送 {len(files)} 个文件的 zip" if ok else "zip 发送失败"


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
) -> str | None:
    """
    执行单个工具调用，返回自然语言结果文本。
    返回 None 表示没有数据。
    """
    cmd_name = _TOOL_CMD_MAP.get(tool_name)
    if not cmd_name:
        logger.warning("未知工具调用: %s args=%s", tool_name, arguments)
        return None

    # read_url 自有实现
    if tool_name == "read_url":
        return await _read_url(arguments.get("url", ""))
    if tool_name == "write_code":
        return await _write_code(
            arguments.get("language", "python"),
            arguments.get("description", ""),
            user_id, group_id, sender_name, is_group, bot_qq,
        )

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
