"""V2 单测：会话复盘 / 画像巩固 / 策略与危机守护 / 上下文注入。

python -m unittest tests.test_v2 -v
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from agents.companion import CompanionAgent
from agents.profile_agent import ProfileAgent
from agents.strategist import StrategistAgent
from memory.retriever import Retriever
from memory.reviewer import SessionReviewer
from memory.store import MemoryStore
from memory.vector_store import VectorStore  # noqa: F401


class FakeLLM:
    """chat 走文本队列（摘要），chat_json 走 JSON 队列（抽取/画像/策略）。"""

    def __init__(self, chat_texts=None, json_results=None):
        self.chat_texts = list(chat_texts or [])
        self.json_results = list(json_results or [])
        self.chat_calls = 0
        self.json_calls = 0
        self.last_stream_messages = None

    async def chat(self, messages, **kw):
        self.chat_calls += 1
        return self.chat_texts.pop(0)

    async def chat_json(self, messages, **kw):
        self.json_calls += 1
        return self.json_results.pop(0)

    async def chat_stream(self, messages, **kw):
        self.last_stream_messages = messages
        self.chat_calls += 1
        # greeting 等生成路径也走流式：有队列则按队列，否则默认（兼容旧测试）
        text = self.chat_texts.pop(0) if self.chat_texts else "好的"
        yield text


class FakeVectorStore:
    """enabled + 可配置的 search 命中（供查重与分层检索测试用）。"""

    def __init__(self, hits=None, enabled=True):
        self.hits = hits or []
        self.enabled = enabled
        self.docs = {}
        self.deleted = set()

    def add(self, memory_id, content, metadata):
        self.docs[str(memory_id)] = content

    def delete(self, memory_id):
        # chroma 的 id 均为字符串，fake 与真实行为保持一致
        self.deleted.add(str(memory_id))

    def search(self, query, top_k):
        return self.hits[:top_k]


def make_store():
    tmp = tempfile.TemporaryDirectory()
    store = MemoryStore(Path(tmp.name) / "t.db")
    return store, tmp


class TestSessionStore(unittest.TestCase):
    def test_summary_roundtrip_and_exclude(self):
        store, tmp = make_store()
        try:
            store.set_session_summary("s1", "聊了工作和猫")
            store.set_session_summary("s2", "聊了搬家")
            self.assertEqual(store.recent_summaries(2)[0]["summary"], "聊了搬家")
            self.assertEqual(
                [s["session_id"] for s in store.recent_summaries(5, exclude_session="s1")],
                ["s2"])
        finally:
            store.close()
            tmp.cleanup()


class TestReviewer(unittest.TestCase):
    def test_review_extracts_and_summarizes(self):
        store, tmp = make_store()
        try:
            for text in ["我叫小王，在杭州工作", "好啊", "我养了只猫叫年糕",
                         "今天就聊到这啦"]:
                store.add_message("s1", "user", text)
                store.add_message("s1", "assistant", "好呀")
            llm = FakeLLM(
                chat_texts=["用户介绍了工作，提到了猫。", "今天认识了新朋友小王，聊得挺开心。"],
                json_results=[
                    [{"content": "用户叫小王", "category": "fact",
                      "importance": 0.8}],
                    {"score": 6.0, "label": "平静", "note": "聊天平和"},
                ],
            )
            reviewer = SessionReviewer(llm, store, MemoryExtractorForTest(llm, store))
            result = asyncio.run(reviewer.review("s1"))
            self.assertEqual(result["saved"], 1)
            self.assertEqual(result["emotion"]["label"], "平静")
            self.assertIn("猫", result["summary"])
            self.assertEqual(store.get_profile() or {}, {})
            self.assertEqual(
                [m["content"] for m in store.all_active_memories()], ["用户叫小王"])
            self.assertIsNotNone(store.recent_summaries(1)[0]["summary"])
        finally:
            store.close()
            tmp.cleanup()

    def test_short_session_skipped(self):
        store, tmp = make_store()
        try:
            store.add_message("s1", "user", "hi")
            llm = FakeLLM()
            result = asyncio.run(
                SessionReviewer(llm, store, MemoryExtractorForTest(llm, store)).review("s1"))
            self.assertEqual(result["saved"], 0)
            self.assertEqual(llm.chat_calls, 0)
        finally:
            store.close()
            tmp.cleanup()


class MemoryExtractorForTest:
    """轻量替身：直接复用真实 MemoryExtractor 但注入 Fake 向量库。"""

    def __new__(cls, llm, store):
        from memory.extractor import MemoryExtractor
        return MemoryExtractor(llm, store, FakeVectorStore())


class TestProfileConsolidation(unittest.TestCase):
    def test_memories_consolidated_into_profile(self):
        store, tmp = make_store()
        try:
            store.add_memory("用户叫老王", "fact", 0.9, "s1")
            store.add_memory("用户是后端开发", "fact", 0.8, "s1")
            llm = FakeLLM(json_results=[{
                "称呼": "老王", "职业": "后端开发", "居住地": ""  # 空值应被过滤
            }])
            written = asyncio.run(ProfileAgent(llm, store).consolidate())
            self.assertEqual(written, {"称呼": "老王", "职业": "后端开发"})
            self.assertEqual(store.get_profile()["称呼"], "老王")
            self.assertNotIn("居住地", store.get_profile())
        finally:
            store.close()
            tmp.cleanup()

    def test_too_few_memories_skips_llm(self):
        store, tmp = make_store()
        try:
            store.add_memory("只有一条", "fact", 0.9, "s1")
            llm = FakeLLM(json_results=[[1]])
            self.assertEqual(asyncio.run(ProfileAgent(llm, store).consolidate()), {})
            self.assertEqual(llm.json_calls, 0)
        finally:
            store.close()
            tmp.cleanup()


class TestStrategist(unittest.TestCase):
    def test_crisis_detected_with_guardian_instruction(self):
        llm = FakeLLM(json_results=[{
            "emotion": "crisis", "intent": "venting", "crisis": True,
            "instruction": "认真回应，不要玩笑"}])
        strategy = asyncio.run(StrategistAgent(llm).decide("我觉得活着没意思"))
        self.assertTrue(strategy.crisis)
        self.assertIn("12356", strategy.instruction)  # 兜底守护指令带热线

    def test_normal_turn_passthrough(self):
        llm = FakeLLM(json_results=[{
            "emotion": "happy", "intent": "sharing", "crisis": False,
            "instruction": "ta 在兴头上，一起开心"}])
        strategy = asyncio.run(StrategistAgent(llm).decide("今天升职啦！"))
        self.assertFalse(strategy.crisis)
        self.assertEqual(strategy.instruction, "ta 在兴头上，一起开心")

    def test_llm_failure_falls_back_to_default(self):
        class BoomLLM(FakeLLM):
            async def chat_json(self, messages, **kw):
                raise RuntimeError("网络挂了")

        strategy = asyncio.run(StrategistAgent(BoomLLM()).decide("随便聊聊"))
        self.assertFalse(strategy.crisis)
        self.assertEqual(strategy.instruction, "")


class TestCompanionIntegration(unittest.TestCase):
    def test_strategy_and_prior_summary_injected(self):
        store, tmp = make_store()
        try:
            store.set_session_summary("prev", "上次聊了猫")
            llm = FakeLLM(json_results=[{
                "emotion": "sad", "intent": "venting", "crisis": False,
                "instruction": "先共情，不给建议"}])
            companion = CompanionAgent(llm, store, Retriever(store, FakeVectorStore()),
                                       StrategistAgent(llm))
            async def run():
                store.add_message("s1", "user", "今天有点累")
                system = await asyncio.to_thread(
                    companion.build_system_prompt, "今天有点累", "s1")
                chunks = [c async for c in companion.reply("s1", "今天有点累")]
                return system, "".join(chunks)

            system, reply = asyncio.run(run())
            # 策略指令在 reply 内部拼进最终 system：从实际发给 LLM 的消息验证
            final_system = llm.last_stream_messages[0]["content"]
            self.assertIn("【本轮策略】先共情，不给建议", final_system)
            self.assertIn("上次聊了猫", final_system)
            self.assertEqual(reply, "好的")
        finally:
            store.close()
            tmp.cleanup()


class TestFixes(unittest.TestCase):
    """系统测试发现的存量问题的回归测试。"""

    def test_crisis_flag_written_then_consumed_once(self):
        from agents.companion import FOLLOWUP_KEY
        store, tmp = make_store()
        try:
            # 仅 high 级危机才写跟进标记（low 只当轮守护，避免标记被一般沮丧污染）
            llm = FakeLLM(json_results=[{
                "emotion": "crisis", "intent": "venting", "crisis": True,
                "severity": "high", "instruction": "认真回应"}])
            companion = CompanionAgent(llm, store, Retriever(store, FakeVectorStore()),
                                       StrategistAgent(llm))

            async def run():
                store.add_message("s1", "user", "我真的不想活了")
                async for _ in companion.reply("s1", "我真的不想活了"):
                    break
                # 当轮已构建 system，标记应保留给「下次会话」
                assert FOLLOWUP_KEY in store.get_profile()
                system = await asyncio.to_thread(
                    companion.build_system_prompt, "我回来啦", "s2")
                return system

            system = asyncio.run(run())
            self.assertIn("关心提醒", system)
            self.assertNotIn(FOLLOWUP_KEY, store.get_profile())  # 消费即清除
        finally:
            store.close()
            tmp.cleanup()

    def test_reviewer_dedup_archives_duplicates(self):
        from memory.extractor import MemoryExtractor
        store, tmp = make_store()
        try:
            id1 = store.add_memory("用户现在住在上海", "fact", 0.8, "s1")
            id2 = store.add_memory("用户这周刚搬到上海", "fact", 0.7, "s1")
            id3 = store.add_memory("用户养了只猫叫年糕", "fact", 0.8, "s1")
            llm = FakeLLM(json_results=[{"groups": [{"keep": 1, "remove": [2]}]}])
            vs = FakeVectorStore()
            reviewer = SessionReviewer(llm, store,
                                       MemoryExtractor(llm, store, vs), vs)
            # LLM 的编号基于 all_active_memories 的实际顺序（importance 降序）
            ordered = [m["id"] for m in store.all_active_memories()]
            removed = asyncio.run(reviewer._deduplicate())
            self.assertEqual(removed, 1)
            self.assertEqual(store.get_memory(ordered[0])["status"], "active")    # keep
            self.assertEqual(store.get_memory(ordered[1])["status"], "archived")  # remove
            self.assertEqual(store.get_memory(ordered[2])["status"], "active")    # 无关不动
            self.assertIn(str(ordered[1]), vs.deleted)  # 索引同步清理
        finally:
            store.close()
            tmp.cleanup()

    def test_fallback_memories_not_touched(self):
        store, tmp = make_store()
        try:
            for text in ["用户叫小王", "用户养了只猫叫年糕"]:
                store.add_memory(text, "fact", 0.9, "s1")
            retriever = Retriever(store, FakeVectorStore())  # enabled 但无命中
            active, past = retriever.retrieve("完全无关的新话题")
            self.assertEqual(len(active), 2)  # 兜底仍注入核心记忆
            untouched = all(m["access_count"] == 0
                            for m in store.all_active_memories())
            self.assertTrue(untouched)  # 但不虚假续命
        finally:
            store.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
