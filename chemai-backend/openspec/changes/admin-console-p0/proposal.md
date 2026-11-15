# Proposal: admin-console-p0


> ⚠️ **2026-11-04 部分作废**：本 change 的信息架构（平铺侧栏 + 单页混装）已被 `admin-shell-v2` 取代；权限门、审计、端点等**能力**仍有效。

## Why

RBAC 改造（rbac-enforcement）把 `exam:delete` / `exam_bank:delete` / 重置密码等动作收归管理员，但管理员登录后与教师看到同一批教师菜单，`pages/` 下没有任何 admin 页面——这些操作现在**谁也执行不了**（HANDOFF:269 记录的「阶段 5 要处理的中间态」）。同时教师入驻审批 API 存在却无界面、`log:read` 操作日志无消费者、user.py 敏感动作不落审计。学校部署已真实发生（school-data-initialization），管理界面从「可以不做」变成「每次部署靠开发者拿脚本救火」。

决策依据见 `docs/管理后台建设方案_20261104.md`：不做独立 admin 应用（chemai.db 被 uvicorn 锁定、chroma 同进程约束、RBAC/菜单机制已就绪），在现有 SPA 内以能力清单驱动菜单补齐管理员界面。

## What Changes

- **新增** 4 个管理员菜单（后端 `MENUS` + 前端 `MENU_REGISTRY`/`ROUTE_MENU`）：概览 `/admin`、教师与班级 `/admin/users`、学校设置 `/admin/school`、操作日志 `/admin/logs`——全部复用既有资源键（school/class/log），**P0 零新增资源键**
- **新增** 后端 `GET /api/admin/overview`（唯一新端点，`student:create` 门，按角色裁剪待办字段）
- **新增** 页面：概览与待办（KPI 卡 + 待审批数 + 快捷入口）；教师审批（approve/reject）与教师账号（列表/编辑/任教分配）；班级管理（class CRUD）；学校信息表单；操作日志表格与筛选
- **修复** 403 中间态：ExamPage 删考试/删题库/删题目、StudentsPage 重置密码等按钮改 `can()` 显隐（教师不可见而非点击报错）
- **整改** `teacher_application.py` 的矩阵外授权：GET 列表门从 `teacher:read` + 函数体 `role != "admin"` 改为矩阵门 `teacher:create`（阶段 7.1 对 log.py 的同款整改）
- **新增** 审计：user.py 六个敏感动作（教师/学生审批、调班、重置密码、删学生、任教分配）成功后直写 `OperationLog`（照 data_import.py 直写模式）
- **修改** 登录落地：LoginPage 按下发的菜单列表跳转（含 `admin_overview` → `/admin`，否则 `/chat`），学生/家长不受影响

## Capabilities

### New Capabilities
- `admin-console`: 管理员端界面能力——能力清单驱动的管理菜单、概览与待办、教师审批与账号管理、班级管理、学校设置、操作日志消费、按钮按能力显隐、管理员登录落地

### Modified Capabilities
- 无（现有 specs 仅覆盖 AI/agent 能力；RBAC 矩阵行为不变，本变更只消费既有权限键）

## Impact

- **新增文件**: `app/api/admin_overview.py`, `frontend/src/pages/admin/AdminOverviewPage.tsx`, `AdminUsersPage.tsx`, `AdminSchoolPage.tsx`, `AdminLogsPage.tsx`
- **修改文件**: `app/middleware/auth.py`（MENUS +4 项）, `app/api/teacher_application.py`（授权整改）, `app/api/user.py`（审计写入）, `app/main.py`（注册 router）, `frontend/src/App.tsx`（菜单注册 + 路由）, `frontend/src/pages/teacher/LoginPage.tsx`（落地跳转）, `frontend/src/pages/teacher/ExamPage.tsx`, `StudentsPage.tsx`（按钮显隐）
- **测试**: `tests/test_permission_matrix.py`（REFERENCE_ROUTERS 加 `/api/admin`）, `tests/test_permission_roles.py`（新增角色断言）, 审计用例, `frontend/tests/`（菜单快照 + 显隐逻辑）, `_e2e_verify.admin.py`
- **非目标**: 学生管理不搬页（StudentsPage 继续用）；数据导入界面（P2）；break-glass 工作台（P2）；KB 治理（kb-review-ui / kb-ingest-ui 后续 change）
