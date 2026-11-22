import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_web_outputs(request, tmp_path_factory, monkeypatch):
    if not request.node.path.is_relative_to(Path(__file__).parent / "tests"):
        return
    outputs = tmp_path_factory.mktemp("isolated-web-outputs")
    for name in ("app", "web.app"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "OUTPUTS_DIR"):
            monkeypatch.setattr(module, "OUTPUTS_DIR", outputs)


@pytest.fixture(autouse=True)
def isolate_browser_backend(request, monkeypatch):
    # 项目 .env 可能把 EASEL_BROWSER_BACKEND 设为 ego；绝大多数测试覆盖的是
    # legacy 浏览器路径，必须显式压住，避免开发机配置污染测试结果。
    # 需要 ego 行为的测试自行 monkeypatch.setenv / monkeypatch 模块函数。
    if request.node.path.is_relative_to(Path(__file__).parent / "tests"):
        monkeypatch.setenv("EASEL_BROWSER_BACKEND", "legacy")
