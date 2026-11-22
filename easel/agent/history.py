"""Per-session message history for the built-in agent."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from easel.harnesses.base import HarnessError

SESSION_ID = re.compile(r"[a-f0-9]{32}")


def history_file(directory: Path, session_id: str) -> Path:
    if not SESSION_ID.fullmatch(session_id or ""):
        raise HarnessError("内置 Agent 会话标识无效，不会自动新建会话替换历史")
    return directory / f"{session_id}.json"


def repair(messages: list[dict]) -> list[dict]:
    """Give every tool call a result so a stopped turn can continue under the chat API's rules."""
    repaired: list[dict] = []
    pending: list[str] = []
    for message in messages:
        if pending and message.get("role") != "tool":
            repaired += [{"role": "tool", "tool_call_id": call, "content": "（上一轮已停止，此工具未完成）"}
                         for call in pending]
            pending = []
        repaired.append(message)
        if message.get("role") == "assistant":
            pending = [call.get("id") for call in message.get("tool_calls") or [] if call.get("id")]
        elif message.get("role") == "tool" and message.get("tool_call_id") in pending:
            pending.remove(message["tool_call_id"])
    repaired += [{"role": "tool", "tool_call_id": call, "content": "（上一轮已停止，此工具未完成）"}
                 for call in pending]
    return repaired


def load(directory: Path, session_id: str) -> list[dict]:
    path = history_file(directory, session_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HarnessError("内置 Agent 会话历史损坏，请新建对话；原文件保留未改动") from None
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise HarnessError("内置 Agent 会话历史格式无效，请新建对话；原文件保留未改动")
    return repair(data)


def save(directory: Path, session_id: str, messages: list[dict]) -> None:
    path = history_file(directory, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(messages, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
