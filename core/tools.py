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
            "name": "learn_slang",
            "description": (
                "把新学到的网络黑话/梗记进知识库，以后就能看懂这类说法。"
                "当你遇到不认识的网络流行语、梗、缩写，且已确认它的意思时调用"
                "（例如用户解释了、或你搜索确认了）。只记真正的网络用语，"
                "不要记普通词汇或一次性玩笑。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "词或梗本身，如 yyds、鸡你太美"},
                    "meaning": {"type": "string", "description": "简明释义（一句话，可含用法示例）"},
                    "category": {"type": "string", "description": "归类，如 缩写梗/语气词/游戏/MC/社交"},
                },
                "required": ["term", "meaning"],
            },
        },
    },
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
            "description": "生成战绩图片卡片。mode=bw/sw 是个人战绩（自动查发言人的绑定账号，player 参数无效不用传）；mode=daily 是全群日报（无需绑定）。用户意图不明确（查个人还是看全群日报）时先问一句再调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "玩家游戏名（当前版本工具忽略此参数，一律用发言人绑定名）"},
                    "mode": {"type": "string", "enum": ["bw", "sw", "daily"], "description": "bw=起床战争(个人), sw=空岛战争(个人), daily=全群日报"},
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
            "description": "搜索互联网获取权威信息。必须调用的场景：① 用户问实时/会变化的事实（新闻/行情/股价/市值/汇率/最新事件）；② 用户提出一个需要核实的断言（如\"长鑫存储市值已超过Intel\"\"某公司上市3周干翻XX\"）——“X是不是真的/真的假的/属实吗”这类事实核实必须搜索后回答，禁止仅凭模型内在知识直接下结论；③ 模型不确定或不懂的概念。日常闲聊（问候/吐槽/无事实内容）不需要调用。\n可选过滤参数（按需填，不必都填）：max_results 返回条数 1~10（默认10，也是服务端硬上限）；freshness 时效窗口 day/week/month/year（查新闻/最新动态填 day 或 week）；content_type 选 news 查新闻、web 查一般网页；zone 选 cn(国内) / intl(国际)；language 如 zh-CN / en。首轮结果不够回答时，换关键词再搜一轮（最多3轮），别硬凑。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词（精炼，不要整句）"},
                    "max_results": {"type": "integer", "description": "返回条数 1~10（默认10）"},
                    "freshness": {"type": "string", "enum": ["day", "week", "month", "year"],
                                  "description": "时效窗口；查最新新闻/动态用 day 或 week"},
                    "content_type": {"type": "string", "enum": ["web", "news"],
                                     "description": "内容类型；查新闻用 news"},
                    "zone": {"type": "string", "enum": ["cn", "intl"],
                             "description": "区域：cn 国内 / intl 国际"},
                    "language": {"type": "string", "description": "语言，如 zh-CN / en"},
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
            "name": "web_fetch",
            "description": (
                "抓取并总结网页内容。用户发送链接、让你看某个网页/查某个站点时，"
                "优先调用本工具，不要用 run_code 自己写爬虫脚本——"
                "本工具已内置反爬处理：URL 编码的 JS 挑战页会自动解码，纯前端（Vue/React）"
                "客户端渲染的页面会用浏览器真渲染兜底，能拿到 run_code 拿不到的正文。"
                "github.com 链接会自动改走 raw（主站被墙）。"
            ),
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
            "description": "按用户要求生成文件并发送给用户。写代码/做游戏/网页/脚本 → language 填对应编程语言；把资料整理成文档、总结成报告、生成 md/txt 文件发送 → language 填 markdown 或 text。用户明确说了「发送文件/发出来/整理成文档」时必须调用本工具，不要只把内容打在聊天里。",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {"type": "string", "enum": ["python", "javascript", "html", "css", "java", "c++", "c#", "go", "rust", "typescript", "markdown", "text"], "description": "编程语言；文档用 markdown（.md）或 text（.txt）"},
                    "description": {"type": "string", "description": "需求描述。写文档时把要整理的【具体内容/素材】一并写进来（例如刚搜到的新闻要点），只写「整理成md」会生成空壳"},
                },
                "required": ["language", "description"],
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
            "name": "run_code",
            "description": (
                "在沙箱中真实运行代码并返回运行输出。用户给出数学题/方程/方程组/计算题，"
                "或需要实跑一段代码验证算法、逻辑、边界情况时，必须调用此工具，"
                "不要心算，也不要只写代码却声称已经跑出结果。"
                "支持 python（默认）与 cpp 两种语言。"
                "Python 预装 requests/pillow/openpyxl/pandas/numpy/matplotlib/beautifulsoup4/lxml，"
                "标准库也可用；工作区文件跨对话持久保留（代码里写文件后，下次 run_code 或 ws_files 都能读到）。"
                "需要抓网页/调用外部 API 时设 net=true（联网模式）。"
                "C++ 按 C++14 用 g++ 编译执行。"
                "代码必须把最终结果打印出来（Python 用 print()，C++ 用 std::cout），否则拿不到答案。"
                "解方程组时要判断是无解还是无穷多解，并把结论打印出来。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "cpp"],
                        "description": "代码语言，默认 python",
                    },
                    "code": {
                        "type": "string",
                        "description": "源码。language=python 时为 Python 代码；C++ 时可直接放一个 main.cpp 的内容",
                    },
                    "files": {
                        "type": "object",
                        "description": "C++ 多文件源码 {文件名: 内容}，如 {\"main.cpp\":\"...\"}；不填则用 code 作为 main.cpp",
                        "additionalProperties": {"type": "string"},
                    },
                    "stdin": {
                        "type": "string",
                        "description": "可选，程序的标准输入内容（需要输入数据时用）",
                    },
                    "net": {
                        "type": "boolean",
                        "description": "联网模式：需要抓网页/调用外部 API 时设 true（默认隔离不联网，仅 python）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ws_files",
            "description": (
                "管理沙箱持久工作区文件（跨对话保留）。"
                "action=list 列出工作区全部文件；action=read 读取一份文件内容；"
                "action=write 写入/覆盖一份文件。"
                "配合 run_code 使用：先用 write 放数据文件，再用 run_code 处理它，"
                "产出的文件也能用 read 读回或下次继续处理。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "read", "write"],
                        "description": "list=列文件, read=读文件, write=写文件",
                    },
                    "name": {
                        "type": "string",
                        "description": "文件名（read/write 必填），如 data.csv、result.json",
                    },
                    "content": {
                        "type": "string",
                        "description": "write 时的文件内容",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            # ★ v2.3.64: 描述里的「可用技能」清单由 get_tool_schemas() 动态拼入
            #   （见 services.llm.get_skill_index），技能文件改了即时反映。
            "name": "load_skill",
            "description": (
                "加载一份内部技能手册的正文。当任务需要专门的规范/流程/方法"
                "（例如写作文、长文、会话总结等）时，先调用本工具拿到完整规范再动手，"
                "比凭印象直接写更准。一次只加载当前真正需要的那一份，不要一次拉多份。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "技能名，取自下方「可用技能」列表里的英文名，如 writing_system",
                    },
                },
                "required": ["name"],
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
    "web_fetch":    "",  # 自有实现
    "write_code":  "",  # 自有实现
    "agent_think": "",  # 自有实现
    "system_status": "",  # 自有实现
    "whois":       "whois",  # ★ 域名查询
    "pgr":         "pgr",
    "run_code":    "",  # 自有实现（沙箱执行 Python / C++）
    "ws_files":    "",  # 自有实现（沙箱持久工作区文件管理）
    "load_skill":  "",  # 自有实现（按需拉取技能手册正文）
}


