"""SQLite 存储层：消息历史、长期记忆、用户画像。

以这里为 source of truth，向量库只是检索索引（删改以 SQLite 为准）。
"""
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,          -- user / assistant
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    content          TEXT    NOT NULL,      -- 第三人称陈述句，如「用户养了只猫叫年糕」
    category         TEXT    NOT NULL DEFAULT 'fact',
                                            -- fact / preference / emotion / event / relationship
    importance       REAL    NOT NULL DEFAULT 0.5,
    source_session   TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_accessed_at TEXT,
    access_count     INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL DEFAULT 'active'
                                            -- active / superseded / archived
                                            -- superseded：被新记忆取代的历史事实（内容永远为真）
    ,superseded_by   INTEGER                -- 指向取代本记忆的新记忆 id
);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);

CREATE TABLE IF NOT EXISTS user_profile (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    summary       TEXT,                -- 会话复盘摘要（跨会话工作记忆）
    message_count INTEGER NOT NULL DEFAULT 0,
    started_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    reviewed_at   TEXT
);

CREATE TABLE IF NOT EXISTS emotion_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    date        TEXT NOT NULL,         -- YYYY-MM-DD
    score       REAL NOT NULL,         -- 1~10，越高越好
    label       TEXT,                  -- 开心 / 平静 / 低落 / 焦虑…
    note        TEXT,                  -- 一句话备注
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_emotion_date ON emotion_snapshots(date);

