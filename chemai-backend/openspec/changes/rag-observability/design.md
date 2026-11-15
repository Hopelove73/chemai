# Design: rag-observability

## Context

`agent/tools/tutoring.py:312` 是全文唯一 `build_kb_context` 调用点，返回 `kb_out`：含 hits（chunk 内容 / 来源 / 分数）、miss 标记、渲染后的上下文文本、图增强片段。回答在该调用之后由模型产出。

现状无任何问答日志；`kb_eval` 只有离线评测；`OperationLog` 记的是管理动作，不是问答。

## Goals / Non-Goals

**Goals**
- 每一次检索问答都留下可复查证据链
- 标注 → 回流金标集 → 可被评测消费
- 六项线上指标可查

**Non-Goals**
- 不做采样（单校日活几百，全量成本可控）
- 不接 Langfuse/OpenTelemetry 等外部观测
- 不做 LLM-as-judge 自动评分（评测 change 里只做简单指标）
- 不记录学生聊天全量内容（只记录与知识检索相关的问答）

## Decisions

**D1 — 在 `tutoring` 工具内、回答产出后写 trace，异步且失败静默。**
备选：在 API 层中间件写 —— 否决，中间件拿不到 hits 与渲染上下文。失败静默是硬要求：观测不能拖垮教学。

**D2 — trace 表独立，不复用 `OperationLog`。**
两者语义不同（问答 vs 管理动作）、量级差百倍、保留期不同（180 天 vs 长期）。复用会让日志页查询变慢。

**D3 — `hits` 与 `retrieval_config` 用 JSON 列，不建关联表。**
证据链是"快照"语义，chunk 之后可能被删/改，关联表会指向已变化的数据。快照连内容摘要一起存，保证事后可读。

**D4 — 低分命中阈值取运行时口径，不硬编码。**
阈值来自 `kb_context.KB_MISS_THRESHOLD` 同源，避免两处数字漂移。

**D5 — 反馈采集挂在消息级，不新增独立评价页。**
学生端回答下方两按钮（有帮助/没帮助），写入最近一条 trace。

**D6 — 保留 180 天，清理走启动期 + 每日一次的轻量 DELETE。**
不引 celery（单机部署）；按 `created_at < now-180d` 分批删除。

**D7 — 标注动作直接复用 `knowledge:update`，不新增资源键。**
与 KB 治理同属内容运营。

## Risks / Trade-offs

- [写入放大：每个问答一次 insert] → 单校日活几百，量级 < 5k/天；异步写 + 批量提交（每 50 条或 2s）可进一步降。
- [trace 表含学生问题文本，涉隐私] → 页面只对 `knowledge:update` 开放；导出需二次确认；保留期 180 天。
- [证据链里存 chunk 快照会重复存内容] → 只存前 200 字 + chunk_id，完整内容按 id 回查（回查不到时展示快照）。
- [埋点位置遗漏其他问答入口] → 实施首任务 grep 所有 `build_kb_context` 与直接 chroma 查询的调用点，确认唯一。

## Migration Plan

1. 新表 + 迁移（幂等）
2. 埋点（带开关 `QA_TRACE_ENABLED` 默认开）
3. 端点 + 页面
4. 回归
5. 回滚：关闭开关即停止写入；表保留无害

## Open Questions

- 无
