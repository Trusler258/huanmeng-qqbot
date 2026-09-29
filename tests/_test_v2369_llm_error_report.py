"""LLM 报错诊断与上报 —— 回归测试（2026-09-29 用户要求）

用户要求：
  1) LLM 调用出问题时能返回"是什么问题"（原来只有写死的一句「LLM返回空内容」）
  2) 报错时把**整个报错栈**丢到聊天去

覆盖：
  A. DeepSeek 官方错误码表完整（400/401/402/422/429/500/503）
  B. `_classify_llm_error` 能从异常里取到真实 HTTP 码 + 响应体，并给出人话提示
  C. `call_llm` / `call_llm_with_tools` 真失败时留下记录（含完整 traceback）
  D. `format_llm_error` 拼出的文本含完整栈；`since` 能挡掉旧记录
  E. pipeline 主回复失败分支确实用了它、且写死的旧文案已移除

用法（本地）: python tests/_test_v2369_llm_error_report.py
"""
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  [OK] {msg}")


def bad(msg):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}")


# ── 假的 openai 异常（带 response / status_code，形状与真的一致）─────
class _FakeResp:
    def __init__(self, code, text):
        self.status_code = code
        self.text = text


class _FakeAPIError(Exception):
    def __init__(self, code=None, text=""):
        super().__init__("Error code: %s" % code)
        if code is not None:
            self.response = _FakeResp(code, text)


class _FakeNetError(Exception):
    """无 response 的网络类异常（走 isinstance 分支前先被 response 判空挡掉）"""


def _client_raising(exc):
    """构造一个 chat.completions.create 必抛 exc 的假 client"""

    def _create(**kw):
        raise exc

    return types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=_create)))


def _cfg(name="deepseek-chat"):
    return types.SimpleNamespace(
        name=name, url="https://api.deepseek.com/v1", key="sk-fake", model=name)


