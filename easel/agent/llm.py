"""Streaming OpenAI-compatible chat completions with tool calls."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Callable

import httpx

from easel.direct_api import BROWSER_UA, DirectAPIError, _sse_payloads, api_config

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Completion:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None


def _upstream_message(raw: bytes) -> str:
    try:
        payload = json.loads(raw)
    except ValueError:
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    return message.strip()[:200] if isinstance(message, str) else ""


async def _stream_once(settings: dict[str, str], body: dict, on_text, on_thinking, timeout: float) -> Completion:
    base, _model, key = api_config(settings, require_model=False)
    headers = {"User-Agent": BROWSER_UA}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    result = Completion()
    text, reasoning, calls = [], [], {}
    # Local/private gateways must bypass inherited system HTTP proxies.
    # Redirects must never forward the credential to a different destination.
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                 timeout=httpx.Timeout(timeout, connect=10)) as client:
        async with client.stream("POST", base + "/chat/completions", json=body, headers=headers) as response:
            if response.status_code != 200:
                detail = _upstream_message(await response.aread())
                error = DirectAPIError(f"API 返回 HTTP {response.status_code}"
                                       + (f"：{detail}" if detail else "，请检查地址、Key 和模型"))
                error.status = response.status_code
                raise error
            async for payload in _sse_payloads(response):
                if payload == "[DONE]":
                    break
                try:
                    event = json.loads(payload)
                except ValueError:
                    raise DirectAPIError("API 返回了无法解析的流数据") from None
                if not isinstance(event, dict):
                    continue
                if event.get("error"):
                    message = _upstream_message(json.dumps(event).encode())
                    raise DirectAPIError("API 返回错误" + (f"：{message}" if message else ""))
                choices = event.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                thought = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(thought, str) and thought:
                    reasoning.append(thought)
                    if on_thinking:
                        on_thinking(thought)
                token = delta.get("content")
                if isinstance(token, str) and token:
                    text.append(token)
                    if on_text:
                        on_text(token)
                for part in delta.get("tool_calls") or []:
                    index = part.get("index", len(calls))
                    slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if part.get("id"):
                        slot["id"] = part["id"]
                    function = part.get("function") or {}
                    if function.get("name"):
                        slot["name"] += function["name"]
                    if function.get("arguments"):
                        slot["arguments"] += function["arguments"]
                result.finish_reason = choice.get("finish_reason") or result.finish_reason
    result.text = "".join(text)
    result.reasoning = "".join(reasoning)
    result.tool_calls = [ToolCall(slot["id"] or f"call_{index}", slot["name"], slot["arguments"])
                         for index, slot in sorted(calls.items()) if slot["name"]]
    return result


async def complete(settings: dict[str, str], messages: list[dict], *, model: str = "",
                   tools: list[dict] | None = None,
                   on_text: Callable[[str], None] | None = None,
                   on_thinking: Callable[[str], None] | None = None,
                   timeout: float = 300, retries: int = 2) -> Completion:
    """One model step. Retries transient failures only while nothing has been streamed."""
    _base, configured, _key = api_config(settings, require_model=not model)
    body: dict = {"model": model or configured, "stream": True, "messages": messages}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    streamed = False

    def text(token):
        nonlocal streamed
        streamed = True
        if on_text:
            on_text(token)

    def thinking(token):
        nonlocal streamed
        streamed = True
        if on_thinking:
            on_thinking(token)

    for attempt in range(retries + 1):
        try:
            return await _stream_once(settings, body, text, thinking, timeout)
        except DirectAPIError as error:
            if streamed or attempt == retries or getattr(error, "status", None) not in RETRYABLE_STATUS:
                raise
        except httpx.TimeoutException:
            if streamed or attempt == retries:
                raise DirectAPIError("API 请求超时，请检查网关或重试") from None
        except httpx.HTTPError:
            if streamed or attempt == retries:
                raise DirectAPIError("API 连接失败，请确认网关正在运行且地址可达") from None
        await asyncio.sleep(1.5 * (attempt + 1))
    raise DirectAPIError("API 请求失败")
