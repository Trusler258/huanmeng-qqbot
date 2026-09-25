# steamapi.truslerweb.dpdns.org —— Steam API 代理 Worker

给**国内服务器**中转 Steam 请求用的 Cloudflare Worker。

## 为什么需要

bot 服务器在河南郑州（电信），直连 `api.steampowered.com` / `store.steampowered.com`
时通时断（实测一天断三次、每次 20 分钟上下，连本地广东也同时不通）。不通时每个请求
要干等 25~30 秒超时。

实测收益（92 款游戏价格）：

| 项 | 直连 | 经 Worker |
|---|---|---|
| 92 款价格一次查完 | 75,013 ms | 3,873 ms |
| 有价覆盖 | 37/92 | 72/92 |
| 完整卡片取数（冷态） | 80,520 ms | 5,920 ms |
| 完整卡片取数（SWR 热态） | — | 450 ms |

## 用法

```
GET  /                                   健康检查（返回 has_steam_key / auth_required）
POST /                                   批量代理
  Header: X-Proxy-Token: <PROXY_TOKEN>
  Body:   {"ops":[{"k":"api","p":"ISteamUser/GetPlayerSummaries/v2/","q":{...}}, ...]}
  返回:   {"ok":true,"n":1,"ms":162,"r":[{"i":0,"ok":true,"status":200,"data":{...}}]}
```

- `k = "api"` → `https://api.steampowered.com`，**key 由 Worker 注入**（服务器不再持有）
- `k = "store"` → `https://store.steampowered.com`，自动带 UA / `cc=cn` / `l=schinese`
- `appids` 超过 15 个时 Worker **内部自动分批并发再合并**，对调用方始终是"一次请求一份结果"
- path 走白名单（只允许 ISteamUser / IPlayerService / ISteamUserStats / ISteamNews /
  ISteamRemoteStorage / IWishlistService / ISteamWebAPIUtil / ISteamApps + store 的
  appdetails、appreviews、storesearch、featuredcategories）
- 单次最多 40 个 op（免费版每请求 50 个子请求的上限）

## 部署

```bash
cd deploy/cf-steam-proxy
wrangler deploy                       # 首次部署：自动建 DNS + 签证书
# secret 用 bulk 写（单个 put 在 Windows 上会撞 libuv 断言，看似失败其实没写进去）
printf '{"STEAM_KEY":"<key>","PROXY_TOKEN":"<主令牌>","PROXY_TOKENS":"<额外令牌:备注>,..."}' > _secrets.json
wrangler secret bulk _secrets.json
rm -f _secrets.json
```

### 多令牌（一人一个，便于单独撤销）

| 变量 | 用途 |
|---|---|
| `PROXY_TOKEN` | 主令牌（本项目 bot 服务器用） |
| `PROXY_TOKENS` | **额外令牌，逗号分隔** —— 给朋友 / 别的机器的放这里 |

两项都支持 `token:备注` 形式，备注只给人看（鉴权只取冒号前那段）：

```
PROXY_TOKENS = friend-d328bb8d...:朋友,friend-1a2b3c...:测试机
```

要停掉某个人，把他那一段从 `PROXY_TOKENS` 里删掉再 `secret bulk` 即可 ——
**不影响其他人**，也不用改主令牌。

`GET /` 会返回 `tokens: N`（只报数量，不泄露值），便于确认改对了。

⚠️ Cloudflare **不允许读取已有 secret 的值**（API 只返回名字）。所以「谁拿了哪个令牌」
必须自己存台账，别指望从 CF 侧查回来。

客户端（bot）侧配两个环境变量即可，**不配则一切照旧走直连**：

```
STEAM_PROXY=https://steamapi.truslerweb.dpdns.org
STEAM_PROXY_TOKEN=<与 Worker 的 PROXY_TOKEN 相同>
```

写进 `/root/bot/config/.env`（config 在 `/~update` 的保护清单里，不会被覆盖）。

## 坑（都是实测踩出来的）

| 坑 | 现象 | 解法 |
|---|---|---|
| **不能用 `*.workers.dev`** | 国内 DNS 污染 / SNI 阻断，直接打不开 | 必须绑自定义域，`routes` 里写 `custom_domain = true` |
| **`wrangler secret put` 在 Windows 上** | 报 `Assertion failed: !(handle->flags & UV_HANDLE_CLOSING)`，**stdin 方式实际没写成功**（健康检查仍 `has_steam_key: false`） | 改用 `wrangler secret bulk <file.json>` |
| **wrangler 不认 MSYS 路径** | `wrangler secret bulk /tmp/x.json` → `ENOENT: G:\tmp\x.json`（原生 Windows 程序按盘符解析） | 用项目内**相对路径** |
| **CF 边缘拦 Python 默认 UA** | urllib 的 `Python-urllib/3.x` 被 Bot Fight Mode 拦成 **403**；`curl` 与 `python-httpx/...` 正常 | 调用方带正常 UA（bot 侧用 httpx，天然没问题；探针脚本用 urllib 才要显式设置） |
| **`caches.default` 期望落空** | 两次同样请求都是 3.8s，边缘缓存**没命中**（不同请求可能落到不同 colo，Cache API 不是全局的） | 别指望它；缓存放在调用方（bot 侧已有 24h 本地缓存）更可靠 |

## 维护

```bash
wrangler deployments list          # 版本历史
wrangler tail                      # 实时日志
wrangler deploy                    # 改完 _worker.js 后重新部署
```

改 Worker 源码后**必须重新 deploy**，仅改 secret 不用（`secret bulk` 会自动触发新版本）。
