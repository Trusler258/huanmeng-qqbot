/**
 * Steam API 代理（Cloudflare Worker）
 *
 * 为什么需要：bot 服务器在河南郑州（电信），直连 api.steampowered.com /
 * store.steampowered.com 时通时断（实测一天断三次、每次 20 分钟上下），
 * 不通时每个请求要干等 25~30s 超时。本 Worker 在 CF 边缘（境外）中转，
 * 服务器只需访问本域名。
 *
 * ── 用法 ─────────────────────────────────────────────
 *   GET  /                                  健康检查（返回是否配了 key）
 *   POST /                                  批量代理
 *     Header: X-Proxy-Token: <PROXY_TOKEN>  （Worker 配了才校验）
 *     Body:   {"ops":[{"k":"api","p":"ISteamUser/GetPlayerSummaries/v2/","q":{...}}, ...]}
 *     返回:   {"ok":true,"n":1,"r":[{"i":0,"ok":true,"status":200,"data":{...}}]}
 *
 *   k = "api"    → https://api.steampowered.com   （自动注入 STEAM_KEY）
 *   k = "store"  → https://store.steampowered.com （自动带 UA / cc=cn / l=schinese）
 *   k = "community" → https://steamcommunity.com （返回文本，白名单见 ALLOW.community）
 *
 *   另有路径前缀形式（便于 curl / 浏览器直接试，令牌用 ?token= 或 X-Proxy-Token）：
 *     GET /steamcommunity/<path>     如 /steamcommunity/user/1467315295
 *                                     或 /steamcommunity/profiles/76561199...
 *
 * ── 设计要点（都是踩过的坑）────────────────────────────
 *   1. 一次请求带多个 op，Worker 内并发执行 —— 跨境每次往返 1~2s，
 *      若一个 Steam 请求一个 Worker 请求，一张卡片几十次往返反而更慢。
 *   2. appdetails 传超过 AD_BATCH 个 appid 时，Worker 内部自动分批并发再合并：
 *      对调用方始终是"一次请求一份合并结果"，同时避开 Steam 侧单请求过大
 *      导致的超时/400（实测多 appid 必须带 filters=price_overview，
 *      配 filters=basic 会 400 —— 这也是为什么这里保留 filters 透传）。
 *   3. Steam key 只存在 Worker secret（env.STEAM_KEY），服务器不再持有。
 *   4. store 响应走 CF 边缘缓存（caches.default，默认 24h）—— 游戏名/价格
 *      一天不会变，命中即毫秒级返回，也顺带减轻 Steam 侧压力。
 *   5. path 走白名单，Worker 不是开放代理（避免被人拿去白嫖或探测）。
 *
 * ── 部署 ─────────────────────────────────────────────
 *   cd deploy/cf-steam-proxy
 *   wrangler secret put STEAM_KEY     # Steam Web API key
 *   wrangler secret put PROXY_TOKEN   # 自定义令牌（防白嫖，可选但强烈建议）
 *   wrangler deploy
 *   详见同目录 README.md
 */

const API_HOST = "https://api.steampowered.com";
const STORE_HOST = "https://store.steampowered.com";
// 用户要求：加 /steamcommunity/ 路径前缀，让国内服务器也能取社区页
// （个人资料页 / 好友代码解析等）。社区站是公开站点，但仍要求带令牌，避免变成开放代理。
const COMMUNITY_HOST = "https://steamcommunity.com";

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

// path 白名单（只允许代理这些接口，Worker 不是开放代理）
const ALLOW = {
  api: [
    /^ISteamUser\//,
    /^IPlayerService\//,
    /^ISteamUserStats\//,
    /^ISteamNews\//,
    /^ISteamApp(s|sService)\//,
    /^ISteamRemoteStorage\//,
    /^ISteamWebAPIUtil\//,
    /^IWishlistService\//,
  ],
  store: [
    /^api\/appdetails$/,
    /^api\/appreviews$/,
    /^api\/storesearch$/,
    /^api\/featuredcategories$/,
  ],
  // 社区站白名单：只放读页面用的路径。
  // ⚠️ 故意**不**放行 actions/（那是加好友等有副作用的入口）。
  community: [
    /^profiles\//,
    /^id\//,
    /^user\//,
    /^app\//,
    /^market\//,
    /^sharedfiles\//,
  ],
};

const AD_BATCH = 15;      // appdetails 单批 appid 上限（再多 Steam 侧会超时）
const MAX_OPS = 40;       // 单次请求最多 op 数（免费版每请求 50 个子请求）
const COMMUNITY_TEXT_MAX = 200000;  // 社区页文本返回上限（约 200KB）
const CACHE_TTL = 86400;  // store 响应的边缘缓存秒数

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

