# 幻梦后台控制面板 · 方案设计

> 版本：v1.0（2026-09-12）
> 目标读者：开发者自己 + 未来接手的人
> 本文是**定稿文档**，实现按此执行；实现后如与文档不符，改文档而非默默偏移。

---

## 0. 一句话

给幻梦 Bot 做一个**独立进程的 Web 控制面板**，前后端分离，能看能管能调，
经 Cloudflare Tunnel + nginx BasicAuth + 应用层 Token 三重门禁对外，绑内网不可直达。

---

## 1. 为什么要独立进程（架构硬约束）

`main.py` 的入口是这样：

```python
async def _amain():
    await bot.initialize()
    await bot.run()     # ← 永久 await，永不返回
```

`bot.run()` 是 WS 主循环，退出 = Bot 掉线。因此：

- **面板绝不允许挂在 Bot 的 event loop 里**。一旦面板的某个请求把 loop 卡住（一个大文件读、
  一次慢 SQL），全群消息全部延迟 —— 这个代价不可接受。
- 反过来也成立：Bot 重启/崩溃时，面板还能活着看现场日志、看错误、重启服务。
  **"出事时能用的面板"才是真面板。**

结论：`panel.service` 是与 `bot.service` **完全平级的独立 systemd 服务**。

---

## 2. 技术选型（定稿）

### 2.1 后端：FastAPI + uvicorn

| 候选 | 结论 | 理由 |
|---|---|---|
| FastAPI + uvicorn | **选用** | 自动 OpenAPI → 前端 TS client 可代码生成；async 天然匹配现有全异步代码；Pydantic 校验省手写 |
| Flask | 否 | 同步框架，读 SQLite/文件会阻塞；无 OpenAPI |
| 直接写 asyncio HTTP | 否 | `log_server.py` 那套是 411 行手写 HTTP，够用但写 20+ 接口会疯 |
| 挂进 Bot 进程 | 否 | 见第 1 节 |

- 端口 **59300**（避开 58888-58892 现有段）
- 绑 **127.0.0.1**，不监听 0.0.0.0
- 依赖：`fastapi` / `uvicorn` / `pydantic` / `pyjwt`（本机实测 fastapi 0.141.1 + uvicorn 0.52.1 已装）

### 2.2 前端：vue-pure-admin 起手

| 候选 | Node 要求 | 结论 |
|---|---|---|
| **vue-pure-admin** | 常规（服务器 v20 可用） | **选用**：Vue3 + Vite + Element-Plus + TS + Pinia + Tailwind，中文文档，MIT，开箱即用，桌面/移动双适配 |
| vue-vben-admin 5.0 | **要求 Node v24** | 否：服务器 node v20.20.2，且是 Turborepo monorepo，构建更重 |
| AstrBot dashboard | — | 仅作**架构参考**（FastAPI + openapi-ts 自动生成 client 的思路照抄） |

选它的核心理由（用户三条约束对照）：
- **好看**：Element-Plus + Tailwind 现成组件与暗色主题，不用从零调 CSS
- **好用**：布局/路由/权限/标签页/面包屑/搜索菜单/暗色切换全部内置
- **安全**：内置前端路由权限 + 按钮级权限指令，与后端 RBAC 对齐成本低

### 2.3 前后端契约：OpenAPI 自动生成

后端 FastAPI 天然输出 `/openapi.json`，前端用 `@hey-api/openapi-ts` 生成
`src/api/generated/` —— **接口改了前端立刻类型报错**，杜绝"后端改了字段前端还在传旧名"。

---

## 3. 目录结构

