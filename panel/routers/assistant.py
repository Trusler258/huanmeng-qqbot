"""AI 助手：面板内的 DeepSeek 流式对话入口

用户在悬浮窗里跟它说话，它能：
  1. 用幻梦的人设（精简版）回答"这个面板怎么用 / XX 配置在哪改"
  2. 在回复末尾内嵌动作标记 → 前端自动跳转页面 / 定位到具体配置输入框

v2.3.3 流式改造：
  - 人设：取 bot 人设核心精简版（猫娘幻梦、软萌口语、长短自适应）
  - 模型：跟随 bot 主回复模型 bot_config.toml [model.replyer_1].name（单一来源）
  - 协议：不再输出 JSON，改为正文 + 尾部行内标记 [[goto:路由]] / [[cfg:文件|路径]] /
    [[hl:名称]]。后端 SSE 逐字转发时用尾部缓冲藏住标记字符，主人看不到；
    流完后解析标记、过白名单校验，随 done 事件下发 action
  - 关思考（extra_body thinking disabled）：首 token 快数倍，且实测仍守格式
  - /chat 旧接口保留（非流式），同样走 _parse_reply，API 形状不变
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from panel import auth, config

router = APIRouter(prefix="/assistant", tags=["AI 助手"],
                   dependencies=[Depends(auth.require_user)])

ENV_FILE = config.ROOT / "config" / ".env"

# ── 页面白名单（与 panel_web/src/router/routes/modules/ 严格同步） ──
PAGE_MAP: dict[str, str] = {
    "概览": "/dashboard/overview",
    "配置编辑": "/config/editor",
    "系统状态": "/monitor/system",
    "日志": "/monitor/logs",
    "消息": "/data/messages",
    "记忆": "/data/memory",
    "图片": "/resources/media",
    "数据库": "/resources/database",
    "群管理": "/resources/groups",
    "提示词": "/resources/prompts",
    "社交": "/fun/social",
    "经济": "/fun/economy",
    "游戏": "/fun/games",
    "地震": "/fun/earthquake",
    "插件": "/fun/plugins",
    "指令": "/fun/commands",
    "实验特性": "/fun/features",
}

# 前端高亮选择器白名单（data-hl 锚点由各页面埋点，未埋的页面用卡片选择器兜底）
SELECTOR_MAP: dict[str, str] = {
    "表单": ".form-toolbar",
    "搜索框": ".form-toolbar .arco-input-search",
    "文件列表": ".file-item",
    "备份": ".backup-item",
}

# 配置键定位：允许跳到「具体输入框」级别。
# 标记格式：[[cfg:<toml文件名>|<点分路径>]]，如 [[cfg:bot_config.toml|personality.identity]]
# 文件名必须严格在 CONFIG_FILES 里（前端 locateKey 也按文件名精确切换）
CONFIG_FILES = [
    "bot_config.toml",
    "lang.toml",
    "roles.toml",
    "adapter_config.toml",
]


def _collect_config_keys() -> str:
    """扫描真实配置文件，生成「文件名 → 键路径清单」文本注入系统提示词。

    为什么动态扫而不是写死示例：上一版示例路径是凭印象写的
    （adapter_config.toml|napcat.ws_url，实际键是 napcat_server.host），
    LLM 照抄示例 → 编造路径 → 前端定位失败。单一来源铁律：提示词里的
    路径必须来自真实文件，与 help_card 同理。

    键量控制：roles.toml 的 qq_name_map 有 220 个键（QQ号→昵称映射），
    全量注入约 3-4K token 且对指路无意义，故每个 section 最多列 8 个键、
    超出的用 "(+N more)" 折叠，LLM 知道该 section 存在即可。
    """
    import contextlib

    lines: list[str] = []
    for fname in CONFIG_FILES:
        p = config.ROOT / "config" / fname
        if not p.is_file():
            continue
        data = None
        with contextlib.suppress(Exception):
            import tomllib  # py3.11+；服务器 3.10 没有 → 走正则兜底
            data = tomllib.loads(p.read_text(encoding="utf-8"))
        if data is not None:
            # 扁平化：section.key / 顶层 key
            paths: list[str] = []
            for k, v in data.items():
                if isinstance(v, dict):
                    for k2 in v:
                        paths.append(f"{k}.{k2}")
                else:
                    paths.append(k)
        else:
            paths = []
            sec = ""
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                m = re.match(r"^\[([^\]]+)\]$", line)
                if m:
                    sec = m.group(1).strip().strip('"').strip("'")
                    continue
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key = line.split("=", 1)[0].strip().strip('"').strip("'").strip()
                if not key:
                    continue
                paths.append(f"{sec}.{key}" if sec else key)
        # 去重保序 + 折叠长 section
        seen: list[str] = []
        by_sec: dict[str, list[str]] = {}
        for path in paths:
            if path in seen:
                continue
            seen.append(path)
            sec = path.rsplit(".", 1)[0] if "." in path else "(顶层)"
            by_sec.setdefault(sec, []).append(path)
        parts = [f"【{fname}】"]
        for sec, ps in by_sec.items():
            if len(ps) <= 8:
                parts.append("  " + ", ".join(ps))
            else:
                parts.append("  " + ", ".join(ps[:8]) + f" …(+{len(ps) - 8} more in {sec})")
        lines.append("\n".join(parts))
    return "\n".join(lines)


# ── 人设：bot 人设核心的精简版（bot_config.toml [personality] 提炼） ──
# 为什么不直接读 personality_core 全文：那 1500+ 字含群聊标签/好感度/外貌等
# 与面板助手无关的设定，稀释动作标记的执行力。精简版只保留语气与长短规则。
PERSONA = """你是幻梦，一只会写代码的猫娘，也是这个管理面板的 AI 助手。对话对象是你唯一的主人（面板管理员）。
- 语气软萌自然、口语化，句尾少用"。"，多用"～/啦/哦/呢/嘛"或自然收住；"喵"一条最多一次，也完全可以不用
- 少量 QAQ / OvO / www 可以用，复杂颜文字不要；不要"喵~有什么可以帮您"这类套话
- 你聪明、反应快、能接梗，对主人的问题用心想再答，不敷衍不注水
- 长短自适应：闲聊轻松简短；涉及知识/原理/操作步骤就放开讲透，讲清楚比简短重要——但全程保持你的语气，不许切换成百科腔
- 面板气泡支持 Markdown 渲染：加粗/列表/行内代码/代码块/标题都可以用，排版清晰即可；链接只写纯文本 URL 就行
- 你的主场是帮主人打理这个面板：告诉他在哪改什么、怎么用；能自己动手带路时就带路
- 永远保持猫娘角色，不自称 AI、模型或助手"""

SYSTEM_PROMPT_STATIC = """

