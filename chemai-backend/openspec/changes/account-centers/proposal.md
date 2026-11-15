# Proposal: account-centers

## Why

上一轮把「教师与班级」做成一个页面三个 Tab，并明确决定「学生管理不搬页、复用教师端 StudentsPage」。这在数据层面被证伪：教师端 `/students` 是**教学视角**（正确率、障碍类型、诊断），管理端需要的是**账号生命周期视角**（有没有账号、是否活跃、绑定几位家长、批量开通/停用）；两者字段、动作、空态都不同。

更关键的是家长：库里有 **680 条绑定记录，但 `accounts` 表中 parent 账号只有 2 个** —— 家长端几乎未激活。这意味着「家长管理」的核心不是管理已有账号，而是**看激活漏斗 + 批量开通 + 处理异常绑定**。这一页今天完全不存在。

依据 `docs/管理后台V2方案_20261104.md` §3.1–3.4。

## What Changes

- **新增** 学生账号页 `/admin/students`（gate `student:update`）：账号状态、最近活跃、绑定家长数；批量开通/重置密码/转班/停用；无账号学生独立筛选视图
- **新增** 家长与绑定页 `/admin/parents`（gate `student:update`）：两个 Tab
  - Tab A 家长账号：激活漏斗（绑定 680 → 有账号 → 近 7 天活跃）、批量开通（一次性凭据弹窗，复用教师审批模式）、重置绑定码、停用
  - Tab B 绑定关系：家长↔学生、关系、绑定码状态、单条/批量解绑、异常视图（重复手机号、未激活超 7 天、一孩绑定 >3 位家长）
- **新增** 班级与年级页 `/admin/classes`（gate `class:update`）：年级→班级组织树、班级 CRUD、年级 CRUD
- **修改** 教师账号页 `/admin/teachers`：由现有 `AdminUsersPage` 拆出「教师审批 + 教师账号」两 Tab，行为不变
- **新增** 批量开通接口：`POST /api/users/students/batch-create-accounts`、`POST /api/users/parents/batch-create-accounts`（返回一次性明文密码，落库为哈希；与教师审批同款）
- **新增** 家长绑定管理接口：`GET /api/users/parents`（列表 + 激活状态）、`POST /api/users/parents/{id}/reset-bind-code`、`DELETE /api/users/parent-bindings/{id}`、`GET /api/users/parent-bindings`（关系列表 + 异常标记）
- **不改** 家长自助路径：`/api/parent/register` 与 `/api/parent/bind` 保持现状，作为并行激活渠道（管理端批量开通 ≠ 取代自助）

## Capabilities

### New Capabilities
- `student-account-center`: 学生账号管理——生命周期字段、批量开通、状态与绑定视图
- `parent-binding-center`: 家长账号与绑定——激活漏斗、批量开通、绑定关系与异常治理
- `class-grade-admin`: 年级与班级组织管理——组织树、班级 CRUD、年级 CRUD

### Modified Capabilities
- 无

## Impact

- **新增文件**：`app/api/account_center.py`、`frontend/src/pages/admin/AdminStudentsPage.tsx`、`AdminParentsPage.tsx`、`AdminTeachersPage.tsx`、`AdminClassesPage.tsx`
- **修改文件**：`app/main.py`（注册 router）、`frontend/src/App.tsx`（路由）、`app/models/database.py`（如 `Parent` 缺 `status` 字段则补列 + 迁移）
- **测试**：批量开通返回一次性明文且落库为哈希、家长激活漏斗计数与库一致、重复开通幂等、异常绑定识别、权限断言（teacher 403 / academic_admin 可进学生与家长页、不可进教师审批）
- **审计**：批量开通、重置绑定码、解绑、停用全部写 `OperationLog`
- **依赖**：`admin-shell-v2`（路由与菜单分组）
