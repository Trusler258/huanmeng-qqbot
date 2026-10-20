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

# 相对能力（不是跑分，是"跟同批模型比"的短标签，用 / 分隔两三个词）
MODEL_ABILITY: dict[str, str] = {
    "sunburst": "旗舰/效果最好",
    "flare": "旗舰/风格化更强",
    "gpt-image-2": "上代旗舰/细节偏旧",
    "gemini": "快/2k降1024",
    "grok": "最快/偏写实",
    "grok2": "快/构图更活",
    "seedream": "中文国风强/最贵",
    "seedream4": "中文好/性价比",
}

# 平均生成耗时（秒，控制台「近期运行情况」24h 均值，2026-10-21 采集）
MODEL_SPEED: dict[str, int] = {
    "sunburst": 66,
    "flare": 58,
    "gpt-image-2": 66,
    "gemini": 28,
    "grok": 15,
    "grok2": 27,
    "seedream": 42,
    "seedream4": 6,
}

DEFAULT_MODEL = "gpt-image-2.5-sunburst"

# 某模型「没有可用渠道」时的降级顺序（同属 Poke 通道，不换服务商）
# 2026-10-21 实测：sunburst / flare 曾整体 502「当前分组下没有可用于该模型的渠道」
FALLBACK_CHAIN: tuple[str, ...] = (
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-flare",
    "gpt-image-2",
    "gemini-3.1-flash-image",
    "grok-imagine-image-quality",
)

# ── 价格与成本（控制台报价，USD/张，2026-10-21 01:16 采集）────────────
# 结构：模型 ID → (1k, 2k, 4k) 美元单价。
# ⚠️ **2k 并非都涨价**：gpt-image-2*/gemini/grok/seedream-4-0 的 2K 与 1K 同价，
#    只有 doubao-seedream-5-0-pro 的 2K 翻倍（0.40 → 0.80）。
# 可用 config/bot_config.toml 的 [image.usd] 覆盖，值写成数组 [1k, 2k, 4k]。
_PRICE_USD: dict[str, tuple[float, float, float]] = {
    "gpt-image-2.5-sunburst": (0.12, 0.12, 0.18),
    "gpt-image-2.5-flare": (0.12, 0.12, 0.18),
    "gpt-image-2": (0.12, 0.12, 0.18),
    "gemini-3.1-flash-image": (0.12, 0.12, 0.16),
    "grok-imagine-image-quality": (0.12, 0.12, 0.18),
    "grok-imagine-image-2.0": (0.12, 0.12, 0.18),
    "doubao-seedream-5-0-pro": (0.40, 0.80, 0.80),
    "doubao-seedream-4-0": (0.30, 0.30, 0.30),
}

DEFAULT_USD_CNY = 7.15   # 汇率，可在 [image] usd_cny 覆盖
# 成本档位阈值（按 1k 价换算成人民币）：≤1.0 便宜 / ≤2.5 中等 / 其余贵
_TIER_CHEAP = 1.0
_TIER_MID = 2.5

_ROUTE = Path(__file__).resolve().parent.parent
_CFG_CACHE: dict = {"mtime": 0.0, "image": {}}


def _image_section() -> dict:
    """读 bot_config.toml 的 [image] 段（按 mtime 缓存，改配置无需重启也能生效）。"""
    p = _ROUTE / "config" / "bot_config.toml"
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


def usd_cny() -> float:
    try:
        return float(_image_section().get("usd_cny") or DEFAULT_USD_CNY)
    except (TypeError, ValueError):
        return DEFAULT_USD_CNY


def price_table() -> dict:
    """模型 ID → (1k, 2k, 4k) 美元单价（内置默认 + 配置覆盖）。"""
    table = dict(_PRICE_USD)
    cfg = _image_section().get("usd") or {}
    for k, v in cfg.items():
        mid = resolve_model(str(k)) or str(k)
        try:
            seq = list(v)
            if len(seq) == 1:
                seq = seq * 3
            elif len(seq) == 2:
                seq = [seq[0], seq[1], seq[1]]
            table[mid] = (float(seq[0]), float(seq[1]), float(seq[2]))
        except (TypeError, ValueError, IndexError):
            continue
    return table


