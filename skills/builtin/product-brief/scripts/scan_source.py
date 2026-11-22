#!/usr/bin/env python3
"""scan_source.py — 产品源码/文档的确定性盘点，给 product-brief 提供“该读哪些文件”的地图。

只做盘点，不下结论：列出 README/文档标题、包清单与命令入口、Web 路由、前端页面、
配置项名称（只取 .env.example 的键名，绝不读 .env）和最近版本记录，并给出证据路径。

用法：
    scan_source.py --source <产品仓库目录> -o outputs/<主题>/assets/brief/source-inventory.md
    scan_source.py --source <目录> -o ... --json outputs/<主题>/assets/brief/source-inventory.json
    scan_source.py --selftest
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared" / "scripts"))
from output_paths import validate_output_path  # noqa: E402

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build",
    "outputs", ".next", ".nuxt", "coverage", ".pytest_cache", ".mypy_cache", "vendor", "site-packages",
    ".idea", ".vscode", "target", ".turbo", ".cache",
}
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".go", ".rs"}
MAX_FILES = 6000
MAX_BYTES = 400_000

ROUTE_PATTERNS = [
    ("http", re.compile(r"@(?:app|router|api|bp|blueprint)\.(get|post|put|patch|delete|route)\(\s*[\"']([^\"']+)")),
    ("http", re.compile(r"\b(?:app|router)\.(get|post|put|patch|delete)\(\s*[\"'`]([^\"'`]+)")),
    ("page", re.compile(r"<Route\b[^>]*\bpath=[\"']([^\"']+)")),
    ("page", re.compile(r"\bpath:\s*[\"'`](/[^\"'`]*)")),
]
CLI_PATTERNS = [
    re.compile(r"add_parser\(\s*[\"']([\w-]+)[\"']"),
    re.compile(r"@(?:\w+\.)?command\(\s*(?:name=)?[\"']([\w-]+)[\"']"),
]
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def _walk(root: Path):
    count = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    stack.append(entry)
                continue
            count += 1
            if count > MAX_FILES:
                return
            yield entry


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _headings(text: str, limit: int = 14) -> list[str]:
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else HEADING_RE.match(line)
        if m:
            out.append("  " * (len(m.group(1)) - 1) + m.group(2))
            if len(out) >= limit:
                break
    return out


def _manifests(root: Path) -> list[dict]:
    found = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            proj = data.get("project", {})
            found.append({
                "file": "pyproject.toml", "name": proj.get("name"), "version": proj.get("version"),
                "description": proj.get("description"),
                "entry_points": sorted((proj.get("scripts") or {}).items()),
            })
        except Exception as exc:  # noqa: BLE001
            found.append({"file": "pyproject.toml", "error": str(exc)})
    for pkg in sorted(root.glob("**/package.json")):
        rel = pkg.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or len(rel.parts) > 4:
            continue
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        bins = data.get("bin")
        found.append({
            "file": rel.as_posix(), "name": data.get("name"), "version": data.get("version"),
            "description": data.get("description"),
            "entry_points": sorted(bins.items()) if isinstance(bins, dict) else ([("bin", bins)] if bins else []),
            "scripts": sorted((data.get("scripts") or {}).keys()),
        })
    for name in ("Cargo.toml", "go.mod"):
        if (root / name).is_file():
            found.append({"file": name, "head": (root / name).read_text(encoding="utf-8", errors="ignore")[:300]})
    return found


def _entry_files(manifests: list[dict]) -> set[str]:
    """命令入口指向的源文件（pyproject 的 module:func、package.json 的 bin）。"""
    files: set[str] = set()
    for m in manifests:
        base = Path(m["file"]).parent
        for _, target in m.get("entry_points") or []:
            target = str(target)
            if ":" in target and "/" not in target:
                mod = target.split(":", 1)[0].replace(".", "/")
                files.update({f"{mod}.py", f"src/{mod}.py", f"{mod}/__init__.py", f"{mod}/__main__.py"})
            else:
                rel = (base / target).as_posix()
                files.add(rel)
                stem = re.sub(r"^(dist|build|lib)/", "src/", rel)
                files.update({re.sub(r"\.js$", ext, stem) for ext in (".ts", ".js", ".mjs")})
    return files


def _env_keys(root: Path) -> list[str]:
    keys = []
    for name in (".env.example", ".env.sample", ".env.template", "env.example"):
        path = root / name
        if path.is_file():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r"\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", line)
                if m and m.group(1) not in keys:
                    keys.append(m.group(1))
    return keys


def _changelog(root: Path) -> list[str]:
    for name in ("CHANGELOG.md", "CHANGES.md", "HISTORY.md"):
        path = root / name
        if path.is_file():
            return [h.strip() for h in _headings(_read(path), 40) if re.search(r"\d+\.\d+", h)][:6]
    return []


def scan(root: Path) -> dict:
    root = root.resolve()
    readmes, docs, routes, clis, pages = [], [], [], [], []
    for path in _walk(root):
        rel = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        if suffix in {".md", ".mdx", ".rst"}:
            text = _read(path)
            item = {"path": rel, "lines": text.count("\n") + 1, "headings": _headings(text)}
            if path.stem.lower().startswith("readme") and len(path.relative_to(root).parts) <= 2:
                readmes.append(item)
            elif re.search(r"(^|/)(docs?|guide|guides|manual|wiki)/", rel, re.I) or len(path.relative_to(root).parts) == 1:
                docs.append(item)
            continue
        if suffix not in CODE_SUFFIXES:
            continue
        if re.search(r"(^|/)(tests?|__tests__|fixtures|examples?)/", rel, re.I):
            continue
        if re.search(r"(Page|View|Screen)\.(tsx|jsx|vue)$", path.name) or re.search(r"/pages?/", rel):
            pages.append(rel)
        text = _read(path)
        if not text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for kind, pattern in ROUTE_PATTERNS:
                for m in pattern.finditer(line):
                    value = m.groups()[-1]
                    method = m.group(1).upper() if kind == "http" and len(m.groups()) > 1 else ""
                    routes.append({"kind": kind, "method": method, "path": value, "at": f"{rel}:{lineno}"})
            for pattern in CLI_PATTERNS:
                for m in pattern.finditer(line):
                    clis.append({"command": m.group(1), "at": f"{rel}:{lineno}"})
    seen, unique_routes = set(), []
    for r in routes:
        key = (r["kind"], r["method"], r["path"])
        if key not in seen:
            seen.add(key)
            unique_routes.append(r)
    manifests = _manifests(root)
    entries = _entry_files(manifests)
    clis.sort(key=lambda c: (c["at"].split(":")[0] not in entries, c["at"].split(":")[0],
                             int(c["at"].rsplit(":", 1)[1])))
    for c in clis:
        c["entry"] = c["at"].split(":")[0] in entries
    return {
        "source": str(root),
        "readmes": readmes,
        "docs": sorted(docs, key=lambda d: d["path"])[:80],
        "manifests": manifests,
        "cli_commands": clis,
        "routes": unique_routes[:200],
        "pages": sorted(pages)[:120],
        "env_keys": _env_keys(root),
        "changelog": _changelog(root),
    }


def to_markdown(inv: dict) -> str:
    lines = [f"# 源码盘点：{Path(inv['source']).name}", "",
             "> 由 scan_source.py 生成，只是阅读地图。写 brief 前必须打开相应文件核实，不能只凭这里的标题下结论。", ""]

    def section(title: str, rows: list[str]) -> None:
        lines.extend([f"## {title}", ""])
        lines.extend(rows or ["（未发现）"])
        lines.append("")

    rows = []
    for item in inv["readmes"]:
        rows.append(f"- `{item['path']}`（{item['lines']} 行）")
        rows.extend(f"  - {h}" for h in item["headings"])
    section("README", rows)
    rows = []
    for item in inv["docs"]:
        first = item["headings"][0].strip() if item["headings"] else ""
        rows.append(f"- `{item['path']}`（{item['lines']} 行）{('— ' + first) if first else ''}")
    section("文档", rows)
    rows = []
    for m in inv["manifests"]:
        head = " ".join(str(v) for v in (m.get("name"), m.get("version")) if v)
        rows.append(f"- `{m['file']}` {head}".rstrip())
        if m.get("description"):
            rows.append(f"  - 描述：{m['description']}")
        for name, target in m.get("entry_points") or []:
            rows.append(f"  - 命令入口：`{name}` → `{target}`")
        if m.get("scripts"):
            rows.append(f"  - npm scripts：{', '.join(m['scripts'])}")
    section("包清单与命令入口", rows)
    section("命令入口的子命令", [f"- `{c['command']}` — `{c['at']}`" for c in inv["cli_commands"] if c["entry"]])
    others: dict[str, int] = {}
    for c in inv["cli_commands"]:
        if not c["entry"]:
            file = c["at"].split(":")[0]
            others[file] = others.get(file, 0) + 1
    section("其他脚本（含子命令）", [f"- `{f}`（{n} 个子命令）" for f, n in list(others.items())[:60]]
            + ([f"- ……另有 {len(others) - 60} 个文件"] if len(others) > 60 else []))
    section("前端页面路由", [f"- `{r['path']}` — `{r['at']}`" for r in inv["routes"] if r["kind"] == "page"])
    section("前端页面文件", [f"- `{p}`" for p in inv["pages"]])
    section("HTTP 接口", [f"- {r['method'] or 'ANY'} `{r['path']}` — `{r['at']}`"
                        for r in inv["routes"] if r["kind"] == "http"])
    section("配置项（仅键名，来自示例配置）", [f"- `{k}`" for k in inv["env_keys"]])
    section("最近版本", [f"- {h}" for h in inv["changelog"]])
    return "\n".join(lines).rstrip() + "\n"


def _selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "README.md").write_text("# Demo\n## 安装\n## 使用\n", encoding="utf-8")
        (root / ".env").write_text("SECRET_TOKEN=should-not-appear\n", encoding="utf-8")
        (root / ".env.example").write_text("API_KEY=\nexport BASE_URL=http://x\n", encoding="utf-8")
        (root / "docs").mkdir()
        (root / "docs" / "guide.md").write_text("# 上手指南\n", encoding="utf-8")
        (root / "app.py").write_text("@app" + ".get('/api/items')\ndef f(): ...\n", encoding="utf-8")
        (root / "demo").mkdir()
        (root / "demo" / "cli.py").write_text("sub.add" + "_parser('run')\n", encoding="utf-8")
        (root / "tool.py").write_text("sub.add" + "_parser('misc')\n", encoding="utf-8")
        (root / "pyproject.toml").write_text("[project]\nname='demo'\n[project.scripts]\ndemo='demo.cli:main'\n",
                                             encoding="utf-8")
        (root / "web").mkdir()
        (root / "web" / "SettingsPage.tsx").write_text("<Route path=\"/settings\" />", encoding="utf-8")
        (root / "node_modules").mkdir()
        (root / "node_modules" / "x.md").write_text("# 不该出现\n", encoding="utf-8")
        inv = scan(root)
        md = to_markdown(inv)
        checks = {
            "readme": any(r["path"] == "README.md" for r in inv["readmes"]),
            "docs": any(r["path"] == "docs/guide.md" for r in inv["docs"]),
            "http": any(r["path"] == "/api/items" and r["method"] == "GET" for r in inv["routes"]),
            "page": any(r["path"] == "/settings" for r in inv["routes"]),
            "cli_entry": [c["command"] for c in inv["cli_commands"] if c["entry"]] == ["run"],
            "cli_other": "`tool.py`（1 个子命令）" in md,
            "env_keys": inv["env_keys"] == ["API_KEY", "BASE_URL"],
            "no_secret": "should-not-appear" not in md and "SECRET_TOKEN" not in md,
            "skip_dirs": "不该出现" not in md,
        }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        print(f"[FAIL] {', '.join(failed)}", file=sys.stderr)
        return 1
    print("[PASS] scan_source 自检通过")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="产品源码/文档盘点（只读，不读 .env）")
    ap.add_argument("--source", help="产品仓库或文档目录")
    ap.add_argument("-o", "--output", help="Markdown 盘点输出路径（须在 outputs/<主题>/ 下）")
    ap.add_argument("--json", help="可选：同时输出 JSON")
    ap.add_argument("--selftest", action="store_true", help="运行自检")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.source or not args.output:
        ap.error("需要 --source 与 -o（或 --selftest）")
    root = Path(args.source).expanduser()
    if not root.is_dir():
        print(f"ERROR: 源目录不存在: {root}", file=sys.stderr)
        return 2
    inv = scan(root)
    out = validate_output_path(args.output, create_parent=True)
    out.write_text(to_markdown(inv), encoding="utf-8")
    print(f"✅ {out}")
    if args.json:
        jp = validate_output_path(args.json, create_parent=True)
        jp.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ {jp}")
    print(f"README {len(inv['readmes'])} · 文档 {len(inv['docs'])} · 路由 {len(inv['routes'])} · "
          f"CLI {len(inv['cli_commands'])} · 页面 {len(inv['pages'])} · 配置键 {len(inv['env_keys'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
