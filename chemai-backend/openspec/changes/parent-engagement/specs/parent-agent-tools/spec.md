# Parent Agent Tools

家长 Agent 扩容（4→9 工具）+ 派任务确认门 + 订阅入口。

## ADDED Requirements

### REQ-PAT-001: Read Tool Expansion
家长可问出「孩子最近哪个知识点最弱」级别的问题。

**Acceptance:**
- 新增 5 个读工具（现有 service 薄包装 + ScopeGuard）：读学习计划 / 读掌握度四级 / 读错因分布 / 读班级对比 / 读工件观看记录
- 三处同步：工具函数 + `TOOLS`/`TOOL_META` personas 声明 + `parent.yaml` available_skills；`_PERSONAS_BY_ROLE` 无需改
- 数据严格限于绑定学生（Data Scope）；越权问题工具返回空而非报错崩会话

### REQ-PAT-002: Parent Task Assignment with Gate
家长派任务走确认门。

**Acceptance:**
- 流：家长对话圈选薄弱点 → 复用教师 agent `generate_learning_plan` 生成 → 家长为决策者的确认门（三值：approve / 打回 / 放弃）→ 学生任务列表出现
- `gate_service` 门记录增 `audience`（teacher/parent）；家长门的决定权限校验家长-学生绑定（复用 parent-scope 检查）
- 家长端 ChatPanel 渲染三值门卡（家长门与学生两值门不得混用——学生无门卡，见 student-daily-loop S4 spec）
- 频控：每学生每周家长派任务 ≤2 次（常量）；门卡显示本周已派数；超限拒绝生成
- 派出任务落学习计划任务项（既有实体）；学生确认门（两值）不受影响

### REQ-PAT-003: Channel Subscription UI
外发订阅入口。

**Acceptance:**
- 家长设置 pane：通道列表（`GET /api/notification/channels`）+ 绑定（`POST /api/notification/subscribe`）+ 测试发送（`POST /api/notification/test-telegram`）
- 外发频率默认每周，随 `summary_frequency` 同一偏好驱动

### REQ-PAT-004: CI Tool-count Assertion
工具数断言不漂移。

**Acceptance:**
- 断言改为从 TOOL_META 动态推导（首选）；若改造成本高，更新为实测值并与 P2 工具合入同一提交
- P2 落地后冒烟：家长 persona 会话实际可见 9 工具、教师 41 不变
