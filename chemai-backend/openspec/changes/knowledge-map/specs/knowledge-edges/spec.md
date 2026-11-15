# Knowledge Edges

知识点先修边：agent 依据数据提议、教师确认门定案、可随时禁用。

## ADDED Requirements

### REQ-KED-001: Evidence-based Proposal
先修边由数据证据 + 学科常识提议。

**Acceptance:**
- 证据两类：知识点共现（同卷/同批错题）、错因迁移（前置未掌握 → 后继错误率显著更高，用既有 barrier/正确率数据统计）
- `propose_knowledge_edges`（teacher persona）：每条候选边附一句依据；依据为空的边不得提出
- 不引入任何外部图谱数据（K12-KGraph 等 NC 数据明确排除）

### REQ-KED-002: Teacher Confirmation Gate
教师逐边定案。

**Acceptance:**
- 提议走确认门：逐边采纳 / 驳回；采纳落 `knowledge_edges{from_kp, to_kp, status}`（active）；驳回可重提议并可见历史
- 提议集合成环时，确认门前警告并列出环路径

### REQ-KED-003: Disable Anytime
错边可随时禁用。

**Acceptance:**
- 教师端边管理：active ↔ disabled 即时切换；disabled 边立即退出就绪过滤计算
- 禁用不影响历史路径展示（仅影响「当前可学」）
