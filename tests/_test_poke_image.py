"""/~draw 多模型改造（v2.3.104）单测：模型解析 + 参数解析 + 主备通道。

跑法：python tests/_test_poke_image.py
不联网（poke_image.generate / HTTP 全部 mock）。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("POKE_API_KEY", "sk-test")

PASS = 0
FAIL = 0


def ck(name: str, cond: bool, extra: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def test_resolve_model():
    from services import poke_image as P
    print("[1] 模型解析")
    ck("别名 sunburst", P.resolve_model("sunburst") == "gpt-image-2.5-sunburst")
    ck("全名 flare", P.resolve_model("gpt-image-2.5-flare") == "gpt-image-2.5-flare")
    ck("大写", P.resolve_model("SUNBURST") == "gpt-image-2.5-sunburst")
    ck("gemini", P.resolve_model("gemini") == "gemini-3.1-flash-image")
    ck("grok 全名", P.resolve_model("grok-imagine-image-quality") == "grok-imagine-image-quality")
    ck("grok2", P.resolve_model("grok2") == "grok-imagine-image-2.0")
    ck("seedream", P.resolve_model("seedream") == "doubao-seedream-5-0-pro")
    ck("seedream4", P.resolve_model("seedream4") == "doubao-seedream-4-0")
    ck("中文不误判", P.resolve_model("樱花猫娘") is None)
    ck("空串", P.resolve_model("") is None)
    ck("别名回查", P.model_alias_of("gpt-image-2.5-sunburst") == "sunburst")
    txt = P.model_list_text("gpt-image-2.5-sunburst")
    ck("模型列表无 md", "**" not in txt and "`" not in txt, txt[:60])
    ck("模型列表含 8 个", sum(1 for l in txt.split("\n") if "/张" in l) == 8, txt[:200])


def test_size():
    from modules import agnes as A
    print("[2] 尺寸/分辨率")
    ck("16:9 基准", A._resolve_size("16:9") == "1536x864")
    ck("1:1 基准", A._resolve_size("1:1") == "1024x1024")
    ck("square 别名", A._resolve_size("square") == "1024x1024")
    ck("2k 放大", A._apply_resolution("1536x864", "2k") == "3072x1728")
    ck("4k 按 2k 处理", A._apply_resolution("1536x864", "4k") == "3072x1728")
    ck("1k 不变", A._apply_resolution("1536x864", "1k") == "1536x864")
    ck("未知档位不变", A._apply_resolution("1536x864", "abc") == "1536x864")


def test_cmd_parse():
    """cmd_draw 参数解析：直接跑，mock 掉配额/发送/后台任务。"""
    from modules import agnes as A
    import services.sender as S

    print("[3] /~draw 参数解析")
    captured = {}

    class _Mgr:
        async def call_api(self, *a, **k):
            return None

    def fake_send(*a, **k):
        captured["tip"] = a[0]
        return asyncio.sleep(0)

    S.send_by_chat_type = fake_send
    S.get_ws_manager = lambda: _Mgr()
    A.check_and_use_draw = lambda uid: (True, 5, 5)

    async def run(args, clear=True):
        captured.clear()
        if clear:
            A._save_model_pref(999001, "")  # 清偏好
        r = await A.cmd_draw(args, 999001, 0, "t", False, 0, raw_message="")
        await asyncio.sleep(0)
        return r, captured.get("tip", "")

    def grab_task():
        """截获 create_task 的参数：直接调 _bg_gen_image 会真发图，故只校验 tips。"""
        pass

    # 只校验解析结果体现在 tip 文本里（tip 里带 模型 + 尺寸）
    real_create = asyncio.create_task
    A.asyncio.create_task = lambda coro: (coro.close(), None)[1]
    try:
        r, tip = asyncio.run(run(["一只橘猫"]))
        ck("默认模型 sunburst", "Sunburst" in tip, tip)
        ck("默认尺寸 16:9", "1536x864" in tip, tip)
        ck("默认无 md", "**" not in tip, tip)

        r, tip = asyncio.run(run(["flare", "1:1", "一只橘猫"]))
        ck("指定模型 flare", "Flare" in tip, tip)
        ck("指定比例 1:1", "1024x1024" in tip, tip)

        r, tip = asyncio.run(run(["16:9", "2k", "雪山"]))
        ck("比例+2k", "3072x1728" in tip, tip)

        r, tip = asyncio.run(run(["gemini", "1024x1024", "海边"]))
        ck("gemini 显式尺寸", "Gemini" in tip and "1024x1024" in tip, tip)

        r, tip = asyncio.run(run(["seedream4", "猫"]))
        ck("seedream4 识别", "Seedream 4.0" in tip, tip)

        # 模型切换
        r = asyncio.run(A.cmd_draw(["model"], 999001, 0, "t", False, 0))
        ck("model 列表", "可用绘图模型" in r and "**" not in r, r[:60])
        r = asyncio.run(A.cmd_draw(["model", "grok2"], 999001, 0, "t", False, 0))
        ck("切换模型成功", "Grok Imagine 2.0" in r, r)
        ck("偏好已落盘", A._load_model_pref(999001) == "grok2")
        r, tip = asyncio.run(run(["猫"], clear=False))
        ck("偏好生效", "Grok Imagine 2.0" in tip, tip)
        r = asyncio.run(A.cmd_draw(["model", "不存在"], 999001, 0, "t", False, 0))
        ck("未知模型报错", "没有这个模型" in r, r)

        # ★ 设过偏好后，命令行仍要能单次换模型（曾把 "gemini" 当提示词）
        A._save_model_pref(999001, "sunburst")
        r, tip = asyncio.run(run(["gemini", "1:1", "猫"], clear=False))
        ck("命令行覆盖偏好", "Gemini" in tip, tip)
        ck("模型名不进提示词", "[猫]" in tip, tip)
        A._save_model_pref(999001, "")
    finally:
        A.asyncio.create_task = real_create


def test_channel():
    """主备通道：poke 成功 / poke 失败转备用 / poke 不可用直接备用。"""
    from modules import agnes as A
    from services import poke_image as P

    print("[4] 主备通道")

    async def fake_poke_ok(prompt, model=P.DEFAULT_MODEL, size=None, quality=None, timeout=0):
        return {"local_path": "/tmp/x.png", "model": model, "size": size or "auto"}

    async def fake_poke_fail(prompt, **k):
        return {"error": "HTTP 500: boom"}

    real_gen = P.generate
    try:
        P.generate = fake_poke_ok
        r = asyncio.run(A._gen_image("猫", "1536x864"))
        ck("poke 成功走主通道", (r or {}).get("provider") == "poke", str(r))
        ck("带回模型名", (r or {}).get("model") == "gpt-image-2.5-sunburst", str(r))

        r = asyncio.run(A._gen_image("猫", "1536x864", model="gemini"))
        ck("指定模型透传", (r or {}).get("model") == "gemini-3.1-flash-image", str(r))
    finally:
        P.generate = real_gen

    # 不可用（无 key）：应尝试备用通道并失败（网络/凭据在本机无效也无妨，只要不抛）
    real_key = os.environ.pop("POKE_API_KEY", "")
    try:
        r = asyncio.run(A._gen_image("猫", "512x512"))
        ck("无 key 回退备用通道", isinstance(r, dict), str(r)[:120])
    finally:
        if real_key:
            os.environ["POKE_API_KEY"] = real_key


def test_size_fallback():
    """poke_image.generate 对 size 被拒的自动降级（mock httpx）。"""
    import httpx
    from services import poke_image as P

    print("[5] size 降级重试")
    calls = []

    class _Resp:
        def __init__(self, code, payload):
            self.status_code = code
            self._p = payload
            self.text = str(payload)

        def json(self):
            return self._p

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append(dict(json))
            if len(calls) == 1:
                return _Resp(502, {"error": {"message": "模型请求格式无效 size 不支持"}})
            return _Resp(200, {"data": [{"b64_json": "aGVsbG8="}]})

        async def get(self, url):
            return _Resp(200, {})

    real = httpx.AsyncClient
    httpx.AsyncClient = _Client
    try:
        r = asyncio.run(P.generate("猫", size="4096x4096"))
        ck("第一次带 size", calls[0].get("size") == "4096x4096", str(calls[0]))
        ck("重试去掉 size", "size" not in calls[1], str(calls[1]))
        ck("重试成功出图", bool(r.get("local_path")), str(r))
    finally:
        httpx.AsyncClient = real


def test_cost_and_tier():
    """价格表 / 档位 / 能力标注（真实报价：控制台 2026-10-21 01:16）."""
    from services import poke_image as P

    print("[6] 价格与能力标注")
    # gpt 系 1k = $0.12 × 7.15 ≈ ¥0.86
    ck("sunburst 1k 价 ¥0.86", P.estimate_cost("sunburst", "1536x864") == 0.86,
       str(P.estimate_cost("sunburst", "1536x864")))
    # ⚠️ gpt 系 2k 与 1k 同价，不再"翻 4 倍"
    ck("sunburst 2k 同价", P.estimate_usd("sunburst", "3072x1728") == 0.12,
       str(P.estimate_usd("sunburst", "3072x1728")))
    ck("sunburst 4k 加价", P.estimate_usd("sunburst", "6144x3456") == 0.18,
       str(P.estimate_usd("sunburst", "6144x3456")))
    # seedream 5.0 Pro 才是 2k 翻倍
    ck("seedream 2k 翻倍 $0.80", P.estimate_usd("seedream", "3072x1728") == 0.80,
       str(P.estimate_usd("seedream", "3072x1728")))
    ck("seedream 2k ≈¥5.72", P.estimate_cost("seedream", "3072x1728") == 5.72,
       str(P.estimate_cost("seedream", "3072x1728")))
    ck("1024x1024 归 1k 档", P.estimate_usd("sunburst", "1024x1024") == 0.12)

    # 档位按人民币：gpt/gemini/grok ¥0.86 便宜；seedream4 ¥2.15 中等；seedream pro ¥2.86 贵
    for m, want in (("gemini", "便宜"), ("gpt-image-2", "便宜"), ("sunburst", "便宜"),
                    ("grok", "便宜"), ("seedream4", "中等"), ("seedream", "贵")):
        ck(f"{m} → {want}", P.tier_label(m) == want, P.tier_label(m))

    txt = P.estimate_text("seedream", "3072x1728")
    ck("预估文本含价格+档位", "5.72" in txt and "贵" in txt, txt)

    # size_note：只有真加价才提醒
    ck("sunburst 2k 无提醒", P.size_note("sunburst", "3072x1728") == "",
       P.size_note("sunburst", "3072x1728"))
    note = P.size_note("seedream", "3072x1728")
    ck("seedream 2k 有提醒", "2k 比 1k 贵" in note and "5.72" in note, note)

    body = P.model_list_text("gpt-image-2.5-sunburst")
    ck("列表含能力描述", "效果最好" in body, body[:200])
    ck("列表含中文名（能力）", "中文国风强" in body)
    ck("列表含平均耗时", "66秒" in body, body[:300])
    ck("列表含单价", "¥0.86/张" in body)
    ck("列表含别名方括号", "[sunburst]" in body and "[seedream4]" in body)
    ck("列表说明 2k 同价", "同价" in body, body[-120:])
    ck("列表无 md", "**" not in body and "`" not in body)


def test_quota_cfg():
    """配额：默认 3 张、免重启跟随配置、owner 手设后不被覆盖。"""
    from modules import agnes as A

    print("[7] 配额配置化")
    ck("默认上限来自配置 = 3", A._draw_limit() == 3, str(A._draw_limit()))
    data = {"daily_reset": "", "users": {}}
    left, limit = A._get_user_quota(data, 12345, A._draw_limit())
    ck("新用户上限 3", limit == 3, str(limit))
    # 模拟历史数据里固化过 limit=5（无 custom）→ 应被配置覆盖回 3
    data["users"]["12345"]["limit"] = 5
    left, limit = A._get_user_quota(data, 12345, A._draw_limit())
    ck("旧记录跟随配置", limit == 3, str(limit))
    # owner 手设后保留
    data["users"]["12345"] = {"used": 0, "limit": 8, "custom": True}
    left, limit = A._get_user_quota(data, 12345, A._draw_limit())
    ck("custom 不被覆盖", limit == 8, str(limit))


if __name__ == "__main__":
    test_resolve_model()
    test_size()
    test_cmd_parse()
    test_channel()
    test_size_fallback()
    test_cost_and_tier()
    test_quota_cfg()
    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    raise SystemExit(0 if FAIL == 0 else 1)
