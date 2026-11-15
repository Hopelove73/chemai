# Spec Delta: school-ops-dashboard

## Purpose

为学校管理员提供学校级运营视野：成绩与活跃的时间趋势、学业预警处理率、集成健康状态，全部为真实数据推导、按角色裁剪，作为管理概览页的运营深化区。

## ADDED Requirements

### Requirement: 学校级运营聚合
具备 `student:create` 的角色（admin / academic_admin）SHALL 能获取学校级运营聚合：按时间窗（近 7/30 天）的作答趋势、活跃计数（作答人数/作答量）、预警处理率（待处理/已处理/处理率）。聚合 SHALL 基于真实作答与日志数据推导；时间窗与去重口径 SHALL 与既有 analytics 服务一致，MUST NOT 另立口径。（门用 `student:create` 而非 `school:read`：治理域全体员工都持 school:read，用它会把运营聚合漏给教师，与「教师不可访问」场景矛盾。）

#### Scenario: 管理员查看趋势
- **WHEN** admin 打开运营看板并选择近 30 天
- **THEN** 展示该时间窗内真实成绩趋势与活跃计数

#### Scenario: 空数据降级
- **WHEN** 某时间窗内无作答数据
- **THEN** 对应图表展示空态提示，不展示 0 值冒充数据

#### Scenario: 教师不可访问
- **WHEN** teacher 请求学校运营聚合端点
- **THEN** 返回 403

### Requirement: 预警处理率视图
运营聚合 SHALL 包含学业预警的处理视图：待处理数、已处理数、处理率，并能跳转至预警处理入口（既有 warning 端点）。数字 SHALL 与 warning 端点口径一致。

#### Scenario: 预警处理率一致
- **WHEN** 管理员查看预警处理率
- **THEN** 展示的待处理/已处理数与 warning 端点返回值一致

### Requirement: 集成健康如实展示
运营看板 SHALL 展示 LMS/Webhook 集成配置状态与调用日志摘要（gate `integration:read`，仅 admin 可见该卡）。数据来源尚为占位实现时，SHALL 如实标注「展示性占位」而非展示虚构健康值。

#### Scenario: 占位期如实标注
- **WHEN** 集成持久化尚未落地（密钥/调用日志无真实数据源）
- **THEN** 集成健康卡展示明确的占位说明，不显示虚构的成功率

#### Scenario: 教务管理员不可见
- **WHEN** academic_admin 查看运营看板
- **THEN** 不展示集成健康卡（无 `integration:read`）

### Requirement: 看板按角色裁剪
运营看板数据 SHALL 按角色裁剪：academic_admin 与 admin 同视图（其能力覆盖 `student:create` 与 analysis:read）；无 `student:create` 的角色不可获取聚合数据。聚合端点 MUST NOT 因角色绕过数据范围（super_admin 未提权时同 P0 概览的空态语义）。

#### Scenario: 超管未提权空态
- **WHEN** super_admin 未发起 break-glass 时请求运营聚合
- **THEN** 返回明确空态标记，前端展示提示而非空图表
