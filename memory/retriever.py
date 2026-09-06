"""记忆检索：给定当前用户消息，挑出要注入 prompt 的记忆。

V1 分层设计（配合取代链）：
- active 记忆 → 「相关记忆」区，日常聊天使用
- superseded 记忆 → 「过往经历」区，仅当语义高度命中（用户在问历史）
  或消息含历史关键词（降级模式）时少量注入，带【过往】标注

这样「我住过哪些城市」能召回历史，日常闲聊不会翻旧账。
"""
import math
from datetime import datetime

import config


def _recency_bonus(created_at: str) -> float:
    """越新越好，7 天半衰期的指数衰减。解析失败按 0 处理。"""
    try:
        dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return 0.0
    days = max(0.0, (datetime.now() - dt).total_seconds() / 86400)
    return math.exp(-days / 7.0)


def _is_history_question(message: str) -> bool:
    return any(kw in message for kw in config.HISTORY_KEYWORDS)


class Retriever:
    def __init__(self, store, vector_store) -> None:
        self._store = store
        self._vs = vector_store

    def retrieve(self, query: str) -> tuple[list[dict], list[dict]]:
        """返回 (当前记忆, 历史记忆)，并更新访问计数。"""
        scored: dict[int, dict] = {}
        past: list[tuple[float, dict]] = []

        # 1) 语义召回（向量库不可用时自然跳过）
        if self._vs.enabled:
            for hit in self._vs.search(query, top_k=config.MEMORY_TOP_K
                                       + config.SUPERSEDED_INJECT_MAX):
                m, sim = hit["memory"], hit["similarity"]
                if m["status"] == "active":
                    score = sim + 0.3 * m["importance"] + 0.2 * _recency_bonus(m["created_at"])
                    scored[m["id"]] = {"memory": m, "score": score}
                elif m["status"] == "superseded" and sim >= config.SUPERSEDED_INJECT_SIM:
                    past.append((sim, m))

        # 2) 「重要性 × 新近度」补齐：核心事实与新事件不缺席
        for m in self._store.all_active_memories():
            if len(scored) >= config.MEMORY_TOP_K:
                break
            if m["id"] not in scored:
                scored[m["id"]] = {"memory": m, "score": 0.0}

        ranked = sorted(scored.values(), key=lambda x: x["score"], reverse=True)
        top = ranked[: config.MEMORY_TOP_K]
        active = [item["memory"] for item in top]

        # 3) 历史记忆：语义命中取最相似的几条；降级模式靠历史关键词
        if not self._vs.enabled and _is_history_question(query):
            past = [(1.0, m) for m in self._store.memories_by_status("superseded")]
        past = [m for _, m in sorted(past, key=lambda x: -x[0])]
        past = past[: config.SUPERSEDED_INJECT_MAX]

        # 只让「语义命中」的记忆续命（access_count）：兜底强塞的记忆与
        # 本轮话题无关，计入访问会让它们被虚假回忆、永远不衰减
        for item in top:
            if item["score"] > 0:
                self._store.touch_memory(item["memory"]["id"])
        for m in past:
            self._store.touch_memory(m["id"])
        return active, past
