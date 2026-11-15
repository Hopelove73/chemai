# Spec Delta: role-visibility

## Purpose

把"为什么我看不到这个按钮"从只能问开发，变成管理员可自查：展示 7 个角色的职责、权限矩阵与数据范围。只读，权限真源仍在代码。

## ADDED Requirements

### Requirement: 角色一览
角色页 SHALL 列出全部 7 个角色，含中文名、职责一句话说明、当前账号数量。

#### Scenario: 角色清单完整
- **WHEN** 管理员打开角色页
- **THEN** 7 个角色全部出现，人数与账户表统计一致

### Requirement: 权限矩阵派生
权限矩阵 SHALL 从后端 `ROLE_PERMISSIONS` 直接派生（资源 × 动作），MUST NOT 存在第二份权限配置。矩阵项 SHALL 与实际端点的权限声明一致。

#### Scenario: 矩阵不漂移
- **WHEN** 对比矩阵端点输出与 `ROLE_PERMISSIONS`
- **THEN** 逐项一致（缺失或多出即失败）

#### Scenario: 只读明示
- **WHEN** 管理员查看矩阵
- **THEN** 页面明确标注"权限真源在代码，变更需发版"，且无任何编辑入口

### Requirement: 数据范围解释
每个角色 SHALL 展示其数据范围语义（全校 / 本年级 / 本学科 / 任教班 / 本人 / 绑定子女），并给出一句"这意味着什么"的说明。

#### Scenario: 学科组长
- **WHEN** 查看 `subject_lead`
- **THEN** 数据范围说明为"本学科任教班级"，而非"全校"

### Requirement: 权限
角色页 SHALL 以 `school:update` 为门（仅 `admin`）；`academic_admin` / `teacher` MUST 403。

#### Scenario: 教务管理员不可见
- **WHEN** `academic_admin` 访问角色页数据
- **THEN** 返回 403
