#!/usr/bin/env python3
"""用 Cloudflare API 部署 steamapi Worker（无需 wrangler）。

为什么带 bindings：
  这个 Worker 的 STEAM_KEY / PROXY_TOKEN / PROXY_TOKENS 都是 secret。
  如果 PUT 时只传代码不传 bindings，有可能把 secret 一起清掉 —— 那会导致
  Steam 代理整体不可用。所以这里把 .env 里的三个值**显式重新声明**回去
  （值只从 .env 读，不回显）。

用法（服务器）:
  python3 scripts/_deploy_cf_worker.py            # 部署
  python3 scripts/_deploy_cf_worker.py --dry-run  # 只打印将要发送的元数据（不含密钥值）
"""
import json
import os
import ssl
import sys
import urllib.request
import uuid
from pathlib import Path

ENV_FILE = Path("/root/bot/config/.env")
WORKER_SRC = Path("/root/bot/deploy/cf-steam-proxy/_worker.js")
COMPAT_DATE = "2026-09-24"


def load_env() -> dict:
    d = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip()
    return d


def build_multipart(fields: dict, files: dict):
    """fields: name->str ; files: name->(filename, bytes)"""
    boundary = "----cfdeploy" + uuid.uuid4().hex
    out = bytearray()
    for name, val in fields.items():
        out += ("--%s\r\n" % boundary).encode()
        out += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode()
        out += val.encode("utf-8") + b"\r\n"
    for name, (fn, data) in files.items():
        out += ("--%s\r\n" % boundary).encode()
        out += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                % (name, fn)).encode()
        out += b"Content-Type: application/javascript+module\r\n\r\n"
        out += data + b"\r\n"
    out += ("--%s--\r\n" % boundary).encode()
    return bytes(out), boundary


def main():
    dry = "--dry-run" in sys.argv
    env = load_env()
    token = env.get("CF_API_TOKEN", "")
    acct = env.get("CF_ACCOUNT_ID", "")
    name = env.get("CF_WORKER_NAME", "")
    if not (token and acct and name):
        print("缺 CF_API_TOKEN / CF_ACCOUNT_ID / CF_WORKER_NAME")
        sys.exit(1)

    # ⚠️ binding 名与 .env 里的键名**不一致**：worker 里叫 PROXY_TOKEN(S)，
    #    而 bot 的 .env 里叫 STEAM_PROXY_TOKEN(S)。第一版照着 binding 名去 .env 找，
    #    只找到 STEAM_KEY -> 若真部署就会把两个令牌 secret 丢掉，
    #    allowedTokens() 变空 -> 鉴权被跳过 -> 代理变成开放代理。
    BINDING_SOURCES = {
        "STEAM_KEY": ["STEAM_KEY"],
        "PROXY_TOKEN": ["PROXY_TOKEN", "STEAM_PROXY_TOKEN"],
        "PROXY_TOKENS": ["PROXY_TOKENS", "STEAM_PROXY_TOKENS"],
    }
    bindings = []
    missing = []
    for bname, keys in BINDING_SOURCES.items():
        v = ""
        for k in keys:
            if env.get(k):
                v = env[k]
                break
        if v:
            bindings.append({"type": "secret_text", "name": bname, "text": v})
        else:
            missing.append(bname)
    names = [b["name"] for b in bindings]
    if missing:
        print("❌ 这些 binding 在 .env 里找不到值: %s" % missing)
        print("   直接部署会把对应 secret 清掉（鉴权会失效），已中止。")
        sys.exit(1)

    meta = {
        "main_module": "_worker.js",
        "compatibility_date": COMPAT_DATE,
        "usage_model": "standard",
        "bindings": bindings,
    }
    if dry:
        safe = dict(meta)
        safe["bindings"] = [{"type": b["type"], "name": b["name"],
                             "text": "<%d 字符，已隐藏>" % len(b["text"])} for b in bindings]
        print("将发送的 metadata:")
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        print("\n源码:", WORKER_SRC, "%d 字节" % WORKER_SRC.stat().st_size)
        return

    body, boundary = build_multipart(
        {"metadata": json.dumps(meta, ensure_ascii=False)},
        {"_worker.js": ("_worker.js", WORKER_SRC.read_bytes())},
    )
    url = ("https://api.cloudflare.com/client/v4/accounts/%s/workers/scripts/%s/content"
           % (acct, name))
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "multipart/form-data; boundary=" + boundary,
    })
    ctx = ssl.create_default_context()
    print("部署中… worker=%s  源码 %d 字节  bindings=%s"
          % (name, WORKER_SRC.stat().st_size, names))
    try:
        with urllib.request.urlopen(req, timeout=90, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        print("❌ HTTP %s" % e.code)
        print(e.read().decode("utf-8", "replace")[:900])
        sys.exit(1)
    print("success =", data.get("success"))
    if not data.get("success"):
        print(json.dumps(data.get("errors"), ensure_ascii=False)[:900])
        sys.exit(1)
    r = data.get("result") or {}
    print("  id      =", r.get("id"))
    print("  etag    =", r.get("etag"))
    print("  modified=", r.get("modified_on"))
    print("✅ 部署成功")


main()