def get_tool_schemas() -> list[dict]:
    """返回 DeepSeek/OpenAI 格式的工具定义列表（内置 + 插件动态注册的工具）。

    插件经 ctx.capability.register_tool 注册后，其 OpenAI Schema 自动并入，
    让 LLM 在普通聊天中也能发现并调用插件能力（always_on 常驻）。
    """
    schemas = list(TOOLS)

    # ★ v2.3.64: 把技能索引动态拼进 load_skill 的描述，让模型知道有哪些 skill 可按需拉取。
    #   深拷贝后再改——schemas 是 list(TOOLS) 浅拷贝，直接改会污染模块级 TOOLS。
    try:
        from services.llm import get_skill_index
        _idx = get_skill_index()
        if _idx:
            for _i, _t in enumerate(schemas):
                _fn = (_t or {}).get("function", {})
                if _fn.get("name") == "load_skill":
                    _t = json.loads(json.dumps(_t, ensure_ascii=False))
                    _t["function"]["description"] = (
                        _t["function"].get("description", "") + "\n可用技能：\n" + _idx
                    )
                    schemas[_i] = _t
                    break
    except Exception as _e:
        logger.debug("注入技能索引失败(忽略): %s", _e)

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

    # ★ v2.3.28 两个关键加固（都直接影响 DeepSeek 上下文缓存命中率）：
    #
    # ① 按 name 去重（保留首次出现）
    #    事故：TOOLS 里曾出现两个 learn_slang / 两个 search_web，
    #    带重名工具请求会被 API 直接拒绝：
    #     400 "Tool names must be unique." → FC 调用全挂。
    #    这里做最后一道防线，即使列表写重了也不会把坏请求发出去。
    #
    # ② 按 name 排序，保证顺序**绝对稳定**
    #    实测（scripts/_diag_api_cache.py）：DeepSeek 的缓存 key 与 tools 定义强相关 ——
    #    同一份 tools 第 2 次调用命中 99.1%，而"换了 tools"的第 1 次只命中 30.1%
    #    （仅 system 部分）。插件是运行时注册的，若其遍历顺序不稳定，
    #    tools 顺序就会在调用间漂移 → 每次都像"第 1 次" → 缓存几乎全失效。
    #    排序后顺序恒为字典序，缓存前缀才稳定。
    _seen: set[str] = set()
    _uniq: list[dict] = []
    for s in schemas:
        _n = ((s or {}).get("function") or {}).get("name") or ""
        if not _n or _n in _seen:
            continue
        _seen.add(_n)
        _uniq.append(s)
    _uniq.sort(key=lambda x: (x.get("function") or {}).get("name") or "")
    return _uniq


