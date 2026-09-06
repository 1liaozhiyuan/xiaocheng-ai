"""主动开场 Agent：启动时小澄先开口，从「被动应答」变成「主动在乎」。

开场白综合四类素材（按优先级）：危机跟进 > 特殊日子 > 约定跟进 > 日常问候。
设计约束：
- 增强件：任何失败静默回退（无输出 → 主循环等待用户先说），不阻塞启动
- 流式生成：智谱非流式请求聚合极慢（实测 25s+），流式首字秒级，且
  「打字机」效果更自然；调用方边收边打印
- 关注标记的时序：成功产出开场白后消费；失败则保留，
  由 companion.build_system_prompt 的兜底注入路径接住
"""
from datetime import datetime
from typing import AsyncIterator, Optional

import config
from llm.client import LLMClient
from memory.store import MemoryStore
from persona import prompts


class ProactiveAgent:
    def __init__(self, llm: LLMClient, store: MemoryStore) -> None:
        self._llm = llm
        self._store = store

    async def greeting(self) -> Optional[str]:
        """收集完整开场白（测试与编程用）。生产路径请用 greeting_stream。"""
        parts = [d async for d in self.greeting_stream()]
        return "".join(parts).strip() or None

    async def greeting_stream(self) -> AsyncIterator[str]:
        """流式生成开场白。失败时已输出的部分保留，标记留给兜底路径。"""
        context = self._gather()
        collected: list[str] = []
        try:
            async for delta in self._llm.chat_stream(
                [
                    {"role": "system",
                     "content": prompts.PROACTIVE_GREETING_SYSTEM.format(
                         name=config.PERSONA_NAME,
                         now=context["now"],
                         followup=context["followup"],
                         profile=context["profile"],
                         commitments=context["commitments"],
                         last_summary=context["last_summary"],
                         mood_trend=context["mood_trend"],
                         milestone=context["milestone"],
                     )},
                    # 智谱等厂商要求 messages 必须含 user 角色
                    {"role": "user", "content": "请生成现在的开场白。"},
                ],
                temperature=0.7, purpose="greeting",
            ):
                collected.append(delta)
                yield delta
        except Exception:
            pass  # 中途失败：已输出的保留，无输出则调用方回退为等待用户
        text = "".join(collected).strip()
        if text and context["followup"]:
            self._store.delete_profile(config.FOLLOWUP_KEY)

    def _gather(self) -> dict:
        now = datetime.now()
        profile = self._store.get_profile()
        followup = profile.get(config.FOLLOWUP_KEY, "（无）")
        # 画像瘦身：开场不需要完整风格字段
        profile_items = [f"{k}: {v}" for k, v in profile.items()
                         if k != config.FOLLOWUP_KEY][:8]
        commitments = [
            m["content"] for m in self._store.all_active_memories()
            if m["category"] == "relationship"
        ][:5] or ["（无）"]
        summaries = self._store.recent_summaries(limit=1)
        trend = self._store.emotion_trend()
        if trend is None:
            mood = "（暂无记录）"
        elif trend < 4.0:
            mood = (f"最近平均 {trend:.1f}/10，持续偏低——开场请更温柔，"
                    f"主动关心 ta 这段时间的状态，不要只打招呼")
        elif trend < 6.0:
            mood = f"最近平均 {trend:.1f}/10，平平偏淡，可以顺口问问近况"
        else:
            mood = f"最近平均 {trend:.1f}/10，状态不错"
        # 关系里程碑：认识天数，纪念日当天提醒开场提及
        milestone = "（暂无记录）"
        if profile.get("初次见面"):
            try:
                days = (datetime.now()
                        - datetime.strptime(profile["初次见面"], "%Y-%m-%d")).days
                milestone = f"你们认识第 {days + 1} 天"
                if days in (6, 29, 99, 364):
                    milestone += "——今天正好是满 7/30/100/365 天的日子，值得自然地提一句！"
            except ValueError:
                pass
        return {
            "now": now.strftime("%Y-%m-%d %H:%M %A"),
            "followup": followup,
            "profile": "；".join(profile_items) or "（暂无）",
            "commitments": "；".join(commitments),
            "last_summary": summaries[0]["summary"] if summaries else "（首次见面）",
            "mood_trend": mood,
            "milestone": milestone,
        }
