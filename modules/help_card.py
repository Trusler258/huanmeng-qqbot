"""
help_card — /~help 三列网格指令卡片生成器
==========================================
- 枚举 CapabilityRegistry 的全部 command 能力（含插件注册指令）
- 按类别分组渲染为「三列网格」HTML（data/templates/help_card.html）
- Playwright 截图输出 data/help_card.png，cmd_help 直接读取该路径

用法（后台）:
    from modules.help_card import build_help_card_image
    path = await build_help_card_image()
"""
from __future__ import annotations

import html as _html
import re
from datetime import datetime
from pathlib import Path

from core.logger import get_logger

logger = get_logger("help_card")

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE = _ROOT / "data" / "templates" / "help_card.html"
_OUTPUT = _ROOT / "data" / "help_card.png"

# ── 分类定义：硬编码（name → 分类）优先，未匹配再按描述关键词兜底 ──
# 排除：内部测试指令、辅助函数
_EXCLUDE = {
    "testsys", "testok", "jsonraw", "md",
    "add_relation",   # 内部入口
    "friend_add", "friend_reject", "friend_list",  # 别名合并到「添加/拒绝/好友列表」
    "roll_dice", "calc", "write_code", "search_web",  # FC 工具别名（已注册但仅内部用）
    "agent_think", "get_time",
    "s", "search", "search_web",  # 内部别名 → 仅显示 search
}

# 硬编码分类（主分类依据，name 是权威）
_CATEGORY = {
    # 聊天
    "help": "聊天",
    # 工具
    "s": "工具", "search": "工具", "read": "工具", "search_web": "工具",
    "remind": "工具", "提醒": "工具", "countdown": "工具", "倒计时": "工具",
    "translate": "工具", "翻译": "工具", "tr": "工具",
    "whois": "工具", "域名": "工具",
    "analyze": "工具",
    "抽": "工具", "chou": "工具",
    # 数据
    "stats": "数据", "统计": "数据", "unstats": "数据", "setstats": "数据",
    "recall": "数据", "favlist": "数据",
    "balance": "数据", "cost": "数据", "tokens": "数据",
    "dbsearch": "数据", "回顾": "数据",
    "box": "数据", "快递": "数据",
    # 游戏
    "wzq": "游戏", "五子棋": "游戏", "xq": "游戏", "象棋": "游戏",
    "luck": "游戏", "dice": "游戏", "掷": "游戏",
    "pc": "游戏", "sys": "游戏", "phone": "游戏",
    "pgr": "游戏", "wdsj": "游戏",
    # 创作
    "draw": "创作", "绘画": "创作", "video": "创作", "视频": "创作",
    "voice": "创作", "语音": "创作",
    "img2video": "创作", "图生视频": "创作", "img": "创作",
    "摸头": "创作", "motou": "创作",
    # 系统
    "info": "系统", "ping": "系统", "favlist": "数据",
    "restart": "系统", "weather": "系统", "天气": "系统",
    "eq": "系统", "地震": "系统",
    "updateinfo": "系统", "up": "系统", "update": "系统", "upd": "系统",
    "_cmd_update": "系统", "gh": "游戏",
    "reload": "系统", "testok": "系统", "nasa": "系统",
    "tuflevel": "系统", "tuf谱面": "系统", "tufsearch": "系统",
    "tufd": "系统", "tufpage": "系统",
    # admin
    "owner": "admin", "memory": "admin", "preset": "admin",
    "ignore": "admin", "unignore": "admin", "resetfav": "admin",
    "op": "admin", "persona": "admin", "人格": "admin",
    "master": "admin", "主人": "admin", "sleep": "admin",
    "hanxu": "admin", "含蓄": "admin", "叙事": "admin",
    "add_relation": "admin", "添加关系": "admin", "nickname": "admin",
    "添加": "admin", "拒绝": "admin", "好友列表": "admin",
    "friend_add": "admin", "friend_reject": "admin", "friend_list": "admin",
    "leave": "admin",
    # 插件
    "plugin": "插件", "插件": "插件", "apy": "插件",
    # 插件指令分类
    "dice": "游戏", "摸头": "创作", "motou": "创作",
    "checkin": "数据", "签到": "数据", "sign": "数据",
    "points": "数据", "积分": "数据", "shop": "工具", "商店": "工具",
}

# 纯 FC 工具（已注册 command 能力但实为工具别名，不展示在指令卡）
_TOOL_ONLY = {
    "roll_dice", "calc", "write_code", "search_web", "agent_think", "get_time",
}

