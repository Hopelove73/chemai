# Spec Delta: star-map-student

## Purpose

把"记忆星图"从教师端延伸到学生端：学生看到的是**自己的星空**——亮 = 掌握、大 = 练得多、闪得快 = 快忘了。复用同一套 3D 渲染组件与全校共享布局，教师端行为不变；移动端以 eco 模式保证可用性。星图节点统一在知识点词典码（`kp_id`）空间，与教师端、先修边、共享布局同构。

## ADDED Requirements

### Requirement: 标签到词典码的聚合
学生星图的每个节点 MUST 使用知识点词典码（`KnowledgePoint.kp_id`）作为标识。数据链路 MUST 与 `kb_error_rate` 同口径：`questions.knowledge_points`（自由文本标签）→ `kp_tag_map.tag_text` → `kp_id`；题目按 `content` 去重取 `MIN(question_id)` 规范实例；一题多标签 MUST 只取首个命中 `kp_tag_map` 的标签（首映射语义）；未命中任何词典码的题目 MUST NOT 参与聚合。现有标签空间的 `mastery/states/layers/faded` 字段 MUST 保持不变（服务现有气泡图/列表），本变更 MUST NOT 迁移它们。系统 MUST 同时提供反向映射 `kp_id → tag_text[]`（`practice_tags`），供定向练习入口使用；反向映射 MUST 与正向聚合读取同一张 `kp_tag_map`。

#### Scenario: 标签映射
- **WHEN** 一道题的 `knowledge_points` 为 `["氧化还原反应"]`，`kp_tag_map` 中该标签映射到 `M1-T2-KP06`
- **THEN** 该题的作答计入 `M1-T2-KP06` 的 `answers` 与掌握度序列

#### Scenario: 未映射标签
- **WHEN** 一道题的标签全部不在 `kp_tag_map` 中
- **THEN** 该题不贡献任何 star 的作答次数或掌握度，且不抛错

#### Scenario: 多标签首映射
- **WHEN** 一道题带 `["化学实验", "氧化还原反应"]`，只有后者在 `kp_tag_map` 中
- **THEN** 该题只计入后者对应的 kp_id，一次作答不重复计数

#### Scenario: 既有字段不变
- **WHEN** 对比改造前后同一学生的 `states/mastery/layers/edges/bindings/faded/cold_start`
- **THEN** 字段名、键空间与值完全一致

### Requirement: 学生星图数据契约
`GET /api/knowledge-map/student/{student_id}` MUST 在保持现有字段原样不变的前提下，新增：

- `stars`: 数组，覆盖 `_graph_base_nodes` 返回的全部知识点；每项 MUST 含 `kp_id`、`name`、`module`、`mastery`（0–1 或 `null`）、`answers`（整数，无作答为 0）、`retrievability`（0–1 或 `null`）、`state`（mastered/ready/locked）、`faded`（布尔）、`practice_tags`（`string[]`，可为空）
- `layout`: `{ kp_id: [x, y, z] }`，直接读全校共享 `KPLayout`

`mastery` MUST 由该学生映射到该码的作答序列经 `bkt_sequence` 计算；有效样本 < 3 MUST 输出 `null`（与 `mastery_service.MIN_SAMPLES` 同源）。`state` MUST 用码空间掌握度与 active 先修边经 `resolve_states` 计算。契约 MUST 为 additive。端点权限 MUST 保持现状（`analysis:read` + `allow_self` + `ScopeGuard`），MUST NOT 为返回布局而放宽 `/api/knowledge-map/layout` 的 `question:read` 门。

#### Scenario: 本人请求
- **WHEN** 学生请求自己的知识地图
- **THEN** 响应同时包含 `stars`（覆盖全部知识点）与 `layout`，且既有字段值与改造前完全一致

#### Scenario: 未作答知识点
- **WHEN** 某知识点该学生从未作答
- **THEN** 对应 star 的 `answers = 0`、`mastery = null`、`retrievability = null`，`state` 按先修关系计算

#### Scenario: 样本不足
- **WHEN** 某知识点只有 2 次有效作答
- **THEN** `answers = 2`、`mastery = null`（不下结论），星体按"数据不足"的暗星渲染

