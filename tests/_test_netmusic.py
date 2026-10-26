# -*- coding: utf-8 -*-
"""回归测试：netmusic 插件的输入解析与文件名规则。

只测纯函数（不联网），联网行为在服务器上单独验证过。
运行：python tests/_test_netmusic.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 插件目录不是 package，按文件路径加载（与 voice 插件同一手法）
_MAIN = ROOT / "plugins" / "netmusic" / "main.py"
_spec = importlib.util.spec_from_file_location("_hm_plugin_netmusic", _MAIN)
M = importlib.util.module_from_spec(_spec)
sys.modules["_hm_plugin_netmusic"] = M
_spec.loader.exec_module(M)

ok = fail = 0


def ck(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print("ok  ", name)
    else:
        fail += 1
        print("FAIL", name)


# ── 输入解析 ──────────────────────────────────────────────
CASES = [
    ("https://music.163.com/song?id=347230", ("song", "347230")),
    ("https://music.163.com/#/song?id=347230", ("song", "347230")),
    ("https://y.music.163.com/m/song?id=347230&uct2=abc", ("song", "347230")),
    ("https://music.163.com/album?id=32311", ("album", "32311")),
    ("https://music.163.com/#/my/m/music/playlist?id=3756532", ("playlist", "3756532")),
    ("347230", ("song", "347230")),
    ("看看这个 https://music.163.com/song?id=1974443814 好听", ("song", "1974443814")),
    ("", (None, "")),
    ("hello", (None, "")),
    # 路径形态（不放宽 id= 就够不到的）
    ("https://music.163.com/song/347230", ("song", "347230")),
    ("https://music.163.com/song/347230/?userid=1", ("song", "347230")),
    ("https://music.163.com/m/song/347230/", ("song", "347230")),
    ("https://music.163.com/album/32311", ("album", "32311")),
    ("https://music.163.com/playlist/3756532", ("playlist", "3756532")),
    # id= 不再要求前面是 ? & #：裸 id= 也要认
    ("https://music.163.com/api/song/detail?id=347230", ("song", "347230")),
    ("music.163.com/song?id=347230", ("song", "347230")),
    # ★ 陷阱：userid= / songid= 不能被当成 id=
    ("https://music.163.com/song?userid=999", (None, "https://music.163.com/song?userid=999")),
    ("https://music.163.com/x?songid=888&id=347230", ("song", "347230")),
]
for text, expect in CASES:
    ck(f"解析 {text!r}", M.parse_target(text) == expect)

# 短链：暂不跟跳转，交给调用侧 _resolve_short
ck("短链先返回 URL 本身",
   M.parse_target("https://163cn.tv/abcdef") == (None, "https://163cn.tv/abcdef"))

# 曲名里带 ? 号不会污染 id 提取
ck("URL 带多余参数", M.parse_target(
    "https://music.163.com/song?id=347230&userid=999&from=x") == ("song", "347230"))


# ── 时长/大小格式化 ──────────────────────────────────────
ck("时长 5:26", M._fmt_duration(326000) == "5:26")
ck("时长 0:07", M._fmt_duration(7000) == "0:07")
ck("大小 0.7MB", M._fmt_size(721023) == "0.7MB")
ck("大小未知返回空", M._fmt_size(0) == "")

# ── 文件名规则：曲名-艺人_id.mp3 ──────────────────────────
detail = {
    "name": "海阔天空",
    "artists": [{"name": "Beyond"}],
    "album": {"name": "海阔天空"},
    "duration": 326000,
}
audio = {"url": "http://x/y.mp3", "br": 128008, "size": 721023, "code": 200, "fee": 1}
stem = M._safe_name(f"{detail['name']}-{M._artists(detail, '_')}", 80)
ck("文件名 曲名-艺人_id.mp3", f"{stem}_347230.mp3" == "海阔天空-Beyond_347230.mp3")

# 多艺人用下划线连接，避免被当成路径分隔符
multi = {"name": "歌", "artists": [{"name": "A"}, {"name": "B"}]}
stem2 = M._safe_name(f"{multi['name']}-{M._artists(multi, '_')}", 80)
ck("多艺人 A_B", f"{stem2}_1.mp3" == "歌-A_B_1.mp3")

# 非法字符（斜杠/冒号/问号）被替换，不能带出路径
bad = {"name": 'a/b:c?d*e"f<g>h|i', "artists": [{"name": "X"}]}
stem3 = M._safe_name(f"{bad['name']}-{M._artists(bad, '_')}", 80)
ck("非法字符被清理", all(ch not in stem3 for ch in '/:*?"<>|'))

# ── 信息文本 ─────────────────────────────────────────────
txt = M.build_info_text(detail, audio)
ck("文本含歌名与歌手", "海阔天空" in txt and "Beyond" in txt)
ck("文本含时长", "5:26" in txt)
ck("文本无 Markdown 星号", "**" not in txt and "`" not in txt)
ck("有直链时不提会员", "会员" not in txt)

no_url = dict(audio, url="", fee=1)
ck("无直链且 fee=1 提到会员", "会员" in M.build_info_text(detail, no_url))

print()
print("通过 %d / 失败 %d" % (ok, fail))
sys.exit(1 if fail else 0)
