"""LLM 客户端：统一封装 OpenAI 兼容协议，区分「对话强模型」与「抽取轻模型」。

所有 Agent 都从这里拿客户端，方便以后统一加重试、限流、成本统计。
Token 用量：每次调用记录到 usage_log（purpose 由调用方标注用途）；
流式请求通过 stream_options include_usage 获取（厂商不支持时静默跳过）。
"""
import json
import re
import time
from typing import AsyncIterator

from openai import AsyncOpenAI

import config
from memory.store import MemoryStore


class LLMClient:
    def __init__(self, store: MemoryStore | None = None) -> None:
        if not config.API_KEY:
            raise RuntimeError(
                "缺少 API key：请复制 .env.example 为 .env 并填入 ZHIPU_API_KEY"
                "（智谱开放平台 https://open.bigmodel.cn 免费注册即可领取）"
            )
        self._client = AsyncOpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
        self._store = store  # 用量落库；None 时不记录（轻量测试用）

    def _record(self, purpose: str, model: str, usage, latency_ms: int) -> None:
        if self._store is None:
            return
        try:
            u = usage if usage else None
            self._store.add_usage(
                purpose, model,
                getattr(u, "prompt_tokens", 0) or 0,
                getattr(u, "completion_tokens", 0) or 0,
                latency_ms,
            )
        except Exception:
            pass  # 用量记录失败不影响主链路

    async def chat(self, messages: list[dict], *, model: str | None = None,
                   temperature: float = 0.8, stream: bool = False,
                   purpose: str = "chat"):
        """对话补全。stream=True 时返回增量迭代器，否则返回完整文本。"""
        used_model = model or config.CHAT_MODEL
        t0 = time.time()
        resp = await self._client.chat.completions.create(
            model=used_model,
            messages=messages,
            temperature=temperature,
            stream=stream,
            **self._model_extras(used_model),
        )
        if stream:
            return resp
        latency = int((time.time() - t0) * 1000)
        self._record(purpose, used_model,
                     getattr(resp, "usage", None), latency)
        return resp.choices[0].message.content or ""

    @staticmethod
    def _model_extras(model: str) -> dict:
        """GLM-4.5 系默认带思考模式，陪伴对话关闭它换响应速度。"""
        if config.DISABLE_THINKING and model.startswith("glm-4.5"):
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {}

    async def chat_stream(self, messages: list[dict], *, model: str | None = None,
                          temperature: float = 0.8,
                          purpose: str = "chat") -> AsyncIterator[str]:
        """流式输出，逐段 yield 文本增量；结束帧带 usage 时落库。"""
        used_model = model or config.CHAT_MODEL
        t0 = time.time()
        resp = await self._client.chat.completions.create(
            model=used_model,
            messages=messages,
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
            **self._model_extras(used_model),
        )
        usage = None
        try:
            async for chunk in resp:
                if getattr(chunk, "usage", None):
                    usage = chunk.usage  # include_usage 的末帧
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except Exception:
            pass  # 某些厂商不支持 stream_options：用量缺失时静默跳过
        finally:
            self._record(purpose, used_model, usage, int((time.time() - t0) * 1000))

    async def chat_json(self, messages: list[dict], *,
                        model: str | None = None,
                        temperature: float = 0.2,
                        purpose: str = "json"):
        """要求 JSON 输出的结构化调用（记忆抽取、画像更新等用）。"""
        return await self.chat(messages, model=model or config.LITE_MODEL,
                               temperature=temperature, purpose=purpose)


def _parse_json(text: str):
    """容错解析：模型偶尔会用 ```json 包裹、夹带说明文字或输出 {{}} 双括号。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start > 0:
        text = text[start:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:  # 模型照着转义示例学会的坏习惯：{{ }} → { }
        return json.loads(text.replace("{{", "{").replace("}}", "}"))
    except json.JSONDecodeError:
        return None
