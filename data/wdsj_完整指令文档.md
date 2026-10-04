# /~wdsj 完整参数指令文档

洛花星雨（Nexus）MC 服务器战绩查询指令的**全部参数、子指令、简写与作用**。
实现：`modules/commands.py::cmd_wdsj` + `services/wdsj_api.py` + `services/wdsj_tracker.py`。

---

## 一、主用法：战绩查询

```
/~wdsj <模式> <玩家名> [输出模式]
```

- 默认发**官方图片卡片**（下载官方 PNG 发群）
- 结尾加 `text` 看文字版完整数据
- 玩家名留空时自动用**绑定玩家名**（需先 `bd` 绑定）
- 已入 HEAVY_COMMANDS 旁路并发（不堵同群其他消息）

### 输出模式参数（写在末尾）

| 参数 | 作用 |
|------|------|
| （不写） | 默认：发官方图片卡片 |
| `img` / `pic` / `card` / `图片` | 等同默认（兼容旧写法） |
| `text` / `文字` / `notext` / `文本` | 发文字版完整数据（headerCards/summaryCards/全部字段） |

### 全部 18 种游戏模式（简写 → 完整 API ID）

| 简写 | 模式 | 完整 ID |
|------|------|---------|
| `bw` | 起床战争 | bedwars-stats |
| `kbw` | 击退战场 | knockbackwars-stats |
| `are` / `jjc` | 竞技场 | arena-stats |
| `kp` | 职业战争 | kitpvp-stats |
| `sw` | 空岛战争 | skywars-stats |
| `pit` | 天坑乱斗 | thepit-stats |
| `cw` | 色盲战争 | colorwars-stats |
| `dg` | 你画我猜 | drawguess-stats |
| `has` | 躲猫猫 | hideandseek-stats |
| `mm` | 神秘谋杀 | murdermystery-stats |
| `uhc` | 极限生存 | uhc-stats |
| `wc` | 星跃水立方 | watercube-stats |
| `bb` | 建筑战争 | buildbattle-stats |
| `vd` | 村庄保卫战 | villagedefense-stats |
| `nd` | 天灾逃生 | naturaldisasters-stats |
| `cs` / `csgo` | 反恐精英 | csgo-stats |
| `am` / `modern` | 高版本竞技场 | arena-modern-stats |
| `lp` / `pillar` | 幸运之柱 | luckypillars-stats |

- 也支持**自然说法**：`/~wdsj 幸运之柱 xxx`（模板中文名直接查）
- 玩家标识仅支持 `name`/`nick`（服务端已禁用 uid/uuid，实测 400）

---

## 二、me / dual：双模式横屏卡

```
/~wdsj me              （用绑定玩家）
/~wdsj <玩家名> me     （指定玩家，不用绑定）
/~wdsj dual <玩家名>   （兼容旧写法）
```

- 一张横屏图同时展示 **起床战争（33 项）+ 竞技场（17 项）** 全部字段
- 每字段配 Minecraft 原版图标，底部另有 18 项衍生比率指标
- 别名：`me` / `我` / `我的` / `my`（写在末尾）、`dual` / `double` / `双模式` / `双段`（写在开头）
- 误把模式名当玩家名（如 `/~wdsj bw me`）→ 自动提示正确用法

---

## 三、bd：绑定玩家名

```
/~wdsj bd <玩家名>     绑定（绑定后 /~wdsj bw 不带名字即可查）
/~wdsj bd              查看自己绑定的玩家名
/~wdsj bd list / 列表  列出所有人绑定的玩家名（按名字排序去重）
```

- 存储：`data/wdsj_player_name.json`（QQ → 玩家名）
- 绑定后所有支持"玩家名留空"的子指令（主查询/me/dual/trend）自动用绑定名

---

## 四、lb：排行榜

```
/~wdsj lb <榜单名> [周期] [img]
```

