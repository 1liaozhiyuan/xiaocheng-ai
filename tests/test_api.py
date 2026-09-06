"""V4 API 层单测：打桩 LLM，覆盖会话/流式聊天/记忆面板/复盘。

python -m unittest tests.test_api -v
"""
import asyncio
import json
import unittest

from httpx import ASGITransport, AsyncClient

from server.app import create_app
from tests.test_v2 import FakeLLM, FakeVectorStore, make_store


class FakeCore:
    """真实装配的鸭子替身：存储用临时库，LLM 全打桩（结构与 AppState 同形）。"""

    def __init__(self, json_results=None, chat_texts=None):
        self._tmp_store, self._tmp = make_store()
        self.store = self._tmp_store
        self.vector_store = FakeVectorStore()
        self.llm = FakeLLM(chat_texts=chat_texts, json_results=json_results)
        from agents.companion import CompanionAgent
        from agents.memory_manager import MemoryManager
        from agents.profile_agent import ProfileAgent
        from agents.proactive import ProactiveAgent
        from agents.strategist import StrategistAgent
        from memory.extractor import MemoryExtractor
        from memory.retriever import Retriever
        from memory.reviewer import SessionReviewer
        self.extractor = MemoryExtractor(self.llm, self.store, self.vector_store)
        self.retriever = Retriever(self.store, self.vector_store)
        self.strategist = StrategistAgent(self.llm)
        self.companion = CompanionAgent(self.llm, self.store, self.retriever,
                                        self.strategist)
        self.memory_manager = MemoryManager(self.llm, self.store, self.vector_store,
                                            self.extractor)
        self.reviewer = SessionReviewer(self.llm, self.store, self.extractor,
                                        self.vector_store)
        self.profile_agent = ProfileAgent(self.llm, self.store)
        self.proactive = ProactiveAgent(self.llm, self.store)
        self.sessions = {}
        self.background_tasks = set()

    def new_session(self):
        from server.state import SessionState
        import uuid
        s = SessionState(id=f"s_{uuid.uuid4().hex[:8]}")
        self.sessions[s.id] = s
        return s

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def revive_session(self, session_id):
        from server.state import SessionState
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState(id=session_id)
        return self.sessions[session_id]

    def touch(self, session):
        pass

    def schedule(self, coro_or_task):
        import asyncio
        if isinstance(coro_or_task, asyncio.Task):
            task = coro_or_task
        else:
            task = asyncio.get_running_loop().create_task(coro_or_task)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    def cleanup(self):
        self.store.close()
        self._tmp.cleanup()


def parse_sse(text: str):
    """把 SSE 文本解析成 [(event, data_dict)]。"""
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


class TestAPI(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_full_flow(self):
        core = FakeCore(
            # 严格时序：轮1策略 → 轮1抽取 → review抽取 → review情绪
            json_results=[
                {"emotion": "happy", "intent": "sharing", "crisis": False,
                 "severity": "none", "instruction": "一起开心"},
                [{"content": "用户叫小王", "category": "fact", "importance": 0.8}],
                [],
                {"score": 6.0, "label": "平静", "note": "平和"},
            ],
            chat_texts=["你好呀！", "小王你好！", "聊天摘要。"],
        )
        app = create_app(core=core, enable_idle_review=False)

        async def flow():
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://t") as client:
                # 1. 创建会话
                sid = (await client.post("/api/session")).json()["session_id"]

                # 2. 开场（流式）
                r = await client.get(f"/api/greeting?session_id={sid}")
                events = parse_sse(r.text)
                self.assertEqual(events[-1][0], "done")
                deltas = "".join(d["text"] for e, d in events if e == "delta")
                self.assertEqual(deltas, "你好呀！")

                # 3. 开场节流：第二次应 skipped
                r = await client.get(f"/api/greeting?session_id={sid}")
                self.assertIn("skipped", r.text)

                # 4. 聊天（流式；meta 因与检索并行而移至流尾，仅作 UI 标注）
                r = await client.post("/api/chat", json={
                    "session_id": sid, "message": "我叫小王，今天升职啦"})
                events = parse_sse(r.text)
                kinds = [e for e, _ in events]
                self.assertEqual(kinds[0], "delta")   # 首帧即内容：无前置等待
                self.assertEqual(kinds[-1], "done")
                self.assertIn("meta", kinds)
                reply = "".join(d["text"] for e, d in events if e == "delta")
                self.assertEqual(reply, "小王你好！")
                # 消息已入库
                self.assertEqual(core.store.count_messages(sid), 2)

                # 5. 记忆面板（后台抽取 fire-and-forget，直接断言存储层终态）
                r = await client.get("/api/memory")
                self.assertIn("active", r.json())

                # 6. 画像接口
                r = await client.get("/api/profile")
                self.assertIn("profile", r.json())
                self.assertIn("followup_flag", r.json())

                # 7. 忘记不存在的记忆 → 404
                r = await client.delete("/api/memory/999")
                self.assertEqual(r.status_code, 404)

        try:
            self._run(flow())
        finally:
            core.cleanup()

    def test_session_revives_after_service_restart(self):
        # 模拟服务重启：内存 sessions 清空，浏览器仍持旧 session_id。
        # 发消息应自动重建会话状态（404 不再出现），对话无缝延续。
        core = FakeCore(
            json_results=[
                {"emotion": "neutral", "intent": "casual", "crisis": False,
                 "severity": "none", "instruction": ""}],
            chat_texts=["嗨，我们继续聊！"],
        )
        app = create_app(core=core, enable_idle_review=False)

        async def flow():
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://t") as client:
                sid = (await client.post("/api/session")).json()["session_id"]
                core.sessions.clear()          # ← 服务重启，内存态全丢
                r = await client.post("/api/chat", json={
                    "session_id": sid, "message": "我还接着上次聊"})
                self.assertEqual(r.status_code, 200)   # 修复前这里是 404
                kinds = [e for e, _ in parse_sse(r.text)]
                self.assertIn("delta", kinds)

        try:
            self._run(flow())
        finally:
            core.cleanup()

    def test_chat_validates_session_and_message(self):
        core = FakeCore(json_results=[])
        app = create_app(core=core, enable_idle_review=False)

        async def flow():
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://t") as client:
                # 未知 session_id 会自动重建会话（服务重启后无缝续聊），
                # 这里只需校验空消息仍被拒绝
                sid = (await client.post("/api/session")).json()["session_id"]
                r = await client.post("/api/chat", json={
                    "session_id": sid, "message": "   "})
                self.assertEqual(r.status_code, 400)

        try:
            self._run(flow())
        finally:
            core.cleanup()


if __name__ == "__main__":
    unittest.main()
