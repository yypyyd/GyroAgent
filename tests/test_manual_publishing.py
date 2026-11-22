from __future__ import annotations

import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from easel.manual_publish import build_package, publishing_mode
from web import app as web


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "OUTPUTS_DIR", tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(web, "ENV_FILE", env_file)
    monkeypatch.delenv("EASEL_PUBLISH_MODE", raising=False)
    monkeypatch.delenv("EASEL_BROWSER_BACKEND", raising=False)
    def unexpected_publish(*args, **kwargs):
        pytest.fail("Manual publishing must never run a platform subprocess")
    monkeypatch.setattr(web.subprocess, "run", unexpected_publish)
    return TestClient(web.app, base_url="http://localhost:7860", headers={"Origin": "http://localhost:7860"})


@pytest.mark.parametrize("endpoint,payload", [
    ("/api/publish/xiaohongshu", {"title": "Test", "body": "Draft"}),
    ("/api/publish/douyin/sms", {"code": "123456"}),
])
def test_legacy_submission_is_blocked_before_any_platform_call(client, endpoint, payload):
    web.ENV_FILE.write_text("EASEL_PUBLISH_MODE=manual\n", encoding="utf-8")
    response = client.post(endpoint, json=payload)
    assert response.status_code == 403


def test_export_requires_explicit_review(client):
    response = client.post("/api/manual-publish/xiaohongshu/export", json={"title": "Draft"})
    assert response.status_code == 400


