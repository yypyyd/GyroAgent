# Easel SKILL 元数据

| 字段 | 值 |
|------|-----|
| **SKILL 名称** | product-brief |
| **所属层** | plan |
| **来源类型** | 自研 |
| **原始来源** | Easel 自研，2026-11-21 新增 |
| **参考项目** | 无 |
| **许可** | 随 Easel 项目许可 |

## 设计说明

- 产品类内容（教程、宣传）的前置步骤：先产出带证据的事实清单，下游只从 `brief.md` 取事实，避免编造功能。
- `scripts/scan_source.py` 只做盘点（README、文档、包清单、CLI、路由、页面、示例配置的键名），不读 `.env`。
- 运行中核实复用 `product-tutorial/scripts/capture.py`。
