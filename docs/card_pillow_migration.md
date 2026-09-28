# 卡片渲染去 Chromium：现状盘点与迁移计划

> 起因（2026-09-28）：用户反馈"卡片可以不用 Chromium 来渲染吗，太卡了"。
> 已完成的前置工作：v2.3.67 把 Chromium 改成「懒加载 + 空闲 10 分钟自动回收」
> （此前是启动预启动、永不释放，实测常驻 **858MB**）。下面是**彻底去掉 Chromium 依赖**的盘点。

## 一、先看真实用量（别凭感觉排优先级）

30 天 `journalctl -u bot.service | grep "正在截图 →"` 统计（共 118 次）：

| 卡片 | 次数 | 占比 | 调用点 |
|------|------|------|--------|
| 群聊日报 `daily_` | **91** | 77% | `modules/stats.py:319` |
| 五子棋棋盘 `wzq_` | 17 | 14% | `modules/wzq.py:891`（**每步都渲染**） |
| 更新日志卡 `changelog_` | 10 | 8% | `modules/changelog.py:642` |
| 帮助卡 / owner / sys / ping / tuf / 趋势图 / wdsj 单模式 | **0** | — | 见下表 |

→ **77% 的 Chromium 用量集中在日报**。低频卡即使不迁，代价也接近零（现在已懒加载）。

## 二、当前渲染分工

### 已经走 Pillow（保持）
| 卡片 | 实现 |
|------|------|
| wdsj 日榜 / 竞技日榜 | `services/wdsj_card_pillow.py`（`pillow_card` 测试项开关，异常自动回退 Chromium） |
| Steam 资料卡 | `services/steam_card_pillow.py` |
| 围棋棋盘 | `modules/go_game.py::_pillow_board`（同一测试项开关） |
| 通用绘图原语 | `services/card_base.py`（渐变/辉光/圆角遮罩/图标/CJK 字体/阴影/截断，**新卡片直接用这套**） |

### 仍走 Chromium（待迁，按优先级排）
| 优先级 | 卡片 | 调用点 | HTML 模板 | 触发场景 | 备注 |
|-------|------|--------|-----------|---------|------|
| **P1** | 群聊日报 | `modules/stats.py:319` | `daily_report.html`(112) | 每日 0 点，每群一张 | 用量 77%；布局=排行条+24h 热力+锐评 |
| **P2** | 五子棋棋盘 | `modules/wzq.py:891` | 内联 HTML | **每步渲染** → 交互可感 | 照抄 `go_game._pillow_board` 的结构 |
| **P3** | 更新日志卡 | `modules/changelog.py:642` | `changelog_card.html`(489) | `/~changelog` | Markdown 渲染，结构化程度低 |
| P4 | wdsj 日榜（另一条路） | `bot.py:373`、`plugins/bg_tasks/main.py:171/195` | 复用 `daily_rank_card.html` | 与 `commands.py:2936` 的 Pillow 路径**重复** | 应统一走 Pillow，顺手去掉重复 |
| P5 | 象棋/五子棋 Web 页面 | `data/templates/{xq,wzq,go}_web.html` | — | 棋局 Web 页 | 这走的是**浏览器访问**，不是截图，不用迁 |
| P5 | TUFD 谱面卡 ×2 | `modules/tuf_commands.py:156/372` | `tuf_level_card.html`(568)/`tuf_search_results.html`(319) | `/~tuflevel` `/~tufsearch` | 30 天 0 次 |
| P5 | 帮助卡 | `modules/help_card.py:440` | `help_card.html`(172) | `/~help` | 30 天 0 次 |
| P5 | PC/系统状态卡 | `modules/commands.py:3632` | `sys_card.html`(306) | `/~sys` | 30 天 0 次 |
| P5 | owner 配置卡 | `modules/commands.py:3259` | 内联 | `/~owner` | 30 天 0 次 |
| P5 | ping 卡 | `modules/ping.py:305` | 内联 | `/~ping` | 30 天 0 次 |
| P5 | wdsj 趋势图 | `services/wdsj_tracker.py:292` | 内联 | `/~wdsj trend` | 30 天 0 次 |
| P5 | 单模式战绩卡 | `modules/wdsj.py:81` | `wdsj_card.html` | — | **疑似死代码**：`cmd_wdsj` 单模式发的是官方图，不走这里，先确认再决定删或迁 |

## 三、迁移规范（每张卡都照这个来）

