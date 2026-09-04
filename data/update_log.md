# 更新日志

## v2.0.4s — wdsj 战绩采集失败统计 + 统一重试 + QQ空间说明 — 2026.9.3
现象：00:03 每日采集"80 条新记录 (失败 20)"，全部 ConnectTimeout；且日志出现**两条"采集完成"+ 双进度流**。
根因：
- **同进程双跑**：bot.py run() 遗留 `_bg_wdsj_collector` 与 bg_tasks 插件版并存，同一批 50 玩家被两套循环并发采集，
  上游并发压力翻倍 → ConnectTimeout 批量失败（这是今天失败 20 的主要放大器）
- 原采集无失败名单落盘、无重试（失败只 count）
修复（4 文件）：
- **services/wdsj_tracker.py**：`daily_stats_collect` 重构
  - 模块级 `_collect_lock` 防双跑（已有采集在跑 → 直接跳过）
  - 失败名单统计 → 对失败项**统一重试最多 3 轮**（轮间 sleep 8s 给上游恢复时间）
  - 返回最终失败 [(player, tid)]；成功追加 history 逻辑不变
- **plugins/bg_tasks/main.py**：采集后若 `failed` 非空 → `_notify_qzone_failures` 发 **QQ 空间文字说说**
  列出未采集玩家（按自然日去重，marker 文件 data/wdsj_qzone_fail_notify.json，一天最多一条）
- **bot.py**：注释掉 line 141 `ensure_future(_bg_wdsj_collector())`（遗留循环停止，方法体保留死代码）
- **modules/commands.py**：`/~wdsj collect` 手动采集返回失败名单（"X 人仍未采集到: ..."）
验证（00:13）：
- 重启后日志仅一条"战绩采集将在 04:01 执行"（插件版单循环）
- 手动补采：首轮 82 成功 / 18 失败 → 重试 3 轮 18→3→1→0，**最终失败 0**
- 50 玩家 × 2 模板今日记录全部补齐（179 条），无缺失 → 无 QQ 空间失败通知触发
- 备份：本地/服务器 `*.bak_20260903`（4 文件）

## v2.0.4r — 群消息卡顿根治：judge 熔断 + 指令插队 — 2026.9.2

### 现象
群 767190084 22:45 起每条消息都卡：
- 22:45:00 问 → 22:47:14 才答（拖 2 分 14 秒）
- 22:47:18 发 `/~restart` → 22:48:03 才响应（拖 45 秒）
- 日志：judge 阶段 `LLM [Qwen2.5-7B] 调用超时 (15.0s/15.0s)` 逐条刷屏，队列深度 2→8；glm-4v-flash 同时 Connection error

### 根因（三层）
1. **上游故障**：SiliconFlow(Qwen2.5-7B-Instruct) 与智谱(glm-4v-flash) 同时段不可达/极慢（服务器出口网络问题，外部根因无法本地根治）
2. **judge 超时过长**：cheap=judge=同一 Qwen → 走 `_judge_combined`，每条普通消息白等 15s 硬超时，且 `call_llm` 吞错返回 `""` 被当成"模型说0"→ 静默不回但时间照烧
3. **串行队列全堵**：所有消息（含指令）进 per-group FIFO 队列串行消费，前面堵 N 条，后面的指令被拖 N×15s；且超时后 executor 线程里阻塞的同步调用要等底层 60s 才释放，多条即可饿死线程池（连 deepseek 主模型都排队）

### 修复（3 文件）
- **services/llm.py**
  - 新增**模型级熔断器**：按模型名独立计数，连续 3 次失败(5 分钟内) → 熔断冷却 180s（重复熔断指数退避 ×2 封顶 600s），任意成功即复位
  - `call_judgment_pipeline` 熔断短路：熔断期间跳过模型调用，走本地规则兜底（点名 bot/@ → 必回；明确提问 → 回；其余不回），毫秒级出队不再堵队列
  - `_judge_combined` 空返回/解析失败 → 走规则兜底（不再误判"不该回"）
  - judge 全部超时缩短：15s/10s → **5s**（judge 只输出数字，正常 1-3s 足够）
  - `_create_client` 底层超时对齐 调用超时+5s（原写死 60s）→ 超时后 executor 线程 5s 内释放，不饿死线程池
- **core/queues.py**：FIFO 队列 → **PriorityQueue**，`is_command=True` 的消息 priority=0 永远插到普通消息前（指令不依赖 LLM 判断，不能被卡住的普通消息堵）
- **core/dispatcher.py**：`enqueue_message()` 传入 `is_command=is_command`

### 效果预期
上游再故障时：普通消息最多等 5s 就出队，指令秒级响应；SiliconFlow 恢复后熔断自动复位，不影响正常聊天判断

### 二次修复（当晚 23:10，双进程 + worker 死亡加固）
现象：重启后队列仍只进不出（len 1→17），worker 从第二条消息起 10 分钟不消费、无异常日志。
排查：py-spy 显示主循环 idle、无活跃协程 → **队列 worker task 已死亡**；同时发现服务器有**双 bot 进程**——
`1387885`（11.5h 孤儿，ppid=1，手动遗留，已 kill）与 systemd 新进程 `4089381` 并存。
API 连通性实测全通（deepseek/siliconflow/zhipu 均 0.4s 响应 401）→ 排除网络。
加固（queues.py + pipeline.py）：
- `_group_worker`：单条消息 `wait_for` 总超时 200s，超时跳过继续出队（单条卡死不再拖死全队列）
- worker 外层 catch 所有异常 → 自动续命；`_get_or_create_queue` 看门狗检测 task.done() → 自动重建
- pipeline 阶段打点：回复判断/LLM生成/总耗时 debug + "✅ 管道处理完成"带总耗时
- 插件 pre/post hook 加 10s 超时保护（防插件卡死整条消息）

### 三修（23:25，真正的根因实证：worker 解包 bug）
二次加固后仍复现，抓旧进程关闭日志发现决定性线索：
```
Task-317 _group_worker() done exception=ValueError('too many values to unpack (expected 2)')
```
对照代码定位：
- **v2.0.4r 首轮把 `enqueue_message` 改成入队三元组 `(priority, seq, kwargs)`（PriorityQueue 插队），
  却漏改 `_group_worker` 的取消息解包** —— 仍是二元 `_priority, kwargs = await queue.get()`。