/**
 * 允许的令牌集合（支持多令牌：一人一个，便于单独撤销）
 *   PROXY_TOKEN   主令牌（单个，向后兼容；也可写逗号分隔的多个）
 *   PROXY_TOKENS  额外令牌，逗号分隔 —— 给朋友用的放这里
 *
 * 每一项支持 `token:备注` 形式（备注只用于人看/面板展示，鉴权只取冒号前那段），
 * 例如：`friend-abc...:小明,friend-def...:测试机`。
 * 令牌本身不含冒号（生成时用 `friend-` + hex），所以切分安全。
 *
 * 两者都为空 = 不校验（仅供调试，正式部署务必配置）
 */
function allowedTokens(env) {
  const out = [];
  const push = (v) => String(v || "").split(",").forEach((s) => {
    const t = s.trim();
    if (!t) return;
    const token = t.split(":")[0].trim();
    if (token && out.indexOf(token) === -1) out.push(token);
  });
  push(env.PROXY_TOKEN);
  push(env.PROXY_TOKENS);
  return out;
}

/** 读边缘缓存；未命中则请求并写回缓存 */
async function cachedText(url, headers, ctx) {
  const cache = caches.default;
  const key = new Request(url, { method: "GET" });
  const hit = await cache.match(key);
  if (hit) {
    return { text: await hit.text(), status: hit.status, cached: true };
  }
  const resp = await fetch(url, { headers });
  const text = await resp.text();
  if (resp.ok && text.length > 2) {
    ctx.waitUntil(cache.put(key, new Response(text, {
      status: resp.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "public, max-age=" + CACHE_TTL,
      },
    })));
  }
  return { text, status: resp.status, cached: false };
}

function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch (e) {
    return null;
  }
}

