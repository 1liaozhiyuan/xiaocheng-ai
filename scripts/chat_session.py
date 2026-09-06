"""自聊造数据：与小澄进行多话题、多情绪的对话，积累分析用数据。

用法：python scripts/chat_session.py [--review]
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8001"

# 22 轮对话：覆盖闲聊 / 事实提供 / 情绪起伏 / 计划 / 记忆回调 / 关系互动
SCRIPT = [
    "嗨，在吗？",
    "今天好多事，忙到刚才才吃上饭",
    "不过中午的外卖意外的好吃，点了一家新开的川菜馆",
    "对了，我最近在追《漫长的季节》，你看过吗",
    "看到半夜两点，今天上班困死了",
    "说起来，我在考虑要不要养一只猫",
    "但是室友对猫毛过敏，有点纠结",
    "换个话题，我上周去做了体检，一切正常",
    "就是医生说我有点缺乏运动",
    "我同事推荐我打羽毛球，感觉还挺感兴趣的",
    "周末打算去试试",
    "对了还有个事，我表弟下个月结婚，喊我当伴郎",
    "我还没当过伴郎呢，有点紧张",
    "得买身西装，正好趁机会添置一下",
    "说到衣服，这几天降温了，好冷",
    "哎对了，我决定先把之前那个考试的计划缓一缓，最近事情太多了",
    "工作上有个新项目下周要上线，压力挺大但还挺有成就感的",
    "晚上准备早点睡，明早去跑步",
    "跟你聊完感觉好多了，谢啦",
    "对了你觉得伴郎致辞要说点什么",
    "哈哈好像也没那么难了，我心里有数了",
    "时间不早了，晚安！",
]


async def main(run_review: bool) -> None:
    async with httpx.AsyncClient(timeout=180, trust_env=False) as c:
        sid = (await c.post(f"{BASE}/api/session")).json()["session_id"]
        print(f"会话: {sid}\n")
        c.get(f"{BASE}/api/greeting?session_id={sid}")  # 消耗开场
        for i, msg in enumerate(SCRIPT, 1):
            t0 = time.time()
            r = await c.post(f"{BASE}/api/chat",
                             json={"session_id": sid, "message": msg})
            text, note = "", ""
            for block in r.text.split("\n\n"):
                ev = data = None
                for line in block.split("\n"):
                    if line.startswith("event: "):
                        ev = line[7:].strip()
                    elif line.startswith("data: "):
                        data = line[6:]
                if ev == "delta" and data:
                    try:
                        text += json.loads(data)["text"]
                    except Exception:
                        pass
                elif ev == "memory" and data:
                    try:
                        mc = json.loads(data).get("content")
                        if mc:
                            note = f"  💭 {mc}"
                    except Exception:
                        pass
                elif ev == "meta" and data:
                    try:
                        sev = json.loads(data).get("severity")
                        if sev and sev != "none":
                            note += f"  [{sev}]"
                    except Exception:
                        pass
            print(f"[{i:02d}] 你: {msg}")
            print(f"     澄: {text.strip()}  ({time.time() - t0:.1f}s){note}\n")
        if run_review:
            r = (await c.post(f"{BASE}/api/review?session_id={sid}")).json()
            rev = r.get("review", {})
            print("── 睡前整理 ──")
            print(f"补记 {rev.get('saved')} 条、去重 {rev.get('deduped')} 条")
            if rev.get("emotion"):
                print(f"情绪: {rev['emotion']}")
            print(f"日记: {(rev.get('diary') or '')[:120]}")
        print(f"\n会话 ID（供后续分析）: {sid}")


if __name__ == "__main__":
    asyncio.run(main("--review" in sys.argv))