- 后果：每条消息 `get()` 成功后解包即抛 ValueError → 首轮无 try 防护 → **worker 直接崩死**（队列只进不出，len 1→17）；
  二次加固的 try/except 让 worker 不死，但**每条消息必崩** → 死循环重试，消息已出队却被丢弃 → bot 对一切消息无响应。
  （用户侧表现：发了没反应→再发→永远等待，即"总是等待再发、莫名卡一下"）
- 修复（queues.py 一行）：`_priority, _seq, kwargs = await queue.get()` 三元解包
- 验证：23:24:08 消息 #1 入队 → `PIPE 回复判断完成 耗时0.01s`，队列秒级消费恢复正常

附带澄清：
- 此前"双进程/孤儿进程"多为**误判**：KOOK bot（`/root/kook_bot` 下也是 `python3 main.py`）与本人 ssh 远程命令行
  会被 `pgrep -f 'main.py'`/`ps | grep` 命中；QQ bot 实为单实例。区分方法：`readlink /proc/<pid>/cwd` 看工作目录。
- main.py 的 SIGTERM 优雅关闭实际正常（4089381 完整走完插件卸载→落盘→"👋 再见"后退出），无需 kill -9。

## v2.0.4q — 摸头回应反套路强化 — 2026.8.31

### 现象
群友反馈摸头回应老是那几句："别摸了/再摸要炸毛了/头都要被摸秃了" 翻来覆去，听腻了。要求更自由：可以直接回应，也可以不直接回应、用动作

### 修复（纯提示词，data/main_skill.md）
- 规则 15 重写：新增**摸头禁止句式清单**（"别摸了/再摸要炸毛了/耳朵要炸毛了/头都要被摸秃了"及一切"抱怨+拒绝"式回应）
- 新增**灵活回应模式**4 种：动作回应（只写 action）、转移话题、配合演（熟人热情/陌生人冷淡）、玩梗不重复
- 被连续摸头时更要变着花样：动作回应/转移话题/装傻轮换，禁止三连"别摸啦"
- action 字段说明强化：可以独立撑起一条回复（互动时不想说话就只写动作）

### 部署注意
`_load_skill_sections()` 有进程内缓存（_skill_loaded 标志），改 main_skill.md **必须重启 bot 才生效**（不是热加载）

## v2.0.4p — 插件内 KOOK 残留清理 — 2026.8.31

### 现象
points/shop 插件（KOOK 版）代码里直接用 `(met){uid}(met)` 做提及标记，QQ 端显示原样；帮助文案用 `.签到`/`.商店` 点号前缀（QQ 是 /~）

### 修复（三个插件 main.py 本地化适配）
- **points**：2 处 `(met){uid}(met)` → `[CQ:at,qq={uid}]`（QQ 原生 @）；docstring/description/用法文案 `.` 前缀 → `/~`
- **shop**：2 处 `(met){uid}(met)` → `[CQ:at,qq={uid}]`；全部 `.商店`/`.签到` 文案 → `/~`
- **motou**：注释/description/用法文案 `.摸头` → `/~`（无实际标记输出）
- loader 直接扫 plugins/ 目录加载 main.py，不会从 .hmp 重新解包覆盖，改文件有效

### 结果
本地+服务器 py_compile 全过，grep 确认零 `(met)`/`.` 前缀残留；重启后 5 插件正常加载，NapCat 已连接

## v2.0.4o — 插件输出剥离 KOOK 残留标记 — 2026.8.31

### 现象
QQ 群消息里出现 `(met)(met)` 等 KOOK KMarkdown 标记——KOOK 移植插件（motou 等 .hmp）返回的文本带 KOOK 专属格式，原样发到 QQ

### 根因
`core/plugin/kook_compat.py` 的 `strip_kook_text()` 定义了但**全项目无调用点**——剥离逻辑是死代码，KOOK 插件文本直接进 QQ 群

### 修复
在三个插件文本出口接线 `strip_kook_text()`：
- `core/plugin/api.py` `PluginMessage.send`：插件主动发送
- `core/plugin/api.py` `_bridge`：插件命令返回（含 KOOK 风格 msg dict 分支）
- `core/tools.py` `execute_tool` 插件工具分支：FC 工具返回

剥离 `(met)/(rol)/(chn)/(emj)/(file)` 标记，本地单测 7 案例全过（含空提及、混合标记）

## v2.0.4n — /~nickname 只显示当前群 — 2026.8.30

### 现象
群里执行 `/~nickname update`，输出带"全局兜底更新 X 条，分群覆盖 N 个群"跨群字样，用户要求只显示当前群内的

### 修复
- 群聊触发时输出改为纯当前群：`当前群昵称同步完成 / 更新 X 条昵称，共 Y 名成员`（去全局/分群字样）
- 私聊触发保持全量摘要；逐群明细仍需 NICKNAME_VERBOSE=1
- 同步范围不变：群聊本来就只拉当前群成员（日志确认 group=True 分支）

## v2.0.4n — /~nickname 精简输出 — 2026.8.30

### 现象
`/~nickname update` 成功后把每个群每个成员的 `QQ -> 昵称` 全部刷出来，太长刷屏

### 修复
- `sync_and_report` 默认只输出摘要（同步来源 + 更新条数 + 覆盖群数），共 3 行
- 逐群逐人明细保留在代码里，设环境变量 `NICKNAME_VERBOSE=1` 才输出（调试用）
- 顺带删除不再使用的 old_global diff 死代码

## v2.0.4n — 修复 /~nickname TypeError — 2026.8.30

### 现象
群聊执行 `/~nickname update` → `指令执行异常 [nickname]: TypeError: list.append() takes exactly one argument (2 given)`

### 根因
`modules/nickname_sync.py:247` 笔误：`lines.append(f"...", "")` 给 `list.append` 传了两个参数。`list.append` 只接受一个参数，第二个 `""` 是残留

### 修复
- 删除多余的 `""`，顺带去掉该行 ✎ 特殊符号
- 检查全模块无其他同类 append 多参数笔误

## v2.0.4m — 自动提取 CALL 防幻觉执行 — 2026.8.30

### 现象（群聊实测 15:13）
用户问"wzq怎么取消禁手" → bot 正确回答了用法（v2.0.4l 文档修复生效）→ 但文本里的示例 `/~wzq duel @某人 nofb` 被自动提取成真实调用执行：duel 把"@某人"当真名查（找不到玩家），`/~wzq ai 普通 nofb 这样` 更是**真的开了一局人机棋**并渲染棋盘。

### 根因
`pipeline.py` 自动提取 CALL 逻辑：用正则从 LLM 回复文本抠 `/~xxx` 并执行。LLM 无法区分"教用户的示例"和"要执行的调用"——教学文本被当真调用。

