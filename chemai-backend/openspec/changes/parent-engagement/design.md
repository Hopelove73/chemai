# Design: parent-engagement

## D1. 三卡数据的来源（P1）

`GET /api/analytics/dashboard/parent/{student_id}` 现返回完成率/正确率/连续天数/预警状态——是汇总，无明细。三卡需要明细：

- 未完成 = 学习计划今日/本周未勾任务 + 未提交练习批次（已有表，缺聚合）
- 即将到期 = 学习计划任务按计划日期 ≥ 明天
- 本周动态 = 正确率变化、完成数、连击里程碑

方案：`parent_summary_service` 一个模块出三卡结构；`dashboard/parent` 追加 `summary` 字段（旧字段保留）；家长端三卡读同端点。**get_child_weekly 的手搓聚合迁入该 service**，迁移以「同数据同结果」等价性测试护航——API 变薄 adapter，死代码（hasattr 重复分支）直接删。

## D2. 摘要推送的调度（P1）

- 频率偏好持久化：`parents` 表加 `summary_frequency`（weekly 默认 / daily / off）——偏好是家长设置页唯一持久化项
- `app/scheduler/parent_summary.py`：APScheduler 作业，周日 20:00（Asia/Shanghai）扫偏好=weekly 的绑定关系 → 组装三卡 + ai-summary 一句话 → `NotificationService.send_to_parent`（幂等键 = 周号+学生 id）；daily 偏好次日早 8:00；off 不推
- 摘要是「拉回 App 的钩子」：推送文案带「查看错因详情」深链参数，家长端通知卡点击跳详情 pane

## D3. 家长动态的挂点与点赞（P1）

- dispatch 挂点（复用现有便捷方法与幂等键机制，通知即存储，**不新建 feed 表**）：
  - `share_artifact` 成功 → type=`artifact_shared`
  - 判卷完成（practice 生效路径）→ type=`practice_completed`
  - 连击里程碑（批 1 streak 达 7/14/30）→ type=`streak_milestone`
- 点赞持久化：`ParentNotification` 加 `liked` 布尔字段（与既有已读字段同类），`PUT /api/notification/{id}/like`
- 家长端 FeedPane 只读自己绑定学生的通知（现有 parent notifications 端点过滤逻辑复用）

## D4. 家长派任务的确认门（P2）

- 流：家长对话圈选薄弱点 → 复用教师 agent 既有 `generate_learning_plan` 生成 → **家长是决策者**的确认门（三值 approve/打回/放弃）→ 学生任务列表出现
- `gate_service` 门记录增 `audience` 字段（teacher/parent），决定权限挂学生 Data Scope（家长绑定校验复用现有 parent-scope 检查）；家长门复用 ChatPanel 家长端渲染（P2 顺带补家长端 gate 卡——家长对话里的门卡是三值全量，与学生两值门不同，不得混用）
- 频控：**每学生每周家长派任务 ≤2 次**（常量），门卡显示本周已派数；超限直接拒绝生成
- 派出的任务落学习计划任务项（既有实体），学生任务列表天然可见

## D5. persona 扩容的接缝（P2）

- 新工具 5 个读工具 = 现有 service 的薄包装（各自过 ScopeGuard）；按既有三处同步：工具函数 + `TOOLS`/`TOOL_META` personas 声明 + `parent.yaml` available_skills；`_PERSONAS_BY_ROLE` 已含 parent 无需改
- `langgraph_agent_v2` 运行时过滤自动生效
- CI 工具数断言（`812e30a` 引入）：改为**从 TOOL_META 动态推导**（一劳永逸防漂移）；若改断言方式成本高则退而更新实测值并入同一提交

## D6. 订阅入口（P2）

- 家长端设置 pane：通道列表（`GET /api/notification/channels`）+ 绑定（`POST /api/notification/subscribe`）+ 测试发送（`POST /api/notification/test-telegram`）
- 外发频率默认每周、随 `summary_frequency` 同步（同一偏好驱动站内推送与外发摘要）
