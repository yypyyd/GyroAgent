"""在隔离目录执行真正的 CLI 入口；禁止子进程、联网和访问真实账号。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "skills/shared/scripts"
BUILTIN = ROOT / "skills/builtin"
WECHAT = BUILTIN / "skill-wechat-publisher/scripts"
RUN = '''
import os, runpy, sys
from pathlib import Path
def audit(event, args):
    if event in {"subprocess.Popen", "os.system", "os.startfile", "socket.connect", "socket.getaddrinfo"}:
        raise AssertionError(event)
sys.addaudithook(audit)
sys.argv = sys.argv[1:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
runpy.run_path(sys.argv[0], run_name="__main__")
'''


def run(tmp_path, script, args, backend=" Ego ", stdin=None):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
           "HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "EASEL_ROOT": str(ROOT)}
    if backend is None:
        env.pop("EASEL_BROWSER_BACKEND", None)
        env["EASEL_ROOT"] = str(tmp_path)
    else:
        env["EASEL_BROWSER_BACKEND"] = backend
    return subprocess.run([sys.executable, "-c", RUN, str(script), *args], input=stdin, cwd=tmp_path,
                          env=env, capture_output=True, encoding="utf-8", timeout=20)


@pytest.mark.parametrize("script,args", [
    *((SHARED / "web_publisher.py", ["publish", "--platform", platform, "--exec"])
      for platform in ("zhihu", "juejin", "kuaishou", "weixin-channels", "twitter-x")),
    (SHARED / "web_publisher.py", ["login-qr", "--platform", "juejin"]),
    (SHARED / "xhs_publish.py", ["publish", "--exec"]),
    (SHARED / "douyin_publish.py", ["publish-video", "--exec"]),
    (SHARED / "weixin_mp_stats.py", ["publish", "--html", "a", "--cover", "a", "--title", "a"]),
    (BUILTIN / "skill-bilibili-upload/scripts/bili_upload.py", ["upload", "--video", "a", "--exec"]),
    (BUILTIN / "skill-toutiao-publisher/scripts/toutiao_publish.py", ["--exec", "--title", "a", "--content", "a"]),
    (WECHAT / "publish.py", ["--input", "a.md", "--exec"]),
    (WECHAT / "wechat_api.py", ["token"]),
    (WECHAT / "multi_publish.py", ["--input", "a.md", "--platforms", "zhihu"]),
    (SHARED / "account_stats.py", ["fetch", "--platform", "douyin"]),
    (SHARED / "bili_login.py", ["login"]), (SHARED / "x_stats.py", ["fetch"]),
    (SHARED / "juejin_stats.py", ["fetch"]), (SHARED / "xhs_comment.py", ["post", "--exec"]),
    (SHARED / "zhihu_answer.py", ["--question", "offline", "--content-file", "none", "--exec"]),
    (SHARED / "zhihu_comments_fetch.py", ["offline"]),
    (BUILTIN / "skill-publish-scheduler/scripts/publish_queue.py", ["run", "--queue", "q.json", "--exec"]),
])
def test_block_before_platform_io(tmp_path, script, args):
    result = run(tmp_path, script, args)
    assert result.returncode != 0
    assert "当前使用 Ego" in result.stderr, result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("script,args,output", [
    (WECHAT / "html_converter.py", ["a.md"], "测试正文"),
    (WECHAT / "publish.py", ["--input", "a.md"], "仅本地预览"),
    (BUILTIN / "skill-bilibili-upload/scripts/bili_upload.py", ["upload", "--video", "a.mp4"], "仅本地预览"),
    (BUILTIN / "skill-publish-scheduler/scripts/publish_queue.py", ["run", "--queue", "q.json"], "仅预览排期"),
])
def test_ego_local_preparation(tmp_path, script, args, output):
    (tmp_path / "a.md").write_text("# 标题\n\n测试正文", encoding="utf-8")
    (tmp_path / "a.mp4").touch()
    queue = tmp_path / "q.json"
    original = '{"items":[{"status":"pending","publish_at":"2020-01-01","content":"a","platform":"bilibili"}]}'
    queue.write_text(original, encoding="utf-8")
    result = run(tmp_path, script, args)
    assert result.returncode == 0, result.stderr
    assert output in result.stdout
    assert queue.read_text(encoding="utf-8") == original


def test_unexported_deployment_setting(tmp_path):
    (tmp_path / ".env").write_text('EASEL_BROWSER_BACKEND="EGO"\n', encoding="utf-8-sig")
    result = run(tmp_path, SHARED / "juejin_stats.py", ["fetch"], backend=None)
    assert result.returncode != 0 and "当前使用 Ego" in result.stderr


def test_skill_cli_never_delegates_an_ego_publish(monkeypatch, capsys):
    from types import SimpleNamespace
    from easel import harnesses
    from easel.commands import skill

    monkeypatch.setattr(skill, "read_settings", lambda: {"EASEL_BROWSER_BACKEND": " EGO "})
    monkeypatch.setattr(harnesses, "settings", lambda *_: {"runtime": "openclaw"})
    monkeypatch.setattr(skill, "_run_via_openclaw", lambda *_args, **_kwargs: pytest.fail("启动了外部 Agent"))
    assert skill.cmd_skill(SimpleNamespace(name="wechat-publisher", input="test", profile=None)) != 0
    assert "未启动 Agent" in capsys.readouterr().err


@pytest.mark.parametrize("backend,publisher", [("ego", None), ("legacy", "skill-douyin-upload")])
def test_planning_remains_offline(tmp_path, backend, publisher):
    result = run(tmp_path, BUILTIN / "skill-cross-platform-publish/scripts/publish_dispatch.py",
                 ["plan", "--manifest", "-"], backend, '{"platforms":["douyin"]}')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["dispatch"][0]["publisher"] == publisher