1. **新增 `services/xxx_card_pillow.py`**，复用 `services/card_base.py` 的绘图原语，
   不要另起一套坐标系/颜色。
2. **加测试项开关**（`modules/features.py`，复用 `pillow_card` 或新增 `pillow_<name>`），
   并在代码里做成 **Pillow 优先、异常自动回退 Chromium** —— 与 `wdsj`/`go` 现有写法一致，
   绝不让渲染单点失败导致功能不可用。
3. **Pillow 是同步 CPU 调用**，必须 `await loop.run_in_executor(None, lambda: draw(...))`，
   否则阻塞事件循环（现有实现都这么写的，照抄）。
4. **视觉验收**：同一份数据分别用 HTML 和 Pillow 渲染，逐像素对比 + 肉眼确认
   （`wdsj_card_pillow` 的注释记录了"平均像素差 2.5%"这种验收口径）。
5. **改完必须实测**：`scripts/_probe_browser_lifecycle.py` 那类探针看进程/内存，
   加上真实触发一次命令看输出。

## 三·五、M1 日报迁移：已完成的准备工作（2026-09-28）

### 1. 数据与 HTML 解耦（已提交，已验证）
新增 `services/daily_report_data.py`：`build_payload()` 只算数据，`payload_to_html()` 只填模板。
`modules/stats.py` 原来的「算数据 + 拼 HTML」耦合被打散，**两条渲染路从此吃同一份载荷**——
这是 1:1 对比的前提，否则像素差里会混进数据差异，根本没法定位。

验证方式（`tests/_test_v2368_daily_payload.py`，13 passed）：
在改 `stats.py` **之前**，用 `scripts/_capture_daily_html.py` 把 `render_card_to_image` 换成记录桩，
直接调用**原** `generate_daily_report_image()` 抓下它真实产出的 HTML，连同输入 stats 一起存进
`tests/fixtures/daily_html_golden.json`。新载荷层产出的 HTML 与之**逐字节一致**
（night 13563 字符 / morning 13560 字符）——"原始算法"不用手抄，杜绝抄错。

### 2. emoji 方案（已解决）
- 实测：**Chromium 能把 📊🥇🥈🥉🗣️🤿😴🌙☀️ 全渲染成彩色**（它通过 snap 的 content snap
  看得到 NotoColorEmoji）；但 Pillow 对 CBDT 位图字体**只接受 109px**，Apple 版字体这版
  Pillow 完全打不开。
- 又踩一个：想用 Chromium 抽图，但 `_screenshot_html` 存的是 **JPEG（无 alpha）**，
  抽出来必然是白底（in bbox = 整张画布）。
- 定案：用 **Pillow + NotoColorEmoji@109 → 裁包围盒 → LANCZOS 缩到目标字号**，
  一次性生成 9 个 PNG 存 `data/web_assets/daily_icons/`（共 19KB），运行时走 `paste_icon` 贴图。
  字型与 Chromium 同源（同一字体），且不必把 11MB 字体塞进仓库。
  生成器：`scripts/_gen_daily_emoji_assets.py`（改字号重跑即可）。

### 3. 金标与实测几何（对齐用）
`scripts/_probe_daily_ab.py` 会把固定载荷同时喂给两条路并输出数值对比；
`scripts/_probe_daily_anchors.py` 按**特征色**定位元素，产出渲染器要对齐的硬坐标。
固定载荷（9 人 / 4 条锐评）下 Chromium 金标 **720×962**，实测锚点：

| 元素 | 实测 |
|------|------|
| 卡片左/上 | x=20 / y=24（wrap padding 24px 20px 30px） |
| 头部底边 | y=121（头部内容高 54.6 = 标题 20px×1.6 + 5 + 副标题 11px×1.6） |
| 摘要区底边 | y=195 |
| 区块竖条（3 个） | y 216..227 / 589..600 / 698..709 |
| 排行条 | 首条 y254，**行距实测 ≈39.9**（非 CSS 推出的 37.8，以实测为准） |
| 24h 热力格 | y 616..639（高 24） |
| 锐评条目 | y 737 / 774 / 811 / 848，**行距 37** |
| 页脚顶边 | y 883，内容末行 ≈931 |

> ⚠️ CSS 盒模型推出的行距（37.8）与实测（39.9）不一致 —— 说明 mono 字体在无头 Chromium 里的
> 行盒比 `font-size × line-height` 更高。**以实测锚点为准**，别硬套 CSS 算式。

### 4. 渲染器首版（`services/daily_report_pillow.py`，**尚未接入 stats.py**）

