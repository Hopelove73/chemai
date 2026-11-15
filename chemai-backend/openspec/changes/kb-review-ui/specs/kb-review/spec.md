# Spec Delta: kb-review

## Purpose

为平台内容团队（admin / super_admin）提供 KB 知识库内容的审校与批准落库界面：审校包结构化浏览、批准/驳回、落库与既有 CLI 流程同语义、全程操作审计，替代「会话口头批准 + `_reviews.json`」的不可追溯终审方式。

## ADDED Requirements

### Requirement: KB 管理菜单按能力下发
系统 SHALL 依据 RBAC 矩阵下发 `admin_kb` 菜单（gate `knowledge:update`）：仅 admin 与 super_admin 可见；academic_admin / subject_lead / teacher MUST NOT 收到该菜单。

#### Scenario: 学校管理员可见
- **WHEN** admin 登录并请求菜单
- **THEN** 下发列表含 `admin_kb`

#### Scenario: 教务管理员不可见
- **WHEN** academic_admin 登录并请求菜单
- **THEN** 下发列表不含 `admin_kb`，且其能力清单不含 `knowledge:update`

### Requirement: 摄取与内容状态总览
系统 SHALL 提供推导式只读的 KB 状态总览：各册摄取状态、文档/chunk 规模、词典覆盖、最近评测阈值水位。状态数据 SHALL 来源于真实库与产物，无数据的维度 SHALL 展示空态而非虚构值。

#### Scenario: 管理员查看状态卡
- **WHEN** admin 打开 KB 管理页
- **THEN** 展示各册真实摄取状态与规模计数

### Requirement: 审校包结构化浏览
具备 `knowledge:read` 的角色 SHALL 能获取审校包列表与结构化详情：提议图谱边、词典条目变更以「新增/修改/冲突」分类的 diff 形式呈现，MUST NOT 要求阅读原始 JSON 文件。

#### Scenario: 浏览审校包
- **WHEN** admin 打开审校队列并选择一个审校包
- **THEN** 以 diff 视图展示该包的提议边与词典变更分类清单

### Requirement: 界面批准落库与 CLI 同语义
具备 `knowledge:update` 的角色 SHALL 能在界面批准审校包，落库 SHALL 复用既有 CLI 落库逻辑（同一服务函数），保证与 `kb_graph_import` 命令行路径结果一致。批准落库 MUST NOT 触发向量删除/重摄（同进程约束归摄取流程管辖）。

#### Scenario: 批准并落库
- **WHEN** admin 对一个 pending 审校包执行「批准落库」
- **THEN** 提议边/词典按 CLI 同语义写入结构库，包状态变为 approved

#### Scenario: 与 CLI 对拍一致
- **WHEN** 同一审校包分别经界面批准与 CLI 执行（对照库）
- **THEN** 两条路径落库后的图谱边与词典内容一致

### Requirement: 审校包状态机与幂等
审校包 SHALL 维护状态机 pending → approved / rejected。已终结（approved/rejected）的审校包 MUST NOT 重复落库；重复批准请求 SHALL 被拒绝并给出明确提示。

#### Scenario: 重复批准被拒
- **WHEN** admin 对已 approved 的审校包再次发起批准
- **THEN** 请求被拒绝，返回包已终结的状态，不产生重复落库

### Requirement: 审校驳回留痕
具备 `knowledge:update` 的角色 SHALL 能驳回审校包；驳回 SHALL 记录操作者与时间并可附理由。

#### Scenario: 驳回审校包
- **WHEN** admin 驳回一个 pending 审校包
- **THEN** 包状态变为 rejected，理由与操作者可查

### Requirement: 审校操作全程审计
批准与驳回动作 SHALL 写入操作日志（操作者、动作、审校包标识、时间），并可在操作日志页追溯。

#### Scenario: 批准留痕
- **WHEN** admin 批准落库成功
- **THEN** 操作日志新增记录，含操作者与审校包标识

### Requirement: 审校权限矩阵执行
审校浏览端点 SHALL 以 `knowledge:read` 为门，批准/驳回端点 SHALL 以 `knowledge:update` 为门；不具备 `knowledge:update` 的角色调用批准端点 MUST 返回 403。

#### Scenario: 教务管理员不可批准
- **WHEN** academic_admin 调用审校包批准端点
- **THEN** 返回 403（其能力清单仅 `knowledge:read`）
