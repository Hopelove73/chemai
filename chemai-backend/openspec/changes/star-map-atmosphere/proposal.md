# Proposal: star-map-atmosphere

## Why

用户反馈教师端班级星图"没有星空空间感"（2026-11-04）。核验现状（`frontend/src/components/teacher/StarMap.tsx`，610 行）：

| 缺口 | 现状 | 位置 |
|------|------|------|
| 背景是死的 | 背景星尘只有**单层** 420 颗 `THREE.Points`，同尺寸（7）、同色（0x8fa8c8）、同透明度（0.35），静态 | `StarMap.tsx:151-179` |
| 背景无物质感 | 画布底只是 CSS 径向渐变 `#08101f → #020409 → #000103` | `StarMap.tsx:460-462` |
| 星无形态差异 | 所有星共用一张圆形径向渐变贴图，亮星暗星只差透明度 | `StarMap.tsx:39-57, 351-360` |
| 画面无生命感 | 除相机巡航（0.8）外全画面零动画 | `StarMap.tsx:246` |
| 交互重建 sprite | `nodeThreeObject` 的 useMemo 依赖 `[tex, highlight, ghostIds, selectedKp]`，与注释"状态切换不重建"相反；选中一次全图对象重建一次 | `StarMap.tsx:404` |

调研结论（决策依据：`docs/星图星空感开源调研与优化方案_20261104.md`）：底座选型正确——`react-force-graph-3d` + `three` + `UnrealBloomPass` 正是 Obsidian 3D 图谱插件与整个知识图谱 3D 生态的同一套底座；官方 31 个示例中没有 galaxy/starfield 示例，"星系感"必须由场景装饰层提供。Spacekit.js 的实践（真实星表点云 "more performant but less visually stunning" vs 天空球）支持"点云星尘 + 程序化星云天空球"并用。**零新增依赖**。

## What Changes

- **三层视差星尘**：单层 420 颗 → 远/中/近三层（1400 / 520 / 72 颗），不同半径带、点径、透明度与色温；相机转动产生真实纵深
- **程序化星云天空球**：半径 5200 的 `BackSide` 球 + 自写 shader（hash 微星 + 3 octave fbm 星云），星云取册色暗变体、亮度压 bloom 阈值下
- **装饰层闪烁**：per-point phase + `uTime`，在顶点着色器内做 alpha 呼吸；**数据星永不闪**（亮度编码掌握度，不能被动画污染）
- **暗星四芒星芒**：新增四芒贴图变体，仅 `kind === 'dim'` 使用；薄弱星获得"变星"形态维度
- **sprite 生命周期修正**：`nodeThreeObject` 依赖收缩到贴图，交互状态全走既有 `visRef` 通道——兑现 `star-map-interactions` REQ-SMI-002 已声明但未实现的"状态切换不重建 sprite"

## Capabilities

### New Capabilities
- `star-map-atmosphere`: 星图深空氛围层——三层视差星尘、程序化星云天空球、装饰层闪烁、暗星星芒、星体对象生命周期与性能预算

### Modified Capabilities
- 无（`star-map-teacher` 的亮度/大小/色相编码语义不变；本变更只加装饰层并修正实现缺陷）

## Impact

- **新增文件**: `frontend/src/lib/starSky.ts`（星尘层生成/闪烁参数等纯函数）, `frontend/tests/star-sky.test.ts`
- **修改文件**: `frontend/src/components/teacher/StarMap.tsx`（场景装饰、天空球、闪烁 shader、星芒贴图、useMemo 依赖）
- **依赖**: 零新增（three 0.187.0、react-force-graph-3d 1.29.2 已在依赖）
- **测试**: vitest 纯函数（层参数/半径带/确定性/闪烁映射）；验证台截图 + FPS 采样 + reduced-motion 断言
- **非目标**: 曲线连线、hover 邻域聚焦、星云核、开场运镜（→ `star-map-constellation`）；学生端记忆星图（→ `star-map-student`）；怠速漂移/流星（P2-9，暂不纳入：动效干扰专注且无用户价值验证）
- **风险**: bloom 爆白三前科（`StarMap.tsx:180-207, 429-434` 注释）。所有新发光体必须 NormalBlending、亮度上限、`depthWrite: false`、幂等守卫