def _size_tier(size: str | None) -> str:
    """尺寸 → '1k' / '2k' / '4k'（按面积，基准 1k=1536x864）。"""
    if not size:
        return "1k"
    s = str(size).lower().strip()
    if "x" not in s:
        return "1k"
    try:
        w, h = s.split("x")[:2]
        area = int(w) * int(h)
    except (TypeError, ValueError):
        return "1k"
    ratio = area / (1536 * 864)
    if ratio >= 6.0:
        return "4k"
    if ratio >= 1.9:
        return "2k"
    return "1k"


def _area_factor(size: str | None) -> float:
    """尺寸面积 / 1k 基准面积（保留兼容，只用于粗略判断）。"""
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
    return max(0.25, round(area / (1536 * 864), 2))


def estimate_usd(model: str, size: str | None = None) -> float:
    """这一张的美元单价（按尺寸档查表，不做面积折算）。"""
    mid = resolve_model(model) or DEFAULT_MODEL
    prices = price_table().get(mid, (0.12, 0.12, 0.18))
    return prices[("1k", "2k", "4k").index(_size_tier(size))]


def estimate_cost(model: str, size: str | None = None) -> float:
    """预估这一张的人民币成本（保留 2 位，显示时用 _fmt_cny 去尾零）。"""
    return round(estimate_usd(model, size) * usd_cny(), 2)


def _fmt_cny(v: float) -> str:
    """0.86 → '0.86'；1.5 → '1.5'；6 → '6'。"""
    return f"{v:g}"


def tier_label(model: str) -> str:
    """按 1k 单价（人民币）返回 便宜 / 中等 / 贵。"""
    mid = resolve_model(model) or DEFAULT_MODEL
    cny = estimate_cost(mid, "1536x864")
    if cny <= _TIER_CHEAP:
        return "便宜"
    if cny <= _TIER_MID:
        return "中等"
    return "贵"


def estimate_text(model: str, size: str | None = None) -> str:
    """给用户看的一行：预计约 ¥x（档位）。"""
    return f"预计约 ¥{_fmt_cny(estimate_cost(model, size))}（{tier_label(model)}）"


def size_note(model: str, size: str | None = None) -> str:
    """2k/4k 且比 1k 贵时给一句成本提醒；不加价返回空串。"""
    mid = resolve_model(model) or DEFAULT_MODEL
    tier = _size_tier(size)
    if tier == "1k":
        return ""
    p1 = estimate_usd(mid, "1536x864")
    pn = estimate_usd(mid, size)
    if pn <= p1:
        return ""
    return (f"{tier} 比 1k 贵：约 ¥{_fmt_cny(estimate_cost(mid, size))}/张"
            f"（1k 约 ¥{_fmt_cny(estimate_cost(mid, '1536x864'))}），嫌贵就省掉分辨率参数")


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
    """/~draw model 的纯文本列表（QQ 不渲染 Markdown）：一行一个模型。

    格式：显示名（默认） [别名]  ¥单价/张  档位/能力标签/耗时
    """
    cur_alias = model_alias_of(current)
    lines = [f"可用绘图模型（当前 {cur_alias}）"]
    for alias, mid in MODELS.items():
        mark = "（默认）" if alias == cur_alias else ""
        label = MODEL_LABELS.get(alias, mid).replace("（默认）", "")
        tail = [tier_label(mid), MODEL_ABILITY.get(alias, "")]
        if alias in MODEL_SPEED:
            tail.append(f"{MODEL_SPEED[alias]}秒")
        tail_s = "/".join(x for x in tail if x)
        cny = _fmt_cny(estimate_cost(mid, "1536x864"))
        lines.append(f"{label}{mark} [{alias}]  ¥{cny}/张  {tail_s}")
    lines.append("用法：/~draw [模型] [比例] [分辨率] 提示词（都可省）｜/~draw model <名字> 切换")
    lines.append("不写分辨率按 1k；2k 多数模型与 1k 同价，只有 seedream 5.0 Pro 的 2k 翻倍")
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
                # ★ 502/503 + 「没有可用渠道」= 该模型当前无渠道（试别的尺寸也没用），
                #   上层据此立刻换模型，而不是掉到备用通道
                channel_err = (r.status_code in (502, 503)
                               and ("渠道" in r.text or "channel" in r.text.lower()))
                return {"error": err, "channel_error": channel_err,
                        "status": r.status_code}
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


