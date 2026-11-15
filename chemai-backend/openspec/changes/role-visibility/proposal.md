# Proposal: role-visibility

## Why

管理员最常问的一句话是「为什么这个按钮我看不到」。今天答案只存在于代码里（`app/middleware/auth.py` 的 `ROLE_PERMISSIONS`），管理员既看不到自己有什么，也看不到 `academic_admin` 和 `teacher` 的区别，更看不懂 `subject_lead` 的"本学科"到底是什么范围。

调研（Moodle `Define roles` / Canvas `Permissions`）证明角色中心是真实需求，但它们都做成**可编辑**，而我们的权限矩阵是 300+ 测试围绕写的单一真源 —— 让 UI 改它等于重构 RBAC。折中方案：**只读展示**，把"看不到为什么"变成可查。

依据 `docs/管理后台V2方案_20261104.md` §3.11。

## What Changes

- **新增** 页面 `/admin/roles`（gate `school:update`，仅 admin）：三个 Tab
  - 角色一览：7 角色 + 中文名 + 职责一句话 + 人数
  - 权限矩阵：资源 × 动作，**从 `ROLE_PERMISSIONS` 自动生成**（只读），页面明示"权限真源在代码，变更需发版"
  - 数据范围解释：每个角色的 `RequestScope` 语义（全校 / 本年级 / 本学科 / 任教班 / 本人 / 绑定子女）
- **新增** 端点 `GET /api/roles/overview`：返回角色清单、矩阵、人数、数据范围说明（全部从现有矩阵派生，**不新增第二份权限配置**）
- **非目标**：不做权限编辑、不做自定义角色、不做角色-用户分配界面（分配走审批与任教分配既有流程）

## Capabilities

### New Capabilities
- `role-visibility`: 角色与权限只读展示——角色清单、权限矩阵派生、数据范围解释

### Modified Capabilities
- 无

## Impact

- **新增文件**：`app/api/roles.py`、`frontend/src/pages/admin/AdminRolesPage.tsx`
- **修改文件**：`app/main.py`、`frontend/src/App.tsx`
- **测试**：矩阵端点输出与 `ROLE_PERMISSIONS` **逐项一致**（防止派生逻辑漂移）、人数统计正确、teacher 403、页面明示只读的文案存在
- **依赖**：`admin-shell-v2`