```
qqbot/
├── main.py                  # Bot 入口（不动）
├── panel/                   # ★ 新增：后端
│   ├── __init__.py
│   ├── app.py               # FastAPI 实例 + 路由挂载 + 启动
│   ├── config.py            # 面板自身配置（密钥/端口/数据根目录）
│   ├── auth.py              # Token 签发/校验 + 依赖注入
│   ├── audit.py             # 写操作审计日志
│   ├── deps.py              # 共享依赖（当前用户、数据根）
│   ├── routers/
│   │   ├── auth.py          # 登录/登出/改密/会话
│   │   ├── overview.py      # 概览仪表盘
│   │   ├── economy.py       # 经济系统
│   │   ├── social.py        # 好感度 / 用户画像
│   │   ├── memory.py        # 笔记 / 短期记忆 / 长期记忆
│   │   ├── messages.py      # 消息检索 / msglog
│   │   ├── logs.py          # 实时日志（复用 log_server 推送）
│   │   ├── features.py      # 实验开关
│   │   ├── commands.py      # 指令清单（复用 help_card.collect_commands）
│   │   ├── games.py         # 五子棋/洛花星雨战绩
│   │   ├── earthquake.py    # 地震订阅管理
│   │   ├── system.py        # 服务状态 / 重启 / 端口
│   │   └── plugins.py       # 插件管理
│   └── schemas/             # Pydantic 模型
├── panel_web/               # ★ 新增：前端（vue-pure-admin）
└── docs/panel_plan.md       # 本文
```

**目录隔离原则**：`panel/` 只读现有模块，不改 `core/` `modules/` 的任何运行逻辑。
唯一允许的例外是**复用函数**（如 `help_card.collect_commands()`），只 import 不修改。

---

## 4. 功能清单（用户要求"功能一定要多"）

按左栏菜单分组，共 **12 组 / 40+ 页面**。数据源已在服务器实测确认。

### 4.1 概览 Dashboard
- Bot 在线状态 / 版本号（`config/version.toml`）/ 运行时长 / 内存占用
- 今日消息量、活跃群数、活跃用户数（`data/stats_*.json`）
- LLM 调用次数与耗时趋势、Token 消耗
- 最近 10 条错误日志
- 服务卡片：`bot` / `panel` / `napcat` / `cloudflared` 的运行态 + 一键重启

### 4.2 消息中心
| 页面 | 数据源 | 能力 |
|---|---|---|
| 消息检索 | `db/store.py`（SQLite + FTS5 trigram） | 全文搜索、按群/人/时间过滤、跳转上下文 |
| 发送记录 | `data/msglog/msglog_<chat_id>.jsonl` | Bot 实际发出的每条消息，按群浏览 |
| 撤回记录 | 撤回记录存储 | 谁撤了什么 |

### 4.3 用户与关系
| 页面 | 数据源 | 能力 |
|---|---|---|
| 好感度 | `data/fav.json`（`"g<群号>:<QQ>": 数值`） | 排行榜、单群视图、手动增减、变更历史 |
| 用户画像 | `data/user_profiles.json` | 按人浏览画像、编辑、删除 |
| 昵称管理 | 分群昵称存储 | 查看/修正分群昵称 |
| 统计报表 | `data/stats_<群号>_<日期>.json` | 每日消息量、小时分布热力图、成员排行、趋势折线 |

### 4.4 长期记忆
| 页面 | 数据源 | 能力 |
|---|---|---|
| 笔记本 | `data/notes/<chat_id>.md` | 按会话查看/编辑/删除单条、清空 |
| 短期记忆 | `data/stm/stm_<chat_id>.json` | 浏览 `[{time,tag,author,content}]`，按标签过滤 |
| 自身认知 | `data/self_knowledge.md` | 在线预览 5 章节 + 关键词触发预览（能查"问什么会注入哪些章节"） |
| 技能文件 | `data/skills/*.md` | 查看/编辑叠加提示词 |

### 4.5 实验开关
- `data/features.json` 的 `face_inline` / `sweet_style` 等
- 显示每项：当前值、默认值、说明、影响的代码路径
- **一键回滚**（写回默认值）、改动审计
- 提示：面板只改 JSON，是否需要重启由页面明确标注

### 4.6 指令中心
- 全量指令表：复用 `help_card.collect_commands()`（**与 LLM 清单同源**，v2.1.20 铁律）
- 按权限分层（owner / admin / 普通）分组
- 每条显示：触发词、说明、参数、是否需要管理员
- **LLM 可见性检查**：标记"已注册但 LLM 清单里没有"的指令（防止再出 v2.1.20 那类黑洞）
- 语言包 `lang.toml` 在线编辑（高风险，二次确认）

