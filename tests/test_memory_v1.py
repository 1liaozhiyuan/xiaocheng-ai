"""V1 记忆系统单测：取代链 / 合并决策 / 分层检索 / 遗忘衰减。

python -m unittest tests.test_memory_v1 -v
"""
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from memory.curator import Curator
from memory.extractor import MemoryExtractor, _normalize_perspective
from memory.retriever import Retriever
from memory.store import MemoryStore


class TestPerspectiveNormalization(unittest.TestCase):
    def test_first_person_rewritten_to_user(self):
        self.assertEqual(_normalize_perspective("我叫小王，做后端开发的"), "用户叫小王，做后端开发的")
        self.assertEqual(_normalize_perspective("我住在杭州"), "用户住在杭州")
        self.assertEqual(_normalize_perspective("我生日是 3 月 12 号"), "用户生日是 3 月 12 号")
        self.assertEqual(_normalize_perspective("用户养了只猫"), "用户养了只猫")  # 已合规不动
        self.assertEqual(_normalize_perspective("我喜欢在周末爬山"), "用户喜欢在周末爬山")

    def test_subjectless_prefixed_with_user(self):
        self.assertEqual(_normalize_perspective("在一家游戏公司做后端"),
                         "用户在一家游戏公司做后端")
        self.assertEqual(_normalize_perspective("最近在学吉他"), "用户最近在学吉他")
        self.assertEqual(_normalize_perspective("年糕三岁"), "年糕三岁")  # 第三方记忆不改写

OLD_MEMORY = "用户住在北京"
NEW_MEMORY = "用户下个月搬去上海工作"


class QueueLLM:
    """按预设顺序返回 chat_json 结果（第一次=抽取，之后=逐条决策）。"""

    def __init__(self, json_results):
        self._results = list(json_results)
        self.calls = 0

    async def chat_json(self, messages, **kw):
        self.calls += 1
        return self._results.pop(0)


class FakeVectorStore:
    """enabled + 可配置的 search 命中（供查重与分层检索测试用）。"""

    def __init__(self, hits=None, enabled=True):
        self.hits = hits or []
        self.enabled = enabled
        self.docs = {}

    def add(self, memory_id, content, metadata):
        self.docs[str(memory_id)] = content

    def search(self, query, top_k):
        return self.hits[:top_k]


def make_store():
    tmp = tempfile.TemporaryDirectory()
    store = MemoryStore(Path(tmp.name) / "t.db")
    return store, tmp


class TestSupersedeChain(unittest.TestCase):
    def test_supersede_links_old_to_new(self):
        store, tmp = make_store()
        try:
            old_id = store.add_memory(OLD_MEMORY, "fact", 0.8, "s1")
            new_id = store.add_memory("用户搬去上海了，现住上海", "fact", 0.85, "s2")
            store.supersede(old_id, new_id)

            old = store.get_memory(old_id)
            self.assertEqual(old["status"], "superseded")
            self.assertEqual(old["superseded_by"], new_id)
            self.assertEqual(old["content"], OLD_MEMORY)  # 原文不改
            self.assertEqual(store.memories_by_status("superseded")[0]["id"], old_id)
            self.assertEqual([m["id"] for m in store.all_active_memories()], [new_id])
        finally:
            store.close()
            tmp.cleanup()


