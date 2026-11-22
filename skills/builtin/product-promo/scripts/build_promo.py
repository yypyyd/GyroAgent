#!/usr/bin/env python3
"""build_promo.py — 由宣传计划 + 真实截图生成落地页、小红书卡片组和宣传视频。

用法：
    build_promo.py page  --promo outputs/<主题>/assets/promo.json -o "outputs/<主题>/<标题>.html" --long-image
    build_promo.py cards --promo outputs/<主题>/assets/promo.json --out-dir outputs/<主题>
    build_promo.py video --promo outputs/<主题>/assets/promo.json --name short -o "outputs/<主题>/<标题>-竖版.mp4"
    build_promo.py video ... --engine closed --voice <音色>   # 闭源音色按量计费，先经用户确认
    build_promo.py --selftest
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS.parent / "shared" / "scripts"))
sys.path.insert(0, str(SKILLS / "product-tutorial" / "scripts"))
from output_paths import validate_output_path  # noqa: E402
from render_browser import render_launch_options  # noqa: E402
from build_video import ASSEMBLE, SHARED, _run, _ts, parse_srt, probe, run_retry, strip_md, zoom_crop  # noqa: E402

FONT = '"Noto Sans SC","Microsoft YaHei UI","Microsoft YaHei","PingFang SC",sans-serif'
THEMES = {
    "dark": {"bg": "#0e1014", "fg": "#f3f1ec", "muted": "#9ba1ab", "card": "#171a21", "bar": "#1e222a",
             "line": "rgba(255,255,255,.08)", "dot": "rgba(255,255,255,.18)", "shadow": "rgba(0,0,0,.45)"},
    "light": {"bg": "#f5f3ee", "fg": "#16181d", "muted": "#5b606a", "card": "#ffffff", "bar": "#ecebe7",
              "line": "rgba(22,24,29,.08)", "dot": "rgba(22,24,29,.18)", "shadow": "rgba(22,24,29,.18)"},
}
DEFAULT_DURATION = {"hook": 2.2, "pain": 2.0, "screen": 3.0, "proof": 2.2, "cta": 3.2}
TAIL_PAD = 0.35
BEAT_TYPES = set(DEFAULT_DURATION)
esc = html.escape


def br(text: str) -> str:
    return esc(text).replace("\n", "<br>")


def fit_px(text: str, base: float, width: float, max_lines: int = 2) -> int:
    """缩小字号直到标题放得下：手动断行的每行不再折行，否则总行数不超过 max_lines。"""
    lines = (text or "").split("\n")

    def units(line: str) -> float:
        return sum(0.56 if ord(ch) < 128 else 1.0 for ch in line)

    def fits(px: float) -> bool:
        if len(lines) > 1:
            return all(units(line) * px <= width for line in lines)
        return math.ceil(units(lines[0]) * px / width) <= max_lines

    px = base
    while px > base * 0.55 and not fits(px):
        px *= 0.93
    return int(px)


class PromoError(RuntimeError):
    pass


def theme(promo: dict) -> dict:
    t = promo.get("theme") or {}
    vals = dict(THEMES.get(t.get("mode", "dark"), THEMES["dark"]))
    vals["accent"] = t.get("accent") or "#ff5a36"
    return vals


def base_css(t: dict) -> str:
    return f"""
