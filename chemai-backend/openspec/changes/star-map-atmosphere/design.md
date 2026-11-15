# Design: star-map-atmosphere

## 1. 场景结构（新增层级）

```
scene
├─ skySphere        Mesh(SphereGeometry 5200, BackSide)，renderOrder -100，depthWrite false
├─ dustFar          Points 1400，radius 3000–4400，size 5，opacity 0.22
├─ dustMid          Points 520， radius 1600–2600，size 8.5，opacity 0.32
├─ dustNear         Points 72，  radius 500–1100， size 14，opacity 0.40
├─ node objects     Group(Sprite + CSS2D label) × ~160（既有）
└─ selection ring   选中白环（既有）
```

三层星尘 + 天空球 = **4 个新 draw call**，在 161 节点规模下可忽略。星尘与天空球都是纯装饰：不参与 force 引擎、不参与 raycast、不承载数据语义。

## 2. 星尘生成（纯函数，可测）

新模块 `frontend/src/lib/starSky.ts`：

```ts
export interface DustLayerSpec {
  count: number; rMin: number; rMax: number; size: number; opacity: number
}
export const DUST_LAYERS: readonly DustLayerSpec[] = [
  { count: 1400, rMin: 3000, rMax: 4400, size: 5,   opacity: 0.36 },
  { count: 520,  rMin: 1600, rMax: 2600, size: 8.5, opacity: 0.50 },
  { count: 72,   rMin: 500,  rMax: 1100, size: 14,  opacity: 0.62 },
]
export function makeDustLayer(spec: DustLayerSpec, seed: number): {
  positions: Float32Array; phases: Float32Array; colors: Float32Array; speeds: Float32Array
}
```

- 球壳均匀采样：`phi = acos(2u-1)`, `theta = 2πv`, `r = rMin + (rMax-rMin) * cbrt(w)`（体积均匀）。
- 随机源用 mulberry32（纯函数、无全局 `Math.random`），测试可断言"同 seed 逐值一致 / 异 seed 不同"。
- 顶点色：70% 冷白（`#dfe8ff`）、20% 淡金（`#ffe9c4`）、10% 淡蓝（`#bcd6ff`）；近层最多 3 颗替换为册色（`MODULE_STAR_COLORS`）点缀。
- 每点 `phase` 均匀分布 `[0, 2π)`，`speed` 在 `[0.9, 1.6]`，供闪烁用。**不存颜色到材质**：材质 `vertexColors: true`。

## 3. 星尘材质与闪烁（GPU）

`THREE.ShaderMaterial`（不用 `PointsMaterial`：需要 per-point 相位与 alpha 呼吸）：

```glsl
// vertex
attribute float phase;
attribute float speed;
attribute vec3 color;
uniform float uTime;
uniform float uSize;
uniform float uScale;
varying vec3 vColor;
varying float vAlpha;
void main() {
  vColor = color;
  vAlpha = 0.70 + 0.30 * (0.5 + 0.5 * sin(uTime * speed + phase)); // 0.70–1.00
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = uSize * uScale / -mv.z;
  gl_Position = projectionMatrix * mv;
}
// fragment
varying vec3 vColor; varying float vAlpha;
void main() {
  float d = length(gl_PointCoord - vec2(0.5));
  if (d > 0.5) discard;
  float soft = smoothstep(0.5, 0.12, d);
  gl_FragColor = vec4(vColor, vAlpha * soft);
}
```

- `uTime` 由 `requestAnimationFrame` 累计（秒）。**reduced-motion 时 `uTime` 固定为 0**（静止星空，不闪）。
- 闪烁幅度限制在 0.70–1.00，不出现"熄灭"；周期 2–7s（由 speed 控制），避免频闪。
- 数据星 sprite：**不接 `uTime`**，`onBeforeRender` 只算距离与 `visRef` 交互因子（既有逻辑不动）。

## 4. 星云天空球（程序化）