# 插件指令（运行时经 plugin.register_command 动态挂进 COMMAND_MAP，静态 import 不可见）
# 按「功能」组织：主名(英文) → (作用描述, [中文别名])
_PLUGIN_COMMANDS: dict[str, tuple] = {
    "dice":   ("掷骰子，掷骰奖励积分", []),
    "motou":  ("用图片生成摸头 GIF", ["摸头"]),
    "checkin": ("每日签到得积分", ["签到", "sign"]),
    "points": ("查看积分余额", ["积分"]),
    "shop":   ("积分商店购买商品", ["商店"]),
}

# 描述兜底：所有指令的「真实作用描述」（读函数实现核实，不含用法）
_EXTRA_DESC = {
    # 聊天
    "help": "指令手册：总览卡片 + 单个指令详情",
    # 工具
    "search": "联网搜索（百度/百科/Bing）",
    "s": "联网搜索（百度/百科/Bing）",
    "search_web": "联网搜索（百度/百科/Bing）",
    "read": "深度读取网页正文并总结",
    "whois": "域名注册信息查询",
    "域名": "域名注册信息查询",
    "write_code": "按描述生成代码文件",
    "remind": "定时提醒（到点自动 @）",
    "提醒": "定时提醒（到点自动 @）",
    "countdown": "倒计时提醒",
    "倒计时": "倒计时提醒",
    "tr": "文本翻译",
    "翻译": "文本翻译",
    "analyze": "零上下文日志分析（不带聊天记录）",
    "抽": "随机抽取一个选项",
    "jsonraw": "输出 LLM 原始 JSON",
    "md": "发送 Markdown 卡片",
    # 数据
    "stats": "群聊今日统计",
    "统计": "群聊今日统计",
    "unstats": "暂停本群统计",
    "setstats": "恢复本群统计",
    "recall": "查看群消息撤回记录",
    "favlist": "查看好感度排行榜",
    "balance": "DeepSeek API 余额查询",
    "cost": "Token 消耗统计与费用",
    "tokens": "计算文本 token 数和费用",
    "dbsearch": "全文检索聊天历史",
    "回顾": "全文检索聊天历史",
    "box": "快递物流查询",
    "checkin": "每日签到得积分",
    "签到": "每日签到得积分",
    "sign": "每日签到得积分",
    "points": "查看积分余额",
    "积分": "查看积分余额",
    # 游戏
    "wzq": "五子棋对战（人机/双人）",
    "五子棋": "五子棋对战（人机/双人）",
    "xq": "中国象棋对战",
    "象棋": "中国象棋对战",
    "luck": "每日运势抽签",
    "dice": "掷骰子得积分",
    "gh": "公会登记管理",
    "pgr": "Phigros 谱面查询",
    "wdsj": "洛花星雨战绩查询",
    "sys": "PC 状态卡片 / 截屏",
    "pc": "PC 状态卡片 / 截屏",
    "phone": "手机实时状态",
    # 创作
    "draw": "AI 文生图 / 图生图",
    "绘画": "AI 文生图 / 图生图",
    "video": "AI 文生视频",
    "视频": "AI 文生视频",
    "voice": "LLM 文本转语音",
    "语音": "LLM 文本转语音",
    "img2video": "图片转视频",
    "图生视频": "图片转视频",
    "img": "随机二次元图片",
    "摸头": "用图片生成摸头 GIF",
    "motou": "用图片生成摸头 GIF",
    # 系统
    "info": "系统运行状态",
    "ping": "在线检测 / 延迟测试",
    "restart": "远程重启 bot",
    "reload": "热重载配置",
    "weather": "天气查询（卡片）",
    "天气": "天气查询（卡片）",
    "eq": "地震速报与订阅",
    "地震": "地震速报与订阅",
    "updateinfo": "更新日志",
    "up": "更新日志",
    "update": "从 git 拉取代码更新 bot",
    "upd": "从 git 拉取代码更新 bot",
    "nasa": "NASA 每日天文图",
    "tuflevel": "TUF 谱面详情查询",
    "tuf谱面": "TUF 谱面详情查询",
    "tufsearch": "搜索 TUF 谱面",
    "tufd": "下载 TUF 谱面文件",
    "tufpage": "TUF 搜索结果翻页",
    # admin
    "owner": "配置与数据管理",
    "memory": "三层记忆查询",
    "preset": "系统提示词注入管理",
    "op": "OP 权限管理",
    "ignore": "全群忽略某用户（仅 admin）",
    "unignore": "解除全群忽略（仅 admin）",
    "resetfav": "重置好感度数据（仅 admin）",
    "persona": "私聊人格切换",
    "人格": "私聊人格切换",
    "master": "指定私聊主人",
    "主人": "指定私聊主人",
    "sleep": "切换睡觉模式（仅 admin）",
    "hanxu": "切换含蓄叙述风格（仅 admin）",
    "含蓄": "切换含蓄叙述风格（仅 admin）",
    "叙事": "切换含蓄叙述风格（仅 admin）",
    "nickname": "同步群昵称到本地",
    "add_relation": "添加用户关系（仅 admin）",
    "添加关系": "添加用户关系（仅 admin）",
    "添加": "批准好友请求",
    "拒绝": "拒绝好友请求",
    "好友列表": "查看待处理好友请求",
    "leave": "退群并清理本群数据（仅 admin）",
    "setstats": "恢复本群统计",
    # 插件
    "plugin": "插件管理（安装/卸载/更新）",
    "插件": "插件管理（安装/卸载/更新）",
    "apy": "插件人工审批回执",
    "shop": "积分商店",
    "商店": "积分商店",
}

