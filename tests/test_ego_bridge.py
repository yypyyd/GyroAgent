import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from easel import ego_browser as ego


@pytest.fixture
def setup(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(ego.router)
    monkeypatch.setattr(ego, 'enabled', lambda: True)
    monkeypatch.setattr(ego, '_STATE', tmp_path / 'spaces.json')
    monkeypatch.setattr(ego.shutil, 'which', lambda _: 'ego-browser.exe')
    spawn = Mock(return_value=SimpleNamespace(returncode=0, stdout='', stderr='EASEL_EGO_RESULT {"ok":true,"spaceId":8,"message":"ok"}\n'))
    monkeypatch.setattr(ego.subprocess, 'run', spawn)
    return app, TestClient(app, client=('127.0.0.1', 8123)), spawn


@pytest.mark.parametrize('action', ['login', 'manage', 'publish'])
def test_all_routes(setup, action):
    _, client, spawn = setup
    assert set(client.get('/api/ego/status').json()['platforms']) == set(ego.PLATFORMS)
    assert len(ego.PLATFORMS) == 10
    for key in ego.PLATFORMS:
        data = client.post('/api/ego/open', json={'action':action,'platform':key}).json()
        assert data == {'ok':True,'message':'ok'}
        assert spawn.call_args.args[0] == ['ego-browser.exe', 'nodejs']
        assert not spawn.call_args.kwargs['shell']
    saved = json.loads(ego._STATE.read_text())
    assert saved['_workspace'] == {'name': 'Easel', 'id': 8}


@pytest.mark.parametrize('route,payload', [('open', {'platform':'custom','action':'login'}), ('fill', {'platform':'douyin','text':'body','reviewed':False}), ('open', {'platform':'douyin','action':'delete'}), ('open', {'platform':'douyin','action':'login','url':'https://example.org'}), ('fill', {'platform':'zhihu','text':'   ','reviewed':True})])
def test_no_arbitrary_actions(setup, route, payload):
    _, client, spawn = setup
    assert client.post('/api/ego/'+route,json=payload).status_code in {400,422}
    spawn.assert_not_called()


def test_disabled_remote_and_missing(setup,monkeypatch):
    app, client, spawn = setup
    data = {'platform':'douyin','action':'login'}
    assert TestClient(app,client=('192.0.2.1',80)).post('/api/ego/open',json=data).status_code == 403
    monkeypatch.setattr(ego,'enabled',lambda:False)
    assert client.post('/api/ego/open',json=data).status_code == 409
    monkeypatch.setattr(ego,'enabled',lambda:True)
    monkeypatch.setattr(ego.shutil,'which',lambda _:None)
    assert client.post('/api/ego/open',json=data).status_code == 503
    spawn.assert_not_called()


def test_reuse_space_and_fill_without_argv_text(setup):
    _, client, spawn = setup
    client.post('/api/ego/open',json={'platform':'juejin','action':'publish'}).raise_for_status()
    text = 'draft "quote"\n测试'
    client.post('/api/ego/fill',json={'platform':'juejin','text':text,'reviewed':True}).raise_for_status()
    assert text not in str(spawn.call_args.args)
    arg = spawn.call_args.kwargs['input'].split('const result = await run(')[1].split(');\n')[0]
    assert json.loads(arg)['text'] == text
    assert json.loads(arg)['spaceId'] == 8
    assert text not in ego._STATE.read_text()


def test_accounts_checks_every_platform_even_if_never_opened(setup):
    _, client, spawn = setup
    data = client.post('/api/ego/accounts').json()['platforms']
    assert set(data) == set(ego.PLATFORMS)
    assert all(item == {'exists': False, 'state': 'unknown'} for item in data.values())
    options = json.loads(spawn.call_args.kwargs['input'].split('await status(')[1].split(');\n')[0])
    assert set(options['platforms']) == set(ego.PLATFORMS)
    assert json.loads(ego._STATE.read_text())['_workspace'] == {'name': 'Easel', 'id': 8}


def test_accounts_reports_state_and_refreshes_space_ids(setup):
    _, client, spawn = setup
    ego._STATE.write_text(json.dumps({'juejin': {'name': 'Easel-juejin-abc', 'id': 3},
                                      'zhihu': {'name': 'Easel-zhihu-def', 'id': 4}}))
    spawn.return_value.stderr = 'EASEL_EGO_RESULT ' + json.dumps({'ok': True, 'spaceId': 13, 'platforms': {
        'juejin': {'exists': True, 'state': 'verified', 'name': '默笙呀\u0007' + 'x' * 60, 'spaceId': 13},
        'zhihu': {'exists': False, 'state': 'unknown', 'spaceId': 13},
    }})
    data = client.post('/api/ego/accounts').json()['platforms']
    assert data['juejin'] == {'exists': True, 'state': 'verified', 'name': ('默笙呀' + 'x' * 60)[:40]}
    assert data['zhihu'] == {'exists': False, 'state': 'unknown'}
    assert data['bilibili'] == {'exists': False, 'state': 'unknown'}
    script = spawn.call_args.kwargs['input']
    assert 'await status(' in script and '"spaceName": "Easel"' in script
    assert spawn.call_args.args[0] == ['ego-browser.exe', 'nodejs']
    assert json.loads(ego._STATE.read_text())['_workspace']['id'] == 13


def test_open_passes_account_name(setup):
    _, client, spawn = setup
    spawn.return_value.stderr = 'EASEL_EGO_RESULT {"ok":true,"spaceId":8,"state":"verified","name":" demo ","message":"ok"}'
    data = client.post('/api/ego/open', json={'platform': 'juejin', 'action': 'login'}).json()
    assert data == {'ok': True, 'state': 'verified', 'name': 'demo', 'message': 'ok'}


def test_restriction_and_no_retry(setup):
    _, client, spawn = setup
    spawn.return_value.stderr='EASEL_EGO_RESULT {"ok":false,"spaceId":8,"message":"平台要求验证"}'
    assert client.post('/api/ego/open',json={'platform':'douyin','action':'login'}).status_code == 409
    assert spawn.call_count == 1
    assert json.loads(ego._STATE.read_text())['_workspace']['id'] == 8
