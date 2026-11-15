# Spec Delta: admin-console

## Purpose

为学校管理员（admin / academic_admin）与平台超管（super_admin）提供界面化的日常管理能力：审批、账号、班级、学校设置、操作日志。管理功能以能力清单驱动菜单的形态挂在现有员工端 SPA 内，权限单一真源仍是后端 RBAC 矩阵；无对应能力的角色看不到入口，也无法通过直调 URL 进入。

## ADDED Requirements

### Requirement: 管理菜单按能力清单下发
系统 SHALL 依据 RBAC 矩阵向员工角色下发管理菜单：`admin_overview`（`student:create`）、`admin_users`（`class:create`）、`admin_school`（`school:update`）、`admin_logs`（`log:read`）。MENUS 顺序 SHALL 将管理菜单排在教师菜单之后。无对应资源动作的角色 MUST NOT 收到对应菜单 key。

#### Scenario: 学校管理员的菜单
- **WHEN** admin 登录并请求菜单
- **THEN** 下发列表包含全部四个管理菜单 key，且排在教师菜单之后

#### Scenario: 教务管理员的菜单
- **WHEN** academic_admin 登录并请求菜单
- **THEN** 收到 `admin_overview`、`admin_users`，不收到 `admin_school`、`admin_logs`

#### Scenario: 教师不可见管理菜单
- **WHEN** teacher 登录并请求菜单
- **THEN** 下发列表不含任何管理菜单 key

### Requirement: 登录按菜单落地
登录成功后系统 SHALL 按下发的菜单列表决定落地页：含 `admin_overview` 时进入管理概览页，否则维持教师工作台落地。学生/家长登录路径 MUST NOT 受影响。

#### Scenario: 管理员落地管理台
- **WHEN** admin 登录成功
- **THEN** 落地管理概览页而非 Agent 工作台

#### Scenario: 教师落地不变
- **WHEN** teacher 登录成功
- **THEN** 落地 Agent 工作台（行为与现状一致）

#### Scenario: 直接访问无权限页面
- **WHEN** teacher 直接输入管理页路由 URL
- **THEN** 被前端路由守卫拦截并展示无权限页（后端权限仍是安全边界）

### Requirement: 管理概览与待办
系统 SHALL 提供管理概览页，展示学生/教师/班级计数与待办（待审教师申请数、待审学生数、未处理预警数）。数据 SHALL 为真实推导值；无数据源的指标 MUST NOT 上卡。待审教师申请字段 SHALL 仅对具备 `teacher:create` 的角色返回。

#### Scenario: 管理员查看概览
- **WHEN** admin 打开管理概览页
- **THEN** 展示真实计数与三类待办数量，可点击待办跳转对应管理页

#### Scenario: 教务管理员不含教师待办
- **WHEN** academic_admin 请求概览数据
- **THEN** 返回中不含待审教师申请字段（而非显示 0）

#### Scenario: 超管未提权时的空态
- **WHEN** super_admin 未发起 break-glass 时打开概览
- **THEN** 展示明确的空态提示（数据范围默认空集是设计行为），不展示空计数冒充数据

### Requirement: 教师入驻审批
具备 `teacher:create` 的角色 SHALL 能在界面查看待审教师申请列表并通过/驳回申请。申请列表端点的访问门 SHALL 由权限矩阵声明（`teacher:create`），MUST NOT 在矩阵之外以角色字符串判断做第二套授权。

#### Scenario: 管理员通过申请
- **WHEN** admin 在审批列表对一条待审申请执行「通过」
- **THEN** 申请状态变为已通过，对应教师账号生效，动作写入操作日志

#### Scenario: 教务管理员被矩阵拦截
- **WHEN** academic_admin 请求教师申请列表
- **THEN** 返回 403（仅由矩阵门拦截，函数体内无第二套角色判断）

### Requirement: 教师账号与任教管理
具备 `teacher:create` 的角色 SHALL 能在界面查看教师列表、编辑教师信息、分配任教班级。

#### Scenario: 分配任教班级
- **WHEN** admin 在教师账号页为某教师选择班级并保存
- **THEN** 任教关系更新并写入操作日志

#### Scenario: 教师不可进入
- **WHEN** teacher 直接请求教师账号管理数据
- **THEN** 返回 403

### Requirement: 班级管理
具备 `class:create` 的角色（admin / academic_admin）SHALL 能在界面执行班级的增删改查。

#### Scenario: 新建班级
- **WHEN** academic_admin 在班级管理中新建班级
- **THEN** 班级创建成功并可被后续学生调班/导入使用

#### Scenario: 教师不可管理班级
- **WHEN** teacher 请求班级管理写操作
- **THEN** 返回 403

### Requirement: 学校设置
具备 `school:update` 的角色 SHALL 能在界面查看并修改学校信息；学校未初始化时 SHALL 以初始化表单形态提供建校入口。

#### Scenario: 修改学校信息
- **WHEN** admin 修改学校名称并保存
- **THEN** 学校信息更新且后续接口返回新值

#### Scenario: 教务管理员只读
- **WHEN** academic_admin 打开学校设置页
- **THEN** 页面不可达（菜单未下发）且直调保存接口返回 403

### Requirement: 操作日志查询
具备 `log:read` 的角色 SHALL 能在界面分页查询操作日志并按用户/动作/时间段筛选。日志页为只读。

#### Scenario: 按动作筛选
- **WHEN** admin 在日志页筛选「重置密码」类动作
- **THEN** 仅返回该类日志记录

#### Scenario: 教师不可读日志
- **WHEN** teacher 请求操作日志数据
- **THEN** 返回 403

### Requirement: 敏感管理动作审计
教师审批、学生审批、学生调班、重置密码、删除学生、任教分配六个敏感动作成功执行后 SHALL 写入操作日志（操作者、动作、目标、时间），并可在日志页追溯。

#### Scenario: 重置密码留痕
- **WHEN** admin 对某学生执行重置密码成功
- **THEN** 操作日志新增一条记录，含操作者与目标学生

#### Scenario: 失败不误记
- **WHEN** 某敏感动作执行失败（如目标不存在）
- **THEN** 不写入成功审计记录

### Requirement: 破坏性按钮按能力显隐
教师端页面上的破坏性/受限操作按钮 SHALL 依据前端能力清单显隐：无对应能力时按钮不可见，而非点击后报 403。覆盖至少：删考试（`exam:delete`）、删题库/删题目（`exam_bank:delete`）、重置密码（`student:update`）。

#### Scenario: 教师端删题库按钮
- **WHEN** teacher 打开出题工作台的题库管理
- **THEN** 删题库/删题目按钮不可见

#### Scenario: 管理员按钮可用
- **WHEN** admin 打开同一页面
- **THEN** 删题库/删题目按钮可见且执行成功

#### Scenario: 教务管理员重置密码
- **WHEN** academic_admin 打开学生管理
- **THEN** 重置密码按钮可见（`student:update` 在其能力清单内）且执行成功