# 描述兜底：关键词 → 分类（顺序：最具体先匹配）
_DESC_FALLBACK = [
    ("数据", ("统计", "撤回", "好感", "余额", "消耗", "用量", "积分", "回溯", "回顾", "快递")),
    ("游戏", ("五子棋", "象棋", "运气", "棋", "骰", "公会", "战绩")),
    ("创作", ("画图", "生图", "绘画", "视频", "图生", "文生", "语音", "摸头", "GIF")),
    ("系统", ("运行", "延迟", "更新", "信息", "状态", "重启", "地震", "天气", "NASA")),
    ("工具", ("搜索", "提醒", "倒计时", "翻译", "域名", "代码", "抽", "分析")),
    ("admin", ("admin", "管理", "配置", "记忆", "重载", "忽略", "人格", "注入", "主人")),
    ("插件", ("插件", "能力", "审批")),
]


def _group(name: str, desc: str) -> str:
    """按 name 硬编码优先 → 描述关键词兜底"""
    if name in _CATEGORY:
        return _CATEGORY[name]
    text = f"{name} {desc}".lower()
    for label, keys in _DESC_FALLBACK:
        for k in keys:
            if k.lower() in text:
                return label
    return "工具"


def _extract_desc(main: str, handler, keys: list[str], cap_desc: dict) -> str:
    """提取指令「作用描述」（不含用法）

    优先级：_EXTRA_DESC（人工精校）→ docstring 中 — 分隔后的作用段
    → _CMD_DESC → capability description → docstring 第一行剥离去 /~ 用法
    """
    if main in _EXTRA_DESC:
        return _EXTRA_DESC[main]
    doc = (getattr(handler, '__doc__', '') or '').strip()
    if doc:
        first = doc.split('\n')[0]
        # 「—」分隔：取作用部分（如 "/~whois <域名> — 查询域名注册信息" → 后半）
        if '—' in first:
            return first.split('—', 1)[1].strip()
        # 无分隔：剥离去 /~xxx 用法片段，保留作用
        cleaned = re.sub(r'/~~?[\w\u4e00-\u9fff]+(?:\s*<[^>]+>)*\s*', '', first).strip()
        cleaned = re.sub(r'\s{2,}', ' ', cleaned)
        if cleaned:
            return cleaned[:48]
    # 兜底链
    try:
        from services.llm import _CMD_DESC
    except Exception:
        _CMD_DESC = {}
    return (cap_desc.get(main) or _CMD_DESC.get(main) or _CMD_DESC.get(keys[0], ""))[:48]


