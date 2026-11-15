# Spec Delta: rag-retrieval-probe

## Purpose

给教研人员一个当场验证"这样问能不能召回那一页"的工具：临时覆盖检索参数、看命中与分数、把结果沉淀成金标问题或坏案例。

## ADDED Requirements

### Requirement: 临时参数覆盖
召回测试 SHALL 接受 query 与临时配置（TopK、相似度阈值、图增强开关），并 MUST 只影响本次调用，不得改写系统默认检索参数或运行时开关。

#### Scenario: 覆盖不落库
- **WHEN** 用户以 TopK=10 跑一次测试，随后以默认参数再问一次
- **THEN** 第二次调用使用默认 TopK，第一次的覆盖未持久化

#### Scenario: 图增强开关
- **WHEN** 关闭图增强跑测试
- **THEN** 命中列表不含图扩展召回项；开启时命中项带 `from_graph=true` 标记

### Requirement: 命中结果呈现
结果 SHALL 返回命中块的排名、内容、分数、来源文档与章节、是否启用、是否图扩展召回；无命中时 SHALL 返回空列表并给出明确提示，MUST NOT 500。

#### Scenario: 无命中
- **WHEN** query 与语料无关
- **THEN** 返回空列表与"未召回任何分段"的提示

#### Scenario: 检索不可用
- **WHEN** 向量库不可用
- **THEN** 返回结构化错误（说明 chroma/模型不可用），而不是抛出未处理异常

### Requirement: 金标沉淀
用户 SHALL 能勾选期望命中块，选择匹配规则（全部命中 / 任一命中），把本次查询保存为金标问题；SHALL 能标记本次结果为坏案例（原因七选一）。

#### Scenario: 保存金标
- **WHEN** 用户勾选 2 个 chunk 并选择「全部命中」保存
- **THEN** 金标集新增一条记录（状态 draft），含该问题与两个期望块

#### Scenario: 标记坏案例
- **WHEN** 用户把一次结果标为「检索未命中」
- **THEN** 生成一条待修复记录，可在问答日志按原因筛到

### Requirement: 权限
召回测试端点 SHALL 以 `knowledge:update` 为门；`academic_admin` / `teacher` MUST 403。

#### Scenario: 教师不可用
- **WHEN** 教师调用召回测试端点
- **THEN** 返回 403
