"""面板后端回归测试 — v0.1.0 P1

覆盖：
  A. 路径安全（6 种逃逸向量全拒）
  B. 脱敏（密钥/IP/域名/邮箱/Bearer）
  C. 原子写（备份 + 内容正确）
  D. 认证（签发/校验/改密失效/限速）
  E. 接口默认拒绝（未授权全 401）
  F. 白名单（非白名单路径写入被拒）

跑法（必须用系统 Python，managed 3.13 缺依赖）：
    C:\\Users\\Huang\\AppData\\Local\\Programs\\Python\\Python312\\python.exe tests\\_test_panel_backend.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        FAILED.append(f"{name} {extra}")
        print(f"  [FAIL] {name}  {extra}")


# ── A. 路径安全 ───────────────────────────────────────────
print("\n=== A. 路径安全 ===")
from panel.security import PathEscape, safe_path  # noqa: E402

ESCAPES = [
    "../../../etc/passwd",
    "../../etc/passwd",
    "/etc/passwd",
    "/root/.ssh/id_rsa",
    "data/../../etc/passwd",
    "data/notes/../../../.env",
    "C:/Windows/win.ini",
    "\\\\server\\share\\x",
]
for p in ESCAPES:
    try:
        safe_path(p)
        check(f"拒绝逃逸: {p}", False, "未被拦截！")
    except PathEscape:
        check(f"拒绝逃逸: {p}", True)
    except Exception as e:
        check(f"拒绝逃逸: {p}", False, f"异常类型错误 {type(e).__name__}")

# 含 NUL
try:
    safe_path("data/x\x00.md")
    check("拒绝含 NUL 路径", False, "未被拦截！")
except PathEscape:
    check("拒绝含 NUL 路径", True)

# 正常路径应通过
try:
    r = safe_path("data/fav.json")
    check("合法路径放行", r.name == "fav.json")
except Exception as e:
    check("合法路径放行", False, str(e))

# 限定 data/ 时，ROOT 下的相对路径应被拒
from panel.security import assert_in_data  # noqa: E402

try:
    assert_in_data("config/version.toml")
    check("assert_in_data 拒绝 data/ 外路径", False, "未被拦截！")
except PathEscape:
    check("assert_in_data 拒绝 data/ 外路径", True)

try:
    assert_in_data("data/fav.json")
    check("assert_in_data 放行 data/ 内", True)
except Exception as e:
    check("assert_in_data 放行 data/ 内", False, str(e))


# ── B. 脱敏 ───────────────────────────────────────────────
print("\n=== B. 敏感信息脱敏 ===")
from panel.security import sanitize_obj, sanitize_text  # noqa: E402

CASES = [
    ('key = "sk-abcdef1234567890abcdef"', ["sk-abcdef1234567890abcdef"], "sk- 密钥"),
    ("api_key = sk-proj-AbCdEf1234567890XyZ", ["sk-proj-AbCdEf1234567890XyZ"], "api_key 值"),
    ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc", ["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"], "Bearer"),
    ("server = 123.163.121.224", ["123.163.121.224"], "公网 IP"),
    ("host = 01240820.xyz", ["01240820.xyz"], "域名"),
    ("mail = someone@gmail.com", ["someone@gmail.com", "someone@"], "邮箱"),
    ("password = SuperSecret123", ["SuperSecret123"], "密码"),
]
for src, must_gone, label in CASES:
    out = sanitize_text(src)
    leaked = [m for m in must_gone if m in out]
    check(f"脱敏 {label}", not leaked, f"泄露: {leaked} -> {out}")

# 回环地址必须保留（否则日志、端口判断全废）
out = sanitize_text("local = 127.0.0.1:58888")
check("保留回环地址", "127.0.0.1" in out, out)

# 字典脱敏
d = sanitize_obj({"api_key": "sk-real123456", "port": 59300,
                  "nested": {"token": "abcdef123456"}, "ok": True})
check("dict 脱敏 api_key", d["api_key"] == "[已打码]", str(d))
check("dict 脱敏嵌套 token", d["nested"]["token"] == "[已打码]", str(d))
check("dict 保留正常值", d["port"] == 59300 and d["ok"] is True, str(d))
check("dict 空值不误打码", sanitize_obj({"token": ""})["token"] == "")


# ── C. 原子写 ─────────────────────────────────────────────
print("\n=== C. 原子写与备份 ===")
from panel.security import atomic_write_json, atomic_write_text, read_json  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "t.txt"
    atomic_write_text(p, "第一版")
    check("首次写入", p.read_text(encoding="utf-8") == "第一版")
    bak = atomic_write_text(p, "第二版")
    check("覆盖写入成功", p.read_text(encoding="utf-8") == "第二版")
    check("生成备份文件", bak is not None and bak.exists())
    check("备份内容为旧版", bak and bak.read_text(encoding="utf-8") == "第一版")

    j = Path(td) / "t.json"
    atomic_write_json(j, {"a": 1, "中文": "值"})
    check("JSON 写入 + 中文不转义", read_json(j) == {"a": 1, "中文": "值"})
    check("不残留临时文件", not list(Path(td).glob(".*.tmp")))

check("read_json 容错", read_json(Path("/nonexistent/x.json"), {"d": 1}) == {"d": 1})


# ── D. 认证 ───────────────────────────────────────────────
print("\n=== D. 认证 ===")
from panel import config as pcfg  # noqa: E402

# 用临时密钥文件测试，避免污染真实配置
_real_secret = pcfg.SECRET_FILE
with tempfile.TemporaryDirectory() as td:
    pcfg.SECRET_FILE = Path(td) / "secret.toml"
    pcfg.load.cache_clear()
    pcfg._generate_secret_file("UnitTestPw123!")
    import importlib
    import panel.auth as pauth
    importlib.reload(pauth)

    cfg = pcfg.load()
    check("密钥文件已生成", pcfg.SECRET_FILE.exists())
    check("密码哈希非明文", "UnitTestPw123!" not in pcfg.SECRET_FILE.read_text(encoding="utf-8"))
    check("secret_configured", cfg.secret_configured)

    check("正确密码通过", pauth.verify_password("UnitTestPw123!"))
    check("错误密码拒绝", not pauth.verify_password("wrong"))
    check("空密码拒绝", not pauth.verify_password(""))

    tok, exp = pauth.issue_token("admin")
    check("签发 token", isinstance(tok, str) and len(tok) > 50)
    check("过期时间在未来", exp > time.time())

    payload = pauth.decode_token(tok)
    check("解码 token", payload.get("sub") == "admin")

    # 篡改 token
    try:
        pauth.decode_token(tok[:-3] + "xyz")
        check("拒绝篡改 token", False, "篡改后仍通过！")
    except Exception:
        check("拒绝篡改 token", True)

    # session_version 变更 → 旧 token 失效
    txt = pcfg.SECRET_FILE.read_text(encoding="utf-8")
    import re as _re
    txt2 = _re.sub(r"session_version\s*=\s*\d+", "session_version = 99", txt)
    pcfg.SECRET_FILE.write_text(txt2, encoding="utf-8")
    pcfg.load.cache_clear()
    try:
        pauth.decode_token(tok)
        check("改密后旧 token 失效", False, "旧 token 仍有效！")
    except Exception:
        check("改密后旧 token 失效", True)

    # 限速
    pcfg.load.cache_clear()
    _ip = "10.0.0.99"
    pauth.clear_fails(_ip)
    check("初始未锁定", pauth.login_locked(_ip) == 0)
    for _ in range(5):
        pauth.record_fail(_ip)
    check("5 次失败后锁定", pauth.login_locked(_ip) > 0)
    pauth.clear_fails(_ip)
    check("清除后可再登录", pauth.login_locked(_ip) == 0)

pcfg.SECRET_FILE = _real_secret
pcfg.load.cache_clear()


# ── E. 接口默认拒绝 ────────────────────────────────────────
print("\n=== E. 接口默认拒绝（静态检查）===")
import importlib  # noqa: E402

importlib.reload(pauth)
from panel.app import app  # noqa: E402

# 遍历 OpenAPI 路径，找出没有任何安全依赖的接口（除白名单）
spec = app.openapi()
paths = spec["paths"]

# 允许免认证的端点
PUBLIC = {"/api/health", "/api/auth/login", "/api/auth/status"}

# 从依赖树里找 require_user
protected_count = 0
unprotected: list[str] = []
for path, methods in paths.items():
    if path in PUBLIC:
        continue
    for method, op in methods.items():
        # FastAPI 把 router 级 dependencies 挂在每个 operation 上
        has_sec = bool(op.get("security")) or any(
            "Security" in str(p.get("$ref", "")) for p in op.get("parameters", [])
        )
        # 更可靠的判断：看 openapi 是否声明了 bearer
        protected_count += 1
        if not has_sec:
            # 检查全局 security schemes
            unprotected.append(f"{method.upper()} {path}")

check("API 端点数 >= 60", protected_count >= 60, f"实际 {protected_count}")

# 用真实请求验证（比静态分析更可信）
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

BASE = os.environ.get("PANEL_TEST_BASE", "http://127.0.0.1:59300")


def _get(path: str, token: str = "") -> tuple[int, str]:
    req = urllib.request.Request(BASE + path)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return -1, str(e)


print(f"  (探测 {BASE})")
code, _ = _get("/api/health")
SERVER_UP = code == 200
if SERVER_UP:
    check("健康检查免认证", code == 200)

    PROTECTED_EPS = [
        "/api/overview", "/api/social/fav", "/api/social/profiles",
        "/api/memory/notes", "/api/memory/stm", "/api/memory/skills",
        "/api/system/services", "/api/system/audit", "/api/system/ports",
        "/api/system/config", "/api/system/update-log",
        "/api/logs", "/api/logs/files", "/api/commands",
        "/api/commands/llm-audit", "/api/features", "/api/economy",
        "/api/plugins", "/api/plugins/hmp", "/api/games/wzq",
        "/api/messages/search", "/api/messages/stats", "/api/earthquake",
    ]
    bad = []
    for ep in PROTECTED_EPS:
        c, _body = _get(ep)
        if c != 401:
            bad.append(f"{ep} -> {c}")
    check(f"未授权访问全部 401（{len(PROTECTED_EPS)} 个端点）", not bad, str(bad))

    # 登录
    import json as _json
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=_json.dumps({"password": os.environ.get("PANEL_TEST_PW", "")}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            tok = _json.loads(r.read())["token"]
        check("登录成功拿 token", True)

        # 带 token 应 200
        bad2 = []
        for ep in ["/api/overview", "/api/features", "/api/commands", "/api/system/services"]:
            c, _b = _get(ep, tok)
            if c != 200:
                bad2.append(f"{ep} -> {c}")
        check("带 token 访问成功", not bad2, str(bad2))

        # 路径逃逸
        import urllib.parse as _up
        bad3 = []
        for esc in ["../../../etc/passwd", "/etc/passwd", "data/../../etc/passwd"]:
            q = _up.urlencode({"rel_path": esc})
            c, _b = _get(f"/api/memory/file?{q}", tok)
            if c != 400:
                bad3.append(f"{esc} -> {c}")
        check("接口层路径逃逸被拒", not bad3, str(bad3))
    except Exception as e:
        check("登录成功拿 token", False, str(e))
else:
    print(f"  [SKIP] 服务未运行（{BASE}），跳过在线测试")
    print("         启动: python -m uvicorn panel.app:app --port 59300")


# ── 汇总 ─────────────────────────────────────────────────
print("\n" + "=" * 56)
print(f"  通过 {PASS}  失败 {FAIL}")
if FAILED:
    print("  失败项：")
    for f in FAILED:
        print(f"    - {f}")
print("=" * 56)
sys.exit(1 if FAIL else 0)
