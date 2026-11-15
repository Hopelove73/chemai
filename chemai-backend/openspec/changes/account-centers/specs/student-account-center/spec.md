# Spec Delta: student-account-center

## Purpose

提供与教师端「学生管理」不同的**账号生命周期视角**：学生在系统里有没有账号、最近是否活跃、绑定了几位家长、需要批量开通还是停用。同一个学生数据，两个页面服务两种问题。

## ADDED Requirements

### Requirement: 账号视角列表
学生账号页 SHALL 以账号维度呈现：学号、姓名、班级、账号状态（有账号 / 无账号）、最近活跃日期、家长绑定数、审批状态。教师端既有页面 MUST NOT 被替换或改变行为。

#### Scenario: 列表字段
- **WHEN** 管理员打开 `/admin/students`
- **THEN** 每行显示账号状态、最近活跃、家长绑定数，而不只是学情指标

#### Scenario: 无账号视图
- **WHEN** 管理员筛选「无账号」
- **THEN** 只列出尚未创建登录账号的学生，并提供「批量开通」入口

### Requirement: 批量开通账号
具备 `student:update` 的角色 SHALL 能为选中的学生批量创建登录账号。初始密码 SHALL 仅在本次响应中展示一次，落库 MUST 为哈希。已开通的学生 SHALL 被跳过并在结果中标注。

#### Scenario: 批量开通
- **WHEN** 管理员选中 5 名学生执行批量开通
- **THEN** 5 个账号创建成功，返回一次性凭据列表，数据库只存哈希

#### Scenario: 重复开通幂等
- **WHEN** 对已开通的学生再次执行批量开通
- **THEN** 该生被标记为"跳过（已有账号）"，不覆盖原密码，不报错

### Requirement: 账号生命周期动作
页面 SHALL 支持单个与批量：重置密码、停用/启用、转班。所有动作 SHALL 写入操作日志（操作者、目标、结果）。

#### Scenario: 重置密码留痕
- **WHEN** 管理员对某学生重置密码
- **THEN** 操作日志新增一条记录，含操作者与目标学生，可在操作日志页按目标筛选到

#### Scenario: 停用后不可登录
- **WHEN** 管理员停用某学生账号
- **THEN** 该生登录被拒绝（账户状态非 active），重新启用后恢复

### Requirement: 权限与数据范围
学生账号页 SHALL 以 `student:update` 为门（`admin` / `academic_admin` 持有）。数据范围 SHALL 收窄到调用者可见的班级；`teacher` MUST 403。

#### Scenario: 教师不可访问
- **WHEN** 教师请求学生账号数据
- **THEN** 返回 403

#### Scenario: 跨校不可见
- **WHEN** 某校管理员查询学生列表
- **THEN** 只返回本校学生
