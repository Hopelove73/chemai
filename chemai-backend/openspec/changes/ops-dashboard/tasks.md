# Tasks: ops-dashboard

## 1. 后端：学校级聚合端点

- [x] 1.1 新增学校运营聚合服务与 `GET /api/admin/ops-dashboard`（`PermissionChecker("school","read")`；成绩趋势/周活跃/预警处理率复用既有查询函数；时间窗参数上限 30 天；集成卡数据按 `can("integration","read")` 裁剪；super_admin 未提权返回 `data_scope_empty`），verify：pytest 聚合口径与 analytics/warning 对拍一致 + 空窗降级 + 权限断言（teacher 403 / academic_admin 无集成卡字段 / super_admin 空态）
- [x] 1.2 router 注册进 `REFERENCE_ROUTERS`（`/api/admin` 已在 P0 登记，确认覆盖），verify：`python -m pytest tests/test_permission_matrix.py -q` 全绿

## 2. 前端：运营区

- [x] 2.1 `AdminOverviewPage` 增运营区（Tab 或区块）：趋势图用 `ui/EChart`（ClassPage/DiagnosisPage 同款）+ 预警处理率卡（跳转处理入口）+ 集成健康卡（`integration:read` 显隐；占位标注沿 PlaceholderNotice 惯例）；页面三态 R-S1（Skeleton/EmptyState 行动引导/错误重试），verify：vitest（图表渲染、空态、角色显隐、占位标注）+ 手动与 warning 端点数值对拍 + `npm run lint:tokens`
- [x] 2.2 集成健康卡在 `integration.py` 返回 `placeholder: true` 时标注占位、移除后自动展示真实数据（无需本 change 再改），verify：vitest 两态渲染用例

## 3. 验收（集成检查）

- [x] 3.1 全量回归不降基线：`python -m ruff check app/ agent/`、`python -m pytest tests/ -q`、`python evals/run_evals.py --fast`、前端 `npm run build` + `npm run lint:tokens`，verify：零新增失败 + 令牌守门零违规
