"""Easel's built-in agent as an execution engine: model API + local tools, no external CLI."""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from easel.direct_api import DirectAPIError, api_config

from .base import Descriptor, HarnessError, RunRequest
from .workspace import harness_instructions

# easel.agent imports easel.harnesses.base, so agent modules load on first use, not at import.

SKILL_INDEX_CHARS = 24_000


def _frontmatter(path: Path) -> tuple[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    match = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    if not match:
        return "", ""
    name, description, lines = "", "", match.group(1).splitlines()
    for index, line in enumerate(lines):
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip("'\"")
        elif line.startswith("description:"):
            value = line.split(":", 1)[1].strip()
            if value in (">", ">-", "|", "|-", ""):
                folded = []
                for follow in lines[index + 1:]:
                    if follow and not follow[0].isspace():
                        break
                    folded.append(follow.strip())
                value = " ".join(part for part in folded if part)
            description = value.strip("'\"")
    return name, description


def skill_index(root: Path) -> str:
    entries = []
    for skill in sorted((root / "skills" / "builtin").glob("*/SKILL.md")):
        name, description = _frontmatter(skill)
        if name or description:
            entries.append(f"- {name or skill.parent.name}（skills/builtin/{skill.parent.name}/SKILL.md）："
                           f"{' '.join(description.split())[:110]}")
    text = "\n".join(entries)
    if len(text) > SKILL_INDEX_CHARS:
        text = text[:SKILL_INDEX_CHARS] + "\n…（技能较多，其余可用 glob skills/builtin/*/SKILL.md 查看）"
    return text


def system_prompt(request: RunRequest) -> str:
    from easel.agent.tools import shell_command

    root = request.cwd.resolve()
    _argv, shell = shell_command("")
    tools = (
        "\n\n## 内置 Agent 的工具\n\n"
        "你运行在 Easel 内置 Agent 中，只能使用下列工具，不要假装调用其它工具：\n"
        "- read_file / list_dir / glob / grep：读取和查找项目内文件（.env 凭证文件不可读）。\n"
        "- write_file / edit_file：只能写入 outputs/ 与 profiles/。\n"
        f"- run_command：在项目根执行命令，当前 shell 是 {shell}"
        + ("（POSIX 语法；Windows 路径请用正斜杠）" if shell != "PowerShell" else "（PowerShell 语法）")
        + "。删除、推送、发布等不可逆命令会先弹出确认卡片。\n"
        "- ask_user：需要用户做决定时提问，用户会看到选项卡片。\n"
        "执行任务前先读对应 SKILL.md，按其中的命令运行脚本；完成后用一两句话说明结果和产物路径。\n"
        "\n## 技能索引\n\n" + skill_index(root)
    )
    return harness_instructions(request) + tools


class EaselAgentAdapter:
    descriptor = Descriptor("easel", "Easel 内置 Agent", "内置 · 模型 API 直连",
                            "在下方「内置 Agent API 配置」中填写 API 地址、模型与 Key")

    async def run(self, request: RunRequest, emit, ask, bind) -> None:
        from easel.agent import history as agent_history
        from easel.agent.loop import DEFAULT_CONTEXT_TOKENS, run_turn
        from easel.agent.tools import ToolContext

        settings = dict(request.env)
        try:
            api_config(settings, require_model=not request.model)
        except DirectAPIError as error:
            raise HarnessError(f"内置 Agent 的模型 API 未配置：{error}") from None
        session_id = request.native_id or uuid.uuid4().hex
        directory = request.cwd / "outputs" / "_sessions" / "agent"
        history = agent_history.load(directory, session_id)
        if not request.native_id:
            bind(session_id)
        ctx = ToolContext(request.cwd, ask, emit, sandbox=request.sandbox,
                          scratch=request.cwd / "outputs" / "_agent" / session_id,
                          env={"EASEL_AGENT_RUNTIME": "easel"})
        try:
            budget = int(os.environ.get("EASEL_AGENT_CONTEXT_TOKENS") or settings.get("EASEL_AGENT_CONTEXT_TOKENS")
                         or DEFAULT_CONTEXT_TOKENS)
        except ValueError:
            budget = DEFAULT_CONTEXT_TOKENS
        try:
            await run_turn(settings=settings, model=request.model, system=system_prompt(request),
                           prompt=request.prompt, history=history, ctx=ctx,
                           persist=lambda messages: agent_history.save(directory, session_id, messages),
                           context_tokens=max(budget, 8_000))
        except DirectAPIError as error:
            raise HarnessError(str(error)) from None
