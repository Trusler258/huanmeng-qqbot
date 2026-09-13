"""把 v2.3.0 条目插到 update_log.md 的 v2.2.0 之前，并更新 version.toml。

为什么不用 scp 覆盖：
  update_log.md 与 version.toml 都是"服务器版可能是超集"的高危文件，
  历史上被无脑覆盖过（lang.toml 那次丢了私有模块段落）。
  所以这里只做**定点插入**，其余内容一个字节都不动。
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path("/root/bot")
LOG = ROOT / "data" / "update_log.md"
VER = ROOT / "config" / "version.toml"

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

ENTRY = """## v2.3.0 — 面板全可写（106 接口）+ 崩溃自愈经实测可用 (2026.9.13)
一句话总结：面板从"能看"变成"啥都能改、啥都能管"，并且加了崩溃自愈——改配置把 bot 改崩了，它会自己回滚并救活；这条机制是真把 bot 弄崩验证过的。

**一、功能全开：65 → 106 个接口，13 → 18 个路由组**

新增 5 个路由组，都是"能写"的：
- **群管理** `groups`（6 接口）：38 个群一览、群详情、成员发言统计、
  写群笔记、改群长期记忆、看备份。群列表从 5 处数据源取并集
  （msglog / stats / memory / notes / 地震订阅），不漏群；
  读"最后活跃时间"只读文件尾 8KB，不把整个 msglog 拖进内存。
- **分类图片管理** `media`（6 接口）：表情库 / 生图产物 / 临时图 / 撤回留图。
  **删除 = 移入回收目录**（`data/panel_trash/`），不真删；缩略图接口做了
  三重路径穿越防护。实测发现 `recall_images` 已 3.8G / 12812 张
  ——`modules/recall.py` 只管下载不管回收，这是它最实际的价值。
- **数据库管理** `database`（7 接口）：两个库的表结构、分页浏览、
  全文检索、自定义 SQL、重建索引、VACUUM。
  自定义 SQL 拒 DDL、单语句、执行前先备份、需 `confirm=CUSTOM`。
- **配置可视化编辑** `config_editor`（7 接口）：7 个 toml 文件在线编辑，
  支持**单键修改**（只改那一行的 `=` 右侧，注释/缩进/键顺序全保留）。
- **提示词在线编辑** `prompts`（7 接口）：13 个提示词文件，带章节大纲解析，
  改完支持热加载。

**二、崩溃自愈：改崩了自动回滚（已实测）**
- 面板与 bot 是独立进程，所以 bot 崩了面板还活着 —— 这是自愈能成立的前提。
- 逻辑：面板写关键文件 → 记操作栈（含备份路径）→ 布防 → 探测发现 bot 反复崩
  → 回滚那一次改动 → 重启 bot → 告警。
- **按需探测，不是一直扫**：`idle` 完全不探测（阻塞在 Event 上）→
  `armed`（布防后 10 分钟内 5s 一次）→ `degraded`（超窗口 60s 一次）→
  `expired`（超 2 小时放弃）。前端心跳可延长高频窗口。
- 只回滚**面板自己写过的**文件；用手改崩的不归它管，这是刻意的。

**三、实测踩出来的三个真 bug（都已修）**

1. **`is-active` 判据完全失效**（最严重）
   `bot.service` 是 `Restart=always` + `RestartSec=2`，配置改坏后 systemd
   每 2 秒把 bot 拉起来一次、永远拉，**`systemctl is-active` 一直返回 active**。
   第一版自愈就是看 `is-active`，结论是"bot 活得好好的"——
   在最该出手的场景下完全瞎了。
   实测数据：`NRestarts` 从 0 一路涨到 6，而 `ActiveState` 始终 active。
   改为读 `NRestarts` + `MainPID`：**PID 变了或重启计数涨了即判定不健康**，
   数值没变再等 3 秒复读一次确认稳定（防止卡在"起来了但马上要崩"的瞬间）。

2. **回滚会弹错文件**
   原来无脑弹栈顶，但栈顶未必是导致这次崩溃的写入。实测时栈里躺着两条
   陈年记录，自愈去回滚了一个跟崩溃无关的文件。
   改为布防时记住具体那条，只回滚它。

3. **同秒写入无法区分**
   时间戳只精确到秒，一次操作连着写三个文件时三条记录 `ts` 完全相同，
   按 `ts` 查找会弹错那一条（实测证实）。给每条操作加唯一 `id`，
   按 id 定位；旧数据兼容 `ts` 查找。

**四、其它**
- 登录失败改为**递增封禁**：5min × 2^(n-1)，上限 24h（原来固定阈值）。
- 新增 `/system/ops/clear`、`/system/selfheal/events/clear`、
  `/system/selfheal/test`（演练，dry-run 不改文件）。
- 135 项单元测试全过；93 项真实数据验收全过；25 项自愈端到端实测全过。

**五、实测记录（自愈真的把 bot 弄崩又救回来了）**
```
NRestarts: 0 → 5（+5）      ← 崩溃循环
MainPID:   1529017 → 1529656 ← 反复重启
自愈自行触发：已回滚 write_config：config/roles.toml
回滚后内容与原始**逐字节**一致  6137 字节（一致）
bot 恢复运行，自愈自动解除布防回到静默
```


"""

print("=== 更新 update_log.md ===")
text = LOG.read_text(encoding="utf-8")

if "## v2.3.0" in text:
    print("  已存在 v2.3.0 条目，跳过插入")
else:
    marker = "## v2.2.0"
    idx = text.find(marker)
    if idx < 0:
        print("  ✗ 找不到 v2.2.0 锚点，放弃（不冒险追加）")
        raise SystemExit(1)
    # 备份（铁律：改任何文件前先备份）
    bak = LOG.with_name(f"update_log.md.bak_v230_{stamp}")
    shutil.copy(LOG, bak)
    print(f"  已备份 → {bak.name}")

    new = text[:idx] + ENTRY + text[idx:]
    # 写之前校验：v2.2.0 及之后的内容必须原样保留
    assert new.endswith(text[idx:]), "尾部内容被破坏"
    LOG.write_text(new, encoding="utf-8")
    print(f"  已插入 v2.3.0，{len(text)} → {len(new)} 字符")
    first = re.search(r"^## v[\d.]+", new, re.M)
    print(f"  第一条 ## v = {first.group(0) if first else '(无)'}")

print()
print("=== 更新 version.toml ===")
vtext = VER.read_text(encoding="utf-8")
if 'current = "v2.3.0"' in vtext:
    print("  已是 v2.3.0，跳过")
else:
    vbak = VER.with_name(f"version.toml.bak_v230_{stamp}")
    shutil.copy(VER, vbak)
    print(f"  已备份 → {vbak.name}")
    vnew, n = re.subn(r'current\s*=\s*"v[\d.]+"', 'current = "v2.3.0"', vtext)
    if n != 1:
        print(f"  ✗ 替换了 {n} 处（期望 1），放弃")
        raise SystemExit(1)
    VER.write_text(vnew, encoding="utf-8")
    print("  已更新为 v2.3.0")

print()
print("=== 校验 ===")
import subprocess
r = subprocess.run(
    ["python3", "-c",
     "import sys; sys.path.insert(0,'/root/bot');"
     "from core import config; c=config.load_bot_config();"
     "print('config.version =', c.version)"],
    capture_output=True, text=True, timeout=60,
)
print(" ", (r.stdout + r.stderr).strip().splitlines()[-1])
