# 🌙 幻梦 HuanMeng

> 一个高度可定制的 LLM 驱动 QQ 机器人 —— 基于 NapCat + OneBot v11 + DeepSeek。默认附带猫娘人设，角色完全自定义。

<p align="center">
  <b>Built with DeepSeek V4 Pro · by Trusler</b>
</p>

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-v2.0.0-ff69b4)](https://github.com)
[![Stars](https://img.shields.io/github/stars/Trusler258/huanmeng-qqbot?style=flat)](https://github.com)
[![Language](https://img.shields.io/github/languages/top/Trusler258/huanmeng-qqbot)](https://github.com)
[![Downloads](https://img.shields.io/github/downloads/Trusler258/huanmeng-qqbot/total)](https://github.com)
[![Repo Size](https://img.shields.io/github/repo-size/Trusler258/huanmeng-qqbot)](https://github.com)

<br>
<img src="https://img.shields.io/badge/powered_by-DeepSeek-8B5CF6?style=flat" />
<img src="https://img.shields.io/badge/adapter-NapCat%20OneBot%20v11-00BFFF?style=flat" />

> ⚠️ **本仓库为社区开源 Lite 版**，服务器运行版含额外功能模块，不在此仓库。<br>
> 克隆后可直接运行，缺失模块自动优雅降级。

> ⚠️ **本项目与腾讯 QQ 内置的「幻梦」官方 Bot 没有关系。** 这是社区开源的第三方项目，项目名仅指 Bot 的默认角色昵称。<br>
> 本项目基于 [NapCat](https://github.com/NapNeko/NapCatQQ) 协议适配，与 QQ 官方接口、机器人平台无关。

---

## ✨ 特性

| 分类 | 功能 |
|------|------|
| 💬 聊天 | LLM 驱动多轮对话、自动回复、好感度系统（100档）、多角色人设 |
| 🧠 三层记忆 | 瞬时记忆（上下文窗口）、短时记忆（JSON滚动30条）、长时记忆（MD文件永久保存，模板化压缩） |
| 🔍 联网搜索 | 自动判断是否需要搜索，DuckDuckGo 免费接口 |
| 🖼️ 图片识别 | 异步识别图片内容，注入上下文供 LLM 自然引用 |
| ☁️ 天气 | 7 天天气预报（viki.moe），HTML 卡片渲染 |
| 📦 快递 | 快递单号查询（快递100），HTML 卡片渲染 |
| ♟️ 五子棋 | PVP 对战 + 人机 AI（4 级 LLM），禁手规则，棋盘卡片渲染 |
| 🎵 音游 | TUF 谱面搜索/详情/下载直链 |
| 🌍 翻译 | 中英日韩法德互译 |
| 📊 群统计 | 昨日/今日发言统计 |
| 🔔 提醒 | 定时提醒（相对/绝对时间） |
| ⏰ 倒计时 | 自定义事件倒计时 |
| 💾 配置管理 | 动态热重载、功能开关、白名单 |

### 完整指令列表

```
/~help             帮助菜单
/~ping             在线检测
/~info             运行状态
/~weather <城市>    天气查询
/~box <单号>        快递查询
/~wzq ai <难度>    五子棋人机
/~wzq duel @人    五子棋对战
/~tufsearch <曲名> 谱面搜索
/~tr <语言> <文本>  翻译
/~stats            群聊统计
/~recall           撤回记录
/~luck             每日运气
/~countdown        倒计时
/~favlist          好感度排行
/~memory           记忆查询
/~owner            配置管理（主人）
/~reload           热重载配置

# 移植自 huanmeng-kook-bot 的新模块
/~points           积分查询（经济系统）
/~sign             每日签到（连续签到加成）
/~gift <qq> <数>   赠送积分给好友
/~shop             积分商店（好感券等）
/~buy <物品>       用积分购买物品
/~bag              查看背包
/~use <物品>       使用背包物品（如好感券 +10 好感）
/~回顾 <关键词>    聊天历史全文检索（SQLite FTS5）

# 插件系统（移植自 huanmeng-kook-bot Phase 13/14）
/~plugin            插件管理（主人）：list/install/unload/reload/pack/update
/~apy <token> 同意|拒绝   响应插件人工审批
/~dice [面数]      示例插件：掷骰子（奖励 1 积分，插件加载后才有）
```

> 经济系统数据存 `data/economy.json`；聊天全文检索存 `data/search.db`。
> 模块化提示词放 `data/skills/*.md`，会被 `data/main_skill.md` 自动叠加到 system 提示词。
> 以上均为 **ADDITIVE 层**：模块缺失/失败不影响聊天主流程。

## 🧩 插件系统（huanmeng-kook-bot Phase 13 完整移植）

插件 = `plugins/<name>/manifest.json` + `main.py`（类名 `Plugin`，构造接收 `ctx`）。

```
plugins/
├── dice/                  # 示例插件
│   ├── manifest.json      # name/version/runtime/entrypoint/permissions/config
│   └── main.py            # class Plugin(ctx) + on_load/on_enable/on_disable/on_unload
└── _down/                 # .hmp 下载临时目录（自动跳过加载）
```

**插件可用能力（`ctx.*`，全部惰性解耦）**

| 能力 | 说明 |
|---|---|
| `ctx.message.send/send_file` | 发文本/文件（群聊/私聊） |
| `ctx.memory.remember/recall` | 记忆写入/检索（SQLite 检索层优先） |
| `ctx.event.on/subscribe/publish` | 事件总线订阅/发布 |
| `ctx.timer.every(秒)` | 周期定时器（卸载自动取消） |
| `ctx.capability.register_command` | 注册指令，自动挂进 COMMAND_MAP（/~name 可调） |
| `ctx.capability.register_tool` | 注册 FC 工具（`always_on=True` 常驻，LLM 普通聊天可用） |
| `ctx.config(key)` | 读 manifest.config |
| `ctx.economy` | 积分/库存（modules.economy，唯一锁+原子写） |
| `ctx.vision.describe` | 图片识别 |
| `ctx.identity.is_admin` | 权限判定 |
| `ctx.llm.generate` | 文本生成（reply_model） |
| `ctx.approval.request` | 人工审批（私聊管理员 + /~apy 回执） |
| `ctx.sandbox.run_python/cpp/shell` | 沙箱真实执行（黑名单+超时+输出截断） |
| `ctx.logger` | 插件命名空间日志 |

**架构组件**（与 kook 对齐）：`core/eventbus.py` 事件总线、`core/capability/` 能力注册表（Capability→Command/Tool/Plugin 统一抽象）、`core/plugin/`（manifest/loader/manager/api）、`core/sandbox.py`、`modules/plugin_share.py`（.hmp 打包/解包/插件库客户端）。

**插件库一键更新**：`/~plugin update` 从 `PLUGIN_LIB_BASE`（默认 `http://01240820.xyz:20030`）拉取插件列表与更新，`/~plugin install <名|url>` 安装，`/~plugin pack <名>` 打包 `.hmp` 分享。

### 🔄 KOOK 生态插件自动兼容（加载时剥离 KOOK 格式）

插件库里的 `.hmp` 插件是为 KOOK 机器人写的，qqbot 加载时自动兼容：

- **KOOK 模块 stub**：加载前向 `sys.modules` 注入 `khl` / `kook` / `kaiheila` 假模块（含 `khl.api`、`Card`、`MessageTypes` 等），`import khl` 不再报错，KOOK 专属调用（发卡片、khl API）安全降级，插件照常加载运行。
- **KMarkdown 剥离**：`core/plugin/kook_compat.py` 的 `strip_kook_text()` 自动去除 `(met)/(rol)/(chn)/(emj)/(file)` 等 KOOK 专属标记，插件返回的文本转成 QQ 可读纯文本。
- **不落盘不改源**：纯内存注入，插件卸载不影响；真实能力走 `ctx.*`（message/economy/vision/sandbox 等）与 qqbot 原生一致。

**从 kook 移植的运行时优化**（168 commits 通读筛选）：单工具超时表（`TOOL_TIMEOUTS`，防慢工具拖死整轮）、工具输出截断保头尾折叠中间（防 LLM 编造尾部结果）、LLM 回复 `calls` 多形态解析（tool/name + arguments/args）、FC 轮数放宽至 6 + 连续相同调用防死循环、`max_tokens<=0` 视为不设上限（防 400）、msglog 回溯上限 500→5000 提升召回。

---

## 🚀 完整部署指南

### ⚠️ 重要前提

- **QQ 账号等级 >= 16 级（建议开通 VIP）** — 低等级账号可能被腾讯风控拦截
- **运行环境推荐 Linux**（Ubuntu 20.04+ / Debian 10+ / CentOS 9+）— 生产环境首选
- Windows 也可运行，建议仅用于开发测试

---

### 第一步：安装 Python 3.10+

| 系统 | 说明 |
|------|------|
| Windows | [python.org/downloads](https://www.python.org/downloads/) — 安装时勾选 "Add Python to PATH" |
| Linux (apt) | `sudo apt install python3 python3-pip -y` |
| Linux (源码) | [python.org/downloads/source](https://www.python.org/downloads/source/) |

---

### 第二步：安装 NapCat（QQ 协议适配）

NapCat 负责与 QQ 服务器通信，提供 OneBot v11 WebSocket 接口。

**Linux 一键安装脚本：**

```bash
curl -o napcat.sh https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh && bash napcat.sh --docker n --cli y
```

安装完成后扫码登录并启动：

```bash
napcat                    # 打开 TUI 配置界面，扫码登录 QQ
napcat start <QQ号>       # 启动 Bot 服务（默认 WS 端口 8099）
```

**Windows 安装：**

前往 [NapCatQQ Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载 `NapCat.Win.zip` 一键包，解压运行。

**相关链接：**
| 资源 | 地址 |
|------|------|
| NapCat 官方 | [github.com/NapNeko/NapCatQQ](https://github.com/NapNeko/NapCatQQ) |
| 官方文档 | [napneko.github.io](https://napneko.github.io/guide/napcat) |
| 使用教程 | [jianer.sr-studio.cn](https://jianer.sr-studio.cn/NapCatQQ使用教程.html) |
| 一键安装脚本 | `curl -o napcat.sh https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh && bash napcat.sh --docker n --cli y` |

---

### 第三步：克隆项目并安装依赖

- Python 3.10+
- [NapCat](https://github.com/NapNeko/NapCatQQ) 或兼容的 OneBot v11 客户端
- DeepSeek API Key（[platform.deepseek.com](https://platform.deepseek.com/)）
- 智谱 API Key（[bigmodel.cn](https://open.bigmodel.cn/)），用于图片识别（可选）

### 安装

```bash
# 1. 克隆项目
git clone https://github.com/Trusler258/huanmeng-qqbot.git
cd huanmeng-qqbot

# 2. 安装依赖
pip install -r requirements.txt
playwright install chromium  # 卡片渲染需要

# 3. 配置 API Key
cp config/example.env config/.env
# 编辑 config/.env，填入 DeepSeek 和 智谱 密钥

# 4. 配置基础信息
# 编辑 config/bot_config.toml
#   - bot的qq号: 你的机器人 QQ 号
#   - 管理员的QQ号: 你的 QQ 号
# 编辑 config/adapter_config.toml
#   - group_list: 允许的群号列表

# 5. 启动
python main.py
```

### 配置 NapCat

NapCat 启动后默认在 `ws://127.0.0.1:8099/` 提供 WebSocket 服务，无需额外配置。如需修改端口，编辑 `config/adapter_config.toml`：

```toml
[napcat_server]
host = "127.0.0.1"
port = 8099
```

---



## 📡 第三方 API

| 功能 | API |
|------|-----|
| LLM 回复/判断 | [DeepSeek](https://platform.deepseek.com/) |
| 图片识别 | [智谱 AI](https://open.bigmodel.cn/)（可选）|
| 搜索 | DuckDuckGo（免费，无需 Key） |

---

## 📁 项目结构


```
huanmeng-qqbot/
├── main.py                # 入口
├── bot.py                 # 主循环、初始化、后台任务
├── config/                # 配置文件
│   ├── bot_config.toml    # 人设、模型、阈值
│   ├── adapter_config.toml # 白名单、群设置
│   ├── features.toml      # 功能开关
│   └── .env               # API 密钥（不上传 git）
├── core/                  # 核心基础设施
│   ├── pipeline.py        # 14 步消息处理管道
│   ├── dispatcher.py      # 事件分发
│   ├── config.py          # 配置管理
│   ├── context_manager.py # 上下文管理
│   └── logger.py          # 日志
├── services/              # 外部服务调用
│   ├── llm.py             # LLM 调用（DeepSeek/SiliconFlow）
│   ├── sender.py          # WebSocket 消息发送
│   └── image_api.py       # 图片识别
├── modules/               # 功能模块
│   ├── commands.py        # 指令系统（18+ 指令）
│   ├── memory.py          # 长时记忆
│   ├── stm.py             # 短时记忆
│   └── ...                # 天气、快递、五子棋等
├── data/                  # 数据文件
│   ├── templates/         # HTML 卡片模板
│   └── architecture.mermaid  # 架构图
└── utils/                 # 工具函数
```

### 消息处理管道

```
消息 → dispatcher → pipeline (14步)
  ├─ Step 1: 提示词注入拦截
  ├─ Step 2: 引用消息注入
  ├─ Step 3: 上下文写入 + 短时记忆
  ├─ Step 4: 指令拦截
  ├─ Step 5: 三级回复判断（关键词→粗判→精判）
  ├─ Step 6: 刷屏检测
  ├─ Step 7: 自动搜索
  ├─ Step 8: 记忆检索 + 好感度
  ├─ Step 9: LLM 多句生成
  └─ Step 10~13: 上下文回写 + 好感度更新 + 记忆保存
```

---

## 🔧 功能开关

编辑 `config/features.toml` 按需开启/关闭功能，`/~help` 自动隐藏已关闭的功能：

```toml
[features]
weather = false      # 天气
express = false      # 快递
wzq = false          # 五子棋
group_stats = false  # 群统计
recall_record = false # 撤回记录
tuf = false          # 音游
translate = false    # 翻译
countdown = false    # 倒计时
preset = false       # 提示词注入
```

---

## 🎭 自定义人设

角色由 `config/bot_config.toml` 三部分组成，**完全由你定义**——默认的猫娘只是示范：

```toml
核心人格 = """
# 这里是角色的内在性格、说话方式、行为准则
# 可以是任何角色：猫娘、龙娘、傲娇、冷酷、技术宅...
"""

侧面人格 = """
# 细微的性格侧面，如"有时候会钻牛角尖" "特别讨厌下雨天"
"""

固定身份 = """
名字：xxx | 种族：xxx | 年龄：xx | 身高/体重
外貌：xxx
性格：xxx
行为守则：xxx
"""
```

角色的好感度会随对话自然变化（0~100），不同档位对应不同的语气和态度。好感度系统同样与角色无关——无论你定义的是什么角色，它都会按照规则工作。

---

## 💾 记忆系统

| 层级 | 存储 | 容量 | 说明 |
|------|------|------|------|
| 瞬时 | 内存 | 15 条 | 当前对话上下文，FIFO |
| 短时 | JSON | 30 条 | 跨重启保留，滚动窗口 |
| 长时 | MD 文件 | 永久保存 | 模板化压缩，零幻觉，不设上限 |

溢出的短时记忆自动写入长时记忆。

---

## ⚙️ 高级配置

### 系统提示词注入

bot 启动时会自动组装完整系统提示词：

```
系统人设（bot_config.toml）
+ 格式规则（回复格式、防重复）
+ 好感度档位表
+ 自我认知（版本信息、运行配置）
+ [按需] 完整架构（仅用户询问时注入）
```

### 负载均衡

- **回复/判断/摘要模型**：全部 DeepSeek（deepseek-chat）
- **视觉模型**：智谱 glm-4v-plus（可选）

### 缓存策略

利用 DeepSeek 前缀缓存机制，system 提示词和锚点消息每次相同 → 缓存命中率 ~89%。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。在提交前请确保：

1. `python _check_all.py` 通过
2. 功能变动同步更新 `features.toml` 开关
3. 新增指令在 `/_CMD_FEATURES` 中注册

---

## 📄 开源协议

MIT License © 2024 Trusler

---

## 🔗 相关项目

- [NapCat](https://github.com/NapNeko/NapCatQQ) - QQ Bot 协议适配
- [DeepSeek](https://platform.deepseek.com/) - 大语言模型 API
- [智谱 AI](https://open.bigmodel.cn/) - 视觉模型 API

---

<p align="center">
  <img src="https://img.shields.io/badge/built_with-%E2%9D%A4%EF%B8%8F_DeepSeek_V4_Pro-2B5FD4?style=for-the-badge" />
</p>