### 4.7 实时日志
- 复用 `core/log_server.py` 的 broadcast 思路，前端用 WebSocket
- 分级过滤（DEBUG/INFO/WARNING/ERROR）、来源过滤、关键词高亮、自动滚动开关
- 支持下载最近 N 行 / 按时间段导出

### 4.8 经济系统
- `data/economy.json`：总积分池、排行、库存、签到记录
- 手动发/扣积分、发物品、清空某人
- 操作全部进审计日志

### 4.9 游戏与娱乐
| 页面 | 数据源 |
|---|---|
| 五子棋战绩 | `data/wzq_results.json` |
| 洛花星雨战绩 | `data/wdsj_history.json` / `wdsj_player_name.json` |
| 抽奖记录 | `/~抽` 相关存储 |
| 倒计时 | `data/countdown.json` |
| 搜索缓存 | `data/search_cache.json`（查看/清理） |

### 4.10 地震模块
- 订阅群列表 + 各省订阅 + 震级阈值（在线调整）
- 最近推送记录（含二报比较结果）
- 手动触发一次拉取测试

### 4.11 系统运维
| 页面 | 能力 |
|---|---|
| 服务管理 | `systemctl status/is-active/restart` bot、panel、napcat、cloudflared |
| 进程端口 | `ss -lntp` 解析，端口占用一览 |
| 文件浏览 | 限定在 `/root/bot` 内，只读下载 + 白名单编辑 |
| 更新日志 | 渲染 `data/update_log.md`，倒序，卡片样式 |
| 配置浏览 | `config/*.toml` 脱敏预览（密钥打码） |

### 4.12 插件管理
- `plugins/` 与 `modules_private/` 的插件清单、启用状态
- `.hmp` 插件：查看 manifest、安装、卸载（复用 `modules/plugin_share.py`）
- 插件注册的指令 / 工具 / 事件订阅一览（`core/capability/`）

---

## 5. 安全方案（用户要求"一定要安全"）

**四层防御，任何一层单独被绕过都不足以拿到东西。**

### L1 · 网络层
- `panel.service` 只绑 `127.0.0.1:59300`，公网/内网**直连不可达**
- 不开放新公网端口

### L2 · 隧道层
- 走现有 Cloudflare Tunnel（`huanmeng-tunnel`），新增 ingress：
  ```yaml
  - hostname: bot.truslerweb.dpdns.org
    path: /panel*
    service: http://127.0.0.1:49300   # nginx BasicAuth 反代 → panel:59300
  ```
- nginx 做反向代理 + **BasicAuth**（与 `/qqbot*` `/kookbot*` 同一套 passwd 文件的管理方式）
- 好处：即使应用层有 0day，攻击者还要先过 BasicAuth

### L3 · 应用层
- **独立密码**，与 BasicAuth 密码不同，`bcrypt` 存哈希（不存明文）
- 登录成功签发 **JWT**（HS256），有效期 12h，密钥存 `panel/config.secret.toml`
  （`chmod 600`，**加入 `.gitignore`**，绝不进 git）
- 失败限速：同 IP 5 次失败锁 5 分钟（内存计数 + 审计）
- 所有 `/api/*` 路由默认要求 token，白名单仅 `/api/auth/login`
- Token 校验用 FastAPI `Depends`，**默认拒绝**（deny by default），漏加依赖也不放行

### L4 · 操作层
- **写操作全部记审计**：`panel/audit.log`（JSONL）——时间、用户、IP、动作、目标、旧值→新值
- **危险操作二次确认**：重启服务、删除笔记、清空经济、改提示词 —— 前端弹窗要求输入确认词
- **只读优先**：能看的一律只读；写接口单独标注
- **配置脱敏**：读 `*.toml` / `.env` 时正则打码 `sk-*` / `token=` / `password=` / QQ 邮箱授权码
- **路径逃逸防护**：文件浏览接口对路径做 `resolve()` + `is_relative_to(数据根)` 校验，
  拒绝 `..`、绝对路径、软链接跳出

