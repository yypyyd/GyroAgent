# 教程计划文件（plan.json）

教程的唯一数据源。截图、文档、视频都从它生成，所以三者的步骤、措辞和画面天然一致。
放在 `outputs/<主题>/assets/plan.json`。路径类字段一律相对 Easel 项目根。

## 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `title` | 是 | 教程标题，写成“动词 + 结果”，如「10 分钟用 Easel 发出第一篇小红书笔记」 |
| `outcome` | 是 | 读完/看完能做到什么，一句话 |
| `audience` | 是 | 写给谁 |
| `product` | 否 | 产品名，用于封面与片尾 |
| `prerequisites` | 否 | 开始前要准备好的东西（字符串数组） |
| `base_url` | 采集网页时必填 | 产品地址，如 `http://127.0.0.1:8000`；`goto` 的路径相对它 |
| `viewport` | 否 | 截图视口，默认 `{"width": 1600, "height": 900}`；要出视频时保持 16:9 |
| `scale` | 否 | 设备像素比，默认 2（文档放大不糊） |
| `storage_state` | 否 | 已登录浏览器状态文件路径（Playwright storage state） |
| `mask` | 否 | 每张截图都要打码的选择器数组，如 `["input[type=password]", ".api-key"]` |
| `settle_ms` | 否 | 每个动作后的稳定等待，默认 600 |
| `sections` | 是 | 章节数组，一个章节对应一个读者任务 |
| `faq` | 否 | `[{"q": "...", "a": "..."}]`，优先写采集中真实遇到的问题 |
| `next` | 否 | 学完之后可以做什么（字符串数组），也用作片尾 |
| `outro_say` | 否 | 片尾口播，不填时用 `next` 生成 |

## 章节 `sections[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `title` | 是 | 任务名，如「创建账号画像」 |
| `goal` | 否 | 这一节完成后读者得到什么 |
| `say` | 否 | 章节过场口播；不填则视频里该章节标题卡静音停留 1.5 秒 |
| `steps` | 是 | 步骤数组 |

## 步骤 `steps[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 全局唯一，英文短横线，如 `profile-new`；截图文件以它命名 |
| `actions` | 否 | 截图前要执行的动作，按顺序执行（见下表） |
| `highlight` | 否 | 截图时要框出的元素选择器，一步只框一个 |
| `crop` | 否 | `true`：文档用以高亮元素为中心的裁切图；填选择器（如 `".modal"`）：按该容器整体裁切。视频始终用全图 |
| `terminal` | 否 | 终端步骤：`{"cmd": "...", "output": "...", "title": "PowerShell"}`，渲染成终端截图；`output` 必须是真实运行结果 |
| `image` | 否 | 直接使用已有图片，跳过采集 |
| `shot` | 否 | `false` 表示纯文字步骤，不截图 |
| `text` | 是 | 文档里的步骤说明（Markdown），界面文字加粗并照抄原文 |
| `result` | 否 | 做完这一步会看到什么 |
| `tip` | 否 | 注意事项；有风险的提醒写在这里，会显示在步骤之前 |
| `say` | 否 | 视频口播；不填时用 `text` 去掉 Markdown 后朗读 |

## 动作 `actions[]`

每个动作是只有一个键的对象。选择器使用 Playwright 语法：`text=新建画像`、`role=button[name="发布"]`、CSS 选择器均可。

| 动作 | 写法 |
|---|---|
| 打开页面 | `{"goto": "/profiles"}`（相对 `base_url`，也可写完整 URL） |
| 点击 | `{"click": "text=新建画像"}` |
| 输入 | `{"fill": ["input[name=title]", "春日穿搭"]}` |
| 按键 | `{"press": "Enter"}` 或 `{"press": ["textarea", "Control+Enter"]}` |
| 悬停 | `{"hover": "text=更多"}` |
| 下拉选择 | `{"select": ["select#platform", "xiaohongshu"]}` |
| 勾选 | `{"check": "text=同意"}` |
| 等待元素 | `{"wait_for": "text=保存成功"}` |
| 等待毫秒 | `{"wait": 800}` |
| 滚动 | `{"scroll": "text=高级设置"}` 或 `{"scroll": 600}` |

步骤按顺序在同一个浏览器页面里执行，前一步的状态会带到后一步。

## 示例

```json
{
  "title": "5 分钟给 Easel 建好第一个账号画像",
  "outcome": "建好一个画像，之后的创作都会按它的定位和风格来",
  "audience": "第一次打开 Easel 工作台的创作者",
  "product": "Easel",
  "prerequisites": ["已按 README 启动 Easel 工作台"],
  "base_url": "http://127.0.0.1:8000",
  "mask": ["input[type=password]"],
  "sections": [
    {
      "title": "新建画像",
      "goal": "得到一个可以在会话里选用的画像",
      "say": "先来建一个画像。",
      "steps": [
        {
          "id": "profile-open",
          "actions": [{"goto": "/"}, {"click": "text=画像"}],
          "highlight": "text=新建画像",
          "text": "在左侧导航点击 **画像**，再点右上角的 **新建画像**。",
          "result": "右侧弹出画像表单。",
          "say": "打开画像页，点右上角的新建画像。"
        },
        {
          "id": "install-check",
          "terminal": {"cmd": "easel doctor", "output": "<真实运行输出>", "title": "PowerShell"},
          "text": "在终端运行 `easel doctor`，确认环境检查全部通过。",
          "say": "终端里跑一下 easel doctor，全绿就说明环境没问题。"
        }
      ]
    }
  ],
  "faq": [{"q": "点了新建画像没有反应？", "a": "刷新页面后重试；仍无反应时运行 easel doctor 检查后端。"}],
  "next": ["用画像生成第一篇小红书笔记", "在发布中心预览多平台版本"]
}
```
