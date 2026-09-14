# 洛花星雨 Nexus API 反扒文档

> 最后更新: 2026-09-14（第三次反扒）
> 本文件记录 `https://www.wdsj.net/nexus` 公开 API 的实测结构与变更，供 `services/wdsj_api.py` 及后续开发参考。

---

## 1. 基址与通用约定

| 项 | 值 |
|---|---|
| API 基址 | `https://www.wdsj.net/nexus` |
| 鉴权 | 无（公开 API） |
| 必须请求头 | `Referer: https://www.wdsj.net/nexus/stats`（缺失时部分端点被风控） |
| 响应码 | `{"code": 0, "message": "success", "data": {...}}`，`code != 0` 为业务错误 |
| 404 错误体 | Spring Boot 风格 `{"timestamp","status","error","path"}`（端点不存在或玩家不存在） |

**风控注意**：服务器 IP 曾因高频请求被 CrowdSec 封禁（HTTP 403）。项目内置 `WDSJ_PROXY` 环境变量走 Cloudflare Worker 中转；实测本地（广东家用宽带）直连正常。

---

## 2. 端点总览

| 端点 | 说明 | 状态 |
|---|---|---|
| `GET /api/v1/templates` | 战绩模板列表（含中文名、卡片背景） | ✅ |
| `GET /api/v1/leaderboards` | 排行榜领域/榜单列表 | ✅ |
| `GET /api/v1/leaderboards/{boardId}?type={period}` | 某榜单某周期的排名 | ✅ |
| `GET /api/v1/players/{identity}/templates/{template}` | 玩家指定模板战绩 | ✅ |
| `GET /api/v1/player-heads/{name}/head.png` | 玩家头像 | ✅ |
| `GET /api/v1/images/{snapshotKey}` | 战绩快照图（webp） | ✅ 新增 |
| `GET /api/v1/players/{identity}` | 玩家档案 | 404（未开放） |
| `GET /api/v1/guilds` `titles` `tags` `status` | 其他 | 404（未开放） |

---

## 3. 端点详解

### 3.1 战绩模板列表

```
GET /api/v1/templates
```

返回 `data`:

```json
{
  "templates": ["bedwars-stats", "knockbackwars-stats", ...],   // 18 个
  "templateItems": [
    {"id": "bedwars-stats", "displayName": "起床战争",
     "cardBackground": "/media/template-backgrounds/bwhub.webp"},
    ...
  ]
}
```

**18 个模板全表**（2026-09-14 实测）：

| id | displayName |
|---|---|
| bedwars-stats | 起床战争 |
| knockbackwars-stats | 击退战场 |
| arena-stats | 竞技场 |
| **arena-modern-stats** | **高版本竞技场** 🆕 |
| kitpvp-stats | 职业战争 |
| skywars-stats | 空岛战争 |
| thepit-stats | 天坑乱斗 |
| colorwars-stats | 色盲战争 |
| drawguess-stats | 你画我猜 |
| hideandseek-stats | 躲猫猫 |
| murdermystery-stats | 神秘谋杀 |
| uhc-stats | 极限生存 |
| watercube-stats | 星跃水立方 |
| buildbattle-stats | 建筑战争 |
| villagedefense-stats | 村庄保卫战 |
| naturaldisasters-stats | 天灾逃生 |
| csgo-stats | 反恐精英 |
| **luckypillars-stats** | **幸运之柱** 🆕 |

> 🆕 = v2（2026-08-27）之后新增。`arena-modern-stats` 与 `luckypillars-stats` 是本次发现。

`data` 另有字段（前端实际使用，探针早期忽略）：

```json
{
  "images": {"arena-stats": "arena-stats", "bedwars-stats": "bedwars-stats", ...},  // 模板→图片标识
  "defaultLocale": "zh-CN", "fallbackLocale": "zh-CN", "locale": "zh-CN",
  "allowedIdentityTypes": ["name", "nick"]
}
```

> ⚠️ `allowedIdentityTypes` 实测仅 `["name", "nick"]` —— **uid、uuid 均被服务端禁用**（见 §3.4）。

---

### 3.2 排行榜列表

```
GET /api/v1/leaderboards
```

返回 `data.boards`（**86 个**，分 26 个领域）。单体结构：

```json
{
  "id": "bedwars-wins",          // 唯一 id（中文或英文）
  "domain": "起床战争",           // 领域名
  "unit": "胜利",                // 数值单位
  "displayName": "起床战争胜利",
  "group": "起床战争",
  "periods": ["ALLTIME", "MONTHLY", "WEEKLY", "DAILY", "SEASON"],
  "limit": 10
}
```

