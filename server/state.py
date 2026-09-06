"""进程级组件装配与会话状态。

CLI 版的 build_app 装配一次跑一个进程循环；Web 版进程常驻，
组件装配为单例，多个 HTTP 会话共享同一份记忆库与 Agent 组件。
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field

import config
from agents.companion import CompanionAgent
from agents.memory_manager import MemoryManager
from agents.profile_agent import ProfileAgent
from agents.proactive import ProactiveAgent
from agents.strategist import StrategistAgent
from llm.client import LLMClient
from memory.extractor import MemoryExtractor
from memory.retriever import Retriever
from memory.reviewer import SessionReviewer
from memory.store import MemoryStore
from memory.vector_store import VectorStore


@dataclass
class SessionState:
    """一个浏览器会话的运行时状态（记忆数据仍在 SQLite，与 CLI 共享）。"""
    id: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    greeted: bool = False      # 主动开场节流：一个会话只开场一次
    reviewed: bool = False     # 闲置复盘标志：有新消息后重置


class AppState:
    def __init__(self) -> None:
        config.ensure_dirs()
        self.store = MemoryStore()
        self.vector_store = VectorStore(self.store)
        self.llm = LLMClient(store=self.store)
        self.extractor = MemoryExtractor(self.llm, self.store, self.vector_store)
        self.retriever = Retriever(self.store, self.vector_store)
        self.strategist = StrategistAgent(self.llm)
        self.companion = CompanionAgent(self.llm, self.store, self.retriever,
                                        self.strategist)
        self.memory_manager = MemoryManager(self.llm, self.store, self.vector_store,
                                            self.extractor)
        self.reviewer = SessionReviewer(self.llm, self.store, self.extractor,
                                        self.vector_store)
        self.profile_agent = ProfileAgent(self.llm, self.store)
        self.proactive = ProactiveAgent(self.llm, self.store)
        self.sessions: dict[str, SessionState] = {}
        self.background_tasks: set = set()

    def new_session(self) -> SessionState:
        s = SessionState(id=f"s_{uuid.uuid4().hex[:8]}")
        self.sessions[s.id] = s
        return s

    def get_session(self, session_id: str) -> SessionState | None:
        return self.sessions.get(session_id)

    def revive_session(self, session_id: str) -> SessionState:
        """取回会话；服务重启后内存态丢失时，按原 id 重建运行时状态。

        聊天记录/记忆都在 SQLite（按 session_id 持久），重建内存态后
        浏览器里存着的旧 session_id 可以无缝续聊。
        """
        s = self.sessions.get(session_id)
        if s is None:
            s = SessionState(id=session_id)
            self.sessions[session_id] = s
        return s

    def touch(self, session: SessionState) -> None:
        session.last_active = time.time()
        session.reviewed = False

    def schedule(self, coro_or_task) -> None:
        """登记后台任务（接受协程或已创建的 Task），进程常驻随服务存活。"""
        if isinstance(coro_or_task, asyncio.Task):
            task = coro_or_task
        else:
            task = asyncio.get_running_loop().create_task(coro_or_task)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
