# Spec Delta: rag-eval-regression

## Purpose

让"改完知识库能不能发"变成可判定：金标集沉淀期望，跑评测得到指标，baseline 与 candidate 逐题对比显示"修好了哪些、弄坏了哪些"。

## ADDED Requirements

### Requirement: 金标集管理
系统 SHALL 维护金标问题：问题、期望块、期望答案、章节与知识点标签、匹配规则、状态（active / draft / stale / paused）、来源。SHALL 支持增删改查与 CSV 导入导出。

#### Scenario: 期望块被删除
- **WHEN** 金标问题引用的 chunk 因重摄取消失
- **THEN** 该问题状态变为 stale，评测时跳过并提示

#### Scenario: CSV 往返
- **WHEN** 导出金标集为 CSV 后再导入
- **THEN** 记录数、匹配规则与标签保持一致

### Requirement: 异步评测
跑评测 SHALL 立即返回任务标识并异步执行，支持轮询进度；完成后 SHALL 产出整体指标与逐题结果（实际命中块、是否通过、判分理由）。

#### Scenario: 启动评测
- **WHEN** 用户对 active 金标集发起评测
- **THEN** 立即返回 job 标识，任务完成后可查询逐题结果

#### Scenario: 任务失败
- **WHEN** 评测过程中检索不可用
- **THEN** 任务标记失败并给出错误摘要，不产生半份指标

### Requirement: 指标
评测 SHALL 至少计算：期望命中率（按匹配规则）、拒答正确率；SHALL 预留忠实度与答案相关性字段。

#### Scenario: 命中率口径
- **WHEN** 10 道金标题中 9 道满足匹配规则
- **THEN** 期望命中率为 90%

### Requirement: 逐题回归对比
系统 SHALL 支持选择 baseline 与 candidate 两次评测，输出逐题差异：新增失败、由败转过、仍失败、仍通过，并可下钻到单题详情。

#### Scenario: 弄坏了一题
- **WHEN** baseline 通过的题在 candidate 中失败
- **THEN** 该题出现在"新增失败"列表，可看到两次的命中块差异

### Requirement: 发布提示
KB 变更后若最近一次评测未达阈值，KB 生产页 SHALL 显示"最近评测未通过"提示与跳转入口；SHALL NOT 阻断发布（软门禁）。

#### Scenario: 软门禁
- **WHEN** 评测未通过时管理员触发摄取
- **THEN** 允许执行，但页面持续显示未通过提示

### Requirement: 权限
金标集与评测端点 SHALL 以 `knowledge:update`（写）/ `knowledge:read`（读）为门；`teacher` MUST 403。

#### Scenario: 教师不可访问
- **WHEN** 教师请求金标集或评测结果
- **THEN** 返回 403
