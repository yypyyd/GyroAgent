"""Local, user-initiated bridge to Ego. No credentials, arbitrary URLs or shell input."""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from easel.direct_api import read_settings

router = APIRouter(prefix="/api/ego")
_lock = threading.Lock()
_SCRIPT = Path(__file__).with_name("ego_workspace.mjs")
_STATE = _SCRIPT.parents[1] / "outputs" / "_ego" / "spaces.json"


def _save_state(state):
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(_STATE)

# Fixed official destinations, corresponding to the workbench's ten platform IDs.
# Login via the official dashboard also preserves an already authenticated session.
PLATFORMS = {
    "xiaohongshu": ("https://creator.xiaohongshu.com/", "https://creator.xiaohongshu.com/publish/publish?source=official"),
    "douyin": ("https://creator.douyin.com/creator-micro/home", "https://creator.douyin.com/creator-micro/content/upload"),
    "kuaishou": ("https://cp.kuaishou.com/profile", "https://cp.kuaishou.com/article/publish/video"),
    "weixin-channels": ("https://channels.weixin.qq.com/platform", "https://channels.weixin.qq.com/platform/post/create"),
    "zhihu": ("https://www.zhihu.com/creator/analytics", "https://zhuanlan.zhihu.com/write"),
    "juejin": ("https://juejin.cn/creator", "https://juejin.cn/editor/drafts/new?v=2"),
    "toutiao": ("https://mp.toutiao.com/profile_v4/index", "https://mp.toutiao.com/profile_v4/graphic/publish"),
    "twitter-x": ("https://x.com/home", "https://x.com/compose/post"),
    "bilibili": ("https://member.bilibili.com/platform/upload-manager/article", "https://member.bilibili.com/platform/upload/video/frame"),
    "wechat-oa": ("https://mp.weixin.qq.com/", "https://mp.weixin.qq.com/"),
}


class Platform(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: str = Field(max_length=32)


class Open(Platform):
    action: Literal["login", "manage", "publish"]


class Fill(Platform):
    text: str = Field(min_length=1, max_length=50000)
    reviewed: StrictBool


class AutoPublish(Platform):
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=50000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    media: list[str] = Field(default_factory=list, max_length=10)
    category: str = Field(default="", max_length=30)
    reviewed: StrictBool


# 允许 Ego 全自动发布的平台（driver 在 ego_workspace.mjs 的 PUBLISH_DRIVERS 中）
AUTO_PUBLISH_PLATFORMS = {"juejin", "toutiao", "zhihu", "xiaohongshu", "wechat-oa"}
_OUTPUTS = _SCRIPT.parents[1] / "outputs"


def _resolve_media(media: list[str]) -> list[str]:
    from easel.manual_publish import MEDIA_EXTENSIONS
    root = _OUTPUTS.resolve()
    files = []
    for relative in dict.fromkeys(media):
        path = (root / relative).resolve()
        if root not in path.parents:
            raise HTTPException(400, "媒体路径超出内容库")
        if any(part.startswith(("_", ".")) for part in path.relative_to(root).parts):
            raise HTTPException(400, "不能发布系统目录或隐藏文件")
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
            raise HTTPException(400, "只能发布内容库中的图片或视频")
        files.append(path)
    return [str(p).replace("\\", "/") for p in files[:3]]


def enabled():
    return read_settings().get("EASEL_BROWSER_BACKEND", "").strip().lower() == "ego"


_STATES = {"verified", "login_required", "restricted", "unknown"}
# One Ego window for every platform; each open adds a tab, not a new space.
WORKSPACE_NAME = "Easel"


def _account_name(value) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c.isprintable()).strip()[:40]


def _guard(request):
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Ego 操作仅允许从本机工作台发起")
    if not enabled():
        raise HTTPException(409, "当前工作台没有启用 Ego")
    exe = shutil.which("ego-browser.exe" if os.name == "nt" else "ego-browser")
    if not exe:
        raise HTTPException(503, "未找到 ego-browser，请安装或检查工作台进程的 PATH")
    return exe


def _read_state() -> dict:
    state = json.loads(_STATE.read_text(encoding="utf-8")) if _STATE.is_file() else {}
    if not isinstance(state, dict):
        raise ValueError("invalid space registry")
    return state


def _workspace(state: dict) -> dict:
    ws = state.get("_workspace")
    if isinstance(ws, dict) and isinstance(ws.get("name"), str) and ws["name"]:
        return ws
    for key, entry in state.items():
        if key in PLATFORMS and isinstance(entry, dict) and isinstance(entry.get("name"), str):
            ws = {"name": WORKSPACE_NAME, "id": entry.get("id") if type(entry.get("id")) is int else None}
            state["_workspace"] = ws
            return ws
    ws = {"name": WORKSPACE_NAME, "id": None}
    state["_workspace"] = ws
    return ws


