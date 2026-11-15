# Spec Delta: star-map-constellation

## Purpose

为教师端班级知识星图补足结构感与局部交互：先修关系以曲率连线呈现为星座，六个册各有星云核作为空间容器，悬停即可读出局部关系，首次进入有镜头引导。所有新增视觉只作用于此画布，不改变任何数据编码与后端契约。

## ADDED Requirements

### Requirement: 曲率星座连线
所有 active 先修边 SHALL 以曲率 `0.3` 渲染（`linkCurvature = 0.3`），替代现有直线。既有连线颜色优先级 MUST 保持不变：幽灵端点 alpha 0.04 > 高亮链 0.85 / 高亮时非链 0.05 > 默认 0.30；连线宽度、方向粒子、粒子速度 MUST 保持不变。实现 MUST NOT 依据节点坐标做颜色/透明度分级。

#### Scenario: 全景可见星座形态
- **WHEN** 加载 161 节点班级星图并缩放到全景
- **THEN** 先修边呈连续弧线，可读出团内星座结构，而不是难以辨认的直线网格

#### Scenario: 高亮与幽灵不回退
- **WHEN** 选中一颗星，或开启某册筛选
- **THEN** 高亮链 alpha 0.85 / 非链 0.05、幽灵边 0.04 与改造前一致；粒子仍沿「前置 → 后续」方向流动

#### Scenario: 曲率不影响命中
- **WHEN** 点击连线附近的空白区域
- **THEN** 不误选节点；点击星体仍正常下钻

### Requirement: 星系团星云核
每个出现在当前数据中的册 SHALL 在其锚点（`moduleAnchor`）位置渲染一个星云核 sprite：材质颜色 = 该册色、opacity 0.045、`NormalBlending`、`depthWrite: false`、`renderOrder ≤ -2`、尺寸 900×900。星云核 MUST NOT 参与 raycast、MUST NOT 进入 `nodeThreeObject`、MUST NOT 随选中/hover/筛选重建或改变透明度。册集合变化时允许增删，且 MUST 幂等（重复挂载不叠加）。

#### Scenario: 六团可读
- **WHEN** 数据包含全部 6 个册
- **THEN** 场景中存在 6 个星云核，位置与六个锚点一致，颜色与册色一致

#### Scenario: 隐藏册仍保留空间参照
- **WHEN** 教师点击图例隐藏「必修一」
- **THEN** 该册星体变幽灵、连线淡出，但星云核保持可见且透明度不变

#### Scenario: 不爆白
- **WHEN** 相机正对任一星云核
- **THEN** 画面不出现白色光斑或整片泛白；核的亮度明显低于数据星

#### Scenario: 幂等
- **WHEN** 触发多次 `bindFg`（热更新/重新挂载）
- **THEN** 星云核数量不增长

### Requirement: Hover 一度邻域聚焦
系统 SHALL 在鼠标悬停星体时聚焦其一度邻域：自身 + 全部直接前置 + 全部直接后继（仅 `status === 'active'` 的边）。邻域 MUST 由纯函数 `hoverNeighborhood(edges, kpId)` 计算。未选中任何星时，邻域成员透明度系数为 1、其余为 0.15；两端都在邻域内的连线 alpha 0.6、其余 0.05。已选中星时 MUST 保持选中高亮，hover 不改变视觉。幽灵星 MUST 保持 0.08。指针离开后 MUST 完全复原。hover 状态变化 MUST NOT 重建 sprite 或标签。

#### Scenario: 悬停读出邻域
- **WHEN** 鼠标悬停在星 A 上（A 无选中）
- **THEN** A、A 的全部直接前置与直接后继保持可见，其余星淡至 0.15，相关连线较亮

#### Scenario: 选中优先
- **WHEN** 已选中星 B（高亮祖先链），鼠标扫过星 C
- **THEN** B 的高亮链保持原样，不因 hover 改变

#### Scenario: 幽灵优先
- **WHEN** 某册被筛选隐藏后，鼠标悬停该册的星
- **THEN** 该星保持幽灵透明度 0.08，不因 hover 变亮

#### Scenario: 离开复原
- **WHEN** 指针离开星体
- **THEN** 所有星与连线恢复到 hover 前的状态

#### Scenario: 纯函数边界
- **WHEN** 对孤立星、只有入边、只有出边、含 disabled 边的图调用 `hoverNeighborhood`
- **THEN** 返回集合只含自身与 active 边的直接邻居；不递归、不含 disabled 邻居

#### Scenario: 触屏不受影响
- **WHEN** 在触屏设备点击星体
- **THEN** 走既有选中路径，不出现 hover 状态残留

### Requirement: 开场运镜
组件首次挂载时，相机 SHALL 从距原点 2200 的远景开始，在首次布局收敛后以 2000ms 缓动执行 `zoomToFit`。后续切换班级 MUST 沿用现有 900ms 取景，不重复长镜头。`prefers-reduced-motion: reduce` 时 MUST 瞬时到位（0ms）。既有"首次收敛后补齐缺失坐标并上报"逻辑 MUST 保持不变；运镜 MUST NOT 在用户已手动操作相机后再次触发。

#### Scenario: 首屏进入感
- **WHEN** 首次打开星图页
- **THEN** 相机从远景平滑推进到全图取景，时长约 2 秒

#### Scenario: 切班不加长
- **WHEN** 教师切换班级
- **THEN** 取景动画为 900ms，与改造前一致

#### Scenario: 减弱动效
- **WHEN** 系统开启减少动效
- **THEN** 首屏直接显示全图，无推进动画

#### Scenario: 只播一次
- **WHEN** 用户点星、搜索飞行、筛选后引擎再次停止
- **THEN** 镜头不再被 `zoomToFit` 拉回全景（沿用 `fittedRef` 守卫）

### Requirement: 既有交互无回归
本变更 MUST NOT 改变搜索/飞行定位、选中高亮闭包、筛选幽灵、常驻标签、布局持久化的任何可观察行为。新增视觉层 MUST NOT 拦截画布事件。

#### Scenario: 交互回归
- **WHEN** 依次执行：搜索一个知识点 → 飞行定位 → 关闭下钻 → 隐藏一册 → 拖动错误率滑条 → 切班
- **THEN** 每一步结果与改造前一致；星云核/曲率连线不拦截点击与拖拽
