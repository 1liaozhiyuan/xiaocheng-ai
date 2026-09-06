"""CLI 入口：python main.py 聊天 | python main.py --check 自检"""
import asyncio
import sys
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

BANNER = f"""
{'=' * 52}
  {config.PERSONA_NAME} · 聊天陪伴 Agent (V2)
{'=' * 52}
  /memory  查看长期记忆   /profile  查看用户画像
  /help    帮助           /exit     退出（自动整理记忆）
{'-' * 52}
"""

HELP = """命令：
  /memory   查看当前所有长期记忆
  /profile  查看用户画像
  /forget N 忘记第 N 条记忆（归档）
  /exit     退出
提示：每轮对话后会在后台自动提取值得记住的信息（ 💭 标记）。"""


def build_app():
    """装配依赖：storage → llm → agents。--check 也复用这里的初始化。"""
    store = MemoryStore()
    vector_store = VectorStore(store)
    llm = LLMClient()
    extractor = MemoryExtractor(llm, store, vector_store)
    retriever = Retriever(store, vector_store)
    strategist = StrategistAgent(llm)
    companion = CompanionAgent(llm, store, retriever, strategist)
    memory_manager = MemoryManager(llm, store, vector_store, extractor)
    reviewer = SessionReviewer(llm, store, extractor, vector_store)
    profile_agent = ProfileAgent(llm, store)
    proactive = ProactiveAgent(llm, store)
    return (store, vector_store, companion, memory_manager,
            reviewer, profile_agent, proactive)


async def chat_loop() -> None:
    (store, vector_store, companion, memory_manager,
     reviewer, profile_agent, proactive) = build_app()
    session_id = f"s_{uuid.uuid4().hex[:8]}"
    print(BANNER)

    # 主动开场：小澄先开口（流式打字效果；失败/关闭时回退为等待用户先说）
    if config.PROACTIVE_GREETING:
        print(f"{config.PERSONA_NAME} > ", end="", flush=True)
        parts = []
        try:
            async for delta in proactive.greeting_stream():
                parts.append(delta)
                print(delta, end="", flush=True)
        finally:
            print("\n")
        if not any(p.strip() for p in parts):
            print("（在等你先开口）\n")

    pending_tasks: set = set()

    async def finalize() -> None:
        """退出前「睡前整理」：等待后台任务 → 补漏抽取 + 去重 + 画像巩固。"""
        if pending_tasks:
            # 强退（Ctrl+C）前收尾后台抽取，避免记忆数据丢失
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        try:
            result = await reviewer.review(session_id)
            if result.get("summary"):
                print(f"\n🌙 会话整理完成（补记 {result['saved']} 条，"
                      f"清理重复 {result.get('deduped', 0)} 条）")
                profile = await profile_agent.consolidate()
                if profile:
                    print(f"   画像更新：{profile}")
        except Exception as e:
            print(f"\n（会话整理未完成，记忆数据不受影响：{type(e).__name__}: {e}）")

    while True:
        try:
            user_text = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            await finalize()
            print("\n再见，下次聊 👋")
            return
        if not user_text:
            continue
        if user_text in ("/exit", "/quit"):
            await finalize()
            print("再见，下次聊 👋")
            return
        if user_text == "/help":
            print(HELP)
            continue
        if user_text == "/memory":
            _show_memories(store)
            continue
        if user_text == "/profile":
            _show_profile(store)
            continue
        if user_text.startswith("/forget"):
            _forget(store, vector_store, user_text)
            continue

        store.add_message(session_id, "user", user_text)

        print(f"{config.PERSONA_NAME} > ", end="", flush=True)
        reply_parts = []
        try:
            async for delta in companion.reply(session_id, user_text):
                reply_parts.append(delta)
                print(delta, end="", flush=True)
            print()
        except KeyboardInterrupt:
            print("\n(已打断)")
            continue
        except Exception as e:
            print(f"\n⚠️ 出错了：{e}")
            continue

        reply_text = "".join(reply_parts).strip()
        if reply_text:
            store.add_message(session_id, "assistant", reply_text)
            task = memory_manager.schedule_turn(
                session_id, store.recent_messages(session_id, limit=config.EXTRACT_WINDOW)
            )
            pending_tasks.add(task)
            task.add_done_callback(pending_tasks.discard)


def _show_memories(store: MemoryStore) -> None:
    active = store.all_active_memories()
    past = store.memories_by_status("superseded")
    if not active and not past:
        print("（还没有长期记忆，多聊聊吧）")
        return
    print(f"── 当前记忆（{len(active)} 条）──")
    for i, m in enumerate(active, 1):
        print(f"  [{i}] ({m['category']}, 重要性{m['importance']:.1f}) {m['content']}")
    if past:
        print(f"── 过往经历（{len(past)} 条，已随时间更新）──")
        for m in past:
            print(f"  · ({m['created_at'][:10]}) {m['content']}")


def _show_profile(store: MemoryStore) -> None:
    profile = store.get_profile()
    if not profile:
        print("（画像还是空的）")
        return
    for k, v in profile.items():
        print(f"  {k}: {v}")


def _forget(store: MemoryStore, vector_store, cmd: str) -> None:
    try:
        idx = int(cmd.split()[1]) - 1
    except (IndexError, ValueError):
        print("用法：/forget N （N 为 /memory 列表中的序号）")
        return
    memories = store.all_active_memories()
    if 0 <= idx < len(memories):
        store.archive_memory(memories[idx]["id"])
        vector_store.delete(memories[idx]["id"])  # 同步清理检索索引
        print(f"已忘记：{memories[idx]['content']}")
    else:
        print("序号不存在，先 /memory 查看")


def self_check() -> int:
    print("== 自检 ==")
    ok = True
    try:
        import openai, chromadb, dotenv  # noqa: F401
        print("✓ 依赖已安装 (openai / chromadb / python-dotenv)")
    except ImportError as e:
        print(f"✗ 依赖缺失：{e} → pip install -r requirements.txt")
        return 1

    if not config.API_KEY:
        print("✗ 未配置 API key：复制 .env.example 为 .env，填入 ZHIPU_API_KEY")
        print("== 自检未通过 ❌ ==")
        return 1
    print(f"✓ API key 已配置（模型: {config.CHAT_MODEL} / 轻量: {config.LITE_MODEL}）")

    store = MemoryStore()
    vector_store = VectorStore(store)
    print(f"✓ SQLite 就绪：{config.DB_PATH}")
    print(f"✓ 向量库就绪：{config.CHROMA_PATH}（{len(store.all_active_memories())} 条记忆）")
    store.close()

    async def _ping():
        client = LLMClient()
        return await client.chat(
            [{"role": "user", "content": "回复：ok"}], model=config.LITE_MODEL
        )

    try:
        resp = asyncio.run(_ping())
        print(f"✓ LLM 连通（{config.LITE_MODEL} 返回: {resp[:20]}）")
    except Exception as e:
        print(f"✗ LLM 调用失败：{e}")
        ok = False

    print("== 自检" + ("通过 ✅" if ok else "未通过 ❌") + "==")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(self_check())
    asyncio.run(chat_loop())
