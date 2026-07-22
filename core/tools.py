"""
工具调用代理 (Tool-Agent)
- LLM 自动判断是否需要调用工具
- 支持: wdsj战绩, search搜索, box快递
- 天气/地震等实时查询已由 LLM 的 [CALL:~xxx] 系统接管
- 工具结果注入上下文后重新生成回复
"""
from __future__ import annotations

import re
from core.logger import get_logger

logger = get_logger("tools")

# ─── 工具定义（注入到 system prompt） ───
TOOL_DEFINITIONS = """
【可用工具与规则】
- 战绩查询 (wdsj) / 快递查询 (box)：仅当用户明确说出对应指令时才由系统执行。
- 搜索/天气/其他功能：由 [CALL:~xxx] 系统处理，仅当用户明确要求时才调用。

你不需要手动调用这些工具，系统会在用户提出明确需求后自动处理。
重要规则：
1. 不要主动提议使用任何工具。用户说"好累""喵喵喵""换键位了"不是工具请求。
2. 用户说"帮我搜""查一下""发个地震"等明确语句时，才用对应的 CALL 指令。
3. 如果没有注入搜索结果，或者搜索结果显示不相关/不足：
   真诚地说"这个我也不太清楚喵~"或"搜到的东西不太对，主人换种问法试试？"
   严禁在没有可靠来源时编造具体数据、数字、规格。
   你是一个16岁的猫娘助手，诚实比万能更重要。
"""

# ─── 判断是否需要工具调用的 prompt ───
TOOL_JUDGE_PROMPT = """判断以下用户消息是否需要调用工具来查询信息。
需要搜索的类型：
- 实时数据：战绩/快递/新闻/价格
- 知识推荐：求推荐/有什么mod/什么插件/什么配置/怎么解决
- 事实查询：某个东西是什么/xx怎么用/xx在哪下载
不要搜索的情况（重点）：
- 哲学/人生/情感问题："人活着为了什么""爱是什么"
- 问机器人自己的看法："你觉得""你认为""你怎么看"
- 用户说"不要搜索/不要查/我要你的回答"
- 闲聊/发表情/纯吐槽
只输出一个数字(0或1):"""

# ─── 工具选择 prompt ───
TOOL_SELECT_PROMPT = """根据用户消息，选择最合适的工具调用。
输出格式: [CALL:工具名 参数]
可用工具: wdsj(战绩), box(快递)
（搜索/天气已由 LLM CALL 系统接管）
如果不需要调用，输出 NONE

用户消息: {msg}

你的选择:"""

async def _call_judge(prompt: str, msg_history: list[str], current_msg: str) -> bool:
    """调用 LLM 做 0/1 判断，返回 True 表示需要工具调用"""
    from services.llm import call_llm
    from core.config import get_config
    cfg = get_config()

    # ★ 预检：用户明确不要搜索 / 要求 bot 自己回答 → 跳过工具判断
    import re
    msg_lower = current_msg.lower()
    NO_SEARCH_RULES = [
        # 明确拒绝搜索
        (r'不[要需用]搜', msg_lower),
        (r'别搜', msg_lower),
        (r'不[要需用]查', msg_lower),
        (r'不用搜索', msg_lower),
        (r'不[是要能].*[搜查找]', msg_lower),  # "不是搜索到的" / "不能搜"
        (r'别[去再].*[搜查找]', msg_lower),   # "别去搜"
        # 要求 bot 自己回答 → 不是搜索请求
        (r'你[自己]的(?:回答|想法|看法|意见|思考)', msg_lower),  # "你的回答/想法/看法"
        (r'(?:不要|不想|不需要).*搜索', msg_lower),  # "不想搜索到的"
        (r'(?:而)?不是.*搜索', msg_lower),  # "而不是搜索到的"
        # 反问/质疑搜索
        (r'搜[索到]了?[什么啥]', msg_lower),  # "搜索了什么"
        (r'不要.*[网上去]?找', msg_lower),
    ]
    if any(re.search(p, t) for p, t in NO_SEARCH_RULES):
        logger.debug("工具判断跳过（用户拒搜或要bot自己答）: '%s'", current_msg[:50])
        return False

    # 最近 5 条上下文
    recent = "\n".join(msg_history[-5:] or [])
    full = f"{TOOL_JUDGE_PROMPT}\n\n上下文:\n{recent}\n当前消息: {current_msg}"

    try:
        result = await call_llm(
            model_cfg=cfg.reply_model,
            messages=[{"role": "user", "content": full}],
            max_tokens=1,
            temperature=0,
        )
        return result.strip() == "1"
    except Exception as e:
        logger.warning("工具判断 LLM 失败: %s", e)
        return False


