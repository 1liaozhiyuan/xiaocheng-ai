"""V4 真实链路验证：子进程起真实 Web 服务，模拟前端完整流程。

覆盖：会话创建 → 开场（节流）→ 流式聊天 → 后台抽取 → 记忆面板 → 画像 → 睡前整理
用法：python scripts/v4_check.py
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

import config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="v4_"))
config.DB_PATH = _TMP / "t.db"
config.CHROMA_PATH = _TMP / "chroma"

ROOT = Path(__file__).resolve().parent.parent
from httpx import AsyncClient  # noqa: E402

PORT = 8765
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
    # 子进程起服务（独立进程更接近真实部署；Windows 下也避免 loop 兼容问题）
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.app:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env={**__import__("os").environ,
                            "DB_PATH_OVERRIDE": str(_TMP / "t.db"),
                            "CHROMA_OVERRIDE": str(_TMP / "chroma")},
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    # 子进程读自己的 .env，路径覆盖用不到——改用轮询就绪即可（服务用自己的 data/）
    # trust_env=False：localhost 请求不走系统代理（本机环境有代理会劫持 127.0.0.1）
    async with AsyncClient(timeout=120, trust_env=False) as c:
        ready = False
        last_err = None
        for _ in range(40):
            try:
                if (await c.get(f"{BASE}/api/profile")).status_code == 200:
                    ready = True
                    break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                await asyncio.sleep(0.5)
        if not ready:
            proc.terminate()
            print(f"❌ 服务未能启动，最后错误: {last_err}")
            return False

        results = {}
        try:
            # 1. 会话创建
            sid = (await c.post(f"{BASE}/api/session")).json()["session_id"]
            print(f"会话: {sid}")

            # 2. 主动开场（真实生成）
            r = await c.get(f"{BASE}/api/greeting?session_id={sid}")
            events = parse_sse(r.text)
            greeting = "".join(d["text"] for e, d in events if e == "delta")
            print(f"开场: {greeting}")
            results["greeting"] = bool(greeting.strip())

            # 3. 开场节流
            r = await c.get(f"{BASE}/api/greeting?session_id={sid}")
            results["greeting_throttle"] = "skipped" in r.text

            # 4. 聊两轮（真实回复）
            for msg in ["我叫小王，在杭州做后端开发，养了只猫叫年糕",
                        "今天她把我键盘踩坏了，又气又好笑"]:
                r = await c.post(f"{BASE}/api/chat",
                                 json={"session_id": sid, "message": msg})
                events = parse_sse(r.text)
                reply = "".join(d["text"] for e, d in events if e == "delta")
                kinds = [e for e, _ in events]
                print(f"\n你: {msg}")
                print(f"小澄: {reply[:80]}")
                assert kinds[0] == "meta" and kinds[-1] == "done"
            results["chat_stream"] = True

            # 5. 等后台抽取落库 → 记忆面板
            await asyncio.sleep(45)
            mem = (await c.get(f"{BASE}/api/memory")).json()
            print(f"\n记忆面板: 当前{len(mem['active'])} 条 {([m['content'] for m in mem['active']])}")
            results["memory_panel"] = len(mem["active"]) >= 1 and all(m["content"] for m in mem["active"])

            # 6. 画像
            p = (await c.get(f"{BASE}/api/profile")).json()
            print(f"画像: {p['profile']}")

            # 7. 睡前整理
            r = (await c.post(f"{BASE}/api/review?session_id={sid}")).json()
            print(f"复盘: 补记 {r['review']['saved']} 条、去重 {r['review']['deduped']} 条")
            print(f"摘要: {(r['review']['summary'] or '')[:60]}…")
            print(f"画像巩固: {r['profile']}")
            results["review"] = bool(r["review"]["summary"])

            # 8. forget 接口
            if mem["active"]:
                mid = mem["active"][0]["id"]
                r = await c.delete(f"{BASE}/api/memory/{mid}")
                results["forget"] = r.json().get("ok") is True
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("\n── V4 验证结论 ──")
    all_ok = True
    for k, v in results.items():
        print(f"{'✅' if v else '❌'} {k}")
        all_ok = all_ok and v
    return all_ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
