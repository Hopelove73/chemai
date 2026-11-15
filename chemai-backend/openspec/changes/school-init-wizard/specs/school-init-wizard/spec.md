# Spec Delta: school-init-wizard

## Purpose

让学校管理员通过分步向导完成学校初始化与批量数据导入（名册/任教/考试成绩）：客户端解析与预览校验、提交既有导入端点、展示真实结果摘要；步骤按既有资源权限显隐，后端零改动。

## ADDED Requirements

### Requirement: 分步向导与按能力显隐
向导 SHALL 分四步：学校信息、学籍名册、教学安排、考试成绩。每一步 SHALL 按其端点的资源门显隐：学校信息（`school:update`）、名册（`class:create`）、任教（`teacher:create`）、成绩（`exam:create`）；无对应能力的步骤 MUST NOT 展示，也不阻塞其他步骤。

#### Scenario: 学校管理员全步骤
- **WHEN** admin 进入向导
- **THEN** 四步全部可见

#### Scenario: 教务管理员步骤裁剪
- **WHEN** academic_admin 进入向导
- **THEN** 可见学校信息（只读，无 `school:update` 则展示现状）、名册、成绩步骤，不见教学安排步骤

### Requirement: 客户端解析与预览校验
名册/任教/成绩步骤 SHALL 支持粘贴文本或上传 CSV，解析为行后先预览：展示行数、列映射与校验结果（缺列/空必填值标记）。存在校验失败行时 SHALL 明确标示，用户可修正后重新解析；提交载荷 SHALL 与导入端点请求模型同形（JSON rows）。

#### Scenario: 预览校验拦截坏行
- **WHEN** 粘贴的名册含缺学号的行
- **THEN** 预览标示该行错误，不自动提交

#### Scenario: 正常提交
- **WHEN** 预览通过并确认提交
- **THEN** 按行数提交 JSON rows 至对应导入端点

### Requirement: 结果摘要如实展示
导入完成 SHALL 展示端点返回的真实结果（新增/跳过/失败计数与原因摘要）；幂等跳过 SHALL 如实标注为「跳过」而非失败。

#### Scenario: 重复导入幂等反馈
- **WHEN** 对同一批学号重复执行名册导入
- **THEN** 结果摘要显示相应行数「跳过（已存在）」，与端点幂等语义一致

### Requirement: 学校初始化入口
学校未初始化时，学校信息步骤 SHALL 提供建校表单（`POST /api/school`）；已初始化时 SHALL 展示现状并允许具备 `school:update` 者修改。

#### Scenario: 未初始化建校
- **WHEN** admin 在全新部署打开向导
- **THEN** 第一步为建校表单，提交后学校信息可用，后续步骤解锁
