#!/usr/bin/env python3
"""review.py — 交付前自检：把成品变成“看得见”的审阅图，并做确定性检查。

    review.py doc   --html outputs/<主题>/<标题>.html -o outputs/<主题>/assets/review
        → 逐页截图 page-NN.png；检查坏图、图片数量、敏感信息
    review.py video --input outputs/<主题>/<标题>.mp4 -o outputs/<主题>/assets/review \
                    --timeline outputs/<主题>/assets/video/timeline.json --size 1920x1080
        → 每个镜头中点抽帧拼成 contact-sheet.png；检查时长、分辨率、音轨、敏感信息

审阅图必须由 Agent 打开查看；脚本只做机器能判定的部分。
    review.py --selftest
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared" / "scripts"))
from output_paths import validate_output_path  # noqa: E402
from render_browser import render_launch_options  # noqa: E402

MAX_PAGES = 12
MAX_FRAMES = 24
THUMB_W = 480


def _guard(text: str) -> tuple[list[str], list[str]]:
    try:
        import content_guard
    except ImportError:
        return [], ["content_guard 不可用，跳过敏感信息扫描"]
    findings = content_guard.scan(text, content_guard.load_env_literals())
    block = [f"{f.category}: {f.snippet}" for f in findings if f.category in content_guard.BLOCK_CATEGORIES]
    warn = [f"{f.category}: {f.snippet}" for f in findings if f.category not in content_guard.BLOCK_CATEGORIES]
    return block, warn


def review_doc(html_path: Path, out_dir: Path, width: int, page_height: int, pdf_path: Path | None = None) -> dict:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    full = out_dir / "doc-full.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--font-render-hinting=none"], **render_launch_options())
        page = browser.new_page(viewport={"width": width, "height": page_height})
        page.goto(html_path.resolve().as_uri(), wait_until="load", timeout=30000)
        page.wait_for_timeout(500)
        info = page.evaluate("""() => ({
            images: document.images.length,
            broken: [...document.images].filter(i => !i.complete || i.naturalWidth === 0).map(i => i.getAttribute('src')),
            text: document.body.innerText
        })""")
        page.screenshot(path=str(full), full_page=True)
        browser.close()
    pages = []
    with Image.open(full) as img:
        total = (img.height + page_height - 1) // page_height
        for i in range(min(total, MAX_PAGES)):
            path = out_dir / f"page-{i + 1:02d}.png"
            img.crop((0, i * page_height, img.width, min(img.height, (i + 1) * page_height))).save(path)
            pages.append(path.as_posix())
    block, warn = _guard(info["text"])
    fails = [f"坏图：{src}" for src in info["broken"]] + [f"敏感信息：{b}" for b in block]
    if info["images"] == 0:
        fails.append("文档里没有任何图片")
    notes = warn + ([f"文档共 {total} 屏，只导出前 {MAX_PAGES} 屏"] if total > MAX_PAGES else [])
    if pdf_path:
        try:
            import pypdfium2
        except ImportError:
            notes.append("未安装 pypdfium2，PDF 未导出审阅图（pip install pypdfium2）")
        else:
            doc = pypdfium2.PdfDocument(str(pdf_path))
            for i in range(min(len(doc), MAX_PAGES)):
                path = out_dir / f"pdf-{i + 1:02d}.png"
                doc[i].render(scale=1.3).to_pil().save(path)
                pages.append(path.as_posix())
            doc.close()
    return {"kind": "doc", "ok": not fails, "fails": fails, "warnings": notes, "pages": pages,
            "images": info["images"], "chars": len(info["text"])}


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8")
    data = json.loads(out.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"duration": float(data.get("format", {}).get("duration") or 0), "width": video.get("width"),
            "height": video.get("height"), "audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


def review_video(video: Path, out_dir: Path, timeline: Path | None, size: str | None) -> dict:
    from PIL import Image, ImageDraw, ImageFont

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"未找到 {tool}")
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = _probe(video)
    fails, warns = [], []
    if meta["duration"] <= 0:
        fails.append("视频时长为 0")
    if not meta["audio"]:
        fails.append("没有音轨")
    if size and f"{meta['width']}x{meta['height']}" != size:
        fails.append(f"分辨率 {meta['width']}x{meta['height']} 与要求 {size} 不符")
    points, said = [], ""
    if timeline and timeline.is_file():
        tl = json.loads(timeline.read_text(encoding="utf-8"))
        points = [(s["start"] + s["duration"] / 2, s["id"]) for s in tl["timeline"]]
        said = "\n".join(s.get("say", "") for s in tl["timeline"])
        if abs(tl["duration"] - meta["duration"]) > 1.5:
            fails.append(f"成片时长 {meta['duration']:.1f}s 与时间线 {tl['duration']:.1f}s 差距过大")
        warns += tl.get("warnings", [])
    if not points and meta["duration"] > 0:
        n = 12
        points = [(meta["duration"] * (i + 0.5) / n, f"#{i + 1}") for i in range(n)]
    if len(points) > MAX_FRAMES:
        step = len(points) / MAX_FRAMES
        points = [points[int(i * step)] for i in range(MAX_FRAMES)]
    thumbs = []
    for i, (at, label) in enumerate(points):
        path = out_dir / f"frame-{i + 1:02d}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={THUMB_W}:-2", str(path)], capture_output=True)
        if path.is_file():
            thumbs.append((path, f"{int(at // 60):02d}:{at % 60:04.1f}  {label}"))
    sheet = None
    if thumbs:
        with Image.open(thumbs[0][0]) as first:
            tw, th = first.size
        cols, label_h = 4, 30
        rows = (len(thumbs) + cols - 1) // cols
        canvas = Image.new("RGB", (cols * tw + (cols + 1) * 12, rows * (th + label_h) + (rows + 1) * 12), "#111317")
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("msyh.ttc", 16)
        except OSError:
            font = ImageFont.load_default()
        for i, (path, label) in enumerate(thumbs):
            x, y = 12 + (i % cols) * (tw + 12), 12 + (i // cols) * (th + label_h + 12)
            with Image.open(path) as im:
                canvas.paste(im, (x, y))
            draw.text((x + 4, y + th + 6), label, fill="#d7dbe2", font=font)
        sheet = out_dir / "contact-sheet.png"
        canvas.save(sheet)
    else:
        fails.append("没能抽出任何画面")
    block, warn = _guard(said)
    fails += [f"口播敏感信息：{b}" for b in block]
    warns += warn
    return {"kind": "video", "ok": not fails, "fails": fails, "warnings": warns, **meta,
            "contact_sheet": sheet.as_posix() if sheet else None, "frames": [t[0].as_posix() for t in thumbs]}


def _selftest() -> int:
    from PIL import Image

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        Image.new("RGB", (400, 300), "#ccc").save(root / "a.png")
        (root / "t.html").write_text("<html><body><h1>教程</h1><img src='a.png'><img src='missing.png'>"
                                     + "<p>内容</p>" * 200 + "</body></html>", encoding="utf-8")
        r = review_doc(root / "t.html", root / "rev", 800, 600)
        checks = {"broken_found": any("missing.png" in f for f in r["fails"]), "pages": len(r["pages"]) >= 2,
                  "images": r["images"] == 2}
        if shutil.which("ffmpeg"):
            vid = root / "v.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "3", "-pix_fmt", "yuv420p", "-shortest",
                            str(vid)], check=True)
            tl = root / "timeline.json"
            tl.write_text(json.dumps({"duration": 3.0, "warnings": [], "timeline": [
                {"id": "a", "start": 0, "duration": 1.5, "say": "第一段"},
                {"id": "b", "start": 1.5, "duration": 1.5, "say": "第二段"}]}), encoding="utf-8")
            v = review_video(vid, root / "vrev", tl, "640x360")
            checks.update(video_ok=v["ok"], sheet=bool(v["contact_sheet"]), frames=len(v["frames"]) == 2)
            v2 = review_video(vid, root / "vrev2", None, "1920x1080")
            checks["size_mismatch"] = not v2["ok"]
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        print(f"[FAIL] {', '.join(bad)}", file=sys.stderr)
        return 1
    print("[PASS] review 自检通过" + ("" if shutil.which("ffmpeg") else "（无 ffmpeg，跳过视频部分）"))
    return 0


def _emit(result: dict, out_dir: Path) -> int:
    path = out_dir / f"review-{result['kind']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(("✅ 机器检查通过" if result["ok"] else "❌ 机器检查未通过") + f"（{path.as_posix()}）")
    for f in result["fails"]:
        print(f"  FAIL {f}")
    for w in result["warnings"]:
        print(f"  WARN {w}")
    looks = result.get("pages") or ([result["contact_sheet"]] if result.get("contact_sheet") else [])
    if looks:
        print("请逐张查看审阅图：\n" + "\n".join(f"  {p}" for p in looks))
    return 0 if result["ok"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="教程成品自检")
    ap.add_argument("--selftest", action="store_true", help="运行自检")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("doc", help="文档自检：逐屏截图 + 坏图/敏感信息检查")
    p.add_argument("--html", required=True)
    p.add_argument("-o", "--out-dir", required=True)
    p.add_argument("--width", type=int, default=1000)
    p.add_argument("--page-height", type=int, default=1400)
    p.add_argument("--pdf", help="同时把 PDF 逐页导出为审阅图（PDF 的图片加载与 HTML 不同，须单独看）")
    p = sub.add_parser("video", help="视频自检：镜头抽帧总览 + 时长/分辨率/音轨检查")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("-o", "--out-dir", required=True)
    p.add_argument("--timeline", help="build_video 输出的 timeline.json")
    p.add_argument("--size", help="期望分辨率，如 1920x1080")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.cmd:
        ap.error("需要子命令 doc / video（或 --selftest）")
    out_dir = Path(args.out_dir)
    validate_output_path(out_dir / f"review-{args.cmd}.json", create_parent=True)
    try:
        if args.cmd == "doc":
            result = review_doc(Path(args.html), out_dir, args.width, args.page_height,
                                Path(args.pdf) if args.pdf else None)
        else:
            result = review_video(Path(args.input), out_dir, Path(args.timeline) if args.timeline else None, args.size)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return _emit(result, out_dir)


if __name__ == "__main__":
    sys.exit(main())