async def edit(
    prompt: str,
    model: str = DEFAULT_MODEL,
    size: str | None = None,
    image_path: str | Path | None = None,
    image_url: str = "",
    timeout: float = 300.0,
) -> dict:
    """参考图生图（/v1/images/edits）：先 multipart 上传，失败再试 JSON 图片 URL。

    成功 {"local_path","url","model","size"}；失败 {"error", "channel_error"?}。
    """
    key = api_key()
    if not key:
        return {"error": "未配置 POKE_API_KEY（config/.env）"}
    if not prompt.strip():
        return {"error": "提示词为空"}
    if not image_path and not image_url:
        return {"error": "没有参考图"}

    url = f"{base_url()}/v1/images/edits"
    h = {"Authorization": f"Bearer {key}"}   # multipart 不能带 JSON Content-Type
    last = ""

    # ── 方式一：multipart 上传本地图 ──
    if image_path:
        try:
            data = {"model": model, "prompt": prompt, "n": "1"}
            if size:
                data["size"] = size
            with open(image_path, "rb") as f:
                files = {"image": (Path(image_path).name, f.read(), "image/png")}
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(url, data=data, files=files, headers=h)
                out = await _handle_edit_resp(r, model, size)
                if out.get("local_path"):
                    return out
                last = out.get("error", "")
                if out.get("channel_error"):
                    return out
                logger.warning("multipart edits 失败: %s，试 JSON 图片 URL", last[:140])
        except Exception as e:
            last = _err_text(e)
            logger.warning("multipart edits 异常: %s，试 JSON 图片 URL", last[:140])

    # ── 方式二：JSON 传图片 URL ──
    if image_url:
        try:
            body = {"model": model, "prompt": prompt, "n": 1,
                    "images": [{"image_url": image_url}]}
            if size:
                body["size"] = size
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(url, json=body,
                                 headers={**h, "Content-Type": "application/json"})
                out = await _handle_edit_resp(r, model, size)
                if out.get("local_path"):
                    return out
                last = last or out.get("error", "")
                if out.get("channel_error"):
                    return out
        except Exception as e:
            last = last or _err_text(e)
            logger.warning("JSON edits 异常: %s", last[:140])

    return {"error": last or "参考图生图失败"}


async def _handle_edit_resp(r, model: str, size: str | None) -> dict:
    """解析 edits 响应（与 generations 同构）。"""
    if r.status_code != 200:
        err = f"HTTP {r.status_code}: {r.text[:240]}".replace("\n", " ")
        channel_err = (r.status_code in (502, 503)
                       and ("渠道" in r.text or "channel" in r.text.lower()))
        return {"error": err, "channel_error": channel_err, "status": r.status_code}
    data = r.json()
    items = data.get("data") or []
    if not items:
        return {"error": f"响应无 data: {str(data)[:200]}"}
    item = items[0] or {}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"draw_{uuid.uuid4().hex[:8]}.png"
    if item.get("b64_json"):
        path.write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        async with httpx.AsyncClient(timeout=120) as c:
            r2 = await c.get(item["url"])
            r2.raise_for_status()
            path.write_bytes(r2.content)
    else:
        return {"error": f"响应无图片数据: {str(item)[:180]}"}
    logger.info("Poke 参考图生图成功 model=%s size=%s -> %s", model, size, path.name)
    return {"local_path": str(path), "url": item.get("url", ""),
            "model": model, "size": size or "auto"}
