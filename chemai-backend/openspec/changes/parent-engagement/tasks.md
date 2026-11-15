# Tasks: parent-engagement

## 1. P1 三卡 + 浅模块加深

- [x] 1.1 `parent_summary_service`（三卡聚合）+ 等价性快照测试
- [x] 1.2 `get_child_weekly` 瘦身为薄 adapter + 删 hasattr 死代码
- [x] 1.3 `dashboard/parent` 追加 `summary` 字段（旧字段保留）
- [x] 1.4 `parents.summary_frequency` 字段 + 设置 UI（周/日/关）
- [x] 1.5 `app/scheduler/parent_summary.py` 周日 20:00 推送作业 + 幂等键（bulk 分组聚合：680 绑定全表扫退化为一次 GROUP BY，调度测试驱动出该性能修复）
- [x] 1.6 ai-summary 接线 + 错因人话卡（消灭率 + 一件事）
- [x] 1.7 ParentHomePage 拆 SummaryPane / ReportPane

## 2. P1 家长动态

- [x] 2.1 通知 type 常量（artifact_shared / practice_completed / streak_milestone）
- [x] 2.2 三个 dispatch 挂点（share_artifact 成功路径 / 判卷生效路径 / 连击里程碑）
- [x] 2.3 `ParentNotification.liked` 字段 + like 端点
- [x] 2.4 FeedPane 组件（倒序 + 点赞 + 深链跳转）

## 3. P2 persona 扩容 + 派任务 + 订阅

- [x] 3.1 5 个读工具（薄包装 + ScopeGuard）+ 三处同步注册
- [x] 3.2 `gate_service` 增 audience 字段 + 家长门决定权限（绑定校验）
- [x] 3.3 家长端三值门卡渲染（与学生两值门隔离）
- [x] 3.4 派任务频控纯函数（每周 ≤2，单测）+ 门卡显示已派数
- [x] 3.5 订阅设置 pane（channels / subscribe / test-telegram）
- [x] 3.6 CI 断言动态化（或实测值同提交更新）+ 家长 9 工具冒烟

## 4. 验收

- [x] 4.1 pytest 全绿；等价性快照、频控、dispatch 幂等单测全绿
- [ ] 4.2 家长全链路手测：绑定→三卡→动态→派任务→学生可见→订阅测试邮件到达——待起服务手测
- [ ] 4.3 手测清单 5–8 条附 PR 描述——待起服务手测
