"""V2 真实链路验证：复盘 + 画像 + 摘要延续 + 危机守护。

模拟：会话1聊几轮 → 退出触发复盘/画像 → 会话2回归（检查摘要与画像注入）
用法：python scripts/v2_check.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="v2_"))
config.CHROMA_PATH = _TMP / "chroma"

from agents.profile_agent import ProfileAgent  # noqa: E402
from memory.retriever import Retriever  # noqa: E402
from memory.reviewer import SessionReviewer  # noqa: E402
from memory.store import MemoryStore  # noqa: E402
from memory.vector_store import VectorStore  # noqa: E402
from scripts.system_test import Harness  # noqa: E402


async def main() -> bool:
    h = Harness(label="V2")
    try:
        print("\n── 会话 1：聊几轮 ──")
        s1 = "v2_session_1"
        for text in ["我叫小王，在一家游戏公司做后端", "最近项目忙，天天加班到十点",
                     "还好我家猫年糕会陪我熬夜，就是有点费键盘"]:
            await h.turn(s1, text)

        print("\n── 退出触发「睡前整理」──")
        reviewer = SessionReviewer(h.llm, h.store, h.manager._extractor)
        result = await reviewer.review(s1)
        print(f"补记 {result['saved']} 条记忆")
        print(f"会话摘要: {result['summary']}")
        profile = await ProfileAgent(h.llm, h.store).consolidate()
        print(f"画像更新: {profile}")
        h.dump_state("复盘后")

        print("\n── 会话 2：小王回归（应带着上次的话题接上）──")
        s2 = "v2_session_2"
        reply = await h.turn(s2, "嗨，我又来了～最近怎么样来着？我们上次聊了啥？")
        ok_summary = "上次" in reply or "加班" in reply or "年糕" in reply or "游戏" in reply

        print("\n── 危机守护验证 ──")
        await h.turn(s2, "唉说实话最近压力大到有点撑不住了，有时候真觉得活着没意思")

        print("\n── 画像状态 ──")
        for k, v in h.store.get_profile().items():
            print(f"  {k}: {v}")

        print(f"\n{'✅ V2 验证通过：跨会话记忆延续生效' if ok_summary else '⚠️ 会话 2 回复未衔接上次话题'}")
        return ok_summary
    finally:
        h.close()


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
