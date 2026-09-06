"""主对话 Agent（Persona）：构建上下文并流式生成回复。

上下文 = 人设 + 画像 + 会话摘要（上次聊到哪）+ 分层记忆 + 近期历史
       + 本轮策略指令（情绪基调 / 危机守护）。

危机守护的连续性：本轮判定 crisis 时写入「关注标记」，下次会话的
第一轮回复消费该标记——开场自然地关心 ta 的近况，而不是等 ta 提。
"""
import asyncio
from datetime import datetime
from typing import AsyncIterator, Optional

import config
from agents.strategist import StrategistAgent, TurnStrategy
from llm.client import LLMClient
from memory.retriever import Retriever
from memory.store import MemoryStore
from persona import prompts

FOLLOWUP_KEY = config.FOLLOWUP_KEY


class CompanionAgent:
    def __init__(self, llm: LLMClient, store: MemoryStore, retriever: Retriever,
                 strategist: Optional[StrategistAgent] = None) -> None:
        self._llm = llm
        self._store = store
        self._retriever = retriever
        self._strategist = strategist

    def build_system_prompt(self, user_message: str, session_id: str = "") -> str:
        profile = self._store.get_profile()
        active_memories, past_memories = self._retriever.retrieve(user_message)

        parts = [prompts.COMPANION_SYSTEM.format(
            name=config.PERSONA_NAME, brief=config.PERSONA_BRIEF
        )]
        if profile:
            items = "\n".join(f"- {k}: {v}" for k, v in profile.items())
            parts.append(prompts.PROFILE_BLOCK.format(items=items))
        # 危机跟进标记：一次性消费，只在下次会话的第一轮注入
        followup = self._store.get_profile().get(FOLLOWUP_KEY)
        if followup:
            self._store.delete_profile(FOLLOWUP_KEY)
            parts.append(f"【关心提醒】{followup}\n"
                         f"开场时自然地先关心 ta 的近况，不要机械复述这句话。")
        summaries = self._store.recent_summaries(limit=2, exclude_session=session_id)
        if summaries:
            items = "\n".join(f"- {s['summary']}" for s in summaries)
            parts.append("【上次你们聊到】\n" + items)
        if active_memories:
            items = "\n".join(f"- {m['content']}" for m in active_memories)
            parts.append(prompts.MEMORY_BLOCK.format(items=items))
        if past_memories:
            items = "\n".join(
                f"- 【过往】{m['content']}（{m['created_at'][:10]}记录）"
                for m in past_memories
            )
            parts.append(prompts.PAST_BLOCK.format(items=items))
        return "\n\n".join(parts)

    async def reply(self, session_id: str, user_message: str,
                    strategy_task=None) -> AsyncIterator[str]:
        # 策略判断与记忆检索互不依赖：并行执行（各省 1~2 秒首字延迟）。
        # strategy_task 允许调用方传入已启动的策略任务（Web 层复用同一任务）。
        strat_task = strategy_task
        if strat_task is None and self._strategist is not None:
            strat_task = asyncio.create_task(self._strategist.decide(
                user_message, self._store.recent_messages(session_id, limit=6)
            ))

        history = self._store.recent_messages(session_id, limit=config.HISTORY_WINDOW)
        # 检索含 embedding API 调用，放线程里；与上方的策略任务并行执行
        system_prompt = await asyncio.to_thread(
            self.build_system_prompt, user_message, session_id
        )

        strategy = None
        if strat_task is not None:
            strategy = await strat_task  # 检索完成时策略通常也已就绪
            if strategy.severity == "high":
                # 仅重度危机才做跨会话跟进标记：low 级的守护当轮生效即可，
                # 否则一般沮丧也会留下「危机信号」标记，稀释真正危机时的关心
                today = datetime.now().strftime("%m月%d日")
                self._store.set_profile(
                    FOLLOWUP_KEY,
                    f"{today} ta 表达过重度危机信号（「{user_message[:40]}」），"
                    f"见面时先郑重地关心 ta 最近的状态",
                )
            if strategy.instruction:
                system_prompt += f"\n\n【本轮策略】{strategy.instruction}"

        # 快慢混合：日常闲聊用快模型保手感；情绪浓度高（倾诉/低落/危机）
        # 自动切重模型——共情和语气是陪伴的命门，这几点慢一点值得
        model = config.CHAT_MODEL
        if strategy is not None and (
            strategy.crisis
            or strategy.emotion in ("sad", "anxious", "lonely", "crisis")
            or strategy.intent == "venting"
        ):
            model = config.CHAT_MODEL_HEAVY

        messages = (
            [{"role": "system", "content": system_prompt}]
            + [{"role": m["role"], "content": m["content"]} for m in history]
            + [{"role": "user", "content": user_message}]
        )
        async for delta in self._llm.chat_stream(messages, model=model, purpose="chat"):
            yield delta
