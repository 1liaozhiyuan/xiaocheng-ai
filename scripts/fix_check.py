"""存量问题修复的真实验证：危机跟进跨会话 + 复盘去重。

用法：python scripts/fix_check.py
"""
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.profile_agent import ProfileAgent  # noqa: E402
from memory.reviewer import SessionReviewer  # noqa: E402
from scripts.system_test import Harness  # noqa: E402


async def main() -> bool:
    h = Harness(label="FIX")
    try:
        print("\n── 阶段 1：制造同义重复记忆（措辞不同的两次陈述）──")
        s1 = "fix_s1"
        await h.turn(s1, "跟你说，我最近搬到上海了，住在浦东")
        await h.turn(s1, "对了对了忘了说，我现在人在上海浦东这边，刚搬来没几天")
        # 逐轮抽取的合并决策通常会拦住重复，这里再手工补一条重复，
        # 确保复盘去重逻辑在真实 LLM 上得到检验
        h.store.add_memory("用户刚搬到上海浦东住", "fact", 0.75, s1)
        h.dump_state("去重前（手工重复 + 可能的天然重复）")
        before = len(h.store.all_active_memories())

        print("\n── 阶段 2：重度危机信号 + 退出复盘 ──")
        await h.turn(s1, "说实话我真的不想活了，觉得自己是个累赘")
        reviewer = SessionReviewer(h.llm, h.store, h.manager._extractor, h.vs)
        result = await reviewer.review(s1)
        print(f"复盘：补记 {result['saved']} 条，去重清理 {result['deduped']} 条")
        h.dump_state("复盘后")
        dedup_ok = len(h.store.all_active_memories()) < before

        profile = await ProfileAgent(h.llm, h.store).consolidate()
        flag = h.store.get_profile().get("关注标记")
        print(f"画像: {profile}")
        print(f"危机跟进标记: {flag or '（无）'}")
        flag_ok = bool(flag)

        print("\n── 阶段 3：下次会话开场（应主动关心）──")
        s2 = "fix_s2"
        await h.turn(s2, "嗨，我又上线啦")

        print("\n── 验证结论 ──")
        print(f"{'✅' if result['deduped'] > 0 else '⚠️'} 同义去重: "
              f"{'清理 ' + str(result['deduped']) + ' 条' if result['deduped'] > 0 else '本次未触发（无重复）'}")
        print(f"{'✅' if flag_ok else '❌'} 危机标记持久化: {'已写入' if flag_ok else '未写入'}")
        return result['deduped'] > 0 and flag_ok
    finally:
        h.close()


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
