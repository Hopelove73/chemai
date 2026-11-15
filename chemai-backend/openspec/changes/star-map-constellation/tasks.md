# Tasks: star-map-constellation

## 1. 纯函数与测试
- [ ] 1.1 `frontend/src/lib/starMap.ts`：`hoverNeighborhood(edges, kpId)`（active 边、一进一出、环安全、孤立星）
- [ ] 1.2 `frontend/tests/star-map-interactions.test.ts`：hover 用例（入边/出边/disabled/孤立）
- [ ] 1.3 vitest 全绿

## 2. 曲率连线
- [ ] 2.1 `ForceGraph3D` 增加 `linkCurvature={0.3}`
- [ ] 2.2 回归：高亮链 alpha / 幽灵边 / 粒子方向与速度不变

## 3. 星系团星云核
- [ ] 3.1 `radialTexture(256)` 工具（若 `glowTexture` 可复用则复用，不新增重复实现）
- [ ] 3.2 `bindFg` 内按当前册集合挂载 halo（锚点、册色、900、opacity 0.045、NormalBlending、renderOrder -2）
- [ ] 3.3 `scene.userData` 幂等守卫；册集合变化时增删
- [ ] 3.4 回归：隐藏册不改变 halo；多次 bindFg 不叠加

## 4. Hover 邻域聚焦
- [ ] 4.1 `visRef` 增加 `hover` 字段；`onNodeHover` 写入 state + ref
- [ ] 4.2 `onBeforeRender` 透明度优先级：ghost > selected > hover > 默认
- [ ] 4.3 `linkColor` 访问器：hover 且未选中时按邻域收敛 alpha
- [ ] 4.4 标签规则：hover 邻域成员可见（与高亮链同规则）
- [ ] 4.5 回归：选中时 hover 不改写；离开复原；触屏无残留

## 5. 开场运镜
- [ ] 5.1 首挂载设置相机 `(0,0,2200)` 起点
- [ ] 5.2 首次 `onEngineStop`：首屏 `zoomToFit(2000, 60)`；切班 `zoomToFit(900, 60)`
- [ ] 5.3 reduced-motion：`zoomToFit(0, 60)`
- [ ] 5.4 保留缺失坐标上报与 `fittedRef` 守卫

## 6. 验收
- [ ] 6.1 验证台截图：全景曲率连线、六团星云核、hover 邻域、首屏运镜帧序列
- [ ] 6.2 对象计数：halo 数量 = 数据中的册数；重复 bindFg 不增长
- [ ] 6.3 相机断言：首屏约 2000ms、切班约 900ms、reduced-motion 0ms
- [ ] 6.4 既有交互回归清单（搜索/选中/筛选/标签/布局）逐项通过
- [ ] 6.5 `npm run typecheck`、`npm run build`、vitest 全绿

## 7. 前置依赖
- [ ] 7.1 `star-map-atmosphere` 的 REQ-SMA-005（sprite 不重建）必须先完成，否则 hover 会闪帧