class TestExtractorDecision(unittest.TestCase):
    def _extract(self, json_results, similar_hits=None):
        """similar_hits 非空时，语义查重会命中旧记忆并进入 LLM 决策。

        返回 (store, saved, llm, tmp)：调用方负责 store.close() + tmp.cleanup()。
        """
        store, tmp = make_store()
        vs = FakeVectorStore(hits=similar_hits if similar_hits not in (None, "match") else [])
        old_id = store.add_memory(OLD_MEMORY, "fact", 0.8, "s0")
        if similar_hits == "match":
            vs.hits = [{"memory": store.get_memory(old_id), "similarity": 0.85}]
        llm = QueueLLM(json_results)
        saved = asyncio.run(MemoryExtractor(llm, store, vs).extract_and_store(
            "s1", [{"role": "user", "content": "我下个月搬去上海"}]))
        return store, saved, llm, tmp

    def test_supersede_decision_replaces_old(self):
        store, saved, _, tmp = self._extract(
            [
                [{"content": NEW_MEMORY, "category": "fact", "importance": 0.85}],
                {"decision": "SUPERSEDE"},
            ],
            similar_hits="match",
        )
        try:
            self.assertEqual(saved[0]["decision"], "SUPERSEDE")
            self.assertEqual([m["content"] for m in store.all_active_memories()], [NEW_MEMORY])
            old = store.memories_by_status("superseded")[0]
            self.assertEqual(old["content"], OLD_MEMORY)
            self.assertEqual(old["superseded_by"], saved[0]["id"])
        finally:
            store.close()
            tmp.cleanup()

    def test_skip_decision_keeps_library_unchanged(self):
        store, saved, _, tmp = self._extract(
            [
                [{"content": "用户住在北京市", "category": "fact", "importance": 0.8}],
                {"decision": "SKIP"},
            ],
            similar_hits="match",
        )
        try:
            self.assertEqual(saved, [])  # 重复信息不入库
            self.assertEqual([m["content"] for m in store.all_active_memories()], [OLD_MEMORY])
            self.assertEqual(store.memories_by_status("superseded"), [])
        finally:
            store.close()
            tmp.cleanup()

    def test_superseded_old_redirects_to_latest_before_deciding(self):
        # 旧记忆已被取代（链上有更新版本）时，决策对象应是最新记忆而非历史版本，
        # 避免「同一条旧记忆被重复取代」和「与最新状态对比错位」
        store, tmp = make_store()
        try:
            v1_id = store.add_memory("用户目前居住在北京", "fact", 0.8, "s0")
            v2_id = store.add_memory("用户这些年一直住在北京", "fact", 0.8, "s1")
            store.supersede(v1_id, v2_id)
            vs = FakeVectorStore(hits=[{"memory": store.get_memory(v1_id),
                                        "similarity": 0.9}])
            llm = QueueLLM([
                [{"content": "用户这些年住在北京呢", "category": "fact", "importance": 0.7}],
                {"decision": "SKIP"},  # 与最新版（v2）同义 → SKIP
            ])
            saved = asyncio.run(MemoryExtractor(llm, store, vs).extract_and_store(
                "s2", [{"role": "user", "content": "我还在北京呢"}]))
            self.assertEqual(saved, [])  # 复述无新信息，不入库
            self.assertEqual(store.get_memory(v1_id)["superseded_by"], v2_id)  # 链未被破坏
        finally:
            store.close()
            tmp.cleanup()

    def test_no_similar_memory_skips_decision_call(self):
        # 向量查重未命中 → 走 LLM 联合粗筛（返回空 = 无同主题旧记忆）→ 直接 ADD
        store, saved, llm, tmp = self._extract(
            [
                [{"content": "用户喜欢周末爬山", "category": "preference", "importance": 0.7}],
                [],  # 粗筛结果：没有相关的旧记忆
            ],
        )
        try:
            self.assertEqual(saved[0]["decision"], "ADD")
            self.assertEqual(llm.calls, 2)  # 抽取 + 粗筛，无细决策调用
            self.assertEqual(len(store.all_active_memories()), 2)
        finally:
            store.close()
            tmp.cleanup()

    def test_topic_screening_catches_rephrased_conflict(self):
        # 模拟 e2e 发现的场景：表述差异大（搬家 vs 居住状态），向量未命中，
        # 粗筛识别出同主题 → 细决策 SUPERSEDE
        store, saved, _, tmp = self._extract(
            [
                [{"content": "用户马上要搬家了，从北京搬到上海", "category": "event",
                  "importance": 0.85}],
                [{"index": 1, "related": [1]}],  # 粗筛：与新信息相关的是第 1 条旧记忆
                {"decision": "SUPERSEDE"},
            ],
        )
        try:
            self.assertEqual(saved[0]["decision"], "SUPERSEDE")
            old = store.memories_by_status("superseded")[0]
            self.assertEqual(old["content"], OLD_MEMORY)
        finally:
            store.close()
            tmp.cleanup()