# ── 单工具超时（移植 kook 67dd501：工具级超时表，防止慢工具拖死整轮）──
DEFAULT_TOOL_TIMEOUT: float = 60.0
TOOL_TIMEOUTS: dict[str, float] = {
    "search_web":  30.0,
    "web_fetch":    30.0,
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
    "run_code":    25.0,
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
    """FC 文件生成：代码单文件发送/多文件 zip；文档（md/txt）单文件发送"""
    import re, zipfile, tempfile
    from pathlib import Path
    from core.logger import get_logger
    logger = get_logger("tools")

    ext_map = {
        "python": "py", "javascript": "js", "html": "html", "css": "css",
        "java": "java", "c++": "cpp", "c#": "cs", "go": "go",
        "rust": "rs", "typescript": "ts",
        # ★ v2.3.81c: 文档类——"整理成 md 文档/报告发送"走同一条发文件通道
        "markdown": "md", "md": "md", "text": "txt", "txt": "txt",
    }
    ext = ext_map.get(language, "txt")
    is_doc = ext in ("md", "txt")

    from services.llm import call_llm
    from core.config import get_config
    from services.sender import send_group_msg, send_private_msg
    cfg = get_config()
    _desc_cap = 8000 if is_doc else 4000
    if is_doc:
        # ★ v2.3.81c: 文档模式——产出的是文档正文，不是代码。
        #   明确禁止开场白/围栏，避免文件名被 "```markdown" 污染，也避免正文里塞"好的以下是"。
        #   v2.3.81d: 加"逐条覆盖 / 每条带来源 / 禁空话 / 过滤无关素材"四条硬规则——
        #   实测素材 1635 字，模型只挑 2 条写，还塞了一堆没有来源的行业大势空话。
        msgs = [
            {"role": "system", "content": (
                "你是资料整理助手。输入里包含【整理要求】和【已检索到的原始素材】，"
                "请把素材整理成一份完整、可直接阅读的文档正文。硬规则：\n"
                "1. 直接输出文档本身：不要代码围栏，不要「好的/以下是/希望对你有帮助」这类话，"
                "不要单独输出文件名行；需要标题用 #，分节用 ##，条目用 -。\n"
                "2. 【素材里有多少条就写多少条】，逐条覆盖，禁止只挑其中几条，"
                "禁止把多条合并成一句空泛的概括。\n"
                "3. 每条事实后面附上素材里给出的来源链接，写成 Markdown 链接。\n"
                "4. 只能写素材里有的内容。素材里没有的数字、时间、人名、出处一律不写；"
                "也不要写「行业大势」「竞争焦点转向」这类没有具体事实的空话。\n"
                "5. 明显与主题无关的素材（天气预报、政府宪报公告、体育赛程、股价行情页）直接略过。\n"
                "6. 素材本身信息很薄时就诚实写薄，不要靠自己的常识补齐来显得丰满。"
            )},
            {"role": "user", "content": description[:_desc_cap]},
        ]
    else:
        msgs = [
            {"role": "system", "content": f"你是{language}程序员。下面是程序设计题，写出完整解法代码。只输出代码不写注释，多文件用 //FILE:name.{ext} 和 //END 分隔。"},
            {"role": "user", "content": description[:_desc_cap]},
        ]
    code = await call_llm(cfg.reply_model, msgs, temperature=0.3, timeout=120.0)
    if not code:
        logger.error("write_code: 生成 LLM 返回空")
        return "文件生成失败，请稍后重试"

    logger.info("write_code: LLM 返回 %d 字符 (语言=%s)", len(code), language)

    if is_doc:
        # 去掉可能残留的代码围栏，否则首行 "```markdown" 会被当成标题写进文件名
        code = code.strip()
        if code.startswith("```"):
            code = code.split("\n", 1)[1] if "\n" in code else ""
        if code.rstrip().endswith("```"):
            code = code.rstrip()[:-3]
        code = code.strip()

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
        # ★ v2.3.24 通用化：不再硬编码人名，owner 交给 pc_status 按配置解析
        return format_pc_status()
    except ImportError:
        return "PC 状态模块未加载"


# ── 沙箱代码执行（run_code）───────────────────────────────
#
# v2.3.62：把原 calc（_python_eval，正则黑名单 + sys.executable -c）整体换成
# core/sandbox 的真实沙箱执行，并改名 run_code、支持 Python + C++。
#
# 相比旧实现：
#   · 旧版靠 _FORBIDDEN_RE 正则黑名单"猜"危险代码，绕过方式多（字符串拼接、
#     getattr(__builtins__) 等），且只支持 Python；
#   · 新版是硬限制：独立临时目录、超时强杀、setrlimit 限内存/CPU、输出截断、
#     子进程 env 清洗（见 core/sandbox._run_proc），不依赖正则。
# 但沙箱不是容器——本身仍能读服务器文件，故保留一层"防手滑"正则，
# 挡掉联网 / 起子进程 / 读敏感路径这三类高危动作（见 _SANDBOX_BLOCK_RE）。

# 单次运行时限（秒）：Python 纯计算很快；C++ 要留出 g++ 编译时间；
# net 模式要留出外网往返（github 主站被墙，api/raw 可达但要超时兜底）
_CODE_TIMEOUT_PY: float = 8.0
_CODE_TIMEOUT_NET: float = 40.0
_CODE_TIMEOUT_CPP: float = 16.0
# 代码体积上限（防把整本小说塞进来）
_MAX_CODE_CHARS: int = 8000
# 单路输出上限（比全局 MAX_OUTPUT 放宽，多给 LLM 一点上下文）
_CODE_MAX_OUTPUT: int = 2000

# 高危动作正则（大小写不敏感）。命中即拒绝执行，不进入子进程。
# 说明：这不是唯一防线，只是拦住最直白的越权动作；真正的隔离靠 core.sandbox。
_SANDBOX_BLOCK_RE = re.compile(
    # ① 联网 / 起进程 / 原生调用
    r'\b(?:subprocess|multiprocessing|pty|ctypes|socket|socketserver|paramiko'
    r'|ftplib|smtplib|telnetlib|urllib|requests|httpx|aiohttp|websocket)\b'
    r'|\bos\.(?:system|popen|exec\w*|spawn\w*|fork|kill|remove|unlink|rmdir)\b'
    # 裸进程调用（C++ 的 system()/popen()/exec*()、Python 的 exec()）
    r'|\b(?:system|popen|fork|exec|execl|execlp|execle|execv|execvp|execve'
    r'|execvpe|CreateProcess|WinExec|ShellExecute\w*)\s*\('
    r'|\bshutil\.(?:rmtree|move)\b'
    # ② 读敏感路径（服务器凭据 / 私钥 / 系统账户库）
    r'|(?:/root/|/etc/|/var/lib/|\.ssh\b|id_rsa|\.env\b|\.pem\b'
    r'|passwd|shadow|credential|secret)',
    re.IGNORECASE,
)


def _fmt_sandbox_result(result: dict, label: str) -> str:
    """把 sandbox 的 dict 结果整理成给 LLM 看的文本。"""
    if result.get("timed_out"):
        return f"[{label} 执行超时] 超过时限，已强制终止（可能是死循环或计算量过大）"
    rc = result.get("returncode", -1)
    out = (result.get("stdout") or "").strip()
    err = (result.get("stderr") or "").strip()
    if rc != 0:
        parts = [f"[{label} 执行失败] 退出码 {rc}"]
        if out:
            parts.append("stdout:\n" + out)
        if err:
            parts.append("stderr:\n" + err)
        return "\n".join(parts)
    if out:
        return out
    if err:
        # 退出码 0 但有 stderr（多为 warning）→ 原文给出，别丢
        return err
    return f"[{label} 运行成功但无输出] 代码没有 print/输出任何内容，请让代码打印结果"


async def _run_code(language: str, code: str, files: dict | None = None,
                    stdin_data: str = "", net: bool = False) -> str:
    """沙箱执行 LLM 生成的代码（Python / C++），返回真实运行输出。

    与 write_code（只生成代码文件发群）不同：这里是真的跑一遍并把运行结果拿回来，
    用来根治"声称算过了但其实是心算/编的"。
    """
    from core import sandbox as _sb

    lang = (language or "python").strip().lower()
    # 我们自己写进沙箱的源码文件名，不算"代码生成的文件"
    input_names: set[str] = set()

    if lang in ("cpp", "c++", "cxx", "c"):
        src: dict[str, str] = dict(files or {})
        if not src and code:
            src = {"main.cpp": code}
        if not src:
            return "[执行失败] 未提供 C++ 源码（用 code 传单个 main.cpp，或用 files 传多文件）"
        joined = "\n".join(src.values())
        if len(joined) > _MAX_CODE_CHARS:
            return f"[执行失败] C++ 代码过长，最大 {_MAX_CODE_CHARS} 字符"
        # v2.3.81b: 黑名单正则已撤——chroot 根隔离是真防线（正则可被绕过，且拦掉 net 模式需要的 requests）
        input_names = set(src)
        result = await _sb.compile_and_run_cpp(
            src, timeout=_CODE_TIMEOUT_CPP, stdin_data=stdin_data,
            max_output=_CODE_MAX_OUTPUT)
        label = "C++"
    else:
        if not code:
            return "[执行失败] 未提供代码"
        if len(code) > _MAX_CODE_CHARS:
            return f"[执行失败] 代码过长，最大 {_MAX_CODE_CHARS} 字符"
        # v2.3.81b: 黑名单正则已撤——chroot 根隔离是真防线（正则可被绕过，且拦掉 net 模式需要的 requests）
        result = await _sb.run_python(
            code, timeout=(_CODE_TIMEOUT_NET if net else _CODE_TIMEOUT_PY), stdin_data=stdin_data,
            max_output=_CODE_MAX_OUTPUT, net=net, workspace=True)
        label = "Python"

    text = _fmt_sandbox_result(result, label)

    # 产物只列名不发送（临时目录随后清理）；要发文件请走 write_code
    try:
        arts = [a for a in _sb.collect_artifacts(result.get("tmp_dir", ""))
                if a.name not in input_names]
        if arts:
            text += "\n[代码生成的文件] " + "、".join(a.name for a in arts)
    except Exception:
        pass
    try:
        _sb.cleanup(result.get("tmp_dir", ""))
    except Exception:
        pass
    return text




async def _ws_files(action: str, name: str = "", content: str = "") -> str:
    """沙箱持久工作区文件管理：list / read / write（跨对话保留，路径穿越已防）。"""
    import os
    from pathlib import Path as _P
    _WS_DIR = _P(__file__).resolve().parent.parent / "data" / "sandbox_ws"
    _WS_DIR.mkdir(parents=True, exist_ok=True)
    action = (action or "list").strip().lower()
    if action == "list":
        entries = [p for p in _WS_DIR.rglob("*") if p.is_file()]
        entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not entries:
            return "工作区为空（可用 ws_files write 或 run_code 写入文件）"
        lines = [f"  {p.relative_to(_WS_DIR)}  ({p.stat().st_size}B)" for p in entries[:50]]
        return "工作区文件（新→旧）:" + chr(10) + chr(10).join(lines)
    name = os.path.basename((name or "").strip())
    if not name:
        return "缺少文件名"
    target = _WS_DIR / name
    if action == "read":
        if not target.exists():
            return f"{name} 不存在（用 ws_files list 查看工作区文件）"
        return f"=== {name} ===" + chr(10) + target.read_text(encoding="utf-8", errors="replace")[:4000]
    if action == "write":
        target.write_text(content or "", encoding="utf-8")
        return f"已写入 {name}（{len(content or '')} 字符），可用 run_code 处理它"
    return f"未知 action: {action}"


_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _decode_js_challenge(url: str, timeout: float = 15.0) -> str:
    """反爬挑战页解码：JS 变量里 URL 编码的真 HTML → 正文（纯 Python，零额外开销）。

    常见于国内站点的 JS 挑战（如 updream.cn：真 HTML 以 %3C!doctype 形式塞进
    _AbConf 变量），requests/readability 只能看到空壳。这里解码后直接提正文，
    不用拉起 Chromium（常驻 858MB）。
    """
    import re, urllib.request, urllib.parse
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return ""
    m = re.search(r"%3C(?:!doctype|html)", html, re.I)
    if not m:
        return ""
    seg = html[m.start(): m.start() + 600000]
    end = seg.find('"')
    if end > 0:
        seg = seg[:end]
    decoded = urllib.parse.unquote(seg.replace("\\u0026", "&"))
    if len(decoded) < 500:
        return ""
    try:
        from readability import Document
        body = Document(decoded).summary()
    except Exception:
        body = decoded
    text = re.sub(r"<script[\s\S]*?</script>", " ", body or decoded, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:6000]


def _github_rewrite(url: str) -> str:
    """github.com 链接改写为可达形式（主站被墙，raw/api 可达）：
    - .../blob/<branch>/<path> → raw.githubusercontent.com/<branch>/<path>
    - 仓库主页 → raw README（HEAD/README.md）
    """
    import re as _re
    m = _re.match(r"https?://github\.com/([\w.-]+)/([\w.-]+)/blob/([^/]+)/(.+)", url)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}/{m.group(4)}"
    m = _re.match(r"https?://github\.com/([\w.-]+)/([\w.-]+)/?", url)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/HEAD/README.md"
    return url


