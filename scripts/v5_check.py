"""V5 真实链路验证：情绪快照/趋势 + 会话重放 + 心情面板数据。

用法：python scripts/v5_check.py
"""
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from httpx import AsyncClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORT = 8769
BASE = f"http://127.0.0.1:{PORT}"


def parse_sse(text: str):
    events = []
    for block in text.split("\n\n"):
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if event:
            events.append((event, data))
    return events


async def main() -> bool:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.app:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    results = {}
    try:
        async with AsyncClient(timeout=120, trust_env=False) as c:
            for _ in range(40):
                try:
                    if (await c.get(f"{BASE}/api/profile")).status_code == 200:
                        break
                except Exception:
                    await asyncio.sleep(0.5)

            # 1. 会话 1：情绪起伏的对话 → 睡前整理 → 情绪快照
            sid = (await c.post(f"{BASE}/api/session")).json()["session_id"]
            await c.get(f"{BASE}/api/greeting?session_id={sid}")
            for msg in ["最近在准备一个重要考试，压力挺大的",
                        "不过今天模拟测成绩还不错，有点小开心"]:
                r = await c.post(f"{BASE}/api/chat",
                                 json={"session_id": sid, "message": msg})
                parse_sse(r.text)  # 消费流
            r = (await c.post(f"{BASE}/api/review?session_id={sid}")).json()
            emo = r["review"].get("emotion")
            print(f"情绪快照: {emo}")
            results["emotion_snapshot"] = bool(emo)

            # 2. 趋势接口
            e = (await c.get(f"{BASE}/api/emotions")).json()
            print(f"趋势接口: snapshots={len(e['snapshots'])}, trend={e['trend']}")
            results["emotion_api"] = len(e["snapshots"]) >= 1 and e["trend"] is not None

            # 3. 会话消息重放（模拟刷新恢复）
            m = (await c.get(f"{BASE}/api/messages?session_id={sid}")).json()
            print(f"消息重放: {len(m['messages'])} 条")
            results["messages_replay"] = len(m["messages"]) >= 4
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\n── V5 验证结论 ──")
    all_ok = True
    for k, v in results.items():
        print(f"{'✅' if v else '❌'} {k}")
        all_ok = all_ok and v
    return all_ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
