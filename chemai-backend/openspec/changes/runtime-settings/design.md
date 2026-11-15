# Design: runtime-settings

## Context

`app/config.py` 的 `Settings` 是模块级单例、启动时读 env——旗子值进程生命周期内不变。消费点：`MINERU_ENABLED`（document_parse_service）、`OCR_SHEET_PROVIDER`/`OCR_VISION_REVIEW_ENABLED`（ocr_service）。白名单的选取标准：**行为类**（切换改变请求期行为）、**运营会想切**（故障降级/成本控制）、**非凭据**。

## Goals / Non-Goals

**Goals:**
- 3 个既有旗子热切换，默认行为零变化
- 白名单封闭 + 类型校验 + 审计

**Non-Goals:**
- 通用配置存储/层级命名空间
- 凭据类配置（API key/密钥永不入此表——它们属于 env/密管）
- 旗子注册框架、灰度/按角色差异化取值

## Decisions

**D1 — `settings` 表 + RuntimeSettings 服务（TTL 约 5s 进程内缓存）。** 修改写表 + 立即失效缓存；读取走「缓存 → 表 → env 种子默认」。qiuqiu 同模式已验证（≤5s 生效无重启）。缓存存在理由：OCR/解析是高频调用，每请求查表不必要。

**D2 — 键名沿用 env 名。** 单一语义：界面键名 = env 名 = 白名单名，消费点改造是「把 `settings.X` 换成 `runtime_settings.get("X")`」式的机械替换，无映射心智。

**D3 — 新资源键 `settings`（read/update 仅 admin）。** 不复用 `school:update` 当门——设置是平台/运维语义，且完整性断言要求新键有消费端点（GET/PUT 就是）。REFERENCE_ROUTERS 登记 `/api/settings`。

**D4 — 恢复默认 = 删除覆写行，而非写入 env 当前值。** 写入当前值会把「env 以后被改」的事实冻死在表里；删行回落语义干净。

**D5 — 消费点改造为逐调用读取。** 三个消费点把模块级读取改为函数内 `runtime_settings.get(...)`；同步 grep 全仓确认无其他读取点（含脚本——脚本可继续直接读 env，本机制只管服务进程行为）。

## Risks / Trade-offs

- [TTL 缓存导致测试不稳定（改值后断言读到旧值）] → 测试工具提供 `invalidate()` 强制失效；用例显式调用。
- [消费点遗漏（某处仍在模块级读 env）] → 任务 2.1 的全仓 grep + 回归用例钉住「默认行为不变」。
- [未来旗子滥用白名单塞业务开关] → 非目标已声明加旗子 = 代码改动；设计意图写进 service 模块 docstring。

## Migration Plan

1. 迁移（settings 表）→ 服务与端点 → 消费点切换 → 前端。
2. 回滚：revert 后消费点回到 env 单例（默认行为不变）；settings 表留存无害。

## Open Questions

- 无。