### 修复（pipeline.py 提取层 + main_skill.md 规则层）
1. **占位符拦截**：args 含 `某人/用户名/昵称/xxx/示例` 等占位词 → 只删文本不执行
2. **教学行拦截**：该行含 `比如/例如/用法/后面加/就行/即可/或者/指令是` 等教学衔接词 → 该行内所有指令文本视为示例，只删不执行
3. **main_skill.md 红线**：明确告知 LLM"replies 里的 /~xxx 文本会被系统真实执行"，教用法时只能描述规则不能写完整指令示例
4. 本地 6 案例回归全过（2 教学/占位符跳过 + 4 真实执行放行）

### 注意
误伤风险评估：真实执行场景（"我帮你查天气 /~weather 北京"）不含教学词，正常放行；若 LLM 真要执行时把"或者"等词写进同一句会被跳过——影响面小，可接受。

## v2.0.4l — wzq 指令文档纠错（LLM 幻觉调用根治）— 2026.8.30

### 现象（群聊实测）
用户问"怎么取消禁手" → bot 回复"加 nofb 参数" → 紧接着 `[工具调用: wzq nofb]` 执行报"这个指令没有注册过喵"。LLM 说的和执行的对不上，用户看到 bot 自己打自己脸。

### 根因
1. `_CMD_DESC["wzq"]` 写的是"查五子棋战绩排行榜"——纯错误，wzq 是对战指令
2. LLM 没有任何正确的 wzq 用法文档 → 只能现编 nofb 参数
3. 巧合：cmd_wzq 真代码里确实有 nofb（`/wzq duel @某人 nofb` / `/wzq ai 难度 nofb` 的末尾可选参数），但 LLM 生成的是裸 `wzq nofb` 调用，action="nofb" 无此分支 → 报未知指令

### 修复
- `services/llm.py` `_CMD_DESC["wzq"]` 补完整用法：`duel @某人 [nofb] / ai 难度 [nofb] / 坐标落子 / board`，明确 nofb 必须跟在 duel/ai 末尾不能单独用
- `_CMD_DESC["五子棋"]` 同步纠正
- `data/main_skill.md` 指令表 wzq 行补参数列

## v2.0.4k — 生图异步化 + 消息分发并发化 — 2026.8.30

### 根因（群聊实测：/~draw 卡死整个 bot 95 秒）
1. `cmd_draw` 同步 `await _gen_image()`（HTTP 超时 300s），生成期间指令处理线程被占住
2. `bot.py` 主循环 `await dispatch(message)` 串行分发，一条慢消息堵死所有后续消息接收（xiao bai 的消息、后续指令全部排队）

### 修复
1. **`/draw` 后台化**（对齐 `/video` 的既有模式）：新增 `_bg_gen_image()`，`cmd_draw` 发出"开始生成"提示后 `asyncio.create_task` 提交即返回，完成后 @用户 发图；失败不扣次数
2. **`bot.py` 并发分发**：主循环改为 `asyncio.create_task(self._dispatch_safe(message))`，每条消息独立任务；`_dispatch_safe` 捕获异常 + `_bg_tasks` 集合持引用防 GC。单条消息再慢（生图/视频/卡渲染）也不会阻塞 WS 接收和其他消息处理

### 部署
- 服务器已备份 agnes.py / bot.py → .bak_20260830 后上传，bot.service 重启验证 active，NapCat 已连接

## v2.0.4j — help 卡片 v3 整改：作用描述 + 别名独立行 + 删假条目 — 2026.8.30

### 修复（用户反馈）
1. **描述只写「作用」，剥离用法**：所有指令描述清理 `/~xxx <参数>` 用法片段；docstring 含 `—` 分隔符的取后半（作用段）；覆盖 `_EXTRA_DESC` 73 条人工精校
2. **别名布局**：中文别名单独一行，灰色斜体（`font-style: italic; color: var(--text-3)`），位于英文主名下方
3. **删掉编造条目**：`/~@bot`、`/~跟我说`、`/~临时人设` 三个假指令移除，聊天分类只保留真实指令 `/~help`
4. **分类核对**：`gh`/`update`/`_cmd_update` 已归"系统"（拉取 git 代码更新 bot），不再误入"游戏"；所有 admin/主人 指令在"主人"分类（17 条）
5. **导入修复**：补 `import re`（之前 `re.sub` 缺导入 NameError）

### 描述样例
| 指令 | 描述（v2） | 描述（v3 作用版） |
|---|---|---|
| `~wzq` | `五子棋 /~wzq <操作>` | `五子棋对战` |
| `~whois` | `查询域名注册信息...` | `域名 WHOIS 查询` |
| `~draw` | `文生图（默认 16:9）` | `文生图 / 图生图` |
| `~help` | `/~help 发送完整指令卡片...` | `查看指令手册与单个指令详情` |
| `~owner` | `配置管理 /~owner <action> ...` | `配置与数据管理` |

### 布局结构（每个指令卡片）
```
/~wzq                  ← 上行：英文主名（粉色加粗）
/五子棋                 ← 中行：中文别名（灰斜体小字）
五子棋对战              ← 下行：作用描述（白色）
```

## v2.0.4i — help 卡片指令对齐 + /~help <指令> 详情修复 — 2026.8.30

### 修复：卡片指令与实际对不上
- 原实现遍历 capability 的 `name`（函数名/工具名）→ 显示 `/~friend_add` 等**不存在的指令**
- 改为遍历 **COMMAND_MAP 的键（用户实际输入）**，按 handler 归并别名，主名 + 别名显示（如 `/~wzq (/五子棋)`）
- 插件指令（dice/摸头/motou/checkin/签到/sign/points/积分/shop/商店）运行时动态注入 COMMAND_MAP，静态不可见 → 手动维护 `_PLUGIN_COMMANDS` 表并入
- 描述源：docstring 第一行 > capability > _CMD_DESC > 手动兜底（pgr/nasa 等）
- 最终 76 条：聊天 4 / 工具 9 / 数据 15 / 游戏 8 / 创作 7 / 系统 14 / 主人 17 / 插件 2

### 修复：/~help <指令> 一直返回 [LANG:...]
- **根因**：`format_lang()` 签名是 `(key_path, **kwargs)` **没有 default 参数**，`cmd_help` 传的 `default=None` 被当成模板变量 → 永远返回 `[LANG:help.detail.xxx]` 裸键
- 修复：先用 `has_key()` 判断存在性 → lang.toml 详情 → docstring/插件指令描述兜底
- 实测：`/~help wdsj` → 战绩查询详情；`/~help dice` → 插件指令详情；`/~help wzq` → 完整详细帮助；未知指令 → 友好提示