def build_help_html(bot_name: str = "幻梦") -> str:
    """收集全部指令 → 填充三列网格 HTML

    指令源 = COMMAND_MAP 的键（用户实际输入），别名归并显示主名；
    描述 = capability description / _CMD_DESC / 兜底。
    """
    # 1. COMMAND_MAP 键 = 用户实际可输入指令（权威）
    try:
        from modules.commands import COMMAND_MAP
    except Exception as e:
        logger.warning("无法加载 COMMAND_MAP: %s", e)
        COMMAND_MAP = {}

    # 2. 描述来源（capability 注册表合并插件 description；_CMD_DESC 核心描述）
    cap_desc: dict[str, str] = {}
    cap_plugin: set[str] = set()
    try:
        from core.capability import get_capability_registry
        reg = get_capability_registry()
        reg.discover()
        for c in (list(reg._caps.values()) if hasattr(reg, "_caps") else []):
            cat = getattr(c, "category", "") or ""
            if "command" not in str(cat).lower():
                continue
            cname = getattr(c, "name", "") or ""
            if not cname:
                continue
            d = getattr(c, "description", "") or ""
            src = getattr(c, "source", "") or ""
            if d:
                cap_desc.setdefault(cname, d)
            if str(src).startswith("plugin:") or str(src).startswith("cap:"):
                cap_plugin.add(cname)
    except Exception as e:
        logger.warning("capability 枚举失败（降级仅 COMMAND_MAP）: %s", e)

    try:
        from services.llm import _CMD_DESC
    except Exception:
        _CMD_DESC = {}

    # 3. 别名归并：COMMAND_MAP 多个键指向同一 handler → 保留「主名」（最长键=完整名）
    #    卡片显示主名，例如 五子棋/wzq → 显示 wzq
    handler_groups: dict[object, list[str]] = {}
    for key in COMMAND_MAP:
        if key in _EXCLUDE:
            continue
        handler_groups.setdefault(COMMAND_MAP[key], []).append(key)

    shown: dict[str, tuple] = {}  # name → (desc, is_plugin, aliases)
    for keys in handler_groups.values():
        # 主名选择：优先英文键（非中文），否则取注册顺序第一
        main = next((k for k in keys if not any('\u4e00' <= ch <= '\u9fff' for ch in k)), keys[0])
        handler = COMMAND_MAP[main]
        desc = _extract_desc(main, handler, keys, cap_desc)
        # 别名列表（中文 + 其他英文别名），如 /~五子棋 /~wzq
        aliases = [k for k in keys if k != main]
        shown[main] = (desc, main in cap_plugin, aliases)

    # 3b. 合并插件指令（运行时动态注册，静态不可见 → 手动维护表，按功能归并）
    for cname, (cdesc, caliases) in _PLUGIN_COMMANDS.items():
        if cname in shown or cname in _EXCLUDE:
            continue
        shown[cname] = (cdesc, True, list(caliases))

    # 3. 按分类分组（保持注册顺序，别名紧凑排列）
    groups: dict[str, list] = {}
    for name, (desc, is_plugin, aliases) in shown.items():
        g = _group(name, desc)
        groups.setdefault(g, []).append((name, desc, is_plugin, aliases))

    # 4. 渲染分类 HTML
    total = len(shown)
    order = ["聊天", "工具", "数据", "游戏", "创作", "系统", "admin", "插件"]
    sec_htmls = []
    for g in order:
        items = groups.get(g)
        if not items:
            continue
        n = len(items)
        items_html = []
        for name, desc, is_plugin, aliases in items:
            cmd_txt = _html.escape(name)
            desc_txt = _html.escape(desc or "")
            desc_txt = desc_txt.split("\n")[0][:44]
            badge = '<span class="badge">插件</span>' if is_plugin else ""
            # 中文别名（灰色斜体，显示在英文下方）
            cn_alias = [a for a in aliases if any('\u4e00' <= ch <= '\u9fff' for ch in a)]
            alias_txt = ""
            if cn_alias:
                alias_txt = f'<div class="alias">/{_html.escape(cn_alias[0])}</div>'
            cls = "item plugin" if is_plugin else "item"
            items_html.append(
                f'<div class="{cls}"><div class="cmd-row">/~{cmd_txt}{badge}</div>'
                f'{alias_txt}<div class="desc">{desc_txt}</div></div>'
            )
        sec_htmls.append(
            f'<div class="section"><div class="section-title">'
            f'<span class="dot"></span>{g}<span class="tag">{n} 条</span></div>'
            f'<div class="grid">{"".join(items_html)}</div></div>'
        )

    # 5. 填模板
    tpl = _TEMPLATE.read_text(encoding="utf-8")
    out = tpl.replace("{{BOT_NAME}}", _html.escape(bot_name))
    out = out.replace("{{TOTAL}}", str(total))
    out = out.replace("{{SECTIONS}}", "\n".join(sec_htmls))
    out = out.replace("{{DATE}}", datetime.now().strftime("%Y-%m-%d"))
    return out


async def build_help_card_image(bot_name: str = "幻梦") -> str | None:
    """生成三列网格 help 卡片 PNG → data/help_card.png（cmd_help 读取路径）"""
    try:
        from modules.changelog import render_card_to_image
    except Exception as e:
        logger.warning("无法导入渲染器: %s", e)
        return None

    html_str = build_help_html(bot_name)
    path = await render_card_to_image(html_str, output_filename="help_card.png", width=780)
    if path and Path(path).exists():
        logger.info("help 卡片已生成: %s (%.1fKB)", path, Path(path).stat().st_size / 1024)
        return path
    logger.error("help 卡片渲染失败")
    return None


if __name__ == "__main__":
    import asyncio
    asyncio.run(build_help_card_image())
