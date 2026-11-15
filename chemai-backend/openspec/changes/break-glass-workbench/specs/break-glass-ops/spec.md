# Spec Delta: break-glass-ops

## Purpose

为平台超管提供 break-glass 限时提权的界面工作台：查看当前生效中的提权、按需发起（理由必填、限时）、提前结束；复用既有端点语义与审计，界面不引入新权限。

## ADDED Requirements

### Requirement: 提权菜单仅超管可见
系统 SHALL 依据矩阵下发 `break_glass` 菜单（gate `break_glass:read`）：仅 super_admin 可见；admin 及以下角色 MUST NOT 收到该菜单。

#### Scenario: 超管可见
- **WHEN** super_admin 登录并请求菜单
- **THEN** 下发列表含 `break_glass`

#### Scenario: 学校管理员不可见
- **WHEN** admin 登录并请求菜单
- **THEN** 下发列表不含 `break_glass`（其矩阵不含 break_glass 资源）

### Requirement: 提权状态查看
super_admin SHALL 能在界面查看本人当前生效中的提权：理由、剩余时间；无生效提权时展示明确空态（含「未提权时学校数据不可见」的引导说明）。

#### Scenario: 生效中展示倒计时
- **WHEN** super_admin 查看工作台且有一条约 10 分钟后过期的提权
- **THEN** 展示该提权的理由与剩余秒数倒计时

#### Scenario: 空态引导
- **WHEN** 无生效提权时打开工作台
- **THEN** 展示空态与发起入口，说明提权是访问学校数据的前提

### Requirement: 发起限时提权
super_admin SHALL 能在界面发起提权：理由必填（空理由 MUST NOT 可提交，后端 400 语义一致）、时长受后端上限约束。重复发起 SHALL 展示「已有生效中的提权」而非叠加。

#### Scenario: 发起成功
- **WHEN** super_admin 填写理由与时长并提交
- **THEN** 提权生效，状态卡转为倒计时，操作已在服务端审计

#### Scenario: 空理由拦截
- **WHEN** 提交空理由
- **THEN** 前端阻止提交（与后端 400 语义一致）

#### Scenario: 不叠开
- **WHEN** 已有生效提权时再次发起
- **THEN** 展示既有提权信息，不创建第二条

### Requirement: 提前结束提权
super_admin SHALL 能在界面提前结束生效中的提权（确认流）；结束后状态卡回到空态。

#### Scenario: 提前结束
- **WHEN** super_admin 对生效中的提权执行「提前结束」并确认
- **THEN** 提权立即失效，学校数据访问回到空集语义，状态卡回到空态