## v2.0.4h — /~help 三列网格指令卡片 — 2026.8.30

### 新增
- `modules/help_card.py`：自动枚举 CapabilityRegistry 全部 command 能力（COMMAND_MAP + 5 个插件注册指令）→ 三列网格 HTML → 渲染 PNG 到 `data/help_card.png`
- `data/templates/help_card.html`：深色极光风格三列网格（与 md_card 同 design system）

### 分类（8 大类 82 条指令）
| 分类 | 数量 | 代表 |
|---|---|---|
| 聊天 | 4 | @bot / 临时人设 |
| 工具 | 14 | search / remind / countdown / pgr / wdsj / translate / whois / analyze / 抽 / read |
| 数据 | 12 | balance / cost / dbsearch / favlist / recall / setstats / stats / unstats / tokens / box |
| 游戏 | 11 | wzq / xq / luck / dice / gh / sys / pc / phone |
| 创作 | 9 | draw / video / voice / img2video / 摸头 |
| 系统 | 17 | info / ping / restart / reload / eq / weather / nasa / tuf* / up* |
| 主人 | 18 | owner / memory / preset / op / persona / sleep / add / leave / ignore |
| 插件 | 3 | plugin / apy / 插件 |

### 改动
- 排除内部测试指令：testsys/testok/jsonraw/md/friend_* 别名等
- cmd_help 行为不变（仍读 `data/help_card.png`），生成器是独立的可随时手动刷新
- 重新生成命令：`cd /root/bot && python3 -c "import asyncio, sys; sys.path.insert(0,'/root/bot'); from modules.help_card import build_help_card_image; asyncio.run(build_help_card_image())"`

## v2.0.4g — 去自动更新 + 日志可读性 overhaul — 2026.8.30

### 启动/停止提速（用户要求最快 + 实测数据）
- **彻底移除自动更新检查**：bot.py 删掉 `_bg_auto_update()` 方法和 `ensure_future` 调度，启动不再有网络请求阻塞
- **实测（systemd 计时）**：
  | 项目 | 优化前 | 优化后 |
  |---|---|---|
  | 优雅停止 | 90s（等满强杀） | **0.6s** |
  | 启动到连 NapCat | 8s+ | **5.2s**（00:08:55.389 启动 → 00:09:00.570 连接，剩数据库/插件/Chromium 固定成本） |

### 日志可读性
1. **指令执行完成日志**：`返回%d字符` → **打印完整返回内容**（`指令执行完成 [xxx]: <实际内容>`），不再只看长度
2. **双时间戳修复**：`core/logger.py` 加 `_logger.propagate = False`——之前 `huanmeng` logger 传播到 root，root 的 lastResort handler 重复打印导致 `[ERROR] (...) [2026-08-30 ...]` 双时间戳
3. **wdsj 查询异常带全量上下文**：`wdsj 查询异常: player='xxx' template=xxx err=ConnectTimeout:... url=...`（含玩家名/模板/完整异常/URL）
4. **采集进度带成功/失败**：`采集进度: 5/42 (成功 3, 失败 2)`，一眼看出有没有成功

### 备注
- 凌晨大量 ConnectTimeout 是 WDSJ_PROXY 代理临时故障，手动验证已恢复（RESULT: OK）
- wdsj_api.py / wdsj_tracker.py 本地已与服务器同步（此前漂移），备份在 qqbot-backup/2026-08-30/

## v2.0.4f — 重启提速：优雅关闭 90s→秒杀，启动 3s — 2026.8.29

### 关闭慢（实测 90 秒）根因
- `main.py` finally 块清理残留任务 `asyncio.gather(*pending)` **没有超时**：wdsj 采集等后台任务卡在 httpx/socket 不响应 cancel 时 gather 永远挂起 → systemd 只能等满 TimeoutStopUSec(90s) 强杀
- 修复：gather 包 `asyncio.wait_for(..., timeout=5)`，超时直接放弃并强制退出

### 启动慢根因
- `bot.py` initialize 里自动更新检查（httpx timeout=8s）串行阻塞，连接 NapCat 前要先等完
- 修复：自动更新改后台 `asyncio.ensure_future(self._bg_auto_update())`，启动只到连接 NapCat 需要 **3 秒**

### systemd 配置
- `TimeoutStopUSec`: 1min30s → **20s**（代码层 15s shutdown 兜底 + 5s gather 超时，20s 足够）
- `TimeoutStartUSec`: 1min30s → 60s
- **删除 `ExecStartPre=pkill -f 'python3 main.py'`**：会误杀 kook-bot（两个服务 cmdline 相同都是 `python3 main.py`），这就是每次重启 kook 都要"自愈"的原因

### 实测
| 项目 | 优化前 | 优化后 |
|---|---|---|
| 优雅关闭 | 90s（等满超时强杀） | 秒级 |
| 启动到连 NapCat | 8s+（串行等自动更新） | 3s |
| kook-bot 存活 | 每次重启被杀自愈 | 不受影响 |

- 备份：`/etc/systemd/system/bot.service.bak_20260829(_b)`、`bot.py.bak_dual_daily`
- 坑：ssh heredoc 传中文 Python 补丁会损坏转义（`\n`→`/n`），必须用本地 Write 生成脚本 + scp 上传执行

## v2.0.4e — 事故修复：lang.toml 被覆盖导致 wdsj 语言键丢失 — 2026.8.29

### 事故经过
- 部署 v2.0.4c 时 scp 本地 `config/lang.toml` 覆盖了服务器版 → 服务器 lang.toml 是**超集**（含私有模块 `[wdsj] [weather] [analyze] [box]` 4 段），本地版没有 → wdsj 指令全部变成 `[LANG:wdsj.xxx]` 裸键
- 首次"修复"合并脚本有 bug：按行正则 `^\[([\w.-]+)\]$` 匹配段头，但文件里存在 `["help.detail"]` 带引号段头 → 匹配失败，后续键全部串段 → wzq 段被污染成 31 键
- 最终修复：**直接以 `bot-backup-20260821-111535/config/lang.toml` 为基底恢复**（完整无损 27 段），仅叠加 draw 帮助文本的引用图说明
- 教训：**服务器私有模块的语言键只存在于服务器 lang.toml，本地覆盖前必须先 diff**；部署配置类文件禁止无脑 scp

### 修复结果
- 服务器 + 本地 lang.toml 已同步为同一份（27 段，含 wdsj 5 键 / box 18 / weather 3 / analyze 1）
- `format_lang('wdsj.player_searching')` 等全部恢复
- bot.service 重启，NapCat 已连接