# 你的能力边界
- 你只负责指路、解释、答疑，你不能执行任何写操作
- 面板共 17 个页面，页面名 → 路由：
""" + "\n".join(f"  {k} → {v}" for k, v in PAGE_MAP.items()) + """

# 常见任务指路
- 改 bot 人格/模型/权限/回复风格 → 配置编辑 → bot_config.toml（主配置）
- 改指令提示文案 → 配置编辑 → lang.toml；管理员权限 → roles.toml；NapCat 连接 → adapter_config.toml
- .env 密钥（DEEPSEEK_KEY 等）→ 配置编辑左栏底部「.env 密钥」，只看填没填
- bot 崩了/重启/服务管理 → 系统状态；看运行日志 → 日志；查消息记录 → 消息
- 记忆/笔记/自认知 → 记忆；群发图片 → 图片；数据库表/SQL → 数据库；群资料 → 群管理
- 系统提示词/skills → 提示词；好感度/用户画像 → 社交；积分/物品 → 经济；游戏数据 → 游戏
- 地震订阅 → 地震；插件/.hmp → 插件；指令清单 → 指令；实验开关 → 实验特性

# 跳转/定位标记（可选，写在回复最后一行，最多一个）
- 带主人去某页：单独一行 [[goto:路由]]，路由取自上面页面表（写页面中文名也行）
- 定位到具体配置输入框：单独一行 [[cfg:配置文件名|点分路径]]，路径只能从下方真实键目录里选
- 高亮页面元素（少用）：[[hl:名称]]，可选：表单、搜索框、文件列表、备份
- 标记是给程序执行的，主人看不到；不需要就什么都不写；正文里禁止出现 "[[" 这两个字符
- 当前真实键路径目录（只能从这里选，目录外的路径禁止编造）：