def test_export_does_not_mark_content_as_published(client, tmp_path):
    media = tmp_path / "my-project" / "cover.png"
    media.parent.mkdir()
    media.write_bytes(b"example-image")
    response = client.post("/api/manual-publish/xiaohongshu/export", json={
        "title": "人工审核稿", "body": "第一段\n\n第二段", "tags": "工具,日常",
        "media": ["my-project/cover.png"], "reviewed": True,
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["published"] is False
    with zipfile.ZipFile(tmp_path / payload["path"]) as archive:
        assert archive.read("title.txt").decode() == "人工审核稿"
        assert archive.read("media/01_cover.png") == b"example-image"
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["status"] == "ready_for_manual_submission"
        assert "cookie" not in " ".join(archive.namelist()).lower()
    assert not (tmp_path / "_schedule.json").exists()


@pytest.mark.parametrize('platform',['juejin','twitter-x'])
def test_new_channels_support_manual_export_without_platform_calls(client,tmp_path,platform):
    web.ENV_FILE.write_text('EASEL_PUBLISH_MODE=manual\n',encoding='utf-8')
    assert client.post(f'/api/publish/{platform}',json={'title':'test','body':'draft'}).status_code==403
    response=client.post(f'/api/manual-publish/{platform}/export',json={'title':'test','body':'draft','reviewed':True})
    assert response.status_code==200,response.text
    assert response.json()['published'] is False
    with zipfile.ZipFile(tmp_path/response.json()['path']) as archive:
        meta=json.loads(archive.read('manifest.json'))
        assert meta['platform']==platform and meta['status']=='ready_for_manual_submission'
    assert not (tmp_path/'_schedule.json').exists()


@pytest.mark.parametrize("relative", ["../outside.png", "_login/qr.png", "project/secret.json", "project/.hidden.png"])
def test_export_rejects_system_files_and_path_escape(tmp_path, relative):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    path = (outputs / relative).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"private-data")
    with pytest.raises(ValueError):
        build_package(outputs, "xiaohongshu", "Title", "Body", "", [relative], True)
    assert not (outputs / "_publish").exists()


def test_wechat_html_is_formatted_without_active_content(tmp_path):
    package = build_package(tmp_path, "wechat-oa", "标题", (
        "## 小标题\n\n正文 <script>alert(1)</script>\n\n"
        "[链接](javascript:alert(1))\n\n![远程图](https://example.org/private.png)"), "", [], True)
    with zipfile.ZipFile(package) as archive:
        article = archive.read("article.html").decode()
    assert "<h2>小标题</h2>" in article
    assert "<script>" not in article
    assert "<img" not in article
    assert "href=" not in article


def test_policy_reports_manual_mode(client):
    web.ENV_FILE.write_text("EASEL_PUBLISH_MODE=manual\n", encoding="utf-8")
    assert client.get("/api/publishing-policy").json() == {"mode": "manual", "automaticSubmission": False}


def test_policy_preserves_automatic_default(client):
    assert client.get("/api/publishing-policy").json() == {"mode": "automated", "automaticSubmission": True}
    assert web._publishing_message("draft") == "draft"


@pytest.mark.parametrize("value", ["manual", " MANUAL ", "invalid", "true"])
def test_manual_or_unknown_configuration_disables_automatic_submission(value):
    assert publishing_mode({"EASEL_PUBLISH_MODE": value}) == "manual"


@pytest.mark.parametrize("settings", [{}, {"EASEL_PUBLISH_MODE": ""}, {"EASEL_PUBLISH_MODE": " automated "}])
def test_missing_empty_or_automatic_configuration_preserves_existing_mode(settings):
    assert publishing_mode(settings) == "automated"


@pytest.mark.parametrize("configuration", ["", "EASEL_PUBLISH_MODE=automated\n"])
def test_default_and_configured_automatic_workflow_use_existing_publisher(client, monkeypatch, configuration):
    web.ENV_FILE.write_text(configuration, encoding="utf-8")
    calls = []
    def fake_publish(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="mocked platform failure")
    monkeypatch.setattr(web.subprocess, "run", fake_publish)
    assert client.get("/api/publishing-policy").json() == {"mode": "automated", "automaticSubmission": True}
    response = client.post("/api/publish/zhihu", json={"title": "Test", "body": "Draft"})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is False
    assert len(calls) == 1 and "--exec" in calls[0]


def test_manual_policy_is_included_in_agent_messages(client):
    web.ENV_FILE.write_text("EASEL_PUBLISH_MODE=manual\n", encoding="utf-8")
    message = web._chat_message(web.ChatRequest(message="排版这个草稿"))
    assert "人工提交" in message and "不要调用发布脚本" in message
    assert "排版这个草稿" in message
    web.ENV_FILE.write_text("EASEL_PUBLISH_MODE=automated\n", encoding="utf-8")
    assert web._publishing_message("draft") == "draft"


def test_environment_can_override_mode_in_dotenv(client, monkeypatch):
    web.ENV_FILE.write_text("EASEL_PUBLISH_MODE=automated\n", encoding="utf-8")
    monkeypatch.setenv("EASEL_PUBLISH_MODE", "manual")
    assert client.post("/api/publish/zhihu", json={"title": "Draft"}).status_code == 403


def test_backend_rechecks_policy_after_automatic_page_was_loaded(client):
    assert client.get("/api/publishing-policy").json()["automaticSubmission"] is True
    web.ENV_FILE.write_text("EASEL_PUBLISH_MODE=manual\n", encoding="utf-8")
    assert client.post("/api/publish/zhihu", json={"title": "Draft"}).status_code == 403
    assert client.post("/api/publish/douyin/sms", json={"code": "123456"}).status_code == 403


def test_export_is_available_with_automatic_policy_without_changing_it(client):
    response = client.post("/api/manual-publish/xiaohongshu/export", json={
        "title": "人工审核稿", "reviewed": True,
    })
    assert response.status_code == 200
    assert response.json()["published"] is False
    assert client.get("/api/publishing-policy").json() == {"mode": "automated", "automaticSubmission": True}
    assert web.ENV_FILE.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("method,endpoint,payload", [
    ("get", "/api/accounts", None),
    ("post", "/api/login/douyin?restart=true", {}),
    ("get", "/api/login/douyin/status", None),
    ("post", "/api/login/douyin/sms", {"code": "123456"}),
    ("get", "/api/accounts/wechat-oa/credentials", None),
    ("post", "/api/accounts/wechat-oa/credentials", {"appId": "test", "appSecret": "test"}),
    ("post", "/api/accounts/wechat-oa/mp-login", {}),
    ("get", "/api/accounts/wechat-oa/mp-login/status", None),
    ("get", "/api/accounts/zhihu/whoami?force=true", None),
    ("post", "/api/logout/douyin", {}),
    ("get", "/api/analytics/platforms", None),
    ("get", "/api/analytics/douyin", None),
    ("post", "/api/publish/douyin", {"title": "草稿"}),
    ("post", "/api/publish/douyin/sms", {"code": "123456"}),
    ("get", "/api/publish/douyin/status", None),
    ("post", "/api/skill", {"skill": "publish-scheduler", "input": "查看到期项"}),
    ("post", "/api/skill", {"skill": "skill-xhs-publisher", "input": "草稿"}),
])
def test_ego_blocks_legacy_routes_before_side_effects(client, tmp_path, monkeypatch, method, endpoint, payload):
    web.ENV_FILE.write_text("EASEL_BROWSER_BACKEND=ego\nEASEL_PUBLISH_MODE=automated\n", encoding="utf-8")
    def unexpected(*args, **kwargs):
        pytest.fail("Ego 模式下不应启动旧进程、读取旧登录状态或调用旧凭证服务")
    monkeypatch.setattr(web.subprocess, "Popen", unexpected)
    monkeypatch.setattr(web, "_account_logged_in", unexpected)
    monkeypatch.setattr(web, "_wechat_web_account", unexpected)
    monkeypatch.setattr(web, "_wechat_save_credentials", unexpected)
    monkeypatch.setattr(web, "run_agent_sync", unexpected)
    for key in ("LOGIN_DIR", "PUBLISH_DIR", "BROWSER_PROFILES"):
        monkeypatch.setattr(web, key, tmp_path / key)
    marker = web.LOGIN_DIR / "douyin.json"
    marker.parent.mkdir()
    marker.write_text('{"state":"success"}', encoding="utf-8")
    response = client.request(method, endpoint, json=payload)
    assert response.status_code in {403, 409} and "Ego" in response.json()["detail"]
    assert marker.read_text(encoding="utf-8") == '{"state":"success"}'
    assert not (web.LOGIN_DIR / 'douyin.code').exists() and not web.PUBLISH_DIR.exists()


def test_ego_configuration_and_calendar_are_independent(client, tmp_path, monkeypatch):
    web.ENV_FILE.write_text("EASEL_BROWSER_BACKEND=legacy\nEASEL_PUBLISH_MODE=automated\n", encoding="utf-8")
    monkeypatch.setenv("EASEL_BROWSER_BACKEND", " EGO ")
    schedule = tmp_path / "_schedule.json"
    original = '[{"id":"a","date":"2000-01-01","title":"旧排期","status":"scheduled"}]'
    schedule.write_text(original, encoding="utf-8")
    monkeypatch.setattr(web, "SCHEDULE_FILE", schedule)
    assert client.get("/api/publishing-policy").json() == {"mode": "manual", "automaticSubmission": False}
    assert "Ego" in web._publishing_message("草稿")
    assert client.get("/api/schedule").json()[0]["status"] == "scheduled"
    assert client.post("/api/manual-publish/zhihu/export", json={"title": "草稿", "reviewed": True}).json()["published"] is False
    status = tmp_path / "publish-status.json"
    web._run_publish_bg('douyin', [], '', '', {}, status, tmp_path / "code")
    assert json.loads(status.read_text(encoding="utf-8"))["state"] == "error"
    assert schedule.read_text(encoding="utf-8") == original


def test_unknown_platform_is_rejected_without_creating_package(client, tmp_path):
    response = client.post("/api/manual-publish/unknown/export", json={"title": "Draft", "reviewed": True})
    assert response.status_code == 400
    assert not (tmp_path / "_publish").exists()
