"""LLM 客户端：统一封装 OpenAI 兼容协议，区分「对话强模型」与「抽取轻模型」。

所有 Agent 都从这里拿客户端，方便以后统一加重试、限流、成本统计。
"""
import json
import re
from typing import AsyncIterator

from openai import AsyncOpenAI

import config


class LLMClient:
    def __init__(self) -> None:
        if not config.API_KEY:
            raise RuntimeError(
                "缺少 API key：请复制 .env.example 为 .env 并填入 ZHIPU_API_KEY"
                "（智谱开放平台 https://open.bigmodel.cn 免费注册即可领取）"
            )
        self._client = AsyncOpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)

    async def chat(self, messages: list[dict], *, model: str | None = None,
                   temperature: float = 0.8, stream: bool = False):
        """对话补全。stream=True 时返回增量迭代器，否则返回完整文本。"""
        used_model = model or config.CHAT_MODEL
        resp = await self._client.chat.completions.create(
            model=used_model,
            messages=messages,
            temperature=temperature,
            stream=stream,
            **self._model_extras(used_model),
        )
        if stream:
            return resp
        return resp.choices[0].message.content or ""

    @staticmethod
    def _model_extras(model: str) -> dict:
        """GLM-4.5 系默认带思考模式，陪伴对话关闭它换响应速度。"""
        if config.DISABLE_THINKING and model.startswith("glm-4.5"):
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {}

    async def chat_stream(self, messages: list[dict], *,
                          model: str | None = None,
                          temperature: float = 0.8) -> AsyncIterator[str]:
        """流式输出，逐段 yield 文本增量。"""
        resp = await self.chat(messages, model=model, temperature=temperature, stream=True)
        async for chunk in resp:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    async def chat_json(self, messages: list[dict], *,
                        model: str | None = None,
                        temperature: float = 0.2) -> any:
        """要求 JSON 输出的结构化调用（记忆抽取、画像更新等用）。"""
        text = await self.chat(messages, model=model or config.LITE_MODEL,
                               temperature=temperature)
        return _parse_json(text)


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
