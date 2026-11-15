# Design: ops-dashboard

## Context

P0 已交付管理壳与概览页（`AdminOverviewPage`，KPI+待办）。数据源现状：`analytics.py`（班级/教师粒度）、`warning.py`（pending/process/check/class summary）、`log.py`（操作日志）、`integration.py`（集成配置与日志——8 处 TODO 占位，由 integration-persistence change 转正）。前端已有 echarts 依赖。

## Goals / Non-Goals

**Goals:**
- 一个学校级聚合端点 + 概览页运营区，复用既有口径，不造新指标
- 集成健康卡对占位现状如实标注

**Non-Goals:**
- 跨校/平台级聚合（多校未真实发生）
- 自定义报表/导出
- 改动 analytics/panel 既有端点

## Decisions

**D1 — 新端点 `GET /api/admin/ops-dashboard` 而非扩 analytics.py。** analytics 的资源门与语义面向教学分析（analysis:read），运营聚合的门是 `student:create`（admin/academic_admin 专属——`school:read` 会被全体教师持有，与 spec 的教师 403 场景矛盾，P0 概览同款教训）且需字段级角色裁剪（集成卡 gate integration:read）——混入会让 analytics 的门语义变复杂。聚合 SQL 直接复用 analytics 服务的查询函数（口径一致性由复用保证，不复制 SQL）。

**D2 — 趋势口径沿用 kb_error_rate/analytics 的既定去重与 MIN_ANSWERS 纪律。** 活跃计数以操作日志与作答事件为准（有日志以来才有数据——界面对更早时间段展示「无数据」而非 0）。

**D3 — 集成健康卡与 integration-persistence 解耦。** 本 change 先按占位现状如实标注；persistence 落地后同一消费端点自然变真，无需本 change 改动。占位判定：读到的密钥列表端点仍返回硬编码空列表的特征不变时即视为占位（实现上以端点返回的 `placeholder: true` 标记为准——由 integration.py 现有 TODO 结构提供，persistence change 移除该标记）。

## Risks / Trade-offs

- [聚合查询在大数据量下变慢] → 时间窗硬上限（30 天）+ 计数查询走索引列；必要时加物化汇总表（实施时按 EXPLAIN 决定，先不做）。
- [活跃口径被质疑（登录日志之前不存在）] → 界面注明数据起点；不回填虚构历史。
- [与 analytics 图表重复建设] → 概览页运营区只做学校级 3-4 张卡，班级/学生粒度继续导向既有 ClassPage/DiagnosisPage。

## Migration Plan

纯增量（新端点 + 前端区），无迁移；回滚 revert 即可。

## Open Questions

- 无。
