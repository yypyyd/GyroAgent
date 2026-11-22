"""内置平台脚本共用的执行边界；不依赖工作台服务或浏览器。"""
import os
from pathlib import Path


HANDOFF = ("当前使用 Ego，脚本未执行平台操作。请在工作台「账号」手动打开官方页面登录或管理；"
           "发布请到「发布」审核稿件并亲自提交。不要改用其他工具重试或记录为已发布。")


def ego_enabled():
    key = "EASEL_BROWSER_BACKEND"
    value = os.environ.get(key)
    if value is None:
        root = Path(os.environ.get("EASEL_ROOT") or Path(__file__).resolve().parents[3])
        try:
            lines = (root / ".env").read_text(encoding="utf-8-sig").splitlines()
        except FileNotFoundError:
            lines = []
        value = ""
        for line in lines:
            name, separator, setting = line.partition("=")
            if separator and name.strip() == key:
                value = setting.strip().strip("\"'")
    return value.strip().lower() == "ego"


def require_legacy_platform():
    if ego_enabled():
        raise SystemExit(HANDOFF)
