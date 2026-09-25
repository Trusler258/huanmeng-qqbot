# -*- coding: utf-8 -*-
"""面板「Steam 代理令牌」端到端往返测试

覆盖：CF 自检 → 台账 → 生成令牌 → **真实调 Worker 验证可用** → 撤销 →
再调 Worker 验证 401 → 台账恢复原状。

用法（服务器上）：
    PANEL_PW=xxx python3 scripts/_verify_steam_proxy_api.py
"""
import os
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:59300/api"
ENDPOINT = os.environ.get("STEAM_PROXY", "https://steamapi.truslerweb.dpdns.org")
SID = "76561199427581023"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
PW = os.environ.get("PANEL_PW", "HuanmengPanel@2026")

RESULT = []


def check(name, ok, extra=""):
    RESULT.append((name, ok))
    print("   [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  " + extra) if extra else ""))


def worker(token: str, timeout: float = 40.0):
    """直接调 CF Worker，返回 (http_code, body头)"""
    try:
        r = httpx.post(ENDPOINT, timeout=timeout, trust_env=False,
                       headers={"Content-Type": "application/json",
                                "X-Proxy-Token": token, "User-Agent": UA},
                       json={"ops": [{"k": "api",
                                      "p": "IPlayerService/GetSteamLevel/v1/",
                                      "q": {"steamid": SID}}]})
        return r.status_code, r.text[:90]
    except Exception as e:
        return 0, "%s: %s" % (type(e).__name__, str(e)[:70])


def main():
    c = httpx.Client(timeout=45, trust_env=False)
    try:
        lg = c.post(BASE + "/auth/login",
                    json={"username": "admin", "password": PW})
        if lg.status_code != 200:
            print("登录失败 HTTP %d：%s" % (lg.status_code, lg.text[:120]))
            return 1
        H = {"Authorization": "Bearer " + lg.json()["token"]}
    except Exception as e:
        print("登录异常: %s: %r" % (type(e).__name__, e))
        return 1

    print("=== 1. CF 连通性与权限自检 ===")
    st = c.get(BASE + "/steam-proxy/status", headers=H).json()
    print("   configured=%s ok=%s worker=%s" % (st.get("configured"), st.get("ok"),
                                                st.get("worker")))
    if st.get("error"):
        print("   error:", st["error"])
    print("   CF 侧 secrets =", st.get("cf_secrets"))
    check("CF 权限可用", bool(st.get("ok")))
    check("CF 侧含 PROXY_TOKEN", "PROXY_TOKEN" in (st.get("cf_secrets") or []))
    check("CF 侧含 PROXY_TOKENS", "PROXY_TOKENS" in (st.get("cf_secrets") or []))

    print()
    print("=== 2. 台账 ===")
    t0 = c.get(BASE + "/steam-proxy/tokens", headers=H).json()
    before = t0.get("tokens") or []
    print("   共 %d 个 | 上次同步=%s | cf_ok=%s" % (t0.get("count"), t0.get("last_sync_text"),
                                                  t0.get("cf_ok")))
    for t in before:
        print("     %-10s %-14s %s" % (t["kind"], t["label"], t["token"][:12] + "…"))
    check("台账非空（已从 .env 导入主令牌）", len(before) >= 1)
    check("主令牌存在且不可删标记正确",
          any(t["primary"] for t in before))

    print()
    print("=== 3. 生成测试令牌（会真实写入 CF）===")
    r = c.post(BASE + "/steam-proxy/tokens", headers=H,
               json={"label": "自测-临时"})
    if r.status_code != 200:
        print("   生成失败 HTTP %d: %s" % (r.status_code, r.text[:200]))
        return 1
    created = r.json()
    newtok = created["token"]
    print("   令牌 = %s… | synced=%s" % (newtok[:14], created.get("synced")))
    if created.get("sync_error"):
        print("   sync_error:", created["sync_error"])
    check("生成接口返回新令牌", len(newtok) >= 32)
    check("已同步到 Cloudflare", bool(created.get("synced")))

    print()
    print("=== 4. 等 8s 后，用新令牌真实调用 Worker ===")
    time.sleep(8)
    code, body = worker(newtok)
    print("   HTTP %s  %s" % (code, body[:80]))
    check("新令牌可用（HTTP 200 且 ok=true）",
          code == 200 and '"ok":true' in body)

    print()
    print("=== 5. 撤销该令牌 ===")
    d = c.delete(BASE + "/steam-proxy/tokens/" + created["id"], headers=H)
    if d.status_code != 200:
        print("   撤销失败 HTTP %d: %s" % (d.status_code, d.text[:200]))
        return 1
    dj = d.json()
    print("   deleted=%s synced=%s" % (dj.get("deleted"), dj.get("synced")))
    check("撤销已同步到 Cloudflare", bool(dj.get("synced")))

    print()
    print("=== 6. 等 8s 后，该令牌应失效（401）===")
    time.sleep(8)
    code2, body2 = worker(newtok)
    print("   HTTP %s  %s" % (code2, body2[:80]))
    check("撤销后令牌失效（401）", code2 == 401)

    print()
    print("=== 7. 台账恢复检查（应与操作前一致）===")
    t1 = c.get(BASE + "/steam-proxy/tokens", headers=H).json()
    after = t1.get("tokens") or []
    same = [t["id"] for t in after] == [t["id"] for t in before]
    print("   操作前 %d 个 / 现在 %d 个 | id 序列一致=%s"
          % (len(before), len(after), same))
    check("台账已还原（无残留测试令牌）", same)
    check("主令牌未被触碰", after and after[0]["token"] == before[0]["token"])

    print()
    print("=== 8. 主令牌仍然可用（确认没写坏）===")
    maintok = before[0]["token"]
    code3, body3 = worker(maintok)
    print("   HTTP %s  %s" % (code3, body3[:80]))
    check("主令牌可用", code3 == 200 and '"ok":true' in body3)

    print()
    bad = [n for n, ok in RESULT if not ok]
    print("=" * 46)
    print("结果: %d/%d 通过" % (len(RESULT) - len(bad), len(RESULT)))
    if bad:
        print("失败项:", "；".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