```ts
new THREE.Mesh(
  new THREE.SphereGeometry(5200, 32, 16),
  new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    uniforms: { uTime: { value: 0 } },
    vertexShader, fragmentShader,
  }),
)
sky.renderOrder = -100
sky.raycast = () => {} // 纯背景，任何情况下不拦截交互
```

fragment 组成：

1. **微星**：方向向量对齐到网格（`vDir * 180`），每格 `hash3(cell)` 决定是否存在（阈值 ~0.06）与亮度；单点用 `smoothstep` 画 1–1.5px。峰值亮度 ≤ 0.6（小面积，bloom 泛光可控）。
2. **星云**：3 octave fbm（`value noise`，非纹理采样），取三册色的极暗变体（teal `#0a2a28`、紫 `#141026`、品红 `#2a0f1e`），`col *= smoothstep(0.52, 0.85, fbm) * 0.05`。**峰值 ≤ 0.05 = 低于 bloom threshold（0.05），不泛光**。
3. **时基**：`fbm(vDir * 3.0 + uTime * 0.003)` 极缓流动；reduced-motion 时 `uTime = 0`。

相机远平面：`fg.camera().far = 12000; updateProjectionMatrix()`（maxDistance 2600 + 球半径 5200 = 7800，需留余量）。**不依赖库默认 far**。

## 5. 暗星四芒星芒

`flareTexture()`：128×128 canvas，白核（同现有 `glowTexture` 的核）+ 两条正交、45° 微旋的高斯光带（十字四芒），光带峰值 alpha ≤ 0.55，其余透明。

- `nodeThreeObject` 内按 `kind` 选贴图：`dim → flareTexture`，`lit/gray → glowTexture`。
- 星体 `scale` 公式不变（不放大暗星，避免叠加 bloom）。
- 亮度/色相编码不变：星芒只是"形态"维度，不改变"越暗越薄弱"。

## 6. 生命周期修正（P0-5）

```ts
const nodeThreeObject = useMemo(() => (node) => buildStarObject(node, tex, flareTex), [tex, flareTex, nodes])
```

- `kind/brightness/size` 全部从传入的 node 读取（工厂调用时即当前数据）；选中/高亮/筛选状态**不参与** useMemo 依赖。
- 逐帧透明度仍走 `visRef`（`StarMap.tsx:298-300`），不触碰对象结构。
- **数据数组 `nodes` 必须在依赖里**：库在 `graphData` 变化但 `nodeThreeObject` 身份不变时按 node id 复用旧对象，不会重建；换班后星星会停留在上一班的数据上（本次实现时实测发现的坑）。
- 切换班级时 `graphData` 变、`nodes` 也变 → 函数身份变 → 库清空对象映射并重建。这是允许的重建。

## 7. 性能预算

| 项 | 预算 |
|----|------|
| 装饰点总数 | 1992（≤ 2000） |
| 新增 draw call | ≤ 4（3 层 Points + 1 天空球） |
| 每帧 CPU 分配 | 0（闪烁全在 shader；不 new 数组/对象） |
| 空闲巡航 FPS | ≥ 改造前基线的 90%（验证台 10s 采样） |

## 8. 方案取舍

- **不用 ESO 银河全景贴图**：6000×3000 照片需显著署名 "ESO/S. Brunier"（[使用条款](https://www.eso.org/public/images/eso0932a/)），且固定色相与册色体系冲突、增加数 MB 资源。程序化 shader 零版权负担、色相可控、可 reduced-motion 冻结。
- **不用 sprite 铺星尘**：1992 个 sprite = 1992 draw call，必须 `Points` + shader。
- **不给数据星加闪烁**：亮度编码掌握度，闪烁会污染编码；且 bloom × 透明度联动是历次爆白雷区。
- **不引入新依赖**：three 已具备全部能力（ShaderMaterial/BufferGeometry/CanvasTexture）。

## 9. 不做

- 曲线连线、hover 聚焦、星云核、开场运镜（`star-map-constellation`）
- 流星/怠速漂移（P2-9 暂缓）
- 学生端（`star-map-student`）
