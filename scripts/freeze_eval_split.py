"""评估集 test 冻结：按类别分层切分 dev/test（8:2），固定种子可复现。

注意（诚实记录）：v1 评估集的迭代（prompt/词表校准）发生在切分之前，
本次 test 基线可能偏乐观；v2 评估集扩充时将严格执行「先切分后迭代」。
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
EVALS = Path(__file__).resolve().parent.parent / "evals"
SPLIT_FILES = {
    "S1_crisis": lambda c: c["expected"],
    "S3a_merge": lambda c: c["expected"],
    "S9_redteam": lambda c: c["attack_type"],
}
TEST_RATIO = 0.2
SEED = 42


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def dump(path: Path, cases: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases),
                    encoding="utf-8")


def main() -> None:
    rng = random.Random(SEED)
    for stem, stratify_key in SPLIT_FILES.items():
        dev_path = EVALS / "dev" / f"{stem}.jsonl"
        cases = load(dev_path)
        groups = defaultdict(list)
        for c in cases:
            groups[stratify_key(c)].append(c)
        test, new_dev = [], []
        for _, group in sorted(groups.items()):
            rng.shuffle(group)
            k = max(1, round(len(group) * TEST_RATIO))
            test.extend(group[:k])
            new_dev.extend(group[k:])
        rng.shuffle(new_dev)
        rng.shuffle(test)
        dump(EVALS / "dev" / f"{stem}.jsonl", new_dev)
        dump(EVALS / "test" / f"{stem}.jsonl", test)
        print(f"{stem}: dev {len(new_dev)} / test {len(test)}（按 {stratify_key.__name__ if callable(stratify_key) else 'label'} 分层）")


if __name__ == "__main__":
    main()