CREATE TABLE IF NOT EXISTS diary (
    date        TEXT PRIMARY KEY,      -- 一天一篇（复盘时覆盖更新）
    content     TEXT NOT NULL,         -- 小澄第一人称日记
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS usage_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    purpose           TEXT NOT NULL,   -- chat/strategy/extract/summary/diary/emotion/merge/greeting/profile/review
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts);
"""


class MemoryStore:
    """线程安全的 SQLite 封装（异步任务和主循环会并发访问）。"""

    def __init__(self, db_path=None) -> None:
        config.ensure_dirs()
        self._conn = sqlite3.connect(str(db_path or config.DB_PATH), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """对已存在的旧库补列（CREATE TABLE IF NOT EXISTS 不会更新已有表）。"""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(memories)")}
        if "superseded_by" not in cols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN superseded_by INTEGER")

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── 消息 ──
    def add_message(self, session_id: str, role: str, content: str) -> int:
        cur = self._exec(
            "INSERT INTO messages(session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        return cur.lastrowid

    def recent_messages(self, session_id: str, limit: int) -> list[dict]:
        rows = self._exec(
            "SELECT role, content, created_at FROM messages "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ── 长期记忆 ──
    def add_memory(self, content: str, category: str, importance: float,
                   source_session: str) -> int:
        cur = self._exec(
            "INSERT INTO memories(content, category, importance, source_session) "
            "VALUES (?, ?, ?, ?)",
            (content, category, importance, source_session),
        )
        return cur.lastrowid

    def memory_exists(self, content: str) -> bool:
        """粗粒度去重：完全同文本的 active 记忆视为重复。"""
        row = self._exec(
            "SELECT 1 FROM memories WHERE content = ? AND status = 'active' LIMIT 1",
            (content,),
        ).fetchone()
        return row is not None

    def get_memory(self, memory_id: int) -> Optional[dict]:
        row = self._exec("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return dict(row) if row else None

    def supersede(self, old_id: int, new_id: int) -> None:
        """旧记忆转为历史（内容不改、永远可查），并链接到取代它的新记忆。"""
        self._exec(
            "UPDATE memories SET status = 'superseded', superseded_by = ? WHERE id = ?",
            (new_id, old_id),
        )

    def memories_by_status(self, status: str) -> list[dict]:
        rows = self._exec(
            "SELECT * FROM memories WHERE status = ? "
            "ORDER BY importance DESC, id DESC",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_active_memories(self) -> list[dict]:
        rows = self._exec(
            "SELECT * FROM memories WHERE status = 'active' "
            "ORDER BY importance DESC, id DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def touch_memory(self, memory_id: int) -> None:
        self._exec(
            "UPDATE memories SET access_count = access_count + 1, "
            "last_accessed_at = datetime('now', 'localtime') WHERE id = ?",
            (memory_id,),
        )

    def update_memory(self, memory_id: int, content: str, importance: float) -> None:
        self._exec(
            "UPDATE memories SET content = ?, importance = ? WHERE id = ?",
            (content, importance, memory_id),
        )

    def archive_memory(self, memory_id: int) -> None:
        self._exec("UPDATE memories SET status = 'archived' WHERE id = ?", (memory_id,))

    # ── 会话 ──
    def session_messages(self, session_id: str) -> list[dict]:
        rows = self._exec(
            "SELECT role, content, created_at FROM messages "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_messages(self, session_id: str) -> int:
        row = self._exec(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row["n"] if row else 0

    def set_session_summary(self, session_id: str, summary: str) -> None:
        self._exec(
            "INSERT INTO sessions(session_id, summary, message_count, reviewed_at) "
            "VALUES (?, ?, ?, datetime('now', 'localtime')) "
            "ON CONFLICT(session_id) DO UPDATE SET summary = excluded.summary, "
            "message_count = excluded.message_count, "
            "reviewed_at = excluded.reviewed_at",
            (session_id, summary, self.count_messages(session_id)),
        )

    def recent_summaries(self, limit: int = 2, exclude_session: str = "") -> list[dict]:
        rows = self._exec(
            "SELECT session_id, summary, reviewed_at FROM sessions "
            "WHERE summary IS NOT NULL AND session_id != ? "
            "ORDER BY reviewed_at DESC, rowid DESC LIMIT ?",
            (exclude_session, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 情绪快照（长期趋势追踪）──
    def add_emotion_snapshot(self, session_id: str, score: float,
                             label: str, note: str) -> int:
        cur = self._exec(
            "INSERT INTO emotion_snapshots(session_id, date, score, label, note) "
            "VALUES (?, date('now','localtime'), ?, ?, ?)",
            (session_id, score, label, note),
        )
        return cur.lastrowid

    def recent_emotion_snapshots(self, limit: int = 10) -> list[dict]:
        rows = self._exec(
            "SELECT date, score, label, note FROM emotion_snapshots "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]  # 时间正序，便于画趋势

    def emotion_trend(self, window: int = 5) -> float | None:
        """最近 window 个快照的平均分；无数据返回 None。"""
        rows = self._exec(
            "SELECT score FROM emotion_snapshots ORDER BY id DESC LIMIT ?",
            (window,),
        ).fetchall()
        if not rows:
            return None
        return sum(r["score"] for r in rows) / len(rows)

    # ── 小澄的日记 ──
    def set_diary(self, date: str, content: str) -> None:
        self._exec(
            "INSERT INTO diary(date, content) VALUES (?, ?) "
            "ON CONFLICT(date) DO UPDATE SET content = excluded.content, "
            "created_at = datetime('now', 'localtime')",
            (date, content),
        )

    def recent_diary(self, limit: int = 30) -> list[dict]:
        rows = self._exec(
            "SELECT date, content FROM diary ORDER BY date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 关系里程碑 ──
    def mark_first_met(self) -> str:
        """记录初次见面日期（首次会话时调用，幂等）。"""
        p = self.get_profile()
        if not p.get("初次见面"):
            self.set_profile("初次见面", datetime.now().strftime("%Y-%m-%d"))
        return self.get_profile()["初次见面"]

    # ── Token 用量 ──
    def add_usage(self, purpose: str, model: str, prompt_tokens: int,
                  completion_tokens: int, latency_ms: int = 0) -> None:
        self._exec(
            "INSERT INTO usage_log(purpose, model, prompt_tokens, completion_tokens, latency_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (purpose, model, prompt_tokens, completion_tokens, latency_ms),
        )

    def usage_summary(self, days: int = 30) -> list[dict]:
        rows = self._exec(
            "SELECT purpose, model, COUNT(*) AS calls, "
            "SUM(prompt_tokens) AS prompt_tokens, "
            "SUM(completion_tokens) AS completion_tokens, "
            "SUM(prompt_tokens + completion_tokens) AS total_tokens, "
            "AVG(latency_ms) AS avg_latency_ms "
            "FROM usage_log WHERE ts >= datetime('now', ?) "
            "GROUP BY purpose, model ORDER BY total_tokens DESC",
            (f'-{days} days',),
        ).fetchall()
        return [dict(r) for r in rows]

    def usage_daily(self, days: int = 14) -> list[dict]:
        rows = self._exec(
            "SELECT date(ts) AS date, SUM(prompt_tokens + completion_tokens) AS total_tokens "
            "FROM usage_log WHERE ts >= datetime('now', ?) "
            "GROUP BY date(ts) ORDER BY date",
            (f'-{days} days',),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 用户画像 ──
    def set_profile(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO user_profile(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = datetime('now', 'localtime')",
            (key, value),
        )

    def get_profile(self) -> dict[str, str]:
        rows = self._exec("SELECT key, value FROM user_profile").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def delete_profile(self, key: str) -> None:
        self._exec("DELETE FROM user_profile WHERE key = ?", (key,))

    def set_profile_bulk(self, kv: dict[str, Any]) -> None:
        for k, v in kv.items():
            self.set_profile(str(k), str(v))