:root{{--bg:{t['bg']};--fg:{t['fg']};--muted:{t['muted']};--card:{t['card']};--bar:{t['bar']};
--line:{t['line']};--dot:{t['dot']};--accent:{t['accent']};--shadow:{t['shadow']};}}
*{{box-sizing:border-box;}} html,body{{margin:0;}}
h1,h2,h3,p,li,.sub,.proof{{text-wrap:pretty;}}
body{{background:var(--bg);color:var(--fg);font-family:{FONT};-webkit-font-smoothing:antialiased;}}
.glow{{background-image:radial-gradient(circle at 82% 8%, {t['accent']}2e, transparent 42%),
linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
background-size:auto,64px 64px,64px 64px;}}
.win{{border-radius:14px;overflow:hidden;background:var(--card);border:1px solid var(--line);
box-shadow:0 30px 80px var(--shadow);}}
.win .bar{{height:30px;display:flex;gap:7px;align-items:center;padding:0 14px;background:var(--bar);}}
.win .bar i{{width:10px;height:10px;border-radius:50%;background:var(--dot);display:block;}}
.win img{{display:block;width:100%;height:auto;}}
.accent{{color:var(--accent);}}
.pill{{display:inline-flex;align-items:center;gap:.6em;border:1.5px solid var(--accent);color:var(--fg);
border-radius:999px;padding:.55em 1.3em;}}
.pill b{{color:var(--accent);font-weight:600;}}
"""


def window(src: str, extra: str = "") -> str:
    return f'<div class="win" {extra}><div class="bar"><i></i><i></i><i></i></div><img src="{esc(src)}" alt=""></div>'


def load_report(promo_path: Path, report: str | None) -> dict:
    path = Path(report) if report else promo_path.parent / "shots" / "capture-report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"steps": {}}


def shot(promo: dict, report: dict, sid: str | None) -> dict | None:
    if not sid:
        return None
    entry = report.get("steps", {}).get(sid) or {}
    provided = next((s.get("image") for s in promo.get("shots", []) if s.get("id") == sid and s.get("image")), None)
    image = provided or entry.get("image")
    if entry.get("status") == "failed" or not image or not Path(image).is_file():
        raise PromoError(f"画面 {sid} 未采集或采集失败，先运行 capture.py")
    return {"image": Path(image), "crop": Path(entry["image_crop"]) if entry.get("image_crop") else None,
            "bbox": entry.get("bbox"), "source": entry.get("source"),
            "viewport": report.get("viewport"), "scale": float(report.get("scale") or 1)}


# ---------- 落地页 ----------

def page_html(promo: dict, report: dict, out_dir: Path) -> str:
    t, page, cta = theme(promo), promo.get("page") or {}, promo["cta"]

    def rel(sid):
        s = shot(promo, report, sid)
        return os.path.relpath(s["image"].resolve(), out_dir.resolve()).replace(os.sep, "/") if s else ""

    hero = page.get("hero") or {}
    css = base_css(t) + """