async def _select_tool(msg: str) -> tuple[str, str] | None:
    """选择工具和参数，返回 (tool_name, args) 或 None。
    如果 LLM 返回 NONE 但 judge 已判断需要工具，调用方会兜底走 search。"""
    from services.llm import call_llm
    from core.config import get_config
    cfg = get_config()

    prompt = TOOL_SELECT_PROMPT.format(msg=msg[:500])
    try:
        result = await call_llm(
            model_cfg=cfg.reply_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            temperature=0,
        )
        m = re.search(r'\[CALL:(\w+)\s+(.+?)\]', result)
        if m:
            return m.group(1).lower(), m.group(2).strip()
        # 没有匹配到 CALL 格式 → 返回空字符串标记，调用方兜底走 search
        if "NONE" not in result.upper():
            logger.debug("工具选择无匹配: '%s'", result[:50])
        return None
    except Exception as e:
        logger.warning("工具选择 LLM 失败: %s", e)
    return None


async def _execute_tool(tool_name: str, args: str, user_id: int, group_id: int) -> str:
    """执行工具调用，返回结果文本。"""
    logger.info("工具调用: %s(%s)", tool_name, args)
    if tool_name == "wdsj":
        return await _tool_wdsj(args)
    elif tool_name == "box":
        return await _tool_box(args)
    return f"未知工具: {tool_name}"


def get_tool_status(tool_name: str, args: str) -> str | None:
    """返回工具执行前的进度提示消息，或 None 表示无提示"""
    if tool_name == "wdsj":
        return f"正在查战绩喵~ 等一下下"
    if tool_name == "box":
        return f"正在查快递喵~"
    return None


async def _tool_wdsj(args: str) -> str:
    try:
        from services import wdsj_api as api
    except ImportError:
        return "战绩查询模块未安装"
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return "wdsj 格式: wdsj <模式> <玩家名>"
    template, player = parts[0], parts[1]
    from services import wdsj_api as api
    tid = api.resolve_template(template)
    if not tid:
        return f"未知模式: {template}"
    data = await api.query_player_stats(player, tid)
    if not data:
        return f"未找到玩家 '{player}' 的 {template} 战绩"
    display = api.TEMPLATES.get(tid, tid)
    lines = [f"{player} 的 {display} 战绩:"]
    for k, v in data.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)[:1500]



