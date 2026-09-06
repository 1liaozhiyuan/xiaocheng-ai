"""向量检索层：chromadb 封装。

默认用智谱 embedding-3（中文效果好）；未配 key 时退回 chroma 内置模型，
便于离线跑通链路。换其他 embedding 只需改这里。
"""
from typing import Optional

import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings

import config
from memory.store import MemoryStore


class _OpenAICompatEmbedding(EmbeddingFunction):
    """OpenAI 兼容 /embeddings 端点的最小实现（用于智谱 embedding-3）。"""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def __call__(self, input: Documents) -> Embeddings:
        resp = self._client.embeddings.create(model=self._model, input=list(input))
        return [d.embedding for d in resp.data]

    def name(self) -> str:
        return f"openai_compat:{self._model}"


class VectorStore:
    """向量索引。只存 memory id + metadata，记忆原文以 SQLite 为准。

    embedding API 不可用（未配 key / 余额不足 / 网络问题）时自动降级：
    禁用语义检索，Retriever 会退回按重要性+时间注入，记忆功能不受影响。
    """

    def __init__(self, store: MemoryStore) -> None:
        config.ensure_dirs()
        self._store = store
        self.enabled = False
        self._embed_fn = None
        if config.EMBED_MODEL and config.EMBED_API_KEY:
            self._embed_fn = _OpenAICompatEmbedding(
                config.EMBED_API_KEY, config.EMBED_BASE_URL, config.EMBED_MODEL
            )
            if not self._probe():
                self._embed_fn = None
                print(f"⚠️ 向量化模型 {config.EMBED_MODEL} 不可用（key 无效/余额不足/网络问题），"
                      f"语义检索已禁用，记忆仍按重要性注入。")
        self._client = chromadb.PersistentClient(path=str(config.CHROMA_PATH))
        self._col = self._client.get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"},
            embedding_function=self._embed_fn,
        )
        self.enabled = self._embed_fn is not None

    def _probe(self) -> bool:
        """启动时用一条短文本试嵌入，避免跑到一半才发现 API 不可用。"""
        try:
            self._embed_fn(input=["连接测试"])
            return True
        except Exception as e:
            print(f"（向量化探测失败：{type(e).__name__}）")
            return False

    def add(self, memory_id: int, content: str, metadata: dict) -> None:
        if not self.enabled:
            return
        self._col.add(ids=[str(memory_id)], documents=[content], metadatas=[metadata])

    def update(self, memory_id: int, content: str, metadata: dict) -> None:
        if not self.enabled:
            return
        self._col.upsert(ids=[str(memory_id)], documents=[content], metadatas=[metadata])

    def delete(self, memory_id: int) -> None:
        if not self.enabled:
            return
        self._col.delete(ids=[str(memory_id)])

    def count(self) -> int:
        return self._col.count() if self.enabled else 0

    def search(self, query: str, top_k: int) -> list[dict]:
        """返回 [{"memory": 记忆行, "similarity": 0~1}]，按相似度降序。

        不在此处按状态过滤：active 与 superseded 都返回，
        由 Retriever 分层处理（当前状态 vs 可召回的历史）。
        """
        if not self.enabled or self._col.count() == 0:
            return []
        res = self._col.query(query_texts=[query], n_results=min(top_k, self._col.count()))
        out = []
        for id_, dist in zip(res["ids"][0], res["distances"][0]):
            row = self._store.get_memory(int(id_))
            if row and row["status"] in ("active", "superseded"):
                out.append({"memory": row, "similarity": 1.0 - dist})
        return out
