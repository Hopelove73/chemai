# Spec Delta: class-grade-admin

## Purpose

把「学校设置」里隐含的组织层级显性化：年级 → 班级的组织树与两级 CRUD。班级今天只能靠数据导入创建，年级没有界面，管理员改一个班名要调接口。

## ADDED Requirements

### Requirement: 组织树
班级与年级页 SHALL 以「年级 → 班级」组织树呈现，每个班级节点 SHALL 显示学生数、班主任、任课教师数；每个年级节点 SHALL 显示班级数与学生总数。

#### Scenario: 树形展示
- **WHEN** 管理员打开 `/admin/classes`
- **THEN** 看到按年级分组的班级列表，含学生数与班主任

### Requirement: 班级 CRUD
具备 `class:update` 的角色 SHALL 能新建、重命名、删除班级。新建 SHALL 绑定到本校年级（跨校 MUST 403）。删除前 SHALL 提示该班学生数与任课教师数；班级非空时 MUST NOT 静默删除。

#### Scenario: 新建班级
- **WHEN** 教务管理员在某年级下新建班级
- **THEN** 班级创建成功并出现在该年级节点下

#### Scenario: 非空班级删除拦截
- **WHEN** 管理员尝试删除仍有学生的班级
- **THEN** 返回明确错误（需先转班或确认级联策略），不静默删除学生

#### Scenario: 跨校年级
- **WHEN** 管理员尝试把班级挂到别校年级下
- **THEN** 返回 403

### Requirement: 年级 CRUD
具备 `grade:update` 的角色 SHALL 能新建与编辑年级（名称、学年）。删除年级 SHALL 仅在该年级无班级时允许。

#### Scenario: 删除空年级
- **WHEN** 管理员删除一个没有任何班级的年级
- **THEN** 删除成功

#### Scenario: 年级下有班级
- **WHEN** 管理员删除仍有班级的年级
- **THEN** 返回错误并提示先处理班级

### Requirement: 权限
年级与班级管理 SHALL 以 `class:update` / `grade:update` 为门；`teacher` MUST 403。

#### Scenario: 教师不可访问
- **WHEN** 教师请求班级或年级的写操作
- **THEN** 返回 403
