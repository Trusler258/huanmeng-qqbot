#!/usr/bin/env python3
"""实测：LLM 真的报错时，聊天里会长什么样（真打 DeepSeek，不是构造的假异常）

用法（服务器）: python3 scripts/_probe_llm_error_report.py
"""
import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEP = "=" * 66


def _fake_cfg(real, name, key=None, model=None):
    """构造隔离的假配置：name 不同 → 熔断计数不污染真实模型（熔断 key 就是 name）"""
    return types.SimpleNamespace(
        name=name,
        url=real.url,
        key=key if key is not None else real.key,
        model=model if model is not None else getattr(real, "model", real.name),
    )


async def _case(title, L, cfg, **kw):
    L.clear_llm_error()
    try:
        out = await L.call_llm(cfg, [{"role": "user", "content": "只回复一个字：好"}],
                               scene=kw.pop("scene", "probe"), **kw)
    except Exception as e:
        out = "（抛出了 %s: %s）" % (type(e).__name__, e)
    print()
    print(SEP)
    print("【%s】" % title)
    print("  模型返回: %r" % (out[:40] if isinstance(out, str) else out))
    print("-" * 66)
    text = L.format_llm_error()
    print(text if text else "（没有错误记录 —— 说明这次调用是成功的）")
    print(SEP)
    return out


async def main():
    import services.llm as L
    from core.config import get_config

    rm = get_config().reply_model
    print("真实模型配置: name=%s url=%s model=%s"
          % (rm.name, rm.url, getattr(rm, "model", rm.name)))

    # ① 认证失败：故意用错 key（真打 DeepSeek，会回 401）
    await _case("真的 401 认证失败（故意用错 key）", L,
                _fake_cfg(rm, "probe-401", key="sk-this-key-is-definitely-wrong"),
                scene="probe-401", max_tokens=16, timeout=30)

    # ② 参数错误：temperature 超出 DeepSeek 允许范围（[0,2]）→ 期望 400/422
    await _case("真的参数错误（temperature=9.9 超范围）", L,
                _fake_cfg(rm, "probe-param"),
                scene="probe-param", max_tokens=16, temperature=9.9, timeout=30)

    # ③ 正常调用：不该留下任何错误记录（否则会把旧错误误报进聊天）
    await _case("正常调用（对照组，应无错误记录）", L,
                _fake_cfg(rm, "probe-ok"),
                scene="probe-ok", max_tokens=16, timeout=60)


asyncio.run(main())