## v2.0.4d — 生图/视频失败消息直接带错误详情 — 2026.8.29

### 改动
- 用户反馈：失败只提示"生成失败喵~"看不出原因，要翻日志才知道是 429
- `_gen_image` / `_gen_image_with_ref` / `_gen_video` 失败时返回 `{"error": 文本}`（原静默返回 None）
- 新增 `_err_text()`：HTTP 状态码 + 响应体前 200 字符（httpx 超时等 str 为空的异常回退到异常类型名）
- `cmd_draw` 失败消息：`图片生成失败喵~ (不扣次数)\n错误: HTTP 429: ...`（截断 350 字符）
- `_bg_gen_video` 失败消息同样带错误详情；创建任务 429/5xx 重试时记录最后一次错误文本
- 参考图 multipart 失败 → JSON 兜底失败时保留**第一次**错误（更有诊断价值），两者都无内容才写"未知错误"
- 已查实 22:42 失败根因：CloudMist 429 Too Many Requests（限流），等一会再试即可

## v2.0.4c — /~draw 引用图片转参考图 — 2026.8.29

### 新功能
- 引用一张图片 + `/~draw <提示词>` → 以引用图为参考图生成（图生图）
- 实现：`cmd_draw` 新增 `raw_message` 参数（inspect 签名自动注入，同 img2video 机制），从 `[CQ:reply,id=]` 用 `get_msg` 提取引用消息图片 URL（message 段优先，raw_message CQ 码兜底）
- `_gen_image` 新增 `reference_image_url` 参数：有参考图时走 `_gen_image_with_ref`

### 参考图生图链路（服务器实测）
- **CloudMist `/images/edits` 仅支持 multipart 文件上传**（实测 200 OK），JSON body 传 URL 返回 500 `convert_request_failed`
- 流程：下载参考图到临时文件 → multipart 上传（`image` 字段 + model/prompt/n/size）→ 保存结果
- 兜底链：multipart 失败 → JSON URL 兜底（预留其他中转站）→ 参考图下载失败 → 退回纯文生图
- 下载校验：响应 < 1KB 视为无效（错误页防御）
- 失败不扣配额（沿用户原有逻辑：失败时 return，成功才 `commit_draw`）

### 其他
- 进度提示带 `[参考图]` 标记，方便确认是否进入图生图模式
- `lang.toml` draw 帮助文本补引用用法说明
- 端到端验证：QQ 头像作参考图 → 生成 1.8MB 赛博朋克风格图 OK

## v2.0.4b — 修复 wdsj 日榜推送 toml 未定义 + CQ 路径 4 斜杠 — 2026.8.29

### 修复 `name 'toml' is not defined`
- `plugins/bg_tasks/main.py` 的 `_bg_wdsj_collector` 在日榜推送里用 `toml.load` 读 `bot_config.toml`，但 `import toml` 只在 `_bg_control_watcher` 函数内，作用域不覆盖 → 每次整点推送都失败
- 修复：`import toml` 提升到模块级（删除函数内冗余 import），两个函数共用
- 备份：`qqbot-backup/2026-08-28/`（该插件为服务器特有，本地已补存一份至 `plugins/bg_tasks/main.py` 保持同步）

### 顺手修复 CQ 路径 4 斜杠（同 motou ENOENT 坑）
- 日榜图片发送原用 `f"[CQ:image,file=file:///{_p}]"` → `file:////root/...`（4 斜杠）NapCat 会 ENOENT
- 改用 `services.sender.build_local_image_cq()`（`lstrip('/')` 后拼 3 斜杠）

### 补发
- 已手动补发 2026-08-28 日榜：普通榜 19 人 + 竞技榜 16 人，2 张图发至群 1058782600 均 OK
- 补发工具保留在 `scripts/_resend_wdsj_daily.py`（服务器执行，跑完自删）

### 推送频率修正（2026.8.29）
- 原逻辑采集与推送绑定：每 4 小时（0/4/8/12/16/20 点）采完就推一次日榜
- 修正：只有 **0 点时段**推送（发昨日完整榜），其他时段仅采集累计数据不推送

### 生图限额确认 5 次/天（2026.8.29）
- 代码默认 `DEFAULT_DRAW_LIMIT = 5` 本就生效，admin（主人+分群 OP）无限
- 清理 `data/draw.json` 历史残留：QQ 3483585417 的 `limit:10` 个人覆盖改回 5
- `/~owner` 帮助文本两处"默认 10"改"默认 5"

## v2.0.4 — 修复 qq_name_map 分群昵称 + LLM 对话者身份 — 2026.8.28

### 修复 qq_name_map 分群隔离
- `modules/nickname_sync.py` 重建：分群昵称存储到 `data/group_nicknames.json`（群号→{QQ→卡片名}），全局 `qq_name_map` 只保留好友主页昵称兜底
- `core/config.py` 新增 `group_nicknames` 字段 + `get_display_name` 分群优先（分群自动同步→分群手动配置→全局→QQ号）
- `core/dispatcher.py` 昵称处理改为：事件自带 card 优先（分群正确），仅 card 缺失时才用映射补全
- `core/pipeline.py` 全面改用 `display_name`（分群感知）传给 LLM 的 `speaker_name`、`actor.name`、`当前对话者` 等
- `_build_at_list` 反查分群映射优先
- 所有调用点统一：`modules/commands.py`（favlist/recall/wzq）、`modules/leave.py`、`modules/memory.py`、`modules/stats.py`（日报）、`modules/wzq.py`（棋盘渲染）、`utils/username.py`

### 修复昵称更新不生效
- `cmd_add_relation` 保存后调用 `reload_config()`，立即刷新内存配置
- `nickname_sync._merge_global_map` 保存后也调 reload

### 修复 LLM 对话者身份错乱
- `services/llm.py` `_build_messages` 修复 bot 角色判断 bug：`sep` 已含 `bot_name: ` 却再次拼接，导致历史 bot 消息全被标为 user → 角色错乱。修复后正确区分 user/assistant
- 强化 `_build_messages` 注入 `【当前对话者】{speaker_name}` 让 LLM 明确知道谁在说话

## v2.0.3 — 插件管道钩子 + 沙箱 OS 级隔离 + 优雅关闭 — 2026.8.23

