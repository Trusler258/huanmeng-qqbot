# 更新日志

## v1.4.2 — 自然对话重构 + OP权限分层 + 模式系统 --2026.7.15

### 🗣️ 自然对话重构
- **禁止回溯旧上下文**：只回应当前消息，不翻10分钟前的截图/聊天记录当素材
- **禁止元对话**：不说"被点名了""翻了一下记录""看到你们在聊XX"这类播报员式总结
- **水群语气**：像QQ群闲聊的女生，可偶尔用"草""笑死""确实""好家伙"等口语
- **内向OS**：可偶尔在回复中夹杂括号内心独白，如"(其实我也有点困了)"，比嘴上诚实
- **CALL 指令全面收紧**：只在用户明确说"帮我搜/查/找/介绍一下"时才调用工具。`tools.py` 的 `TOOL_DEFINITIONS` 移除"系统自动检测调用"矛盾指令

### 🔫 OP 权限分层（admin > op > user）
- `/~op add/del <QQ>` — 添加/移除 OP 次级管理员
- `/~op group set <群号> <OP_QQ>` — 群主权限指派给 OP（OP 在该群内等于 admin）
- `/~op group del <群号>` — 撤销指派，恢复 admin
- `/~op group list` — 查看所有 OP 和群指派
- OP 在指派群内自动获得 `[admin]` 角色标签，可用全部 CALL 指令

### 👤 私聊增强
- `/~persona <人格描述>` — 私聊切换 AI 人格，完全替换 system prompt
- `/~persona reset/show` — 恢复默认/查看当前人格
- `/~主人` — 将当前私聊对象设为主人，获得 admin 级权限

### 😴 模式系统
- `/~sleep` — 睡觉模式：全部回复用第三人称括号叙述，如"(迷迷糊糊翻了个身)"，10字内迷糊呓语
- `/~含蓄` / `/~叙事` — 含蓄叙述模式：日常可用，第三人称括号内心独白 ，如"(默默记下了这件事)"
- 三种模式互斥（普通/含蓄/睡觉），无论哪种都作用于群聊+私聊+戳一戳

### 🐛 关键修复
- **NapCat 缺 post_type**：`dispatcher` + `message_parser` 兼容缺少 `post_type` 字段的消息（有 `message_type` 就当作 message 事件）
- **cmd_whitelist=None 崩溃**：`grp_cmds` 为 None 时 `in` 操作抛 TypeError，改为 `if not grp_cmds: pass`
- **dispatch 异常捕获**：`_dispatch_inner` try/except + 完整 traceback 日志
- **scp 丢行连锁崩溃**：`recall.py`/`commands.py`/`judge.py` 三处语法错误导致全体消息丢失 ~100 分钟，修复并加强部署验证
- **分群昵称**：`get_display_name(uid, gid)` 优先读分群 `nicknames`，fallback 全局 `qq_name_map`
- **@优先回复**：当 @机器人 + CALL 指令时，丢弃 CALL 前的闲聊句子，直接回复指令结果

### 🌐 开源版同步
- GitHub `huanmeng-qqbot` 同步 v1.4.1 全部 42 文件（+4146/-742 行）
- README 版本 v1.1→v1.4.1，LICENSE 年份 2024→2026
- `wdsj_cache.py` 从开源版移除，`commands.py` 加 ImportError fallback
- 服务器 SSH key 配置 GitHub push（443 被墙，走 22 端口）

### 🔧 戳一戳
- 禁止重复"摸头很舒服"句式，每次随机情绪（疑惑/开心/害羞/吓一跳/嫌弃/淡定）
- 睡觉/含蓄模式下戳一戳也切换为括号第三人称叙述

