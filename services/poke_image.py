"""Poke2API 图片生成（OpenAI Images 兼容接口）。

文档：https://docs.poke2api.com/guide/image-generation
- 端点：POST {base}/v1/images/generations
- 鉴权：Authorization: Bearer <POKE_API_KEY>（key 放 config/.env，禁止硬编码）
- 参数：model / prompt 必填；size / quality / n / response_format 可选
- 返回：data[0].b64_json 或 data[0].url

与框架无关，可被 modules/agnes.py（/~draw）或脚本直接调用。
"""
from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path

import httpx

from core.logger import get_logger

logger = get_logger("poke_image")

DEFAULT_BASE = "https://www.poke2api.com"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "agnes_output"

# 别名 → 真实模型 ID（顺序即 /~draw model 的列表顺序）
MODELS: dict[str, str] = {
    "sunburst": "gpt-image-2.5-sunburst",
    "flare": "gpt-image-2.5-flare",
    "gpt-image-2": "gpt-image-2",
    "gemini": "gemini-3.1-flash-image",
    "grok": "grok-imagine-image-quality",
    "grok2": "grok-imagine-image-2.0",
    "seedream": "doubao-seedream-5-0-pro",
    "seedream4": "doubao-seedream-4-0",
}

# 别名展示名（给 /~draw model 列表用）
MODEL_LABELS: dict[str, str] = {
    "sunburst": "ChatGPT Image 2.5 Sunburst（默认）",
    "flare": "ChatGPT Image 2.5 Flare",
    "gpt-image-2": "ChatGPT Image 2",
    "gemini": "Gemini 3.1 Flash Image",
    "grok": "Grok Imagine Quality",
    "grok2": "Grok Imagine 2.0",
    "seedream": "豆包 Seedream 5.0 Pro",
    "seedream4": "豆包 Seedream 4.0",
}

# 相对能力（不是跑分，是"跟同批模型比"的定位；耗时为本机实测值，没测就不写）
MODEL_ABILITY: dict[str, str] = {
    "sunburst": "最稳最准，指令跟随最好",
    "flare": "风格张力强，画面更有戏",
    "gpt-image-2": "上一代旗舰，稳但细节偏旧",
    "gemini": "便宜快，细节一般，2k 会被降回 1024",
    "grok": "出图最快，偏写实",
    "grok2": "构图更活，速度也快",
    "seedream": "中文语义/国风强，但最贵",
    "seedream4": "中文语义好，性价比款",
}

# 实测耗时（秒）；没实测的不出现
MODEL_SPEED: dict[str, int] = {
    "sunburst": 41,
    "gemini": 26,
    "grok": 9,
    "seedream": 25,
    "seedream4": 7,
}

DEFAULT_MODEL = "gpt-image-2.5-sunburst"

# ── 价格与成本预估（v2.3.104 第 2 轮）────────────────────
# 每张「1k 基准尺寸」（1536x864 ≈ 1.33MP）的预估人民币。仅用于对话里给"预计 ¥X"，
# 不是账单；可用 config/bot_config.toml 的 [image.price] 覆盖（键=模型 ID 或别名）。
# ⚠️ 依据：2026-10-21 控制台实测 doubao-seedream-5-0-pro 2 次（含一张 2048x2048）
#    共 32.81K token = $1.72 → 2k 单张约 ¥6.2，按面积比 4 反推 1k 约 ¥1.5
_PRICE_DEFAULTS: dict[str, float] = {
    "gpt-image-2.5-sunburst": 1.5,
    "gpt-image-2.5-flare": 1.5,
    "gpt-image-2": 0.9,
    "gemini-3.1-flash-image": 0.15,
    "grok-imagine-image-quality": 0.4,
    "grok-imagine-image-2.0": 0.4,
    "doubao-seedream-5-0-pro": 1.5,
    "doubao-seedream-4-0": 0.6,
}

# 成本档位阈值（元/张，按 1k 基准算）
_TIER_CHEAP = 0.3
_TIER_MID = 1.0

_ROOT = Path(__file__).resolve().parent.parent
_CFG_CACHE: dict = {"mtime": 0.0, "image": {}}


