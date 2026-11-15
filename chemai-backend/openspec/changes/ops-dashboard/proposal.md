# Proposal: ops-dashboard


> ⚠️ **2026-11-04 部分作废**：本 change 的信息架构（平铺侧栏 + 单页混装）已被 `admin-shell-v2` 取代；权限门、审计、端点等**能力**仍有效。

## Why

P0 概览页只有计数与待办（推导式快照），学校管理员缺少「趋势与运营」视野：成绩/活跃随时间的变化、学业预警的处理率、LMS 集成调用是否健康——数据源都在（`analytics.py`/`warning.py`/`log.py`/`integration.py`），但止步于班级/教师粒度或无界面消费。学校部署常态化后，管理员要能自己回答「这个月学校用得怎么样、预警处理了没有、集成挂没挂」，而不是问开发者。依据 `docs/管理后台建设方案_20261104.md` P2 第 1 项。

## What Changes

- **新增** 学校级聚合端点 `GET /api/admin/ops-dashboard`（门 `student:create`，与 P0 概览同口径——排除 teacher/subject_lead）：作答趋势、活跃计数（作答人数/作答量）、预警处理率（`warning` pending/process 汇总）、集成健康标记
- **新增** `/admin` 概览页的「运营」区或独立 Tab：趋势图（echarts，前端已有依赖）、预警处理进度、入口快捷跳转（`/admin/logs`）
- **新增** 集成健康卡：消费 `integration.py` 的 `GET /api/integration/logs` 与 LMS 配置状态（门 `integration:read`，仅 admin）——在 `integration-persistence` change 落地前，该卡读取现状并如实展示「占位」标注（沿 IntegrationPage 的 PlaceholderNotice 惯例，不展示虚假健康）
- **非目标**：不做跨校/平台级聚合（super_admin 多校视图依赖 break-glass 数据范围，等真实多校部署）；不做自定义报表导出；不改既有 analytics/panel 端点语义

## Capabilities

### New Capabilities
- `school-ops-dashboard`: 学校运营看板——学校级趋势与活跃聚合、预警处理率、集成健康消费、按角色裁剪

### Modified Capabilities
- 无

## Impact

- **新增文件**: 学校级聚合服务与端点, `frontend/src/pages/admin/` 看板组件
- **修改文件**: `frontend/src/pages/admin/AdminOverviewPage.tsx`（运营区/Tab）, `app/main.py`
- **测试**: 聚合口径 pytest（时间窗/去重/角色裁剪/空数据降级）、权限断言（teacher 403）、vitest 图表渲染与空态
- **依赖**: P0（admin-console-p0 的菜单机制与概览页承载本看板）