.wrap{max-width:1120px;margin:0 auto;padding:0 48px;}
section{padding:88px 0;border-top:1px solid var(--line);}
.kicker{font-size:15px;letter-spacing:.18em;color:var(--muted);}
h1{font-size:64px;font-weight:300;line-height:1.15;margin:18px 0 0;letter-spacing:.01em;}
h2{font-size:36px;font-weight:300;margin:0 0 36px;}
.sub{font-size:20px;line-height:1.7;color:var(--muted);max-width:720px;margin:22px 0 32px;}
.hero{padding:96px 0 72px;} .hero .win{margin-top:56px;}
.pains{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px;}
.pains div{border:1px solid var(--line);border-radius:14px;padding:26px;font-size:19px;line-height:1.6;}
.pains div::before{content:"";display:block;width:28px;height:3px;background:var(--accent);margin-bottom:16px;}
.feat{display:grid;grid-template-columns:1fr 1.45fr;gap:56px;align-items:center;margin-bottom:84px;}
.feat:nth-child(even){grid-template-columns:1.45fr 1fr;} .feat:nth-child(even) .txt{order:2;}
.feat .no{font-size:14px;color:var(--accent);letter-spacing:.12em;}
.feat h3{font-size:32px;font-weight:400;margin:12px 0 14px;} .feat p{font-size:18px;line-height:1.75;color:var(--muted);margin:0;}
.feat .proof{margin-top:18px;font-size:15px;color:var(--fg);border-left:3px solid var(--accent);padding-left:12px;}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px;}
.stats div{border:1px solid var(--line);border-radius:14px;padding:28px;}
.stats b{display:block;font-size:52px;font-weight:300;color:var(--accent);}
.stats span{display:block;font-size:17px;margin-top:6px;} .stats small{display:block;color:var(--muted);margin-top:10px;font-size:13px;}
ol.steps{margin:0;padding:0;list-style:none;counter-reset:n;}
ol.steps li{counter-increment:n;font-size:18px;padding:16px 0;border-bottom:1px solid var(--line);font-family:Consolas,"Cascadia Mono",monospace;}
ol.steps li::before{content:counter(n,decimal-leading-zero);color:var(--accent);margin-right:18px;font-family:inherit;}
.end{text-align:center;padding:110px 0 120px;} .end h2{font-size:44px;margin-bottom:28px;}
footer{padding:28px 0;color:var(--muted);font-size:13px;border-top:1px solid var(--line);}
@media (max-width:900px){h1{font-size:44px;}.feat,.feat:nth-child(even){grid-template-columns:1fr;}.feat:nth-child(even) .txt{order:0;}}
"""
    parts = [f'<header class="hero glow"><div class="wrap"><div class="kicker">{esc(hero.get("kicker", promo["product"]))}</div>',
             f'<h1>{br(hero.get("title", promo["message"]))}</h1>',
             f'<p class="sub">{esc(hero.get("sub", promo["message"]))}</p>',
             f'<span class="pill">{esc(cta["text"])}<b>{esc(cta.get("action", ""))}</b></span>']
    if hero.get("shot"):
        parts.append(window(rel(hero["shot"])))
    parts.append("</div></header>")
    if page.get("pains"):
        parts.append(f'<section><div class="wrap"><h2>{esc(page.get("pains_title", "常见的卡点"))}</h2><div class="pains">'
                     + "".join(f"<div>{esc(p)}</div>" for p in page["pains"]) + "</div></div></section>")
    if page.get("features"):
        parts.append(f'<section><div class="wrap"><h2>{esc(page.get("features_title", "用 " + promo["product"]))}</h2>')
        for i, f in enumerate(page["features"], 1):
            proof = f'<div class="proof">{esc(f["proof"])}</div>' if f.get("proof") else ""
            img = window(rel(f["shot"])) if f.get("shot") else ""
            parts.append(f'<div class="feat"><div class="txt"><div class="no">{i:02d}</div><h3>{esc(f["title"])}</h3>'
                         f'<p>{esc(f.get("desc", ""))}</p>{proof}</div><div>{img}</div></div>')
        parts.append("</div></section>")
    if page.get("proof"):
        parts.append(f'<section><div class="wrap"><h2>{esc(page.get("proof_title", "一些事实"))}</h2><div class="stats">'
                     + "".join(f'<div><b>{esc(p["value"])}</b><span>{esc(p["label"])}</span>'
                               f'<small>来源：{esc(p.get("source", ""))}</small></div>' for p in page["proof"])
                     + "</div></div></section>")
    if page.get("steps"):
        parts.append(f'<section><div class="wrap"><h2>{esc(page.get("steps_title", "开始使用"))}</h2><ol class="steps">'
                     + "".join(f"<li>{esc(s)}</li>" for s in page["steps"]) + "</ol></div></section>")
    parts.append(f'<section class="end glow"><div class="wrap"><h2>{br(page.get("closing", promo["message"]))}</h2>'
                 f'<span class="pill">{esc(cta["text"])}<b>{esc(cta.get("action", ""))}</b></span></div></section>')
    parts.append(f'<footer><div class="wrap">{esc(promo["product"])}</div></footer>')
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{esc(promo["title"])}</title><style>{css}</style>'
            f'</head><body>{"".join(parts)}</body></html>')


def render_files(jobs: list[tuple[Path, Path, tuple[int, int], bool]]) -> None:
    """jobs: (html 文件, 输出 png, 视口, 是否整页)。一个浏览器渲染全部。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--font-render-hinting=none"], **render_launch_options())
        for doc, out, (w, h), full in jobs:
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            page.goto(doc.resolve().as_uri(), wait_until="load", timeout=30000)
            page.wait_for_timeout(250)
            page.screenshot(path=str(out), full_page=full)
            page.close()
        browser.close()


# ---------- 小红书卡片组 ----------

CARD_W, CARD_H = 1080, 1440


