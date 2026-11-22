#!/usr/bin/env python3
"""capture.py — 按教程计划操作真实产品并截图（Playwright + Chromium）。

每一步：执行动作 → 打码 → 高亮目标元素 → 截全图（可选裁切图）→ 记录高亮框坐标。
终端步骤把真实命令输出渲染成终端截图。结果写入 capture-report.json，供 build_doc / build_video 使用。

用法：
    capture.py --plan outputs/<主题>/assets/plan.json            # 默认输出到 plan 同级 shots/
    capture.py --plan ... --out-dir outputs/<主题>/assets/shots --record   # 同时录制整段操作
    capture.py --selftest
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared" / "scripts"))
from output_paths import validate_output_path  # noqa: E402
from render_browser import render_launch_options  # noqa: E402

ACCENT = "#ff5a36"
CROP_PAD = 160
CROP_MIN = (720, 405)
CONTAINER_PAD = 28
LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none"]

HIGHLIGHT_JS = """([x, y, w, h, color]) => {
  const d = document.createElement('div');
  d.id = '__easel_hl';
  Object.assign(d.style, {position: 'fixed', left: (x - 6) + 'px', top: (y - 6) + 'px',
    width: (w + 12) + 'px', height: (h + 12) + 'px', border: '3px solid ' + color,
    borderRadius: '10px', boxShadow: '0 0 0 6px ' + color + '33, 0 0 28px ' + color + '99',
    zIndex: 2147483647, pointerEvents: 'none'});
  document.body.appendChild(d);
}"""
UNHIGHLIGHT_JS = "() => { const d = document.getElementById('__easel_hl'); if (d) d.remove(); }"

TERMINAL_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;height:100%;background:#e9ebef;}}
body{{display:flex;align-items:center;justify-content:center;font-family:"Cascadia Mono","JetBrains Mono",Consolas,"Microsoft YaHei",monospace;}}
.term{{width:{tw}px;max-height:{th}px;background:#14161b;border-radius:14px;overflow:hidden;
box-shadow:0 24px 60px rgba(15,18,25,.28);}}
.bar{{height:40px;display:flex;align-items:center;gap:8px;padding:0 16px;background:#1f2229;color:#9aa1ad;font-size:14px;}}
.dot{{width:12px;height:12px;border-radius:50%;}}
.title{{margin-left:10px;}}
pre{{margin:0;padding:22px 26px;color:#d7dbe2;font-size:{fs}px;line-height:1.55;white-space:pre-wrap;word-break:break-all;}}
.ps{{color:#7ee0a1;}} .cmd{{color:#ffffff;font-weight:600;}}
</style></head><body><div class="term"><div class="bar">
<span class="dot" style="background:#ff5f57"></span><span class="dot" style="background:#febc2e"></span>
<span class="dot" style="background:#28c840"></span><span class="title">{title}</span></div>
<pre><span class="ps">&gt;</span> <span class="cmd">{cmd}</span>
{output}</pre></div></body></html>"""


class StepError(RuntimeError):
    pass


def _resolve_url(base: str | None, target: str) -> str:
    if "://" in target or target.startswith("about:"):
        return target
    if not base:
        raise StepError(f"goto 使用了相对路径 {target!r}，但计划里没有 base_url")
    return urljoin(base.rstrip("/") + "/", target.lstrip("/"))


def _run_action(page, action: dict, base_url: str | None, timeout: int) -> None:
    if len(action) != 1:
        raise StepError(f"每个动作只能有一个键：{action}")
    (kind, value), = action.items()
    if kind == "goto":
        page.goto(_resolve_url(base_url, str(value)), wait_until="domcontentloaded", timeout=timeout)
    elif kind == "click":
        page.locator(value).first.click(timeout=timeout)
    elif kind == "fill":
        page.locator(value[0]).first.fill(str(value[1]), timeout=timeout)
    elif kind == "press":
        if isinstance(value, list):
            page.locator(value[0]).first.press(value[1], timeout=timeout)
        else:
            page.keyboard.press(value)
    elif kind == "hover":
        page.locator(value).first.hover(timeout=timeout)
    elif kind == "select":
        page.locator(value[0]).first.select_option(str(value[1]), timeout=timeout)
    elif kind == "check":
        page.locator(value).first.check(timeout=timeout)
    elif kind == "wait_for":
        page.locator(value).first.wait_for(state="visible", timeout=timeout)
    elif kind == "wait":
        page.wait_for_timeout(int(value))
    elif kind == "scroll":
        if isinstance(value, (int, float)):
            page.mouse.wheel(0, float(value))
        else:
            page.locator(value).first.scroll_into_view_if_needed(timeout=timeout)
    else:
        raise StepError(f"不支持的动作：{kind}")