**26 个领域**：起床战争(25) 幸运之柱(3) 击退战场(10) 职业战争(4) 空岛战争(9) 你建我猜(1) 你画我猜(3) 全服(1) 公会(1) 单方块(1) 在线时间(3) 情侣(1) 星跃水立方(1) 星雨阁(6) 梦落岛(1) 竞技场(1) 高版本竞技场(4) 色盲战争(2) 躲猫猫(1) 凌空乱斗(2) 天坑乱斗(1) 建筑战争(1) 神秘谋杀(1) 羊羊战争(1) 记忆速建(1) 财富(1)

> 第三次反扒对比（v2 后）：榜单 28 → **86**，领域 11 → **26**。新增领域：幸运之柱、你建我猜、单方块、星雨阁、梦落岛、高版本竞技场、凌空乱斗、羊羊战争、记忆速建、财富等。

---

### 3.3 排行榜详情

```
GET /api/v1/leaderboards/{boardId}?type={period}
```

`period` 取值（以 `periods` 字段为准）：

| 值 | 说明 | 实测 |
|---|---|---|
| `ALLTIME` | 总榜 | ✅ |
| `MONTHLY` | 月榜 | ✅ |
| `WEEKLY` | 周榜 | ✅ |
| `DAILY` | 日榜 | ✅ |
| `SEASON` | 赛季榜 | ✅ 2026-s3（26S3，ACTIVE） |
| `SEASONAL` | ❌ 不存在的拼写，返回 400 | 400 `排行榜 bedwars-wins 不支持周期: SEASONAL` |

返回 `data`:

```json
{
  "board": { ...见 3.2... },
  "type": "ALLTIME",
  "entries": [
    {"rank": 1, "owner": "dongtians", "value": "11055",
     "headImageUrl": "/api/v1/player-heads/dongtians/head.png"}
  ],
  "locale": "zh-CN",
  "currentSeason": {
    "id": "2026-s3", "displayName": "26S3赛季",
    "startsAt": 1782835200000, "endsAt": 1790784000000,
    "state": "ACTIVE", "version": 2
  }
}
```

**⚠️ 条目字段是 `owner`（玩家名），不是 `name`**。v2 时代还是 `name`，本次已改名。

---

### 3.4 玩家战绩

```
GET /api/v1/players/{identity}/templates/{template}
```

`identity` 前缀：

| 前缀 | 含义 | 实测 |
|---|---|---|
| `name:` | 玩家名 | ✅ 默认 |
| `nick:` | 昵称 | ✅ |
| `uuid:` | UUID | ❌ **服务端已禁用** |
| `uid:` | 数字 UID | ❌ **400「当前服务器不允许使用 uid 查询玩家」** |

> 注意：`templates` API 返回 `allowedIdentityTypes: ["name", "nick"]` —— uid/uuid 均已停用。项目代码 `IDENTITY_TYPES` 仍含 uid/uuid，属误导（保留仅作兼容）。

返回 `data`（v3 新增三件套）：

```json
{
  "player": {"uid": 801123, "name": "dongtians", "uuid": "2bb5543f-..."},
  "template": "bedwars-stats",
  "displayName": "起床战争",
  "locale": "zh-CN",
  "labels": {            // value 的中文标签映射 🆕
    "score": "积分", "kills": "击杀", "deaths": "死亡",
    "kd": "KD", "wins": "胜利", ...
  },
  "values": {            // 指标值（键与 labels 一一对应）
    "score": "237060", "kills": "109622", "deaths": "58526", ...
  },
  "headerCards": [       // 卡片式头部信息 🆕
    {"key": "player", "label": "玩家", "value": "dongtians", "hint": null},
    {"key": "uid", "label": "UID", "value": "4941", "hint": "UID不能代表注册顺序..."},
    {"key": "template", "label": "模板", "value": "起床战争", "hint": null},
    ...
  ],
  "summaryCards": [      // 概览卡片 🆕
    {"key": "score", "label": "积分", "value": "237060", "hint": null},
    {"key": "wins", "label": "胜利", "value": "11055", "hint": null},
    ...
  ],
  "snapshotKey": "20260914_4495f116",   // 🆕 快照标识
  "imageUrl": "/api/v1/images/20260914_4495f116"   // 🆕 快照图
}
```

**labels/values 数量随模板不同**：起床战争 33 项、幸运之柱/反恐精英/建筑战争 14 项、天灾逃生 5 项。