async function handleOp(op, env, ctx) {
  const kind = String((op && op.k) || "api").toLowerCase();
  const path = String((op && op.p) || "").replace(/^\/+/, "");
  const q = (op && op.q && typeof op.q === "object") ? op.q : {};

  const rules = ALLOW[kind];
  if (!rules) return { ok: false, error: "unknown kind: " + kind };
  if (!rules.some((re) => re.test(path))) {
    return { ok: false, error: "path not allowed: " + path };
  }

  const host = kind === "store" ? STORE_HOST
    : (kind === "community" ? COMMUNITY_HOST : API_HOST);
  const params = new URLSearchParams();
  for (const k of Object.keys(q)) {
    const v = q[k];
    if (v === undefined || v === null || v === "") continue;
    params.set(k, String(v));
  }

  const headers = {};
  if (kind === "api") {
    if (!params.has("key")) {
      if (!env.STEAM_KEY) return { ok: false, error: "STEAM_KEY not set on worker" };
      params.set("key", env.STEAM_KEY);
    }
  } else {
    headers["User-Agent"] = UA;
    if (kind === "store") {
      if (!params.has("cc")) params.set("cc", "cn");
      if (!params.has("l")) params.set("l", "schinese");
    }
  }

  // appdetails 大列表：内部分批并发再合并
  if (kind === "store" && path === "api/appdetails") {
    const ids = String(params.get("appids") || "").split(",").filter(Boolean);
    if (ids.length > AD_BATCH) {
      const chunks = [];
      for (let i = 0; i < ids.length; i += AD_BATCH) {
        chunks.push(ids.slice(i, i + AD_BATCH));
      }
      const parts = await Promise.all(chunks.map((c) => {
        const p2 = new URLSearchParams(params);
        p2.set("appids", c.join(","));
        return cachedText(host + "/" + path + "?" + p2.toString(), headers, ctx);
      }));
      const merged = {};
      let anyOk = false;
      for (const part of parts) {
        const obj = parseJson(part.text);
        if (obj && typeof obj === "object") {
          for (const kk of Object.keys(obj)) merged[kk] = obj[kk];
          anyOk = true;
        }
      }
      return { ok: anyOk, status: 200, data: merged, batches: chunks.length };
    }
  }

  const url = host + "/" + path + "?" + params.toString();

  if (kind === "store") {
    const part = await cachedText(url, headers, ctx);
    const data = parseJson(part.text);
    return {
      ok: data !== null,
      status: part.status,
      data,
      cached: part.cached,
      raw: data === null ? part.text.slice(0, 300) : undefined,
    };
  }

  const resp = await fetch(url, { headers });
  const text = await resp.text();

  // 社区页是 HTML，不是 JSON：原样返回（截断上限防爆响应体）
  if (kind === "community") {
    return {
      ok: resp.status === 200,
      status: resp.status,
      len: text.length,
      text: text.slice(0, COMMUNITY_TEXT_MAX),
      truncated: text.length > COMMUNITY_TEXT_MAX,
    };
  }

  const data = parseJson(text);
  return {
    ok: data !== null,
    status: resp.status,
    data,
    raw: data === null ? text.slice(0, 300) : undefined,
  };
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Headers": "content-type, x-proxy-token",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        },
      });
    }

    // ★ /steamcommunity/<path> —— 路径前缀直通社区站（用户要求的形式）
    //   令牌：X-Proxy-Token 头，或 ?token= / ?t=（便于 curl / 浏览器直接试）
    //   转发时会**剥掉** token/t 参数，不带给上游。
    if (url.pathname === "/steamcommunity" || url.pathname.startsWith("/steamcommunity/")) {
      const allowedP = allowedTokens(env);
      if (allowedP.length) {
        const got = (request.headers.get("x-proxy-token")
          || url.searchParams.get("token")
          || url.searchParams.get("t") || "").trim();
        if (allowedP.indexOf(got) === -1) {
          return json({ ok: false, error: "unauthorized" }, 401);
        }
      }
      const rest = url.pathname.replace(/^\/steamcommunity\/?/, "");
      if (!rest) {
        return json({ ok: false, error: "用法：/steamcommunity/<path>，如 /steamcommunity/user/1467315295" }, 400);
      }
      if (!ALLOW.community.some((re) => re.test(rest))) {
        return json({ ok: false, error: "path not allowed: " + rest }, 403);
      }
      const q2 = new URLSearchParams(url.searchParams);
      q2.delete("token");
      q2.delete("t");
      const qs = q2.toString();
      const target = COMMUNITY_HOST + "/" + rest + (qs ? "?" + qs : "");

      // 诊断分支：?debug=1 时只回报 URL 与上游抓取结果，便于定位 500
      if (url.searchParams.get("debug")) {
        const info = { target: target, rest: rest, qs: qs };
        try {
          const r0 = await fetch(COMMUNITY_HOST + "/robots.txt",
            { headers: { "User-Agent": UA } });
          info.robots_status = r0.status;
          info.robots_len = (await r0.text()).length;
        } catch (e) {
          info.robots_error = String((e && e.message) || e);
          info.robots_name = String((e && e.name) || "");
        }
        try {
          const r1 = await fetch(target, { headers: { "User-Agent": UA }, redirect: "follow" });
          info.up_status = r1.status;
          const t1 = await r1.text();
          info.up_len = t1.length;
          info.up_head = t1.slice(0, 200);
        } catch (e) {
          info.up_error = String((e && e.message) || e);
          info.up_name = String((e && e.name) || "");
        }
        return json(info);
      }

      try {
        const up = await fetch(target, {
          headers: { "User-Agent": UA, Accept: "text/html,application/json;q=0.9,*/*;q=0.8" },
          redirect: "follow",
        });
        const ct = up.headers.get("content-type") || "text/html; charset=utf-8";
        // 读成文本再包一层：直接透传 up.body 时若上游是 3xx/304/204（无 body 状态），
        // 用 new Response(body, {status}) 会抛 "Response with null body status cannot have body"。
        const text = await up.text();
        return new Response(text, {
          status: up.status === 200 ? 200 : up.status,
          headers: {
            "content-type": ct,
            "x-proxy-upstream-url": target,
            "x-proxy-upstream-status": String(up.status),
            "cache-control": "no-store",
          },
        });
      } catch (e) {
        // 把真实原因带出来（否则只剩一个 500，没法排查）
        return json({
          ok: false,
          error: String((e && e.message) || e),
          name: String((e && e.name) || ""),
          stack: String((e && e.stack) || "").slice(0, 600),
          target: target,
        }, 502);
      }
    }

    if (request.method === "GET") {
      const cachedTest = url.searchParams.get("ping");
      return json({
        ok: true,
        service: "steam-api-proxy",
        host: url.hostname,
        has_steam_key: !!env.STEAM_KEY,
        auth_required: allowedTokens(env).length > 0,
        tokens: allowedTokens(env).length,
        ad_batch: AD_BATCH,
        max_ops: MAX_OPS,
        community_prefix: "/steamcommunity/",
        ping: cachedTest || undefined,
      });
    }

    if (request.method !== "POST") {
      return json({ ok: false, error: "POST only" }, 405);
    }

    const allowed = allowedTokens(env);
    if (allowed.length) {
      const got = (request.headers.get("x-proxy-token") || "").trim();
      if (allowed.indexOf(got) === -1) {
        return json({ ok: false, error: "unauthorized" }, 401);
      }
    }

    let body = null;
    try {
      body = await request.json();
    } catch (e) {
      return json({ ok: false, error: "bad json body" }, 400);
    }

    const ops = (body && Array.isArray(body.ops)) ? body.ops : [];
    if (!ops.length) return json({ ok: false, error: "no ops" }, 400);
    if (ops.length > MAX_OPS) {
      return json({ ok: false, error: "too many ops (max " + MAX_OPS + ")" }, 400);
    }

    const started = Date.now();
    const results = await Promise.all(ops.map(async (op, i) => {
      try {
        const r = await handleOp(op, env, ctx);
        return Object.assign({ i: i }, r);
      } catch (e) {
        return { i: i, ok: false, error: String((e && e.message) || e) };
      }
    }));

    return json({ ok: true, n: results.length, ms: Date.now() - started, r: results });
  },
};
