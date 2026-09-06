"""会话复盘（SessionReviewer）：会话结束时的一次「睡前整理」。

解决单轮抽取的两个盲区：
1. 漏抽——闲聊语境里顺带提到的自身事实，整段重看时更容易被抓到
2. 跨轮因果——8 条窗口外的事，整段视角下能关联起来

同时生成本次会话摘要存入 sessions 表，作为下次会话的工作记忆
（「上次你们聊到…」），让关系跨会话延续。
"""
import config
from llm.client import LLMClient
from memory.extractor import MemoryExtractor
from memory.store import MemoryStore
from persona import prompts


class SessionReviewer:
    def __init__(self, llm: LLMClient, store: MemoryStore,
                 extractor: MemoryExtractor, vector_store=None) -> None:
        self._llm = llm
        self._store = store
        self._extractor = extractor
        self._vector_store = vector_store  # 去重清理索引用；None 时仅归档

    async def review(self, session_id: str, min_messages: int = 4) -> dict:
        """复盘一个会话：补漏抽取 + 同义去重 + 生成摘要。返回复盘结果。"""
        dialog = self._store.session_messages(session_id)
        if len(dialog) < min_messages:
            return {"saved": 0, "summary": None, "deduped": 0,
                    "skipped": "对话太短，无需复盘"}

        # 1) 补漏抽取：整段对话直接走抽取 → 粗筛 → 决策 → 入库的完整管线
        saved = await self._extractor.extract_and_store(session_id, dialog)

        # 2) 同义去重：清掉逐轮抽取积累的重复表述（措施不同的事实不动）
        deduped = await self._deduplicate()

        # 3) 情绪快照：长期趋势追踪（连续低迷时主动开场会更关切）
        emotion = await self._assess_emotion(dialog, session_id)

        # 4) 会话摘要（供下次会话注入「上次你们聊到」）
        summary = (await self._llm.chat(
            [
                {"role": "system", "content": prompts.SESSION_SUMMARY_SYSTEM},
                {"role": "user", "content": _fmt_dialog(dialog)},
            ],
            model=None, temperature=0.3, purpose="summary",
        )).strip()
        self._store.set_session_summary(session_id, summary)

        # 5) 小澄的日记：以她的视角记下今天的相处（情感连续性）
        diary = (await self._llm.chat(
            [
                {"role": "system", "content": prompts.DIARY_SYSTEM.format(
                    name=config.PERSONA_NAME)},
                {"role": "user", "content": _fmt_dialog(dialog)},
            ],
            model=None, temperature=0.7, purpose="diary",
        )).strip()
        if diary:
            from datetime import datetime
            self._store.set_diary(datetime.now().strftime("%Y-%m-%d"), diary)

        return {"saved": len(saved), "deduped": deduped, "summary": summary,
                "emotion": emotion, "diary": diary or None}

    async def _assess_emotion(self, dialog: list[dict], session_id: str) -> dict | None:
        result = await self._llm.chat_json(
            [
                {"role": "system", "content": prompts.EMOTION_ASSESS_SYSTEM},
                {"role": "user", "content": _fmt_dialog(dialog)},
            ],
            temperature=0.0, purpose="emotion",
        )
        if not isinstance(result, dict):
            return None
        try:
            score = max(1.0, min(10.0, float(result.get("score", 5))))
        except (TypeError, ValueError):
            return None
        label = str(result.get("label", "")).strip()[:10]
        note = str(result.get("note", "")).strip()[:100]
        self._store.add_emotion_snapshot(session_id, score, label, note)
        return {"score": score, "label": label, "note": note}

    async def _deduplicate(self) -> int:
        """让轻量模型找出「同一事实的重复表述」组，保留一条、其余归档。

        与取代链的分工：SUPERSEDE 处理事实变化（旧状态有历史价值），
        这里处理纯重复（无历史价值 → 归档并清出检索索引）。
        """
        actives = self._store.all_active_memories()
        if len(actives) < 3:
            return 0
        result = await self._llm.chat_json(
            [
                {"role": "system", "content": prompts.MEMORY_DEDUP_SYSTEM},
                {"role": "user", "content": prompts.MEMORY_DEDUP_USER.format(
                    memories="\n".join(
                        f"{i}. {m['content']}" for i, m in enumerate(actives, 1)
                    ),
                )},
            ],
            temperature=0.0, purpose="dedup",
        )
        if not isinstance(result, dict):
            return 0
        removed = 0
        for group in result.get("groups", []) if isinstance(result.get("groups"), list) else []:
            try:
                keep = actives[int(group["keep"]) - 1]
                remove_ids = [int(r) - 1 for r in group.get("remove", [])]
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if not (0 <= int(group["keep"]) - 1 < len(actives)):
                continue
            for ri in remove_ids:
                if 0 <= ri < len(actives) and ri != int(group["keep"]) - 1:
                    m = actives[ri]
                    if m["id"] != keep["id"] and m["status"] == "active":
                        self._store.archive_memory(m["id"])
                        self._vs_delete(m["id"])
                        removed += 1
        return removed

    def _vs_delete(self, memory_id: int) -> None:
        """向量索引清理的容错包装（去重是整理性质，失败不影响记忆数据）。"""
        try:
            self._vector_store.delete(memory_id)
        except Exception:
            pass


def _fmt_dialog(dialog: list[dict]) -> str:
    return "\n".join(f"{'用户' if m['role'] == 'user' else '陪伴者'}：{m['content']}"
                     for m in dialog)