def card_docs(promo: dict, report: dict) -> list[str]:
    t, page, cta = theme(promo), promo.get("page") or {}, promo["cta"]
    css = base_css(t) + f"""
body{{width:{CARD_W}px;height:{CARD_H}px;overflow:hidden;}}
.c{{position:absolute;inset:0;padding:96px 84px;display:flex;flex-direction:column;}}
.top{{display:flex;justify-content:space-between;font-size:24px;letter-spacing:.16em;color:var(--muted);}}
.top b{{color:var(--accent);font-weight:500;letter-spacing:.08em;}}
h1{{font-size:96px;font-weight:300;line-height:1.16;margin:72px 0 0;}}
h2{{font-size:72px;font-weight:300;line-height:1.2;margin:56px 0 0;}}
p{{font-size:34px;line-height:1.65;color:var(--muted);margin:32px 0 0;}}
.rule{{width:64px;height:5px;background:var(--accent);margin-top:56px;}}
.shot{{margin-top:auto;}}
.proof{{font-size:30px;border-left:5px solid var(--accent);padding-left:22px;margin-top:36px;}}
ul{{list-style:none;padding:0;margin:56px 0 0;}} li{{font-size:38px;line-height:1.5;padding:26px 0;border-bottom:1px solid var(--line);}}
.pill{{font-size:32px;margin-top:48px;align-self:flex-start;}}
.mid{{margin:auto 0;display:flex;flex-direction:column;}} .mid h1{{margin-top:0;}}
ol{{margin:auto 0 0;padding:0;list-style:none;counter-reset:n;}}
ol li{{counter-increment:n;font-size:28px;font-family:Consolas,monospace;padding:16px 0;}}
ol li::before{{content:counter(n,decimal-leading-zero);color:var(--accent);margin-right:20px;}}
"""

    def doc(body: str) -> str:
        return f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body class="glow"><div class="c">{body}</div></body></html>'

    def ttl(tag: str, text: str, base: int) -> str:
        return f'<{tag} style="font-size:{fit_px(text, base, CARD_W - 2 * 84)}px">{br(text)}</{tag}>'

    def src(sid, prefer_crop=False):
        s = shot(promo, report, sid)
        img = s["crop"] if prefer_crop and s["crop"] else s["image"]
        return img.resolve().as_uri()

    hero, feats = page.get("hero") or {}, page.get("features") or []
    total = 2 + len(feats) + (1 if page.get("pains") else 0)
    docs, n = [], 1
    cover = (f'<div class="top"><span>{esc(promo["product"])}</span><b>{n:02d} / {total:02d}</b></div>'
             f'{ttl("h1", hero.get("title", promo["message"]), 96)}<p>{esc(hero.get("sub", ""))}</p>')
    if hero.get("shot"):
        cover += f'<div class="shot">{window(src(hero["shot"]))}</div>'
    docs.append(doc(cover))
    if page.get("pains"):
        n += 1
        docs.append(doc(f'<div class="top"><span>{esc(promo["product"])}</span><b>{n:02d} / {total:02d}</b></div>'
                        f'<h2>{esc(page.get("pains_title", "常见的卡点"))}</h2><ul>'
                        + "".join(f"<li>{esc(p)}</li>" for p in page["pains"]) + "</ul>"))
    for f in feats:
        n += 1
        body = (f'<div class="top"><span>{esc(promo["product"])}</span><b>{n:02d} / {total:02d}</b></div>'
                f'{ttl("h2", f["title"], 72)}<p>{esc(f.get("desc", ""))}</p>')
        if f.get("proof"):
            body += f'<div class="proof">{esc(f["proof"])}</div>'
        if f.get("shot"):
            body += f'<div class="shot">{window(src(f["shot"], prefer_crop=True))}</div>'
        docs.append(doc(body))
    n += 1
    steps = "".join(f"<li>{esc(s)}</li>" for s in page.get("steps") or [])
    docs.append(doc(f'<div class="top"><span>{esc(promo["product"])}</span><b>{n:02d} / {total:02d}</b></div>'
                    f'<div class="mid">{ttl("h1", page.get("closing", promo["message"]), 96)}<div class="rule"></div>'
                    f'<span class="pill">{esc(cta["text"])}<b>{esc(cta.get("action", ""))}</b></span></div>'
                    + (f"<ol>{steps}</ol>" if steps else "")))
    return docs


# ---------- 宣传视频 ----------

