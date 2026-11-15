# Design: star-map-interactions

## 1. 不重建 sprite 的视觉状态通道

高亮 / 筛选会频繁变化。星体 sprite 若随状态重建（nodeThreeObject 依赖状态），161 个对象连重建会闪一帧（star-map-teacher 已踩过）。做法：

- 视觉状态放在一个 ref（`visRef = { highlight: Set|null, ghost: Set, selected }`），React state 变化时同步写入 ref；
- 每颗星的 `onBeforeRender` 每帧读 ref 计算透明度系数：幽灵 0.08 / 高亮激活但不在链上 0.15 / 其余 1；
- 连线走 `linkColor` / `linkDirectionalParticles` 访问器（函数引用随状态更新，库只更新材质，不重建几何）。

## 2. 先修闭包（#2）

纯函数 `prereqClosure(edges, kpId)`：沿 active 边反向 BFS 取全部祖先，加上直接后继；返回 `{ nodes, edges }`（边键 `from->to`）。高亮边只包含「祖先链内部的边 + 选中星到直接后继的边」。闭包有环防御（visited 集）。

## 3. 搜索飞行（#1）

`matchStars(nodes, query)`：名称子串 / 编码前缀匹配，名称前缀命中排前，最多 8 条。飞行按 3d-force-graph `click-to-focus` 示例：`cameraPosition(node * (1 + d/|node|), node, 1200)`，d = 180（大于 minDistance 120）。命中后与点星走同一个 `selectStar(kpId)`（含自动暂停巡航）。

## 4. 筛选（#3）

`isGhost(node, filter)`：所属册被关闭、或 `class_error_rate` 低于滑条下限（无样本星在下限 > 0 时视为不满足）→ 幽灵。任一端点为幽灵的连线淡到 alpha 0.04。筛选不触发重新布局。

## 5. 标签（#4）

官方 html-nodes 示例写法：`extraRenderers: [new CSS2DRenderer()]`，每颗星的三维对象改为 Group（星 sprite + CSS2DObject 标签）。CSS2DRenderer 对可见对象调用 `onBeforeRender`，在其中按规则设标签透明度：
- 显示：暗星、选中星、高亮链上的星、或相机距离 < 260 的星；幽灵星不显示；
- 远处淡出（距离越大越淡），选中星始终满不透明；
- 标签元素 `pointer-events: none`，不拦截画布的拖拽 / 悬停 / 点击。
HTML 文字不进 WebGL 后处理，bloom 不会把字糊掉。

## 6. 布局持久化（#5）

- 表 `kp_layout(kp_id PK, x, y, z, updated_at)`；`GET /api/knowledge-map/layout` 返回 `{kp_id: [x, y, z]}`；`POST` 只插入缺失的 kp_id、**从不覆盖**（多位教师并发首访时幂等安全）。
- 前端加载坐标后再挂星图：有坐标的星设 `fx/fy/fz` 固定；无坐标的星由册锚点力自然落位。
- 首次引擎停止时，把缺坐标星的位置 POST 上去；**不回写本地状态**（回写会改 graphData 引用 → 重启引擎、重新取景）。下次加载即全部固定。
- 读接口与星图同权限（question:read）；写接口 question:update（教师具备）。

## 7. 减弱动效（#6）

`matchMedia('(prefers-reduced-motion: reduce)').matches` 决定巡航初始值；手动开关不受限。

## 8. 不做

重新排布按钮（需管理员权限设计）、图内编辑先修边、框选多星、时间轴（见差距报告 P2/P3）。