def _crop(image_path: Path, bbox: dict, scale: float, viewport: dict, out: Path, *,
          pad: int = CROP_PAD, min_size: tuple[int, int] = CROP_MIN) -> None:
    from PIL import Image

    with Image.open(image_path) as img:
        vw, vh = viewport["width"], viewport["height"]
        cx, cy = bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2
        w = min(vw, max(min_size[0], bbox["width"] + pad * 2))
        h = min(vh, max(min_size[1], bbox["height"] + pad * 2))
        left = min(max(0, cx - w / 2), vw - w)
        top = min(max(0, cy - h / 2), vh - h)
        box = tuple(int(round(v * scale)) for v in (left, top, left + w, top + h))
        img.crop(box).save(out)


def _shoot(page, path: Path, masks: list[str]) -> None:
    locators = [page.locator(sel) for sel in masks]
    page.screenshot(path=str(path), mask=locators, mask_color="#2b2f36", animations="disabled")


def _terminal_shot(context, step: dict, path: Path, viewport: dict) -> dict:
    term = step["terminal"]
    page = context.new_page()
    try:
        page.set_viewport_size(viewport)
        tw = int(viewport["width"] * 0.78)
        doc = TERMINAL_HTML.format(
            tw=tw, th=int(viewport["height"] * 0.86), fs=max(16, viewport["width"] // 72),
            title=html.escape(term.get("title") or "Terminal"), cmd=html.escape(term.get("cmd", "")),
            output=html.escape(term.get("output", "").rstrip()),
        )
        page.set_content(doc, wait_until="load")
        page.wait_for_timeout(200)
        page.screenshot(path=str(path), animations="disabled")
        return page.locator(".term").bounding_box() or {}
    finally:
        page.close()


def run_plan(plan: dict, out_dir: Path, *, record: bool = False, headed: bool = False,
             slow_mo: int = 0, timeout: int = 15000) -> dict:
    from playwright.sync_api import sync_playwright

    viewport = plan.get("viewport") or {"width": 1600, "height": 900}
    scale = float(plan.get("scale") or 2)
    settle = int(plan.get("settle_ms") or 600)
    masks = list(plan.get("mask") or [])
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"viewport": viewport, "scale": scale, "base_url": plan.get("base_url"),
                    "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": {}, "ok": True}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed, slow_mo=slow_mo, args=LAUNCH_ARGS, **render_launch_options())
        ctx_kwargs: dict = {"viewport": viewport, "device_scale_factor": scale, "locale": "zh-CN"}
        if plan.get("storage_state"):
            ctx_kwargs["storage_state"] = plan["storage_state"]
        if record:
            ctx_kwargs["record_video_dir"] = str(out_dir / "recording")
            ctx_kwargs["record_video_size"] = viewport
        context = browser.new_context(**ctx_kwargs)
        context.set_default_timeout(timeout)
        page = context.new_page()
        sections = plan.get("sections") or [{"title": "", "steps": plan.get("shots") or []}]
        try:
            for section in sections:
                for step in section.get("steps", []):
                    sid = step["id"]
                    entry: dict = {"status": "ok"}
                    report["steps"][sid] = entry
                    if step.get("image"):
                        entry.update(image=step["image"], source="provided")
                        continue
                    if step.get("shot") is False:
                        entry.update(status="skipped", source="text-only")
                        continue
                    image = out_dir / f"{sid}.png"
                    try:
                        if step.get("terminal"):
                            bbox = _terminal_shot(context, step, image, viewport)
                            entry.update(image=image.as_posix(), source="terminal", bbox=bbox)
                            if step.get("crop", True) and bbox:
                                crop = out_dir / f"{sid}-crop.png"
                                _crop(image, bbox, scale, viewport, crop, pad=CONTAINER_PAD, min_size=(0, 0))
                                entry["image_crop"] = crop.as_posix()
                            continue
                        for action in step.get("actions") or []:
                            _run_action(page, action, plan.get("base_url"), timeout)
                            page.wait_for_timeout(settle)
                        try:
                            page.evaluate("document.fonts && document.fonts.ready")
                        except Exception:  # noqa: BLE001
                            pass
                        bbox = None
                        if step.get("highlight"):
                            target = page.locator(step["highlight"]).first
                            target.scroll_into_view_if_needed(timeout=timeout)
                            bbox = target.bounding_box()
                            if not bbox:
                                raise StepError(f"高亮元素不可见：{step['highlight']}")
                            page.evaluate(HIGHLIGHT_JS, [bbox["x"], bbox["y"], bbox["width"], bbox["height"], ACCENT])
                        _shoot(page, image, masks)
                        page.evaluate(UNHIGHLIGHT_JS)
                        entry.update(image=image.as_posix(), source="web", url=page.url, bbox=bbox)
                        crop_spec = step.get("crop")
                        if isinstance(crop_spec, str):
                            region = page.locator(crop_spec).first.bounding_box()
                            if not region:
                                raise StepError(f"裁切容器不可见：{crop_spec}")
                            crop = out_dir / f"{sid}-crop.png"
                            _crop(image, region, scale, viewport, crop, pad=CONTAINER_PAD, min_size=(0, 0))
                            entry["image_crop"] = crop.as_posix()
                        elif crop_spec and bbox:
                            crop = out_dir / f"{sid}-crop.png"
                            _crop(image, bbox, scale, viewport, crop)
                            entry["image_crop"] = crop.as_posix()
                    except Exception as exc:  # noqa: BLE001
                        entry.update(status="failed", error=f"{type(exc).__name__}: {exc}".splitlines()[0][:400])
                        debug = out_dir / f"{sid}-error.png"
                        try:
                            page.screenshot(path=str(debug))
                            entry["debug_image"] = debug.as_posix()
                        except Exception:  # noqa: BLE001
                            pass
                        report["ok"] = False
                        raise StepError(sid) from exc
        except StepError:
            pass
        finally:
            video = page.video if record else None
            context.close()
            browser.close()
            if video:
                try:
                    src = Path(video.path())
                    dst = out_dir / "walkthrough.webm"
                    src.replace(dst)
                    report["recording"] = dst.as_posix()
                except Exception as exc:  # noqa: BLE001
                    report["recording_error"] = str(exc)
    (out_dir / "capture-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _print_report(report: dict, out_dir: Path) -> None:
    for sid, entry in report["steps"].items():
        mark = {"ok": "✅", "skipped": "·", "failed": "❌"}[entry["status"]]
        print(f"{mark} {sid}: {entry.get('image') or entry.get('source')}"
              + (f"\n   {entry['error']}\n   调试截图：{entry.get('debug_image')}" if entry["status"] == "failed" else ""))
    if report.get("recording"):
        print(f"🎬 录屏：{report['recording']}")
    print(f"报告：{(out_dir / 'capture-report.json').as_posix()}")


def _selftest() -> int:
    page_html = """<!doctype html><html><body style="font-family:sans-serif;padding:40px">
<h1>Demo App</h1><input id="name" placeholder="名称"><input type="password" id="pw" value="secret">
<button id="go" onclick="document.getElementById('done').style.display='block'">新建</button>
<p id="done" style="display:none">创建成功</p></body></html>"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "index.html").write_text(page_html, encoding="utf-8")
        plan = {
            "base_url": (root.as_uri() + "/"), "viewport": {"width": 960, "height": 540}, "scale": 1,
            "mask": ["#pw"], "settle_ms": 100,
            "sections": [{"title": "t", "steps": [
                {"id": "open", "actions": [{"goto": "index.html"}], "highlight": "#go", "crop": True, "text": "x"},
                {"id": "create", "actions": [{"fill": ["#name", "春日"]}, {"click": "#go"},
                                             {"wait_for": "text=创建成功"}], "crop": "h1", "text": "x"},
                {"id": "term", "terminal": {"cmd": "easel doctor", "output": "OK"}, "text": "x"},
                {"id": "note", "shot": False, "text": "x"},
            ]}],
        }
        out = root / "shots"
        report = run_plan(plan, out, timeout=5000)
        checks = {
            "ok": report["ok"],
            "images": all((out / f"{s}.png").stat().st_size > 0 for s in ("open", "create", "term")),
            "crop": (out / "open-crop.png").is_file() and (out / "term-crop.png").is_file(),
            "container_crop": (out / "create-crop.png").is_file(),
            "bbox": bool(report["steps"]["open"].get("bbox")),
            "skip": report["steps"]["note"]["status"] == "skipped",
        }
        bad = dict(plan)
        bad["sections"] = [{"title": "t", "steps": [{"id": "missing", "actions": [{"goto": "index.html"},
                                                                                  {"click": "#nope"}], "text": "x"}]}]
        failed = run_plan(bad, root / "bad", timeout=1500)
        checks["failure_reported"] = (not failed["ok"]) and failed["steps"]["missing"]["status"] == "failed"
    bad_checks = [k for k, v in checks.items() if not v]
    if bad_checks:
        print(f"[FAIL] {', '.join(bad_checks)}", file=sys.stderr)
        return 1
    print("[PASS] capture 自检通过（动作、高亮、打码、裁切、终端截图、失败报告）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="按教程计划操作产品并截图")
    ap.add_argument("--plan", help="plan.json 路径")
    ap.add_argument("--out-dir", help="截图输出目录（默认 plan 同级 shots/）")
    ap.add_argument("--record", action="store_true", help="同时录制整段操作为 walkthrough.webm")
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口（调试选择器用）")
    ap.add_argument("--slow-mo", type=int, default=0, help="每个动作放慢毫秒数（录屏时建议 200-400）")
    ap.add_argument("--timeout", type=int, default=15000, help="单个动作超时毫秒（默认 15000）")
    ap.add_argument("--selftest", action="store_true", help="运行自检")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.plan:
        ap.error("需要 --plan（或 --selftest）")
    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir) if args.out_dir else plan_path.parent / "shots"
    validate_output_path(out_dir / "capture-report.json", create_parent=True)
    try:
        report = run_plan(plan, out_dir, record=args.record, headed=args.headed,
                          slow_mo=args.slow_mo, timeout=args.timeout)
    except ImportError:
        print("ERROR: 未安装 playwright。请运行：pip install playwright", file=sys.stderr)
        return 3
    _print_report(report, out_dir)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