def _image_section() -> dict:
    """读 bot_config.toml 的 [image] 段（按 mtime 缓存，改配置无需重启也能生效）。"""
    p = _ROOT / "config" / "bot_config.toml"
    try:
        mt = p.stat().st_mtime
    except OSError:
        return {}
    if _CFG_CACHE["mtime"] != mt:
        try:
            import toml
            d = toml.load(p)
            _CFG_CACHE["image"] = d.get("image") or {}
            _CFG_CACHE["mtime"] = mt
        except Exception as e:
            logger.warning("读取 [image] 配置失败（用内置默认）: %s", e)
    return _CFG_CACHE["image"]


def price_table() -> dict:
    """模型 ID → 每张 1k 基准预估人民币（内置默认 + 配置覆盖）。"""
    table = dict(_PRICE_DEFAULTS)
    cfg = _image_section().get("price") or {}
    for k, v in cfg.items():
        mid = resolve_model(str(k)) or str(k)
        try:
            table[mid] = float(v)
        except (TypeError, ValueError):
            continue
    return table


def _area_factor(size: str | None) -> float:
    """尺寸面积 / 1k 基准面积（1536x864）。auto/空 → 1.0。"""
    if not size:
        return 1.0
    s = str(size).lower().strip()
    if "x" not in s:
        return 1.0
    try:
        w, h = s.split("x")[:2]
        area = int(w) * int(h)
    except (TypeError, ValueError):
        return 1.0
    base = 1536 * 864
    return max(0.25, round(area / base, 2))


def estimate_cost(model: str, size: str | None = None) -> float:
    """预估这一张的人民币成本（保留 2 位，显示时用 _fmt_cny 去尾零）。"""
    mid = resolve_model(model) or DEFAULT_MODEL
    base = price_table().get(mid, 1.0)
    return round(base * _area_factor(size), 2)


def _fmt_cny(v: float) -> str:
    """1.5 → '1.5'；0.15 → '0.15'；6.0 → '6'。"""
    return f"{v:g}"


def tier_label(model: str) -> str:
    """按 1k 基准单价返回 便宜 / 中等 / 贵。"""
    mid = resolve_model(model) or DEFAULT_MODEL
    base = price_table().get(mid, 1.0)
    if base <= _TIER_CHEAP:
        return "便宜"
    if base <= _TIER_MID:
        return "中等"
    return "贵"


def estimate_text(model: str, size: str | None = None) -> str:
    """给用户看的一行：预计约 ¥x（档位）。"""
    return f"预计约 ¥{_fmt_cny(estimate_cost(model, size))}（{tier_label(model)}）"

# 文件名别名 → 规范别名（用户手打的各种写法）
_ALIASES: dict[str, str] = {
    "chatgpt": "sunburst",
    "gpt": "sunburst",
    "gpt-image": "sunburst",
    "openai": "sunburst",
    "sun": "sunburst",
    "chatgpt2.5": "sunburst",
    "gpt-image-2.5": "sunburst",
    "gpt-image-2.5-sunburst": "sunburst",
    "gpt-image-2.5-flare": "flare",
    "gemini-3.1-flash-image": "gemini",
    "gemini3": "gemini",
    "nano-banana": "gemini",
    "grok-imagine-image-quality": "grok",
    "grok-quality": "grok",
    "grok-imagine-image-2.0": "grok2",
    "grok-imagine-image-2": "grok2",
    "doubao": "seedream",
    "doubao-seedream-5-0-pro": "seedream",
    "seedream-5": "seedream",
    "seedream5": "seedream",
    "doubao-seedream-4-0": "seedream4",
    "seedream-4": "seedream4",
}


def base_url() -> str:
    return (os.environ.get("POKE_BASE_URL") or DEFAULT_BASE).rstrip("/")


def api_key() -> str:
    # 运行时读取，避免 .env 加载晚于模块 import
    return os.environ.get("POKE_API_KEY", "")


def available() -> bool:
    return bool(api_key())


def resolve_model(raw: str) -> str | None:
    """用户输入 → 真实模型 ID；无法识别返回 None。"""
    s = (raw or "").strip().lower()
    if not s:
        return None
    if s in MODELS:
        return MODELS[s]
    alias = _ALIASES.get(s)
    if alias:
        return MODELS[alias]
    for mid in MODELS.values():
        if s == mid.lower():
            return mid
    return None


def model_alias_of(model_id: str) -> str:
    """真实模型 ID → 别名（找不到返回原值）。"""
    for k, v in MODELS.items():
        if v == model_id:
            return k
    return model_id


