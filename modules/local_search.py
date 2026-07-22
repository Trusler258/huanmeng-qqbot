"""
多功能本地搜索工具 + 页面抓取
- 必应 + 百度网页 + 百度百科 三数据源（并行搜索）
- PageScraper 深度页面正文提取（readability-lxml）
- 适配 CALL 调用 + 深度读取流程
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

import requests
from bs4 import BeautifulSoup

from core.logger import get_logger

logger = get_logger("local_search")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class LocalMultiSearch:
    def __init__(self, default_limit: int = 4, timeout: int = 12, request_delay: float = 0.3):
        self.default_limit = default_limit
        self.timeout = timeout
        self.request_delay = request_delay
        logger.info("本地多源搜索器已初始化: limit=%d timeout=%ds delay=%.1fs",
                    default_limit, timeout, request_delay)

    def _search_one(self, query: str, source: str, limit: int) -> str:
        """单个数据源搜索（线程安全）"""
        time.sleep(self.request_delay)  # 每个源的延时，防止同一目标短时间大量请求
        try:
            if source == "bing":
                return self._search_bing(query, limit)
            elif source == "baidu":
                return self._search_baidu_web(query, limit)
            elif source == "baike":
                return self._search_baike(query, limit)
        except Exception as e:
            logger.warning("%s搜索失败: %s", source, e)
        return ""

    def _format_entry(self, idx: int, title: str, snippet: str, link: str) -> str:
        """精简格式化：标题 + 短摘要(≤80字) + URL"""
        short = snippet[:80].replace("\n", " ") if snippet else ""
        return f"{idx}. {title}\n   {short}...\n   {link}"

    def _search_bing(self, query: str, limit: int) -> str:
        params = {"q": query, "mkt": "zh-CN", "setlang": "zh", "count": limit}
        resp = requests.get("https://cn.bing.com/search", headers=_HEADERS, params=params, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.find_all("li", class_="b_algo")
        if not items:
            return ""
        output = []
        for i, item in enumerate(items):
            if i >= limit:
                break
            h2 = item.find("h2")
            if not h2 or not h2.a:
                continue
            title = h2.get_text(strip=True)
            link = h2.a["href"]
            p = item.find("p")
            snippet = p.get_text(strip=True) if p else ""
            output.append(self._format_entry(i+1, title, snippet, link))
        return "\n".join(output)

    def _search_baidu_web(self, query: str, limit: int) -> str:
        params = {"wd": query, "rn": limit}
        resp = requests.get("https://www.baidu.com/s", headers=_HEADERS, params=params, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.find_all("div", class_="result-op c-container xpath-log")
        if not items:
            items = soup.find_all("div", class_="c-container")
        if not items:
            return ""
        output = []
        for i, item in enumerate(items):
            if i >= limit:
                break
            h3 = item.find("h3")
            if not h3 or not h3.a:
                continue
            title = h3.get_text(strip=True)
            link = h3.a["href"]
            div_snip = item.find("div", class_="c-abstract")
            snippet = div_snip.get_text(strip=True) if div_snip else ""
            output.append(self._format_entry(i+1, title, snippet, link))
        return "\n".join(output)

    def _search_baike(self, query: str, limit: int) -> str:
        params = {"wd": f"{query} site:baike.baidu.com"}
        resp = requests.get("https://www.baidu.com/s", headers=_HEADERS, params=params, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.find_all("div", class_="c-container")
        if not items:
            return ""
        output = []
        for i, item in enumerate(items):
            if i >= limit:
                break
            h3 = item.find("h3")
            if not h3 or not h3.a:
                continue
            url = h3.a["href"]
            if "baike.baidu.com/item/" not in url:
                continue
            title = h3.get_text(strip=True)
            div_snip = item.find("div", class_="c-abstract")
            snippet = div_snip.get_text(strip=True) if div_snip else ""
            output.append(self._format_entry(i+1, title, snippet, url))
        return "\n".join(output)

    def search_all(self, query: str, limit: int = None) -> str:
        """三源并行搜索，合并结果"""
        limit = limit if limit is not None else self.default_limit
        sources = ["baike", "baidu", "bing"]
        results: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(self._search_one, query, s, limit): s for s in sources}
            for future in as_completed(futures):
                src = futures[future]
                try:
                    r = future.result()
                    if r:
                        results[src] = r
                except Exception as e:
                    logger.warning("并行搜索 %s 异常: %s", src, e)

        if not results:
            return ""
        # 必应→百度网页→百科 顺序输出
        parts = []
        for s in ["bing", "baidu", "baike"]:
            if s in results:
                label = {"bing": "===== 必应 =====", "baidu": "===== 百度网页 =====", "baike": "===== 百度百科 ====="}
                parts.append(f"{label[s]}\n{results[s]}")
        return "\n\n".join(parts)

    def run_search(self, query: str, source: Literal["bing", "baidu", "baike", "all"] = "all", limit: int = None) -> str:
        if source == "all":
            return self.search_all(query, limit)
        return self._search_one(query, source, limit or self.default_limit)


# ════════════════════════════════════════════════════════════
#  PageScraper: 单页正文提取
# ════════════════════════════════════════════════════════════

class PageScraper:
    def __init__(self, timeout: int = 12, request_delay: float = 0.3):
        self.timeout = timeout
        self.request_delay = request_delay

    def scrape(self, url: str, max_chars: int = 2000) -> str | None:
        """抓取网页正文，返回纯文本。失败返回 None"""
        time.sleep(self.request_delay)
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=self.timeout, allow_redirects=True)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding
            html = resp.text
        except Exception as e:
            logger.warning("页面请求失败 [%s]: %s", url[:60], e)
            return None

        try:
            from readability import Document
            doc = Document(html)
            title = doc.title()
            raw = doc.summary()
            soup = BeautifulSoup(raw, "html.parser")
        except ImportError:
            logger.debug("readability 未安装，回退 bs4 提取")
            soup = BeautifulSoup(html, "html.parser")
            title = soup.title.string if soup.title else ""
            soup = soup  # 直接用全文

        for tag in soup(["script", "style", "iframe", "noscript", "nav", "footer"]):
            tag.decompose()
        text = soup.get_text(strip=True, separator="\n")
        result = f"【{title}】\n{text}"
        if len(result) > max_chars:
            result = result[:max_chars] + "..."
        logger.info("页面提取完成: %s (%d字)", url[:50], len(result))
        return result


# 全局单例
_searcher: LocalMultiSearch | None = None
_scraper: PageScraper | None = None


def get_searcher() -> LocalMultiSearch:
    global _searcher
    if _searcher is None:
        _searcher = LocalMultiSearch(default_limit=4, timeout=15, request_delay=0.3)
    return _searcher


def get_scraper() -> PageScraper:
    global _scraper
    if _scraper is None:
        _scraper = PageScraper(timeout=12, request_delay=0.3)
    return _scraper
