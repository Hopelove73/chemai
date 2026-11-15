# Class Star Map

教师端班级知识星图：知识点为星、掌握度为亮度、先修边为星座线；暗星下钻直达出题。

## ADDED Requirements

### REQ-CSM-001: Class-Scoped Graph Data
按班聚合的图谱读侧，口径与全局错误率同源。

**Acceptance:**
- `GET /api/knowledge-map/class/{class_id}/graph`：节点 = 全部知识点（161），带 `class_error_rate` / `class_answers`（作答事件级口径、题按 content 去重、一题多标签取首个映射——与 `kb_error_rate` 全局口径完全同源，仅增加班级过滤）；边 = 全量（含 status）
- 权限：教师仅可读自己任教的班级（`ScopeGuard("class_id")` + `PermissionChecker("question", "read")`）；无任教关系 403
- 作答 < MIN_ANSWERS(5) 的知识点不带 `class_error_rate` 且 `class_answers` 记 0（整条压制、视同无可信样本——与全局口径同尺，阈值一致）
- 聚合失败或空数据静默降级为无错误率字段，端点不 500

### REQ-CSM-002: Star Encoding
星图视觉编码纪律。

**Acceptance:**
- 亮度 = 掌握度（`class_error_rate` 反转映射，带可见下限）；大小 = 作答量（sqrt 归一）；无样本星 = 固定灰星，不参与暗星观感
- 色相 = 册/专题（6 色，2026-10-31 用户定案：恢复分类色相，R-C9 显式豁免 R-C8）；亮度 = 掌握度；大小 = 作答量。**不做任何模拟/伪造的时间衰减**（真 FSRS 可提取性属 star-map-student 个人粒度）
- 画布工具条：巡航开关（默认开，速度 0.8）与全屏切换（Fullscreen API，ResizeObserver 自适应）
- 选中星自动暂停巡航（被选星与白环停在视野内）；关闭下钻面板时仅恢复「由选星触发」的暂停——用户中途手动切换过巡航则保持手动状态
- 全屏时下钻面板随画布进入全屏层（面板作为 StarMap 叠加层插槽 children，非页面级兄弟节点）
- 初始取景（zoomToFit）每份数据只执行一次；此后引擎再停（点星拖拽等）不重置镜头；节点拖拽关闭（防扰动全班共用布局）
- 布局：按册/专题力导向聚为星系团；active 先修边渲染为低透明度连线，disabled 边不画

### REQ-CSM-003: Deep-Canvas Seam
深底独立接缝。

**Acceptance:**
- 星图画布区域整块深底，为教师端浅色红线的**唯一显式例外**；深底令牌（`--star-canvas-*`）定义在星图组件作用域，不进全局 data-theme，不劫持全局令牌（R-K3）
- DESIGN.md 增补 R-C9 规则：「深底画布仅允许出现在显式声明的独立接缝页区，当前唯一实例 = /kmap 星图」
- list 视图与边管理维持浅色常规可用，视图切换即时

### REQ-CSM-004: Drill-down to Action
暗星下钻与出题闭环。

**Acceptance:**
- 单星点击 → 下钻面板：知识点档案（名称/描述/方程式/必背细则——复用 teacher graph 既有字段）+ 薄弱学生名单（懒加载 `GET .../kp/{kp_id}/weak-students`，每人姓名 + 错误次数 + 最近 1 条错误样本）+ 先修上下文（直接前驱的班级掌握状态，前端本地计算）
- 面板内「出变式题」→ 跳转出题工作台并预选该知识点（URL 参数），生成/审核/派发全走既有流程与确认门；星图不新增任何写操作
- 按班切换即时生效（默认教师第一个任教班）；不做跨班聚合、框选多星、图内嵌对话
