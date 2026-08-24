# 更新日志

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
- `/~img18`（R18）恢复注册，同样支持多标签，**仅限私聊**（群聊直接拒绝，防封号）
- 拆两套请求头：API 请求不带 Referer（否则 403），图片下载必须带 `Referer: https://www.pixiv.net/`（走 i.pixiv.re 反代）
- `size` 用重复键手工拼接 `size=regular&size=original`（urlencode 对数组不可靠），优先取 regular 缩略图发送
- r18=0 全年龄过滤：库分类 r18 字段 + **R18 标签黑名单**（与 lolicon_client.py 一致，因 API r18 字段是库分类、不等同作品本身标识）；拉取 5 张再过滤保证有命中；无结果时提示换关键词
- 保留本地下载 + `file:///` CQ 码发送链路，失败降级直发远程 URL
- `/~img help` / `/~img18 help` 显示用法帮助（支持 帮助/?/usage 别名）
- 备份：`qqbot-backup/2026-08-24/`
