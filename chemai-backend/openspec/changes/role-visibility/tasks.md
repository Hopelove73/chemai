# Tasks: role-visibility

## 1. 后端

- [x] 1.1 `GET /api/roles/overview`：角色清单（key/中文名/人数）、矩阵（从 `ROLE_PERMISSIONS` 扁平化）、范围枚举，verify：pytest 断言端点输出与 `ROLE_PERMISSIONS` 逐项一致
- [x] 1.2 人数统计一次 group by（accounts 表），verify：pytest 与直接查询一致
- [x] 1.3 门 `school:update` + 进 `REFERENCE_ROUTERS`，verify：`pytest tests/test_permission_matrix.py`

## 2. 前端

- [x] 2.1 `AdminRolesPage`：三 Tab（角色一览 / 权限矩阵 / 数据范围），矩阵按资源分组渲染、只读明示，verify：vitest（7 角色、分组渲染、只读文案）
- [x] 2.2 数据范围说明常量（7 条产品语言），verify：vitest 快照

## 3. 验收

- [ ] 3.1 全量回归：ruff / pytest / vitest / build，verify：零新增失败
- [ ] 3.2 浏览器实测：矩阵与 `academic_admin` 实际可见菜单一致，verify：手动对比


## 实施记录（2026-11-04）

已完成：`GET /api/roles/overview` 实时派生矩阵（测试断言与 ROLE_PERMISSIONS 逐项一致）、
人数一次 group by、三 Tab 页面（角色一览 / 权限矩阵 / 数据范围）与只读明示。

