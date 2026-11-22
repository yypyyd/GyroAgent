#!/usr/bin/env python3
"""build_doc.py — 由教程计划 + 采集报告生成图文教程（Markdown，可再转 HTML/PDF）。

用法：
    build_doc.py --plan outputs/<主题>/assets/plan.json -o outputs/<主题>/<标题>.md --html --pdf
    build_doc.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SHARED = Path(__file__).resolve().parents[3] / "shared" / "scripts"
sys.path.insert(0, str(SHARED))
from output_paths import validate_output_path  # noqa: E402

CN_NUM = "一二三四五六七八九十"


def _section_no(i: int) -> str:
    return CN_NUM[i] if i < len(CN_NUM) else str(i + 1)


def load_report(plan_path: Path, report_path: str | None) -> dict:
    path = Path(report_path) if report_path else plan_path.parent / "shots" / "capture-report.json"
    if not path.is_file():
        return {"steps": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def step_image(step: dict, report: dict, *, prefer_crop: bool) -> str | None:
    if step.get("image"):
        return step["image"]
    entry = report.get("steps", {}).get(step["id"], {})
    if entry.get("status") == "failed":
        return None
    if prefer_crop and step.get("crop", bool(step.get("terminal"))) and entry.get("image_crop"):
        return entry["image_crop"]
    return entry.get("image")


def needs_image(step: dict) -> bool:
    return step.get("shot") is not False


def render(plan: dict, report: dict, out_dir: Path) -> tuple[str, list[str]]:
    missing: list[str] = []
    lines = [f"# {plan['title']}", "", f"> {plan['outcome']}", ""]
    if plan.get("audience"):
        lines += [f"**适合谁**：{plan['audience']}", ""]
    if plan.get("prerequisites"):
        lines += ["## 开始之前", ""] + [f"- {item}" for item in plan["prerequisites"]] + [""]
    sections = plan.get("sections", [])
    if len(sections) > 1:
        lines += ["## 你将完成", ""]
        lines += [f"{i + 1}. {s['title']}" + (f"：{s['goal']}" if s.get("goal") else "") for i, s in enumerate(sections)]
        lines.append("")
    for si, section in enumerate(sections):
        lines += [f"## {_section_no(si)}、{section['title']}", ""]
        if section.get("goal"):
            lines += [section["goal"], ""]
        for n, step in enumerate(section.get("steps", []), 1):
            if step.get("tip"):
                lines += [f"> **注意**：{step['tip']}", ""]
            lines += [f"**第 {n} 步**　{step['text']}", ""]
            if needs_image(step):
                img = step_image(step, report, prefer_crop=True)
                if img and Path(img).is_file():
                    rel = os.path.relpath(Path(img).resolve(), out_dir.resolve()).replace(os.sep, "/")
                    alt = re.sub(r"[*_`\[\]]", "", step.get("result") or step["text"])
                    lines += [f"![{alt}]({rel})", ""]
                else:
                    missing.append(step["id"])
            if step.get("result"):
                lines += [f"**结果**：{step['result']}", ""]
    if plan.get("faq"):
        lines += ["## 常见问题", ""]
        for item in plan["faq"]:
            lines += [f"### {item['q']}", "", item["a"], ""]
    if plan.get("next"):
        lines += ["## 下一步", ""] + [f"- {item}" for item in plan["next"]] + [""]
    return "\n".join(lines).rstrip() + "\n", missing


def convert(md: Path, target: Path, title: str) -> int:
    cmd = [sys.executable, str(SHARED / "doc_convert.py"), "convert", "-i", str(md), "-o", str(target),
           "--title", title, "--page-width", "860"]
    return subprocess.run(cmd).returncode


def _selftest() -> int:
    from PIL import Image

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        shots = root / "assets" / "shots"
        shots.mkdir(parents=True)
        Image.new("RGB", (320, 180), "#ddd").save(shots / "a.png")
        Image.new("RGB", (160, 90), "#bbb").save(shots / "a-crop.png")
        report = {"steps": {"a": {"status": "ok", "image": (shots / "a.png").as_posix(),
                                  "image_crop": (shots / "a-crop.png").as_posix()},
                            "b": {"status": "failed"}}}
        plan = {"title": "测试教程", "outcome": "学会测试", "audience": "新人", "prerequisites": ["装好"],
                "sections": [{"title": "第一件事", "goal": "做完", "steps": [
                    {"id": "a", "crop": True, "text": "点击 **开始**", "result": "出现面板", "tip": "先保存"},
                    {"id": "b", "text": "失败的步骤"},
                    {"id": "c", "shot": False, "text": "纯文字"}]}],
                "faq": [{"q": "问", "a": "答"}], "next": ["继续"]}
        md, missing = render(plan, report, root)
        checks = {
            "crop_used": "assets/shots/a-crop.png" in md,
            "missing": missing == ["b"],
            "tip_before_step": md.index("**注意**") < md.index("**第 1 步**"),
            "sections": "## 一、第一件事" in md and "## 常见问题" in md and "## 下一步" in md,
        }
    bad = [k for k, v in checks.items() if not v]
    if bad:
        print(f"[FAIL] {', '.join(bad)}", file=sys.stderr)
        return 1
    print("[PASS] build_doc 自检通过")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="教程计划 → 图文教程")
    ap.add_argument("--plan", help="plan.json 路径")
    ap.add_argument("--report", help="capture-report.json（默认 plan 同级 shots/）")
    ap.add_argument("-o", "--output", help="输出 Markdown，放在 outputs/<主题>/ 根目录")
    ap.add_argument("--html", action="store_true", help="同时输出同名 .html")
    ap.add_argument("--pdf", action="store_true", help="同时输出同名 .pdf")
    ap.add_argument("--allow-missing", action="store_true", help="允许有步骤缺截图（默认缺图即失败）")
    ap.add_argument("--selftest", action="store_true", help="运行自检")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.plan or not args.output:
        ap.error("需要 --plan 与 -o（或 --selftest）")
    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    out = validate_output_path(args.output, create_parent=True)
    md, missing = render(plan, load_report(plan_path, args.report), out.parent)
    if missing and not args.allow_missing:
        print(f"ERROR: 以下步骤缺截图（采集失败或未采集）：{', '.join(missing)}。先修好 capture 再生成。",
              file=sys.stderr)
        return 2
    out.write_text(md, encoding="utf-8")
    print(f"✅ {out}（{len(md)} 字符）")
    if missing:
        print(f"WARN: 缺截图步骤：{', '.join(missing)}")
    rc = 0
    for flag, suffix in ((args.html, ".html"), (args.pdf, ".pdf")):
        if flag:
            rc = rc or convert(out, out.with_suffix(suffix), plan["title"])
    return rc


if __name__ == "__main__":
    sys.exit(main())