用 `scripts/_probe_daily_ab.py` 迭代（同一固定载荷，Chromium 金标 vs Pillow）：

| 轮次 | 平均像素差 | 修掉的问题 |
|------|-----------|-----------|
| 首版 | 20.62 | —— |
| ① | 9.83 | **Y() 多加了一次卡片偏移**（锚点表是绝对坐标）→ 整卡下移 24px；高度差 1px |
| ② | 9.73 | 等宽字体错用 Monocraft → 换 **DejaVu Sans Mono**（`fc-match monospace` 实测值） |
| ③ | 9.43 | radial-gradient 的 `ellipse 80% 55%` 是**半径**不是直径（第一版除了 2）；锐评字号 12.5 不是 13 |
| ④ | 9.08 | conic-gradient 两层颜色写成一色 + 角度错 → 背景差 5.14 → **2.77** |

独立标定（`scripts/_probe_daily_bg.py`，只渲染 body 背景、不放卡片）：
背景平均差 **2.77**、`>32` 像素 **0%** —— 说明背景已基本对齐，弥散误差不再是主因。

#### 剩余误差在哪（下一步的靶子）
条带差显示集中在：

| 条带 | 差值 | 初判 |
|------|------|------|
| y 769-865（锐评 2-3 条） | **14.92** | 文字区；x60-360 误差 27-30，x420+ 仅 6 → 纯文字/emoji 定位问题 |
| y 96-192（摘要区） | 11.94 | 数字/标签的字号与纵向位置（sum-num 24px weight 800、sum-lbl 9.5px） |
| y 673-769（区块3+锐评1） | 9.72 | 同上 |
| y 0-96（头部） | 8.36 | logo 高光/渐变标题/药丸 |

#### 已知未对齐项（按预期收益排序）
1. **锐评行内的 emoji 与文字基线**：emoji 走贴图，其 advance 与浏览器不同 → 整行文字横向漂移。
   需要实测 emoji 在行内的实际起止 x，而不是按"13px 宽 + 4px 间隔"估。
2. **卡片内容的纵向基线**：目前用「行盒中心」近似，浏览器是基线对齐，逐元素差 1-3px。
3. **`sec-cn` 的字重**：CSS `font-weight:600`，我用的是 Bold(700)，字面更粗。
4. **`.rbar` 的 box-shadow 辉光**未画；`.logo` 的 radial 高光用了等效提亮近似。
5. **backdrop-filter 的 saturate(1.4)** 未实现（只做了 blur）。
6. **conic 角度**目前是实测试出来的，不是推导值（推导会让背景差从 2.77 涨到 3.46）——
   要精调就写个角度拟合循环搜一遍。

> 接入 `stats.py` 的**前提**是把平均像素差压到可接受区间（wdsj 卡的历史口径是 ~2.5%）。
> 现在 9.08/255 ≈ 3.6%，还差一截，所以**暂不接入**，生产日报继续走 Chromium，功能不受影响。

## 四、里程碑

- [ ] **M1 日报迁 Pillow**（P1，砍掉 77% 的 Chromium 用量）
- [ ] **M2 五子棋棋盘迁 Pillow**（P2，每步交互可感）
- [ ] **M3 更新日志卡迁 Pillow**（P3）+ 顺带统一 wdsj 日榜的两条渲染路径（P4）
- [ ] **M4 剩余低频卡迁 Pillow**（P5 全部）+ 确认 `modules/wdsj.py` 是否死代码
- [ ] **M5 去掉 Chromium 依赖**：M4 完成后 `_ensure_browser()` 理论上只剩兜底，
      届时可评估是否彻底移除 playwright 依赖（先观察一段时间再动）

## 五、注意事项（踩过的坑）

- ⚠️ **服务器上 `modules/changelog.py` 有未进 git 的热修**（手工拼 `file:///` →
  `services.sender.build_local_image_cq()`）。覆盖前一律先
  `git show HEAD:<path> | diff - <(ssh ... 'cat /root/bot/<path>')`。
- ⚠️ 该文件**原先没有模块级 `logger`**（只在各函数内部 `get_logger`），
  新增模块级函数要自己取 logger，否则 `NameError` 会连锁炸在 except 分支里。
- ⚠️ Windows 上生成要上传的 py 文件必须 `newline="\n"`，否则 `write_text` 把整文件翻成 CRLF。
- ⚠️ 服务器 Python 是 **3.10**：改完先 `python3 -m py_compile` 再重启。
