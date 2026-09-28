#!/usr/bin/env python3
"""比对线上打包后的 worker 与仓库源码的关键常量，做部署前的漂移检查。

线上经 esbuild 打包（含 __defProp/__name 辅助），无法直接文本 diff，
所以比对"有意义的字面量"：主机、批大小、op 上限、path 白名单、UA 等。
用法（服务器）: python3 scripts/_probe_cf_worker_drift.py
"""
import re
import pathlib
import sys

LIVE = pathlib.Path("/tmp/live_mod")            # CF 返回的 multipart
REPO = pathlib.Path("/root/bot/deploy/cf-steam-proxy/_worker.js")

PATS = {
    "API_HOST": r"API_HOST\s*=\s*\"([^\"]+)\"",
    "STORE_HOST": r"STORE_HOST\s*=\s*\"([^\"]+)\"",
    "AD_BATCH": r"AD_BATCH\s*=\s*(\d+)",
    "op上限": r"ops\.length\s*>\s*(\d+)",
    "ISteamUser": r"(ISteamUser[A-Za-z/]*)",
    "appdetails": r"(appdetails)",
    "storesearch": r"(storesearch)",
    "steamcommunity": r"(steamcommunity\.com)",
    "User-Agent片段": r"UA\s*=\s*\"([^\"]{0,40})",
    "token头名": r"(X-Proxy-Token)",
}


def extract_live():
    raw = LIVE.read_text(encoding="utf-8", errors="replace")
    i = raw.find("var __defProp")
    j = raw.rfind("\n--")
    if i < 0:
        return raw
    return raw[i:j] if j > i else raw[i:]


def show(label, text):
    print(f"── {label} ──")
    for name, pat in PATS.items():
        m = re.findall(pat, text)
        val = sorted(set(m))[:4] if m else []
        print("  %-16s %s" % (name, val if val else "(无)"))


def main():
    if not LIVE.exists():
        print("缺 /tmp/live_mod（先跑拉取命令）")
        sys.exit(1)
    live = extract_live()
    print("线上 js 长度:", len(live))
    show("线上（打包后）", live)
    print()
    show("仓库 _worker.js", REPO.read_text(encoding="utf-8"))


main()
