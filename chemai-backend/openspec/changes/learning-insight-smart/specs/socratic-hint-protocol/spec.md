# Socratic Hint Protocol

学生答疑的分级提示协议：从「一句宣言」到可验收的三层协议。

## ADDED Requirements

### REQ-SHP-001: State Probe First
答疑先探状态。

**Acceptance:**
- 学生提解题类问题时，agent 首回合不讲解，先确认「做到哪一步/卡在哪」（有上下文可推断时除外）
- 评估样例（10 问回归集）断言：首回合讲解率 = 0（可推断上下文场景除外）

### REQ-SHP-002: Three-tier Hint Ladder
提示分三级放。

**Acceptance:**
- L1 方向提示 → L2 方法提示 → L3 最后一小步；学生每有推进则重置到当前层级继续引导
- 每回合只推进一步；学生明确答对才确认并收尾
- L3 之后仍卡住才允许完整讲解；讲解后必须接一道同类变式
- 协议落在 `agent/personas/student.yaml` system_prompt；不改工具集、不加路由层

### REQ-SHP-003: Never Direct Answer on First Ask
直接索要答案被引导回提示流。

**Acceptance:**
- 学生首轮就要答案 → 给 L1 提示而非答案（评估样例断言）
- 验收 = 评估样例跑分（批 2 末），而非 prompt 文本审查；样例入库 `evals/`
