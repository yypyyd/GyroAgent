"""Browser engine for headless HTML rendering (cards, slides, PDFs, video frames).

On Windows the system Microsoft Edge is used through Playwright's ``msedge`` channel,
so no separate Chromium download is needed. Other systems keep Playwright's bundled
Chromium. ``EASEL_RENDER_BROWSER`` overrides the choice: ``msedge``, ``chrome``,
``chromium`` (Playwright's bundled build) or an absolute path to a Chromium-based
executable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

CHANNELS = {"msedge", "msedge-beta", "msedge-dev", "chrome", "chrome-beta", "chrome-dev"}

RENDER_HINT = ("需要本机 Microsoft Edge（Windows 自带）；或设置 EASEL_RENDER_BROWSER 为 "
               "chrome / chromium / 浏览器可执行文件路径")


def render_choice() -> str:
    choice = os.environ.get("EASEL_RENDER_BROWSER", "").strip()
    if choice:
        return choice
    return "msedge" if sys.platform == "win32" else "chromium"


def render_launch_options() -> dict:
    """Playwright ``chromium.launch`` keyword arguments selecting the render browser."""
    choice = render_choice()
    if choice == "chromium":
        return {}
    if choice in CHANNELS:
        return {"channel": choice}
    return {"executable_path": str(Path(choice).expanduser())}


def render_channel() -> str:
    """Channel name for Node renderers that read ``BROWSER_CHANNEL``; empty means bundled."""
    choice = render_choice()
    return choice if choice in CHANNELS else ""


_WINDOWS_PATHS = {
    "msedge": ("Microsoft", "Edge", "Application", "msedge.exe"),
    "chrome": ("Google", "Chrome", "Application", "chrome.exe"),
}


def _windows_candidates(channel: str) -> list[Path]:
    roots = [os.environ.get(name, "") for name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    return [Path(root).joinpath(*_WINDOWS_PATHS[channel]) for root in roots if root]


def render_executable() -> Path | None:
    """The executable the current choice resolves to, when it can be located without launching."""
    choice = render_choice()
    if choice in _WINDOWS_PATHS and sys.platform == "win32":
        return next((path for path in _windows_candidates(choice) if path.is_file()), None)
    if choice in CHANNELS:
        return None
    if choice == "chromium":
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                path = Path(playwright.chromium.executable_path)
        except (ImportError, OSError, RuntimeError):
            return None
        return path if path.is_file() else None
    path = Path(choice).expanduser()
    return path if path.is_file() else None
