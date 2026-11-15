# Spec Delta: kb-ingest-jobs

## Purpose

为平台内容团队（admin / super_admin）提供 KB 摄取的界面触发与进度跟踪：长耗时嵌入摄取以异步任务执行并可轮询，失败不得破坏既有检索索引；词典/标签映射同步界面化。前置条件为部署机具备嵌入模型环境（不满足则本能力不启用，维持 CLI 流程）。

## ADDED Requirements

### Requirement: 摄取任务触发
具备 `knowledge:update` 的角色 SHALL 能从界面触发指定册的摄取任务。触发请求 SHALL 立即返回任务标识（不阻塞等待嵌入完成）；参数 SHALL 限于白名单任务类型与受控册名，MUST NOT 接受任意命令或自由路径。触发前系统 SHALL 自检嵌入环境（模型目录可达），缺失时返回结构化错误并明确指出环境缺失，MUST NOT 进入执行。

#### Scenario: 触发摄取
- **WHEN** admin 对「必修二」触发摄取任务
- **THEN** 请求立即返回任务标识，任务进入队列状态

#### Scenario: 环境缺失拒绝触发
- **WHEN** 部署机无嵌入模型环境时触发摄取
- **THEN** 返回结构化错误（指明环境缺失），不产生任务，既有索引不变

#### Scenario: 非法参数拒绝
- **WHEN** 请求携带白名单之外的任务类型或未知册名
- **THEN** 返回参数错误，不产生任务

#### Scenario: 教务管理员不可触发
- **WHEN** academic_admin 调用摄取触发端点
- **THEN** 返回 403（其能力清单不含 `knowledge:update`）

### Requirement: 任务进度查询
具备 `knowledge:read` 的角色 SHALL 能按任务标识轮询任务状态：queued / running / succeeded / failed，附带进度信息；失败时 SHALL 提供错误摘要。任务历史 SHALL 落库，服务重启后仍可查询历史任务。

#### Scenario: 轮询进行中的任务
- **WHEN** admin 查询一个正在执行的任务
- **THEN** 返回 running 状态与可读的进度信息

#### Scenario: 重启后查历史
- **WHEN** 服务重启后查询一个历史任务
- **THEN** 返回其最终状态与完成时间

### Requirement: 摄取失败不损既有索引
摄取 SHALL 采用影子集合构建-切换：新索引构建成功后才切换检索指向，失败 SHALL 清理影子集合并保留既有索引完全可用。

#### Scenario: 失败后检索不受影响
- **WHEN** 摄取任务在嵌入阶段失败
- **THEN** 任务标记 failed，影子集合被清理，既有知识检索功能正常

#### Scenario: 成功后切换
- **WHEN** 摄取任务成功
- **THEN** 检索指向新索引，旧索引被清理

### Requirement: 词典与标签映射同步
具备 `knowledge:update` 的角色 SHALL 能从界面触发词典同步与标签映射导入。二者为幂等同步操作（秒级完成，不走异步任务）；成功 SHALL 写操作日志。

#### Scenario: 同步词典
- **WHEN** admin 触发词典同步
- **THEN** 同步完成并返回结果摘要，操作日志新增记录

#### Scenario: 重复同步幂等
- **WHEN** 对同一词典连续两次触发同步
- **THEN** 第二次执行无副作用（幂等），结果一致

### Requirement: 任务操作审计与并发约束
摄取触发、词典同步、标签映射导入 SHALL 写入操作日志（操作者、动作、目标册/文件、任务标识）。同一册 SHALL NOT 允许并发重复摄取：已有同册任务未终结时，新触发请求 SHALL 被拒绝并提示进行中的任务。

#### Scenario: 并发触发拒绝
- **WHEN** 「必修二」摄取任务运行中再次触发同册摄取
- **THEN** 请求被拒绝，返回进行中任务的标识

#### Scenario: 触发留痕
- **WHEN** admin 触发摄取成功
- **THEN** 操作日志新增记录，含操作者、目标册与任务标识
