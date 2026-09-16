"""给服务器的 get_tool_schemas 打补丁：去重 + 按 name 排序

为什么：DeepSeek 的上下文缓存与 tools 定义强相关。实测
  · 同一份 tools 第 2 次调用 → 命中 92-99%
  · tools 变了 → 只命中 30%（仅 system 部分）
插件工具是运行时注册的，顺序漂移会让 tools 每次都"变" → 缓存几乎全失效。
排序后顺序恒为字典序 → 缓存前缀稳定。去重则防 API 400（Tool names must be unique）。

本脚本只做定点文本替换，不动文件其他内容。
"""
import re
import shutil
import sys
from pathlib import Path

P = Path('/root/bot/core/tools.py')
s = P.read_text(encoding='utf-8')

if "_uniq.sort(" in s:
    print("已打过补丁（存在 _uniq.sort），退出")
    sys.exit(0)

OLD = """        for cap in registry.all():
            if cap.category != CATEGORY_TOOL or not cap.source.startswith("plugin:"):
                continue
            if cap.name in builtin_names:
                continue  # 与内置工具同名 → 内置优先
            schema = registry.get_tool_schema(cap.id)
            if schema and schema not in schemas:
                schemas.append(schema)
    except Exception:
        pass
    return schemas"""

NEW = """        for cap in registry.all():
            if cap.category != CATEGORY_TOOL or not cap.source.startswith("plugin:"):
                continue
            if cap.name in builtin_names:
                continue  # 与内置工具同名 → 内置优先
            schema = registry.get_tool_schema(cap.id)
            if schema and schema not in schemas:
                schemas.append(schema)
    except Exception:
        pass

    # ★ v2.3.28 两个关键加固（都直接影响 DeepSeek 上下文缓存命中率）
    #
    # ① 按 name 去重（保留首次出现）
    #    TOOLS 里曾出现两个 learn_slang，带重名工具请求会被 API 拒绝：
    #      400 "Tool names must be unique." → FC 调用全挂。
    #    这里做最后一道防线，列表写重了也不会把坏请求发出去。
    #
    # ② 按 name 排序，保证顺序**绝对稳定**
    #    实测（scripts/_diag_api_cache.py）：
    #      同一份 tools 第 2 次调用命中 92~99%；
    #      tools 一变，第 1 次只命中 30%（仅 system 部分）。
    #    插件是运行时注册的，顺序漂移会让 tools 每次"变" → 缓存几乎全失效
    #    （线上实测命中率长期只有 38%，正是这个原因）。
    #    排序后顺序恒为字典序，缓存前缀才稳定。
    _seen: set[str] = set()
    _uniq: list[dict] = []
    for s_ in schemas:
        _n = ((s_ or {}).get("function") or {}).get("name") or ""
        if not _n or _n in _seen:
            continue
        _seen.add(_n)
        _uniq.append(s_)
    _uniq.sort(key=lambda x: (x.get("function") or {}).get("name") or "")
    return _uniq"""

n = s.count(OLD)
if n != 1:
    print(f"锚点匹配 {n} 处（期望 1），中止")
    sys.exit(1)

shutil.copy2(P, str(P) + ".bak_v2328")
print(f"已备份: {P.name}.bak_v2328")
out = s.replace(OLD, NEW, 1)
P.write_text(out, encoding="utf-8")
print(f"补丁已应用：{len(s)} → {len(out)} 字符")
print(f"  含去重逻辑: {'_uniq.sort(' in out}")
