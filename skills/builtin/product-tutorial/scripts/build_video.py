#!/usr/bin/env python3
"""build_video.py — 由教程计划 + 真实截图生成教程视频（逐步配音 + 字幕 + 高亮推近）。

流程：渲染封面/章节/片尾卡 → 逐段 TTS → 按配音时长排镜头（高亮步骤先全图再推近）
→ 合并配音与字幕 → 交给 auto-short-video 的 assemble.py 合成。

用法：
    build_video.py --plan outputs/<主题>/assets/plan.json -o outputs/<主题>/<标题>.mp4 --size 1920x1080
    build_video.py ... --engine closed --voice <音色>    # 闭源音色，按量计费，先经用户确认
    build_video.py --selftest
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SHARED = ROOT / "shared" / "scripts"
ASSEMBLE = ROOT / "builtin" / "auto-short-video" / "scripts" / "assemble.py"
sys.path.insert(0, str(SHARED))
from output_paths import validate_output_path  # noqa: E402
from render_browser import render_launch_options  # noqa: E402

ACCENT = "#ff5a36"
TAIL_PAD = 0.45
SILENT_CARD = 1.6
SUBTITLE_ZONE = 0.22

CARD_CSS = """
html,body{margin:0;width:%(w)dpx;height:%(h)dpx;overflow:hidden;}
body{background:#f5f3ee;color:#16181d;font-family:"Noto Sans SC","Microsoft YaHei UI","Microsoft YaHei","PingFang SC",sans-serif;}
.wrap{position:absolute;inset:0;padding:%(pad)dpx;display:flex;flex-direction:column;justify-content:center;}
.brand{position:absolute;top:%(pad)dpx;left:%(pad)dpx;font-size:%(small)dpx;letter-spacing:.18em;color:#6b6f78;}
.chip{position:absolute;top:%(pad)dpx;right:%(pad)dpx;font-size:%(small)dpx;color:%(accent)s;border:1.5px solid %(accent)s;
border-radius:999px;padding:.35em 1.1em;}
.rule{width:%(rule)dpx;height:4px;background:%(accent)s;margin-bottom:%(gap)dpx;}
h1{margin:0;font-weight:300;font-size:%(title)dpx;line-height:1.22;letter-spacing:.01em;max-width:88%%;}
p{margin:%(gap)dpx 0 0;font-size:%(sub)dpx;line-height:1.6;color:#4a4e57;max-width:80%%;font-weight:400;}
ol{margin:%(gap)dpx 0 0;padding:0;list-style:none;counter-reset:n;}
li{counter-increment:n;font-size:%(sub)dpx;line-height:1.5;margin:.55em 0;color:#2a2d33;}
li::before{content:counter(n,decimal-leading-zero);color:%(accent)s;font-weight:600;margin-right:.9em;}
.grid{position:absolute;inset:0;background-image:linear-gradient(#16181d0a 1px,transparent 1px),
linear-gradient(90deg,#16181d0a 1px,transparent 1px);background-size:%(cell)dpx %(cell)dpx;}
"""


def strip_md(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"[*_#>]", "", text).strip()


def card_html(size: tuple[int, int], *, brand: str, chip: str, title: str, sub: str = "", items=()) -> str:
    w, h = size
    base = min(w, h)
    vals = {"w": w, "h": h, "pad": int(base * 0.1), "small": int(base * 0.026), "title": int(base * 0.085),
            "sub": int(base * 0.036), "gap": int(base * 0.035), "rule": int(base * 0.07), "cell": int(base * 0.06),
            "accent": ACCENT}
    if w < h:
        vals["title"] = int(w * 0.095)
    body = [f'<div class="grid"></div><div class="brand">{html.escape(brand)}</div>']
    if chip:
        body.append(f'<div class="chip">{html.escape(chip)}</div>')
    body.append('<div class="wrap"><div class="rule"></div>')
    body.append(f"<h1>{html.escape(title)}</h1>")
    if sub:
        body.append(f"<p>{html.escape(sub)}</p>")
    if items:
        body.append("<ol>" + "".join(f"<li>{html.escape(i)}</li>" for i in items) + "</ol>")
    body.append("</div>")
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{CARD_CSS % vals}</style></head>'
            f'<body>{"".join(body)}</body></html>')


def render_cards(cards: list[tuple[str, Path]], size: tuple[int, int]) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--font-render-hinting=none"], **render_launch_options())
        page = browser.new_page(viewport={"width": size[0], "height": size[1]}, device_scale_factor=1)
        for doc, out in cards:
            page.set_content(doc, wait_until="load")
            page.wait_for_timeout(150)
            page.screenshot(path=str(out))
        browser.close()


def zoom_crop(image: Path, bbox: dict, scale: float, viewport: dict, size: tuple[int, int], out: Path) -> None:
    from PIL import Image

    aspect = size[0] / size[1]
    vw, vh = viewport["width"], viewport["height"]
    w = min(vw, max(bbox["width"] * 2.6, vw * 0.5))
    h = w / aspect
    if h > vh:
        h, w = vh, vh * aspect
    if bbox["height"] * 1.6 > h:
        h = min(vh, bbox["height"] * 1.6)
        w = min(vw, h * aspect)
    cx, cy = bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2
    left, top = min(max(0, cx - w / 2), vw - w), min(max(0, cy - h / 2), vh - h)
    with Image.open(image) as img:
        img.crop(tuple(int(round(v * scale)) for v in (left, top, left + w, top + h))).save(out)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败（{proc.returncode}）：{' '.join(cmd[:4])} …\n{(proc.stderr or proc.stdout)[-1500:]}")


def run_retry(cmd: list[str], attempts: int = 3, wait: float = 4.0) -> None:
    """在线服务（如 TTS）偶发网络失败时重试；最后一次仍失败则抛出原错误。"""
    for i in range(attempts):
        try:
            _run(cmd)
            return
        except RuntimeError:
            if i == attempts - 1:
                raise
            time.sleep(wait)


def probe(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    return float(out.stdout.strip() or 0)


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _parse_ts(value: str) -> float:
    h, m, rest = value.strip().replace(".", ",").split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    if not path.is_file():
        return []
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="ignore").strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        start, end = lines[idx].split("-->")
        text = " ".join(lines[idx + 1:]).strip()
        if text:
            cues.append((_parse_ts(start), _parse_ts(end), text))
    return cues


def build_segments(plan: dict, report: dict) -> list[dict]:
    product = plan.get("product") or ""
    sections = plan.get("sections", [])
    segs = [{"kind": "card", "id": "cover", "card": {"chip": "教程", "title": plan["title"], "sub": plan["outcome"]},
             "say": plan.get("intro_say") or f"{plan['title']}。{plan['outcome']}。"}]
    for si, section in enumerate(sections):
        if len(sections) > 1:
            segs.append({"kind": "card", "id": f"section-{si + 1}",
                         "card": {"chip": f"{si + 1:02d} / {len(sections):02d}", "title": section["title"],
                                  "sub": section.get("goal", "")},
                         "say": section.get("say", "")})
        for step in section.get("steps", []):
            say = step.get("say") or strip_md(step.get("text", ""))
            entry = report.get("steps", {}).get(step["id"], {})
            image = step.get("image") or entry.get("image")
            if step.get("shot") is False or not image:
                segs.append({"kind": "card", "id": step["id"],
                             "card": {"chip": section["title"], "title": strip_md(step.get("text", ""))}, "say": say})
            else:
                segs.append({"kind": "step", "id": step["id"], "image": image, "bbox": entry.get("bbox"),
                             "source": entry.get("source"), "say": say})
    outro_items = plan.get("next") or []
    outro_say = plan.get("outro_say") or (("接下来，你可以" + "；".join(outro_items) + "。") if outro_items else "")
    segs.append({"kind": "card", "id": "outro", "card": {"chip": product, "title": "接下来可以做", "items": outro_items}
                 if outro_items else {"chip": product, "title": "完成", "sub": plan["outcome"]}, "say": outro_say})
    return segs


def build(plan: dict, report: dict, out: Path, work: Path, size: tuple[int, int], *, engine: str, voice: str | None,
          rate: str | None, bgm: str | None, zoom: bool, assemble: bool = True) -> dict:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"未找到 {tool}，请先安装并加入 PATH")
    work.mkdir(parents=True, exist_ok=True)
    segs = build_segments(plan, report)
    brand = plan.get("product") or ""
    cards = []
    for seg in segs:
        if seg["kind"] == "card":
            seg["image"] = (work / f"card-{seg['id']}.png").as_posix()
            c = seg["card"]
            cards.append((card_html(size, brand=brand, chip=c.get("chip", ""), title=c["title"], sub=c.get("sub", ""),
                                    items=c.get("items", ())), Path(seg["image"])))
    render_cards(cards, size)

    viewport, scale = report.get("viewport") or {}, float(report.get("scale") or 1)
    shots, cues, timeline, warnings, wavs, t = [], [], [], [], [], 0.0
    for i, seg in enumerate(segs):
        wav = work / f"a{i:03d}.wav"
        if seg["say"]:
            mp3, srt = work / f"a{i:03d}.mp3", work / f"a{i:03d}.srt"
            cmd = [sys.executable, str(SHARED / "tts.py"), "speak", "-t", seg["say"], "-o", str(mp3),
                   "--subtitle", str(srt), "--engine", engine]
            if voice:
                cmd += ["--voice", voice]
            if rate:
                cmd += ["--rate", rate]
            run_retry(cmd)
            speech = probe(mp3)
            dur = round(speech + TAIL_PAD, 3)
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-af", f"apad=whole_dur={dur}", "-t", str(dur),
                  "-ar", "44100", "-ac", "2", str(wav)])
            seg_cues = parse_srt(srt) or [(0.0, speech, seg["say"])]
            cues += [(t + a, t + min(b, speech), txt) for a, b, txt in seg_cues]
        else:
            dur = SILENT_CARD
            _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(dur), str(wav)])
        wavs.append(wav)
        bbox = seg.get("bbox")
        if zoom and seg["kind"] == "step" and bbox and viewport and seg.get("source") == "web":
            full = round(dur * 0.4, 3)
            crop = work / f"zoom-{seg['id']}.png"
            zoom_crop(Path(seg["image"]), bbox, scale, viewport, size, crop)
            shots += [{"image": str(Path(seg["image"]).resolve()), "duration": full, "motion": "static"},
                      {"image": str(crop.resolve()), "duration": round(dur - full, 3), "motion": "static"}]
        else:
            shots.append({"image": str(Path(seg["image"]).resolve()), "duration": dur, "motion": "static"})
        if (seg.get("source") == "web" and bbox and viewport
                and (bbox["y"] + bbox["height"]) > viewport["height"] * (1 - SUBTITLE_ZONE)):
            warnings.append(f"{seg['id']}: 高亮元素在画面底部，可能被字幕遮挡")
        timeline.append({"id": seg["id"], "kind": seg["kind"], "start": round(t, 3), "duration": dur, "say": seg["say"]})
        t += dur

    listing = work / "audio-list.txt"
    listing.write_text("".join(f"file '{w.resolve().as_posix()}'\n" for w in wavs), encoding="utf-8")
    narration = work / "narration.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(narration)])
    srt_path = work / "subtitles.srt"
    srt_path.write_text("".join(f"{n}\n{_ts(a)} --> {_ts(b)}\n{txt}\n\n" for n, (a, b, txt) in enumerate(cues, 1)),
                        encoding="utf-8")
    storyboard = {"size": f"{size[0]}x{size[1]}", "image_motion": "static", "shots": shots,
                  "narration": str(narration.resolve()), "subtitle": str(srt_path.resolve())}
    if bgm:
        storyboard["bgm"] = str(Path(bgm).resolve())
    sb_path = work / "storyboard.json"
    sb_path.write_text(json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"output": out.as_posix(), "duration": round(t, 2), "segments": len(segs), "shots": len(shots),
              "timeline": timeline, "warnings": warnings, "storyboard": sb_path.as_posix()}
    (work / "timeline.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if assemble:
        proc = subprocess.run([sys.executable, str(ASSEMBLE), "assemble", "--resume", "--storyboard", str(sb_path),
                               "-o", str(out)])
        if proc.returncode != 0:
            raise RuntimeError(f"assemble.py 合成失败（退出码 {proc.returncode}）")
    return result


def _selftest() -> int:
    from PIL import Image

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        img = root / "s1.png"
        Image.new("RGB", (1600, 900), "#e8e8e8").save(img)
        plan = {"title": "测试教程", "outcome": "学会一件事", "product": "Demo",
                "sections": [{"title": "第一节", "steps": [{"id": "s1", "text": "点击 **开始**", "say": ""}]},
                             {"title": "第二节", "say": "", "steps": [{"id": "t1", "shot": False, "text": "纯文字"}]}],
                "next": ["继续学习"]}
        report = {"viewport": {"width": 800, "height": 450}, "scale": 2,
                  "steps": {"s1": {"status": "ok", "image": img.as_posix(), "source": "web",
                                   "bbox": {"x": 600, "y": 400, "width": 120, "height": 40}}}}
        segs = build_segments(plan, report)
        crop = root / "z.png"
        zoom_crop(img, report["steps"]["s1"]["bbox"], 2, report["viewport"], (1920, 1080), crop)
        with Image.open(crop) as c:
            ratio = c.width / c.height
        srt = root / "a.srt"
        srt.write_text("1\n00:00:00,100 --> 00:00:01,500\n你好\n\n2\n00:00:01,600 --> 00:00:02,000\n世界\n", encoding="utf-8")
        checks = {
            "segments": [s["id"] for s in segs] == ["cover", "section-1", "s1", "section-2", "t1", "outro"],
            "say_fallback": segs[2]["say"] == "点击 开始",
            "zoom_aspect": abs(ratio - 16 / 9) < 0.02,
            "srt": parse_srt(srt) == [(0.1, 1.5, "你好"), (1.6, 2.0, "世界")],
            "card_html": "测试教程" in card_html((1920, 1080), brand="Demo", chip="教程", title="测试教程"),
        }
        if shutil.which("ffmpeg") and shutil.which("ffprobe"):
            render_cards([(card_html((640, 360), brand="Demo", chip="", title="卡片"), root / "c.png")], (640, 360))
            checks["render_card"] = (root / "c.png").stat().st_size > 0
    bad = [k for k, v in checks.items() if not v]
    if bad:
        print(f"[FAIL] {', '.join(bad)}", file=sys.stderr)
        return 1
    print("[PASS] build_video 自检通过（分段、推近裁切、字幕解析、卡片渲染）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="教程计划 + 截图 → 教程视频")
    ap.add_argument("--plan", help="plan.json 路径")
    ap.add_argument("--report", help="capture-report.json（默认 plan 同级 shots/）")
    ap.add_argument("-o", "--output", help="输出 mp4，放在 outputs/<主题>/ 根目录")
    ap.add_argument("--size", help="成片尺寸，如 1920x1080 / 1080x1920（须与用户确认）")
    ap.add_argument("--work-dir", help="中间文件目录（默认 plan 同级 video/）")
    ap.add_argument("--engine", choices=["edge", "closed", "auto"], default="edge",
                    help="配音引擎，默认 edge（免费）；closed 为按量计费的闭源音色")
    ap.add_argument("--voice", help="音色 ID")
    ap.add_argument("--rate", help="语速，如 +5%%")
    ap.add_argument("--bgm", help="背景音乐文件（可选，自动压低）")
    ap.add_argument("--no-zoom", action="store_true", help="关闭高亮推近镜头")
    ap.add_argument("--selftest", action="store_true", help="运行自检")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not (args.plan and args.output and args.size):
        ap.error("需要 --plan、-o 与 --size（或 --selftest）")
    m = re.fullmatch(r"(\d{3,4})x(\d{3,4})", args.size)
    if not m:
        ap.error("--size 格式应为 宽x高，如 1920x1080")
    size = (int(m.group(1)), int(m.group(2)))
    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    report_path = Path(args.report) if args.report else plan_path.parent / "shots" / "capture-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {"steps": {}}
    failed = [sid for sid, e in report.get("steps", {}).items() if e.get("status") == "failed"]
    if failed:
        print(f"ERROR: 采集报告里有失败步骤：{', '.join(failed)}。先修好 capture 再出视频。", file=sys.stderr)
        return 2
    out = validate_output_path(args.output, create_parent=True)
    work = Path(args.work_dir) if args.work_dir else plan_path.parent / "video"
    validate_output_path(work / "storyboard.json", create_parent=True)
    try:
        result = build(plan, report, out, work, size, engine=args.engine, voice=args.voice, rate=args.rate,
                       bgm=args.bgm, zoom=not args.no_zoom)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"✅ {result['output']}（约 {result['duration']} 秒，{result['shots']} 个镜头）")
    print(f"时间线：{(work / 'timeline.json').as_posix()}")
    for w in result["warnings"]:
        print(f"WARN: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
