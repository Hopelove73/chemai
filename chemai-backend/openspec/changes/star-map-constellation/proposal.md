# Proposal: star-map-constellation

## Why

`star-map-atmosphere` 解决"背景没有纵深"；但空间感还有一半来自**结构**。核验现状（`frontend/src/components/teacher/StarMap.tsx`）：

| 缺口 | 现状 | 位置 |
|------|------|------|
| 全景下看不见关系 | 所有先修边是直线，默认 alpha 0.3、宽 0.65；161 节点全景下边几乎不可见，图读起来是"漂浮的点"而不是"星座" | `StarMap.tsx:329-334` |
| 星系团没有整体形态 | 6 册只靠力导向聚簇，无任何团级视觉容器；看不出"这里是一个星系" | `lib/starMap.ts:41-48` |
| 没有局部聚焦 | 只能点选（点击锁定高亮链）；鼠标扫过时不反馈邻域，找关系要反复点 | `StarMap.tsx:259-261` |
| 首帧没有进入感 | 数据一到直接 `zoomToFit(900)`，没有"进入宇宙"的第一印象 | `StarMap.tsx:502-519` |

上一轮 `star-map-interactions` 已交付搜索、选中高亮、筛选、标签、布局持久化；本变更只补**结构感与局部交互**，不改数据编码、不改后端。

## What Changes

- **曲率星座连线**：`linkCurvature = 0.3`，全景下先修链呈弧线星座形态；高亮/幽灵/粒子的既有优先级不变
- **星系团星云核**：每册锚点处一个 900 尺度的极淡辉光 sprite（册色、opacity 0.045、NormalBlending），六团结构一眼可读
- **hover 邻域聚焦**：鼠标悬停星时，一度邻域（自身 + 直接前置 + 直接后继）保持、其余淡出；选中高亮优先于 hover；触屏无 hover 不受影响
- **开场运镜**：组件首挂载时相机从远景 2200 缓动进入 `zoomToFit`，时长 2000ms；切换班级维持现状 900ms；reduced-motion 瞬时到位

## Capabilities

### New Capabilities
- `star-map-constellation`: 星图结构与局部交互层——曲率星座连线、星系团星云核、hover 一度邻域聚焦、开场运镜

### Modified Capabilities
- 无（搜索/筛选/高亮/标签/布局持久化行为不变；本变更不得回归这些交互）

## Impact

- **修改文件**: `frontend/src/components/teacher/StarMap.tsx`（曲率、halo、hover 状态、开场相机）
- **修改纯函数**: `frontend/src/lib/starMap.ts`（新增 `hoverNeighborhood`）
- **修改测试**: `frontend/tests/star-map-interactions.test.ts`（hover 邻域用例）
- **依赖**: 零新增（`linkCurvature` 为 react-force-graph-3d 原生 prop）
- **测试**: vitest 纯函数；验证台截图 + 相机时长/对象计数断言
- **非目标**: 怠速漂移与流星（P2-9 暂缓：动效干扰专注，且无用户价值验证）；学生端星图（→ `star-map-student`）；图内编辑先修边、框选多星
- **风险**: hover 若触发 sprite 重建会闪帧——依赖 `star-map-atmosphere` REQ-SMA-005 先落地；halo 是新的发光体，必须遵守 bloom 亮度上限
