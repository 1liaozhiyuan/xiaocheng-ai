"""记忆抽取管线：对话 → 候选记忆 → 查重/合并决策 → 入库（SQLite + 向量库）。

V1 核心变化：入库前先做语义查重，与相似旧记忆交给轻量 LLM 决策
ADD / SUPERSEDE / MERGE / SKIP。被取代的旧记忆不删除，转为历史链
（见 store.supersede），使 Agent 既知道「现状」也记得「曾经」。

由 MemoryManager 在每轮对话后异步调用，不阻塞聊天主链路。
"""
import asyncio
from typing import Optional

import config
from llm.client import LLMClient
from memory.store import MemoryStore
from memory.vector_store import VectorStore
from persona import prompts


class MemoryExtractor:
    def __init__(self, llm: LLMClient, store: MemoryStore, vector_store: VectorStore) -> None:
        self._llm = llm
        self._store = store
        self._vs = vector_store

    async def extract_and_store(self, session_id: str,
                                dialog: list[dict]) -> list[dict]:
        """dialog: [{"role", "content"}, ...] 最近若干轮。返回本轮落库的记忆及取代关系。"""
        profile = self._store.get_profile()
        existing = [m["content"] for m in self._store.all_active_memories()[:30]]

        result = await self._llm.chat_json(
            [
                {"role": "system", "content": prompts.MEMORY_EXTRACT_SYSTEM},
                {"role": "user", "content": prompts.MEMORY_EXTRACT_USER.format(
                    profile=_fmt_profile(profile),
                    memories="\n".join(f"- {c}" for c in existing) or "（暂无）",
                    dialog=_fmt_dialog(dialog),
                )},
            ],
            temperature=0.0, purpose="extract",
        )
        if not isinstance(result, list):
            return []

        # 过滤出合格候选
        candidates = []
        for item in result:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            importance = _to_float(item.get("importance"), 0.5)
            category = str(item.get("category", "fact"))
            if (content and importance >= config.IMPORTANCE_THRESHOLD
                    and not self._store.memory_exists(content)):
                candidates.append({"content": content, "category": category,
                                   "importance": importance})

        # 为每条候选找「谈论同一主题」的旧记忆：向量快路径 + LLM 联合粗筛兜底
        related = await self._match_related(candidates)

        saved = []
        for i, cand in enumerate(candidates):
            outcome = await self._save_candidate(session_id, cand, related.get(i))
            if outcome:
                saved.append(outcome)
        return saved

    async def _match_related(self, candidates: list[dict]) -> dict[int, dict]:
        """候选序号 → 相似旧记忆。两层：embedding 相似度快筛，未命中者
        由轻量模型按「同一主题」粗筛（对表述差异鲁棒，如搬家 vs 居住状态）。"""
        mapping: dict[int, dict] = {}
        pending: list[int] = []
        for i, cand in enumerate(candidates):
            old = await self._semantic_find(cand["content"])
            if old is not None:
                mapping[i] = old
            else:
                pending.append(i)
        if pending:
            screened = await self._screen_by_topic([candidates[i] for i in pending])
            for local_i, old in screened.items():
                mapping[pending[local_i]] = old
        return mapping

    async def _semantic_find(self, text: str) -> Optional[dict]:
        """向量模式：top1 相似度达阈值即视为同一话题。降级模式返回 None。"""
        if not self._vs.enabled:
            return None
        hits = await asyncio.to_thread(self._vs.search, text, 1)
        if hits and hits[0]["similarity"] >= config.SIMILARITY_MERGE_THRESHOLD:
            return hits[0]["memory"]
        return None

    async def _save_candidate(self, session_id: str, cand: dict,
                              old: Optional[dict]) -> Optional[dict]:
        """单条候选记忆：结合预匹配的相似旧记忆做决策后落库。"""
        content = _normalize_perspective(str(cand["content"]).strip())
        category, importance = cand["category"], cand["importance"]
        if not content:
            return None
        if old is not None:
            # 同轮已有新记忆取代了它 → 沿取代链定位最新版本再决策，避免链上错位
            if old["status"] == "superseded" and old.get("superseded_by"):
                latest = self._store.get_memory(old["superseded_by"])
                if latest and latest["status"] == "active":
                    old = latest
            decision = await self._decide_merge(content, category, old)
            if decision["decision"] == "SKIP":
                return None
            if decision["decision"] == "MERGE" and decision["merged_content"]:
                content = decision["merged_content"]
        else:
            decision = {"decision": "ADD"}

        memory_id = self._store.add_memory(content, category, importance,
                                           source_session=session_id)
        try:
            self._vs.add(memory_id, content,
                         {"category": category, "importance": importance})
        except Exception as e:
            # 向量索引失败不影响记忆本身（SQLite 已入库），下轮重启可重建
            print(f"  （向量索引写入失败，记忆仍已保存：{type(e).__name__}）")

        if old is not None and decision["decision"] in ("SUPERSEDE", "MERGE"):
            self._store.supersede(old["id"], memory_id)
            print(f"  （记忆更新：「{old['content']}」已成为过去）")

        return {"id": memory_id, "content": content,
                "decision": decision["decision"],
                "superseded": old["content"] if old is not None else None}

    async def _screen_by_topic(self, candidates: list[dict]) -> dict[int, dict]:
        """LLM 联合粗筛：对向量未命中的候选，按「同一主题」在已有记忆中找关联。

        记忆量大时先做向量预筛（按候选的语义相似度挑出最相关的记忆池），
        避免「只看 importance 前 40 条」漏掉低重要性但相关的记忆。
        """
        actives = self._store.all_active_memories()
        if not candidates or not actives:
            return {}
        pool = await self._pre_filter_pool(candidates, actives)
        result = await self._llm.chat_json(
            [
                {"role": "system", "content": prompts.MEMORY_SCREEN_SYSTEM},
                {"role": "user", "content": prompts.MEMORY_SCREEN_USER.format(
                    memories="\n".join(
                        f"{i}. ({m['category']}) {m['content']}"
                        for i, m in enumerate(pool, 1)),
                    candidates="\n".join(
                        f"{i}. ({c['category']}) {c['content']}"
                        for i, c in enumerate(candidates, 1)),
                )},
            ],
            temperature=0.0, purpose="merge",
        )
        mapping: dict[int, dict] = {}
        if isinstance(result, list):
            for item in result:
                try:
                    ci = int(item.get("index")) - 1
                    related = [int(r) for r in item.get("related", [])]
                except (TypeError, ValueError, AttributeError):
                    continue
                if not (0 <= ci < len(candidates)):
                    continue
                for r in related:
                    if 1 <= r <= len(pool):
                        mapping[ci] = pool[r - 1]
                        break
        return mapping

    async def _pre_filter_pool(self, candidates: list[dict],
                               actives: list[dict]) -> list[dict]:
        """粗筛记忆池：≤40 条直接全量；更大时按候选的向量相似度选相关子集。"""
        if len(actives) <= config.SCREEN_POOL_SIZE or not self._vs.enabled:
            return actives[: config.SCREEN_POOL_SIZE]
        scored: dict[int, float] = {}
        for cand in candidates:
            try:
                hits = await asyncio.to_thread(
                    self._vs.search, cand["content"], config.SCREEN_POOL_SIZE
                )
            except Exception:
                continue
            for hit in hits:
                scored[hit["memory"]["id"]] = max(
                    scored.get(hit["memory"]["id"], 0.0), hit["similarity"])
        pool_ids = {mid for mid, _ in sorted(scored.items(), key=lambda x: -x[1])
                    [: config.SCREEN_POOL_SIZE]}
        pool = [m for m in actives if m["id"] in pool_ids]
        # 池不足时按 importance 补齐，保证核心事实始终在场
        if len(pool) < config.SCREEN_POOL_SIZE:
            for m in actives:
                if m["id"] not in pool_ids:
                    pool.append(m)
                    if len(pool) >= config.SCREEN_POOL_SIZE:
                        break
        return pool

    async def _decide_merge(self, new_content: str, new_category: str,
                            old: dict) -> dict:
        result = await self._llm.chat_json(
            [
                {"role": "system", "content": prompts.MEMORY_DECISION_SYSTEM},
                {"role": "user", "content": prompts.MEMORY_DECISION_USER.format(
                    old_category=old["category"], old_date=old["created_at"],
                    old_memory=old["content"],
                    new_category=new_category, new_memory=new_content,
                )},
            ],
            temperature=0.0, purpose="merge",
        )
        if not isinstance(result, dict):
            return {"decision": "ADD"}
        decision = str(result.get("decision", "ADD")).upper()
        if decision not in ("ADD", "SUPERSEDE", "MERGE", "SKIP"):
            decision = "ADD"
        return {"decision": decision,
                "merged_content": str(result.get("merged_content", "")).strip()}