- 榜单名支持**双词**（`bw win`）和**单词**（`beds`）两种写法
- 末尾加 `img`（或 `pic`/`card`/`图片`）→ 发卡片图片（Pillow 直绘，失败回退 Chromium）
- 不加 `img` → 文字版前 N 名列表
- **周期智能对齐**：榜单只支持部分周期时自动降级，避免 API 400

### 周期参数

| 写法 | 简写 | 含义 |
|------|------|------|
| `alltime` | `all` | 总榜（默认） |
| `monthly` | `month` | 月榜 |
| `weekly` | `week` | 周榜 |
| `daily` | `day` | 日榜 |
| `season` | — | 赛季榜（部分榜单支持） |

### 排行榜榜单全表

**起床战争 `bw`**

| 简写 | 双词 | 指标 |
|------|------|------|
| `bw win` | — | 胜利 |
| `bw kill` / `bwk` | — | 击杀 |
| `bw beds` / `beds` / `bwb` | — | 摧床 |
| `bw fk` / `bwfk` | — | 最终击杀 |
| `bw 1k` / `bw1k` | — | 首杀 |
| `bw void` | — | 自走虚空 |
| `bw egg` | — | 鸡蛋击杀 |
| `bw fb` | — | 火球击杀 |
| `bw de` / `bwde` | — | 死亡 |
| `bw score` / `overall` / `bwscore` | — | 总分 |
| `bw ns` / `bwns` | — | 普通连胜 |
| `bw ms` / `bwms` | — | 起床连胜 |
| `bw os` / `bwos` | — | 单方块连胜 |
| `bw ss` / `bwss` | — | 单挑连胜 |

**击退战场 `kbw`**

| 简写 | 指标 |
|------|------|
| `kbw kill` / `kbwk` | 击杀 |
| `kbw dead` | 死亡 |
| `kbw tnt` / `tnt` / `kbwt` | TNT 击杀 |
| `kbw arrow` | 弓箭击杀 |
| `kbw rod` | 鱼竿击杀 |
| `kbw jp` | 跳板击杀 |

**空岛战争 `sw`**

| 简写 | 指标 |
|------|------|
| `sw kill` / `swk` | 击杀 |
| `sw win` / `sww` / `wins` | 胜利 |
| `sw dead` | 死亡 |
| `sw 1k` | 首杀 |
| `sw s` / `sws` | 最佳连胜 |
| `sw l` / `swl` | 最高连败 |

**高版本竞技场 `am`**

| 简写 | 指标 |
|------|------|
| `am w` / `amw` | 胜利 |
| `am k` / `amk` | 击杀 |
| `am s` / `ams` | 最佳连胜 |
| `am elo` / `amelo` | 全服 ELO |

**幸运之柱 `lp`**

| 简写 | 指标 |
|------|------|
| `lp w` / `lpw` | 胜利 |
| `lp k` / `lpk` | 击杀 |
| `lp t` / `lpt` | 总存活秒数 |

**其他**

| 简写 | 指标 |
|------|------|
| `pt` | 在线时长（分钟） |
| `cp` | 情侣亲密值 |
| `title` | 全服称号数量 |
| `guild` | 公会总贡献 |
| `dg win` | 你画我猜获胜 |
| `cw win` | 色盲战争获胜 |
| `has win` | 躲猫猫获胜 |
| `kp kill` / `kp xp` | 职业击杀 / 经验 |
| `kpd` | 职业战争死亡 |
| `pit k` / `pitk` | 天坑击杀 |
| `bb w` / `bbw` | 建筑战争获胜 |
| `mm k` / `mmk` | 神秘谋杀击杀 |
| `ab k` / `abk` | 凌空乱斗击杀 |
| `ab l` / `abl` | 凌空乱斗搜箱 |
| `sheep` | 绵羊战争击杀 |
| `sbw` | 极速建筑师获胜 |
| `coins` | 金币 |

---

