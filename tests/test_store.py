"""存储层单测：python -m pytest tests/ -q  或  python -m unittest tests.test_store"""
import tempfile
import unittest
from pathlib import Path

from memory.store import MemoryStore


class TestMemoryStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_message_roundtrip(self):
        self.store.add_message("s1", "user", "你好")
        self.store.add_message("s1", "assistant", "嗨，今天怎么样")
        history = self.store.recent_messages("s1", limit=10)
        self.assertEqual([m["role"] for m in history], ["user", "assistant"])
        self.assertEqual(history[1]["content"], "嗨，今天怎么样")

    def test_memory_dedup(self):
        self.store.add_memory("用户养了只猫叫年糕", "fact", 0.9, "s1")
        self.assertTrue(self.store.memory_exists("用户养了只猫叫年糕"))
        self.assertFalse(self.store.memory_exists("用户喜欢爬山"))

    def test_profile_upsert(self):
        self.store.set_profile("称呼", "小王")
        self.store.set_profile("称呼", "老王")  # 覆盖更新
        self.assertEqual(self.store.get_profile()["称呼"], "老王")

    def test_archive_hides_memory(self):
        mid = self.store.add_memory("测试记忆", "fact", 0.5, "s1")
        self.store.archive_memory(mid)
        self.assertEqual(self.store.all_active_memories(), [])
        # 归档后 get_memory 仍可取到（供审计），但 active 列表不含
        self.assertEqual(self.store.get_memory(mid)["status"], "archived")


if __name__ == "__main__":
    unittest.main()
