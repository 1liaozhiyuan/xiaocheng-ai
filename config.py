"""全局配置：从 .env 读取密钥与模型设置，其余为代码内默认值。"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ── LLM（默认智谱 GLM，OpenAI 兼容协议；换 OpenAI/DeepSeek 只需改 .env 三个值）──
API_KEY = os.getenv("ZHIPU_API_KEY", "")
BASE_URL = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
# 对话主模型：glm-4-flash-250414 首 token 实测 ~0.3s（glm-4.5-flash 服务端
# 首 token 实测 10s+，严重影响聊天手感）；更看重指令遵循可换回 glm-4.5-flash
CHAT_MODEL = os.getenv("CHAT_MODEL", "glm-4-flash-250414")
# 情绪浓度高的对话（倾诉/低落/危机）自动切换的重模型：共情与语气更好，但首字慢 ~10s
CHAT_MODEL_HEAVY = os.getenv("CHAT_MODEL_HEAVY", "glm-4.5-flash")
# 记忆抽取/画像更新用的轻量模型：便宜、快
LITE_MODEL = os.getenv("LITE_MODEL", "glm-4-flash")
# 策略判定（危机分级）专用模型：要求指令遵循强，与检索并行不拖首字
STRATEGY_MODEL = os.getenv("STRATEGY_MODEL", "glm-4-flash-250414")
# 关闭 GLM-4.5 系列的思考模式（陪伴对话要快）；换其他厂商时设为 false
DISABLE_THINKING = os.getenv("DISABLE_THINKING", "true").lower() == "true"
# 检索用向量化模型（留空则禁用语义检索，记忆仍按重要性注入）
EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen3.7-text-embedding-flash")
# 向量化可独立于对话模型使用其他厂商：EMBED_API_KEY → QWEN_API_KEY → 对话 key 依次回落
EMBED_API_KEY = (os.getenv("EMBED_API_KEY", "") or os.getenv("QWEN_API_KEY", "") or API_KEY)
# 向量服务端点：未显式指定时，若回落到了独立的千问 key 则用 DashScope 兼容端点
EMBED_BASE_URL = os.getenv("EMBED_BASE_URL", "") or (
    "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if EMBED_API_KEY != API_KEY else BASE_URL
)

# ── 存储 ──
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "chat.db"
CHROMA_PATH = DATA_DIR / "chroma"

# ── 对话与记忆参数 ──
HISTORY_WINDOW = 20          # 工作记忆：注入 prompt 的最近消息条数
MEMORY_TOP_K = 6             # 每轮注入的相关记忆条数
EXTRACT_EVERY_N_TURNS = 1    # 每多少轮触发一次异步记忆抽取
EXTRACT_WINDOW = 8           # 单次抽取回看的对话消息条数
IMPORTANCE_THRESHOLD = 0.55  # 抽取出的记忆入库的最低重要性分

# ── 记忆合并（V1 取代链）──
SIMILARITY_MERGE_THRESHOLD = 0.72  # 语义相似度超过此值才进入 LLM 合并决策
SCREEN_POOL_SIZE = 40              # LLM 联合粗筛的记忆池大小（更大时先向量预筛）
SUPERSEDED_INJECT_SIM = 0.75       # 历史记忆注入所需的最低相似度（语义模式）
SUPERSEDED_INJECT_MAX = 2          # 每轮最多注入的历史记忆条数
HISTORY_KEYWORDS = ("以前", "之前", "曾经", "当初",
                    "去过", "住过", "待过", "换过")
# 降级模式（无向量）：消息含上述词时额外召回历史记忆。
# 「过去」「原来」已移除——「过去两天」「原来如此」这类日常用法误触发太多。

# ── 遗忘衰减（V1 整理任务）：score = importance × 0.5^(天数/半衰期) + 回忆加成 ──
HALF_LIFE_DAYS = {
    "fact": 365,          # 事实几乎不遗忘
    "preference": 180,
    "relationship": 90,
    "event": 21,
    "emotion": 10,        # 情绪来得快去得也快
}
DEFAULT_HALF_LIFE_DAYS = 30
RECALL_BONUS = 0.04         # 每被回忆一次的加成（封顶 10 次）
ARCHIVE_THRESHOLD = 0.15    # 有效性低于此值转入归档（不物理删除）

# ── 人设（可在此调整性格基调）──
PERSONA_NAME = os.getenv("PERSONA_NAME", "小澄")
PERSONA_BRIEF = os.getenv(
    "PERSONA_BRIEF",
    "一个温暖、真诚、有分寸感的长期陪伴者，幽默但不油腻，关心人但不说教",
)

# ── 主动性（V3）──
PROACTIVE_GREETING = os.getenv("PROACTIVE_GREETING", "true").lower() == "true"
# Web 服务端口（.env 里 SERVER_PORT=8001 可改）
SERVER_PORT = int(os.getenv("SERVER_PORT", "8001"))
# 危机跟进标记的画像键名（strategist 写入 / proactive 开场消费 / companion 兜底注入）
FOLLOWUP_KEY = "关注标记"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