def frame_doc(promo: dict, beat: dict, size: tuple[int, int], index: int, total: int, img: Path | None) -> str:
    t, cta = theme(promo), promo["cta"]
    w, h = size
    vertical = h > w
    unit = w / 100 if vertical else h / 100
    css = base_css(t) + f"""
body{{width:{w}px;height:{h}px;overflow:hidden;}}
.f{{position:absolute;inset:0;padding:{unit * (10 if vertical else 8):.0f}px {unit * (8 if vertical else 10):.0f}px
{unit * (16 if vertical else 20):.0f}px;display:flex;flex-direction:column;justify-content:center;}}
.brand{{position:absolute;top:{unit * 6:.0f}px;left:{unit * (8 if vertical else 10):.0f}px;font-size:{unit * 2.6:.0f}px;
letter-spacing:.2em;color:var(--muted);}}
.prog{{position:absolute;left:0;bottom:0;height:{max(4, unit * .5):.0f}px;background:var(--accent);width:{index / total * 100:.2f}%;}}
h1{{margin:0;font-weight:300;line-height:1.18;font-size:{unit * (10.5 if vertical else 9):.0f}px;}}
h2{{margin:0;font-weight:300;line-height:1.25;font-size:{unit * (6.4 if vertical else 5.6):.0f}px;}}
.sub{{margin-top:{unit * 3:.0f}px;font-size:{unit * (3.6 if vertical else 3.2):.0f}px;color:var(--muted);line-height:1.6;}}
.rule{{width:{unit * 8:.0f}px;height:{max(4, unit * .6):.0f}px;background:var(--accent);margin-bottom:{unit * 4:.0f}px;}}
.big{{font-size:{unit * (16 if vertical else 14):.0f}px;font-weight:300;color:var(--accent);line-height:1.1;margin:0;}}
.shotwrap{{margin-top:{unit * 5:.0f}px;display:flex;justify-content:center;}}
.pill{{font-size:{unit * 3.4:.0f}px;margin-top:{unit * 5:.0f}px;align-self:flex-start;}}
"""
    text_w = w - 2 * unit * (8 if vertical else 10)
    h1_px, h2_px, big_px = (unit * (10.5 if vertical else 9), unit * (6.4 if vertical else 5.6),
                            unit * (16 if vertical else 14))

    def ttl(tag: str, text: str, base: float, attrs: str = "") -> str:
        return f'<{tag} {attrs}style="font-size:{fit_px(text, base, text_w)}px">{br(text)}</{tag}>'

    kind = beat["type"]
    body = [f'<div class="brand">{esc(promo["product"])}</div>']
    if kind == "hook":
        body.append('<div class="rule"></div>' + ttl("h1", beat.get("title", ""), h1_px))
    elif kind == "pain":
        body.append('<div class="rule"></div>' + ttl("h2", beat.get("title", ""), h2_px))
    elif kind == "proof":
        body.append(ttl("p", beat.get("title", ""), big_px, 'class="big" '))
    elif kind == "cta":
        body.append(ttl("h1", beat.get("title") or cta["text"], h1_px) +
                    f'<span class="pill">{esc(promo["product"])}<b>{esc(cta.get("action", ""))}</b></span>')
    elif kind == "screen":
        body.append(ttl("h2", beat.get("title", ""), h2_px))
    if beat.get("sub") and kind != "screen":
        body.append(f'<div class="sub">{esc(beat["sub"])}</div>')
    if img is not None:
        from PIL import Image
        with Image.open(img) as im:
            aspect = im.width / im.height
        max_w, max_h = w * (0.86 if vertical else 0.74), h * (0.5 if vertical else 0.54)
        win_w = min(max_w, max_h * aspect)
        body.append(f'<div class="shotwrap">{window(img.resolve().as_uri(), f"style=width:{win_w:.0f}px")}</div>')
        if beat.get("sub"):
            body.append(f'<div class="sub">{esc(beat["sub"])}</div>')
    body.append('<div class="prog"></div>')
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>'
            f'<body class="glow"><div class="f">{"".join(body)}</div></body></html>')