### 🔮 文件变更
| 文件 | 变更 |
|------|------|
| `data/main_skill.md` | 规则 10~14：禁止回溯/元对话/水群语气/内向OS；规则 0 扩展：CALL 全局收紧 |
| `modules/op.py` | **新建**：OP 权限系统 + 私聊人格/主人 + 睡觉/含蓄模式 |
| `core/config.py` | `op_qqs` + `group_owners` 字段；`get_user_tag(gid)` 支持分群 OP；`get_display_name(uid,gid)` 分群昵称 |
| `modules/commands.py` | 注册 `op/persona/主人/sleep/含蓄` 指令；wdsj_cache fallback |
| `core/pipeline.py` | @优先丢弃闲聊 / 分群昵称 / 模式注入 / 私聊人格覆盖 / 私聊主人 / 戳一戳多样化 |
| `core/dispatcher.py` | post_type 缺失兼容 / cmd_whitelist None 防御 / try/except + traceback |
| `utils/message_parser.py` | `_is_message_event` 兼容无 post_type（有 message_type 即当消息） |
| `core/tools.py` | `TOOL_DEFINITIONS` 移除"系统自动检测调用"→ 明确仅在用户要求时执行 |
| `data/templates/*` | 1.4.1 同步至开源版 |
| `bot.py` / `main.py` | VERSION 1.4.1 → 1.4.2 |



### 🗣️ 回复质量控制
- **句数压缩**：群聊 1~5 → 1~3 句；短消息（喵/嗯/好）→ 1 句就够了
- **戳一戳精简**：token 400→150 + 注入"只用 1 句短回应"
- **禁止反复晚安**：说了晚安就别再提睡觉，翻来覆去显得假
- **禁止假设作息**：不说"明天上课/上班"，不知道对方放没放假
- **上下文不合并**：多人发言不再挤在一条 user 消息里，每条独立让 LLM 看清
- **私聊角色**：`[admin]` 统一叫主人，`[friend]` 只用你我，禁止精分切换
- **群聊 @ 功能**：注入可 @ 用户列表，LLM 可用 `[CQ:at,qq=QQ号]` @ 人

### ✍️ 全量消息录制 & 撤回
- `send_sentences` 用 `call_api` 获取真实 message_id → 撤回 bot 消息能匹配原文
- `send_group_msg` / `send_private_msg` 底层自动录 bot 消息到 msglog
- msglog 统一 JSONL，`type="bot"` 一键过滤所有 bot 回复，用于排查质量
- `mark_recalled` 跳过 msg_id=0 的 bot 消息，防止多条 bot 消息互相误中撤回

### 🧠 msglog 记忆回溯
- `search_msglog()` 从最近 300 条消息中关键词匹配，注入 extra_info
- 触发条件：消息 ≥6 字（短词不触发），用户消息优先，bot 消息限 2 条
- 效果：LLM 能看到上下文窗口之外的聊天记录，不会重复回答或遗漏上下文

### 🐛 关键修复
- **CALL 系统**：正则 `~` 可选 / 清洗 HTML 碎片(`admin">`) / COMMAND_MAP 校验 / wdsj 参数校验 / 错误结果标记 `[CALL错误]` / follow-up LLM 诚实报错
- **LLM 伪装 CALL**：剥离写入上下文的 `[系统] 已调用:` 后缀，防止 LLM 从历史学会伪造
- **好感度缺失**：格式提醒追加 `[fav: ...]` 要求，防止 LLM 忘记加好感度
- **fav 越界**：`data_set` 对 fav 类型 clamp 0~100；服务器回正 Trusler 的 114514
- **fav key**：`data get/set/del` 对 fav 自动纠正纯数字 key → `g{chat}:{user}`
- **更新日志模板**：删除重复 `id="changelog-body"` + `<li>` 包裹 `<ul>` + 行内粗体代码转换
- **日报升级**：HTML 卡片替代纯文本，排行条+24h 热力图+幻梦锐评

### 🌐 服务器
- **NTP 自动校准**：`crontab` 每天 23:58 `ntpdate -s ntp.aliyun.com`