def _invoke(exe, function, options, timeout):
    # Source on stdin, not a command line. JSON values never become executable code.
    script = (f"const {{ {function} }} = await import({json.dumps(_SCRIPT.as_uri())});\n"
              f"const result = await {function}({json.dumps(options, ensure_ascii=True)});\n"
              "console.log('EASEL_EGO_RESULT ' + JSON.stringify(result));\n")
    proc = subprocess.run([exe, "nodejs"], input=script, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False, shell=False)
    # Ego writes console.log to stderr on Windows. Never return/log raw CLI output.
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        if proc.returncode == 0 and line.startswith("EASEL_EGO_RESULT "):
            return json.loads(line.partition(" ")[2])
    raise HTTPException(503, "Ego 未完成操作；请查看 Ego 窗口是否显示授权提示。未切换浏览器或重试。")


def _bridge(request, platform, action, **fields):
    exe = _guard(request)
    if platform not in PLATFORMS:
        raise HTTPException(422, "此平台不在工作台支持列表中")
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "上一次 Ego 操作尚未结束，请稍候")
    try:
        state = _read_state()
        workspace = _workspace(state)
        if "_workspace" not in state:
            state["_workspace"] = workspace
        _save_state(state)
        options = dict(platform=platform, action=action, urls=PLATFORMS[platform],
                       spaceName=workspace["name"], spaceId=workspace.get("id"), **fields)
        result = _invoke(exe, "run", options, 55)
        space_id = result.pop("spaceId", None)
        if type(space_id) is int and space_id != workspace.get("id"):
            workspace["id"] = space_id
            _save_state(state)
        if not result.get("ok"):
            raise HTTPException(409, result["message"])
        if "name" in result:
            result["name"] = _account_name(result["name"])
            if not result["name"]:
                del result["name"]
        return result
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Ego 未及时响应，请先查看当前窗口，不要重复点击。") from None
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(503, "无法启动 Ego 或读取其结果，请检查本机 Ego。") from None
    finally:
        _lock.release()


def _accounts(request):
    exe = _guard(request)
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "上一次 Ego 操作尚未结束，请稍候")
    try:
        state = _read_state()
        migrated = "_workspace" not in state
        workspace = _workspace(state)
        if migrated:
            _save_state(state)
        # Logins live in the Ego profile, so every platform is checked, opened before or not.
        options = {"spaceName": workspace["name"], "spaceId": workspace.get("id"),
                   "platforms": {key: {"urls": PLATFORMS[key]} for key in PLATFORMS}}
        result = _invoke(exe, "status", options, 150)
        found = result.get("platforms") if isinstance(result.get("platforms"), dict) else {}
        space_id = result.get("spaceId")
        if type(space_id) is int and space_id != workspace.get("id"):
            workspace["id"] = space_id
            state["_workspace"] = workspace
            _save_state(state)
        accounts = {}
        for key in PLATFORMS:
            item = found.get(key) if isinstance(found.get(key), dict) else {}
            account = {"exists": item.get("exists") is True,
                       "state": item.get("state") if item.get("state") in _STATES else "unknown"}
            name = _account_name(item.get("name"))
            if name and account["state"] == "verified":
                account["name"] = name
            accounts[key] = account
        return {"platforms": accounts}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Ego 未及时响应，请稍后刷新。") from None
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(503, "无法启动 Ego 或读取其结果，请检查本机 Ego。") from None
    finally:
        _lock.release()


@router.get("/status")
def status():
    return {"enabled": enabled(), "platforms": list(PLATFORMS)}


@router.post("/open")
def open_official(body: Open, request: Request):
    return _bridge(request, body.platform, body.action)


@router.post("/accounts")
def accounts(request: Request):
    return _accounts(request)


@router.post("/verify")
def verify(body: Platform, request: Request):
    return _bridge(request, body.platform, "verify")


@router.post("/fill")
def fill(body: Fill, request: Request):
    if not body.reviewed or not body.text.strip():
        raise HTTPException(400, "请先审核非空稿件，再填入官方编辑框")
    return _bridge(request, body.platform, "fill", text=body.text)


@router.post("/publish")
def auto_publish(body: AutoPublish, request: Request):
    if not body.reviewed:
        raise HTTPException(400, "请先完成人工审核再自动发布")
    if not (body.title.strip() or body.body.strip()):
        raise HTTPException(400, "标题和正文不能同时为空")
    if body.platform not in AUTO_PUBLISH_PLATFORMS:
        raise HTTPException(400, f"{body.platform} 暂不支持 Ego 自动发布")
    exe = _guard(request)
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "上一次 Ego 操作尚未结束，请稍候")
    try:
        state = _read_state()
        workspace = _workspace(state)
        if "_workspace" not in state:
            state["_workspace"] = workspace
            _save_state(state)
        media = _resolve_media(body.media)
        options = dict(platform=body.platform, action="publish", urls=PLATFORMS[body.platform],
                       spaceName=workspace["name"], spaceId=workspace.get("id"),
                       payload={"title": body.title, "body": body.body,
                                "tags": [t.strip() for t in body.tags if t.strip()][:10],
                                "media": media, "category": body.category.strip()})
        result = _invoke(exe, "publish", options, 240)
        if isinstance(result, dict) and result.get("spaceId") is not None:
            sid = result.pop("spaceId")
            if type(sid) is int and sid != workspace.get("id"):
                workspace["id"] = sid
                _save_state(state)
        return result
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Ego 自动发布超时，请查看官方页面确认是否已提交") from None
    except HTTPException:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(503, "无法启动 Ego 或读取其结果，请检查本机 Ego。") from None
    finally:
        _lock.release()
