"""V5 单测：情绪快照/趋势联动/向量预筛/会话重放。

python -m unittest tests.test_v5 -v
"""
import asyncio
import unittest

from agents.proactive import ProactiveAgent
from memory.extractor import MemoryExtractor
from memory.reviewer import SessionReviewer

from tests.test_v2 import FakeLLM, FakeVectorStore, make_store


class TestEmotionSnapshot(unittest.TestCase):
    def test_roundtrip_and_trend(self):
        store, tmp = make_store()
        try:
            store.add_emotion_snapshot("s1", 3.0, "低落", "考研受挫")
            store.add_emotion_snapshot("s2", 8.0, "开心", "项目上线")
            snaps = store.recent_emotion_snapshots(10)
            self.assertEqual([s["score"] for s in snaps], [3.0, 8.0])  # 时间正序
            self.assertAlmostEqual(store.emotion_trend(), 5.5)
            self.assertAlmostEqual(store.emotion_trend(window=1), 8.0)
        finally:
            store.close()
            tmp.cleanup()

    def test_review_writes_emotion_snapshot(self):
        store, tmp = make_store()
        try:
            for text in ["今天好累啊", "嗯", "想睡", "晚安"]:
                store.add_message("s1", "user", text)
                store.add_message("s1", "assistant", "好")
            llm = FakeLLM(
                chat_texts=["用户很累，早点休息了。", "今天 ta 很累，希望 ta 睡个好觉。"],
                json_results=[
                    [],                                   # 抽取：无候选
                    {"score": 3.5, "label": "疲惫", "note": "加班"},
                ],
            )
            vs = FakeVectorStore()
            result = asyncio.run(
                SessionReviewer(llm, store, MemoryExtractor(llm, store, vs), vs).review("s1"))
            self.assertEqual(result["emotion"]["label"], "疲惫")
            snaps = store.recent_emotion_snapshots(5)
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0]["score"], 3.5)
        finally:
            store.close()
            tmp.cleanup()

    def test_bad_score_ignored(self):
        store, tmp = make_store()
        try:
            for text in ["a", "b", "c", "d"]:
                store.add_message("s1", "user", text)
            llm = FakeLLM(
                chat_texts=["摘要", "今天的日记。"],
                json_results=[
                    [],                                    # 抽取：无候选
                    {"score": "很糟", "label": "?"},       # 情绪评估：非法分 → 忽略
                ],
            )
            vs = FakeVectorStore()
            result = asyncio.run(
                SessionReviewer(llm, store, MemoryExtractor(llm, store, vs), vs).review("s1"))
            self.assertIsNone(result["emotion"])
            self.assertEqual(store.recent_emotion_snapshots(5), [])
        finally:
            store.close()
            tmp.cleanup()


class TestMoodTrendInGreeting(unittest.TestCase):
    def test_low_trend_changes_greeting_context(self):
        store, tmp = make_store()
        try:
            for score in (3.0, 3.5, 2.5):
                store.add_emotion_snapshot("s", score, "低落", "")
            ctx = ProactiveAgent(FakeLLM(), store)._gather()
            self.assertIn("持续偏低", ctx["mood_trend"])
        finally:
            store.close()
            tmp.cleanup()

    def test_good_trend(self):
        store, tmp = make_store()
        try:
            store.add_emotion_snapshot("s", 8.0, "开心", "")
            ctx = ProactiveAgent(FakeLLM(), store)._gather()
            self.assertIn("状态不错", ctx["mood_trend"])
        finally:
            store.close()
            tmp.cleanup()

    def test_no_data(self):
        store, tmp = make_store()
        try:
            ctx = ProactiveAgent(FakeLLM(), store)._gather()
            self.assertIn("暂无记录", ctx["mood_trend"])
        finally:
            store.close()
            tmp.cleanup()


class TestPreFilterPool(unittest.TestCase):
    def test_small_library_untouched(self):
        store, tmp = make_store()
        try:
            for i in range(5):
                store.add_memory(f"记忆{i}", "fact", 0.5, "s")
            vs = FakeVectorStore()
            ext = MemoryExtractor(FakeLLM(), store, vs)
            actives = store.all_active_memories()
            pool = asyncio.run(ext._pre_filter_pool([{"content": "x"}], actives))
            self.assertEqual(len(pool), 5)  # 小库全量
        finally:
            store.close()
            tmp.cleanup()

    def test_large_library_uses_similarity_and_importance(self):
        store, tmp = make_store()
        try:
            relevant_id = store.add_memory("用户在杭州做后端", "fact", 0.4, "s")
            for i in range(60):
                store.add_memory(f"无关琐事{i}", "event", 0.6, "s")
            vs = FakeVectorStore(hits=[{"memory": store.get_memory(relevant_id),
                                        "similarity": 0.9}])
            ext = MemoryExtractor(FakeLLM(), store, vs)
            actives = store.all_active_memories()
            pool = asyncio.run(ext._pre_filter_pool(
                [{"content": "在杭州工作的近况"}], actives))
            self.assertEqual(len(pool), 40)  # 池上限
            self.assertIn(relevant_id, [m["id"] for m in pool])  # 相似记忆入选
        finally:
            store.close()
            tmp.cleanup()


class TestSessionReplay(unittest.TestCase):
    def test_messages_replay_from_store(self):
        # 重放逻辑只依赖 store.session_messages，验证数据形态
        store, tmp = make_store()
        try:
            store.add_message("s1", "user", "你好")
            store.add_message("s1", "assistant", "嗨")
            rows = store.recent_messages("s1", limit=100)
            out = [{"role": r["role"], "text": r["content"]} for r in rows]
            self.assertEqual(out, [{"role": "user", "text": "你好"},
                                   {"role": "assistant", "text": "嗨"}])
        finally:
            store.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
