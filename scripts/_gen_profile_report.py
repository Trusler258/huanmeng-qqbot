# -*- coding: utf-8 -*-
"""生成用户画像审阅报告（HTML）。

数据源：服务器 data/user_profiles.json
输出：`G:/py/用户画像审阅/index.html`
"""
import html
import json
from datetime import datetime
from pathlib import Path

SRC = Path(r"G:\py\_profiles.json")
OUT = Path(r"G:\py\用户画像审阅\index.html")
FIELDS = [("role", "身份"), ("demands", "常问"), ("preference", "偏好"),
          ("habit", "习惯"), ("warning", "注意")]

data = json.loads(SRC.read_text(encoding="utf-8"))


def scope_of(k):
    if k.startswith("g") and ":" in k:
        return "group", k[1:].split(":", 1)[0], k[1:].split(":", 1)[1]
    if k.startswith("p"):
        return "private", "", k[1:]
    return "legacy", "", k


rows = []
for k, v in data.items():
    sc, gid, qq = scope_of(k)
    filled = sum(1 for f, _ in FIELDS if (v.get(f) or "").strip())
    rows.append({"key": k, "scope": sc, "group": gid, "qq": qq, "p": v, "filled": filled})

rows.sort(key=lambda r: (-r["filled"], -(r["p"].get("days") or 0)))

n_group = sum(1 for r in rows if r["scope"] == "group")
n_priv = sum(1 for r in rows if r["scope"] == "private")
n_filled = sum(1 for r in rows if r["filled"] > 0)
n_ts = sum(1 for r in rows if (r["p"].get("last_msg_at") or 0) > 0)
tot_msgs = sum((r["p"].get("msg_count") or 0) for r in rows)


def esc(s):
    return html.escape(str(s or ""))


def card(r):
    p = r["p"]
    scope_label = ("群 %s" % r["group"]) if r["scope"] == "group" else "私聊"
    body = []
    for f, label in FIELDS:
        val = (p.get(f) or "").strip()
        if val:
            body.append(
                '<div class="f"><span class="k">%s</span><span class="v">%s</span></div>'
                % (label, esc(val)))
    if not body:
        body.append('<div class="empty">这个会话还没形成画像</div>')
    ts = p.get("updated_at")
    ts_txt = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "—"
    return f"""<article class="card {'g' if r['scope']=='group' else 'p'}">
  <header>
    <span class="tag">{scope_label}</span>
    <span class="nick">{esc(p.get('nick')) or '（无昵称）'}</span>
    <span class="qq">{r['qq']}</span>
    <span class="meta">已分析 {p.get('msg_count') or 0} 条 · 建档 {p.get('days') or 0} 天 · {ts_txt}</span>
  </header>
  <div class="body">{''.join(body)}</div>
</article>"""


groups = [r for r in rows if r["scope"] == "group"]
privs = [r for r in rows if r["scope"] == "private"]

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>用户画像审阅</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:30px 26px 70px;
    font:14px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
    background:#f5f6f8; color:#1f2328; }}
  h1 {{ font-size:20px; margin:0 0 6px; }}
  .lead {{ color:#57606a; margin:0 0 18px; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:12px; }}
  .stat {{ background:#fff; border:1px solid #d8dee4; border-radius:8px;
    padding:10px 14px; }}
  .stat b {{ font-size:19px; }}
  .stat span {{ color:#57606a; font-size:12px; margin-left:6px; }}
  h2 {{ font-size:15px; margin:26px 0 12px; padding-bottom:6px;
    border-bottom:1px solid #d8dee4; }}
  .grid {{ display:grid; gap:12px;
    grid-template-columns:repeat(auto-fill,minmax(400px,1fr)); align-items:start; }}
  .card {{ background:#fff; border:1px solid #d8dee4; border-left:3px solid #b4b2a9;
    border-radius:9px; overflow:hidden; }}
  .card.g {{ border-left-color:#378ADD; }}
  .card.p {{ border-left-color:#639922; }}
  .card header {{ padding:9px 13px; border-bottom:1px solid #eaeef2;
    display:flex; flex-wrap:wrap; align-items:baseline; gap:8px; }}
  .tag {{ font-size:11px; padding:2px 7px; border-radius:5px;
    background:#f0f2f4; color:#57606a; white-space:nowrap; }}
  .card.g .tag {{ background:#E6F1FB; color:#185FA5; }}
  .card.p .tag {{ background:#EAF3DE; color:#3B6D11; }}
  .nick {{ font-weight:500; font-size:14px; }}
  .qq {{ font:11.5px ui-monospace,Consolas,monospace; color:#6e7781; }}
  .meta {{ font-size:11.5px; color:#6e7781; margin-left:auto; white-space:nowrap; }}
  .body {{ padding:11px 13px 13px; }}
  .f {{ display:flex; gap:9px; margin:3px 0; align-items:baseline; }}
  .k {{ flex:0 0 34px; font-size:12px; color:#6e7781; }}
  .v {{ flex:1; font-size:13px; }}
  .empty {{ color:#8c959f; font-size:12.5px; }}
  .note {{ margin-top:26px; background:#fff; border:1px solid #d8dee4;
    border-radius:9px; padding:14px 17px; }}
  .note h3 {{ font-size:14px; margin:0 0 8px; }}
  .note ul {{ margin:0; padding-left:19px; color:#3d444d; font-size:13px; }}
  .note li {{ margin:4px 0; }}
</style>
</head>
<body>

<h1>用户画像审阅</h1>
<p class="lead">
  数据来自 <code>data/user_profiles.json</code>，v2.3.34 架构：每天 00:05 回看前一天记录、
  在旧画像上增量更新；同一个人在<b>每个群</b>和<b>私聊</b>各有一份画像。
  更新时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}
</p>

<div class="stats">
  <div class="stat"><b>{len(rows)}</b><span>画像总数</span></div>
  <div class="stat"><b>{n_group}</b><span>群聊</span></div>
  <div class="stat"><b>{n_priv}</b><span>私聊</span></div>
  <div class="stat"><b>{n_filled}</b><span>有内容的</span></div>
  <div class="stat"><b>{n_ts}</b><span>近一天活跃</span></div>
  <div class="stat"><b>{tot_msgs}</b><span>累计已分析条数</span></div>
</div>

<h2>群聊画像（{len(groups)} 份）</h2>
<div class="grid">
{''.join(card(r) for r in groups)}
</div>

<h2>私聊画像（{len(privs)} 份）</h2>
<div class="grid">
{''.join(card(r) for r in privs)}
</div>

<div class="note">
  <h3>怎么读这份报告</h3>
  <ul>
    <li>按「有内容的字段数」倒序，越靠前信息越完整</li>
    <li>「已分析 N 条」= 这个画像基于多少条发言得出（回填了近 7 天）</li>
    <li>同一个人会出现在多个群，各自画像不同 —— 这是设计目标，不是重复</li>
    <li>字段是自由文本，看不出依据的会留空（不会硬凑）</li>
  </ul>
</div>

</body>
</html>
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(HTML, encoding="utf-8")
print("已生成: %s" % OUT)
print("画像 %d 份（群 %d / 私聊 %d），有内容 %d 份" % (len(rows), n_group, n_priv, n_filled))
