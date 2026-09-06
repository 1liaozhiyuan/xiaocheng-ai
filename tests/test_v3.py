"""V3 单测：主动开场 / 危机分级 / 标记时序。

python -m unittest tests.test_v3 -v
"""
import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

from agents.proactive import ProactiveAgent
from agents.strategist import StrategistAgent, TurnStrategy

from tests.test_v2 import FakeLLM, FakeVectorStore, make_store


class TestProactiveGreeting(unittest.TestCase):
    def _agent(self, llm, store):
        return ProactiveAgent(llm, store)

    def test_followup_flag_consumed_on_success(self):
        store, tmp = make_store()
        try:
            store.set_profile("关注标记", "09月05日 ta 表达过危机信号，先关心")
            llm = FakeLLM(chat_texts=["昨天听你说很难受，今天感觉好点吗？"])
            greeting = asyncio.run(self._agent(llm, store).greeting())
            self.assertIn("难受", greeting)
            self.assertNotIn("关注标记", store.get_profile())  # 成功即消费
        finally:
            store.close()
            tmp.cleanup()

    def test_failure_keeps_flag_for_fallback(self):
        store, tmp = make_store()
        try:
            store.set_profile("关注标记", "先关心 ta")

            class BoomChat(FakeLLM):
                async def chat_stream(self, messages, **kw):
                    raise RuntimeError("网络挂了")
                    yield  # pragma: no cover

            greeting = asyncio.run(self._agent(BoomChat(), store).greeting())
            self.assertIsNone(greeting)
            self.assertIn("关注标记", store.get_profile())  # 保留给兜底注入路径
        finally:
            store.close()
            tmp.cleanup()

    def test_simple_greeting_without_flag(self):
        store, tmp = make_store()
        try:
            llm = FakeLLM(chat_texts=["嗨，来啦～"])
            greeting = asyncio.run(self._agent(llm, store).greeting())
            self.assertEqual(greeting, "嗨，来啦～")
            self.assertEqual(store.get_profile(), {})
        finally:
            store.close()
            tmp.cleanup()

    def test_context_gathers_commitments_and_followup(self):
        store, tmp = make_store()
        try:
            store.set_profile("关注标记", "先关心 ta")
            store.add_memory("答应周五提醒用户给年糕买猫粮", "relationship", 0.8, "s1")
            store.set_session_summary("s0", "上次聊了猫粮")
            agent = self._agent(FakeLLM(), store)
            ctx = agent._gather()
            self.assertIn("猫粮", ctx["commitments"])
            self.assertIn("先关心", ctx["followup"])
            self.assertIn("猫粮", ctx["last_summary"])
            self.assertIn(datetime.now().strftime("%Y-%m-%d"), ctx["now"])
        finally:
            store.close()
            tmp.cleanup()


class TestCrisisSeverity(unittest.TestCase):
    def test_high_severity_parsed_and_fallback_injected(self):
        llm = FakeLLM(json_results=[{
            "emotion": "crisis", "intent": "venting", "crisis": True,
            "severity": "high", "instruction": "认真回应"}])
        strategy = asyncio.run(StrategistAgent(llm).decide("我不想活了，已经想好了"))
        self.assertTrue(strategy.crisis)
        self.assertEqual(strategy.severity, "high")
        self.assertIn("12356", strategy.instruction)  # high 兜底指令带热线
        self.assertIn("110/120", strategy.instruction)

    def test_low_severity_and_crisis_implied(self):
        llm = FakeLLM(json_results=[{
            "emotion": "crisis", "intent": "venting", "crisis": True,
            "severity": "low", "instruction": ""}])  # 指令缺失 → low 兜底
        strategy = asyncio.run(StrategistAgent(llm).decide("觉得撑不住了"))
        self.assertEqual(strategy.severity, "low")
        self.assertIn("温柔", strategy.instruction)

    def test_crisis_without_severity_defaults_low(self):
        llm = FakeLLM(json_results=[{
            "emotion": "crisis", "intent": "venting", "crisis": True,
            "severity": "none", "instruction": "回应"}])
        strategy = asyncio.run(StrategistAgent(llm).decide("撑不住"))
        self.assertEqual(strategy.severity, "low")

    def test_high_without_evidence_downgraded_to_low(self):
        # 「撑不住」无明确意念词汇：模型判 high 时降为 low（守护当轮生效、不写跨会话标记）
        llm = FakeLLM(json_results=[{
            "emotion": "crisis", "intent": "venting", "crisis": True,
            "severity": "high", "instruction": "认真回应"}])
        strategy = asyncio.run(StrategistAgent(llm).decide("最近压力好大，感觉快撑不住了"))
        self.assertEqual(strategy.severity, "low")
        self.assertTrue(strategy.crisis)

    def test_high_with_evidence_kept(self):
        llm = FakeLLM(json_results=[{
            "emotion": "crisis", "intent": "venting", "crisis": True,
            "severity": "high", "instruction": "认真回应"}])
        strategy = asyncio.run(StrategistAgent(llm).decide("我真的不想活了"))
        self.assertEqual(strategy.severity, "high")

    def test_contradictory_output_severity_wins(self):
        # 模型输出矛盾（非危机却给了 high）时，severity 优先 → 保守触发守护。
        # 安全设计：宁可误报（回复更认真），不可漏报。
        llm = FakeLLM(json_results=[{
            "emotion": "happy", "intent": "sharing", "crisis": False,
            "severity": "high",
            "instruction": "一起开心"}])
        strategy = asyncio.run(StrategistAgent(llm).decide("今天升职啦！"))
        self.assertTrue(strategy.crisis)


if __name__ == "__main__":
    unittest.main()
