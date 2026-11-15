# Design: star-map-constellation

## 1. 曲率连线

- `linkCurvature={0.3}`（react-force-graph-3d 原生 prop，曲率相对边长；1 ≈ 半圆）。`linkCurveRotation` 用默认 0。
- 既有优先级保持：幽灵端点 `alpha 0.04` > 高亮链 `0.85` / 非链 `0.05` > 默认 `0.30`；宽度与粒子不变。
- **不做长度分级透明度**：`linkColor` 访问器在数据装载时求值，此时节点坐标尚未收敛，按长度分级会把错误值永久写进材质（或需要额外刷新回路）。曲率已足够提供"星座"形态，不值得为装饰引入状态同步风险。

## 2. 星系团星云核

```ts
// 每个出现在数据里的册，一个 sprite
const haloTexture = radialTexture(256) // 中心 alpha 0.5 → 边缘 0
const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
  map: haloTexture, color: MODULE_STAR_COLORS[m], opacity: 0.045,
  transparent: true, depthWrite: false, blending: THREE.NormalBlending,
}))
sprite.position.copy(moduleAnchor(m))
sprite.scale.set(900, 900, 1)
sprite.renderOrder = -2
```

- 位置 = `MODULE_ANCHORS[m]`（与聚簇力同源，团心即核）。
- 900 是团直径量级（锚点间距约 600–900），不按团内节点数缩放——核是空间容器，不是数据编码。
- **NormalBlending + opacity 0.045**：叠在星尘/天空球上不会累积成白斑（Additive 是历史爆白真凶）。
- 册被筛选隐藏时 halo **保持可见**：与幽灵星同一哲学——保留空间参照。不跟随筛选变化，避免额外状态耦合。
- 全屏切换/切班不重建：册集合只随 `nodes` 的册集合变化，用 `scene.userData` 幂等守卫。

## 3. hover 一度邻域聚焦

纯函数（`lib/starMap.ts`）：

```ts
export function hoverNeighborhood(edges: StarEdgeLite[], kpId: string): Set<string> {
  const set = new Set<string>([kpId])
  for (const e of edges) {
    if (e.status !== 'active') continue
    if (e.from_kp === kpId) set.add(e.to_kp)
    if (e.to_kp === kpId) set.add(e.from_kp)
  }
  return set
}
```

状态与优先级：

```
ghost（筛选幽灵） > selected（点击锁定高亮链） > hover（一度邻域） > 默认
```

- `visRef` 增加 `hover: Set<string> | null`，每帧在 `onBeforeRender` 读取；sprite 不重建。
- hover 且**未选中**时：邻域成员 opacity 系数 1；其余 0.15；端点都在邻域内的边 alpha 0.6，其余 0.05。
- 已选中时：hover 不改变高亮（避免"鼠标扫过就把选中链打散"）。
- 幽灵星即使被 hover 也保持 0.08（筛选意图优先）。
- `onNodeHover` 只更新一个 React state（供 `linkColor` 访问器更新）与 `visRef`；指针离开置 null 复原。
- 触屏没有 hover 事件，行为不变（点按 = 选中）。

## 4. 开场运镜

- 组件首挂载：`cameraPosition({x: 0, y: 0, z: 2200}, {x: 0, y: 0, z: 0}, 0)`（远景起点）。
- 首次 `onEngineStop`：`zoomToFit(2000, 60)`；同时保留既有"补齐缺失坐标上报"逻辑。
- 切班（`graphData` 变化）：`fittedRef` 重置，按现状 `zoomToFit(900, 60)`——不重复 2 秒长镜头。
- `prefers-reduced-motion: reduce`：`zoomToFit(0, 60)` 瞬时到位。
- 相机 `minDistance 120 / maxDistance 2600` 不变；2200 在允许范围内。

## 5. 与既有交互的边界

| 交互 | 本变更影响 |
|------|-----------|
| 搜索 + 飞行定位 | 不变；飞行中 hover 不触发（指针不在星上） |
| 选中高亮（祖先链 + 直接后继） | 优先级最高；hover 不改写 |
| 筛选幽灵 | 优先级最高；hover 不改写 |
| 标签 | hover 邻域成员标签显示（与高亮链规则一致），其余维持既有规则 |
| 布局持久化 | 不变；开场运镜在首次收敛后执行，坐标上报逻辑不动 |

## 6. 不做

- 长度分级连线透明度（见 §1）
- 流星 / 怠速漂移（P2-9）
- hover 触发的"预览面板"（会与下钻面板抢焦点，另评估）
