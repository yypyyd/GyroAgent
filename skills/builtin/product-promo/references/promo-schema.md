# 宣传计划文件（promo.json）

宣传的唯一数据源。截图、落地页、小红书卡片、视频都从它生成。
放在 `outputs/<主题>/assets/promo.json`。路径类字段一律相对 Easel 项目根。

## 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `product` | 是 | 产品名 |
| `title` | 是 | 成品文件名用的标题，如「Easel 产品介绍」 |
| `message` | 是 | 核心信息，一句话（见 promo-writing.md 第一节） |
| `audience` | 是 | 说给谁听 |
| `cta` | 是 | `{"text": "3 分钟装好试一次", "action": "github.com/xxx"}`；`action` 是去哪里（网址或搜索词） |
| `theme` | 否 | `{"mode": "dark" 或 "light", "accent": "#ff5a36"}`，默认深色 + 橙红；有 Profile 时取 `style.md` 的品牌色 |
| `base_url` / `viewport` / `scale` / `mask` | 采集时 | 同 product-tutorial 的 plan-schema.md |
| `shots` | 是 | 要采集的真实画面，每项是一个采集步骤（`id`、`actions`、`highlight`、`crop`、`terminal`、`image`），写法同 plan-schema.md 的步骤，`text` 可省略 |
| `page` | 出文档时 | 落地页与卡片内容，见下 |
| `videos` | 出视频时 | 一个或多个视频，见下 |

## `page`

```json
{
  "hero": {"kicker": "开源 · 社媒内容工作台", "title": "从一个想法，到发出去", "sub": "……", "shot": "home"},
  "pains": ["……", "……", "……"],
  "features": [
    {"title": "画像驱动", "desc": "……", "shot": "profile", "proof": "一份画像跨平台、跨会话生效"}
  ],
  "proof": [{"value": "10", "label": "个平台登录与发布", "source": "README"}],
  "steps": ["pip install …", "easel web", "打开 localhost:7860"]
}
```

可选 `closing`：落地页结尾和最后一张卡片的大标题，默认用 `message`；标题类字段都可用 `\n` 手动断行。
`shot` 填 `shots` 里的 `id`。`features` 建议 2–4 个；`proof` 最多 4 条且每条有 `source`。

## `videos`

```json
{
  "short": {"size": "1080x1920", "beats": [ ... ]},
  "intro": {"size": "1920x1080", "beats": [ ... ]}
}
```

每个镜头（beat）：

| 字段 | 说明 |
|---|---|
| `type` | `hook` / `pain` / `screen` / `proof` / `cta` |
| `title` | 画面大字，最多两行；用 `\n` 手动断行，避免中文断在词中间 |
| `sub` | 画面小字（可选） |
| `shot` | `screen` 镜头用的画面 `id` |
| `zoom` | `true` 表示先全图再推近：画面采集时设了 `crop` 就推近到裁切图，否则推近到 `highlight` 元素周围 |
| `say` | 口播（可选）。有口播时时长跟随配音；没有时用 `duration` |
| `duration` | 无口播时的停留秒数，默认 hook 2.2、pain 2、screen 3、proof 2.2、cta 3.2 |

`cta` 镜头自动显示 `product`、`cta.text` 和 `cta.action`，`title` 可省略。