#### Scenario: 越权请求
- **WHEN** 学生 A 请求学生 B 的地图
- **THEN** 返回 403/404（与改造前一致），不泄露任何星图数据

#### Scenario: 布局不可写
- **WHEN** 学生端发起 `POST /api/knowledge-map/layout`
- **THEN** 被权限拒绝（学生无 `question:update`），学生页代码 MUST NOT 发起该请求

### Requirement: FSRS 可提取性聚合
系统 SHALL 按 kp_id 聚合该学生 FSRS 记忆卡的可提取性算术均值。只有 `last_review` 非空且 `stability > 0` 的卡 MUST 参与聚合；`last_review` 为空或 `stability <= 0` 的卡 MUST 被跳过。没有任何有效卡的 KP MUST NOT 出现在结果中（端点输出 `null`，不得输出 0）。题到码的映射 MUST 复用同一份 `content → kp_id` 映射。

#### Scenario: 均值
- **WHEN** 某 KP 有 2 张有效卡，可提取性分别为 0.8 与 0.6
- **THEN** 该 KP 的 `retrievability` 为 0.7（容差 1e-4）

#### Scenario: 新卡不冒充遗忘
- **WHEN** 某 KP 的卡全部 `last_review = null`
- **THEN** 该 KP 的 `retrievability = null`，而不是 0

#### Scenario: 稳定性为零
- **WHEN** 某卡的 `stability = 0`
- **THEN** 该卡被跳过，不产生除零或 NaN，不影响其他卡

#### Scenario: 到期卡更低
- **WHEN** 两张卡 stability 相同，一张刚复习、一张已逾期
- **THEN** 逾期卡的可提取性更低（FSRS 公式方向正确）

#### Scenario: 多知识点题
- **WHEN** 一道题的标签经首映射归到 KP-A
- **THEN** 该题的记忆卡只计入 KP-A 的聚合，不重复计入其他 KP

### Requirement: 学生星图编码与闪烁
学生星图 SHALL 使用与教师端相同的色相（册）与视觉底板，编码为：亮度 = 个人掌握度、大小 = 个人作答次数、星型 = `mastery === null` 或 `< 0.7` 为暗星（四芒）、作答 0 次为灰星。闪烁周期 MUST 为 `1.5 + 6.5 × retrievability` 秒（r=0 → 1.5s 急促，r=1 → 8s 缓慢），alpha 系数 MUST 限制在 0.70–1.00。`answers = 0` 或 `retrievability = null` MUST NOT 闪烁。教师端 MUST NOT 出现任何数据星闪烁。

#### Scenario: 未探索星
- **WHEN** 知识点作答次数为 0
- **THEN** 显示为灰星、不闪烁，仍保留在星图上作为空间参照

#### Scenario: 薄弱星
- **WHEN** 知识点掌握度 0.5、作答 40 次
- **THEN** 显示为暗星（四芒）、亮度按掌握度公式计算、大小按作答量计算

#### Scenario: 快忘了的星
- **WHEN** 某知识点 `retrievability = 0.1`
- **THEN** 其闪烁周期约 2.15 秒，明显快于 `retrievability = 0.9` 的星（约 7.35 秒）

#### Scenario: 教师端不闪
- **WHEN** 打开教师端班级星图并观察任意数据星 10 秒
- **THEN** 透明度无周期变化（学生端闪烁逻辑不得泄漏到教师端）

#### Scenario: 减弱动效
- **WHEN** 系统 `prefers-reduced-motion: reduce`
- **THEN** 学生端数据星闪烁停止，星尘/星云静止

### Requirement: 共享渲染组件与教师端零回归
星图渲染组件 SHALL 从 `components/teacher/` 移至 `components/star/`，并通过**可选访问器**支持两种数据编码：`kindOf`、`brightnessOf`、`sizeOf`、`tooltipHtml`、`twinklePeriodOf`，以及展示参数 `showErrorFilter`、`legendText`、`height`、`quality`。所有访问器 MUST 有默认实现（等价于现有教师逻辑）；教师页 MUST NOT 传任何新 prop。教师端搜索、飞行定位、选中高亮、筛选幽灵、常驻标签、布局持久化、错误率滑条 MUST 保持改造前行为。