def model_list_text(current: str = DEFAULT_MODEL) -> str:
    """/~draw model 的纯文本列表（QQ 不渲染 Markdown）：模型 + 相对能力 + 成本档位 + 实测耗时。"""
    cur_alias = model_alias_of(current)
    table = price_table()
    lines = [f"可用绘图模型（当前 {cur_alias}，约 ¥{_fmt_cny(table.get(current, 0))}/张）"]
    for i, (alias, mid) in enumerate(MODELS.items(), 1):
        mark = " ★" if alias == cur_alias else ""
        lines.append(f"· {i} {alias}  {MODEL_LABELS.get(alias, mid)}{mark}")
        bits = [MODEL_ABILITY.get(alias, "")]
        bits.append(f"{tier_label(mid)} 约¥{_fmt_cny(table.get(mid, 0))}/张")
        if alias in MODEL_SPEED:
            bits.append(f"实测约{MODEL_SPEED[alias]}秒")
        lines.append("    " + "｜".join(x for x in bits if x))
    lines.append("用法：/~draw <模型> [比例] [分辨率] 提示词（参数顺序随意，都可省）")
    lines.append("切换：/~draw model <名字>")
    lines.append("提示：不写分辨率一律按 1k 算；写 2k 面积翻 4 倍、成本约为 4 倍，seedream 系列最贵")
    return "\n".join(lines)


def _err_text(e: Exception) -> str:
    """异常 → 简短可读文本（优先带 HTTP 状态与响应体）。"""
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            return f"HTTP {resp.status_code}: {resp.text[:240]}".replace("\n", " ")
        except Exception:
            pass
    s = str(e).strip()
    if not s:
        return type(e).__name__
    return f"{type(e).__name__}: {s[:200]}"


async def generate(
    prompt: str,
    model: str = DEFAULT_MODEL,
    size: str | None = None,
    quality: str | None = None,
    timeout: float = 300.0,
) -> dict:
    """文生图。成功 {"local_path", "url", "model", "size"}；失败 {"error": 文本}。"""
    key = api_key()
    if not key:
        return {"error": "未配置 POKE_API_KEY（config/.env）"}
    if not prompt.strip():
        return {"error": "提示词为空"}

    body: dict = {"model": model, "prompt": prompt, "n": 1}
    if size:
        body["size"] = size
    if quality:
        body["quality"] = quality

    t_name = f"draw_{uuid.uuid4().hex[:8]}.png"
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(
                f"{base_url()}/v1/images/generations",
                json=body,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
            )
            # ★ size 不被该模型接受时（400/422/502 且带着 size）→ 去掉 size 重试一次
            if r.status_code in (400, 422, 502) and "size" in body:
                low = r.text.lower()
                if any(k in low for k in ("size", "resolution", "尺寸", "格式无效")):
                    logger.warning("模型 %s 拒绝 size=%s，退回 auto 重试: %s",
                                   model, body.get("size"), r.text[:120])
                    body.pop("size", None)
                    r = await c.post(
                        f"{base_url()}/v1/images/generations",
                        json=body,
                        headers={"Authorization": f"Bearer {key}",
                                 "Content-Type": "application/json"},
                    )
                    size = None
            if r.status_code != 200:
                err = f"HTTP {r.status_code}: {r.text[:240]}".replace("\n", " ")
                logger.error("Poke 图片生成失败 model=%s size=%s :: %s",
                             model, size, err)
                return {"error": err}
            data = r.json()
            items = data.get("data") or []
            if not items:
                return {"error": f"响应无 data: {str(data)[:200]}"}
            item = items[0] or {}
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            path = DATA_DIR / t_name
            if item.get("b64_json"):
                path.write_bytes(base64.b64decode(item["b64_json"]))
            elif item.get("url"):
                r2 = await c.get(item["url"])
                r2.raise_for_status()
                path.write_bytes(r2.content)
            else:
                return {"error": f"响应无图片数据: {str(item)[:180]}"}
            logger.info("Poke 图片生成成功 model=%s size=%s -> %s",
                        model, size, path.name)
            return {"local_path": str(path), "url": item.get("url", ""),
                    "model": model, "size": size or "auto"}
    except Exception as e:
        err = _err_text(e)
        logger.error("Poke 图片生成异常 model=%s size=%s: %s", model, size, err,
                     exc_info=True)
        return {"error": err}
