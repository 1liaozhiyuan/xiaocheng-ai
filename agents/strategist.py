"""对话策略 Agent：每轮主对话前的快速「定调」，含危机分级守护（三级级联判定）。

职责：
1. 判断用户情绪/意图，产出一句回复基调指令（注入主对话 system prompt）
2. 危机分级识别采用级联漏斗（对照 LongMemEval/AICompanionBench 的校准结论：
   flash 级模型对隐晦意念召回不足，纯 prompt 无法解决）：
   L1 快模型初判（亚秒级）→ L2 语义门控（embedding 与意念锚点相似度）→
   L3 重模型复核（glm-4.5-flash，仅疑似危机触发，慢但只落在疑似危机轮）
3. heavy 路由：情绪浓度高（倾诉/低落/危机）时主对话切重模型

设计约束：任何失败（超时/解析失败/网络）回退为低级结果，绝不阻塞或搞砸聊天。
"""
import asyncio
from dataclasses import dataclass

import config
from llm.client import LLMClient
from persona import prompts


@dataclass
class TurnStrategy:
    emotion: str = "neutral"
    intent: str = "casual"
    crisis: bool = False
    severity: str = "none"      # none / low（无望倾向）/ high（明确自伤意念或计划）
    instruction: str = ""       # 注入主对话的一句话基调指令（可为空）


_CRISIS_HIGH_FALLBACK = (
    "crisis(high)：直接、明确地表达关心与重视，建议立即联系信任的人或"
    "全国心理援助热线 12356，如有紧急危险提示拨打 110/120；"
    "语气坚定温柔，不慌张不说教，全程不玩笑"
)
_CRISIS_LOW_FALLBACK = (
    "crisis(low)：放下一切玩笑和话题引导，认真温柔地回应，承认 ta 的痛苦，"
    "建议联系信任的人或专业支持（可提及全国心理援助热线 12356），不慌张不说教"
)

# high 级证据词：双向规则（high 无证据降级 / low 有证据升级）——属于被测系统组件
HIGH_EVIDENCE_KEYWORDS = ("不想活", "想死", "想消失", "消失掉", "活不下去",
                          "结束生命", "结束这一切", "自杀", "自残", "轻生", "了断",
                          "了结", "解脱", "跳下去", "跳楼", "安眠药", "手腕",
                          "遗书", "告别信", "长眠", "离开这个世界", "不想醒")

# L2 语义门控的意念锚点（embedding 相似度触发 L3 复核；误触发无害——复核纠正）
CRISIS_ANCHORS = ["我想结束自己的生命", "我不想活了想死",
                  "我想消失告别这个世界", "我已经准备好离开人世"]
SEMANTIC_GATE_THRESHOLD = 0.52   # 0.45 会误触发 low 类消息（实测校准）

# L3 复核：专用二分类 prompt（"是否有自伤意念"比三级分类简单得多，
# flash 系模型对二分类的召回显著更好——见 evals/reports 的 S1 迭代记录）
BINARY_IDEATION_PROMPT = """判断这条消息是否表达了**说话者本人**的自伤/自杀意念——包括直接表达和隐晦表达
（想去死、想消失、想告别这个世界、准备离开、羡慕已逝者、交代后事等）。

不算意念的情况：转述别人（朋友/家人说想不开）、讨论新闻影视游戏、中文习语（累得要死/笑死）、一般沮丧压力。

只输出 JSON：{"ideation": true, "confidence": "high|medium|low"}"""


class StrategistAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._embed_client = None
        self._anchor_vectors = None

    # ── L2 语义门控 ──
    def _embed(self, texts):
        if self._embed_client is None:
            from openai import OpenAI
            self._embed_client = OpenAI(api_key=config.EMBED_API_KEY,
                                        base_url=config.EMBED_BASE_URL)
        resp = self._embed_client.embeddings.create(
            model=config.EMBED_MODEL, input=texts)
        return [d.embedding for d in resp.data]

    def _semantic_gate(self, message: str) -> float:
        """消息与危机意念锚点的最大相似度。失败返回 0（跳过复核，增强件语义）。"""
        try:
            if self._anchor_vectors is None:
                self._anchor_vectors = self._embed(CRISIS_ANCHORS)
            q = self._embed([message])[0]
            import math
            scores = []
            for a in self._anchor_vectors:
                dot = sum(x * y for x, y in zip(q, a))
                na = math.sqrt(sum(x * x for x in q))
                nb = math.sqrt(sum(x * x for x in a))
                scores.append(dot / (na * nb))
            return max(scores)
        except Exception:
            return 0.0

    # ── L1 / L3 共用的调用构造 ──
    def _decide_messages(self, user_message: str, history: list[dict]) -> list[dict]:
        dialogue = "最近对话：\n"
        dialogue += "\n".join(
            f"{'用户' if m['role'] == 'user' else '陪伴者'}：{m['content'][:100]}"
            for m in history)
        dialogue += f"\n\n用户最新消息：{user_message}"
        return [
            {"role": "system", "content": prompts.STRATEGY_SYSTEM},
            {"role": "user", "content": dialogue},
        ]

    async def _decide_with(self, model: str, user_message: str,
                           history: list[dict], timeout: float):
        return await asyncio.wait_for(
            self._llm.chat_json(self._decide_messages(user_message, history),
                                model=model, temperature=0.0),
            timeout=timeout)

    # ── 结果归一化 + 双向词表规则 ──
    def _normalize(self, result, user_message: str) -> TurnStrategy:
        if not isinstance(result, dict):
            return TurnStrategy()
        severity = str(result.get("severity", "none")).lower()
        if severity not in ("none", "low", "high"):
            severity = "none"
        crisis = bool(result.get("crisis")) or severity in ("low", "high")
        if severity == "none" and crisis:
            severity = "low"  # 模型给了 crisis 但没给级别时按低级处理
        has_evidence = any(kw in user_message for kw in HIGH_EVIDENCE_KEYWORDS)
        # 转述/外部事件标记：消息讨论的是别人或作品，意念词不构成用户本人证据
        reported = any(t in user_message for t in
                       ("朋友", "同事", "家人", "亲戚", "他说", "她说", "新闻",
                        "电影", "游戏", "小说", "剧", "明星", "纪录片", "播客"))
        if reported:
            has_evidence = False
        if severity == "high" and not has_evidence:
            severity = "low"  # 无词汇证据的 high 视为模型过度升级
        if severity == "none" and has_evidence:
            severity = "low"  # none + 意念词汇：模型漏判，词表是强证据，至少按 low 守护
        if severity == "low" and has_evidence:
            severity = "high"  # low + 意念词汇：升为 high（链式规则的两级跳）
        emotion = str(result.get("emotion", "neutral"))
        intent = str(result.get("intent", "casual"))
        instruction = str(result.get("instruction", "")).strip()
        if crisis and "12356" not in instruction:
            instruction = (_CRISIS_HIGH_FALLBACK if severity == "high"
                           else _CRISIS_LOW_FALLBACK)
        return TurnStrategy(emotion=emotion, intent=intent,
                            crisis=crisis, severity=severity, instruction=instruction)

    async def decide(self, user_message: str,
                     recent_history: list[dict] | None = None) -> TurnStrategy:
        history = (recent_history or [])[-4:]
        try:
            # L1 快模型初判（亚秒级，与记忆检索并行不拖首字）
            initial = await asyncio.wait_for(
                self._decide_with(config.STRATEGY_MODEL, user_message, history, 6.0),
                timeout=6.0)
            strategy = self._normalize(initial, user_message)
        except (asyncio.TimeoutError, Exception):
            return TurnStrategy()  # 策略是增强件：失败回退默认，绝不阻塞聊天

        # L2/L3 语义门控 → 重模型复核：初判非 high 但语义接近意念锚点时，
        # 用更强的模型复核（误触发无害——复核会纠正；漏触发是残余漏报风险）
        if strategy.severity != "high":
            gate = await asyncio.to_thread(self._semantic_gate, user_message)
            if gate >= SEMANTIC_GATE_THRESHOLD:
                try:
                    verdict = await asyncio.wait_for(
                        self._llm.chat_json(
                            [{"role": "system", "content": BINARY_IDEATION_PROMPT},
                             {"role": "user", "content": user_message}],
                            model=config.CHAT_MODEL_HEAVY, temperature=0.0),
                        timeout=15.0)
                    if (isinstance(verdict, dict) and verdict.get("ideation")
                            and verdict.get("confidence") in ("high", "medium")):
                        strategy.severity = "high"
                        strategy.crisis = True
                        strategy.emotion = "crisis"
                        strategy.instruction = _CRISIS_HIGH_FALLBACK
                except (asyncio.TimeoutError, Exception):
                    pass  # 复核失败：保留 L1 结果
        return strategy