class TestRetrieverLayering(unittest.TestCase):
    def _prepare(self, old_sim=None, enabled=True):
        store, tmp = make_store()
        try:
            old_id = store.add_memory(OLD_MEMORY, "fact", 0.8, "s0")
            new_id = store.add_memory("用户现住上海", "fact", 0.85, "s1")
            store.supersede(old_id, new_id)
            hits = [{"memory": store.get_memory(new_id), "similarity": 0.9}]
            if old_sim is not None:
                hits.append({"memory": store.get_memory(old_id), "similarity": old_sim})
            vs = FakeVectorStore(hits=hits, enabled=enabled)
            return store, Retriever(store, vs), tmp
        except Exception:
            store.close()
            tmp.cleanup()
            raise

    def test_semantic_hit_surfaces_history(self):
        store, retriever, tmp = self._prepare(old_sim=0.85)
        try:
            active, past = retriever.retrieve("我一直住过的城市有哪些来着")
            self.assertEqual([m["content"] for m in active], ["用户现住上海"])
            self.assertEqual([m["content"] for m in past], [OLD_MEMORY])
        finally:
            store.close()
            tmp.cleanup()

    def test_low_similarity_hides_history(self):
        store, retriever, tmp = self._prepare(old_sim=0.5)
        try:
            active, past = retriever.retrieve("今天吃什么好")
            self.assertEqual([m["content"] for m in active], ["用户现住上海"])
            self.assertEqual(past, [])  # 日常闲聊不翻旧账
        finally:
            store.close()
            tmp.cleanup()

    def test_fallback_mode_uses_history_keywords(self):
        store, retriever, tmp = self._prepare(enabled=False)
        try:
            _, past = retriever.retrieve("我以前都住过哪里来着")
            self.assertEqual([m["content"] for m in past], [OLD_MEMORY])
            _, past = retriever.retrieve("晚饭吃什么")
            self.assertEqual(past, [])
        finally:
            store.close()
            tmp.cleanup()


class TestCurator(unittest.TestCase):
    def _store_with(self, content, category, importance, days_ago):
        store, tmp = make_store()
        mid = store.add_memory(content, category, importance, "s1")
        past = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        store._exec("UPDATE memories SET created_at = ?, last_accessed_at = ? WHERE id = ?",
                    (past, past, mid))
        return store, tmp, mid

    def test_stale_emotion_archives_recent_fact_survives(self):
        store, tmp, emotion_id = self._store_with(
            "用户这周很焦虑", "emotion", 0.6, days_ago=45)
        try:
            fact_id = store.add_memory("用户养了只猫叫年糕", "fact", 0.9, "s1")
            archived = Curator(store).run()
            self.assertGreaterEqual(archived, 1)
            self.assertEqual(store.get_memory(emotion_id)["status"], "archived")
            self.assertEqual(store.get_memory(fact_id)["status"], "active")
        finally:
            store.close()
            tmp.cleanup()

    def test_frequent_recall_extends_life(self):
        # 0.6 的情绪记忆 45 天，若被回忆 10 次：0.6×0.5^(45/10)+0.4 ≈ 0.42 → 存活
        store, tmp, mid = self._store_with(
            "用户这周很焦虑", "emotion", 0.6, days_ago=45)
        try:
            store._exec("UPDATE memories SET access_count = 10 WHERE id = ?", (mid,))
            Curator(store).run()
            self.assertEqual(store.get_memory(mid)["status"], "active")
        finally:
            store.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
