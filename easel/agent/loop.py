"""The built-in agent loop: model step → tool calls → results → repeat."""
from __future__ import annotations

import hashlib
import json
from typing import Awaitable, Callable

from easel.harnesses.base import HarnessError

from . import llm
from .tools import TOOL_SPECS, ToolContext, describe_call, execute

MAX_STEPS = 80
REPEAT_WARN = 3
REPEAT_STOP = 5
DEFAULT_CONTEXT_TOKENS = 120_000
SUMMARY_HEADER = "[早前对话摘要]"

Persist = Callable[[list[dict]], None]


def estimate_tokens(messages: list[dict]) -> int:
    # Chinese runs near one token per character, English near four characters per token.
    return len(json.dumps(messages, ensure_ascii=False)) // 2


def _cut_index(history: list[dict], keep_tokens: int) -> int:
    """Earliest user message such that everything after it fits keep_tokens; 0 when none."""
    for index, message in enumerate(history):
        if (index > 0 and message.get("role") == "user"
                and not str(message.get("content", "")).startswith(SUMMARY_HEADER)
                and estimate_tokens(history[index:]) <= keep_tokens):
            return index
    return 0


async def compact(settings: dict, model: str, history: list[dict], budget: int) -> bool:
    """Summarize older turns in place when the history nears the context budget."""
    if estimate_tokens(history) <= budget * 3 // 4:
        return False
    cut = _cut_index(history, budget // 3)
    if cut <= 0:
        return False
    transcript = json.dumps(history[:cut], ensure_ascii=False)[-budget:]
    summary = await llm.complete(settings, [
        {"role": "system", "content": "你负责压缩对话记录。用中文分条总结：用户目标与偏好、已完成的步骤与产物路径、"
                                      "关键结论与数据、尚未完成的事项。不要遗漏文件路径和用户的明确要求。"},
        {"role": "user", "content": transcript}], model=model)
    history[:cut] = [{"role": "user", "content": f"{SUMMARY_HEADER}\n{summary.text.strip()}"},
                     {"role": "assistant", "content": "好的，我会基于以上摘要继续。"}]
    return True


def request_messages(system: str, history: list[dict]) -> list[dict]:
    """Thinking text goes back only within the current turn (needed by DeepSeek tool calls)."""
    last_user = max((index for index, message in enumerate(history) if message.get("role") == "user"), default=-1)
    messages = [{"role": "system", "content": system}]
    for index, message in enumerate(history):
        if index < last_user and "reasoning_content" in message:
            message = {key: value for key, value in message.items() if key != "reasoning_content"}
        messages.append(message)
    return messages


def _assistant_message(completion: llm.Completion) -> dict:
    message: dict = {"role": "assistant", "content": completion.text or None}
    if completion.reasoning:
        message["reasoning_content"] = completion.reasoning
    if completion.tool_calls:
        message["tool_calls"] = [{"id": call.id, "type": "function",
                                  "function": {"name": call.name, "arguments": call.arguments}}
                                 for call in completion.tool_calls]
    return message


async def run_turn(*, settings: dict, model: str, system: str, prompt: str, history: list[dict],
                   ctx: ToolContext, persist: Persist, context_tokens: int = DEFAULT_CONTEXT_TOKENS,
                   max_steps: int = MAX_STEPS) -> str:
    """Run one user turn to completion; history is mutated and persisted after every step."""
    emit = ctx.emit
    history.append({"role": "user", "content": prompt})
    persist(history)
    spoke = False
    repeat_key, repeat_count = "", 0
    stuck = ""

    def on_text(token: str) -> None:
        nonlocal spoke
        spoke = True
        emit("token", token)

    for _step in range(max_steps):
        if await compact(settings, model, history, context_tokens):
            emit("activity", "上下文接近上限，已自动压缩较早的对话")
            persist(history)
        if spoke:
            emit("token", "\n\n")
            spoke = False
        completion = await llm.complete(settings, request_messages(system, history),
                                        model=model, tools=TOOL_SPECS, on_text=on_text,
                                        on_thinking=lambda token: emit("thinking", token))
        history.append(_assistant_message(completion))
        persist(history)
        if not completion.tool_calls:
            if completion.finish_reason == "length":
                raise HarnessError("回复达到模型输出长度上限，已保留收到的内容；可让 Agent 继续")
            return completion.text
        for call in completion.tool_calls:
            emit("activity", describe_call(call.name, call.arguments))
            content, is_error = await execute(ctx, call.name, call.arguments)
            key = hashlib.sha256(f"{call.name}\0{call.arguments}\0{content}".encode()).hexdigest()
            repeat_count = repeat_count + 1 if key == repeat_key else 1
            repeat_key = key
            if repeat_count >= REPEAT_STOP:
                stuck = call.name
            elif repeat_count >= REPEAT_WARN:
                content += (f"\n\n[提醒] 你已经连续 {repeat_count} 次用相同参数调用 {call.name} 并得到相同结果，"
                            "没有新进展。请换一种做法，或停下来说明受阻原因。")
            history.append({"role": "tool", "tool_call_id": call.id,
                            "content": content if not is_error else f"[错误] {content}"})
            persist(history)
        if stuck:
            break
    reason = (f"连续 {REPEAT_STOP} 次相同调用 {stuck} 没有进展" if stuck else f"已达到单轮 {max_steps} 步上限")
    history.append({"role": "user", "content": f"[系统提示] {reason}。停止调用工具，"
                                               "用简短中文总结已完成的部分、未完成的部分和受阻原因。"})
    if spoke:
        emit("token", "\n\n")
    summary = await llm.complete(settings, request_messages(system, history),
                                 model=model, on_text=lambda token: emit("token", token))
    history.append(_assistant_message(summary))
    persist(history)
    raise HarnessError(f"{reason}，已停止调用工具并总结进度")