def main() -> None:
    import services.llm as L

    print("=== A. DeepSeek 官方错误码表完整 ===")
    expect = {400, 401, 402, 422, 429, 500, 503}
    have = set(L._DEEPSEEK_ERRORS)
    if have == expect:
        ok("7 个错误码齐全：%s" % sorted(have))
    else:
        bad("错误码表缺：%s / 多：%s" % (sorted(expect - have), sorted(have - expect)))
    for c in sorted(expect):
        name, hint = L._DEEPSEEK_ERRORS[c]
        if name and hint:
            ok("  %d → %s（%s）" % (c, name, hint[:28]))
        else:
            bad("  %d 说明不全" % c)

    print()
    print("=== B. 异常分类：取真实状态码 + 响应体 + 人话提示 ===")
    cases = [
        (402, '{"error":{"message":"Insufficient Balance"}}', "余额不足"),
        (401, '{"error":{"message":"Authentication Fails"}}', "认证失败"),
        (400, '{"error":{"message":"Invalid max_tokens"}}', "格式错误"),
        (422, '{"error":{"message":"invalid temperature"}}', "参数错误"),
        (429, '{"error":{"message":"Rate limit reached"}}', "请求速率达到上限"),
        (500, '{"error":{"message":"server error"}}', "服务器故障"),
        (503, '{"error":{"message":"overloaded"}}', "服务器繁忙"),
    ]
    for code, body, want in cases:
        d = L._classify_llm_error(_FakeAPIError(code, body), timeout=60)
        good = (d["code"] == code and want in d["summary"] and body in d["detail"])
        if good:
            ok("HTTP %d → 「%s」并带上了响应体" % (code, d["summary"]))
        else:
            bad("HTTP %d 分类异常: %s" % (code, d))

    # 未在表里的码
    d = L._classify_llm_error(_FakeAPIError(418, "teapot"), timeout=60)
    if d["code"] == 418 and "418" in d["summary"]:
        ok("表外的 HTTP 码也如实报出（%s）" % d["summary"])
    else:
        bad("表外码处理异常: %s" % d)

    # 超时（真 openai 的 APITimeoutError 是子类，这里退化为无 response）
    d = L._classify_llm_error(_FakeNetError("timed out"), timeout=45)
    if d["code"] is None and "timed out" in d["summary"]:
        ok("无 HTTP 码的异常 → 原文进 summary（%s）" % d["summary"][:40])
    else:
        bad("无响应异常处理异常: %s" % d)

    print()
    print("=== C. call_llm 真失败时留下记录（含完整 traceback）===")
    L.clear_llm_error()
    fake_exc = _FakeAPIError(402, '{"error":{"message":"Insufficient Balance"}}')
    L._create_client = lambda cfg, timeout=60.0: _client_raising(fake_exc)
    out = asyncio.run(L.call_llm(_cfg(), [{"role": "user", "content": "hi"}],
                                 scene="reply", timeout=5.0))
    rec = L.last_llm_error()
    if out == "":
        ok("call_llm 失败仍返回空字符串（不改原有契约）")
    else:
        bad("call_llm 返回值变了: %r" % out)
    if rec and rec.get("code") == 402 and "余额不足" in rec.get("summary", ""):
        ok("错误记录：code=402 / %s" % rec["summary"])
    else:
        bad("错误记录异常: %s" % rec)
    tb = (rec or {}).get("traceback") or ""
    if "Traceback (most recent call last)" in tb and "call_llm" in tb:
        ok("完整报错栈已留存（%d 字符，含 Traceback 与 call_llm 帧）" % len(tb))
    else:
        bad("报错栈不完整: %r" % tb[:120])
    if (rec or {}).get("scene") == "reply":
        ok("scene 被记录为 reply（便于归因）")
    else:
        bad("scene 记录异常: %s" % (rec or {}).get("scene"))

    print()
    print("=== C2. call_llm_with_tools 同样留记录 ===")
    L.clear_llm_error()
    L._create_client = lambda cfg, timeout=60.0: _client_raising(
        _FakeAPIError(503, '{"error":{"message":"overloaded"}}'))
    res = asyncio.run(L.call_llm_with_tools(_cfg(), [{"role": "user", "content": "hi"}],
                                            [], scene="tools", timeout=5.0))
    rec2 = L.last_llm_error()
    if res.content == "" and not res.tool_calls:
        ok("call_llm_with_tools 失败仍返回空 ToolCallResult（契约不变）")
    else:
        bad("返回值变了: %r" % res)
    if rec2 and rec2.get("code") == 503 and "服务器繁忙" in rec2.get("summary", ""):
        ok("错误记录：code=503 / %s" % rec2["summary"])
    else:
        bad("错误记录异常: %s" % rec2)
    if "Traceback" in ((rec2 or {}).get("traceback") or ""):
        ok("完整报错栈已留存")
    else:
        bad("报错栈缺失")

    print()
    print("=== D. format_llm_error 拼出的聊天文本 ===")
    text = L.format_llm_error()
    for need in ("错误:", "提示:", "场景", "响应体:", "报错栈:", "Traceback"):
        if need in text:
            ok("输出含「%s」" % need)
        else:
            bad("输出缺「%s」\n%s" % (need, text[:300]))

    print()
    print("=== D2. since 能挡掉旧记录（防误报上一轮/别的群的错）===")
    old = L.format_llm_error(since=time.time() + 10)
    if old == "":
        ok("since 晚于记录时间 → 返回空（不误报）")
    else:
        bad("since 未生效: %r" % old[:100])
    now_ok = L.format_llm_error(since=time.time() - 60)
    if "报错栈" in now_ok:
        ok("since 早于记录时间 → 正常返回")
    else:
        bad("since 误挡了本次错误")

    print()
    print("=== D3. 无记录时返回空（不会拼出半截文本）===")
    L.clear_llm_error()
    if L.format_llm_error() == "":
        ok("clear 后 format 返回空")
    else:
        bad("clear 后仍有内容: %r" % L.format_llm_error()[:80])
    # 传具体 err 也能格式化
    one = L.format_llm_error({"summary": "HTTP 429 请求速率达到上限", "hint": "稍后重试",
                              "traceback": "Traceback\nboom", "ts": time.time()})
    if "429" in one and "boom" in one:
        ok("显式传 err 也能格式化")
    else:
        bad("显式 err 格式化异常: %r" % one[:120])

    print()
    print("=== D4. 密钥脱敏（要发到群里，不能把 key 原样发出去）===")
    leaky = L.format_llm_error({
        "summary": "HTTP 401 认证失败", "hint": "检查 key",
        "ts": time.time(), "traceback": "Traceback\nboom",
        "detail": ('{"error":{"message":"Authentication Fails, Your api key: '
                   'sk-abcdef1234567890 is invalid","x":"Bearer sk-live9876543210zz"}}'),
    })
    if "sk-abcdef1234567890" not in leaky and "sk-live9876543210zz" not in leaky:
        ok("响应体里的 sk-xxx 已打码")
    else:
        bad("密钥未脱敏:\n%s" % leaky)
    if "sk-***" in leaky:
        ok("打码占位符 sk-*** 存在")
    else:
        bad("未看到打码占位符")
    # 栈里也不该漏 key
    leaky2 = L.format_llm_error({
        "summary": "X", "ts": time.time(),
        "traceback": "Traceback\n  headers={'Authorization': 'Bearer sk-secretvalue12345'}\nboom",
    })
    if "sk-secretvalue12345" not in leaky2:
        ok("报错栈里的密钥同样被打码")
    else:
        bad("栈里密钥未脱敏:\n%s" % leaky2)

    print()
    print("=== E. pipeline 失败分支确实用了它 ===")
    psrc = Path(ROOT, "core/pipeline.py").read_text(encoding="utf-8")
    if "from services.llm import format_llm_error" in psrc:
        ok("失败分支已引入 format_llm_error")
    else:
        bad("失败分支未使用 format_llm_error")
    if "_err_detail = format_llm_error(since=_llm_err_since)" in psrc:
        ok("传了 since=_llm_err_since（只认本次错误）")
    else:
        bad("未传 since 起点")
    if "_llm_err_since = _tm.time()" in psrc:
        ok("调用前记了起点时间戳")
    else:
        bad("缺起点时间戳")
    # 只看"实际会被输出的代码"（带 f 前缀的那句），注释里引用旧文案是允许的
    if 'f"错误: LLM返回空内容"' not in psrc:
        ok("写死的「错误: LLM返回空内容」已从输出里移除")
    else:
        bad("旧的写死文案仍在（作为实际输出）")
    if "错误: 模型返回空内容" in psrc:
        ok("空返回时改给可区分的说明（模型返回空内容）")
    else:
        bad("空返回分支缺说明")

    print()
    print(f"结果: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
