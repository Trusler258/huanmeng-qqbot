from pathlib import Path
from playwright.sync_api import sync_playwright

TPL = Path(r"G:\py\qqbot\data\templates")
OUT = Path(r"G:\py\qqbot\data\img_temp")
OUT.mkdir(parents=True, exist_ok=True)

changelog_md = """
<h1>更新日志</h1>
<h2><span class="icon-h2">✨</span>v1.4.2 — OP 权限系统 + 模式系统</h2>
<span class="tag-new">NEW</span>
<ul>
<li><strong>OP 权限系统</strong>：admin > op > 用户，群主权限 1对多</li>
<li><code>/~persona</code> 人格切换、<code>/~主人</code> 私聊主人指定</li>
<li>模式系统：<code>/~sleep</code> 睡觉、<code>/~含蓄</code> <code>/~叙事</code> 含蓄叙述</li>
</ul>
<span class="tag-fix">FIX</span>
<ul>
<li>NapCat post_type 缺失兼容</li>
<li>scp 丢行连锁崩溃修复</li>
</ul>
<span class="tag-optimize">OPT</span>
<ul>
<li>6个文件20+处静默 except:pass 改为 logger.warning</li>
</ul>
<h3>性能数据</h3>
<table>
<thead><tr><th>指标</th><th>优化前</th><th>优化后</th></tr></thead>
<tbody>
<tr><td>截图速度</td><td>2.1s</td><td>0.8s</td></tr>
<tr><td>内存占用</td><td>180MB</td><td>120MB</td></tr>
</tbody>
</table>
<blockquote>注：数据来自本地测试环境</blockquote>
<pre><code>async def render():
    await page.set_content(html)
    await page.screenshot()</code></pre>
"""

daily_rank = """
<div class="rrow"><div class="rnum r1">🥇</div><div class="rname">张三</div><div class="rbar-w"><div class="rbar rb1" style="width:100%"></div></div><div class="rcnt">156</div></div>
<div class="rrow"><div class="rnum r2">🥈</div><div class="rname">李四</div><div class="rbar-w"><div class="rbar rb2" style="width:72%"></div></div><div class="rcnt">112</div></div>
<div class="rrow"><div class="rnum r3">🥉</div><div class="rname">王五</div><div class="rbar-w"><div class="rbar rb3" style="width:58%"></div></div><div class="rcnt">91</div></div>
<div class="rrow"><div class="rnum">4</div><div class="rname">赵六</div><div class="rbar-w"><div class="rbar" style="width:35%"></div></div><div class="rcnt">55</div></div>
<div class="rrow"><div class="rnum">5</div><div class="rname">孙七</div><div class="rbar-w"><div class="rbar" style="width:20%"></div></div><div class="rcnt">31</div></div>
"""
daily_hours = "".join(f'<div class="hr-c l{min(i%6,5)}"></div>' for i in range(24))
daily_facts = """
<div class="fn-it">🗣️ 今日金话筒：<strong>张三</strong>，贡献了 156 条消息，占全群 38%</div>
<div class="fn-it">🤿 深海潜水员：<strong>孙七</strong>，仅冒泡 31 次</div>
<div class="fn-it">😴 全员休眠期：3点、4点</div>
"""
weather_rows = """
<tr class="today-row"><td>08-02</td><td class="weather-icon-cell">☀️<br><span>晴</span></td><td class="temp-high">35°</td><td class="temp-low">26°</td><td class="wind-text">东南风3级</td><td><span class="aqi-badge aqi-good">良(45)</span></td></tr>
<tr><td>08-03</td><td class="weather-icon-cell">⛅<br><span>多云</span></td><td class="temp-high">33°</td><td class="temp-low">25°</td><td class="wind-text">东风2级</td><td><span class="aqi-badge aqi-excellent">优(38)</span></td></tr>
<tr><td>08-04</td><td class="weather-icon-cell">🌧️<br><span>小雨</span></td><td class="temp-high">30°</td><td class="temp-low">24°</td><td class="wind-text">北风3级</td><td><span class="aqi-badge aqi-moderate">轻度(68)</span></td></tr>
"""
weather_talk = '<div class="talk-section"><div class="talk-item highlight">🌡️ 近七日气温范围：<b>23°C ~ 35°C</b></div><div class="talk-item warning">🌧️ 预计以下日期有雨：08-04</div><div class="talk-item">👌 平均空气质量：<b>良</b> (AQI=48)</div></div>'
box_timeline = """
<div class="timeline-item"><div class="timeline-dot active"></div><div class="timeline-card current"><span class="tl-time">08-02 14:23</span><span class="tl-msg">【派送中】快件已到达广州天河区网点，正在派送</span></div></div>
<div class="timeline-item"><div class="timeline-dot done"></div><div class="timeline-card"><span class="tl-time">08-02 08:15</span><span class="tl-msg">【运输中】快件已从广州转运中心发出</span></div></div>
<div class="timeline-item"><div class="timeline-dot done"></div><div class="timeline-card"><span class="tl-time">08-01 22:30</span><span class="tl-msg">【运输中】快件已到达广州转运中心</span></div></div>
"""
lb_entries = """
<div class="entry"><span class="rank">#1</span><div class="head"><img src="x"></div><span class="name">PlayerOne</span><span class="value">1523400 分</span></div>
<div class="entry"><span class="rank">#2</span><div class="head"><img src="x"></div><span class="name">TopGamer</span><span class="value">1489200 分</span></div>
<div class="entry"><span class="rank">#3</span><div class="head"><img src="x"></div><span class="name">ProPlayer</span><span class="value">1356700 分</span></div>
<div class="entry"><span class="rank">#4</span><div class="head"><img src="x"></div><span class="name">Newbie</span><span class="value">980000 分</span></div>
"""
wzq_cells = '<div class="row-label">15</div>' + ''.join('<div class="cell"></div>' for _ in range(15))
md_content = """
<h1>帮助文档</h1>
<h2><span class="icon-h2">📋</span>基础指令</h2>
<ul>
<li><code>/~weather 城市</code> — 查询天气预报</li>
<li><code>/~eq</code> — 查询最新地震信息</li>
<li><code>/~stats</code> — 查看群聊统计</li>
</ul>
<h2><span class="icon-h2">🔧</span>管理员指令</h2>
<span class="tag-new">NEW</span>
<ul>
<li><code>/~op add QQ 群号</code> — 添加 OP</li>
</ul>
<h3>示例</h3>
<pre><code>/~op add 123456 789012</code></pre>
<blockquote>OP 在指派群内自动获得 admin 等价权限</blockquote>
"""

