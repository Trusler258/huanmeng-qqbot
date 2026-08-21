"""
db 包：结构化存储与全文检索。

对外统一从这里导出，调用方尽量用 `from db import SearchStore, get_search_store`，
避免直接依赖内部文件名（store.py）。
"""

from __future__ import annotations

from .store import (
    SearchStore,
    get_search_store,
    fts5_available,
)

__all__ = ["SearchStore", "get_search_store", "fts5_available"]
