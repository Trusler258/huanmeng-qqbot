"""AI 助手：面板内的 DeepSeek 对话入口

用户在悬浮窗里跟它说话，它能：
  1. 回答"这个面板怎么用 / XX 配置在哪改"
  2. 返回结构化指令 → 前端自动跳转页面 / 高亮元素

设计要点：
  - LLM 配置直接读 bot 的 config/.env（DEEPSEEK_URL / DEEPSEEK_KEY），
    不新增任何配置项——用户明确要求"直接用 env 的"
  - 系统提示词单独维护在本文件顶部（PAGE_MAP + SYSTEM_PROMPT），
    列全 17 个页面与配置文件说明，LLM 才知道往哪跳
  - 返回 JSON 两种动作：navigate（跳路由）/ highlight（CSS 选择器高亮）
    LLM 只许选白名单里的路由与选择器，防注入乱跳
  - 不写任何文件、不调用任何写接口——纯只读 + 前端导航，风险面为零
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
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

SYSTEM_PROMPT = """你是幻梦 QQ Bot 控制面板的 AI 助手，运行在管理后台悬浮窗里。
用户是面板管理员，他会问你"XX 在哪改 / 怎么用 / 出了什么问题"之类的问题。

# 你的能力边界
- 你只负责指路、解释、答疑，你不能执行任何操作
- 你可以引导用户跳转页面，或让前端高亮某个元素
- 面板共 17 个页面，页面名 → 前端路由：
""" + "\n".join(f"  {k} → {v}" for k, v in PAGE_MAP.items()) + """

# 常见任务指路
- 改 bot 人格/模型/权限/回复风格 → 页面「配置编辑」，左侧选 bot_config.toml（主配置）
- 改指令提示文案 → 配置编辑 → lang.toml
- 管理员权限 → 配置编辑 → roles.toml
- NapCat 连接 → 配置编辑 → adapter_config.toml
- .env 密钥（DEEPSEEK_KEY 等）→ 配置编辑左栏底部「.env 密钥」，只看填没填，值不可见
- bot 崩了/重启/服务管理 → 「系统状态」
- 看运行日志/实时日志 → 「日志」
- 查消息记录/全文搜索 → 「消息」
- 记忆/笔记/自认知/skills → 「记忆」
- 群发图片/回收站 → 「图片」
- 数据库表/SQL/检索 → 「数据库」
- 群资料/群记忆/成员 → 「群管理」
- 系统提示词/skills 文件 → 「提示词」
- 好感度/用户画像/群统计 → 「社交」
- 积分/物品 → 「经济」
- 五子棋/数学/倒数/抽奖数据 → 「游戏」
- 地震订阅/推送记录 → 「地震」
- 插件目录/.hmp 包 → 「插件」
- 指令清单/LLM 可见性审计 → 「指令」
- 实验开关 → 「实验特性」

# 输出格式（严格遵守，用户可见回复只能是 JSON）
{"reply": "给用户看的中文回答，简洁直接",
 "action": {"type": "navigate", "target": "<上表中的路由>"},
 "action": null}

- action 可以是 null（纯问答不需要跳转）
- navigate 的 target 必须严格取自上表路由，禁止编造
- highlight 的 target 是 CSS 选择器，只在用户问"页面上某个元素"时用：
  可选值：""" + "、".join(f'"{k}"→{v}' for k, v in SELECTOR_MAP.items()) + """
- 不要输出 Markdown 代码块标记，就是裸 JSON
- reply 保持简短（一两句话），管理员没耐心读长篇大论"""


class ChatReq(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list,
                                description="最近几轮对话 [{role, content}]，最多 8 条")


class ChatResp(BaseModel):
    reply: str
    action: dict | None = None


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
        # LLM 可能返回页面中文名而不是路由，做一次反查
        rev = {k: v for k, v in PAGE_MAP.items() if k in target or target in k}
        return {"type": "navigate", "target": next(iter(rev.values()))} if rev else None
    if t == "highlight":
        if target in SELECTOR_MAP.values():
            return {"type": "highlight", "target": target}
        return None
    return None


@router.post("/chat", summary="AI 助手对话（返回回复 + 可选导航/高亮指令）")
async def chat(body: ChatReq, user: auth.CurrentUser = Depends(auth.require_user)) -> ChatResp:
    env = _read_env()
    base_url = env.get("DEEPSEEK_URL", "")
    api_key = env.get("DEEPSEEK_KEY", "")
    if not api_key:
        raise HTTPException(503, "未配置 DEEPSEEK_KEY（config/.env），AI 助手不可用")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url or None,   # 为空走 openai 默认，DeepSeek 一般填 https://api.deepseek.com/v1
        timeout=30,
    )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in body.history[-8:]:   # 只带最近 8 轮，省 token
        role = h.get("role")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": str(content)[:1500]})
    messages.append({"role": "user", "content": body.message})

    try:
        resp = await client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,   # type: ignore[arg-type]
            temperature=0.3,
            max_tokens=500,
        )
    except Exception as e:
        raise HTTPException(502, f"LLM 调用失败：{str(e)[:200]}")

    text = (resp.choices[0].message.content or "").strip()
    auth.audit(user, "assistant_chat", body.message[:100], f"reply {len(text)}B")

    # 解析 JSON（容错：剥掉 ```json 包裹）
    reply_text = text
    action = None
    try:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            reply_text = str(obj.get("reply", text))
            action = _validate_action(obj.get("action"))
    except Exception:
        pass   # 不是 JSON 就当纯文本回复

    return ChatResp(reply=reply_text, action=action)
