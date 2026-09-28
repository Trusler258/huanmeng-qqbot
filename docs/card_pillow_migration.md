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
