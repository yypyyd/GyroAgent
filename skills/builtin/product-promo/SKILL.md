---
name: product-promo
description: >-
  软件产品宣传：基于 product-brief 的事实提炼一个核心信息，按“钩子→痛点→转折→演示→证据→行动号召”叙事，
  用真实界面截图生成宣传落地页（HTML + 长图）、小红书卡片组，以及竖版快节奏短片和横版产品介绍片。
  当用户说“做产品宣传”“宣传片”“产品介绍视频”“落地页”“推广图文”“发布宣传”时使用。
  教学性质的操作讲解用 product-tutorial；只要一张海报用 poster-hero。
layer: produce
---

# 产品宣传（product-promo）

> 教程让人"会用"，宣传让人"想用"。只讲一个核心信息，所有画面都是真实界面，所有数字都能在简报里找到出处。

**运行环境**：所有命令都在 Easel 项目根目录（含 `.env` 和 `skills/shared/scripts/` 的目录）执行，Python 用项目自己的
`.venv`（Windows：`.venv\Scripts\python.exe`），不要用其他环境的 Python。视频需要 `ffmpeg`/`ffprobe` 在 PATH 中。

## 输入

- 产品简报 `brief.md`（没有就先走 [product-brief](../product-brief/SKILL.md)；已有简报可直接引用其路径）
- 产品访问方式：运行中的地址或可执行命令（截真实画面用）
- 交付形态：落地页、小红书卡片组、竖版短片、横版介绍片中的一种或多种；**要视频必须确认画幅**，未明确前不开工
- 可选：品牌色、背景音乐、配音音色、目标平台

## 输出

`outputs/<主题>/`（如 `outputs/Easel产品宣传/`）：

- 成品（项目根）：`<标题>.html` 与 `<标题>-长图.png`、`卡片_NN.png`、`<标题>-竖版.mp4`、`<标题>-横版.mp4`
- 中间件（`assets/`）：`promo.json`、`shots/`、`cards/`、`video-<名称>/`、`review/`

## 执行步骤

1. **定核心信息**。读 `brief.md`，按 [promo-writing.md](references/promo-writing.md) 第一节定下
   `message`、`audience`、痛点和 `cta`。卖点、数字只能取简报里「已验证」或「有文档」的条目。
   先把这四项发给用户确认一句，再往下做。

2. **写 promo.json**。按 [promo-schema.md](references/promo-schema.md) 写到 `outputs/<主题>/assets/promo.json`：
   `shots` 列出要截的 3–6 个画面；`page` 写落地页与卡片内容；`videos` 按需写 `short`（1080×1920）和/或
   `intro`（1920×1080）的镜头。镜头节奏按 promo-writing.md 第五节，竖版前 3 秒必须出钩子。

3. **采集真实画面**（复用教程技能的采集脚本，写法同教程的步骤）：
   ```bash
   python skills/builtin/product-tutorial/scripts/capture.py --plan outputs/<主题>/assets/promo.json
   ```
   截图里出现密钥、手机号、真实账号信息时，用 `mask` 打码后重拍。不在产品里真实发布、付费或删除数据。

4. **生成宣传文档**：
   ```bash
   python skills/builtin/product-promo/scripts/build_promo.py page --promo outputs/<主题>/assets/promo.json \
     -o "outputs/<主题>/<标题>.html" --long-image
   python skills/builtin/product-promo/scripts/build_promo.py cards --promo outputs/<主题>/assets/promo.json \
     --out-dir outputs/<主题>
   ```
   要竖版海报时，用 [poster-hero](../poster-hero/SKILL.md) 并沿用 `promo.json` 的文案和主题色；
   要公众号或知乎长文时，用 [copywriting](../copywriting/SKILL.md) 按同一条叙事线写，配图用 `shots/` 里的真实画面。

5. **生成宣传视频**（每个视频一条命令）。配音默认免费的 edge 音色；用闭源音色前按 AGENTS 的媒体模型规则查询
   已配置的 TTS，说明会按量计费，确认后再加 `--engine closed --voice <音色>`。没有口播时建议配 `--bgm`：
   ```bash
   python skills/builtin/product-promo/scripts/build_promo.py video --promo outputs/<主题>/assets/promo.json \
     --name short -o "outputs/<主题>/<标题>-竖版.mp4"
   python skills/builtin/product-promo/scripts/build_promo.py video --promo outputs/<主题>/assets/promo.json \
     --name intro -o "outputs/<主题>/<标题>-横版.mp4"
   ```
   输出里的 `WARN`（开头太慢、静音成片等）要逐条处理。

6. **渲染回看自检**（必做）：
   ```bash
   python skills/builtin/product-tutorial/scripts/review.py doc --html "outputs/<主题>/<标题>.html" \
     -o outputs/<主题>/assets/review
   python skills/builtin/product-tutorial/scripts/review.py video -i "outputs/<主题>/<标题>-竖版.mp4" \
     -o outputs/<主题>/assets/review/short --timeline outputs/<主题>/assets/video-short/timeline.json --size 1080x1920
   ```
   机器检查 FAIL 就不交付。然后**逐张打开**落地页逐屏图、每张卡片和视频镜头总览图，对照 promo-writing.md
   第六节检查：首屏/前 3 秒能不能看懂、画面是否支撑标题、竖版里截图的字是否看得清、数字是否有出处。
   发现问题就改 `promo.json` 重跑对应命令，只返工一次。

7. **交付与登记**。成品贴进对话：图片用 `/api/media/<outputs 相对路径>` 的 Markdown 图片，视频用
   `<video src="/api/media/<outputs 相对路径>" controls style="max-width:420px">`。说明核心信息、各成品用途和建议发布平台。
   ```bash
   python skills/shared/scripts/manifest.py meta --topic <主题> --title "<标题>" --kind video --status ready \
     --cover 卡片_01.png --deliverables "<标题>.html,<标题>-长图.png,卡片_01.png,<标题>-竖版.mp4"
   ```

## 红线

- 不用 AI 生图冒充产品界面；不编数字、用户量、对比结论和用户评价。
- 对外文案和画面不得出现密钥、内部地址、本机路径、模型名和"由 AI 生成"之类措辞。
- 画幅未确认不出视频；闭源配音未经确认不调用。
- 只做宣传素材，不替用户发布；发布交给发布层技能。

## Profile 感知

- 有 Profile：品牌色写进 `theme.accent`，语气取 `style.md`，受众取 `audience.md`，严格遵守 `preferences.md` 红线。
- 无 Profile：深色主题 + 橙红强调色，语气平实克制。
