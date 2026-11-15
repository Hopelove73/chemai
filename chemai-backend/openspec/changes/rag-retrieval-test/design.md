# Design: rag-retrieval-test

## Context

`kb_context.build_kb_context(question, collection, encoder, graph_engine, ...)` 是检索主入口，参数全部来自全局配置（`COLLECTION_NAME`、阈值常量、图增强逻辑）。要做召回测试必须能**传覆盖配置**跑同一条管线，而不是另写一套检索。

## Goals / Non-Goals

**Goals**
- 与线上完全同一条检索管线，只是参数可覆盖
- 覆盖不落库、不影响默认配置
- 结果可直接沉淀为金标/坏案例

**Non-Goals**
- 不做 A/B 并排（P1）
- 不暴露 embedding 模型切换（换模型等于重建索引，不是测试范围）
- 不做批量评测（评测 change 负责）

## Decisions

**D1 — 从 `build_kb_context` 抽出 `retrieve(question, collection, encoder, graph_engine, *, top_k, threshold, graph_enabled)`，`build_kb_context` 改为调用它并传默认值。**
备选：在测试端点里复制检索逻辑 —— 否决，双实现必然漂移（本轮已在 kb_graph_import 上吃过教训）。

**D2 — 覆盖参数白名单：`top_k`（1–20）、`threshold`（0–1）、`graph_enabled`（bool）。**
不接受任意参数透传，防止把测试页变成配置面。

**D3 — 测试请求不写 `qa_traces`。**
测试与线上日志混在一起会污染指标（命中率/零召回率）。仅在用户显式"标记坏案例"时写一条带 `source=retrieval_test` 的记录。

**D4 — 结果响应结构与 `qa_traces.hits` 同形。**
复用同一序列化器，保证"测试页看到的就是日志页看到的"。

**D5 — 金标保存复用 `rag-eval-regression` 的金标表与端点。**
本 change 不新建表；若评测 change 尚未落地，先落 `POST /api/rag/golden-questions` 的最小实现（问题 + 期望块 + 匹配规则 + draft 状态）。

## Risks / Trade-offs

- [临时覆盖被误当成"改了系统参数"] → 页头常驻提示"仅本次测试，不修改默认配置"；响应里回显 `applied_config` 与 `default_config` 对比。
- [大 TopK 拖慢] → 上限 20。
- [图增强在测试中开启会增加延迟] → 显示本次耗时与是否启用图增强。

## Migration Plan

纯增量：抽函数 + 新端点 + 新页面。回滚 revert 即可；抽函数保持 `build_kb_context` 行为不变（回归测试保证）。

## Open Questions

- 无