### 🔮 文件变更
| 文件 | 变更 |
|------|------|
| `core/pipeline.py` | CALL 大修 / 上下文标签剥离 / 句数+戳一戳 / 时间注入 / msglog回溯 / @列表 |
| `services/llm.py` | 格式提醒 `[fav]` / 上下文不合并 |
| `services/sender.py` | `_send_and_record` + `_log_bot_sent` 全量录制 |
| `modules/memory.py` | **新增** `search_msglog` msglog 回溯检索 |
| `modules/recall.py` | `mark_recalled` 跳过 msg_id=0 |
| `modules/admin.py` | fav `data_set` clamp 0~100 |
| `modules/commands.py` | fav key 自动纠正 / wzq duel 修复 / unduel+admin clear |
| `modules/stats.py` | `generate_daily_report_image` HTML 日报 / `record_message` +sender_name |
| `modules/changelog.py` | `_md_to_html` `<ul>`包裹 + `_inline_fmt` 行内格式 |
| `data/main_skill.md` | 句数 5→3 / 短消息规则 / 禁晚安 / 禁假设作息 / 私聊角色 / @规则 / wdsj文档 |
| `data/templates/daily_report.html` | **新建**：日报 HTML 卡片 |
| `data/templates/changelog_card.html` | 修复重复 `changelog-body` |
| `data/update_log.md` | v1.4.1 条目 |
| `core/dispatcher.py` | `record_message` +sender_name |
| `bot.py` / `main.py` | VERSION 1.4.0 → 1.4.1 |
| 服务器 crontab | 23:58 NTP 自动校准 |


## v1.4.0 — 五子棋 AI + 引用图片 + 质控大修 --2026.7.13

### 🎮 五子棋 AI 重写
- 从 LLM 猜坐标 → **Minimax + Alpha-Beta 剪枝**评分搜索
- 模式评估：全盘四方向扫描，活四 50000 / 冲四 1000 / 活三 1000
- 候选剪枝：只搜已有棋子半径 2 格，225 格砍到 20-40 格
- 走法排序：高分先搜，α-β 大量剪枝
- 四档难度：新手(depth 1) / 普通(2) / 困难(2) / 专家(迭代 1→2→3 + 8s 超时)
- 专用线程池 `_AI_EXECUTOR`，不阻塞 Playwright 渲染
- 立即防守：对手活四/五连威胁直接堵

### 📎 引用图片自动响应
- @机器人 + 引用消息中的图片 → 自动下载识别，描述注入管道
- WDSJ 战绩图智能分流：bot 自己发的战绩图被引用时，直接返回缓存数据（不耗视觉 token）
- `wdsj_cache.py`：snapshot → 数据映射，1 小时过期

### 🐛 关键修复
- **@检测严格化**：正则 `@<bot_qq>(?!\d)`，bot 自身 @ 保持 QQ 号不替换
- **更新日志卡片**：去 CDN 依赖（fonts/marked.js/highlight.js），Python `_md_to_html()` 服务端渲染
- **图片识别**：PIL 预处理 → 转 RGB + 长边缩至 2048 + 输出 PNG，避免智谱 1210 格式错误
- **日志控制台**：`addLine()` 改为单 `<span class="msg">`，解决实时日志复制换行
- **预搜索 CALL**：搜索结果注入加 `【系统预搜索】` 标记 + 代码级拦截重复 `[CALL:~search]`
- **||| 多句分隔**：格式提醒钉在 user message 末尾，防止长 extra_info 冲掉规则
- **不可见字符清洗**：管道入口正则洗零宽/双向控制/不可见运算符，纯垃圾跳过
- **wzq duel 修复**：缺 return 掉到末尾变"未知操作"；undo 申请人覆盖；AI 图片顺序颠倒
- **wzq 增强**：/~wzq unduel 撤销挑战、/~wzq admin clear 强制结束本群棋局，持久化 `wzq_games.json`
- **CALL 系统大修**：正则 `~` 可选（防 LLM 漏写前缀静默丢失）；程序级校验指令存在性 + wdsj 参数数量；清洗 CALL 文本中的 HTML 碎片(`admin">`)和换行；命令返回错误自动捕获 → 标注 `[CALL错误]` → follow-up LLM 诚实告知失败
- **wdsj 文档修正**：main_skill.md `[CALL:~wdsj <玩家名>]` → `[CALL:~wdsj bw 玩家名]`，避免 LLM 只传一个参数
- **LLM 伪装 CALL**：剥离写入上下文的 `[系统] 已调用:` 后缀，防止 LLM 从历史学会伪造（输出假 CALL 而不实际执行）
- **好感度缺失**：user message 格式提醒追加 `[fav: ...]` 要求，防止 LLM 被大量规则淹没忘加好感度

