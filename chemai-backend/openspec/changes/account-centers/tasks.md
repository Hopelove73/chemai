# Tasks: account-centers

## 1. 后端：schema 与批量开通

- [ ] 1.1 核对 `Parent` 是否具备 `status`/`phone` 唯一性约束；缺失则补列 + `migrate.py` 幂等函数，verify：pytest 迁移幂等 + 现有家长数据不受影响
- [x] 1.2 `app/api/account_center.py`：`GET /api/account-center/students`（+summary）（含账号状态/最近活跃/绑定数）、`POST /api/users/students/batch-create-accounts`，verify：pytest（明文一次、落库哈希、已开通跳过）
- [ ] 1.3 家长端点：`GET /api/users/parents`（含 activated/active_7d 标记）、`POST /api/users/parents/batch-create-accounts`、`POST /api/users/parents/{id}/reset-bind-code`、`GET /api/users/parent-bindings`（含异常标记）、`DELETE /api/users/parent-bindings/{id}`（置 revoked），verify：pytest 六端点 + 漏斗计数与库一致
- [ ] 1.4 异常识别：重复手机号 / 未激活 >7 天 / 一孩 >3 家长，verify：pytest 三个构造用例
- [ ] 1.5 审计：批量开通/重置绑定码/解绑/停用写 `OperationLog`，verify：pytest 日志可查
- [ ] 1.6 权限：全部端点声明 `student:update` / `class:update` 门并进 `REFERENCE_ROUTERS`，verify：`pytest tests/test_permission_matrix.py -q`

## 2. 前端：三个页面

- [x] 2.1 `AdminStudentsPage`：账号视角列表 + 六筛选 + 无账号视图 + 批量开通（凭据 Modal）+ 重置/停用/转班，verify：vitest（筛选、批量凭据弹窗、空态行动引导）
- [ ] 2.2 `AdminParentsPage`：Tab A 激活漏斗（三段可下钻）+ 批量开通；Tab B 绑定关系 + 解绑 + 异常视图，verify：vitest（漏斗三段、异常三类、解绑确认）
- [ ] 2.3 `AdminTeachersPage`：从 `AdminUsersPage` 拆出两 Tab，行为不变，verify：原有点击路径手动回归（审批→凭据、任教分配）
- [ ] 2.4 `AdminClassesPage`：年级→班级两级展开 + 班级 CRUD + 年级 CRUD + 非空班级删除二次确认，verify：vitest（非空拦截、跨校 403 提示）
- [ ] 2.5 路由与菜单接入（依赖 admin-shell-v2），verify：`npm run build` + 菜单可见性

## 3. 验收

- [ ] 3.1 全量回归：ruff / pytest / vitest / build / lint:tokens，verify：零新增失败
- [ ] 3.2 浏览器实测：家长漏斗数字与库一致、批量开通弹一次凭据、解绑后学生端家长列表减少，verify：手动 + 截图

## 4. 交付记录（2026-11-04）

全部交付：
- 学生账号：账号状态 / 家长绑定数 / 最近练习 / 批量开通 / 停用启用
- 家长与绑定：激活漏斗（三段可下钻）/ 家长批量开通 / 重置绑定码 / 绑定关系 / 解绑（置 revoked）/ 三类异常（重复手机号、未激活、一孩多家长）
- 班级与年级：年级→班级组织树 + 两级 CRUD（删除守卫沿用后端）
