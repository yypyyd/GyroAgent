# Easel SKILL 元数据

| 字段 | 值 |
|------|-----|
| **SKILL 名称** | product-promo |
| **所属层** | produce |
| **来源类型** | 自研 |
| **原始来源** | Easel 自研，2026-11-21 新增 |
| **参考项目** | 无 |
| **许可** | 随 Easel 项目许可 |

## 设计说明

- 单一数据源：`promo.json` 同时驱动截图采集、落地页、小红书卡片组和宣传视频。
- 截图采集与成品自检复用 `product-tutorial/scripts/capture.py`、`review.py`；配音、字幕、推近裁切复用 `product-tutorial/scripts/build_video.py` 的函数；合成交给 `auto-short-video/scripts/assemble.py`。
- `scripts/build_promo.py`：`page`（落地页 HTML + 长图）、`cards`（1080×1440 卡片组）、`video`（按镜头渲染设计画面，竖版/横版自适应排版，居中缓推 + 字幕 + 可选 BGM）。
- 海报复用 `poster-hero`，长文复用 `copywriting`。
