"""端到端真实链路验证（会用真实 API，数据写入临时目录，不污染 data/）。

用法：python scripts/e2e_check.py
验证：真实对话 → 真实记忆抽取入库 → 真实向量检索注入 → 个性化回应
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

_TMP = Path(tempfile.mkdtemp(prefix="e2e_"))
config.CHROMA_PATH = _TMP / "chroma"

from agents.companion import CompanionAgent      # noqa: E402
from agents.memory_manager import MemoryManager  # noqa: E402
from llm.client import LLMClient                 # noqa: E402
from memory.retriever import Retriever           # noqa: E402
from memory.store import MemoryStore             # noqa: E402
from memory.vector_store import VectorStore      # noqa: E402


async def run() -> bool:
    store = MemoryStore(_TMP / "e2e.db")
    vs = VectorStore(store)
    llm = LLMClient()
    companion = CompanionAgent(llm, store, Retriever(store, vs))
    manager = MemoryManager(llm, store, vs)

    print("── 轮 1：自我介绍（应触发记忆抽取）──")
    msg1 = "我叫小王，养了只猫叫年糕，今天她把我键盘踩坏了，我又气又好笑"
    store.add_message("e2e", "user", msg1)
    r1 = "".join([d async for d in companion.reply("e2e", msg1)])
    store.add_message("e2e", "assistant", r1)
    print(f"{config.PERSONA_NAME}: {r1}\n")

    await manager.schedule_turn("e2e", store.recent_messages("e2e", limit=4))

    memories = store.all_active_memories()
    print(f"── 抽取结果：{len(memories)} 条记忆 ──")
    for m in memories:
        print(f"  💭 ({m['category']}, {m['importance']:.1f}) {m['content']}")
    print(f"向量库条数: {vs.count()}\n")
    if not memories:
        print("❌ 未抽取到任何记忆，检查 LITE_MODEL 输出")
        return False

    print("── 轮 2：无关话题（看是否记得小王和年糕）──")
    msg2 = "晚上吃点什么好呢"
    store.add_message("e2e", "user", msg2)
    system_prompt = await asyncio.to_thread(companion.build_system_prompt, msg2)
    injected = [ln for ln in system_prompt.splitlines()
                if ln.startswith("- ") and ("年糕" in ln or "小王" in ln)]
    print(f"注入的记忆行: {injected or '（无）'}")
    r2 = "".join([d async for d in companion.reply("e2e", msg2)])
    print(f"{config.PERSONA_NAME}: {r2}\n")

    hit = ("年糕" in r2 or "小王" in r2) and bool(injected)
    print("✅ 记忆闭环通过：记忆被检索并主动使用" if hit
          else "⚠️ 链路跑通但回复未提及记忆（可能是模型风格，多试几轮）")

    print("\n── 轮 3：告诉现状（应入库「住北京」）──")
    msg3 = "对了跟你说，我这些年一直住在北京"
    store.add_message("e2e", "user", msg3)
    r3 = "".join([d async for d in companion.reply("e2e", msg3)])
    store.add_message("e2e", "assistant", r3)
    print(f"{config.PERSONA_NAME}: {r3}\n")
    await manager.schedule_turn("e2e", store.recent_messages("e2e", limit=8))

    print("── 轮 4：制造冲突（应触发 SUPERSEDE 决策）──")
    msg4 = "跟你说个大事！我下周就搬去上海了，以后就不住北京啦"
    store.add_message("e2e", "user", msg4)
    r4 = "".join([d async for d in companion.reply("e2e", msg4)])
    store.add_message("e2e", "assistant", r4)
    print(f"{config.PERSONA_NAME}: {r4}\n")
    await manager.schedule_turn("e2e", store.recent_messages("e2e", limit=8))

    active_after = store.all_active_memories()
    past_after = store.memories_by_status("superseded")
    print(f"── 冲突处理结果 ──")
    print(f"当前记忆: {[m['content'] for m in active_after]}")
    print(f"历史记忆: {[m['content'] for m in past_after]}")

    print("\n── 轮 5：问历史（应答出北京和上海）──")
    msg5 = "你还记得我都住过哪些城市吗？"
    store.add_message("e2e", "user", msg5)
    system_prompt = await asyncio.to_thread(companion.build_system_prompt, msg5)
    print("注入的过往区:", [ln for ln in system_prompt.splitlines() if "【过往】" in ln] or "（无）")
    r5 = "".join([d async for d in companion.reply("e2e", msg5)])
    print(f"{config.PERSONA_NAME}: {r5}\n")

    history_ok = "北京" in r5 and "上海" in r5
    supersede_ok = bool(past_after)
    if history_ok:
        print("✅ V1 取代链通过：历史问题正确答出过去与现在"
              + ("（旧记忆已转历史）" if supersede_ok else "（注意：旧记忆未被取代，两记忆并存）"))
    else:
        print(f"⚠️ 历史问答未达标（supersede 触发: {supersede_ok}）")
    store.close()
    return hit and history_ok


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
