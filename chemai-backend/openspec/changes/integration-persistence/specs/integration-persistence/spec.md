# Spec Delta: integration-persistence

## Purpose

将 LMS 集成端从展示性占位转正为真实数据层：配置、API 密钥、Webhook 注册持久化（重启不丢），投递留痕可查，密钥哈希存储与校验路径就绪；不扩张事件接入面与认证消费端点。

## ADDED Requirements

### Requirement: LMS 配置持久化
具备 `integration:read`/`update` 的角色 SHALL 能保存并读取 LMS 集成配置；配置 SHALL 持久化，服务重启后 MUST 保持不变。

#### Scenario: 保存后重启可读
- **WHEN** admin 保存 LMS 配置后模拟服务重启（以新建存储实例读取）
- **THEN** 读回的配置与保存值一致

### Requirement: API 密钥生命周期
admin SHALL 能签发 API 密钥：明文密钥 SHALL 仅在签发响应中展示一次，落库 SHALL 为哈希；列表 SHALL 以掩码展示；删除 SHALL 使密钥不再可用。密钥 SHALL 附带权限清单（与既有 PERMISSION_PRESETS 同口径）。校验路径（凭据→哈希比对→权限清单）SHALL 作为可复用能力实现并有单元测试；本 change MUST NOT 新增以 API key 认证的业务端点。

#### Scenario: 签发只展示一次
- **WHEN** admin 签发新密钥
- **THEN** 响应含完整明文与密钥标识；再次查询列表仅见掩码与元数据

#### Scenario: 删除后失效
- **WHEN** admin 删除某密钥后以该密钥调用校验路径
- **THEN** 校验不通过

#### Scenario: 校验路径权限比对
- **WHEN** 以权限清单不含 `student:read` 的密钥请求校验 `student:read`
- **THEN** 校验通过身份但权限比对拒绝该动作

### Requirement: Webhook 注册持久化与投递留痕
admin SHALL 能注册/启停/删除 Webhook，注册 SHALL 持久化、重启不丢。投递（测试投递与既有事件投递）结果 SHALL 写入集成日志：目标 URL、事件类型、成败、耗时、时间。既有事件接入面 MUST NOT 因本 change 扩张。

#### Scenario: 重启后注册仍在
- **WHEN** 注册 Webhook 后模拟服务重启
- **THEN** 注册列表仍包含该 Webhook

#### Scenario: 测试投递留痕
- **WHEN** admin 对某 Webhook 执行测试投递（无论成败）
- **THEN** 集成日志新增一条投递记录（URL/事件/成败/耗时）

### Requirement: 集成日志查询
admin SHALL 能分页查询集成日志并按事件类型/时间筛选；日志为只读。

#### Scenario: 按事件筛选
- **WHEN** admin 按事件类型筛选集成日志
- **THEN** 仅返回该类型的投递与配置动作记录

### Requirement: 占位语义移除
集成页 SHALL 移除「展示性占位」横幅；配置/密钥/Webhook 操作 SHALL 表现为真实生效（重启可验证）。运营看板的集成健康卡 SHALL 展示真实数据（占位标记协议随之解除）。

#### Scenario: 集成页转正
- **WHEN** admin 打开集成页
- **THEN** 不再展示占位横幅，配置保存后刷新仍存在

### Requirement: 权限口径不变
集成端全部端点的门维持 `integration` 资源（仅 admin 持有）；本 change MUST NOT 改变任何端点的权限语义。

#### Scenario: 权限回归
- **WHEN** 运行权限矩阵与角色断言测试
- **THEN** 集成端点的角色决策与本 change 前完全一致
