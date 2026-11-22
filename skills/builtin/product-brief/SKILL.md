---
name: product-brief
description: >-
  产品简报：从源码仓库、文档、官网或正在运行的产品里提炼带证据的事实清单，明确目标读者、核心任务、术语、
  限制和价值主张，写成 brief.md 供教程、宣传、脚本下游复用。当用户说“了解一下这个产品”“先摸清产品”
  “产品简报”“做教程/宣传之前先整理产品信息”或要为某个软件做教程、宣传时先使用。
  只整理事实不出成品；教程成品用 product-tutorial，泛主题选题用 skill-topic-evaluator。
layer: plan
---

# 产品简报（product-brief）

> 先搞懂产品，再动笔。简报里的每一条事实都要能指回源码、文档或截图；下游技能只从这里取事实，避免编造功能。

**运行环境**：所有命令都在 Easel 项目根目录（含 `.env` 和 `skills/shared/scripts/` 的目录）执行，Python 用项目自己的
`.venv`（Windows：`.venv\Scripts\python.exe`），不要用其他环境的 Python。

## 输入

- 产品来源（至少一项）：本地仓库目录、文档目录、官网/文档 URL、正在运行的产品地址
- 用途：教程、宣传或两者；目标读者（不给就从 Profile 的 `audience.md` 推断，仍不确定就问一句）
- 内容项目名：后续成品所在的 `outputs/<主题>/`，如 `outputs/Easel上手教程/`

## 输出

- `outputs/<主题>/brief.md`：按 [brief-template.md](references/brief-template.md) 结构写的产品简报
- `outputs/<主题>/assets/brief/source-inventory.md`：源码盘点（有仓库时）
- `outputs/<主题>/assets/brief/screens/`：核实用截图（产品可运行时）

## 执行步骤

1. **盘点来源**。有本地仓库就先跑盘点，拿到“该读哪些文件”的地图：
   ```bash
   python skills/builtin/product-brief/scripts/scan_source.py \
     --source "<产品仓库目录>" -o outputs/<主题>/assets/brief/source-inventory.md
   ```
   盘点不读 `.env`，配置只列示例文件里的键名。只有 URL 时，读官网和文档页面，记下 URL 作为证据。

2. **按地图精读**。依次读 README、上手类文档、包清单里的命令入口、前端页面和路由文件。
   界面文字以前端源码里的原文为准；文档和代码不一致时以代码为准，并记进「限制与差异」。
   不要通读全仓库，读到能填满模板为止。

3. **在运行中的产品里核实**（能跑就必须做）。产品已在本机运行、或按 README 可以低成本启动时，
   用 product-tutorial 的采集脚本打开关键页面截图，确认入口、按钮名和结果与源码一致：
   ```bash
   python skills/builtin/product-tutorial/scripts/capture.py \
     --plan outputs/<主题>/assets/brief/verify-plan.json --out-dir outputs/<主题>/assets/brief/screens
   ```
   计划文件格式见 [plan-schema.md](../product-tutorial/references/plan-schema.md)，核实时只需 `goto` 加截图。
   需要登录、付费或真实发布的步骤不要替用户操作，标为「有文档」并写明原因。
   CLI 产品就实际跑 `--help` 和一条无副作用的命令，把真实输出作为证据。

4. **写 brief.md**。按模板逐节填写。每条功能事实标「已验证 / 有文档 / 推断」三种状态之一；
   推断项只能放进「待确认」。核心任务写读者想完成的事（如“发出第一篇笔记”），不写功能名。

5. **自检**。对照模板末尾的自检清单逐条检查：事实都有证据、卖点不引用推断项、没有密钥和内部地址。
   不通过就补证据或删掉那条，不要靠措辞模糊过去。

6. **确认与交接**。把一句话定义、核心任务和「待确认」问题（最多 3 个）发给用户确认。
   跨层编排时登记，供下游读取：
   ```bash
   python skills/shared/scripts/manifest.py record --topic <主题> --layer plan --skill product-brief \
     --outputs brief.md --summary "<一句话定义> · <N> 个核心任务 · 已验证 <M> 项"
   ```

## 红线

- 没有证据的功能不写进事实表；不编数字、版本、用户量和竞品对比。
- 不读取、不回显 `.env` 和任何密钥；截图里出现密钥或个人信息时，重拍时用计划里的 `mask` 打码。
- 只读核实，不在产品里做会产生副作用的操作（发布、删除、付费、改配置）。

## Profile 感知

- 有 Profile：用 `identity.md` 确定品牌与产品名写法，用 `audience.md` 预填目标读者，遵守 `preferences.md` 红线。
- 无 Profile：目标读者按来源推断后向用户确认一句。