def validate_beats(promo: dict, beats: list[dict]) -> None:
    ids = {s.get("id") for s in promo.get("shots", [])}
    for i, b in enumerate(beats, 1):
        if b.get("type") not in BEAT_TYPES:
            raise PromoError(f"第 {i} 个镜头的 type 无效：{b.get('type')}")
        if b["type"] == "screen" and b.get("shot") not in ids:
            raise PromoError(f"第 {i} 个镜头引用了不存在的画面：{b.get('shot')}")


def build_video(promo: dict, report: dict, name: str, out: Path, work: Path, *, engine: str, voice: str | None,
                rate: str | None, bgm: str | None, assemble: bool = True) -> dict:
    spec = (promo.get("videos") or {}).get(name)
    if not spec:
        raise PromoError(f"promo.json 里没有名为 {name} 的视频")
    m = re.fullmatch(r"(\d{3,4})x(\d{3,4})", str(spec.get("size", "")))
    if not m:
        raise PromoError(f"视频 {name} 的 size 应为 宽x高")
    size = (int(m.group(1)), int(m.group(2)))
    beats = spec.get("beats") or []
    validate_beats(promo, beats)
    if assemble:
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool):
                raise PromoError(f"未找到 {tool}，请先安装并加入 PATH")
    work.mkdir(parents=True, exist_ok=True)
    vertical = size[1] > size[0]

    frames: list[list[Path]] = []
    jobs = []
    for i, beat in enumerate(beats, 1):
        imgs: list[Path | None] = [None]
        if beat["type"] == "screen":
            s = shot(promo, report, beat["shot"])
            imgs = [s["image"]]
            if beat.get("zoom") and s["crop"] and s["crop"].is_file():
                imgs.append(s["crop"])
            elif beat.get("zoom") and s["bbox"] and s["viewport"] and s["source"] == "web":
                crop = work / f"zoom-{i:02d}.png"
                zoom_crop(s["image"], s["bbox"], s["scale"], s["viewport"], (4, 3) if vertical else (16, 10), crop)
                imgs.append(crop)
        paths = []
        for j, img in enumerate(imgs):
            doc = work / f"frame-{i:02d}-{j}.html"
            doc.write_text(frame_doc(promo, beat, size, i, len(beats), img), encoding="utf-8")
            png = work / f"frame-{i:02d}-{j}.png"
            jobs.append((doc, png, size, False))
            paths.append(png)
        frames.append(paths)
    render_files(jobs)

    shots, cues, timeline, wavs, t, voiced = [], [], [], [], 0.0, False
    for i, (beat, paths) in enumerate(zip(beats, frames), 1):
        wav = work / f"a{i:02d}.wav"
        say = (beat.get("say") or "").strip()
        if say and assemble:
            mp3, srt = work / f"a{i:02d}.mp3", work / f"a{i:02d}.srt"
            cmd = [sys.executable, str(SHARED / "tts.py"), "speak", "-t", say, "-o", str(mp3),
                   "--subtitle", str(srt), "--engine", engine]
            cmd += ["--voice", voice] if voice else []
            cmd += ["--rate", rate] if rate else []
            run_retry(cmd)
            speech = probe(mp3)
            dur = round(max(speech + TAIL_PAD, float(beat.get("duration") or 0)), 3)
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-af", f"apad=whole_dur={dur}", "-t", str(dur),
                  "-ar", "44100", "-ac", "2", str(wav)])
            cues += [(t + a, t + min(b, speech), txt) for a, b, txt in (parse_srt(srt) or [(0.0, speech, say)])]
            voiced = True
        else:
            dur = float(beat.get("duration") or DEFAULT_DURATION[beat["type"]])
            if assemble:
                _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                      "-t", str(dur), str(wav)])
        wavs.append(wav)
        if len(paths) == 2:
            first = round(dur * 0.45, 3)
            shots += [{"image": str(paths[0].resolve()), "duration": first},
                      {"image": str(paths[1].resolve()), "duration": round(dur - first, 3)}]
        else:
            shots.append({"image": str(paths[0].resolve()), "duration": dur})
        timeline.append({"id": f"{i:02d}-{beat['type']}", "start": round(t, 3), "duration": dur, "say": say})
        t += dur

    warnings = []
    if vertical and timeline and timeline[0]["duration"] > 3.5:
        warnings.append("竖版开头镜头超过 3.5 秒，钩子可能太慢")
    if not voiced and not bgm:
        warnings.append("没有口播也没有背景音乐，成片将是静音")
    storyboard: dict = {"size": f"{size[0]}x{size[1]}", "image_motion": "ken-burns", "shots": shots}
    if voiced:
        listing = work / "audio-list.txt"
        listing.write_text("".join(f"file '{w.resolve().as_posix()}'\n" for w in wavs), encoding="utf-8")
        narration = work / "narration.wav"
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(narration)])
        srt_path = work / "subtitles.srt"
        srt_path.write_text("".join(f"{n}\n{_ts(a)} --> {_ts(b)}\n{txt}\n\n" for n, (a, b, txt) in enumerate(cues, 1)),
                            encoding="utf-8")
        storyboard.update(narration=str(narration.resolve()), subtitle=str(srt_path.resolve()))
    if bgm:
        storyboard["bgm"] = str(Path(bgm).resolve())
    sb_path = work / "storyboard.json"
    sb_path.write_text(json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"output": out.as_posix(), "duration": round(t, 2), "shots": len(shots), "timeline": timeline,
              "warnings": warnings, "storyboard": sb_path.as_posix()}
    (work / "timeline.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if assemble:
        proc = subprocess.run([sys.executable, str(ASSEMBLE), "assemble", "--resume", "--storyboard", str(sb_path),
                               "-o", str(out)])
        if proc.returncode != 0:
            raise PromoError(f"assemble.py 合成失败（退出码 {proc.returncode}）")
    return result


# ---------- 命令行 ----------

def _selftest() -> int:
    from PIL import Image

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        img = root / "home.png"
        Image.new("RGB", (1600, 900), "#d9d9d9").save(img)
        promo = {"product": "Demo", "title": "Demo 介绍", "message": "一句核心信息", "audience": "创作者",
                 "cta": {"text": "立即试用", "action": "demo.dev"}, "shots": [{"id": "home"}],
                 "page": {"hero": {"title": "从想法到发布", "shot": "home"}, "pains": ["痛点一", "痛点二"],
                          "features": [{"title": "功能一", "desc": "说明", "shot": "home", "proof": "事实"}],
                          "proof": [{"value": "10", "label": "个平台", "source": "README"}], "steps": ["安装"]},
                 "videos": {"short": {"size": "540x960", "beats": [
                     {"type": "hook", "title": "钩子"}, {"type": "screen", "title": "功能一", "shot": "home", "zoom": True},
                     {"type": "proof", "title": "10 个平台", "sub": "来源：README"}, {"type": "cta"}]}}}
        report = {"viewport": {"width": 800, "height": 450}, "scale": 2,
                  "steps": {"home": {"status": "ok", "image": img.as_posix(), "source": "web",
                                     "bbox": {"x": 300, "y": 200, "width": 120, "height": 40}}}}
        page = page_html(promo, report, root)
        docs = card_docs(promo, report)
        checks = {"page": "从想法到发布" in page and 'src="home.png"' in page and "来源：README" in page,
                  "cards": len(docs) == 4,
                  "fit_short": fit_px("短标题", 96, 912) == 96,
                  "fit_long": fit_px("一个会记住你账号的内容搭档，\n从想法到发布都在一处", 96, 912) < 96,
                  "fit_two_lines": fit_px("一" * 30, 96, 912) * 30 <= 912 * 2}
        cards = []
        for i, doc in enumerate(docs[:2], 1):
            p = root / f"c{i}.html"
            p.write_text(doc, encoding="utf-8")
            cards.append((p, root / f"c{i}.png", (CARD_W, CARD_H), False))
        render_files(cards)
        with Image.open(root / "c1.png") as c:
            checks["card_size"] = c.size == (CARD_W, CARD_H)
        res = build_video(promo, report, "short", root / "x.mp4", root / "v", engine="edge", voice=None, rate=None,
                          bgm=None, assemble=False)
        checks["video_shots"] = res["shots"] == 5 and abs(res["duration"] - 10.6) < 0.01
        checks["silent_warn"] = any("静音" in w for w in res["warnings"])
        with Image.open(root / "v" / "frame-02-1.png") as f:
            checks["frame_size"] = f.size == (540, 960)
        try:
            validate_beats(promo, [{"type": "screen", "shot": "nope"}])
            checks["bad_shot"] = False
        except PromoError:
            checks["bad_shot"] = True
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        print(f"[FAIL] {', '.join(bad)}", file=sys.stderr)
        return 1
    print("[PASS] build_promo 自检通过（落地页、卡片、视频分镜与画面、校验）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="宣传计划 → 落地页 / 小红书卡片 / 宣传视频")
    ap.add_argument("--selftest", action="store_true", help="运行自检")
    sub = ap.add_subparsers(dest="cmd")
    for name, help_text in (("page", "落地页 HTML（可加长图）"), ("cards", "小红书卡片组 1080×1440"), ("video", "宣传视频")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--promo", required=True, help="promo.json 路径")
        p.add_argument("--report", help="capture-report.json（默认 promo 同级 shots/）")
        if name == "page":
            p.add_argument("-o", "--output", required=True, help="输出 HTML，放在 outputs/<主题>/ 根目录")
            p.add_argument("--long-image", action="store_true", help="同时输出同名“-长图.png”（宽 1080）")
        elif name == "cards":
            p.add_argument("--out-dir", required=True, help="卡片输出目录，通常是 outputs/<主题>")
            p.add_argument("--prefix", default="卡片", help="文件名前缀（默认 卡片）")
        else:
            p.add_argument("--name", required=True, help="promo.json 中 videos 的键，如 short / intro")
            p.add_argument("-o", "--output", required=True, help="输出 mp4，放在 outputs/<主题>/ 根目录")
            p.add_argument("--work-dir", help="中间文件目录（默认 promo 同级 video-<name>/）")
            p.add_argument("--engine", choices=["edge", "closed", "auto"], default="edge",
                           help="配音引擎，默认 edge（免费）；closed 为按量计费的闭源音色")
            p.add_argument("--voice", help="音色 ID")
            p.add_argument("--rate", help="语速，如 +8%%")
            p.add_argument("--bgm", help="背景音乐文件（可选，有口播时自动压低）")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.cmd:
        ap.error("需要子命令 page / cards / video（或 --selftest）")
    promo_path = Path(args.promo)
    promo = json.loads(promo_path.read_text(encoding="utf-8"))
    report = load_report(promo_path, args.report)
    try:
        if args.cmd == "page":
            out = validate_output_path(args.output, create_parent=True)
            out.write_text(page_html(promo, report, out.parent), encoding="utf-8")
            print(f"✅ {out}")
            if args.long_image:
                png = out.with_name(out.stem + "-长图.png")
                render_files([(out, png, (1080, 1600), True)])
                print(f"✅ {png}")
        elif args.cmd == "cards":
            out_dir = Path(args.out_dir)
            validate_output_path(out_dir / f"{args.prefix}_01.png", create_parent=True)
            work = promo_path.parent / "cards"
            work.mkdir(parents=True, exist_ok=True)
            jobs = []
            for i, doc in enumerate(card_docs(promo, report), 1):
                p = work / f"card-{i:02d}.html"
                p.write_text(doc, encoding="utf-8")
                jobs.append((p, out_dir / f"{args.prefix}_{i:02d}.png", (CARD_W, CARD_H), False))
            render_files(jobs)
            for _, png, _, _ in jobs:
                print(f"✅ {png.as_posix()}")
        else:
            out = validate_output_path(args.output, create_parent=True)
            work = Path(args.work_dir) if args.work_dir else promo_path.parent / f"video-{args.name}"
            validate_output_path(work / "storyboard.json", create_parent=True)
            res = build_video(promo, report, args.name, out, work, engine=args.engine, voice=args.voice,
                              rate=args.rate, bgm=args.bgm)
            print(f"✅ {res['output']}（约 {res['duration']} 秒，{res['shots']} 个镜头）")
            print(f"时间线：{(work / 'timeline.json').as_posix()}")
            for w in res["warnings"]:
                print(f"WARN: {w}")
    except PromoError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
