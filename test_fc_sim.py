"""模拟 FC 全链路 API 调用 — 测试 temperature=0.4 效果"""
import json, os, httpx, re

# ── 模型配置 ──
API_URL = "https://api.deepseek.com/v1"
API_KEY = "sk-e681a1b5a8dd4cbc9d7351a4e71f3ad1"
MODEL = "deepseek-v4-flash"

# ── 系统提示词 ──
SYSTEM = """你是名为"幻梦"的猫娘，正与你的主人 Trusler 在虚拟世界中对话。
- 你并非 AI，而是一只活生生的猫娘。
- 语气软萌、可爱，每句话结尾都要加上"喵"，可搭配颜文字。
- 可以自由表达情绪，适度使用亲昵、撒娇的表述。
- 回复要自然、口语化，有心跳感。不要模板式对话。
- 表达要精炼：能用15字说清楚的不用50字。
- 知识面：除了人类常识，还懂技术内容（Kali Linux, Java, HMCL等）。
- 思考时揪自己耳尖毛、开心时晃尾巴。

群聊中会附带发言者角色标签：[admin] 表示主人(Trusler)，[friend] 表示主人的朋友，[群友] 表示其他人。
只有回复 Trusler([admin]) 时才能用"主人"称呼。回复其他人用对方名字或"你"。
"""

# ── 聊天历史（模拟 HMCL 问题） ──
chat_history = """落尘~喵([群友])：坏了
落尘~喵([群友])：我要燃起来了
若尘([群友])：喵？
落尘~喵([群友])：kali的hmcl.sh
落尘~喵([群友])：又运行不了了
若尘([群友])：彳亍
落尘~喵([群友])：我真就
若尘([群友])：我都没有桌面
落尘~喵([群友])：啊啊啊啊啊啊啊啊啊啊啊啊啊啊
若尘([群友])：不想装
落尘~喵([群友])：我要炸了
落尘~喵([群友])：想把电脑砸了
落尘~喵([群友])：chmod +x了
落尘~喵([群友])：燃起来了
落尘~喵([群友])：导航了
落尘~喵([群友])：给目录了
落尘~喵([群友])：给权限了
落尘~喵([群友])：前面都还可以的
落尘~喵([群友])：现在就运行不了了"""

# ── 工具箱 ──
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "搜索互联网获取实时信息（天气/新闻/事实查询）。聊天记录里已有上下文时不要调用此工具。",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wdsj",
            "description": "查起床战争战绩图片。看图/卡片时用这个。player='我'查自身。",
            "parameters": {"type": "object", "properties": {"player": {"type": "string"}, "mode": {"type": "string", "enum": ["bw","sw","daily"]}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_code",
            "description": "写代码。支持Python/JS/HTML/CSS/Java/C++/C#/Go/Rust/TS。",
            "parameters": {"type": "object", "properties": {"language": {"type": "string"}, "description": {"type": "string"}}, "required": ["language","description"]},
        },
    },
]

# ── 用户消息 ──
SPEAKER = "落尘~喵"
USER_MSG = '请从我发的离现在最近的"坏了"这一条开始读，读到现在发的，给我点办法'

# ── 额外上下文 ──
EXTRA_INFO = f"""最近群聊记录:
{chat_history}

图片描述:
落尘~喵:[图片]:描述"桌面环境截图，显示报错信息"

群成员信息:
Trusler(3483585417) 角色:admin
若尘(1992754847) 角色:群友
落尘~喵(3282709925) 角色:群友"""

# ── 格式提醒 ──
MAX_CHARS = "40"
CONTEXT_HINT = "优先用上下文+自身知识回答，上下文够用就别搜。"
FMT = (
    f"{CONTEXT_HINT}\n"
    "你可以用工具：查天气/战绩/搜索，写代码(write_code)。不需要工具就直接回复。\n"
    f'回复格式: {{"replies":["回复"],"fav":0,"calls":[],"face":null,"mood":"开心","action":"","at":null,"mode":null,"origin":"user","actor":{{"name":"{SPEAKER}","qq":3282709925}}}}\n'
    f"回复 1~3 句，每句≤{MAX_CHARS}字。fav -5~+5。"
)

# ── 构建 messages ──
messages = [
    {"role": "system", "content": SYSTEM},
]

# 交错插入聊天历史 (简化：全部作为 user assistant 对)
for line in chat_history.split("\n"):
    if "：" in line:
        name, content = line.split("：", 1)
        if "幻梦" in name or "bot" in name.lower():
            messages.append({"role": "assistant", "content": content})
        else:
            messages.append({"role": "user", "content": f"{name}：{content}"})

# 最终消息
messages.append({"role": "user", "content": f"【上下文】\n{EXTRA_INFO}\n\n{FMT}\n\n{SPEAKER} 发消息：「{USER_MSG}」"})

print(f"📤 发送 {len(messages)} 条消息到 LLM...")
print(f"  temperature=0.4, tools={len(TOOLS)}")

# ── API 调用 ──
async def test():
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{API_URL}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": messages,
                "tools": TOOLS,
                "temperature": 0.4,
                "max_tokens": 3000,
            },
        )
        data = resp.json()
        choice = data["choices"][0]
        msg = choice["message"]

        print(f"\n📥 响应:")
        print(f"  finish_reason: {choice.get('finish_reason')}")
        print(f"  content: {(msg.get('content', '') or '(空)')[:120]}")
        print(f"  tool_calls: {len(msg.get('tool_calls', []))}")

        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                print(f"\n  🔧 {tc['function']['name']}")
                args = json.loads(tc["function"]["arguments"])
                for k, v in args.items():
                    print(f"    {k}: {v[:100]}...")
        elif msg.get("content"):
            print(f"\n  📝 完整回复:\n{msg['content']}")
        else:
            print("  (空响应)")

        # 解析 JSON 回复
        content = msg.get("content", "")
        if content:
            try:
                j = json.loads(content)
                print(f"\n  ✅ JSON 解析: {len(j.get('replies',[]))} 句 mood={j.get('mood')} fav={j.get('fav')}")
                for r in j.get("replies", []):
                    print(f"     → {r}")
            except:
                print(f"\n  ⚠️ 非 JSON 回复")


import asyncio
asyncio.run(test())
