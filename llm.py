"""大模型客户端：走 OpenAI 兼容的 /chat/completions 接口。

DeepSeek、通义、Moonshot、Ollama、vLLM 都是这个协议，改 LLM_BASE_URL 即可。
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

import httpx

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """大模型调用失败，消息可直接回给用户看。"""


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 1.0,
        max_tokens: int = 2048,
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=15.0),
            headers=headers,
        )

    def close(self) -> None:
        self._client.close()

    def chat(self, messages: List[dict]) -> str:
        """发一轮对话，返回纯文本回复。retries=1 只重试一次幂等失败。"""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }

        last_error: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                resp = self._client.post(url, json=payload)
            except httpx.TimeoutException:
                last_error = LLMError("模型响应超时了，再试一次或者缩短问题。")
                log.warning("LLM timeout (attempt %s)", attempt)
                if attempt == 1:
                    time.sleep(1.0)
                    continue
                raise last_error
            except httpx.HTTPError as exc:
                last_error = LLMError(f"连不上模型服务：{exc.__class__.__name__}")
                log.warning("LLM transport error (attempt %s): %s", attempt, exc)
                if attempt == 1:
                    time.sleep(1.0)
                    continue
                raise last_error

            # 5xx / 429 值得重试
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = LLMError(_explain_status(resp))
                log.warning("LLM retryable status %s (attempt %s)", resp.status_code, attempt)
                if attempt == 1:
                    time.sleep(2.0)
                    continue
                raise last_error

            if resp.status_code >= 400:
                raise LLMError(_explain_status(resp))

            try:
                data = resp.json()
            except ValueError:
                raise LLMError("模型返回的不是合法 JSON。")

            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise LLMError(f"模型返回结构异常：{str(data)[:200]}")

            if not text or not text.strip():
                raise LLMError("模型返回了空内容，换个说法再试试？")
            return text.strip()

        raise last_error or LLMError("模型调用失败。")


def _explain_status(resp: httpx.Response) -> str:
    """把常见错误码翻成人话。"""
    code = resp.status_code
    detail = ""
    try:
        body = resp.json()
        detail = body.get("error", {}).get("message") or body.get("message") or ""
    except Exception:
        detail = (resp.text or "")[:200]

    hints = {
        400: "请求格式有问题",
        401: "API Key 无效，检查 LLM_API_KEY",
        402: "账户余额不足",
        403: "没有权限访问这个模型",
        404: "模型名或接口地址不对，检查 LLM_MODEL / LLM_BASE_URL",
        422: "参数不合法",
        429: "请求太频繁，被限流了",
        500: "模型服务内部错误",
        503: "模型服务暂时不可用",
    }
    hint = hints.get(code, f"HTTP {code}")
    return f"{hint}（{detail}）" if detail else hint