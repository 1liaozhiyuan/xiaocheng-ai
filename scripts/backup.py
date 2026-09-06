"""数据备份：打包 data/（chat.db + chroma）到 backups/，保留最近 5 份。

用法：python scripts/backup.py
"""
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
BACKUP_DIR = ROOT / "backups"
KEEP = 5


def main() -> None:
    if not DATA_DIR.exists():
        print("data/ 不存在，无需备份")
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"backup-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DATA_DIR / "chat.db", dest / "chat.db")
    if (DATA_DIR / "chroma").exists():
        shutil.copytree(DATA_DIR / "chroma", dest / "chroma", dirs_exist_ok=True)
    # 清理只保留最近 KEEP 份
    backups = sorted(BACKUP_DIR.glob("backup-*"))
    for old in backups[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    print(f"✅ 备份完成：{dest}")
    print(f"当前保留 {min(len(backups), KEEP)} 份备份于 {BACKUP_DIR}")


if __name__ == "__main__":
    main()