cases = [
    ("changelog_card", 760, {"{{VERSION}}": "v1.4.2", "{{RELEASE_DATE}}": "2026年08月02日", "{{CHANGELOG_MARKDOWN}}": changelog_md, "{{BRAND}}": "Generated by 幻梦"}),
    ("daily_report", 720, {"{{GROUP_ID}}": "123456789", "{{GROUP_NAME}}": "测试群", "{{REPORT_DATE}}": "2026.08.01", "{{TOTAL_MSGS}}": "412", "{{PARTICIPANTS}}": "12", "{{PEAK_HOUR}}": "21:00", "{{AVG_MSG}}": "34.3", "{{RANKING_ROWS}}": daily_rank, "{{HOURS_CELLS}}": daily_hours, "{{FUN_FACTS}}": daily_facts, "{{REPORT_TIME}}": "00:00", "{{BRAND}}": "幻梦 Project"}),
    ("weather_card", 560, {"{{TODAY_ICON}}": "☀️", "{{CITY_NAME}}": "广州", "{{HEADER_DATE}}": "2026年8月2日 星期六", "{{TODAY_TEMP}}": "35", "{{TODAY_COND}}": "晴", "{{TEMP_RANGE}}": "26℃ ~ 35℃", "{{TALK_SECTION}}": weather_talk, "{{FORECAST_ROWS}}": weather_rows, "{{AQI_SUMMARY}}": "近七日平均 AQI 为 <b>48</b>，空气质量良。", "{{DATA_TIME}}": "08-02 14:00", "{{BRAND}}": "Generated by 幻梦"}),
    ("box_card", 560, {"{{CP_NAME}}": "顺丰速运", "{{TRACKING_NO}}": "SF1234567890", "{{STATE_CLASS}}": "badge-delivering", "{{STATE_ICON}}": "🏃", "{{STATE_TEXT}}": "派送中", "{{LATEST_SECTION}}": '<div class="latest-bar"><span class="label">最新动态</span><span class="msg">快件已到达广州天河区网点，正在派送</span></div>', "{{TIMELINE_ITEMS}}": box_timeline, "{{TIP_SECTION}}": '<div class="tip-bar tip-delivering">📬 包裹正在派送中，请留意电话通知！</div>', "{{QUERY_TIME}}": "08-02 14:30", "{{BRAND}}": "Generated by 幻梦"}),
    ("leaderboard_card", 440, {"{{TITLE}}": "节奏大师 综合榜", "{{PERIOD}}": "总榜", "{{ENTRIES}}": lb_entries, "{{BRAND}}": "Generated by 幻梦"}),
    ("md_card", 560, {"{{CONTENT}}": md_content, "{{BRAND}}": "洛花星雨 Nexus"}),
    ("wzq_board", 680, {"${SUB_TEXT}": "手数 3 | 轮到黑方", "${DATE}": "2026-08-02 14:00", "${BLACK_NAME}": "Player1", "${WHITE_NAME}": "Player2", "${BLACK_ACTIVE}": "active-turn", "${WHITE_ACTIVE}": "", "${COL_LABELS}": "".join(f'<div class="col-label">{c}</div>' for c in "ABCDEFGHIJKLMNO"), "${CELLS}": wzq_cells, "${STATUS_CLASS}": "playing", "${STATUS_TEXT}": "轮到 Player1 落子 (黑)", "${MOVE_COUNT}": "3"}),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox","--disable-gpu"])
    for name, width, vars in cases:
        html = (TPL / f"{name}.html").read_text(encoding="utf-8")
        for k, v in vars.items():
            html = html.replace(k, v)
        page = browser.new_page()
        page.set_viewport_size({"width": width, "height": 10})
        page.set_content(html, wait_until="domcontentloaded")
        page.wait_for_timeout(200)
        out = OUT / f"test_{name}.png"
        page.screenshot(path=str(out), full_page=True, type="png")
        page.close()
        print(f"OK {name} -> {out.stat().st_size//1024}KB")
    browser.close()
print("DONE")