### 插件能力扩展（移植 KOOK 优化）
- `core/plugin/api.py` 新增 `PluginPipeline`（on_message 预钩子 / on_reply 后钩子）、`PluginBackground`（后台任务注册，卸载自动取消）、`_HookRegistry` + `get_pipeline_hooks`
- `core/plugin/loader.py` 新增 `drop_module()`：热重载时清 `sys.modules` 缓存，插件更新后 reload 生效
- `core/plugin/manager.py` unload 时调用 `drop_module()`
- `core/pipeline.py` 消息处理接入 pre/post hooks；签名加 `**extra_kwargs` 前向兼容，避免未来参数变更导致 TypeError 静默

### 沙箱安全升级
- `core/sandbox.py` 从正则黑名单改为 OS 级隔离：`resource.setrlimit` 限内存(256MB)/限 CPU，独立临时目录 + 超时强杀 + 产物收集
- 新增 `run_python_str` / `run_shell_str` / `compile_and_run_cpp_str` 兼容旧接口

### 其他
- `main.py` 完全重写优雅关闭：15s 超时 + CancelledError 捕获 + 残留任务清理，v1.4.2 → v2.0.0
- `bot.py` initialize 加 `init_db()` + 存量记忆回填；shutdown 加 Plugin Runtime / DB 关闭
- `modules/search.py` 新增承接句检测 `_is_continuation`，搜索自动合并上下文补主题
- `.gitignore` 增加 `*.key` / `*.pem` / `tmp/` / `temp/` / `backup/` / `.update_cache/` / `plugins/` / `*.db` / `*.sqlite`
- 备份：`qqbot-backup/2026-08-23/`

## v2.0.2 — 手机状态 TCP 接收 + /~phone 指令 — 2026.8.22

### 新增手机状态长连接
- 新增 `services/phone_status.py`：独立 TCP 接收端（端口 58892），复用 `BOT_PC_KEY` 鉴权，与 PC 状态服务（58890）同一套 JSON 行协议，存 `_PHONE_DATA` 快照（60s 超时）
- `bot.py` 新增 `_bg_phone_status_server`，随启动拉起；后台任务清单补充「手机状态:58892」
- `modules/commands.py` 新增 `cmd_phone` + `COMMAND_MAP["phone"]`，`/~phone` 返回手机实时状态文本（电量/CPU/内存/存储/网络/屏幕/开机时长）
- 配套 Android App（本地 `AndroidStudioProjects`）作为 TCP 上报端，经 TCP 长连接持续上报本机状态
- 设计决策：复用现有 TCP 长连接协议而非新建 WebSocket（等价「长连接」，零新依赖、对 PC 链路零侵入）
- 备份：`qqbot-backup/2026-08-22/`

## v2.0.1 — 经济系统插件化 + KOOK 插件兼容 — 2026.8.21

### 经济系统迁移为插件
- 主仓库移除 `modules/economy.py` 及 `/~points /~sign /~gift /~shop /~buy /~bag /~use` 内置指令
- 积分/签到/商店改由插件库 `points` / `shop` 插件提供（`/~plugin install points shop`）
- 数据独立存 `plugins/points/data.json`，与内置版 economy.json 不互通
- `ctx.economy` 优雅降级：模块不存在时返回 no-op 空对象，旧插件（dice 积分奖励）不崩溃

### KOOK 生态插件兼容
- `core/plugin/api.py` 的 `_bind_command` 支持 KOOK 风格 handler（收 msg 字典：args/author/sender/chat_id/is_group），配合 kook_compat 可加载插件库 KOOK 生态插件
- 实测 points/shop 插件加载 + 签到通过

## v2.0.0 — Huanmeng 2.0 架构升级 — 2026.8.21

从 v1.4.2 直接升到 2.0.0：本轮移植 huanmeng-kook-bot（同作者）的完整 2.0 架构与全部优化，版本号与 KOOK 端对齐（kook 已升级为 Huanmeng 2.0.0）。

v2.0.0 包含：完整插件系统、三大功能模块（经济系统 / SQLite 全文检索 / skills 模块化提示词）、KOOK 生态兼容、calc 数学计算沙箱、168 commits 运行时优化。

---

### 1. 完整插件系统

把 huanmeng-kook-bot 的插件体系整体移植到 qqbot，架构对齐。

#### 插件运行时
- `core/plugin/` 四件套：`manifest.py`（PluginManifest 校验）/ `loader.py`（发现 + 动态导入）/ `manager.py`（discover→load→init→enable→disable→reload→unload→health 生命周期机，单插件崩溃隔离）/ `api.py`（PluginContext 公开 API）
- `core/eventbus.py`：统一事件总线（订阅/发布/通配/异步隔离，插件协作基础设施）
- `core/capability/`：能力注册表（Capability 统一 Command/Tool/Plugin 抽象 + registry/router/loader）
- 插件写法：`plugins/<name>/manifest.json` + `main.py`，类名 `Plugin(ctx)`，可选 `on_load/on_enable/on_disable/on_unload`，reload 自动清理事件/定时器/能力注册

#### 插件能力（ctx.*，惰性解耦）
- `message.send/send_file`、`memory.remember/recall`、`event`、`timer.every`、`capability.register_command/register_tool(always_on)`、`config`、`economy`、`vision.describe`、`identity.is_admin`、`llm.generate`、`approval.request`（私聊管理员 + /~apy 回执）、`sandbox.run_python/cpp/shell`、`logger`

#### 分享与插件库
- `modules/plugin_share.py`：`.hmp` 打包/解包（防 zip-slip）、聊天下载、插件库客户端（`PLUGIN_LIB_BASE`，默认 `01240820.xyz:20030`，实测可连、库内 13 个插件）
- `/~plugin` 指令：list / install / unload / reload / pack / import / update（一键更新）
- `/~apy <token> 同意|拒绝`：审批回执

#### FC 集成
- `core/tools.py`：`get_tool_schemas()` 合并插件动态注册工具 Schema（内置同名优先）；`execute_tool` 增加插件 handler 分发回退
- 插件命令经 `register_command` 自动挂进 COMMAND_MAP，`/~name` 直接可调，卸载自动移除
- 插件工具经 FC 循环被 LLM 自动发现并调用，核心 pipeline 零改动

#### 启动
- `bot.py` 启动时 `get_plugin_manager().load_all()`，单插件失败不影响主流程
- 新增示例插件 `plugins/dice/`：`/~dice` 掷骰 + `roll_dice` 常驻工具 + 掷骰奖励 1 积分（联动经济系统）

---

### 2. 三大功能模块移植

从 KOOK 机器人移植三块 qqbot 原本缺失的能力。经比对，qqbot 已是 kook bot 的超集（多中国象棋/好友请求/退群/昵称同步/撤回记录），故仅精准补齐以下三块。

