"""全链路冒烟测试：打桩 LLM 与向量库，离线验证骨架闭环。

python -m unittest tests.test_smoke -v
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from agents.companion import CompanionAgent
from agents.memory_manager import MemoryManager
from memory.retriever import Retriever
from memory.store import MemoryStore

SEED_MEMORY = "用户养了只猫叫年糕"
EXTRACTED_MEMORY = {"content": "用户 9 月 4 日面试失败了，有些沮丧",
                    "category": "event", "importance": 0.8}


class FakeLLM:
    """替代真实 API：chat_stream 走对话链路，chat_json 走记忆抽取链路。"""

    def __init__(self):
        self.stream_calls, self.json_calls = [], []

    async def chat_stream(self, messages, **kw):
        self.stream_calls.append(messages)
        for token in ["抱抱", "，", "明天会好起来的"]:
            yield token

    async def chat_json(self, messages, **kw):
        self.json_calls.append(messages)
        return [dict(EXTRACTED_MEMORY)]


class FakeVectorStore:
    enabled = True

    def __init__(self):
        self.docs = {}

    def add(self, memory_id, content, metadata):
        self.docs[str(memory_id)] = content

    def search(self, query, top_k):
        return []  # 空实现：测试依赖 Retriever 的高重要性兜底路径


class TestSmoke(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.tmp.name) / "t.db")
        self.llm = FakeLLM()
        self.vs = FakeVectorStore()
        self.companion = CompanionAgent(self.llm, self.store, Retriever(self.store, self.vs))
        self.manager = MemoryManager(self.llm, self.store, self.vs)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_full_turn_loop(self):
        async def run():
            self.store.add_memory(SEED_MEMORY, "fact", 0.9, "s_seed")

            # 第一轮：生成回复，system prompt 应包含兜底召回的记忆
            chunks = [c async for c in self.companion.reply("s1", "今天心情不太好")]
            self.assertEqual("".join(chunks), "抱抱，明天会好起来的")
            system = self.llm.stream_calls[0][0]["content"]
            self.assertIn(SEED_MEMORY, system)
            self.assertIn("小澄", system)  # 人设已注入

            # 后台记忆抽取
            self.manager.schedule_turn("s1", self.store.recent_messages("s1", limit=4))
            await asyncio.gather(*asyncio.all_tasks(asyncio.get_running_loop()) - {asyncio.current_task()})

            # 新记忆已入库并进入向量库
            contents = [m["content"] for m in self.store.all_active_memories()]
            self.assertIn(EXTRACTED_MEMORY["content"], contents)
            self.assertIn(str(len(contents)), self.vs.docs)  # 向量库收到 id

            # 去重：同一内容不会被重复入库
            await self.manager._extractor.extract_and_store("s1", [])
            self.assertEqual(len(self.store.all_active_memories()), len(contents))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
