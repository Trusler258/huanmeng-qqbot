"""
自动更新引擎 v2 — Git Patch 行级增量合并

流程:
  1. 取 GitHub diff → .patch 文本数组
  2. .bot_protect 优先合并（先更新保护规则）
  3. 逐文件解析 patch → hunks → 上下文匹配 → 行级替换
  4. 只改变化的行，不动任何本地未涉及的行
  5. 更新 state.json 追踪每个文件 blob SHA
"""

from __future__ import annotations

import asyncio
import fnmatch
import httpx
from pathlib import Path

from core.logger import get_logger
from modules._auto_update.patcher import parse_patch, apply_hunks
from modules._auto_update.state import load_state, save_state, get_file_blob, set_file_blob

logger = get_logger("auto_update")

GITHUB_REPO = "Trusler258/huanmeng-qqbot"
GITHUB_BRANCH = "main"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"
CACHE_DIR = ".update_cache"

# 文件下载镜像
RAW_MIRRORS = [
    "https://raw.githubusercontent.com",
    "https://raw.gitmirror.com",
    "https://gh-proxy.com/raw.githubusercontent.com",
]


def _root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _load_protect_list() -> set[str]:
    path = _root() / ".bot_protect"
    if not path.exists():
        return set()
    entries = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.add(line.rstrip("/"))
    return entries


def _is_protected(rel_path: str, protect: set[str]) -> bool:
    for p in protect:
        if fnmatch.fnmatch(rel_path, p) or fnmatch.fnmatch(rel_path, p + "/*"):
            return True
        if rel_path.startswith(p + "/") or rel_path == p:
            return True
    return False


def _skip_prefix(rel_path: str) -> bool:
    """总是跳过的路径前缀"""
    return rel_path.startswith(("logs/", ".git/", "data/", "__pycache__/"))


async def _fetch_compare(base_sha: str, head_sha: str) -> list[dict] | None:
    """获取两个 commit 之间的 diff 文件列表（含 .patch 字段）"""
    if not base_sha:
        return None
    try:
        url = f"{GITHUB_API}/compare/{base_sha[:7]}...{head_sha[:7]}"
        headers = {"Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient(timeout=15, verify=False) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json().get("files", [])
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning("Compare 基准 SHA 已失效（force-push），需全量同步")
        else:
            logger.warning("Compare API 失败: %s", e)
        return None
    except Exception as e:
        logger.warning("Compare API 失败: %s", e)
        return None


async def _get_head_sha() -> str | None:
    """获取远程 HEAD commit SHA"""
    try:
        url = f"{GITHUB_API}/commits/{GITHUB_BRANCH}"
        headers = {"Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json().get("sha", "")
    except Exception as e:
        logger.warning("获取 HEAD 失败: %s", e)
        return None


async def _get_blob_sha(rel_path: str, commit_sha: str) -> str:
    """获取某个文件在指定 commit 中的 blob SHA"""
    try:
        url = f"{GITHUB_API}/contents/{rel_path}?ref={commit_sha}"
        headers = {"Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient(timeout=5, verify=False) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json().get("sha", "")
    except Exception:
        return ""


def _read_local(root: Path, rel_path: str) -> list[str]:
    """读取本地文件，返回行列表 (保留换行符)"""
    fpath = root / rel_path
    if not fpath.exists():
        return []
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            return f.readlines()
    except Exception:
        return []


def _write_local(root: Path, rel_path: str, lines: list[str]):
    """写入本地文件"""
    fpath = root / rel_path
    fpath.parent.mkdir(parents=True, exist_ok=True)
    # 备份
    bak = fpath.with_suffix(fpath.suffix + ".bak")
    if fpath.exists():
        try:
            bak.write_bytes(fpath.read_bytes())
        except Exception:
            pass
    fpath.write_text("".join(lines), encoding="utf-8")


async def check_and_update(check_only: bool = False, force: bool = False) -> str:
    root = _root()
    state = load_state(root)

    # 1. 获取 HEAD SHA
    head = await _get_head_sha()
    if not head:
        return "无法连接 GitHub，请检查网络"

    # 2. 比对
    stored = state.get("remote_sha", "")
    if not force and stored == head:
        return "已是最新"

    # 3. 获取 diff（404 时 base SHA 已失效，清空后重新全量获取）
    base = stored if stored and not force else ""
    files = await _fetch_compare(base, head)
    if not files:
        if stored:
            # 旧 SHA 可能已被 force-push 覆盖，清除后下次从新基线开始
            logger.info("△ 旧基线 SHA 失效，清除并从当前 HEAD 重建基线")
            state["remote_sha"] = head
            state.pop("files", None)
            save_state(root, state)
            return "历史 commit 已过期（仓库可能 force-push 过）。基线已重置，请再次 /~update 完成全量同步。"
        state["remote_sha"] = head
        save_state(root, state)
        return "首次运行，无历史版本来 diff。请用 /~update 同步后续更新"

    # 4. 处理 .bot_protect（优先合并）
    _merge_bot_protect_priority(files, root, head, state)

    # 5. 读取保护列表（此时已是最新）
    protect = _load_protect_list()

    # 6. 逐文件 patch 合并
    ok = 0
    skip = 0
    updated_files: list[str] = []

    for item in files:
        rel = item.get("filename", "")
        if not rel or _skip_prefix(rel):
            continue

        patch_text = item.get("patch", "")
        status = item.get("status", "")

        # 删除
        if status == "removed":
            local = root / rel
            if local.exists() and not _is_protected(rel, protect):
                if not check_only:
                    local.unlink(missing_ok=True)
                    updated_files.append(f"[删除] {rel}")
            continue

        # 跳过保护文件
        if _is_protected(rel, protect):
            continue

        # 无 patch 或无变化
        if not patch_text:
            continue

        # 显示模式
        if check_only:
            updated_files.append(f"[待更新] {rel}")
            continue

        # 解析 + 合并
        hunks = parse_patch(patch_text)
        if not hunks:
            # 无 hunk → 可能是二进制/重命名，跳过
            continue

        local_lines = _read_local(root, rel)
        merged, aok, sk = apply_hunks(local_lines, hunks)
        if aok > 0:
            _write_local(root, rel, merged)
            blob = await _get_blob_sha(rel, head)
            set_file_blob(state, rel, blob, aok, sk)
            updated_files.append(rel)
        ok += aok
        skip += sk

    # 7. 保存状态
    state["remote_sha"] = head
    save_state(root, state)

    # 8. 返回结果
    parts = []
    if updated_files:
        if check_only:
            parts.append(f"待更新 {len(updated_files)} 个文件:")
            parts.extend(f"  {f}" for f in updated_files)
        else:
            parts.append(f"已更新 {len(updated_files)} 个文件 "
                         f"({ok} hunks 成功, {skip} hunks 跳过)")
    if not updated_files:
        parts.append("已是最新")
    return "\n".join(parts)


def _merge_bot_protect_priority(
    files: list[dict], root: Path, head: str, state: dict
):
    """优先合并 .bot_protect 文件，确保后续文件应用最新保护规则"""
    for item in files:
        if item.get("filename") == ".bot_protect":
            patch_text = item.get("patch", "")
            if not patch_text:
                continue
            hunks = parse_patch(patch_text)
            if not hunks:
                continue
            local_lines = _read_local(root, ".bot_protect")
            merged, _, _ = apply_hunks(local_lines, hunks)
            _write_local(root, ".bot_protect", merged)
            break
