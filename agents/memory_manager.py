"""记忆管理 Agent：聊天主链路之外的后台异步任务。

职责（V0 已实现抽取入库，其余为预留）：
- 每轮对话后调用 MemoryExtractor 提取记忆
- V1 TODO：记忆冲突合并（新旧记忆 UPDATE/DELETE 决策）
- V2 TODO：访问频次 × 时间衰减的遗忘/归档
"""
import asyncio
import traceback
from typing import Optional

import config
from llm.client import LLMClient
from memory.curator import Curator
from memory.extractor import MemoryExtractor
from memory.store import MemoryStore
from memory.vector_store import VectorStore


class MemoryManager:
    def __init__(self, llm: LLMClient, store: MemoryStore, vector_store: VectorStore,
                 extractor: Optional[MemoryExtractor] = None) -> None:
        # extractor 可注入共享实例（复盘也走同一管线）
        self._extractor = extractor or MemoryExtractor(llm, store, vector_store)
        self._curator = Curator(store)
        self._turn_count = 0

    def schedule_turn(self, session_id: str, turn_messages: list[dict]) -> asyncio.Task:
        """每轮对话结束后 fire-and-forget 调度，不阻塞主循环。"""
        self._turn_count += 1
        if self._turn_count % config.EXTRACT_EVERY_N_TURNS != 0:
            return asyncio.create_task(asyncio.sleep(0))  # 保持返回类型一致
        return asyncio.create_task(self._run(session_id, turn_messages))

    async def _run(self, session_id: str, turn_messages: list[dict]) -> list[dict]:
        try:
            saved = await self._extractor.extract_and_store(session_id, turn_messages)
            for m in saved:
                print(f"  💭 记住了：{m['content']}")
            # 抽取后顺带做一次本地整理（遗忘衰减），纯计算不耗时
            if self._curator.run():
                print("  🌙 有些久远的琐事已悄悄淡去（归档）")
            return saved
        except Exception:
            # 后台任务失败不能影响聊天，但也不能静默吞掉
            print("  ⚠️ 记忆抽取失败（不影响本轮对话）")
            traceback.print_exc()
            return []