#### Scenario: 教师端零回归
- **WHEN** 教师打开星图页，不传任何新 prop
- **THEN** 星图外观、错误率滑条、搜索、高亮、筛选、标签、布局上报与改造前一致

#### Scenario: 学生访问器
- **WHEN** 学生页传入学生访问器与 `showErrorFilter={false}`
- **THEN** 工具条不出现「只看错误率」滑条，图例文案为学生版说明，星体按学生编码渲染

#### Scenario: 高度可配
- **WHEN** 学生页传入 `height={520}`
- **THEN** 画布高度为 520，全屏切换后仍自适应容器

### Requirement: 学生端视图与下钻
知识地图页 SHALL 提供「星图 / 地图」视图切换，默认「星图」；「地图」视图保留现有气泡图与分层列表。点击星体 MUST 打开既有详情面板（`MobileFullPane`），并展示名称（`stars[].name`，不得显示裸码）、状态、掌握度、**作答次数**、**可提取性**（有值时）、前置知识点；「练习此点」MUST 以 `practice_tags[0]` 作为 `smartKp` 传给 `/m/practice`（定向练习按题目标签匹配，传 `kp_id` 或 `name` 会为空池）。`practice_tags` 为空时 MUST 隐藏练习按钮并提示"该知识点暂无定向练习"。锁定知识点 MUST 保持现有"先掌握前置"提示，不出现练习按钮。

#### Scenario: 默认星图
- **WHEN** 学生打开知识地图页
- **THEN** 默认显示星图视图，可一键切回地图视图

#### Scenario: 点星下钻
- **WHEN** 点击一颗已掌握/可学的星
- **THEN** 详情面板展示名称（非编码）、状态、掌握度、作答次数、可提取性（若有）、前置知识点与「练习此点」

#### Scenario: 有标签的知识点练习
- **WHEN** 某星 `practice_tags = ["氧化还原反应"]`，学生点「练习此点」
- **THEN** 跳转 `/m/practice` 并以「氧化还原反应」开定向练习，题目池非空

#### Scenario: 无标签的知识点
- **WHEN** 某星 `practice_tags = []`（该词典码在 `kp_tag_map` 中没有反向标签）
- **THEN** 练习按钮不出现，面板提示"该知识点暂无定向练习"，其余信息正常展示

#### Scenario: 锁定星
- **WHEN** 点击未解锁的星
- **THEN** 展示前置知识点与"先掌握前置"提示，无练习按钮

#### Scenario: 冷启动
- **WHEN** 教师尚未确认先修边（`cold_start = true`）
- **THEN** 星图仍渲染（无连线），页面保留现有冷启动提示条

### Requirement: 移动端 eco 模式
学生页 SHALL 以 `quality="eco"` 渲染：MUST NOT 创建 `UnrealBloomPass`；星尘数量减半（700/260/36）；天空球分段降为 16×8；渲染像素比 MUST ≤ 1.5。eco 模式 MUST 保留星尘、天空球、暗星四芒与可提取性闪烁。

#### Scenario: 无后处理
- **WHEN** 学生页加载星图
- **THEN** 场景中不存在 `UnrealBloomPass`，后处理 composer 不增加泛光 pass

#### Scenario: 装饰减半
- **WHEN** 统计 eco 模式星尘点数
- **THEN** 三层分别为 700 / 260 / 36，总数 ≤ 1000

#### Scenario: 像素比受限
- **WHEN** 设备 `devicePixelRatio = 3`
- **THEN** 渲染器实际像素比不超过 1.5

### Requirement: 深底边界与设计规范
`DESIGN.md` R-C9 MUST 被显式修订：学生知识地图页（`/m/knowledge-map`）的星图卡片纳入深底例外，范围**仅限该卡片画布**；MUST NOT 修改全局 `data-theme`、MUST NOT 扩散到同页其他区域或组件。

#### Scenario: 深底不外溢
- **WHEN** 学生页显示星图视图
- **THEN** 深空底只出现在星图卡片内；页面导航、详情面板、地图视图仍为浅色主题

#### Scenario: 全屏
- **WHEN** 学生点击星图全屏
- **THEN** 全屏层为深底画布，退出后页面主题不变
