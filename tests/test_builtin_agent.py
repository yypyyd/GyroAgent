import asyncio
import json
from pathlib import Path

import httpx
import pytest

from easel.agent import history as agent_history
from easel.agent import llm, loop, tools
from easel.agent.tools import ToolContext, execute
from easel.harnesses import ADAPTERS
from easel.harnesses.base import HarnessError, RunRequest

SETTINGS = {"EASEL_DIRECT_API_BASE_URL": "http://gateway.test/v1", "EASEL_DIRECT_API_MODEL": "demo-model",
            "EASEL_DIRECT_API_KEY": "sk-test"}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "skills" / "builtin" / "demo").mkdir(parents=True)
    (tmp_path / "skills" / "builtin" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: >-\n  演示技能\n  第二行\n---\n# Demo\n", encoding="utf-8")
    (tmp_path / "agents" / "workspace").mkdir(parents=True)
    (tmp_path / "agents" / "workspace" / "AGENTS.md").write_text("# Easel Agent\n", encoding="utf-8")
    (tmp_path / "outputs").mkdir()
    (tmp_path / "profiles").mkdir()
    (tmp_path / ".env").write_text("EASEL_DIRECT_API_KEY=secret\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("EASEL_DIRECT_API_KEY=\n", encoding="utf-8")
    return tmp_path


def context(root, answers=None, sandbox="workspace-write", events=None):
    asked = []

    async def ask(questions):
        asked.append(questions)
        if answers is None:
            raise HarnessError("no user")
        return answers

    ctx = ToolContext(root, ask, lambda kind, text: (events if events is not None else []).append((kind, text)),
                      sandbox=sandbox)
    ctx.asked = asked
    return ctx


def call(ctx, name, **args):
    return asyncio.run(execute(ctx, name, json.dumps(args, ensure_ascii=False)))


def test_reads_stay_inside_the_project_and_never_touch_credentials(root, tmp_path_factory):
    ctx = context(root)
    outside = tmp_path_factory.mktemp("outside") / "x.txt"
    outside.write_text("nope", encoding="utf-8")
    for path in (str(outside), "../x.txt", ".env", "outputs/../.env"):
        content, error = call(ctx, "read_file", path=path)
        assert error, path
    assert "secret" not in call(ctx, "grep", pattern="secret")[0]
    content, error = call(ctx, "read_file", path=".env.example")
    assert not error and "EASEL_DIRECT_API_KEY=" in content
    content, error = call(ctx, "read_file", path="skills/builtin/demo/SKILL.md", offset=2, limit=1)
    assert not error and content.startswith("     2|name: demo") and "offset=3" in content


def test_writes_are_limited_to_outputs_and_profiles(root):
    ctx = context(root)
    assert call(ctx, "write_file", path="skills/builtin/demo/SKILL.md", content="x")[1]
    assert call(ctx, "write_file", path="agents/workspace/AGENTS.md", content="x")[1]
    content, error = call(ctx, "write_file", path="outputs/项目/note.md", content="你好")
    assert not error and (root / "outputs" / "项目" / "note.md").read_text(encoding="utf-8") == "你好"
    readonly = context(root, sandbox="read-only")
    assert call(readonly, "write_file", path="outputs/a.md", content="x")[1]
    assert call(readonly, "run_command", command="echo hi")[1]


def test_edit_requires_a_unique_match_and_keeps_line_endings(root):
    ctx = context(root)
    target = root / "profiles" / "demo" / "memory.md"
    target.parent.mkdir()
    target.write_bytes(b"# memory\r\n- a\r\n- a\r\n")
    assert "2 次" in call(ctx, "edit_file", path="profiles/demo/memory.md", old_string="- a", new_string="- b")[0]
    assert call(ctx, "edit_file", path="profiles/demo/memory.md", old_string="missing", new_string="x")[1]
    content, error = call(ctx, "edit_file", path="profiles/demo/memory.md", old_string="# memory\n- a",
                          new_string="# memory\n- first")
    assert not error
    assert target.read_bytes() == b"# memory\r\n- first\r\n- a\r\n"


def test_search_and_listing(root):
    ctx = context(root)
    (root / ".venv").mkdir()
    (root / ".venv" / "hidden.md").write_text("演示技能", encoding="utf-8")
    hits = call(ctx, "grep", pattern="演示技能", glob="*.md")[0]
    assert "skills/builtin/demo/SKILL.md:" in hits and ".venv" not in hits
    assert "skills/builtin/demo/SKILL.md" in call(ctx, "glob", pattern="skills/builtin/*/SKILL.md")[0]
    assert "skills/" in call(ctx, "list_dir")[0]


def test_long_results_are_clipped_and_saved(root):
    ctx = context(root)
    big = root / "outputs" / "big.txt"
    big.write_text("\n".join("x" * 100 for _ in range(1500)), encoding="utf-8")
    content, error = call(ctx, "read_file", path="outputs/big.txt")
    assert not error and len(content) < 32_000 and "outputs/_agent/read-" in content


def test_commands_run_in_the_project_and_risky_ones_need_consent(root):
    ctx = context(root)
    content, error = call(ctx, "run_command", command="echo easel-ok")
    assert not error and content.startswith("[exit 0]") and "easel-ok" in content
    assert "不能直接查看 .env" in call(ctx, "run_command", command="cat .env")[0]
    content, error = call(ctx, "run_command", command="git push origin main")
    assert error and "未同意" in content and ctx.asked
    allowed = context(root, answers={"confirm": ["允许本次"]})
    content, error = call(allowed, "run_command", command="echo publish.py --dry-run")
    assert not error and allowed.asked


def test_tool_call_validation_and_questions(root):
    ctx = context(root, answers={"q1": ["小红书"]})
    assert call(ctx, "nope")[1]
    assert asyncio.run(execute(ctx, "read_file", "{broken"))[1]
    content, error = call(ctx, "ask_user", questions=[{"question": "发到哪个平台？", "header": "平台",
                                                       "options": ["小红书", "抖音"]}])
    assert not error and json.loads(content) == {"发到哪个平台？": ["小红书"]}
    card = ctx.asked[0][0]
    assert card == {"questionId": "q1", "question": "发到哪个平台？", "header": "平台",
                    "options": [{"label": "小红书"}, {"label": "抖音"}]}


def _sse(*events):
    return "".join(f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events) + "data: [DONE]\n\n"


def test_streamed_tool_calls_are_assembled(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text=_sse(
            {"choices": [{"delta": {"reasoning_content": "想一想"}}]},
            {"choices": [{"delta": {"content": "我先看看"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "read_", "arguments": '{"pa'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "file", "arguments": 'th":"a.md"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}))

    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    texts, thoughts = [], []
    result = asyncio.run(llm.complete(SETTINGS, [{"role": "user", "content": "hi"}], tools=tools.TOOL_SPECS,
                                      on_text=texts.append, on_thinking=thoughts.append))
    assert result.text == "我先看看" and result.reasoning == "想一想" and texts and thoughts
    assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [("call_1", "read_file", '{"path":"a.md"}')]
    assert seen["body"]["model"] == "demo-model" and seen["body"]["tools"] and seen["auth"] == "Bearer sk-test"


def test_transient_failures_retry_before_anything_streams(monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(200, text=_sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}))

    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    real_sleep = asyncio.sleep
    monkeypatch.setattr(llm.asyncio, "sleep", lambda _s: real_sleep(0))
    assert asyncio.run(llm.complete(SETTINGS, [{"role": "user", "content": "hi"}])).text == "ok"
    assert len(attempts) == 2


def scripted(monkeypatch, steps):
    calls = []

    async def fake_complete(settings, messages, *, model="", tools=None, on_text=None, on_thinking=None, **_):
        calls.append({"messages": messages, "tools": tools})
        step = steps.pop(0) if steps else llm.Completion(text="总结完毕")
        if on_text and step.text:
            on_text(step.text)
        return step

    monkeypatch.setattr(loop.llm, "complete", fake_complete)
    return calls


def tool_step(name, **args):
    return llm.Completion(tool_calls=[llm.ToolCall(f"call_{name}", name, json.dumps(args))], reasoning="思考")


def test_loop_runs_tools_until_the_model_answers(root, monkeypatch):
    events = []
    calls = scripted(monkeypatch, [tool_step("read_file", path="skills/builtin/demo/SKILL.md"),
                                   llm.Completion(text="读完了", finish_reason="stop")])
    saved = []
    history = []
    text = asyncio.run(loop.run_turn(settings=SETTINGS, model="", system="SYS", prompt="读技能", history=history,
                                     ctx=context(root, events=events), persist=lambda h: saved.append(len(h))))
    assert text == "读完了"
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]
    assert "name: demo" in history[2]["content"]
    assert history[1]["reasoning_content"] == "思考"
    assert calls[1]["messages"][0] == {"role": "system", "content": "SYS"}
    assert any(kind == "activity" and "read_file" in text for kind, text in events)
    assert saved == sorted(saved) and saved[-1] == 4


def test_repeated_identical_calls_stop_with_a_summary(root, monkeypatch):
    scripted(monkeypatch, [tool_step("list_dir", path=".") for _ in range(loop.REPEAT_STOP)])
    history = []
    with pytest.raises(HarnessError, match="没有进展"):
        asyncio.run(loop.run_turn(settings=SETTINGS, model="", system="S", prompt="go", history=history,
                                  ctx=context(root), persist=lambda h: None))
    tool_results = [m["content"] for m in history if m["role"] == "tool"]
    assert len(tool_results) == 5 and "[提醒]" in tool_results[2]
    assert history[-1] == {"role": "assistant", "content": "总结完毕"}


def test_step_limit_ends_the_turn(root, monkeypatch):
    scripted(monkeypatch, [tool_step("glob", pattern=f"*{i}") for i in range(5)])
    with pytest.raises(HarnessError, match="2 步上限"):
        asyncio.run(loop.run_turn(settings=SETTINGS, model="", system="S", prompt="go", history=[],
                                  ctx=context(root), persist=lambda h: None, max_steps=2))


def test_previous_turns_send_no_thinking_text():
    history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "x", "reasoning_content": "old"},
               {"role": "user", "content": "b"}, {"role": "assistant", "content": None, "reasoning_content": "new",
                                                  "tool_calls": []}]
    messages = loop.request_messages("S", history)
    assert "reasoning_content" not in messages[2] and messages[4]["reasoning_content"] == "new"


def test_stopped_turns_are_repaired_for_the_next_request():
    history = [{"role": "user", "content": "a"},
               {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}, {"id": "c2"}]},
               {"role": "tool", "tool_call_id": "c1", "content": "ok"}]
    repaired = agent_history.repair(history)
    assert repaired[-1]["role"] == "tool" and repaired[-1]["tool_call_id"] == "c2"


def test_long_histories_are_compacted_at_a_user_boundary(monkeypatch):
    scripted(monkeypatch, [llm.Completion(text="- 用户要做小红书卡片\n- 产物 outputs/a/card.png")])
    history = []
    for index in range(6):
        history += [{"role": "user", "content": f"问题{index} " + "长" * 800},
                    {"role": "assistant", "content": "答" * 800}]
    assert not asyncio.run(loop.compact(SETTINGS, "", list(history), budget=100_000))
    assert asyncio.run(loop.compact(SETTINGS, "", history, budget=3_000))
    assert history[0]["content"].startswith(loop.SUMMARY_HEADER) and "card.png" in history[0]["content"]
    assert history[2]["role"] == "user" and history[2]["content"].startswith("问题5")
    assert loop.estimate_tokens(history) < 3_000


def test_adapter_binds_a_session_and_continues_it(root, monkeypatch):
    scripted(monkeypatch, [llm.Completion(text="第一轮"), llm.Completion(text="第二轮")])
    adapter = ADAPTERS["easel"]
    bound, tokens = [], []

    def emit(kind, text):
        if kind == "token":
            tokens.append(text)

    async def ask(_questions):
        return {}

    request = RunRequest("你好", root, dict(SETTINGS), "", "", "workspace-write")
    asyncio.run(adapter.run(request, emit, ask, bound.append))
    assert len(bound) == 1
    session = bound[0]
    saved = root / "outputs" / "_sessions" / "agent" / f"{session}.json"
    assert json.loads(saved.read_text(encoding="utf-8"))[-1]["content"] == "第一轮"
    follow = RunRequest("继续", root, dict(SETTINGS), session, "", "workspace-write")
    asyncio.run(adapter.run(follow, emit, ask, bound.append))
    assert len(bound) == 1
    assert [m["content"] for m in json.loads(saved.read_text(encoding="utf-8"))] == ["你好", "第一轮", "继续", "第二轮"]
    assert "".join(tokens) == "第一轮第二轮"


def test_adapter_requires_the_model_api(root):
    request = RunRequest("你好", root, {}, "", "", "workspace-write")

    async def ask(_questions):
        return {}

    with pytest.raises(HarnessError, match="模型 API 未配置"):
        asyncio.run(ADAPTERS["easel"].run(request, lambda *_: None, ask, lambda _: None))


def test_system_prompt_lists_skills_and_tools(root):
    from easel.harnesses.builtin import system_prompt

    prompt = system_prompt(RunRequest("", root, {"EASEL_AGENT_RUNTIME": "easel"}, sandbox="workspace-write"))
    assert "# Easel Agent" in prompt and "run_command" in prompt
    assert "- demo（skills/builtin/demo/SKILL.md）：演示技能 第二行" in prompt