#### 经济系统（积分 / 库存）
- 新增 `modules/economy.py`：积分增减、转增、签到、商店、背包、物品使用
- 数据落 `data/economy.json`（全局 RLock + 临时文件原子写，沿用 kook 设计）
- 新增指令：`/~points /~sign /~gift /~shop /~buy /~bag /~use`（含中文别名 积分/签到/赠送/商店/购买/背包/使用）
- 示例物品「好感券」：使用后给当前聊天中的自己 +10 好感度（联动 `modules/fav.py`）
- 所有经济函数以 try/except 优雅降级，模块缺失不影响聊天主流程

#### SQLite + FTS5 全文检索数据层
- 新增 `db/store.py`（`SearchStore`）：聊天记录 / 记忆结构化存储 + 全文检索
- FTS5(trigram) 中文子串匹配；不可用时自动降级为 SQL LIKE
- `dispatcher` 在 `record_message` 钩子后自动索引群消息，私聊消息单独索引
- 新增 `/~回顾 <关键词>` 指令直接查询索引；`db/migrate.py` 一键回溯 `data/msglog/*.jsonl`
- 完全 ADDITIVE：不破坏现有 JSON 存储，写入失败静默降级

#### skills/ 模块化提示词体系
- `services/llm.py` 的 `_load_skill_sections()` 新增 `_merge_skills_dir()`，自动把 `data/skills/*.md` 按 `## 章节` 叠加到 `main_skill.md`，未显式引用的章节兜底追加
- 新增示例 `data/skills/20_economy.md`，让 bot 主动知晓积分系统

---

### 3. KOOK 生态兼容

#### .hmp 插件 KOOK 格式自动剥离
- 新增 `core/plugin/kook_compat.py`：加载插件时向 `sys.modules` 注入 `khl`/`kook`/`kaiheila` 假模块（含 `khl.api`、`Card`、`MessageTypes` 等），KOOK 生态 `.hmp` 插件无需改动即可在 qqbot 加载运行，KOOK 专属调用安全降级
- `strip_kook_text()`：剥离 `(met)/(rol)/(chn)/(emj)/(file)` 等 KMarkdown 标记
- `loader.py` 类定位改严格判定（`vars()` 检查钩子 + `_kook_stub` 标记跳过），修复 stub 类被误判为 Plugin 类的问题
- 纯内存注入、不改源不落盘；插件真实能力走 ctx.* 与 qqbot 原生一致

---

### 4. calc 数学计算沙箱

#### calc 工具：Python 代码精确求解
- 用户发数学题/方程/方程组时，LLM 自动生成 Python 代码调用 `calc` 工具
- 代码在沙箱子进程中执行（5 秒超时、2000 字符输出限制，保头尾折叠中间）
- 执行结果喂回 LLM，再生成自然聊天回复
- 彻底解决 LLM 心算出错的问题（如：忽略方程矛盾、算错代数式）

#### 安全沙箱
- 静态正则拦截：禁止 `import os/sys/subprocess/socket` 等危险模块
- 禁止 `open()`/`exec()`/`eval()`/`__import__()` 等危险调用
- 禁止 `getattr`/`setattr`/`delattr` 与 `__class__`/`__subclasses__`/`__mro__` 等沙箱逃逸向量（core/tools.py 与 core/sandbox.py 一致）
- 最小化环境变量，子进程隔离
- 可用模块：math, fractions, decimal, statistics, sympy（如已安装）

#### FC 管道集成
- `calc` 结果归类为 `data_results`，走 json_mode 生成最终回复
- 执行失败（含"失败"关键字）归类为 `errors`，LLM 可解释错误
- `_CMD_DESC` 和 `_build_messages` 格式提醒同步更新

---

### 5. 168 commits 运行时优化

通读 kook 仓库 168 commits 筛选的运行时优化：

- **单工具超时**（kook 67dd501）：`core/tools.py` 新增 `TOOL_TIMEOUTS` 工具级超时表 + `get_tool_timeout()`，FC 循环 `run_one` 用 `asyncio.wait_for` 包裹，防慢工具拖死整轮
- **输出截断保头尾**（kook 6cda8e0）：`_python_eval` 用 `_fold_truncate` 保留头尾折叠中间，防 LLM 编造被截断的尾部结果
- **calls 多形态解析**（kook a101954）：LLM 回复 JSON 的 calls 兼容 `tool/name` + `arguments/args`（含字符串 JSON 参数）
- **FC 轮数 2→6 + 防死循环**（kook 5fcab40）：MAX_ROUNDS 放宽为保险上限，连续两轮相同工具调用集自动终止
- **max_tokens<=0 保护**（kook 900125e）：`call_llm`/`call_llm_with_tools` 将 `max_tokens<=0` 视为不设上限，防 DeepSeek 400
- **msglog 回溯 500→5000**（kook dcb9ba3）：`search_msglog` 默认扫描上限提升，提升记忆召回
- **事实断言强制搜索**：`search_web` 工具描述强化，用户提出需核实的断言时必须搜索后回答，禁止仅凭模型内在知识下结论

---

### 6. 验证
- KOOK 插件（import khl / Card / MessageTypes / api.fetch）+ 原生插件共存加载、命令/工具分发、卸载清理全通过
- 沙箱逃逸向量（getattr / __class__ / __mro__ / globals 等）全部拦截测试通过
- 全部修改文件 py_compile 通过

## v2.0.5 — /~img 指令恢复（Lolicon API）— 2026.8.24

### 图源切换：waifu.pics → Lolicon（Pixiv 来源）
- 废弃不可用的 `waifu.pics`，`/~img` 改用 `https://api.lolicon.app/setu/v2`（免费免 key）
- **支持多标签参数**：`/~img 甘雨 原神` 为 AND（同时包含，最多 3 个）；单个标签内可用 `|` 做 OR，如 `/~img 萝莉|少女 白丝|黑丝` → (萝莉 OR 少女) AND (白丝 OR 黑丝)；无参数时随机
- `/~img18`（R18）曾恢复注册，**现按需求下线（移除注册，逻辑保留待启用）**；`/~img` help 不再提示 R18 版
- 拆两套请求头：API 请求不带 Referer（否则 403），图片下载必须带 `Referer: https://www.pixiv.net/`（走 i.pixiv.re 反代）
- `size` 用重复键手工拼接 `size=regular&size=original`（urlencode 对数组不可靠），优先取 regular 缩略图发送
- r18=0 全年龄过滤：库分类 r18 字段 + **R18 标签黑名单**（与 lolicon_client.py 一致，因 API r18 字段是库分类、不等同作品本身标识）；拉取 5 张再过滤保证有命中；无结果时提示换关键词
- **随机选图**：拉 5 张用 `random.choice` 而非固定 `items[0]`（Lolicon 带 tag 返回顺序固定，始终取第一张会导致同 tag 永远同一张图）
- **最近 pid 去重**：`_IMG_SENT_PIDS` 集合缓存已发 pid（最多 200，超限清空），同 tag 连发跳过已发过的图，全命中则放宽不跳过
- 保留本地下载 + `file:///` CQ 码发送链路，失败降级直发远程 URL
- `/~img help` 显示用法帮助（支持 帮助/?/usage 别名）
- 备份：`qqbot-backup/2026-08-24/`

