# Easel SKILL 元数据

| 字段 | 值 |
|------|-----|
| **SKILL 名称** | product-tutorial |
| **所属层** | produce |
| **来源类型** | 自研 |
| **原始来源** | Easel 自研，2026-11-21 新增 |
| **参考项目** | 无 |
| **许可** | 随 Easel 项目许可 |

## 设计说明

- 单一数据源：`plan.json` 同时驱动截图采集、图文教程和教程视频，三者步骤与措辞一致。
- `scripts/capture.py`：Playwright 按计划操作真实产品，高亮目标、`mask` 打码、终端截图、可选录屏。
- `scripts/build_doc.py`：计划 + 采集报告 → Markdown，再经 `shared/scripts/doc_convert.py` 转 HTML/PDF。
- `scripts/build_video.py`：封面/章节/片尾卡 + 逐段 TTS（`shared/scripts/tts.py`）+ 高亮推近，交给 `auto-short-video/scripts/assemble.py` 合成。
- `scripts/review.py`：文档逐屏截图、视频镜头总览图，加坏图、分辨率、音轨、敏感信息（`content_guard`）检查。
