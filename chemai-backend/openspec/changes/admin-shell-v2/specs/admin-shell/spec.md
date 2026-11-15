# Spec Delta: admin-shell

## Purpose

把管理功能从教师侧栏里分离出来：教师保持干净的教学导航，管理员通过「管理中心」进入独立的管理导航（分组折叠），并可一键返回上次的教学页面。权限矩阵仍是唯一真源，分组只影响展示。

## ADDED Requirements

### Requirement: 双模式导航
系统 SHALL 提供教学态与管理态两种侧栏。教学态 SHALL 只显示教师的日常功能；管理态 SHALL 只显示管理功能并按分组折叠。当前处于哪一态 SHALL 由路由前缀 `/admin` 决定，而不是由额外的开关状态决定。

#### Scenario: 教师登录
- **WHEN** 教师（无任何管理能力）登录
- **THEN** 侧栏只有教学项，且不渲染「管理中心」入口

#### Scenario: 管理员登录
- **WHEN** 学校管理员登录并落在 `/admin`
- **THEN** 侧栏为管理导航，按「概览 / 组织与账号 / 内容与 RAG / 运营与安全 / 系统」五组呈现，且顶部显示「← 返回教学」

#### Scenario: 教师直接访问管理路由
- **WHEN** 教师直接输入 `/admin/students`
- **THEN** 被路由守卫送回其可访问的第一页（后端端点同样 403，前端守卫只是 UX）

### Requirement: 管理中心入口与返回
管理态入口 SHALL 只在当前身份拥有至少一个管理菜单时出现。进入管理态 SHALL 记住进入前的教学页路径；「返回教学」SHALL 回到该路径（默认 `/chat`），且刷新页面后仍可返回。

#### Scenario: 往返不丢上下文
- **WHEN** 管理员从 `/class` 点「管理中心」进入 `/admin`，再点「返回教学」
- **THEN** 回到 `/class` 而不是 `/chat`

#### Scenario: 首次直接进管理态
- **WHEN** 管理员直接在地址栏打开 `/admin`（没有进入前的教学页记录）
- **THEN** 「返回教学」回到 `/chat`

#### Scenario: 教务管理员
- **WHEN** `academic_admin` 登录
- **THEN** 可见「管理中心」，管理导航只含其有权限的项（概览 / 学生账号 / 家长与绑定 / 班级与年级），不含教师审批、操作日志、学校设置、内容与 RAG 组

### Requirement: 菜单分组由后端声明
每个管理菜单 SHALL 在 `MENUS` 中声明所属分组（`overview` / `org` / `content` / `ops` / `system`）。分组顺序 SHALL 固定为上述五组。前端 MUST NOT 硬编码"哪些角色看哪些菜单"，只按后端下发的 key 与分组渲染。

#### Scenario: 分组顺序稳定
- **WHEN** 管理员打开管理侧栏
- **THEN** 五组按固定顺序出现，组内顺序沿用 `MENUS` 声明顺序

#### Scenario: 空组不渲染
- **WHEN** 某角色的管理菜单全部属于两组
- **THEN** 其余三组不渲染标题（不出现空分组）

### Requirement: 旧路由兼容
`/admin/users` SHALL 重定向到 `/admin/teachers`，保留原有查询串与书签可用性。

#### Scenario: 旧书签
- **WHEN** 访问 `/admin/users`
- **THEN** 重定向到 `/admin/teachers`，页面正常渲染

### Requirement: 高亮与折叠状态
管理态的侧栏高亮 SHALL 遵循路径段边界与最长前缀匹配（`/admin/students` 不得点亮 `/admin`）。分组折叠状态 SHALL 至少在当前会话内保持；当前页所在组 SHALL 自动展开。

#### Scenario: 子页高亮
- **WHEN** 位于 `/admin/rag/traces`
- **THEN** 只有「问答日志」高亮，「概览」不亮

#### Scenario: 折叠不隐藏当前页
- **WHEN** 管理员刷新在 `/admin/rag/evals`
- **THEN** 「内容与 RAG」组自动展开并高亮该项
