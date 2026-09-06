"""样本标注一致性检查：强模型独立标注 vs 人工标注。

不一致率高 → 样本标注本身有争议（灰区/错误）；
不一致率低但被测系统仍错 → 是被测模型能力问题。
输出不一致清单供人工裁决。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import config  # noqa: E402
from llm.client import LLMClient  # noqa: E402
from persona import prompts  # noqa: E402

EVAL_FILE = Path(__file__).resolve().parent.parent / "evals/dev/S1_crisis.jsonl"
OUT_FILE = Path(__file__).resolve().parent.parent / "evals/annotation-check-S1.jsonl"


async def main() -> None:
    cases = [json.loads(l) for l in EVAL_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    llm = LLMClient()
    agree = disagree = failures = 0
    out_lines = []
    for i, case in enumerate(cases, 1):
        try:
            result = await llm.chat_json(
                [{"role": "system", "content": prompts.STRATEGY_SYSTEM},
                 {"role": "user", "content": (
                     f"最近对话：\n"
                     + "\n".join(f"{'用户' if m['role'] == 'user' else '陪伴者'}：{m['content'][:100]}"
                                 for m in case.get("history") or [])
                     + f"\n\n用户最新消息：{case['message']}")}],
                model="glm-4.5-flash", temperature=0.0)
            model_label = str(result.get("severity", "?")) if isinstance(result, dict) else "parse_fail"
        except Exception as e:
            failures += 1
            print(f"[{i}] 组件异常 {type(e).__name__}")
            continue
        if model_label != case["expected"]:
            disagree += 1
            entry = {"id": case["id"], "human": case["expected"], "model": model_label,
                     "message": case["message"][:60], "category": case["category"]}
            out_lines.append(json.dumps(entry, ensure_ascii=False))
            print(f"⚠️ [{i}] 人工[{case['expected']}] 模型[{model_label}] {case['message'][:36]}")
        else:
            agree += 1
        await asyncio.sleep(0.2)  # 温和限速

    rate = disagree / (agree + disagree) if (agree + disagree) else 0
    print(f"\n── 一致性结论 ──")
    print(f"一致 {agree} / 不一致 {disagree} → 人工-模型分歧率 {rate:.1%}（失败 {failures}）")
    OUT_FILE.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"不一致清单已写入 {OUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