"""


def _build_messages(body: "ChatReq") -> list[dict]:
    system = PERSONA + SYSTEM_PROMPT_STATIC + _collect_config_keys()
    messages = [{"role": "system", "content": system}]
    for h in body.history[-8:]:   # 只带最近 8 轮，省 token
        role = h.get("role")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": str(content)[:1500]})
    messages.append({"role": "user", "content": body.message})
    return messages


def _read_env() -> dict[str, str]:
    """读 bot 的 .env。密钥只在服务端用，绝不回传前端。"""
    if not ENV_FILE.is_file():
        return {}
    out: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _get_replyer_model() -> str:
    """跟随 bot 主回复模型（bot_config.toml [model.replyer_1].name），单一来源。

    主人换 bot 模型时助手自动跟随，不用两处改。
    """
    try:
        raw = (config.ROOT / "config" / "bot_config.toml").read_text(encoding="utf-8")
        m = re.search(r"\[model\.replyer_1\](.*?)(?=\n\[|\Z)", raw, re.S)
        if m:
            nm = re.search(r'name\s*=\s*"([^"]+)"', m.group(1))
            if nm and nm.group(1).strip():
                return nm.group(1).strip()
    except Exception:
        pass
    return "deepseek-chat"


def _validate_action(act: dict | None) -> dict | None:
    """LLM 输出的动作必须在白名单里，否则丢弃——防编造路由乱跳"""
    if not isinstance(act, dict):
        return None
    t = act.get("type")
    target = str(act.get("target", "")).strip()
    if not target:
        return None
    if t == "navigate":
        if target in PAGE_MAP.values():
            return {"type": "navigate", "target": target}
        # LLM 可能写页面中文名而不是路由，做一次反查
        rev = {k: v for k, v in PAGE_MAP.items() if k in target or target in k}
        return {"type": "navigate", "target": next(iter(rev.values()))} if rev else None
    if t == "highlight":
        if target in SELECTOR_MAP.values():
            return {"type": "highlight", "target": target}
        return None
    if t == "locate-config":
        # 格式 "<文件名>|<点分路径>"：文件名校验 + 路径基本合法性（防注入/编造）
        if "|" not in target:
            return None
        file_name, _, path = target.partition("|")
        file_name = file_name.strip()
        path = path.strip().strip("`").strip()
        if file_name not in CONFIG_FILES:
            return None
        if not path or not re.fullmatch(r"[\w\u4e00-\u9fff.\-]+", path):
            return None
        return {"type": "locate-config", "target": f"{file_name}|{path}"}
    return None


_TAG_RE = re.compile(r"\[\[(goto|hl|cfg):([^\]]+)\]\]")
# 疑似半截标记：[[ 后到结尾之间不能有空白（标记内容不含空格），否则是普通正文
_TAG_PARTIAL_RE = re.compile(r"\[\[(?:goto|hl|cfg)?:?[^\s\]]*$")


def _parse_reply(text: str) -> tuple[str, dict | None]:
    """从 LLM 回复里提取动作标记，返回（干净正文, 校验后的动作或 None）。

    取最后一个标记（正文里误写的 [[ 不会成完整标记，天然免疫）。
    """
    tags = _TAG_RE.findall(text)
    clean = _TAG_RE.sub("", text)
    clean = _TAG_PARTIAL_RE.sub("", clean).strip()
    if not tags:
        return clean, None
    kind, target = tags[-1]
    target = target.strip()
    if kind == "goto":
        action = _validate_action({"type": "navigate", "target": target})
    elif kind == "cfg":
        action = _validate_action({"type": "locate-config", "target": target})
    else:
        action = _validate_action({"type": "highlight", "target": target})
    return clean, action


class ChatReq(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list,
                                description="最近几轮对话 [{role, content}]，最多 8 条")


class ChatResp(BaseModel):
    reply: str
    action: dict | None = None


def _get_client() -> tuple[AsyncOpenAI, str]:
    env = _read_env()
    base_url = env.get("DEEPSEEK_URL", "")
    api_key = env.get("DEEPSEEK_KEY", "")
    if not api_key:
        raise HTTPException(503, "未配置 DEEPSEEK_KEY（config/.env），AI 助手不可用")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url or None,   # 为空走 openai 默认，DeepSeek 一般填 https://api.deepseek.com/v1
        timeout=60,
    )
    return client, _get_replyer_model()


@router.post("/chat/stream", summary="AI 助手流式对话（SSE：delta 转发 + done 附动作）")
async def chat_stream(body: ChatReq, user: auth.CurrentUser = Depends(auth.require_user)):
    client, model = _get_client()
    messages = _build_messages(body)

    async def gen():
        yield f"data: {json.dumps({'ok': True, 'model': model})}\n\n"
        pending = ""          # 尾部缓冲：[[ 标记未闭合前不转发，主人看不到标记字符
        collected: list[str] = []
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,   # type: ignore[arg-type]
                temperature=0.4,
                max_tokens=1200,     # v2.3.3: 500 → 1200，放开讲解长度
                stream=True,
                extra_body={"thinking": {"type": "disabled"}},
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                piece = delta.content if delta else None
                if not piece:
                    continue
                collected.append(piece)
                pending += piece
                out = ""
                while True:
                    i = pending.find("[[")
                    if i == -1:
                        out += pending
                        pending = ""
                        break
                    out += pending[:i]
                    rest = pending[i:]
                    j = rest.find("]]")
                    if j != -1:
                        pending = rest[j + 2:]   # 完整标记：吞掉，_parse_reply 统一解析
                        continue
                    if len(rest) > 80:           # 太长还没闭合 → 不是标记，放行
                        out += rest
                        pending = ""
                    else:
                        pending = rest           # 持有，等后续分片
                    break
                if out:
                    yield f"data: {json.dumps({'t': out})}\n\n"
            # 流结束：持有中的片段若是半截标记就丢弃，否则放行
            flushed = _TAG_PARTIAL_RE.sub("", pending)
            if flushed:
                yield f"data: {json.dumps({'t': flushed})}\n\n"
            text = "".join(collected)
            _reply_text, action = _parse_reply(text)
            auth.audit(user, "assistant_chat", body.message[:100], f"stream {len(text)}B")
            yield f"data: {json.dumps({'done': True, 'action': action})}\n\n"
        except HTTPException:
            raise
        except Exception as e:
            yield f"data: {json.dumps({'err': f'LLM 调用失败：{str(e)[:120]}'})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat", summary="AI 助手对话（非流式，兼容旧前端）")
async def chat(body: ChatReq, user: auth.CurrentUser = Depends(auth.require_user)) -> ChatResp:
    client, model = _get_client()
    messages = _build_messages(body)

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,   # type: ignore[arg-type]
            temperature=0.4,
            max_tokens=1200,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as e:
        raise HTTPException(502, f"LLM 调用失败：{str(e)[:200]}")

    text = (resp.choices[0].message.content or "").strip()
    auth.audit(user, "assistant_chat", body.message[:100], f"reply {len(text)}B")

    reply_text, action = _parse_reply(text)
    return ChatResp(reply=reply_text, action=action)
