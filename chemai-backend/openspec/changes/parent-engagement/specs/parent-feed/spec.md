# Parent Feed

家长动态（CONTEXT.md「Parent Feed」）：高频小额作品卡，替代「低频大报告」的主导地位。

## ADDED Requirements

### REQ-PFD-001: Event-triggered Cards
孩子学习动作自动产生动态卡。

**Acceptance:**
- 三个 dispatch 挂点（复用 NotificationService 便捷方法 + 幂等键，通知即存储，不新建 feed 表）：
  - 工件分享成功 → `artifact_shared`（含工件标题/kind/被分享版本号）
  - 练习判卷生效 → `practice_completed`（含知识点、正确题数）
  - 连击里程碑（7/14/30 天）→ `streak_milestone`
- 挂点位于 `share_artifact` 成功路径与判卷生效路径；判卷覆盖场景不含（只有确认生效才推）

### REQ-PFD-002: Feed Pane
家长动态页。

**Acceptance:**
- FeedPane 按时间倒序渲染绑定学生的通知（复用现有 parent notifications 端点）；分页或最近 N 条
- 卡片可点赞：`ParentNotification.liked` 布尔字段 + `PUT /api/notification/{id}/like`（家长只能点自己收到的）
- 学生动作后 ≤1 个轮询周期家长可见（与现有通知轮询节奏一致）

### REQ-PFD-003: No Sensitive Leakage
动态不泄露口径外信息。

**Acceptance:**
- 动态卡不含班级排名、不含他人信息；工件卡只写「学习了《X》」，版本细节按工件分发契约（学生只见被分享版本，家长只见「有这个事件」）
