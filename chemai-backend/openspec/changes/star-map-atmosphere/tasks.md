# Tasks: star-map-atmosphere

## 1. 纯函数与测试
- [x] 1.1 `frontend/src/lib/starSky.ts`：`DUST_LAYERS` 常量 + `makeDustLayer(spec, seed)`（球壳体积均匀采样、顶点色、phase/speed）
- [x] 1.2 闪烁参数：以 `twinkleAlpha(t, speed, phase)` 暴露（未单拆 `twinkleParams`，单层生成已含相位/速度）
- [x] 1.3 `frontend/tests/star-sky.test.ts`：层参数、半径带互不重叠、同 seed 确定性、异 seed 差异、颜色分量范围
- [x] 1.4 `npx vitest run` 全绿（540 passed，含新增 12）

## 2. 场景装饰（StarMap.tsx）
- [x] 2.1 移除现有单层 420 颗星尘块（`StarMap.tsx:151-179`），改挂三层 `Points`（ShaderMaterial + 顶点色 + `uTime`）
- [x] 2.2 天空球：`SphereGeometry(5200, 32, 16)` + 自写 shader（hash 微星 + 3 octave fbm + 星云暗变体），`BackSide`、`depthWrite:false`、`renderOrder:-100`、`raycast=()=>{}`
- [x] 2.3 相机：`camera.far = 12000` + `updateProjectionMatrix()`，加入既有 `bindFg` 幂等区
- [x] 2.4 `uTime` 更新回路：单一 rAF；`prefers-reduced-motion` 时冻结为 0
- [x] 2.5 场景对象幂等：`scene.userData.starSky` 守卫，多次 bindFg 不叠加

## 3. 星体形态与生命周期
- [x] 3.1 `flareTexture()`：白核 + 正交四芒光带（峰值 alpha 0.30、长度 64%，防 bloom 光刃）
- [x] 3.2 `nodeThreeObject` 按 `kind === 'dim'` 选 `flareTexture`，其余用 `glowTexture`；scale 公式不动
- [x] 3.3 `nodeThreeObject` useMemo 依赖收缩为 `[tex, flareTex, nodes]`：交互状态出依赖、数据数组保留（换班按 id 复用旧对象的坑）
- [ ] 3.4 对象计数断言：依赖已收缩、连续点选肉眼无闪帧；**场景对象计数的自动化断言未做**（需验证台注入计数）

## 4. 验收
- [x] 4.1 浏览器实测截图：全景、错误率≥30% 过滤、近景（dev server + 教师账号 qian）
- [ ] 4.2 bloom 回归：连续点击 20 颗星、切换班级 3 次后画面不泛白；pass 数不增长（未做完整 20 次/3 班回归）
- [ ] 4.3 reduced-motion：系统开启后星尘/星云静止、巡航默认关闭（未在浏览器中模拟）
- [ ] 4.4 FPS：改造前后各 10 秒空闲巡航采样，改造后 ≥ 基线 90%（未采样）
- [x] 4.5 `npm run typecheck`、`npm run build`、`npx vitest run` 全绿

## 5. 实测记录（2026-11-04）
- [x] 5.1 开发服务器（Vite 3001）+ 教师端 `/kmap` 实测：天空球与三层星尘渲染正常，无 shader 编译报错
- [x] 5.2 全景可见冷暖混合星尘与极淡星云；错误率≥30% 过滤下背景纵深更明显
- [x] 5.3 近景“贯穿屏幕的光刃”排查：真因为**选中星的高亮先修边**被 bloom 放大，不是星芒贴图；已把光带峰值从 0.5 收到 0.30、长度收到 64%
- [x] 5.4 暗星四芒在远景（星体 ~6px）偏弱，中近景可见；如需更强形态提示，后续可加独立 flare sprite（不在本变更）
- [x] 5.5 代码评审修正：`nodeThreeObject` 依赖只留贴图会让库在换班时按 node id 复用旧对象（星体停留在上一班数据）；依赖已改为 `[tex, flareTex, nodes]`，spec/design 同步修订

## 6. 不做（转交）
- [x] 6.1 曲线连线 / hover 聚焦 / 星云核 / 开场运镜 → `star-map-constellation`
- [x] 6.2 学生端星图 → `star-map-student`

## 7. 已知遗留
- [ ] 7.1 `npm test` 的 token-lint 门仍红：工作树中存在 43 处**存量**色值违规（StarDrilldownPanel、家长页等），与本变更无关；本变更新增的色值已用 `token-lint:disable-frame` 按 R-C9 豁免
- [ ] 7.2 远景暗星形态提示偏弱（见 5.4）
