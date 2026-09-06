"""画像更新 Agent：「睡眠整理」——把零散的情景记忆归纳成结构化用户画像。

画像是当前状态的快照（KV 存储，user_profile 表），记忆层保存完整轨迹。
主对话的 system prompt 已通过 PROFILE_BLOCK 注入画像，本 Agent 一旦填充，
「它知道我是谁、在做什么、最近怎么样」的体验立即生效。

触发：低频（每次会话复盘后），不在聊天主链路上。
"""
import config
from llm.client import LLMClient
from memory.store import MemoryStore
from persona import prompts


class ProfileAgent:
    def __init__(self, llm: LLMClient, store: MemoryStore) -> None:
        self._llm = llm
        self._store = store

    async def consolidate(self, min_memories: int = 2) -> dict[str, str]:
        """基于全部 active 记忆归纳更新画像，返回本次写入的字段。"""
        memories = self._store.all_active_memories()
        if len(memories) < min_memories:
            return {}

        result = await self._llm.chat_json(
            [
                {"role": "system", "content": prompts.PROFILE_CONSOLIDATION_SYSTEM},
                {"role": "user", "content": prompts.PROFILE_CONSOLIDATION_USER.format(
                    profile=self._store.get_profile() or "（暂无）",
                    memories="\n".join(
                        f"- ({m['category']}, {m['created_at'][:10]}) {m['content']}"
                        for m in memories[:60]),
                )},
            ],
            temperature=0.0, purpose="profile",
        )
        if not isinstance(result, dict):
            return {}

        kv = {str(k).strip(): str(v).strip()
              for k, v in result.items() if str(v).strip()}
        self._store.set_profile_bulk(kv)
        return kv
