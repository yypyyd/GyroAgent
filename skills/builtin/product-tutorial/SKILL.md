---
name: product-tutorial
description: >-
  软件产品教程：基于 product-brief 的事实，按计划操作真实产品截图（高亮目标、密钥打码、终端截图），
  生成图文教程（Markdown/HTML/PDF）和配音字幕教程视频，并渲染回看自检。当用户说“做产品教程”“使用教程”
  “上手指南”“操作手册”“教程视频”“录个教程”“新手引导文档”时使用。
  泛主题口播短视频用 auto-short-video，已有原片的整片包装用 video-production。
layer: produce
---

# 产品教程（product-tutorial）

> 读者照着做，一次就成功。教程只用真实界面和真实命令输出；截图、文档、视频都从同一份 `plan.json` 生成，三者天然一致。

**运行环境**：所有命令都在 Easel 项目根目录（含 `.env` 和 `skills/shared/scripts/` 的目录）执行，Python 用项目自己的
`.venv`（Windows：`.venv\Scripts\python.exe`），不要用其他环境的 Python。视频需要 `ffmpeg`/`ffprobe` 在 PATH 中。

## 输入

- 产品简报 `outputs/<主题>/brief.md`（没有就先走 [product-brief](../product-brief/SKILL.md)，不要跳过）
- 产品访问方式：运行中的地址（如本机工作台）、可执行的命令；需要登录时提供已登录的 `storage_state`
- 交付形态：文档、视频或两者；**要视频时必须确认画幅**（横版 1920×1080 或竖版 1080×1920），未明确前不得开工
- 可选：配音音色、背景音乐、目标时长

## 输出

`outputs/<主题>/`（主题用人类可读名，如 `Easel上手教程`）：

- 成品（项目根）：`<标题>.md`、`<标题>.html`、可选 `<标题>.pdf`、`<标题>.mp4`
- 中间件（`assets/`）：`plan.json`、`shots/`（截图与 `capture-report.json`）、`video/`（配音、字幕、时间线）、`review/`（审阅图）

## 执行步骤

1. **读简报，定读者与结果**。从 `brief.md` 取目标读者、核心任务、术语和限制。
   按 [tutorial-writing.md](references/tutorial-writing.md) 第一节确定读者起点、可检验的结果和最短路径。
   只写简报里「已验证」或「有文档」的功能；「推断」项不进教程。

2. **写 plan.json**。按 [plan-schema.md](references/plan-schema.md) 写到 `outputs/<主题>/assets/plan.json`：
   一个章节对应一个读者任务，每步写 `actions`、`highlight`、`text`、`result`、`say`。
   选择器优先用界面原文（`text=新建画像`）或 `role=`，从前端源码确认原文。
   涉及密钥、手机号、账号信息的区域写进 `mask`。会产生副作用的操作（真实发布、付费、删除）不写进动作，
   改为纯文字步骤并在 `tip` 里说明。

3. **采集真实截图**：
   ```bash
   python skills/builtin/product-tutorial/scripts/capture.py --plan outputs/<主题>/assets/plan.json
   ```
   某步失败时脚本停下，报告里有错误和调试截图 `<id>-error.png`。看调试截图修正选择器或补等待动作后重跑整份计划。
   连续两次修不好就停下，告诉用户卡在哪一步。需要调试时加 `--headed` 看浏览器操作过程。
   命令行步骤先真实运行命令，把输出粘进 `terminal.output`，不能手写理想输出。

4. **生成图文教程**：
   ```bash
   python skills/builtin/product-tutorial/scripts/build_doc.py --plan outputs/<主题>/assets/plan.json \
     -o "outputs/<主题>/<标题>.md" --html
   ```
   要 PDF 加 `--pdf`。有步骤缺截图时脚本直接失败，回到第 3 步补齐。

5. **生成教程视频**（需要时）。配音默认免费的 edge 音色；用闭源音色前先按 AGENTS 的媒体模型规则
   查询已配置的 TTS，向用户说明会按量计费，确认后再加 `--engine closed --voice <音色>`：
   ```bash
   python skills/builtin/product-tutorial/scripts/build_video.py --plan outputs/<主题>/assets/plan.json \
     -o "outputs/<主题>/<标题>.mp4" --size 1920x1080
   ```
   脚本会生成封面、章节卡、片尾卡，逐段配音并对齐字幕。有高亮的步骤先给全图再推近目标，最后交给
   [auto-short-video](../auto-short-video/SKILL.md) 的合成器出片。竖版用 `--size 1080x1920`，截图会自动补边。
   输出里的 `WARN` 要逐条处理，例如高亮目标在画面底部、可能被字幕挡住。

6. **渲染回看自检**（必做，不能只看脚本退出码）：
   ```bash
   python skills/builtin/product-tutorial/scripts/review.py doc --html "outputs/<主题>/<标题>.html" \
     -o outputs/<主题>/assets/review
   python skills/builtin/product-tutorial/scripts/review.py video -i "outputs/<主题>/<标题>.mp4" \
     -o outputs/<主题>/assets/review --timeline outputs/<主题>/assets/video/timeline.json --size 1920x1080
   ```
   出了 PDF 就给 `doc` 加 `--pdf "outputs/<主题>/<标题>.pdf"`，PDF 的逐页图也要看。
   机器检查 FAIL 就不交付。机器检查通过后，**逐张打开** `page-NN.png`、`pdf-NN.png` 和 `contact-sheet.png`，
   对照 tutorial-writing.md 第六节的清单检查。发现问题就改 `plan.json`，重跑受影响的脚本，只返工一次。

7. **交付与登记**。把成品贴进对话：图片用 `/api/media/<outputs 相对路径>` 的 Markdown 图片，
   视频用 `<video src="/api/media/<outputs 相对路径>" controls style="max-width:560px">`。
   附一句制作说明：覆盖了哪些任务，哪些步骤因为需要登录或有副作用改成了纯文字。然后登记：
   ```bash
   python skills/shared/scripts/manifest.py meta --topic <主题> --title "<标题>" --kind article \
     --status ready --cover assets/shots/<首个步骤 id>.png --deliverables "<标题>.md,<标题>.html,<标题>.mp4"
   ```
   跨层编排时再用 `manifest.py record --layer produce --skill product-tutorial` 登记产物路径。

## 红线

- 不用 AI 生图冒充界面截图，不手写命令输出，不描述产品没有的功能。
- 截图和口播里不得出现密钥、内部地址、本机绝对路径和真实个人信息；`review.py` 报敏感信息时必须处理。
- 不替用户在产品里真实发布、付费或删除数据；这些步骤只写文字说明。
- 画幅未确认不出视频；闭源配音未经用户确认不调用。

## Profile 感知

- 有 Profile：口播语气取 `style.md`，读者设定参考 `audience.md`，严格遵守 `preferences.md` 红线。
- 无 Profile：用简报里的目标读者，语气用平实的第二人称。
