"""v2.3.66 wdsj 请求「优先直连、失败兜底代理」回归测试

背景：WDSJ_PROXY 配在 systemd Environment= 里（不在 .env），bot 进程一直看得到。
旧 _api_url 的逻辑是「配了代理就永远走代理」→ 线上每次请求都绕 CF Worker。
服务器实测（同 URL 各 3 次）：
    战绩 API   直连 763ms  vs 代理 2030ms   (2.7x)
    官方图     直连 733ms  vs 代理 1703ms   (2.3x)
所以改成直连优先，代理只在直连不可用时兜底。

用法（本地）:
  python tests/_test_v2366_wdsj_direct_first.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  \033[32mOK\033[0m   {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  \033[31mFAIL\033[0m {msg}")


class FakeResp:
    def __init__(self, status=200, body=b"x"):
        self.status_code = status
        self.content = body

    def json(self):
        return {"code": 0, "data": {"ok": True}}


class FakeClient:
    """记录所有 get 调用；按 URL 关键字决定行为"""

    def __init__(self, direct_result, proxy_result=None):
        self.direct_result = direct_result
        self.proxy_result = proxy_result
        self.calls: list[str] = []

    async def get(self, url, **kw):
        self.calls.append(url)
        if "/proxy?" in url:
            if isinstance(self.proxy_result, Exception):
                raise self.proxy_result
            return self.proxy_result
        if isinstance(self.direct_result, Exception):
            raise self.direct_result
        return self.direct_result


def _patch_client(api, fc):
    """把 _get_client 换成返回假 client 的 async 桩（真实实现是 async，别用同步 lambda）"""
    async def _fake(timeout: float = 15.0):
        return fc
    api._get_client = _fake


def main() -> None:
    from services import wdsj_api as api

    print("=== 1. _api_url 语义已改（恒返回直连）===")
    api.PROXY_BASE = "https://wdsj1.example/proxy"     # 人为打开代理
    u = api._api_url("/api/v1/x")
    if u == "https://www.wdsj.net/nexus/api/v1/x":
        ok(f"_api_url 返回直连（配了代理也不绕）: {u}")
    else:
        bad(f"_api_url 仍返回代理: {u}")
    pu = api._proxy_url(u)
    if pu and pu.startswith("https://wdsj1.example/proxy?url="):
        ok("_proxy_url 正确构造兜底 URL")
    else:
        bad(f"_proxy_url 异常: {pu}")

    print("\n=== 2. 直连可用时不碰代理 ===")
    for code, why in ((200, "正常"), (404, "资源不存在（业务结果）"), (400, "参数错误")):
        fc = FakeClient(FakeResp(code))
        _patch_client(api, fc)
        r = asyncio.run(api._request("/api/v1/x"))
        hit_proxy = any("/proxy?" in c for c in fc.calls)
        if r is not None and r.status_code == code and not hit_proxy:
            ok(f"直连 HTTP {code}（{why}）→ 直接用，未触发代理（调用数={len(fc.calls)}）")
        else:
            bad(f"直连 HTTP {code} 行为错误: resp={r} calls={fc.calls}")

    print("\n=== 3. 直连不可用时兜底代理 ===")
    cases = [
        ("403 风控", 403, 200),
        ("500 服务端错误", 500, 200),
        ("超时异常", RuntimeError("ReadTimeout"), 200),
    ]
    for why, direct, proxy_code in cases:
        d = FakeResp(direct) if isinstance(direct, int) else direct
        fc = FakeClient(d, FakeResp(proxy_code))
        _patch_client(api, fc)
        r = asyncio.run(api._request("/api/v1/x"))
        proxy_called = any("/proxy?" in c for c in fc.calls)
        if proxy_called and r is not None and r.status_code == proxy_code:
            ok(f"{why} → 走了代理兜底并返回 {proxy_code}")
        else:
            bad(f"{why} → 未正确兜底: proxy_called={proxy_called} resp={r}")

    print("\n=== 4. 没配代理时不回退（避免无意义等待）===")
    saved = api.PROXY_BASE
    api.PROXY_BASE = ""
    fc = FakeClient(RuntimeError("ConnectTimeout"))
    _patch_client(api, fc)
    r = asyncio.run(api._request("/api/v1/x"))
    if r is None and len(fc.calls) == 1:
        ok("无代理 + 直连失败 → 返回 None，且只请求 1 次")
    else:
        bad(f"无代理场景异常: resp={r} calls={len(fc.calls)}")

    print("\n=== 5. 代理也失败 → 返回直连结果，不抛异常 ===")
    api.PROXY_BASE = saved
    fc = FakeClient(FakeResp(403), RuntimeError("proxy down"))
    _patch_client(api, fc)
    r = asyncio.run(api._request("/api/v1/x"))
    if r is not None and r.status_code == 403 and len(fc.calls) == 2:
        ok("两边都失败 → 返回直连响应（不抛），共请求 2 次")
    else:
        bad(f"双重失败场景异常: resp={r} calls={fc.calls}")

    print("\n=== 6. 各请求点都改用了 _request（不再直连 client.get）===")
    import inspect
    src = inspect.getsource(api)
    for fn, label in (("query_player_stats", "玩家战绩"),
                      ("download_stats_image", "官方图"),
                      ("download_player_head", "头像"),
                      ("fetch_player_head_data_uri", "头像dataURI"),
                      ("query_leaderboards", "榜单列表")):
        s = inspect.getsource(getattr(api, fn))
        if "_request(" in s:
            ok(f"{label}（{fn}）已走 _request（含兜底）")
        else:
            bad(f"{label}（{fn}）未使用 _request")
    # _request 自己体内必然有 2 处 client.get（直连 + 代理兜底），属正常
    own = inspect.getsource(api._request).count("await client.get(")
    n_raw = src.count("await client.get(") - own
    if n_raw == 0:
        ok(f"除 _request 自身外无裸 client.get（_request 内 {own} 处 = 直连+兜底）")
    else:
        bad(f"_request 之外仍有 {n_raw} 处裸 client.get")

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
