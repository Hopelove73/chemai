## Why

家长端 2 页 / 4 工具，且是浅模块堆叠：`get_child_weekly` 把 SQL 聚合与 streak 计算写在 API 函数体内（含无效 hasattr 死代码），前端绕开已建成的 `/api/analytics/dashboard/parent`、`/child/{sid}/report/ai-summary` 两个深 seam；报告是纯文本弹层——「低频大报告」而非「高频小额反馈」。后端权限模型（Self Role + 绑定学生 Data Scope）与外发网关（Telegram/Email）全部就绪，家长端「能看懂、能行动、能被触达」的 seam 全部空着。

## What Changes

- **修改** 浅模块加深：`get_child_weekly` 聚合下沉新模块 `parent_summary_service`（三卡结构单一产出点），API 变薄 adapter，删死代码；家长端改接 `dashboard/parent` 并**扩展其返回**三卡字段（未完成明细/即将到期/本周动态——现有返回只有完成率/正确率/连击/预警汇总，无明细）
- **新增** 家长摘要三卡（Guardian Summary）：刻意不放分数；频率偏好（周/日/关，默认周）持久化 + 周日摘要推送调度作业
- **新增** 错因人话卡：接 `ai-summary`（已有零调用）+ 松鼠AI 式翻译 + 消灭率 + 「家长现在能做的一件事」
- **新增** 家长动态（Parent Feed）：工件分享/练习完成/连击里程碑 → `NotificationService.dispatch` 新 type → 动态页卡片 + 点赞（持久化）
- **新增** 家长 Agent 扩容 4→9 工具（读计划/读掌握度四级/读错因分布/读班级对比/读工件观看记录）
- **新增** 家长派任务（走确认门三值）+ 每周频控；**新增** 订阅入口 UI（channels/subscribe，频率默认每周）
- **修改** `ParentHomePage.tsx`（565 行内联三 Pane）拆组件；CI 工具数断言同步

## Capabilities

### New Capabilities
- `guardian-summary`: 三卡摘要 + 频率偏好 + 周日推送调度
- `parent-feed`: 高频小额动态流 + 点赞持久化
- `parent-agent-tools`: persona 扩容 + 派任务确认门 + 订阅入口

### Modified Capabilities
- 无（dashboard/parent 返回体扩展记入 guardian-summary spec）

## Impact

- **新增文件**: `app/services/parent_summary_service.py`（聚合下沉落点）, `app/scheduler/parent_summary.py`（周日推送作业）, `frontend/src/pages/mobile/parent/{FeedPane,SummaryPane,ReportPane}.tsx`
- **修改文件**: `app/api/parent.py`（get_child_weekly 瘦身 + 死代码删除）, `app/api/analytics.py`（dashboard/parent 三卡字段）, `app/models/database.py`（家长频率偏好、通知 liked 字段）, `app/services/notification_service.py`（新 type 常量）, `app/services/artifact_service.py` + `app/jobs/handlers.py`（dispatch 挂点）, `agent/tools/parent*.py` + `agent/tools/__init__.py` TOOL_META + `agent/personas/parent.yaml`, `app/services/gate_service.py`（决策者=家长）
- **依赖**: 无新外部依赖
- **测试**: 聚合下沉等价性（瘦身前后同数据同结果）、feed dispatch 幂等键、派任务频控纯函数、CI 断言绿
