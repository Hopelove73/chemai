# Guardian Summary

家长摘要三卡（CONTEXT.md「Guardian Summary」）：未完成 / 即将到期 / 本周动态，刻意不放分数；固定频率可调。

## ADDED Requirements

### REQ-GDS-001: Three Cards Without Scores
家长首页三卡。

**Acceptance:**
- 未完成（本周未勾计划任务 + 未提交练习批次计数 + 明细前 3）/ 即将到期（明日及以后的计划任务）/ 本周动态（正确率变化、完成数、连击里程碑）
- **无任何分数展示**（Google Classroom 三段式：摘要不含分数，分数属教师端口径）
- 数据源 `GET /api/analytics/dashboard/parent/{student_id}` 追加的 `summary` 字段；旧字段保留向后兼容

### REQ-GDS-002: Shallow Module Deepened
浅模块加深。

**Acceptance:**
- `get_child_weekly` 的 SQL 聚合与 streak 计算迁入 `parent_summary_service`；API 函数体只剩参数校验 + 调用 + 响应（薄 adapter）
- 无效 hasattr 死代码删除
- 迁移以等价性测试护航：同数据下瘦身前后返回结果一致（快照测试）

### REQ-GDS-003: Frequency Preference
摘要频率可调且持久化。

**Acceptance:**
- 家长设置：每周（默认）/ 每天 / 关闭；存 `parents.summary_frequency`
- 站内推送与外发摘要（Telegram/Email）由同一偏好驱动

### REQ-GDS-004: Scheduled Push
摘要定时推送。

**Acceptance:**
- APScheduler 作业：weekly 周日 20:00（Asia/Shanghai）、daily 次日 08:00、off 不推
- 幂等键 = 周号/日期 + 学生 id；重复调度不重发
- 推送带深链参数，家长端通知卡点击跳详情 pane（摘要是拉回 App 的钩子）

### REQ-GDS-005: Plain-language Barrier Card
错因人话卡。

**Acceptance:**
- 接 `POST /api/parent/child/{sid}/report/ai-summary`（已有零调用）产出通俗错因句（「不是不会，是没看清『过量』条件」级别）
- 附消灭率 n/m（读批 1 elimination 端点，同源）+ 每卡「家长现在能做的一件事」一句