## 五、boards：排行榜简写速查

```
/~wdsj boards   （别名：/~wdsj 榜单 / 榜）
```

按游戏分组的简写速查文字表（起床/击退/空岛/职业/其他），不调 API。

---

## 六、list：模式别名速查

```
/~wdsj list
```

列出全部 13+ 种游戏模式（简写 => 完整 ID）+ 排行榜别名表。

---

## 七、rank：群内绑定排行

```
/~wdsj rank [指标]
```

- 指标默认 `bw_kills`，可选：`bw_kills` / `bw_wins` / `bw_finals` / `bw_deaths` / `arena_kills`
- 统计**群内已绑定玩家**的战绩排名，图片返回
- 先回执"正在生成绑定排行"，再发图

---

## 八、daily：日榜（今日击杀排名）

```
/~wdsj daily [are] [日期] [send]
```

| 参数 | 作用 |
|------|------|
| （不写） | 今天的起床战争日榜（默认） |
| `are` / `arena` / `竞技` / `竞技场` | 竞技场日榜（冷蓝模板；起床是暖橙模板） |
| `7-20` / `07-20` / `2026-07-20` / `0720` | 查指定日期（过去日期自动用跨天模式：昨天 0:01 → 今天 0:01） |
| `send` / `push` / `推送` / `发送` | 推送模式：强制跨天取昨天数据，直接发当前群（仅群聊可用） |

- 榜单每天 **0:01 / 4:01 / 8:01 / 12:01 / 16:01 / 20:01** 产出；无数据时提示下一轮时间
- 渲染：优先 Pillow 直绘（测试项 `pillow_card` 控制，`/~key pillow_card off` 可回退），失败自动回退 Chromium
- 非推送模式先回执"正在生成 X 日榜"

---

## 九、trend：趋势折线图

```
/~wdsj trend <玩家名> [指标]
/~wdsj trend [指标]        （用绑定玩家）
```

| 指标 | 含义 |
|------|------|
| `bw_kills`（默认） | 击杀 |
| `bw_wins` | 胜场 |
| `bw_finals` | 最终击杀 |
| `bw_deaths` | 死亡 |
| `arena_kills` | 竞技场击杀 |

- 需要至少 2 天的采集记录，不够会提示

---

## 十、collect：手动采集（仅管理员）

```
/~wdsj collect
```

- 手动触发一轮战绩采集（正常由定时任务自动跑）
- 返回耗时 + 失败名单（3 轮重试后仍未采集到的玩家）
- 非管理员提示"只有管理员才能手动采集"

---

## 十一、help：帮助卡片

```
/~wdsj help     （或 /~wdsj 不带参数）
```

- 渲染 `data/wdsj_help.md` 为图片卡片发群（Chromium 渲染）
- 渲染失败时回退纯文字帮助

---

## 十二、管理端（相关联）

```
/~owner wdsj groups show            查看日榜推送群
/~owner wdsj groups set <群号,...>  设日榜推送群
/~owner wdsj groups clear           清除
```

- 配置存 `bot_config.toml [wdsj] target_groups`，热更新内存
- 日榜定时任务按该列表推送

---

## 十三、底层机制注记

| 项 | 说明 |
|----|------|
| 网络策略 | 直连优先，仅网络异常 / 403 / 5xx 回退代理（实测快 2.3-2.7 倍） |
| 战绩缓存 | 图片模式下载官方 PNG 后把文字摘要存入 `wdsj_cache`——群里引用该图时直接返回文字数据，不调视觉模型 |
| 渲染引擎 | 日榜/排行榜卡优先 Pillow（实测 79ms vs Chromium 872ms），失败自动回退 Chromium；测试项 `pillow_card` 一键切换 |
| 并发 | HEAVY_COMMANDS 旁路并发，不占用本群 worker |
| 采集 | 定时任务自动采集绑定玩家战绩（每日多轮），`collect` 可手动触发 |
