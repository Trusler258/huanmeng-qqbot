# 幻梦面板 · AI 助手项目知识库

> 用途：给面板悬浮助手（panel/routers/assistant.py）提供**准确的项目事实**。
> 铁律：这里写的每个事实都必须能在代码/配置里验证。凭印象写 = 助手编造答案。
> 维护：改了面板能力/文件位置后同步更新本文件（单一来源）。

## 一、版本号在哪（高频问题，曾答错）

| 想改什么 | 真实位置 | 面板能改吗 |
|---|---|---|
| **bot 版本号** | `config/version.toml` 的 `[version] current` | ✅ 能，配置编辑 → version.toml |
| 面板 API 版本 | `panel/config.py` 的 `PANEL_API_VERSION` | ❌ 改代码，得 SSH |
| 面板前端版本 | `panel_web/package.json` | ❌ 改代码 |

- 机器人自报的版本、`/~changelog`、提示词里的 `${version}` **全部**读 `config/version.toml`
- 更新日志在 `data/update_log.md`（新版本在最上面），可以改
- **绝对不要**说"版本号在 pyproject.toml / package.json / __init__.py"——本项目不是那种结构

## 二、配置文件都在 config/ 目录（面板「配置编辑」页可改）

| 文件 | 管什么 |
|---|---|
| `bot_config.toml` | **主配置**：人格（personality）、模型（model.*）、判断（judge） |
| `lang.toml` | 所有指令的提示文案 |
| `roles.toml` | 管理员（admin_qq）、操作员（op_qqs）、朋友、群主、QQ 昵称映射 |
| `adapter_config.toml` | NapCat 连接（napcat_server.*）、群列表、群设置 |
| `version.toml` | 版本号 |
| `.env` | 密钥（DEEPSEEK_KEY 等），面板只看填没填，值不可见 |

## 三、数据都存在 data/ 目录（各页面管理）

| 数据 | 文件/位置 | 面板页面 |
|---|---|---|
| 好感度 | `data/fav/*.json` | 社交 |
| 幸运值 | `data/luck.json`（按日期分层） | 社交（幸运值 tab） |
| 积分/库存 | `data/economy.json` | 经济 |
| 用户画像 | `data/user_profiles.json` | 社交 |
| 长期记忆 | `data/memory/<群号>.md` | 记忆 |
| 笔记本 | `data/notes/<chat_id>.md` | 记忆 |
| 群长期记忆 | `data/group_memory/` | 群管理 |
| bot 发出的消息 | `data/msglog/msglog_<chat_id>.jsonl` | 消息 |
| 实验开关 | `data/features.json` | 实验特性 |
| 许可码 | `data/licenses.json` | 实验特性 |
| 表情库图片 | `data/faces/` | 图片 |
| 运行日志 | `logs/huanmeng.log` | 日志 |
| 提示词章节 | `data/skills/*.md` | 提示词 |

## 四、面板每个页面「能干什么」（照着说，别编）

- **概览** `/dashboard/overview`：运行数据总览、趋势图
- **配置编辑** `/config/editor`：改上面 config/ 里所有 toml + 看 .env 填写状态；
  支持表单模式（按 section 分组，可搜索键名）和源码模式
- **系统状态** `/monitor/system`：bot 服务状态、重启、**服务与自愈**（崩溃自愈的布防状态：
  改配置把 bot 改崩了会自动回滚并救活）、进程/内存/端口
- **日志** `/monitor/logs`：实时日志（WebSocket 跟随）+ 关键字/级别过滤 + **导出**
- **消息** `/data/messages`：消息记录全文检索（SQLite FTS5）
- **记忆** `/data/memory`：长期记忆文件、笔记本、自认知文档、skills
- **图片** `/resources/media`：表情库/生图产物/临时图/撤回留图，删除 = 移入回收站
- **数据库** `/resources/database`：表结构、分页浏览、全文检索、自定义 SQL、VACUUM
- **群管理** `/resources/groups`：38 个群一览、成员发言统计、写群笔记/群记忆
- **提示词** `/resources/prompts`：系统提示词章节、skills 文件编辑
- **社交** `/fun/social`：好感度增删改、用户画像、**幸运值**（按日期查/设/删）
- **经济** `/fun/economy`：积分、库存
- **游戏** `/fun/games`：五子棋/数学/倒数/抽奖数据
- **地震** `/fun/earthquake`：群订阅、震级阈值、推送记录
- **插件** `/fun/plugins`：插件目录、.hmp 包管理、能力注册表、事件总线
- **指令** `/fun/commands`：指令清单 + LLM 可见性审计（哪些指令 LLM 不知道，是排查"bot 不会用某功能"的地方）
- **实验特性** `/fun/features`：实验开关 + **许可码生成**（/~key 授权）

## 五、bot 侧的关键概念（解释用）

- **指令**：QQ 里发 `/~xxx` 触发；也由 LLM 通过 JSON 的 `calls` 字段自动调用
- **%~/**：`/~key` 是实验开关指令，`/~help` 看指令清单
- **许可码**：面板「实验特性」页生成；用户在 QQ 发 `/~key 兑换 <码>` 兑换；
  三档 = 一次性（1 次操作）/ 一天（24h 不限次）/ 无期限。管理员免码
- **实验开关**：`face_inline`（逐句配图，默认开）、`sweet_style`（私聊贴贴，默认关）；
  改动需重启 bot.service 才完全生效
- **表情包**：**群聊不发图片表情**（靠 action 括号动作表达情绪）；私聊可逐句配图
- **好感度 fav**：-100~100，初始 50；对朋友/群友按群独立存储
- **崩溃自愈**：改配置导致 bot 起不来时，panel 会自动回滚配置并重启救活（布防 10 分钟）

## 六、常被问到的操作怎么做

- 改机器人性格 → 配置编辑 → bot_config.toml → `personality.personality_core`
- 改模型 → 配置编辑 → bot_config.toml → `model.replyer_1.name`
- 加/减管理员 → 配置编辑 → roles.toml → `admin_qq`
- 改 NapCat 地址 → 配置编辑 → adapter_config.toml → `napcat_server.host` / `port`
- 看 bot 报错 → 日志页，级别选 ERROR
- bot 不回消息 → 先看系统状态（服务活着吗）→ 再看日志（有异常吗）→ 查 NapCat 连接配置
- 想让 bot 学会某指令 → 指令页看「LLM 可见性审计」，缺说明的指令不会用
- 改版本号 → 配置编辑 → version.toml（回复时带 `[[cfg:version.toml|version.current]]` 定位标记，让主人一键跳过去）
- 给某人开 /~key 权限 → 实验特性页生成许可码 → 把码发给对方

## 七、做不到的事（明确说，别硬编）

- 改代码文件（panel/、modules/、services/ 下的 .py）→ 得 SSH 上服务器
- 直接给 QQ 用户发消息（面板只读数据，不代发）
- 改 `data/luck.json`（幸运值）→ 社交页「幸运值」tab，填 QQ+值点保存；也可让助手下 `fill` 动作预填（带 `qq`/`value`）
