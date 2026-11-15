# Mobile Stream Consumption

移动端对话的 SSE 事件消费契约：与教师端共享同一 reducer 与同一事件协议，渲染投影按学生端裁剪。

## ADDED Requirements

### REQ-MSC-001: Shared Reducer
移动 ChatPanel 复用教师端 `frontend/src/lib/agent-stream-reducer.ts` 归约事件流，删除内联 reducer。

**Acceptance:**
- `ChatPanel.tsx` 不再内联消息/工具状态机；消息、工具卡、错误态由共享 reducer 归约产出
- reducer 现有 11 用例全绿；新增移动端裁剪行为的单测（见 REQ-MSC-003）
- 学生会话历史恢复完整还原工具卡（现状只渲染纯文本）

### REQ-MSC-002: Full Event Consumption
后端发出的全部事件类型在移动端有定义的处理路径。

**Acceptance:**
- `artifact` → 工件卡（标题 + kind 图标）→ 点击跳 `/#/m/artifact/{id}` 只读预览（现有 StudentArtifactPage）
- `gate_open` → 只读提示卡（「老师正在处理一个确认步骤」），不出现任何决定按钮
- `navigate` → 仅当目标匹配 `/m/*` 路由白名单才跳转，其余忽略
- `populate` / `action` → 未知目标忽略并计数上报（console.warn 即可，不崩流）
- `context` → 忽略
- `plan_summary` / `plan_progress` → 折叠为单行进度卡（学生对话罕见，防御性渲染）

### REQ-MSC-003: Student Navigate Whitelist
学生端 navigate 跳转只允许移动路由。

**Acceptance:**
- 白名单表独立为常量（`lib/mobile-routes.ts`），单测覆盖白名单内/外目标
- 教师页面目标（exam-v2 / diagnosis / students 等）到达学生流时被忽略

### REQ-MSC-004: Stream Resume
移动端断流后可恢复回合状态。

**Acceptance:**
- 异常断开（网络断/SSE error）→ 以 DB history 重同步消息列表 + 系统提示「连接中断，已重新同步」（ADR-0016 语义与教师端一致：历史重拉即恢复机制，resume 端点仅用于门决定——实现对规格的落细，见任务 0.1 核实记录）
- 学生下一步发消息即正常续聊；手测：答题中断网→恢复→上一回合工具卡与文本完整

### REQ-MSC-005: Tabs Single Source
移动底栏 tabs 收敛为单一真源。

**Acceptance:**
- `MobileLayout.tsx` 导出学生 5-tab（含复习 tab，RCS-004 徽章挂点）与家长 3-tab 两组常量，及 `studentTabs` 接线工厂；5 个页面全部改为引用
- 常量预留 `badge` 挂点（key=review）；任一页新增 tab 只改一处