### 🔧 功能调整
- **搜索改为手动**：关闭 `auto_search_if_needed`，只响应用户 `/~search` 和 LLM `[CALL:~search]`
- **搜索进度提示**：`perform_search` 内只在实际搜索时发 "🔍 正在搜喵~"（缓存命中不提示）
- **统计开关**：`/~unstats` 暂停 / `/~setstats` 恢复，数据文件不动
- **退群指令**：`/~leave [群号]` 收集 GH/统计/好感度 → 重置 fav → 退群，文件全保留
- **戳一戳白名单**：`/~owner wl remove` 后该群戳一戳同步屏蔽

### 🔮 文件变更
| 文件 | 变更 |
|------|------|
| `modules/wzq.py` | AI 重写：Minimax + α-β + 评估 + 走法排序 + 专用线程池 |
| `core/dispatcher.py` | @ 严格检测 / 引用图片识别 / WDSJ 缓存分流 / 戳一戳白名单 |
| `modules/changelog.py` | `_md_to_html()` 服务端渲染 / 模板去 CDN |
| `services/image_api.py` | PIL 预处理：格式统一 → PNG |
| `core/pipeline.py` | @ 严格检测 / 关自动搜索 / 不可见字符清洗 / 预搜索标记 |
| `core/log_server.py` | 日志格式统一单 span |
| `services/llm.py` | ||| 格式提醒钉到 user message |
| `services/sender.py` | echo 匹配修复 |
| `utils/username.py` | bot @ 保持 QQ 号不替换 |
| `modules/search.py` | 搜索进度提示移到 perform_search 内部 |
| `modules/stats.py` | 统计开关 `_stats_disabled` |
| `modules/commands.py` | /~unstats /~setstats /~leave / cmd_wdsj 缓存 |
| `modules/leave.py` | **新建**：退群数据收集/清理 |
| `services/wdsj_cache.py` | **新建**：WDSJ 战绩图片缓存 |



## v1.3.0 — LLM 指令调用 + 表情库 + 地震大修 + 全指令文档 --2026.7.6

### 💡 LLM 可调用指令系统 [CALL:~xxx]
- 大模型可在回复中嵌入 `[CALL:~指令 参数]` 自动调用机器人功能
- 20+ 指令可被 LLM 调用：天气、地震、搜索、画图、管理配置等
- 管道自动检测 CALL → 执行 → 过滤标签 → 在主消息末尾附加 `[系统] 已调用: /~指令`
- 私聊/群聊均可使用
- 调用结果通过 follow-up LLM 自然回应

### 😊 表情库 [FACE:关键词]
- 新增 `modules/face_lib.py`：关键词匹配本地 emoji 图片
- 支持 15 种情绪表情（开心/害羞/坏笑/疑惑/大哭等）
- LLM 在回复末尾用 `[FACE:关键词]` 匹配并发送
- 表情文件位于 `data/faces/`

### 🗺️ 地震系统全面重构
- 数据源升级：CENC + 四川/福建/重庆 省级预警，5 源并行 1s 轮询
- MD5 响应缓存 + 60s 防重推冷却
- 省映射表支持 31 省级地区多省订阅
- 地图三代进化：OSM(不可达) → SVG雷达图(兜底) → 天地图Leaflet(最终)
- 天地图 WMTS 瓦片 (vec_w + cva_w)，真实地理地名
- 缩放策略：M≥7 z5 / M≥5 z6 / 其他 z7（覆盖省级范围）
- HTML 地震卡片 → Playwright 截图 → CQ 图片发送
- EQ 监视器 WebSocket 端口 58889

### 🗣️ 私聊语气优化
- 软禁"主人"称呼（不禁止但限制频率）
- 拟声词开头 ≤10%
- 每句 12-20 字，2-6 句结构
- 文字表情 QAQ/OvO/QwQ 替代颜文字

### 📋 全指令参考文档
- `data/幻梦Bot_完整指令文档.md`：50+ 指令完整用法说明
- `/~help` 改为发送预渲染指令文档卡片（即时响应，不等待渲染）
- 文档以 `## command_reference` 注入到 LLM system prompt

### 📄 废弃指令
- `/~s`（搜索）：已从 COMMAND_MAP 移除
- `/~img` / `/~img18`（随机二次元图）：已从 COMMAND_MAP 移除
- 旧 `tools.py` 中的天气工具：已移除，由 CALL 系统接管