### 5.1 与 Bot 的关系
- 面板**只读文件**，不 import `bot.py`，不建立 NapCat WS 连接
- 需要主动发消息/执行指令时，走"落一个任务文件 → Bot 轮询"或用现有 `v1api`，
  **不允许面板直接抢占 WS 连接**
- 面板改完 `features.json` 等配置，是否需要重启**在 UI 上明说**，不做隐式重启

---

## 6. 部署方案

### 6.1 服务单元 `/etc/systemd/system/panel.service`

```ini
[Unit]
Description=Huanmeng Panel API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/bot
EnvironmentFile=/root/bot/.env
ExecStart=/root/bot/venv/bin/python -m uvicorn panel.app:app --host 127.0.0.1 --port 59300
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- 与 `bot.service` 平级，互不影响
- 前端 `panel_web` 构建产物 `dist/` 由 nginx 直接托管静态文件（不占 Python 进程）

### 6.2 部署工作流（沿用现有铁律）

1. 本地改代码 → 用系统 Python `C:\Users\Huang\AppData\Local\Programs\Python\Python312\python.exe` 跑测试
2. **改/覆盖任何服务器文件前先备份**（`cp 原文件 原文件.bak_YYYYMMDD`，本地备份到 `G:\py\qqbot-backup\YYYY-MM-DD\`）
3. scp 逐文件指定完整目标路径
4. `systemctl restart panel.service` → 查 `journalctl -u panel.service -n 50`
5. 写 `data/update_log.md`（**新条目插到文件头部**，倒序约定）

### 6.3 内存注意
服务器 available 内存仅 **2.8G**。Vite 构建可能 OOM：
- 方案 A：**本地构建**，只把 `dist/` 上传服务器（推荐，服务器零构建压力）
- 方案 B：服务器构建前设 `NODE_OPTIONS=--max-old-space-size=2048`

---

## 7. 分阶段实施

| 阶段 | 内容 | 产出 |
|---|---|---|
| **P0** | 方案定稿（本文） | `docs/panel_plan.md` |
| **P1** | 后端骨架 | `panel/`：FastAPI + 认证 + 概览 + 日志接口，本地可 curl 通 |
| **P2** | 后端全量接口 | 12 组路由全部实现，OpenAPI 完整 |
| **P3** | 前端起手 | `panel_web/` 由 vue-pure-admin 起手，登录 + 概览 + 日志三页跑通 |
| **P4** | 前端全量页面 | 按第 4 节清单铺完 |
| **P5** | 部署上线 | `panel.service` + nginx + Tunnel 接入，全链路验证 |
| **P6** | 打磨 | 暗色主题、动效、移动端适配、空状态/骨架屏 |

每阶段独立可用、独立可回滚。

---

## 8. 明确不做

- ❌ 不做"面板内直接开 NapCat WS 连接发消息"—— 会与 Bot 抢连接
- ❌ 不做多用户/注册系统 —— 单管理员足够，减少攻击面
- ❌ 不做隐式重启 Bot —— 重启是危险操作，必须显式点且二次确认
- ❌ 不在面板里存明文密钥
- ❌ 不把面板挂进 Bot 进程

---

## 9. 验收标准（对照用户四条约束）

| 约束 | 验收方式 |
|---|---|
| **好看** | 暗色科技风 + 卡片式布局 + 图表可视化；无原生丑陋控件；移动端可用 |
| **安全** | ① 公网直连 59300 不通 ② 无 token 访问 `/api/*` 全 401 ③ 路径逃逸测试全拒 ④ 配置接口不回显密钥 ⑤ 审计日志有记录 |
| **好用** | 常用操作 ≤3 次点击；列表支持搜索/排序/导出；错误有明确提示；不强制刷新 |
| **功能多** | 第 4 节 12 组 40+ 页面全部可访问且数据真实 |
