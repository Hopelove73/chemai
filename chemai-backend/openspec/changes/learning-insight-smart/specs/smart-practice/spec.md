# Smart Practice

CAT 自适应练习会话（CONTEXT.md「Smart Practice」）：逐题选题、每题 θ 更新、停止规则、能力估计落点。

## ADDED Requirements

### REQ-SMP-001: Session Lifecycle
智能练习会话三端点闭环。

**Acceptance:**
- `POST /api/practice/smart/start`（知识点范围，默认薄弱点）→ `POST .../answer`（判卷 + θ 更新 + 返回下一题）→ `POST .../finish`
- 全部 allow_self + ScopeGuard；作答落既有 StudentAnswer 链路（练习生效语义，即时进诊断）
- PracticePage 新增「智能练习」模式入口

### REQ-SMP-002: Item Selection
catsim 最大信息量选题。

**Acceptance:**
- 选题来自已标定 IRT 参数的题池（catsim 最大信息量，实现在 smart_practice_service）；θ 初值取学生既有能力估计，无历史则 0
- **回退硬要求**：无标定参数的题回退「难度最接近 θ 定向」的确定性排序（优于随机——可复现）；未标定题不进 EAP/SE（防伪信息量压低 SE 误停），会话汇总标注「未标定题占比」
- 不出重复题、不出该会话已答题

### REQ-SMP-003: Stop Rule
停止规则可预期。

**Acceptance:**
- SE≤0.3 或 8 题（先到为准）即结束；纯函数实现 + 边界单测（第 8 题、SE 达标即停）
- 结束页：本轮 θ 变化、能力估计、薄弱点快照列表、建议（「去复习 X」→复习中心 /「练 Y」→再开一轮）

### REQ-SMP-004: Mastery Feedback Loop
掌握度更新走既有链路。

**Acceptance:**
- 作答经既有判卷→诊断链路自动更新 BKT（消灭率自动反映）；smart 会话**不直写学习计划**（计划是教师 agent 产物）
- 会话结束的薄弱点快照仅为前端展示，与 mastery_service 同源
