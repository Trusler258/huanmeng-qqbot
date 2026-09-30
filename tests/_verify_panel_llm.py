"""LLM 供应商页 后端接口验收（走真实 HTTP）

原则：
- 供应商写往返用**专用测试供应商** TESTLLM_CHECK，跑完删除并比对 .env 快照
- 模型槽位只做「等值回写」（写回与原值相同的 name/provider）→ 文件应几乎逐字节一致，
  以此验证 _replace_section_kv 不会破坏注释/排版；绝不改真实值
- 连通性测试打假 URL，验证错误分类路径（不碰真实 key、不耗 token）
"""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:49300"
ENV_PATH = "/root/bot/config/.env"
TOML_PATH = "/root/bot/config/bot_config.toml"

_results = []


def ok(msg):
    _results.append(("ok", msg))
    print("  OK  " + msg)


def bad(msg):
    _results.append(("bad", msg))
    print("  FAIL " + msg)


def api(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def read_file(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def main():
    # ── 登录 ──
    st, login = api("POST", "/api/auth/login",
                    {"username": "admin", "password": PWD})
    if st != 200:
        print("登录失败", st, login)
        sys.exit(1)
    tok = login.get("token") or (login.get("data") or {}).get("token")
    ok("登录成功")

    env_snap = read_file(ENV_PATH)
    toml_snap = read_file(TOML_PATH)

    # ── 1. overview ──
    st, ov = api("GET", "/api/llm/overview", token=tok)
    if st == 200 and ov.get("providers") and ov.get("slots"):
        names = [p["name"] for p in ov["providers"]]
        ok("overview: 供应商=%s 槽位=%d" % (names, len(ov["slots"])))
        assert "STEAM" not in names and "BOT_PC" not in names, "功能性凭据混进来了"
        ok("功能性凭据（STEAM/BOT_PC 等）未混入供应商列表")
    else:
        bad("overview 异常: %s %s" % (st, str(ov)[:200]))

    # ── 2. 供应商创建 ──
    st, r = api("POST", "/api/llm/providers", {
        "name": "TESTLLM_CHECK", "url": "https://example.invalid/v1",
        "key": "sk-roundtrip123456789"}, tok)
    if st == 200:
        ok("创建测试供应商 OK")
    else:
        bad("创建失败: %s %s" % (st, str(r)[:200]))

    # .env 里应有两行、其余内容不变
    env2 = read_file(ENV_PATH)
    added = [l for l in env2.splitlines() if l.startswith("TESTLLM_CHECK_")]
    if len(added) == 2 and all(
            l in env2 for l in env_snap.splitlines() if not l.startswith("TESTLLM_CHECK_")):
        ok(".env 只新增了 2 行，其余逐行保留")
    else:
        bad(".env 变动异常: %s" % added)

    # ── 3. 更新 key（url 留空 = 不改）──
    st, r = api("PUT", "/api/llm/providers/TESTLLM_CHECK",
                {"url": "", "key": "sk-newkey99887766"}, tok)
    if st == 200 and "sk-newkey99887766" in read_file(ENV_PATH):
        ok("更新 key OK（url 未动）")
    else:
        bad("更新 key 失败: %s" % st)

    # ── 4. 连通性测试（假 URL → 网络错误分类）──
    st, r = api("POST", "/api/llm/test/TESTLLM_CHECK", token=tok)
    if st == 200 and r.get("ok") is False and r.get("http") == 0:
        ok("连通性测试：假 URL 正确归类为「连不上」(%s)" % r.get("msg", "")[:60])
    else:
        bad("连通性测试分类异常: %s %s" % (st, str(r)[:160]))

    # ── 5. 删除 ──
    st, r = api("DELETE", "/api/llm/providers/TESTLLM_CHECK", token=tok)
    env3 = read_file(ENV_PATH)
    if st == 200 and "TESTLLM_CHECK" not in env3:
        ok("删除供应商 OK，.env 已无残留")
    else:
        bad("删除异常: %s" % st)
    after = [l for l in env3.splitlines()
             if not l.startswith("TESTLLM_CHECK_")]
    orig = [l for l in env_snap.splitlines()]
    if after == orig:
        ok(".env 与操作前逐行一致（往返无损）")
    else:
        bad(".env 往返后不一致！")

    # ── 6. 被引用的供应商不可删 ──
    st, r = api("DELETE", "/api/llm/providers/DEEPSEEK", token=tok)
    if st == 409:
        ok("删除被引用供应商被拒(409): %s" % str(r.get("detail", ""))[:60])
    else:
        bad("被引用供应商删除未被拒: %s" % st)

    # ── 7. 槽位等值回写（不改真实值）──
    slots = ov["slots"]
    slot, cur = "replyer_1", slots["replyer_1"]
    st, r = api("PUT", "/api/llm/models/" + slot, {
        "name": cur["name"], "provider": cur["provider"],
        "maxtoken": None, "switch": None}, tok)
    if st != 200:
        bad("槽位回写失败: %s %s" % (st, str(r)[:200]))
    else:
        toml2 = read_file(TOML_PATH)
        diff = [i for i, (a, b) in enumerate(
            zip(toml_snap.splitlines(), toml2.splitlines())) if a != b]
        extra = len(toml2.splitlines()) - len(toml_snap.splitlines())
        if not diff and extra == 0:
            ok("槽位等值回写：bot_config.toml 逐字节一致")
        elif len(diff) <= 2 and extra == 0:
            ok("槽位等值回写：仅 %d 行格式微差（注释/空格）" % len(diff))
            for i in diff[:2]:
                print("      -%r" % toml_snap.splitlines()[i])
                print("      +%r" % toml2.splitlines()[i])
        else:
            bad("槽位回写改动过大: diff=%d extra=%d" % (len(diff), extra))

    # ── 8. 非法输入 ──
    st, _ = api("PUT", "/api/llm/models/nope", {
        "name": "x", "provider": "DEEPSEEK"}, tok)
    ok_if = st == 404
    (ok if ok_if else bad)("未知槽位被拒(%s)" % st)
    st, _ = api("PUT", "/api/llm/models/picture", {
        "name": "glm-4v-flash", "provider": "NOSUCHPROV"}, tok)
    (ok if st == 400 else bad)("未知供应商被拒(%s)" % st)
    st, _ = api("POST", "/api/llm/providers", {
        "name": "bad name", "url": "https://x", "key": ""}, tok)
    (ok if st == 400 else bad)("非法供应商名被拒(%s)" % st)

    # ── 汇总 ──
    n_ok = sum(1 for t, _ in _results if t == "ok")
    n_bad = sum(1 for t, _ in _results if t == "bad")
    print("\n=== 结果: %d passed, %d failed ===" % (n_ok, n_bad))
    sys.exit(1 if n_bad else 0)


try:
    PWD = sys.argv[1]
except IndexError:
    PWD = ""
main()
