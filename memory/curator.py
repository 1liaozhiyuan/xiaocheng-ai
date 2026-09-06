"""记忆整理（Curator）：遗忘衰减的后台任务。

认知类比「睡眠整理」：不再被回忆的琐事随时间淡去（有效性跌破阈值 → 归档），
重要的核心事实几乎不衰减（按类别设置半衰期）。归档不删除——用户主动提起
时仍可人工找回，避免「它忘了我告诉过它的事」的伤害。

纯本地计算（无 API 调用），每次抽取任务后顺带运行，毫秒级。
"""
from datetime import datetime

import config


def _parse_dt(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return datetime.now()


class Curator:
    def __init__(self, store) -> None:
        self._store = store

    def run(self) -> int:
        """对全部 active 记忆计算当前有效性，过低的转入归档。返回归档条数。"""
        now = datetime.now()
        archived = 0
        for m in self._store.all_active_memories():
            # 回忆会刷新记忆：以最近一次被想起的时间为衰减起点
            anchor = _parse_dt(m["last_accessed_at"] or m["created_at"])
            days = max(0.0, (now - anchor).total_seconds() / 86400)
            half_life = config.HALF_LIFE_DAYS.get(
                m["category"], config.DEFAULT_HALF_LIFE_DAYS
            )
            retention = 0.5 ** (days / half_life)
            score = (m["importance"] * retention
                     + config.RECALL_BONUS * min(m["access_count"], 10))
            if score < config.ARCHIVE_THRESHOLD:
                self._store.archive_memory(m["id"])
                archived += 1
        return archived

    def score_of(self, m: dict) -> float:
        """暴露单条记忆的当前有效性（调试/展示用），与 run 同一套公式。"""
        anchor = _parse_dt(m["last_accessed_at"] or m["created_at"])
        days = max(0.0, (datetime.now() - anchor).total_seconds() / 86400)
        half_life = config.HALF_LIFE_DAYS.get(
            m["category"], config.DEFAULT_HALF_LIFE_DAYS
        )
        return (m["importance"] * 0.5 ** (days / half_life)
                + config.RECALL_BONUS * min(m["access_count"], 10))