def _normalize_perspective(content: str) -> str:
    """视角兜底归一：记忆必须以「用户」为第三人称主语。

    抽取模型偶尔输出「我叫小王」「我给绿萝起名」这类第一人称记忆，混进
    prompt 会造成陪伴者的视角混乱。凡「我」开头的一律改写为「用户」——
    抽取规则本就禁止第一人称，能漏出来的「我X」必然指用户。
    无主语句（「在一家游戏公司做后端」）按白名单前缀补「用户」；
    第三方陈述（「年糕三岁」）不匹配白名单、保持原样。
    """
    if content.startswith("我"):
        content = "用户" + content[1:]
    subjectless = ("在", "做", "养了", "喜欢", "讨厌", "最近", "今天", "现在",
                   "生日", "下周", "打算", "准备", "即将")
    if "用户" not in content and content.startswith(subjectless):
        return "用户" + content
    return content


def _fmt_dialog(dialog: list[dict]) -> str:
    return "\n".join(f"{'用户' if m['role'] == 'user' else '陪伴者'}：{m['content']}"
                     for m in dialog)


def _fmt_profile(profile: dict) -> str:
    if not profile:
        return "（暂无，用户信息尚待了解）"
    return "\n".join(f"- {k}: {v}" for k, v in profile.items())


def _to_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