**快照图** `GET /api/v1/images/{snapshotKey}` → `image/webp`（实测 223KB），可直接下载当战绩图发送。

**前端反扒结论**（2026-09-14 抓 `_next` chunk 分析）：战绩页只调 `templates` + `players/{id}/templates/{tpl}` 两个端点，图片直接用返回的 `imageUrl`（无 `imageUrl` 时用 `snapshotKey` 拼 `/api/v1/images/...`）。**无隐藏端点**。

---

### 3.5 玩家头像

```
GET /api/v1/player-heads/{name}/head.png
```
- 玩家名（URL 编码）
- 返回 `image/png`（实测 552B 小图，864³ 级别像素头像）

---

## 4. 与项目现有代码的差异（2026-09-14）

| 差异 | 现有代码 | 线上实际 | 影响 |
|---|---|---|---|
| 模板 | 16 个 | **18 个** | `arena-modern-stats`、`luckypillars-stats` 缺失 |
| 榜单 | 别名覆盖 62/86 | 86 个 | **24 个新榜单查不到**（见 §5） |
| 条目字段 | 已用 `owner` ✅ | `owner` | 无（HTML 构建处正确） |
| 周期 | `ALLTIME/MONTHLY/WEEKLY/DAILY` | **+ `SEASON`** | `PERIOD_LABELS` 缺 SEASON |
| 战绩 | 只用 `headerCards` | labels/values/summaryCards/imageUrl | 摘要可更丰富 |
| uid 查询 | 声明支持 | **400 禁用** | 用 uid 会失败 |
| **标识类型** | `name/nick/uid/uuid` | **仅 `name`/`nick`** | **uuid 也被禁用**，`IDENTITY_TYPES` 误导 |
| 周期支持 | 假定每榜都全支持 | **按榜可选** | `periods` 字段为准（见 §3.3） |

---

## 5. 现有代码查不到的新榜单（24 个）

补齐 `BOARD_ALIASES` / `BOARD_SHORTHAND` 时参考（建议至少补常用项 + 中文简写）：

```
起床战争:   bedwars-overall(综合评分)  bedwars-deaths(死亡)
           bedwars-normal-win-streak(普通局最高连胜)  bedwars-moe-win-streak(萌局最高连胜)
           bedwars-oneblock-win-streak(单方块局最高连胜)  bedwars-solo-win-streak(单人局最高连胜)
幸运之柱:   luckypillars-wins  luckypillars-kills  luckypillars-total-survival-seconds
高版本竞技场: arena-modern-wins  arena-modern-kills  arena-modern-best-streak  arena-modern-global-elo
空岛战争:   skywars-best-win-streak  skywars-highest-loss-streak
凌空乱斗:   aerial-battle-kills  aerial-battle-loot-chests
天坑乱斗:   thepit-kills    建筑战争: buildbattle-wins
神秘谋杀:   murdermystery-kills     羊羊战争: sheepwars-kills
记忆速建:   speedbuilders-wins      财富: wealth-coins
职业战争:   kitpvp-deaths
```

中文 id 的榜单（如 `起床战争-击杀`）浏览器/API 均可用原样 `boardId` 查，现有代码 `resolve_board` 会原样透传，无需别名。

---

## 6. 反扒数据快照

实测原始 JSON 存于本地 `_probe/data/`（不入库）：

| 文件 | 内容 |
|---|---|
| `templates.json` | 18 模板 + templateItems |
| `boards.json` | 86 榜单全量 |
| `real_player_bedwars.json` | 真实玩家起床战争战绩（含 labels/values/cards） |

---

## 7. 待办建议

- [x] `TEMPLATES` 补 `arena-modern-stats`(高版本竞技场)、`luckypillars-stats`(幸运之柱)（v2.3.15 已做）
- [x] `PERIOD_LABELS` 补 `SEASON: 赛季`（v2.3.15 已做）
- [x] `IDENTITY_TYPES` 注释说明 uid/uuid 服务端禁用（v2.3.15 已做，保留兼容）
- [x] `BOARD_ALIASES`/`BOARD_SHORTHAND` 补齐 86 榜单全量别名（v2.3.15 已做，86/86 覆盖）
- [ ] 战绩摘要 `_build_wdsj_summary` 可扩展：用 `labels` 全量 + `imageUrl` 快照图
- [ ] `/~wdsj lb` 选周期时参考榜单 `periods` 字段（当前假定全支持，会 400）