# Knowledge Map Views

学生/教师知识地图视图：就绪过滤、工件绑定、练习入口。

## ADDED Requirements

### REQ-KMV-001: Readiness State Machine
知识点三态。

**Acceptance:**
- `锁定`（任一 active 前置 BKT <0.7）→ `就绪`（全部前置 ≥0.7 且自身 <0.9）→ `精通`（≥0.9）；阈值常量与 mastery-visualization 四级映射同源
- 状态计算纯函数 + 单测（含环防御、无边知识点直通就绪/精通判定）
- 「当前可学」只呈现就绪节点（ALEKS 原则）

### REQ-KMV-002: Student Map Page
学生端地图页。

**Acceptance:**
- `/m/map` 新页（移动守卫组）；节点按拓扑分层卡式列表（430px 不用力导向 canvas 图）
- 节点着色 = 三态；FSRS 到期卡所属知识点呈褪色态（与掌握环一致）
- 冷启动：无边班级显示引导态，不对学生开放空图

### REQ-KMV-003: Artifact Binding via Share Contract
节点工件只链已分享版本。

**Acceptance:**
- 绑定只链接经 share_artifact 确认门分享给该学生的工件版本；学生不见未分享工件、不自动见新版（工件分发契约）
- 有绑定 → 节点播放入口；无绑定 → 只显示状态与「练习此点」

### REQ-KMV-004: Practice Entry
「练习此点」定向练习。

**Acceptance:**
- 有批 2 smart-practice → 定向智能练习会话（指定该知识点）；未落地时降级跳错题本对应筛选
- 锁定节点不可发起练习

### REQ-KMV-005: Teacher Map View
教师端视图。

**Acceptance:**
- 教师视图：全班知识点状态分布 + 边管理（启用/禁用）+ navigate 矩阵新增 `map` 目标
- 教师可按学生查看个人三态着色（复用既有学生抽屉模式）
