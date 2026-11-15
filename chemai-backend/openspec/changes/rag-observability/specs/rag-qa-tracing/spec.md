# Spec Delta: rag-qa-tracing

## Purpose

把每一次 AI 问答变成可复查的证据链：学生问了什么、检索到哪些 chunk 与分数、模型看到什么上下文、答了什么、学生怎么反馈。没有它，幻觉无法定位、内容无法改进、评测集无法积累。

## ADDED Requirements

### Requirement: 逐请求证据链留存
系统 SHALL 为每一次调用知识检索的问答写入一条 trace，至少包含：问题、检索配置快照、命中块列表（块标识 / 分数 / 排名 / 是否图扩展召回）、是否零召回、Top1 分数、回答、引用、拒答标记、延迟、模型名、KB 索引版本、时间。

#### Scenario: 正常问答
- **WHEN** 学生提问且检索返回 3 个 chunk
- **THEN** 产生一条 trace，`hits` 含 3 项且带分数与排名，`zero_hit=false`

#### Scenario: 零召回
- **WHEN** 检索没有返回任何 chunk
- **THEN** trace 记录 `zero_hit=true`、`hits=[]`，并保留当时的检索配置

### Requirement: 埋点不影响回答
trace 写入 SHALL 与回答链路解耦：写入失败 MUST NOT 导致回答失败或延迟明显增加。写入 SHALL 使用独立会话与短事务。

#### Scenario: 落库异常
- **WHEN** trace 写入抛出异常（如表锁）
- **THEN** 学生仍收到正常回答，异常被记录到服务日志而非返回给用户

### Requirement: 日志列表与筛选
问答日志页 SHALL 支持按时间、是否拒答、是否零召回、是否低分命中（Top1 低于阈值）、是否有负反馈、学生、班级筛选，并支持按时间/分数/延迟排序。

#### Scenario: 找零召回
- **WHEN** 管理员筛选「零召回」
- **THEN** 只返回 `zero_hit=true` 的 trace，且能看到当时的问题与配置

#### Scenario: 找低分命中
- **WHEN** 管理员筛选「低分命中」
- **THEN** 只返回有命中但 Top1 分数低于阈值的 trace

### Requirement: 证据链详情
详情 SHALL 按七段呈现：问题与会话、检索配置、命中块与分数、送入模型的上下文（或摘要 + token）、回答与引用、模型与延迟与索引版本、反馈与标注。

#### Scenario: 定位幻觉
- **WHEN** 教师报告某答案错误
- **THEN** 管理员能在详情中判断是未检索到、排名过低、还是检索正确但回答编造

### Requirement: 标注与回流
具备 `knowledge:update` 的角色 SHALL 能标注一条 trace 为好/坏，坏必须选择原因（检索错 / 块质量差 / 缺知识 / 幻觉 / 不完整 / 超纲 / 误拒）。标注后 SHALL 能：修正答案、把该问题回流为金标问题、一键禁用命中的问题块。

#### Scenario: 标注坏案例
- **WHEN** 管理员把一条 trace 标为「幻觉」
- **THEN** 该 trace 的 `ops_label=bad`、`bad_reason=幻觉`，可按原因筛选

#### Scenario: 回流金标
- **WHEN** 管理员选择「加入金标集」
- **THEN** 该问题被写入金标集（状态 draft），并记录来源为该 trace

### Requirement: 线上质量指标
系统 SHALL 提供指标端点与概览卡：检索命中率、零召回率、拒答率、负反馈率、低分命中率，口径固定并可指定时间窗。

#### Scenario: 指标口径
- **WHEN** 查询近 7 天指标
- **THEN** 命中率 = 有命中的请求 / 总请求，零召回率 = `zero_hit` / 总请求，两者之和为 1

### Requirement: 数据保留与权限
trace 数据 SHALL 有明确保留策略（默认保留 180 天，可配置）。问答日志页 SHALL 以 `knowledge:update` 为门，`teacher` MUST 403；学生只能看到自己的反馈入口，MUST NOT 访问他人 trace。

#### Scenario: 教师不可访问
- **WHEN** 教师请求问答日志
- **THEN** 返回 403

#### Scenario: 过期清理
- **WHEN** trace 超过保留期
- **THEN** 被清理任务删除，清理动作写日志
