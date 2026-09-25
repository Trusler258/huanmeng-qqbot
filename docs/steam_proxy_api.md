# Steam API 代理 使用教程

> 服务地址：`https://steamapi.truslerweb.dpdns.org`
> 部署方式与踩坑见 `deploy/cf-steam-proxy/README.md`（Cloudflare Worker）
>
> 作者：**Trusler** · [@Trusler258](https://github.com/Trusler258) ｜ © 2026
>
> 文中的响应结构与耗时**均为实测**；`steamid`、`TOKEN` 等已替换成占位符。

## 一、它是干什么的

国内（尤其电信）直连 Steam 的两个域名时通时断，实测一天断三次、每次 20 分钟上下，
不通时每个请求要干等 25~30 秒超时。这个代理部署在 Cloudflare 边缘（境外），
把请求中转出去：

```
你的服务器（国内）  →  steamapi.truslerweb.dpdns.org（CF 边缘）  →  Steam
```

它只做一件事：**替你请求 Steam 并原样返回 JSON**。附带三个好处：

1. **合并请求** —— 一次 POST 可带多个上游调用，Worker 内并发执行（跨境每趟 1~2 秒，
   一个请求占一趟的话，一次业务几十趟反而更慢）
2. **自动分批** —— `appids` 传几十个也没关系，Worker 内部切批并发再合并
3. **key 不在你手上** —— Steam Web API key 存在 Worker secret 里，调用方无需持有

实测收益（92 款游戏价格）：直连 75,013 ms → 经代理 **3,873 ms**，
而且有价覆盖从 37/92 提到 **72/92**（代理侧到 Steam 更稳）。

## 二、快速开始

准备两样东西：**地址** 和 **令牌**（`PROXY_TOKEN`，部署时设定）。

```bash
export STEAM_PROXY="https://steamapi.truslerweb.dpdns.org"
export STEAM_PROXY_TOKEN="<你的 TOKEN>"

# 1) 先确认服务活着（不需要令牌）
curl -s "$STEAM_PROXY/" | python3 -m json.tool

# 2) 查一个玩家的在线状态
curl -s -X POST "$STEAM_PROXY/" \
  -H "Content-Type: application/json" \
  -H "X-Proxy-Token: $STEAM_PROXY_TOKEN" \
  -H "User-Agent: Mozilla/5.0" \
  -d '{"ops":[{"k":"api","p":"ISteamUser/GetPlayerSummaries/v2/",
               "q":{"steamids":"<STEAMID64>"}}]}' | python3 -m json.tool
```

第 1 步会返回：

```json
{"ok": true, "service": "steam-api-proxy", "host": "steamapi.truslerweb.dpdns.org",
 "has_steam_key": true, "auth_required": true, "ad_batch": 15, "max_ops": 40}
```

`has_steam_key` 是 `false` 说明 Worker 侧没配 key，任何 `k=api` 的 op 都会失败。

第 2 步返回（截断）：

```json
{"ok": true, "n": 1, "ms": 393,
 "r": [{"i": 0, "ok": true, "status": 200,
        "data": {"response": {"players": [{
            "steamid": "<STEAMID64>", "personaname": "<昵称>",
            "personastate": 1, "communityvisibilitystate": 3,
            "gameextrainfo": "A Dance of Fire and Ice"}]}}}]}
```

## 三、接口参考

### 3.1 `POST /` —— 批量代理

**请求头**

| 头 | 必填 | 说明 |
|---|---|---|
| `Content-Type` | 是 | `application/json` |
| `X-Proxy-Token` | 视部署而定 | 服务端配的令牌之一；不匹配返回 **401**<br>（支持多令牌、一人一个，便于单独撤销 —— 见 `deploy/cf-steam-proxy/README.md`） |
| `User-Agent` | **建议填** | 见下方「403 排查」—— CF 会拦 Python 默认 UA |

**请求体**

```json
{
  "ops": [
    {"k": "api",   "p": "ISteamUser/GetPlayerSummaries/v2/", "q": {"steamids": "xxx"}},
    {"k": "store", "p": "api/appdetails", "q": {"appids": "730,570", "filters": "price_overview"}}
  ]
}
```

| 字段 | 说明 |
|---|---|
| `k` | `"api"` → `api.steampowered.com`；`"store"` → `store.steampowered.com`。其他值报错 |
| `p` | 上游路径（**不含域名与开头的 `/`**），必须在白名单内（见第四节） |
| `q` | 查询参数（对象）。值为 `null`/`""` 的项会被丢掉 |

`k=api` 时**不用传 `key`** —— Worker 会注入。自己传了也会被忽略。
`k=store` 时会自动补 `cc=cn`、`l=schinese` 和浏览器 UA（不传就用默认，传了以你的为准）。

**响应体**

```json
{"ok": true, "n": 2, "ms": 283,
 "r": [
   {"i": 0, "ok": true,  "status": 200, "data": { ... }},
   {"i": 1, "ok": false, "status": 429, "error": "..."}
 ]}
```

| 字段 | 说明 |
|---|---|
| `ok` | **整批**是否正常处理（鉴权/格式问题才是 false） |
| `n` | op 数量 |
| `ms` | Worker 内部耗时（不含到你那台机器的网络往返） |
| `r` | 结果数组，**与请求的 ops 同序**，用 `i` 对应 |
| `r[].ok` | 该 op 是否成功 |
| `r[].data` | 上游返回的 JSON（成功时） |
| `r[].status` | 上游 HTTP 状态码 |
| `r[].cached` | 是否命中 Worker 边缘缓存（`k=store` 才有，实测意义不大，见第八节） |
| `r[].batches` | `k=store` 的 `appdetails` 自动分批后的批数 |

### 3.2 `GET /` —— 健康检查

不需要令牌，返回服务状态（`has_steam_key` / `auth_required` / `ad_batch` / `max_ops`）。
适合做监控探测。

## 四、白名单：能代理哪些上游

Worker **不是开放代理**，只放行下面这些前缀（其余一律 `path not allowed`）：

| `k` | 允许的路径前缀 |
|---|---|
| `api` | `ISteamUser/`、`IPlayerService/`、`ISteamUserStats/`、`ISteamNews/`、`ISteamApps/`、`ISteamAppService/`、`ISteamRemoteStorage/`、`ISteamWebAPIUtil/`、`IWishlistService/` |
| `store` | `api/appdetails`、`api/appreviews`、`api/storesearch`、`api/featuredcategories` |

> 需要放行别的接口时，改 `_worker.js` 的 `ALLOW` 常量后 `wrangler deploy`。

## 五、代码示例

### 5.1 Python + httpx（推荐，连接池复用）

```python
import asyncio
import httpx

BASE = "https://steamapi.truslerweb.dpdns.org"
TOKEN = "<你的 TOKEN>"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ★ 全局复用一个 client。实测同一趟调用的往返耗时：
#     urllib（每次新连接）   955 ~ 1103 ms
#     httpx（共享 client）   411 ~ 454  ms
#   差的就是每趟重新做 TLS 握手的那部分 —— 跨境场景下这一条最值钱
_client = httpx.AsyncClient(timeout=60, trust_env=False, verify=False,
                            headers={"User-Agent": UA})


async def steam_ops(ops):
    """一次请求打多个上游调用，返回与 ops 同序的结果列表"""
    r = await _client.post(BASE, json={"ops": ops},
                           headers={"X-Proxy-Token": TOKEN})
    r.raise_for_status()
    d = r.json()
    if not d.get("ok"):
        raise RuntimeError("代理拒绝: %s" % d.get("error"))
    return d["r"]


async def main():
    sid = "<STEAMID64>"
    rs = await steam_ops([
        {"k": "api", "p": "ISteamUser/GetPlayerSummaries/v2/", "q": {"steamids": sid}},
        {"k": "api", "p": "IPlayerService/GetSteamLevel/v1/", "q": {"steamid": sid}},
        {"k": "api", "p": "IPlayerService/GetBadges/v1/", "q": {"steamid": sid}},
    ])
    for r in rs:
        print(r["i"], r["ok"], list((r.get("data") or {}).keys()))

    # 92 款游戏价格：一次请求，Worker 内部自动分批
    appids = ["730", "570", "440", "977950", "1426210"]
    rs = await steam_ops([{"k": "store", "p": "api/appdetails",
                           "q": {"appids": ",".join(appids),
                                 "filters": "price_overview"}}])
    data = rs[0]["data"]
    for aid in appids:
        po = ((data.get(aid) or {}).get("data") or {}).get("price_overview") or {}
        print(aid, po.get("final_formatted") or "无价格（免费/锁区）")


asyncio.run(main())
```

### 5.2 curl（排查首选）

```bash
# 多 op 合并
curl -s -X POST "$STEAM_PROXY/" \
  -H "Content-Type: application/json" \
  -H "X-Proxy-Token: $STEAM_PROXY_TOKEN" \
  -H "User-Agent: Mozilla/5.0" \
  -d '{"ops":[
        {"k":"api","p":"IPlayerService/GetSteamLevel/v1/","q":{"steamid":"<STEAMID64>"}},
        {"k":"api","p":"IPlayerService/GetBadges/v1/","q":{"steamid":"<STEAMID64>"}}
      ]}' | python3 -m json.tool
```

### 5.3 Node / 浏览器

```js
const BASE = "https://steamapi.truslerweb.dpdns.org";
const TOKEN = "<你的 TOKEN>";

const res = await fetch(BASE, {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Proxy-Token": TOKEN },
  body: JSON.stringify({
    ops: [{ k: "api", p: "ISteamUser/GetPlayerSummaries/v2/",
            q: { steamids: "<STEAMID64>" } }],
  }),
});
const { ok, r } = await res.json();
console.log(ok, r[0].data.response.players[0].personaname);
```

> 浏览器直接调要注意 CORS：Worker 已允许 `OPTIONS` 预检与 `X-Proxy-Token` 头，
> 但**不建议把 TOKEN 放进前端代码**（会被任何人看到）。

## 六、`appids` 自动分批

`k=store` 的 `api/appdetails` 传超过 **15** 个 appid 时，Worker 自动切批并发再合并，
**对调用方永远是一次请求一份结果**：

```
传入 20 个 appid  →  Worker 内部批数 = 2，有价格 15/20，Worker 侧耗时 254 ms
```

为什么需要它：

- Steam 侧一次塞太多会超时或 400
- 分批若放在调用方，就变成多次跨境往返，又慢又容易部分失败

⚠️ **多 appid 时必须带 `filters=price_overview`** —— 配 `filters=basic` 会 **400**。
（单 appid 时 `filters=basic` 正常，所以这个坑本地拿一个游戏试永远试不出来。）

## 七、错误处理：两个层级

这是最容易写错的地方 —— **失败可能发生在两个层级**：

| 层级 | HTTP | 表现 | 含义 |
|---|---|---|---|
| **整批失败** | 4xx | `{"ok": false, "error": "..."}` | 鉴权、格式、op 超限 —— 什么都没执行 |
| **单个 op 失败** | **200** | `r[i].ok = false, r[i].error` | 上游拒绝或白名单外 —— 其他 op 可能已成功 |

实测的各种失败：

```
6.1 错误 token            -> HTTP 401  {"ok":false,"error":"unauthorized"}
6.2 白名单外 path         -> HTTP 200  {"ok":true,"r":[{"i":0,"ok":false,"error":"path not allowed: ../../etc/passwd"}]}
6.3 未知 kind             -> HTTP 200  {"ok":true,"r":[{"i":0,"ok":false,"error":"unknown kind: evil"}]}
6.4 op 超过 40 个         -> HTTP 400  {"ok":false,"error":"too many ops (max 40)"}
6.5 没带 User-Agent       -> HTTP 403  error code: 1010
```

**所以调用方不能只看 HTTP 状态**，必须逐个检查 `r[i].ok`：

```python
for r in rs:
    if not r.get("ok"):
        logger.warning("op %s 失败: %s", r.get("i"), r.get("error"))
        continue
    use(r["data"])
```

## 八、限制与注意事项

| 项 | 值 / 说明 |
|---|---|
| 单次 op 数 | 最多 **40** 个（CF 免费版每请求子请求上限 50，留了余量） |
| `appdetails` 单批 | Worker 内 15 个一批，全自动 |
| 免费额度 | 10 万请求/天 —— 一次卡片约 3~8 个请求，够用几个数量级 |
| 边缘缓存 | `caches.default` **实测没命中**（两次同样请求都 3.8s）。Cache API 是数据中心级、不是全局，请求可能落到不同 colo。**缓存请放在你自己那侧** |
| 数据新鲜度 | 代理不缓存业务数据，你拿到的是上游实时结果 |
| 日志 | `wrangler tail` 可看实时日志（会打印路径，不打印 key） |

## 九、排查

| 现象 | 原因 | 处理 |
|---|---|---|
| **403 `error code: 1010`** | CF 的浏览器完整性检查拦了你的 UA（`Python-urllib/3.x` 必中） | 请求带上正常 UA。`httpx`、`curl`、浏览器 UA 都能过 |
| **401 unauthorized** | `X-Proxy-Token` 缺失或与 Worker 的 `PROXY_TOKEN` 不一致 | 检查两边是否一致（`printf` 写 secret 时别带多余换行） |
| **`{"ok":false,"error":"STEAM_KEY not set on worker"}`** | Worker secret 没配 | `wrangler secret bulk`（**不要用 `secret put`**，Windows 上会静默失败） |
| **健康检查 `has_steam_key: false`** | 同上 | 同上 |
| **`path not allowed: xxx`** | 请求的接口不在白名单 | 查第四节，或改 `_worker.js` 的 `ALLOW` |
| **打开域名 404 / 连不上** | 用了 `*.workers.dev` 地址 | 必须用绑定的自定义域（`*.workers.dev` 国内被墙） |
| **整批慢（>10s）** | 通常不是代理的问题 | 先看 `ms` 字段：`ms` 小 = Worker 快、慢在你的网络上；`ms` 大 = 上游慢（Steam 侧限流） |

## 十、在自己的项目里接入（本项目做法）

本项目的接入点是 `services/steam_api.py`，设计成**配了就生效、不配就退回直连**：

```bash
# /root/bot/config/.env
STEAM_PROXY=https://steamapi.truslerweb.dpdns.org
STEAM_PROXY_TOKEN=<你的 TOKEN>
```

核心就三处：

1. `_get_json()` 里判断 `proxy_on()` → 把 `(url, params)` 拆成 `(kind, path)` 走代理
2. 需要批量时（如查 92 款价格）单独调 `_proxy_call(ops)` 一次发完
3. **共享一个 `httpx.AsyncClient`** —— 这条最容易被忽略。实测同一趟调用：
   urllib（每次新连接）955~1103 ms，httpx（共享 client）411~454 ms ——
   差的就是每趟重新握手的那部分

## 十一、自建 / 换域名

这套代理不绑定 Steam，改 `ALLOW` 白名单和 `API_HOST`/`STORE_HOST` 就能给别的上游用。
源码与部署步骤：`deploy/cf-steam-proxy/`（`_worker.js` + `wrangler.toml` + `README.md`）。

```bash
cd deploy/cf-steam-proxy
wrangler deploy                          # 改完 _worker.js 后重新部署
printf '{"STEAM_KEY":"...","PROXY_TOKEN":"..."}' > _secrets.json
wrangler secret bulk _secrets.json       # 改 secret（会自动触发新版本）
rm -f _secrets.json
wrangler tail                            # 实时日志
```