async def _read_url(url: str) -> str | None:
    url = _github_rewrite(url)
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
        raw_text = ""

    # ★ v2.3.81b: 反爬挑战页（真 HTML 被 URL 编码塞进 JS 变量）→ 纯 Python 解码，
    #   比 Chromium 轻得多（零额外开销）；解码失败才用 Chromium 真渲染兜底
    if not raw_text or len(raw_text.strip()) < 200:
        decoded = _decode_js_challenge(url)
        if decoded:
            logger.info("JS 挑战页解码成功: %s (%d字)", url[:40], len(decoded))
            raw_text = decoded

    if not raw_text or len(raw_text.strip()) < 200:
        try:
            from modules.changelog import _ensure_browser
            browser = await _ensure_browser()
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            await page.wait_for_timeout(1200)
            rendered = await page.evaluate("() => document.body ? document.body.innerText : ''")
            await page.close()
            if rendered and len(rendered.strip()) > len(raw_text or ""):
                logger.info("Chromium 渲染兜底成功: %s (%d字)", url[:40], len(rendered))
                raw_text = rendered.strip()[:6000]
        except Exception as e:
            logger.warning("Chromium 渲染兜底失败: %s", e)

    if not raw_text or not raw_text.strip():
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
6. 用纯文本输出，禁止 markdown 语法（# 标题、** 加粗、| 表格在 QQ 里原样显示），对比内容用「名称：值」逐行

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
    if tool_name == "learn_slang":
        # v2.3.24: 让 bot 把新学到的网络黑话记进 data/skills/15_slang.md，
        # 追加后立即成为词典触发词（词表按文件 mtime 自动失效重算）
        from services.llm import append_slang_term
        ok, msg = append_slang_term(
            arguments.get("term", ""),
            arguments.get("meaning", ""),
            arguments.get("category", "LLM 自学") or "LLM 自学",
        )
        return msg
    if tool_name == "web_fetch":
        return await _read_url(arguments.get("url", ""))
    if tool_name == "write_code":
        _lang = arguments.get("language", "python") or "python"
        _arg_desc = arguments.get("description", "")
        # ★ v2.3.81c: 文档类必须用 LLM 传来的 description——里面带着要整理的素材
        #   （如刚搜到的新闻要点）；original_msg 只有"整理成md发我"这种指令，没有内容。
        if _lang in ("markdown", "md", "text", "txt") and _arg_desc.strip():
            desc = _arg_desc
        else:
            desc = original_msg or _arg_desc
        return await _write_code(
            _lang,
            desc,
            user_id, group_id, sender_name, is_group, bot_qq,
        )
    if tool_name == "agent_think":
        return await _agent_think(arguments.get("question", ""), group_id if is_group else user_id, is_group)
    if tool_name == "system_status":
        return await _system_status()
    if tool_name == "run_code":
        return await _run_code(
            arguments.get("language", "python") or "python",
            arguments.get("code", "") or "",
            arguments.get("files") or {},
            arguments.get("stdin", "") or "",
            net=bool(arguments.get("net", False)),
        )
    if tool_name == "ws_files":
        return await _ws_files(
            arguments.get("action", "list"),
            arguments.get("name", "") or "",
            arguments.get("content", "") or "",
        )
    if tool_name == "load_skill":
        # ★ v2.3.64: 按需拉取技能手册正文（data/skills/*.md 的章节）
        from services.llm import get_skill_content, get_skill_index
        _name = (arguments.get("name") or "").strip()
        _content = get_skill_content(_name)
        if _content:
            logger.info("load_skill 命中: %s (%d字)", _name, len(_content))
            return f"【技能 {_name}】\n{_content}"
        logger.info("load_skill 未命中: %r", _name)
        _idx = get_skill_index()
        if _idx:
            return f"没有名为「{_name}」的技能。可用技能：\n{_idx}"
        return f"没有名为「{_name}」的技能。"

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
        mode = arguments.get("mode", "bw")
        if mode == "daily":
            # ★ v2.3.75: daily 是全群日报，无需绑定（旧逻辑先查绑定，把没绑定的用户挡在日榜外）
            from modules.commands import COMMAND_MAP as _CM
            _h = _CM.get("wdsj")
            if _h:
                await _h(["daily", "img"], user_id, group_id, sender_name, is_group, bot_qq)
                return "今日日报图片已生成 (全群)"
            return "wdsj 指令未注册"
        # bw/sw 个人战绩：强制用绑定名（player 参数无效，见工具描述）
        player = await _resolve_player(user_id, "wdsj")
        if not player:
            return "你还未绑定起床战绩账号。"
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
        # ★ v2.3.81e: FC 搜索直接走 perform_search（不再绕 /~search 指令层），把 LLM 按需
        #   填的过滤参数透传给 AnySearch；max_results 同时决定返回条数与结果文本的截断上限
        #   （原来固定 limit=4 → 结果只留 1600 字，多路结果被腰斩）。
        raw_query = (arguments.get("query") or "").strip()
        if not raw_query:
            return "请提供搜索关键词"
        optimized = await _optimize_search_keywords(raw_query)
        try:
            _lim = int(arguments.get("max_results") or 8)
        except Exception:
            _lim = 8
        _lim = max(3, min(_lim, 10))
        from modules.search import perform_search
        return await perform_search(
            optimized, sender_name=sender_name, user_id=user_id,
            chat_id=group_id if is_group else user_id,
            limit=_lim, source="all", is_group=is_group,
            freshness=arguments.get("freshness") or None,
            content_type=arguments.get("content_type") or None,
            zone=arguments.get("zone") or None,
            language=arguments.get("language") or None,
        )
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
        raw_text = ""

    # ★ v2.3.81b: 反爬挑战页（真 HTML 被 URL 编码塞进 JS 变量）→ 纯 Python 解码，
    #   比 Chromium 轻得多（零额外开销）；解码失败才用 Chromium 真渲染兜底
    if not raw_text or len(raw_text.strip()) < 200:
        decoded = _decode_js_challenge(url)
        if decoded:
            logger.info("JS 挑战页解码成功: %s (%d字)", url[:40], len(decoded))
            raw_text = decoded

    if not raw_text or len(raw_text.strip()) < 200:
        try:
            from modules.changelog import _ensure_browser
            browser = await _ensure_browser()
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            await page.wait_for_timeout(1200)
            rendered = await page.evaluate("() => document.body ? document.body.innerText : ''")
            await page.close()
            if rendered and len(rendered.strip()) > len(raw_text or ""):
                logger.info("Chromium 渲染兜底成功: %s (%d字)", url[:40], len(rendered))
                raw_text = rendered.strip()[:6000]
        except Exception as e:
            logger.warning("Chromium 渲染兜底失败: %s", e)

    if not raw_text or not raw_text.strip():
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
6. 用纯文本输出，禁止 markdown 语法（# 标题、** 加粗、| 表格在 QQ 里原样显示），对比内容用「名称：值」逐行

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
    if tool_name == "learn_slang":
        # v2.3.24: 让 bot 把新学到的网络黑话记进 data/skills/15_slang.md，
        # 追加后立即成为词典触发词（词表按文件 mtime 自动失效重算）
        from services.llm import append_slang_term
        ok, msg = append_slang_term(
            arguments.get("term", ""),
            arguments.get("meaning", ""),
            arguments.get("category", "LLM 自学") or "LLM 自学",
        )
        return msg
    if tool_name == "web_fetch":
        return await _read_url(arguments.get("url", ""))
    if tool_name == "write_code":
        _lang = arguments.get("language", "python") or "python"
        _arg_desc = arguments.get("description", "")
        # ★ v2.3.81c: 文档类必须用 LLM 传来的 description——里面带着要整理的素材
        #   （如刚搜到的新闻要点）；original_msg 只有"整理成md发我"这种指令，没有内容。
        if _lang in ("markdown", "md", "text", "txt") and _arg_desc.strip():
            desc = _arg_desc
        else:
            desc = original_msg or _arg_desc
        return await _write_code(
            _lang,
            desc,
            user_id, group_id, sender_name, is_group, bot_qq,
        )
    if tool_name == "agent_think":
        return await _agent_think(arguments.get("question", ""), group_id if is_group else user_id, is_group)
    if tool_name == "system_status":
        return await _system_status()
    if tool_name == "run_code":
        return await _run_code(
            arguments.get("language", "python") or "python",
            arguments.get("code", "") or "",
            arguments.get("files") or {},
            arguments.get("stdin", "") or "",
            net=bool(arguments.get("net", False)),
        )
    if tool_name == "ws_files":
        return await _ws_files(
            arguments.get("action", "list"),
            arguments.get("name", "") or "",
            arguments.get("content", "") or "",
        )
    if tool_name == "load_skill":
        # ★ v2.3.64: 按需拉取技能手册正文（data/skills/*.md 的章节）
        from services.llm import get_skill_content, get_skill_index
        _name = (arguments.get("name") or "").strip()
        _content = get_skill_content(_name)
        if _content:
            logger.info("load_skill 命中: %s (%d字)", _name, len(_content))
            return f"【技能 {_name}】\n{_content}"
        logger.info("load_skill 未命中: %r", _name)
        _idx = get_skill_index()
        if _idx:
            return f"没有名为「{_name}」的技能。可用技能：\n{_idx}"
        return f"没有名为「{_name}」的技能。"

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
        mode = arguments.get("mode", "bw")
        if mode == "daily":
            # ★ v2.3.75: daily 是全群日报，无需绑定（旧逻辑先查绑定，把没绑定的用户挡在日榜外）
            from modules.commands import COMMAND_MAP as _CM
            _h = _CM.get("wdsj")
            if _h:
                await _h(["daily", "img"], user_id, group_id, sender_name, is_group, bot_qq)
                return "今日日报图片已生成 (全群)"
            return "wdsj 指令未注册"
        # bw/sw 个人战绩：强制用绑定名（player 参数无效，见工具描述）
        player = await _resolve_player(user_id, "wdsj")
        if not player:
            return "你还未绑定起床战绩账号。"
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
        # ★ v2.3.81e: FC 搜索直接走 perform_search（不再绕 /~search 指令层），把 LLM 按需
        #   填的过滤参数透传给 AnySearch；max_results 同时决定返回条数与结果文本的截断上限
        #   （原来固定 limit=4 → 结果只留 1600 字，多路结果被腰斩）。
        raw_query = (arguments.get("query") or "").strip()
        if not raw_query:
            return "请提供搜索关键词"
        optimized = await _optimize_search_keywords(raw_query)
        try:
            _lim = int(arguments.get("max_results") or 8)
        except Exception:
            _lim = 8
        _lim = max(3, min(_lim, 10))
        from modules.search import perform_search
        return await perform_search(
            optimized, sender_name=sender_name, user_id=user_id,
            chat_id=group_id if is_group else user_id,
            limit=_lim, source="all", is_group=is_group,
            freshness=arguments.get("freshness") or None,
            content_type=arguments.get("content_type") or None,
            zone=arguments.get("zone") or None,
            language=arguments.get("language") or None,
        )
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