## v2.0.6 — /~draw 图源切换 CloudMist（gpt-image-2）— 2026.8.24

### 文生图后端更换
- `modules/agnes.py` 文生图接口改走 `https://v2.cloudmist.cloud/v1`（OpenAI 兼容），模型 `agnes-image-2.1-flash` → `gpt-image-2`
- 返回改为 base64（`data[0].b64_json`），解码后存 PNG 发送；保留 url 兜底分支
- API key 硬编码为 CloudMist token（原环境变量 AGNES_KEY 不再用于 draw）
- 文生视频（`/~video` / `/~img2video`）独立为 `AGNES_VIDEO_BASE` + `_video_headers()`，仍走原 Agnes 服务与环境变量 key，不受图源切换影响
- 备份：`qqbot-backup/2026-08-24/`

### 修复：/~draw 超时失败（v2.0.6 补充）
- `_gen_image` 超时 120s → 300s：gpt-image-2 复杂 prompt 生成可超过 2 分钟（实测日志 120s 整触发超时）
- 异常日志改打印类型 + repr（httpx 超时异常 str 为空导致日志无内容）
- url 兜底下载超时 30s → 60s

## v2.0.7 — /~draw 自定义画面比例（默认 16:9）— 2026.8.25

### 比例支持
- 默认不再是 1024x1024 方块，改为 **16:9（1536x864）**
- 新增 `_ASPECT_RATIOS` 映射表 + `_resolve_size()` 解析器，支持：
  - 比例别名：`16:9` / `1:1` / `3:2` / `2:3` / `9:16` / `4:3` / `3:4`
  - 英文别名：`square` / `portrait` / `landscape` / `wide` / `tall`
  - 直接尺寸：`1024x1024` / `1536x864` 等任意 WxH
  - 无法识别→默认 16:9
- 用法：`/~draw <提示词>`（默认 16:9）/ `/~draw 1:1 猫娘`（指定比例）/ `/~draw 1024x1024 猫娘`（指定尺寸）
- 备份：`qqbot-backup/2026-08-25/`

## v2.0.4t — 后台任务去重（提醒/控制/节假日）— 2026.9.3

### 现象
- 每次启动日志出现 2 条"提醒轮询已启动（每30秒检查一次）"（间隔约 60ms，历史每次启动都双份）

### 根因
- bot.py run() 与 plugins/bg_tasks 插件 on_enable 各自注册了同一批后台任务：
  提醒轮询(remind_checker_loop) / 控制文件监听(control_watcher) / 节假日服务(holiday) → 三组双跑
- 30s 提醒轮询 x2 → 到点提醒存在双发风险；holiday x2 → 节日推送双发风险
- 与 v2.0.4s wdsj 双跑同源：插件化迁移后旧入口的重复任务没删干净

### 修复
- bot.py 注释掉 3 处重复注册（方法体保留死代码），统一由 bg_tasks 插件负责：
  `# _bg_remind_checker()` / `# _bg_control_watcher()` / `# _bg_holiday()`
- 备份：`qqbot-backup/2026-09-03/bot.py.bak_20260903_remind_dup` / 服务器 `bot.py.bak_20260903_nodup`

## v2.0.4u — 搜索结果"诚实归因"提示词修复 — 2026.9.4

### 现象
- 用户问"环流三号是啥"，bot 先说"让我查查喵~"并调用 search_web，搜到后却答"哦这个我知道喵！"
  —— 把刚查到的内容装成自己本来就知道，与上一句"让我查查"自相矛盾

### 修复（双层）
1. `data/main_skill.md` v1.1.3→v1.1.4：command_tools 新增规则10【诚实归因】
   凡工具查来的信息必须如实说"刚查了下/搜到啦/查到啦"，严禁说"我知道/我记得/早就知道"；
   仅聊天记忆/长期记忆内容可用"记得"
2. `services/llm.py` `generate_multi_reply_with_tools`：工具执行完的最终 json_mode 回复轮，
   在 system 区插入一条"诚实归因"提醒（v2.0.4u 注释），就近压制该轮幻觉

### 验证
- 本地 py_compile 通过；备份 qqbot-backup/2026-09-04/（llm.py + main_skill.md）

## v2.0.4v — 深度讲解模式（详细解释类真·详细输出）— 2026.9.4

### 现象
- 问"环流三号是啥""雷石东直放站"这类想了解的话题，bot 两三句打发，不够详细
- 用户要求：像 webai 工具那样输出完整深度长文；纯文本不渲染 markdown；可以换行有格式；
  分多句发出来且分句合理；人格不丢

### 改动（data/main_skill.md v1.1.4→v1.1.5，纯提示词，无代码）
- group_format 新增小节【深度讲解】规则19~24：
  - 触发：XX是啥/详细解释/讲讲/科普/原理/为什么/怎么实现/区别/完整流程 等值得展开的话题
  - replies 8~15 句逐条发出成一篇长文；每句=一个完整小节/段落，句内可 \n 换行分段
  - 分句边界=小节边界，不硬切；每句 50~500 字
  - 排版：纯文本【】分节 + "1."/"- "列表 + 空行分段；禁止 markdown 符号(#/>/**/```)
  - 人格：全程猫娘口吻但内容专业完整，卖萌是点缀
  - 不确定数据先 calls search 查证 + 规则10 诚实归因
- 长内容规则17 补充：名词解释/科普/答疑不算"写东西"，不走 FILE，直接深度讲解
- private_format 新增规则11：私聊深度讲解指针（6~12 句、≤300字/句）

### 约束核验（无需改代码）
- JSON 路径不截断 replies 句数（[:5] 仅在纯文本降级路径）
- sender.send_sentences 逐条发送、不截断内容、\n 原样保留 → 句内换行格式可行
