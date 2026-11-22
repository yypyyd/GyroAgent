"""Local tools for the built-in agent, with Easel's workspace safety rules.

Reads stay inside the project root and never touch credentials; writes are limited
to outputs/ and profiles/; irreversible commands require the user's confirmation.
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

MAX_RESULT_CHARS = 30_000
MAX_READ_LINES = 2_000
MAX_LISTING = 300
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", ".pytest_cache", "easel.egg-info"}
WRITE_ROOTS = ("outputs", "profiles")

RISKY_COMMANDS = re.compile(
    r"\brm\s+-[a-z]*[rf]|\brmdir\b|\bdel\s+/[sq]|\bRemove-Item\b|\bformat\s+[a-z]:|\bshutdown\b"
    r"|\bgit\s+(push|reset\s+--hard|clean|checkout\s+--)"
    r"|(xhs_publish|douyin_publish|web_publisher|toutiao_publish|zhihu_answer|xhs_comment|publish_dispatch"
    r"|multi_publish|bili_upload|biliup)\b|\bpublish\.py\b|--publish\b|\bupload\b",
    re.IGNORECASE)
SECRET_READ = re.compile(
    r"\b(cat|type|more|less|head|tail|Get-Content|gc|strings|xxd|od)\b[^|;&\n]*(^|[\\/\s'\"])\.env(\.local)?(['\"\s]|$)",
    re.IGNORECASE)

Ask = Callable[[list[dict]], Awaitable[dict[str, list[str]]]]
Emit = Callable[[str, str], None]


class ToolError(Exception):
    pass


@dataclass
class ToolContext:
    root: Path
    ask: Ask
    emit: Emit
    sandbox: str = "workspace-write"
    scratch: Path | None = None
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.root = self.root.resolve()
        if self.scratch is None:
            self.scratch = self.root / "outputs" / "_agent"


def _spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOL_SPECS = [
    _spec("read_file", "读取项目内的文本文件，返回带行号的内容。大文件用 offset/limit 分段读取。",
          {"path": {"type": "string", "description": "相对项目根或绝对路径"},
           "offset": {"type": "integer", "description": "起始行号（从 1 开始）"},
           "limit": {"type": "integer", "description": f"最多读取行数，默认 {MAX_READ_LINES}"}}, ["path"]),
    _spec("list_dir", "列出目录内容；目录名以 / 结尾。",
          {"path": {"type": "string", "description": "相对项目根或绝对路径，默认项目根"}}, []),
    _spec("glob", "按 glob 模式查找文件（如 skills/builtin/*/SKILL.md），按修改时间倒序。",
          {"pattern": {"type": "string"}, "path": {"type": "string", "description": "搜索起点，默认项目根"}},
          ["pattern"]),
    _spec("grep", "在文件内容中按正则搜索，返回 路径:行号: 内容。",
          {"pattern": {"type": "string"}, "path": {"type": "string", "description": "文件或目录，默认项目根"},
           "glob": {"type": "string", "description": "只搜文件名匹配此模式的文件，如 *.md"},
           "ignore_case": {"type": "boolean"}}, ["pattern"]),
    _spec("write_file", "创建或覆盖文件。只能写入 outputs/ 或 profiles/ 下。",
          {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _spec("edit_file", "精确替换文件中的一段文字。old_string 必须唯一匹配，除非 replace_all=true。"
                       "只能修改 outputs/ 或 profiles/ 下的文件。",
          {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
           "replace_all": {"type": "boolean"}}, ["path", "old_string", "new_string"]),
    _spec("run_command", "在项目根目录执行一条非交互 shell 命令（用于运行技能脚本等）。"
                         "删除、推送、发布等不可逆命令会先请用户确认。",
          {"command": {"type": "string"},
           "timeout": {"type": "integer", "description": "超时秒数，默认 600，最大 1800"}}, ["command"]),
    _spec("ask_user", "需要用户做决定时提问；每题给出可选项，用户也可自填。只在答案会改变后续做法时使用。",
          {"questions": {"type": "array", "items": {"type": "object", "properties": {
              "question": {"type": "string"},
              "header": {"type": "string", "description": "2-6 字的短标签"},
              "options": {"type": "array", "items": {"type": "string"}},
              "multi_select": {"type": "boolean"}}, "required": ["question", "options"]}}}, ["questions"]),
]

TOOL_NAMES = {spec["function"]["name"] for spec in TOOL_SPECS}


def _resolve(ctx: ToolContext, value: str | None) -> Path:
    raw = Path(value or ".")
    path = (raw if raw.is_absolute() else ctx.root / raw).resolve()
    try:
        relative = path.relative_to(ctx.root)
    except ValueError:
        raise ToolError(f"只能访问项目目录 {ctx.root} 内的文件") from None
    parts = relative.parts
    if ".git" in parts:
        raise ToolError("不能访问 .git 目录")
    if parts and re.fullmatch(r"\.env(\..+)?", parts[-1]) and parts[-1] != ".env.example":
        raise ToolError("不能读取或修改 .env 凭证文件；请用技能脚本的 check 命令查看脱敏配置")
    return path


def _writable(ctx: ToolContext, path: Path) -> None:
    if ctx.sandbox == "read-only":
        raise ToolError("当前为只读权限，不能写入文件")
    relative = path.relative_to(ctx.root)
    if not relative.parts or relative.parts[0] not in WRITE_ROOTS:
        raise ToolError("只能写入 outputs/ 或 profiles/ 下的文件")


def _display(ctx: ToolContext, path: Path) -> str:
    try:
        return path.relative_to(ctx.root).as_posix() or "."
    except ValueError:
        return str(path)


def _clip(ctx: ToolContext, text: str, label: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    ctx.scratch.mkdir(parents=True, exist_ok=True)
    saved = ctx.scratch / f"{label}-{uuid.uuid4().hex[:8]}.txt"
    saved.write_text(text, encoding="utf-8")
    half = MAX_RESULT_CHARS // 2
    return (text[:half] + f"\n\n…（输出过长，中间已省略；完整内容见 {_display(ctx, saved)}）…\n\n" + text[-half:])


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        raise ToolError(f"{path.name} 是二进制文件（{len(data)} 字节），不能按文本读取")
    return data.decode("utf-8", errors="replace")


def read_file(ctx: ToolContext, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> str:
    target = _resolve(ctx, path)
    if not target.is_file():
        raise ToolError(f"文件不存在：{_display(ctx, target)}")
    lines = _read_text(target).splitlines()
    start = max(int(offset or 1), 1)
    count = min(max(int(limit or MAX_READ_LINES), 1), MAX_READ_LINES)
    chunk = lines[start - 1:start - 1 + count]
    body = "\n".join(f"{number:>6}|{line}" for number, line in enumerate(chunk, start))
    more = len(lines) - (start - 1 + len(chunk))
    if more > 0:
        body += f"\n…（还有 {more} 行，用 offset={start + len(chunk)} 继续读取）"
    return _clip(ctx, body or "（空文件）", "read")


def _walk(base: Path):
    for directory, names, files in os.walk(base):
        names[:] = sorted(name for name in names if name not in SKIP_DIRS)
        for name in sorted(files):
            yield Path(directory) / name


def list_dir(ctx: ToolContext, path: str = ".") -> str:
    target = _resolve(ctx, path)
    if not target.is_dir():
        raise ToolError(f"目录不存在：{_display(ctx, target)}")
    entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    lines = []
    for item in entries[:MAX_LISTING]:
        if item.is_dir():
            lines.append(item.name + "/")
        else:
            lines.append(f"{item.name}  ({item.stat().st_size} B)")
    if len(entries) > MAX_LISTING:
        lines.append(f"…（共 {len(entries)} 项，仅列出前 {MAX_LISTING} 项）")
    return "\n".join(lines) or "（空目录）"


def glob_files(ctx: ToolContext, pattern: str, path: str = ".") -> str:
    base = _resolve(ctx, path)
    if not base.is_dir():
        raise ToolError(f"目录不存在：{_display(ctx, base)}")
    matches = [item for item in base.glob(pattern)
               if item.is_file() and not SKIP_DIRS.intersection(item.relative_to(ctx.root).parts)]
    matches.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    lines = [_display(ctx, item) for item in matches[:MAX_LISTING]]
    if len(matches) > MAX_LISTING:
        lines.append(f"…（共 {len(matches)} 个，仅列出最近修改的 {MAX_LISTING} 个）")
    return "\n".join(lines) or "没有匹配的文件"


def grep(ctx: ToolContext, pattern: str, path: str = ".", glob: str = "", ignore_case: bool = False) -> str:
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as error:
        raise ToolError(f"正则表达式无效：{error}") from None
    base = _resolve(ctx, path)
    files = [base] if base.is_file() else list(_walk(base)) if base.is_dir() else []
    hits = []
    for item in files:
        if glob and not fnmatch.fnmatch(item.name, glob):
            continue
        try:
            if item.stat().st_size > 2_000_000:
                continue
            text = _read_text(_resolve(ctx, str(item)))
        except (ToolError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                hits.append(f"{_display(ctx, item)}:{number}: {line.strip()[:300]}")
                if len(hits) >= MAX_LISTING:
                    return "\n".join(hits) + f"\n…（结果过多，仅显示前 {MAX_LISTING} 条，请缩小范围）"
    return "\n".join(hits) or "没有匹配"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with open(temporary, "w", encoding="utf-8", newline="") as stream:
        stream.write(text)
    temporary.replace(path)


def write_file(ctx: ToolContext, path: str, content: str) -> str:
    target = _resolve(ctx, path)
    _writable(ctx, target)
    _atomic_write(target, content)
    return f"已写入 {_display(ctx, target)}（{len(content)} 字符）"


def edit_file(ctx: ToolContext, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    target = _resolve(ctx, path)
    _writable(ctx, target)
    if not target.is_file():
        raise ToolError(f"文件不存在：{_display(ctx, target)}")
    with open(target, encoding="utf-8", errors="replace", newline="") as stream:
        text = stream.read()
    if "\r\n" in text and "\r\n" not in old_string:
        old_string, new_string = old_string.replace("\n", "\r\n"), new_string.replace("\n", "\r\n")
    count = text.count(old_string) if old_string else 0
    if count == 0:
        raise ToolError("没有找到 old_string，请先 read_file 核对原文（包括空格与缩进）")
    if count > 1 and not replace_all:
        raise ToolError(f"old_string 出现了 {count} 次，请提供更多上下文使其唯一，或设置 replace_all=true")
    _atomic_write(target, text.replace(old_string, new_string))
    return f"已修改 {_display(ctx, target)}（替换 {count if replace_all else 1} 处）"


def _git_bash() -> str | None:
    if sys.platform != "win32":
        return None
    git = shutil.which("git")
    candidates = []
    if git:
        folder = Path(git).resolve().parent
        candidates += [folder / "bash.exe", folder.parent / "bin" / "bash.exe", folder.parent.parent / "bin" / "bash.exe"]
    for root in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", "")):
        if root:
            candidates.append(Path(root) / "Git" / "bin" / "bash.exe")
    return next((str(path) for path in candidates if path.is_file()), None)


def shell_command(command: str) -> tuple[list[str], str]:
    """argv that runs one command string, and a human name for the shell."""
    if sys.platform != "win32":
        return ["/bin/bash", "-c", command], "bash"
    bash = _git_bash()
    if bash:
        return [bash, "-l", "-c", command], "Git Bash"
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command], "PowerShell"


async def _confirm(ctx: ToolContext, header: str, question: str) -> bool:
    allow = "允许本次"
    try:
        answers = await ctx.ask([{"questionId": "confirm", "header": header, "question": question,
                                  "options": [{"label": allow}, {"label": "拒绝"}]}])
    except Exception:  # noqa: BLE001 - unattended or unanswered means no consent
        return False
    return (answers.get("confirm") or [""])[0] == allow


def _kill_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           capture_output=True, timeout=10, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        process.kill()


async def run_command(ctx: ToolContext, command: str, timeout: int = 600) -> str:
    command = (command or "").strip()
    if not command:
        raise ToolError("命令为空")
    if ctx.sandbox == "read-only":
        raise ToolError("当前为只读权限，不能执行命令")
    if SECRET_READ.search(command):
        raise ToolError("不能直接查看 .env 凭证；请用技能脚本的 check 命令查看脱敏配置")
    if RISKY_COMMANDS.search(command):
        if not await _confirm(ctx, "执行确认", f"Agent 想执行可能不可逆的命令：\n{command[:400]}"):
            raise ToolError("用户未同意执行此命令；请换一种做法，或说明需要用户手动完成的步骤")
    timeout = min(max(int(timeout or 600), 1), 1800)
    argv, _shell = shell_command(command)
    python_dir = ctx.root / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    env = {**os.environ, **ctx.env, "EASEL_ROOT": str(ctx.root), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "PATH": str(python_dir) + os.pathsep + os.environ.get("PATH", "")}
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=ctx.root, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        **({} if sys.platform == "win32" else {"start_new_session": True}))
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        _kill_tree(process)
        raise ToolError(f"命令超过 {timeout} 秒未结束，已终止") from None
    except asyncio.CancelledError:
        _kill_tree(process)
        raise
    text = output.decode("utf-8", errors="replace").replace("\r\n", "\n")
    return _clip(ctx, f"[exit {process.returncode}]\n{text}".rstrip(), "command")


async def ask_user(ctx: ToolContext, questions: list) -> str:
    if not isinstance(questions, list) or not questions:
        raise ToolError("questions 不能为空")
    cards = []
    for index, item in enumerate(questions[:4], 1):
        if not isinstance(item, dict) or not str(item.get("question", "")).strip():
            raise ToolError("每个问题都需要 question 文本")
        options = [str(option)[:120] for option in (item.get("options") or []) if str(option).strip()][:6]
        card = {"questionId": f"q{index}", "question": str(item["question"])[:500],
                "options": [{"label": option} for option in options]}
        if item.get("header"):
            card["header"] = str(item["header"])[:12]
        if item.get("multi_select"):
            card["multiSelect"] = True
        cards.append(card)
    try:
        answers = await ctx.ask(cards)
    except Exception as error:  # noqa: BLE001
        raise ToolError(f"无法向用户提问：{error}") from None
    return json.dumps({card["question"]: answers.get(card["questionId"], []) for card in cards}, ensure_ascii=False)


async def execute(ctx: ToolContext, name: str, arguments: str) -> tuple[str, bool]:
    """Run one tool call. Returns (content for the model, is_error)."""
    if name not in TOOL_NAMES:
        return f"未知工具：{name}。可用工具：{', '.join(sorted(TOOL_NAMES))}", True
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return "工具参数不是合法的 JSON 对象，请重新生成完整参数", True
    try:
        if name == "read_file":
            return read_file(ctx, args.get("path", ""), args.get("offset", 1), args.get("limit", MAX_READ_LINES)), False
        if name == "list_dir":
            return list_dir(ctx, args.get("path", ".")), False
        if name == "glob":
            return glob_files(ctx, args.get("pattern", ""), args.get("path", ".")), False
        if name == "grep":
            return grep(ctx, args.get("pattern", ""), args.get("path", "."), args.get("glob", ""),
                        bool(args.get("ignore_case"))), False
        if name == "write_file":
            return write_file(ctx, args.get("path", ""), args.get("content", "")), False
        if name == "edit_file":
            return edit_file(ctx, args.get("path", ""), args.get("old_string", ""), args.get("new_string", ""),
                             bool(args.get("replace_all"))), False
        if name == "run_command":
            return await run_command(ctx, args.get("command", ""), args.get("timeout", 600)), False
        return await ask_user(ctx, args.get("questions")), False
    except ToolError as error:
        return str(error), True
    except (OSError, ValueError, TypeError) as error:
        return f"{name} 执行失败：{type(error).__name__}: {error}", True


def describe_call(name: str, arguments: str) -> str:
    """Short activity line for the chat timeline."""
    try:
        args = json.loads(arguments or "{}")
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    detail = {
        "read_file": args.get("path"), "list_dir": args.get("path", "."), "glob": args.get("pattern"),
        "grep": args.get("pattern"), "write_file": args.get("path"), "edit_file": args.get("path"),
        "run_command": args.get("command"), "ask_user": "向你提问",
    }.get(name) or ""
    detail = " ".join(str(detail).split())
    return f"🔧 {name} {detail[:120]}".rstrip()
