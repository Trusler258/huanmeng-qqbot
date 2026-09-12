"""服务器端接口验收脚本 — 面板 v0.1.0

用途：在服务器上一次性拉全部接口，打印真实数据摘要。
用法：cd /root/bot && python3 deploy/panel_check.py <密码>
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:59300"


def call(path, token=None, method="GET", body=None, timeout=25):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


def main():
    pw = sys.argv[1] if len(sys.argv) > 1 else ""
    code, d = call("/api/auth/login", method="POST", body={"password": pw})
    if code != 200:
        print(f"[x] 登录失败 {code}: {d}")
        return 1
    tok = d["token"]
    print(f"[+] 登录成功，token {len(tok)} 字符\n")

    # 逐接口验收。校验函数返回 (是否通过, 摘要文本)
    def brief_overview(b):
        return (b["totals"]["groups_tracked"] > 0,
                f"版本{b['version']} bot={'活' if b['bot']['alive'] else '死'} "
                f"今日{b['today']['messages']}条/{b['today']['groups']}群")

    def brief_trend(b):
        tot = sum(x["messages"] for x in b["data"])
        return (b["days"] == 7, f"7天共{tot}条")

    def brief_fav(b):
        return (b["total"] > 0, f"{b['total']}条好感记录，分组{len(b['groups'])}")

    def brief_stats_groups(b):
        return (len(b["items"]) > 0, f"{len(b['items'])}个群有统计")

    def brief_profiles(b):
        return (b["total"] > 0, f"{b['total']}个画像")

    def brief_notes(b):
        return (True, f"{len(b['items'])}个会话有笔记")

    def brief_stm(b):
        return (True, f"{len(b['items'])}个会话有短期记忆")

    def brief_selfk(b):
        return (len(b["sections"]) > 0, f"{len(b['sections'])}章节，{b['size']}字符")

    def brief_skills(b):
        return (True, f"{len(b['items'])}个技能文件")

    def brief_features(b):
        return (b["count"] > 0, f"{b['count']}个开关: " +
                ",".join(f"{i['key']}={'开' if i['enabled'] else '关'}" for i in b["items"]))

    def brief_commands(b):
        return (b["total"] > 0, f"{b['total']}条指令，{len(b['categories'])}分类")

    def brief_llmaudit(b):
        miss = b.get("missing_in_llm", [])
        return (b["ok"], f"注册{len(b['registered'])}条，LLM可见{len(b['in_llm_prompt'])}条"
                         f"，缺失{len(miss)}条" + (f" 例:{miss[:4]}" if miss else ""))

    def brief_logfiles(b):
        return (len(b["files"]) > 0, f"{len(b['files'])}个日志文件")

    def brief_logs(b):
        return (len(b["logs"]) > 0, f"取到{len(b['logs'])}行")

    def brief_msgstats(b):
        return (b.get("total", 0) > 0,
                f"{b.get('total')}条消息，{b.get('db')}，{b.get('oldest','')[:10]}~{b.get('newest','')[:10]}")

    def brief_msglog(b):
        return (len(b["items"]) > 0, f"{len(b['items'])}个会话有msglog")

    def brief_services(b):
        act = [i for i in b["items"] if i["in_service"]]
        return (True, f"{len(act)}/{len(b['items'])} 运行中: " +
                ",".join(i["name"].replace(".service", "") for i in act))

    def brief_ports(b):
        return (b["total"] > 0, f"{b['total']}个监听，其中{b['exposed_count']}个对外(all)")

    def brief_updlog(b):
        return (len(b["items"]) > 0, f"当前{b['current']}，共{len(b['items'])}条")

    def brief_cfglist(b):
        return (len(b["items"]) > 0, f"{len(b['items'])}个配置文件")

    def brief_audit(b):
        return (True, f"{b['total']}条审计")

    def brief_sysinfo(b):
        mem = b.get("mem_available_kb", 0) // 1024
        return (True, f"Py{b['python']} 可用内存{mem}MB")

    def brief_plugins(b):
        n = sum(len(v["items"]) for v in b.values() if isinstance(v, dict))
        return (True, f"{n}个插件条目")

    def brief_hmp(b):
        return (True, f"{b['count']}个.hmp")

    def brief_caps(b):
        return (b["ok"], f"{b.get('total')}个能力 {b.get('by_category')}"
                         + (f" err={b.get('error')}" if not b["ok"] else ""))

    def brief_bus(b):
        return (b["ok"], f"{b.get('topic_count')}个事件类型，{b.get('handler_total')}个订阅者"
                         + (f" err={b.get('error')}" if not b["ok"] else ""))

    def brief_wzq(b):
        return (True, f"{b['total']}条战绩记录")

    def brief_wdsj(b):
        return (True, f"{b['history_total']}个键，{len(b.get('playernames',{}))}个玩家名")

    def brief_countdown(b):
        return (True, f"{len(b['items'])}个倒计时")

    def brief_searchcache(b):
        return (True, f"{b['total']}条缓存")

    def brief_eq(b):
        return (True, f"{len(b.get('files',[]))}个数据文件，{len(b.get('recent_pushes',[]))}条推送")

    def brief_econ(b):
        return (True, f"{b['user_count']}个用户，总积分{b['total_points']}")

    CHECKS = [
        ("/api/overview", "概览", brief_overview),
        ("/api/overview/trend?days=7", "趋势", brief_trend),
        ("/api/social/fav?limit=5", "好感度", brief_fav),
        ("/api/social/stats/groups", "群统计列表", brief_stats_groups),
        ("/api/social/profiles?limit=5", "用户画像", brief_profiles),
        ("/api/memory/notes", "笔记本", brief_notes),
        ("/api/memory/stm", "短期记忆", brief_stm),
        ("/api/memory/self-knowledge", "自身认知", brief_selfk),
        ("/api/memory/skills", "技能文件", brief_skills),
        ("/api/features", "实验开关", brief_features),
        ("/api/commands", "指令清单", brief_commands),
        ("/api/commands/llm-audit", "LLM可见性审计", brief_llmaudit),
        ("/api/logs/files", "日志文件", brief_logfiles),
        ("/api/logs?lines=20", "日志内容", brief_logs),
        ("/api/messages/stats", "消息库概况", brief_msgstats),
        ("/api/messages/msglog/files", "msglog列表", brief_msglog),
        ("/api/system/services", "服务状态", brief_services),
        ("/api/system/ports", "监听端口", brief_ports),
        ("/api/system/update-log?limit=3", "更新日志", brief_updlog),
        ("/api/system/config", "配置列表", brief_cfglist),
        ("/api/system/audit?limit=5", "审计日志", brief_audit),
        ("/api/system/info", "系统信息", brief_sysinfo),
        ("/api/plugins", "插件目录", brief_plugins),
        ("/api/plugins/hmp", "hmp插件", brief_hmp),
        ("/api/plugins/capabilities", "能力注册表", brief_caps),
        ("/api/plugins/eventbus", "事件总线", brief_bus),
        ("/api/games/wzq?limit=5", "五子棋", brief_wzq),
        ("/api/games/wdsj?limit=5", "洛花星雨", brief_wdsj),
        ("/api/games/countdown", "倒计时", brief_countdown),
        ("/api/games/search-cache?limit=5", "搜索缓存", brief_searchcache),
        ("/api/earthquake", "地震模块", brief_eq),
        ("/api/economy", "经济系统", brief_econ),
    ]

    ok = 0
    failed = []
    for path, name, fn in CHECKS:
        code, body = call(path, tok)
        if code != 200:
            print(f"  [HTTP-{code}] {name:16s} {path}")
            failed.append(f"{name}(HTTP {code})")
            continue
        try:
            passed, summary = fn(body)
        except Exception as e:
            passed, summary = False, f"解析异常 {type(e).__name__}: {e}"
        if passed:
            ok += 1
            print(f"  [OK ] {name:16s} {summary}")
        else:
            failed.append(name)
            print(f"  [ERR] {name:16s} {summary}")

    print()
    print("=" * 60)
    print(f"  通过 {ok}/{len(CHECKS)}")
    if failed:
        print("  失败: " + ", ".join(failed))
    print("=" * 60)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
