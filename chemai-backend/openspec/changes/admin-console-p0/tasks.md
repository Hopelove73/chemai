# Tasks: admin-console-p0

## 1. 后端：管理菜单与概览端点

- [x] 1.1 `app/middleware/auth.py` 的 `MENUS` 追加 4 个管理菜单项（`admin_overview`/`admin_users`/`admin_school`/`admin_logs`，gate 依 design D7；附选型注释与「新增敏感端点对照审计清单」备忘），verify：pytest 断言 `menus_for('admin')` 含四键且排在教师菜单后、`menus_for('academic_admin')` 恰含前两键、`menus_for('teacher')` 不含任何管理键
- [x] 1.2 新增 `app/api/admin_overview.py`：`GET /api/admin/overview`（`PermissionChecker("student","create")`——实现时发现 `school:read` 会把概览漏给全体教师，测试抓到后修正门与 spec；学生/教师/班级计数 + 待办；教师待审字段按 `can("teacher","create")` 裁剪；super_admin 未提权返回 `data_scope_empty` 标记），`app/main.py` 注册 router，`tests/test_permission_matrix.py` 的 `REFERENCE_ROUTERS` 加 `"/api/admin"`，verify：`python -m pytest tests/test_permission_matrix.py -q` 全绿（197 passed）
- [x] 1.3 `tests/test_permission_roles.py` 增补概览角色断言：admin 200 全字段、academic_admin 200 无教师待审字段（非 0）、teacher 403、super_admin 未提权得 `data_scope_empty=true`，verify：`python -m pytest tests/test_permission_roles.py -q` 通过

## 2. 后端：授权整改与审计

- [x] 2.1 `app/api/teacher_application.py` 整改：GET 列表门改 `PermissionChecker("teacher","create")` 并删除函数体 `role != "admin"`；approve/reject 同为函数体检查已同款整改（并补审计，见 2.2 同款），verify：`test_permission_roles.py` 新增断言 academic_admin 调 `/teacher-applications` 403、admin 200（113 passed）
- [x] 2.2 `app/api/user.py` 六个敏感动作（教师审批/学生审批/调班/重置密码/删学生/任教分配）成功路径直写 `OperationLog`（照 `app/services/data_import.py` 模式：操作者、动作、目标类型/ID、时间；同事务提交），verify：pytest 审计用例——admin 重置密码后 `GET /api/logs` 出现记录；动作失败（目标不存在）不写

## 3. 前端：菜单注册与管理页面

- [x] 3.1 `frontend/src/App.tsx` 的 `MENU_REGISTRY`/`ROUTE_MENU` 注册 4 key + 元素符号（design D8）+ lazy 路由（新页放 `frontend/src/pages/admin/`），verify：vitest 菜单快照（含 navItemsFor 顺序）+ `npm run build` + `npm run lint:tokens` 通过
- [x] 3.2 `AdminOverviewPage.tsx`：KPI 卡用 `ui/StatCard`（真实推导值）+ 待办卡（可点跳转）+ 快捷入口 + `data_scope_empty` 空态提示；页面三态 R-S1（Skeleton/EmptyState 行动引导/错误重试），verify：vitest 渲染（空态分支含行动引导 + 待办点击路由）
- [x] 3.3 `AdminUsersPage.tsx`：教师审批（列表/通过/驳回）+ 教师账号（列表/编辑/任教分配）+ 班级管理（CRUD）三 Tab，Tab 内按 `can("teacher:create")`/`can("class:create")` 显隐；审批动作走 `useAsyncAction`+`ActionButton`，批准走行内两段式微确认（R-I5），驳回附意见确认卡（R-I2），verify：vitest Tab 显隐 + 两段式状态机 + 手动过一遍审批通过流程
- [x] 3.4 `AdminSchoolPage.tsx`：学校信息表单（GET/PUT）+ 未初始化时建校入口（POST）+ 三态，verify：手动保存后重新拉取显示新值
- [x] 3.5 `AdminLogsPage.tsx`：分页表格（antd Table）+ 用户/动作/时间段筛选（只读）+ 三态 + R-T-L4 窄屏折叠，verify：手动筛选与 2.2 审计数据联动可见
- [x] 3.6 `LoginPage.tsx` 登录落地按下发菜单：含 `admin_overview` → `/admin`，否则 `/chat`；学生/家长路径不变，verify：vitest（admin → `/admin`，teacher → `/chat`）
- [x] 3.7 `design/教师端.md` 增补管理页章节：页面清单（4 页）、元素符号登记（D8 表）、表格折叠适用说明，verify：分册存在且 DESIGN.md 总册「7 页」表述同步修订

## 4. 前端：破坏性按钮按能力显隐

- [x] 4.1 全量排查教师端受限按钮并加 `can()` 门：ExamPage 删考试 `can("exam:delete")`、删题库/删题目 `can("exam_bank:delete")`、StudentsPage 重置密码 `can("student:update")`/新增与导入 `can("student:create")`/删除 `can("student:delete")`（以 `test_permission_roles.py` 决策表为准，grep DELETE 类调用补漏）；删除类确认走 `ActionButton` 两段式，verify：vitest 显隐分支 + 手动验证 teacher 不可见、academic_admin 重置密码可用、admin 删题库可用

## 5. e2e 与验收（集成检查）

- [ ] 5.1 新增 `_e2e_verify.admin.py`（仿 `_e2e_verify.phase5.py`）：admin 登录落地 `/admin`、四页可达；teacher 无管理菜单且直调管理路由见无权限页；academic_admin 无学校设置/日志入口；重置密码在日志页可追溯，verify：脚本全部通过
- [ ] 5.2 全量回归不降基线：`python -m ruff check app/ agent/`、`python -m pytest tests/ -q`、`python evals/run_evals.py --fast`、前端 `npm run build` + `npm run lint:tokens`，verify：对齐 rbac-enforcement 验证基线口径（矩阵/角色/全量 pytest 零新增失败 + 令牌守门零违规）
