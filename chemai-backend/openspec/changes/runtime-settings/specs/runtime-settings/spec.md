# Spec Delta: runtime-settings

## Purpose

为学校管理员提供白名单内行为旗子的运行时热切换：修改不重启即可生效（短缓存时延内）、回落环境变量默认值、每次修改留审计。白名单是封闭集合，防止把该机制变成无治理的通用配置存储。

## ADDED Requirements

### Requirement: 白名单键集
可切换键 SHALL 限于白名单：初始为 `MINERU_ENABLED`（布尔）、`OCR_SHEET_PROVIDER`（枚举：paddle/baidu）、`OCR_VISION_REVIEW_ENABLED`（布尔）。白名单外的键 SHALL 一律拒绝读写；值 SHALL 按类型校验（布尔/枚举），非法值拒绝写入。

#### Scenario: 白名单外键拒绝
- **WHEN** 管理员尝试读写白名单外的键（如 `DEEPSEEK_API_KEY`）
- **THEN** 请求被拒绝并提示键不在可切换范围内

#### Scenario: 枚举值校验
- **WHEN** 将 `OCR_SHEET_PROVIDER` 设为不存在的引擎名
- **THEN** 写入被拒绝并提示合法取值

### Requirement: 热生效与默认回落
修改白名单键后，新请求 SHALL 在短缓存时延内（约 5 秒）使用新值，MUST NOT 要求重启。未被覆写的键 SHALL 使用环境变量种子默认值；删除覆写（恢复默认）SHALL 回到 env 默认。

#### Scenario: 修改后热生效
- **WHEN** 管理员将 `OCR_VISION_REVIEW_ENABLED` 关闭
- **THEN** 缓存过期后的下一次识别请求即不执行视觉复核，全程无重启

#### Scenario: 恢复默认
- **WHEN** 管理员对已覆写键执行「恢复默认」
- **THEN** 该键行为回到环境变量默认值

### Requirement: 消费点行为回归
旗子消费点（OCR 引擎选择、MinerU 开关、视觉复核）SHALL 从每次调用读取生效值；在未修改任何键时，系统行为 MUST 与本 change 之前完全一致（env 默认）。

#### Scenario: 默认行为不变
- **WHEN** 未覆写任何键时执行既有 OCR/解析回归用例
- **THEN** 行为与改动前一致

### Requirement: 修改审计
每次修改 SHALL 写操作日志：操作者、键、旧值→新值、时间，并可在操作日志页追溯。

#### Scenario: 修改留痕
- **WHEN** 管理员修改 `OCR_SHEET_PROVIDER` 由 paddle 至 baidu
- **THEN** 操作日志记录旧值 paddle → 新值 baidu

### Requirement: 权限矩阵执行
设置读取 SHALL 以 `settings:read` 为门、修改 SHALL 以 `settings:update` 为门；两者 SHALL 仅 admin（及镜像的 super_admin）持有。设置页菜单（`admin_settings`）gate `settings:update`。

#### Scenario: 教师不可读写
- **WHEN** teacher 调用设置读取或修改端点
- **THEN** 均返回 403

#### Scenario: 教务管理员不可见菜单
- **WHEN** academic_admin 登录
- **THEN** 能力清单不含 `settings:update`，菜单不含 `admin_settings`