async def _fetch_page(url: str) -> str:
    """Playwright 爬取网页文本, 超时 8 秒"""
    try:
        from modules.changelog import _ensure_browser
        browser = await _ensure_browser()
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=8000)
        await page.wait_for_timeout(500)
        text = await page.inner_text("body")
        await page.close()
        # 清洗: 去多余空白, 截取前 2000 字
        import re
        text = re.sub(r'\n\s*\n', '\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        return text.strip()[:2000]
    except Exception:
        return ""


async def _llm_select_urls(results: list[dict]) -> list[str]:
    """让 LLM 从搜索结果中选择最多 3 个最相关的 URL 进行深度阅读"""
    from services.llm import call_llm
    from core.config import get_config
    cfg = get_config()

    items = []
    for i, r in enumerate(results, 1):
        items.append(f"{i}. {r.get('title','')}\n   {r.get('body','')[:120]}\n   URL: {r.get('href','')}")
    menu = "\n".join(items)

    prompt = f"""以下搜索结果，请选择最多3个最相关、内容最丰富的URL。
只输出选中的URL，每行一个，不要其他内容:
{menu}
选中URL:"""

    try:
        result = await call_llm(
            model_cfg=cfg.reply_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0,
        )
        urls = [line.strip() for line in result.splitlines() if line.strip().startswith("http")]
        return urls[:3]
    except Exception as e:
        logger.warning("URL 选择 LLM 失败: %s", e)
        return [r.get("href") for r in results[:2] if r.get("href")]


async def _web_search(query: str, max_results: int = 12) -> list[dict]:
    """搜索: ddgs yahoo(Bing)优先, Playwright Bing回退, 百度兜底"""
    # ddgs yahoo (Bing)
    try:
        from ddgs import DDGS
        loop = __import__('asyncio').get_running_loop()
        results = await loop.run_in_executor(
            None, lambda: list(DDGS().text(query, backend="yahoo", max_results=max_results))
        )
        if results:
            return results
    except Exception:
        pass

    # Playwright Bing (借助已有 Chromium 实例)
    try:
        from modules.changelog import _ensure_browser
        browser = await _ensure_browser()
        page = await browser.new_page()
        await page.goto(f"https://www.bing.com/search?q={query}", wait_until="networkidle", timeout=15000)
        await page.wait_for_timeout(1000)  # 等 JS 渲染完
        items = await page.query_selector_all(".b_algo")
        results = []
        for item in items[:max_results]:
            title_el = await item.query_selector("h2 a")
            snippet_el = await item.query_selector(".b_caption p, .b_caption .b_lineclamp2")
            if title_el:
                title = (await title_el.inner_text()).strip()
                href = (await title_el.get_attribute("href")) or ""
                body = ((await snippet_el.inner_text()).strip() if snippet_el else "")
                if title and href and not title.startswith("http"):
                    results.append({"title": title, "body": body, "href": href})
        await page.close()
        if results:
            logger.debug("Playwright Bing: %d results", len(results))
            return results
    except Exception as e:
        logger.debug("Playwright Bing 搜索失败: %s", e)

    # 百度回退
    import httpx, re
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"https://www.baidu.com/s?wd={query}", headers=headers)
            html = resp.text
        results = []
        blocks = re.split(r'<div[^>]*class="[^"]*result[^"]*c-container[^"]*"', html)[1:max_results+1]
        for block in blocks:
            title_m = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            snippet_m = re.search(r'<span[^>]*class="[^"]*content-right_[^"]*"[^>]*>(.*?)</span>', block, re.DOTALL)
            if not snippet_m:
                snippet_m = re.search(r'<div[^>]*class="[^"]*c-abstract[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
            if title_m:
                results.append({
                    "title": re.sub(r'<[^>]+>', '', title_m.group(2)).strip(),
                    "body": re.sub(r'<[^>]+>', '', snippet_m.group(1)).strip() if snippet_m else "",
                    "href": title_m.group(1),
                })
        if results:
            return results
    except Exception:
        pass

    raise Exception("所有搜索引擎均失败")


async def _optimize_keywords(user_query: str) -> list[str]:
    """LLM 优化搜索关键词，返回多个精准搜索词"""
    from services.llm import call_llm
    from core.config import get_config
    cfg = get_config()
    prompt = f"将以下用户查询转化为搜索引擎用的关键词（不要整句，只要2-4个关键词组合，每个用|分隔）：\n{user_query[:200]}"
    try:
        result = await call_llm(
            model_cfg=cfg.reply_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80, temperature=0,
        )
        keywords = [k.strip() for k in result.split("|") if k.strip() and len(k.strip()) > 2]
        if keywords:
            logger.debug("关键词优化: %s → %s", user_query[:30], keywords)
            return keywords[:3]
    except Exception:
        pass
    return [user_query]


async def _tool_search(args: str) -> str:
    query = args.strip()

    # 0. LLM 优化关键词
    keywords = await _optimize_keywords(query)
    progress_messages = []  # 存储进度消息

    # 1. 多关键词搜索 + 去重
    all_results = []
    seen_urls = set()
    for i, kw in enumerate(keywords):
        try:
            batch = await _web_search(kw, max_results=6)
            logger.debug("搜索: %s → %d条", kw[:30], len(batch))
            new_count = 0
            for r in batch:
                url = r.get("href", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
                    new_count += 1
            if i > 0 and new_count > 0:
                progress_messages.append(f"关键词「{kw}」找到了 {new_count} 条新结果喵~")
        except Exception:
            pass
        if len(all_results) >= 10:
            break

    if not all_results:
        return f"没有找到「{query}」的相关结果喵~"

    results = all_results[:12]
    logger.debug("多关键词搜索完成: %d条去重结果", len(results))
    for i, r in enumerate(results[:5], 1):
        logger.debug("  [%d] %s | %s", i, r.get("title","")[:30], r.get("href","")[:60])

    # 2. 让 LLM 选 URL
    selected = await _llm_select_urls(results[:8])
    logger.debug("LLM 选中 %d 个URL: %s", len(selected), selected)

    # 3. 格式化摘要（含 URL）
    parts = [f"【搜索: {query} · 共{len(results)}条结果 · 以下网址请务必在回复中引用】"]
    for i, r in enumerate(results[:8], 1):
        title = r.get("title", "")
        body = r.get("body", "")[:120]
        url = r.get("href", "")
        parts.append(f"{i}. [{title}]({url}) - {body}")

    # 4. 爬取 LLM 选中的页面详情
    if selected:
        parts.append(f"\n【已深度阅读 {len(selected)} 个页面】")
        for url in selected:
            content = await _fetch_page(url)
            if content:
                logger.debug("爬取: %s → %d字", url[:50], len(content))
                parts.append(f"\n页面 {url}: {content}")
            else:
                logger.debug("爬取失败: %s", url[:60])

    # 进度消息用 PROGRESS: 前缀注入到结果中
    result = "\n".join(parts)[:3500]
    if progress_messages:
        result = "PROGRESS:" + "；".join(progress_messages) + "\n" + result
    return result


async def _tool_box(args: str) -> str:
    try:
        from services.box_api import query_express
    except ImportError:
        return "快递查询模块未安装"
    data = await query_express(args.strip())
    if not data:
        return f"未找到单号 '{args}' 的物流信息"
    lines = [f"快递 {args}:"]
    if data.get("state"):
        lines.append(f"  状态: {data['state']}")
    if data.get("latest"):
        lines.append(f"  最新: {data['latest']}")
    return "\n".join(lines)[:500]


def inject_tool_system(prompt: str) -> str:
    """将工具定义注入到 system prompt"""
    return prompt + "\n\n" + TOOL_DEFINITIONS


async def try_tool_select(
    current_msg: str,
    msg_history: list[str],
) -> tuple[bool, tuple[str, str] | None]:
    """
    判断并选择工具，返回 (judge_passed, (tool_name, args))。
    judge_passed=False 表示判断为不需要工具，上层不应兜底搜索。
    """
    need = await _call_judge("", msg_history, current_msg)
    if not need:
        return (False, None)
    tool = await _select_tool(current_msg)
    return (True, tool)


async def execute_tool(tool_name: str, args: str, user_id: int, group_id: int) -> str:
    """执行工具调用，返回结果文本"""
    return await _execute_tool(tool_name, args, user_id, group_id)