### 🔧 其他改进
- 判决合并：cheap judge + interest judge 合并为单次 API 调用
- 消息去重：`dispatcher.py` 新增 `message_id` 去重
- 自动搜索聚焦明确关键词，不再拦截"再查一次"
- 旧 tools.py 清理：移除 weather 调用防重复查询
- Bot 服务修复：robot.service 废弃，只保留 bot.service 单实例
- service 配置修正：ExecStart 改为 `main.py`，WorkingDirectory 改为 `/root/bot`

### 🔮 文件变更
| 文件 | 变更 |
|------|------|
| `modules/earthquake.py` | 重写：多源、天地图卡片、省订阅、EQ监视器 |
| `modules/face_lib.py` | **新建**：表情库关键词匹配 |
| `data/main_skill.md` | 新增 command_tools、face_lib、command_reference、private_format/tone |
| `data/幻梦Bot_完整指令文档.md` | **新建**：完整指令参考 |
| `data/help_card.png` | **新建**：预渲染帮助卡片 |
| `services/llm.py` | 动态注入 + `_build_dynamic_command_list()` |
| `core/pipeline.py` | CALL/FACE 检测 + tools.py 清理 |
| `core/dispatcher.py` | message_id 去重 |
| `core/tools.py` | 移除 weather 工具 |
| `modules/commands.py` | 废弃 s/img/img18，/~help 改卡片模式 |

### 💬 私聊专用提示词

群聊和私聊现在使用**独立的格式规则**：

| 项目 | 群聊 | 私聊 |
|------|------|------|
| 最大句数 | 5 句 | 8 句 |
| 每句上限 | 40 字 | 30 字 |
| 表情风格 | 颜文字 (。>∀<。)ﾉ | 文字表情 (QAQ / OuO / QwQ) |
| 称呼规则 | 正常角色标签 [admin]/[friend] | 禁止主人/朋友，只用你我他 |
| 对话风格 | 多人聊天，偶尔插话 | 二人独处，更亲切简短 |

### 📁 main_skill.md 统一管理

所有 LLM 提示词从代码中移出，集中到 `data/main_skill.md`：

```
data/main_skill.md
├── ## prompt_header     # 角色身份：你是{bot_name}...
├── ## group_format      # 群聊格式规则（5句+颜文字）
├── ## private_format    # 私聊格式规则（8句+文字表情+你我他）
├── ## fav_format        # 好感度反馈标记
├── ## fav_tiers         # 好感度档位表
├── ## anti_repeat       # 防重复规则
├── ## play_mode         # 扮演模式规则
├── ## self_awareness    # 自我认知模板（版本/架构/配置）
```

### 🔧 代码改动

**`services/llm.py`**:
- 移除 60 行硬编码 `MULTI_REPLY_SYSTEM_STATIC`
- 新增 `_load_skill_sections()` — 按 `##` 解析 main_skill.md
- 新增 `_build_system_text(bot_name, personality, is_group)` — 群聊/私聊选择不同章节
- `generate_multi_reply()` 新增 `is_group` 参数

**`core/pipeline.py`**:
- 两处 `generate_multi_reply` 调用传入 `is_group`
- 移除"多行分批发送"残留代码

**`core/config.py`**:
- `_build_self_awareness()` 改为从 main_skill.md 读模板
- 模型信息更新为 DeepSeek + Zhipu

### 📦 新增文件

- `data/main_skill.md` — 所有提示词集中管理

---

## 2026-07-06 — 日志控制台格式修复

### 🐛 修复
**`core/log_server.py`**:
- `addLine()` 实时日志渲染从 flex 多列改为单 `<span class="msg">` 结构
- 与 `loadHistory()` 历史日志格式一致：`[时间] [级别] (来源) 消息`
- 解决实时推送日志复制时换行的问题

---

## 2026-07-11 — 预搜索重复 CALL 修复

### 🐛 修复
**`core/pipeline.py`**:
- 搜索结果注入时加前缀 `【系统预搜索】请直接基于这些内容回答，不要再调用 [CALL:~search] 重复搜索`
- CALL 处理中新增检查：管道已自动搜索过时，跳过 LLM 生成的 `[CALL:~search]`，避免重复搜索和 follow-up LLM 胡诌
- 根因：LLM 拿到预搜索结果后仍按 system prompt 要求生成 `[CALL:~search]`，二次搜索 baidu 超时导致 follow-up LLM 胡编乱造
