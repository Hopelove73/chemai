# Spec Delta: star-map-atmosphere

## Purpose

为教师端班级知识星图提供"深空空间感"的氛围层：三层视差星尘、程序化星云天空球、装饰层闪烁、暗星四芒星芒，并把星体对象生命周期修正为"交互状态不重建 sprite"。装饰层只提供纵深与物质感，**不改变任何数据编码**（亮度=掌握度、大小=作答量、色相=册）。

## ADDED Requirements

### Requirement: 三层视差星尘
星图场景 SHALL 以三个独立 `THREE.Points` 对象渲染背景星尘，参数 MUST 为：远层 1400 颗、球壳半径 3000–4400、点径 5、透明度 0.36；中层 520 颗、半径 1600–2600、点径 8.5、透明度 0.50；近层 72 颗、半径 500–1100、点径 14、透明度 0.62。各层 MUST 使用 `depthWrite: false` 与 `renderOrder: -1`，颜色 MUST 以顶点色提供冷暖混合（冷白/淡金/淡蓝），近层 MAY 含不超过 3 颗册色点缀。星尘 MUST NOT 参与 raycast、MUST NOT 承载数据语义、MUST NOT 随选中/筛选/切班重建。生成逻辑 MUST 为纯函数并接受显式种子。

#### Scenario: 层参数与半径带
- **WHEN** 以任意种子调用 `makeDustLayer(spec, seed)` 三次（对应 `DUST_LAYERS` 三项）
- **THEN** 返回点数分别为 1400 / 520 / 72，每个点坐标模长都落在该层 `[rMin, rMax]` 内，且三层半径区间互不重叠

#### Scenario: 确定性
- **WHEN** 用同一种子调用两次 `makeDustLayer`
- **THEN** 两次返回的 positions/phases/colors/speeds 逐值相等；不同种子结果不同

#### Scenario: 视差纵深
- **WHEN** 相机绕原点环绕 90°
- **THEN** 远层在屏幕上的位移明显小于近层（三层半径不重叠带来的自然视差），无需任何额外视差代码

#### Scenario: 不参与交互
- **WHEN** 用户点击/拖拽/悬停画布
- **THEN** 星尘不拦截事件、不成为点击目标，节点选中行为与改造前一致

### Requirement: 程序化星云天空球
场景 SHALL 包含一个半径 5200 的 `BackSide` 球体，使用自写 `ShaderMaterial` 渲染：方向网格 hash 微星 + 3 octave fbm 星云 + 极缓时基流动（`uTime * 0.003`）。星云亮度峰值 MUST ≤ 0.05（低于 bloom threshold，不泛光），微星在默认相机距离下的屏幕投影尺寸 MUST ≤ 1.5px、峰值亮度 MUST ≤ 0.6。天空球 MUST 设置 `depthWrite: false`、`renderOrder ≤ -10`、禁用 raycast；相机 `far` MUST 显式设置为 ≥ 12000 并更新投影矩阵（不依赖库默认值）。星云色相 MUST 只取册色的暗变体，MUST NOT 引入第七种色相。

#### Scenario: 默认参数下不爆白
- **WHEN** 以现有 bloom 参数（threshold 0.05 / strength 1.5 / radius 0.6 / ACES 曝光 1.55）加载星图并巡航一周
- **THEN** 画面不出现全屏泛白或底色发灰；星云可见但明显暗于数据星

#### Scenario: 背景随相机变化
- **WHEN** 相机环绕或缩放
- **THEN** 天空球微星与星云保持方向稳定（无限远背景），不出现贴图拉伸或接缝

#### Scenario: 减弱动效
- **WHEN** 系统 `prefers-reduced-motion: reduce`
- **THEN** `uTime` 固定为 0，星云停止流动但保持渲染

### Requirement: 装饰层闪烁
只有装饰层（三层星尘与天空球微星）SHALL 参与闪烁；数据星 sprite 的透明度 MUST NOT 出现周期性变化。星尘闪烁 MUST 在顶点着色器内完成（per-point `phase` + `speed` + `uTime`），alpha 系数 MUST 限制在 0.70–1.00，周期 MUST 在 2–7 秒之间。MUST NOT 在每帧 CPU 循环里逐点修改属性或创建临时对象。

#### Scenario: 数据星稳定
- **WHEN** 在教师星图上对同一颗数据星连续采样 10 秒透明度
- **THEN** 采样值不呈现周期波动（仅交互状态变化时改变）

#### Scenario: 装饰层呼吸
- **WHEN** 正常动效模式
- **THEN** 星尘可见缓慢明暗呼吸，无频闪；不同点相位不同步

#### Scenario: 减弱动效静止
- **WHEN** 系统 `prefers-reduced-motion: reduce`
- **THEN** 闪烁停止，装饰层保持固定亮度

### Requirement: 暗星四芒星芒
星体贴图 SHALL 提供第二种形态：四芒星芒（白核 + 两条正交、45° 微旋的高斯光带）。只有 `starKind(...) === 'dim'` 的星 MUST 使用星芒贴图；`lit` 与 `gray` MUST 继续使用现有圆形贴图。星芒光带峰值 alpha MUST ≤ 0.55，星体 `scale` 公式 MUST NOT 改变。星芒只增加形态维度，MUST NOT 改变亮度/大小/色相编码。

#### Scenario: 暗星可辨
- **WHEN** 星图处于 1:1 缩放（未放大）
- **THEN** 薄弱星（错误率 ≥ 30%）呈现可辨认的四芒形态，与亮星、灰星在形状上可区分

#### Scenario: 亮星与灰星不变
- **WHEN** 对比改造前后同一亮星/灰星的截图
- **THEN** 除背景氛围外，星体形状、大小、颜色不变

#### Scenario: 不触发爆白
- **WHEN** 视口内同时存在 20 颗以上暗星
- **THEN** 画面不出现叠加泛白；星芒不产生大面积 bloom

### Requirement: 星体对象生命周期
`nodeThreeObject` 的 React memo 依赖 MUST 只包含贴图资源（`tex`、`flareTex`）与数据数组（`nodes` 或等价的数据标识）。依赖 MUST NOT 包含选中、取消选中、高亮、筛选（幽灵）等交互状态；这些状态变化 MUST NOT 重建任何 sprite 或 CSS2D 标签对象，逐帧视觉差异 MUST 继续通过既有 `visRef` 通道在 `onBeforeRender` 内计算。切换班级导致数据数组变化时 MUST 重建（函数身份变化，促使库清空对象映射），且重建后 MUST 正确反映新数据。

#### Scenario: 连续选中不重建
- **WHEN** 依次点击 10 颗不同的星（含选中→取消→再选中）
- **THEN** 场景中 Sprite 对象数量与首次加载一致，画面无整图闪帧

#### Scenario: 筛选不重建
- **WHEN** 切换册显隐或拖动错误率下限滑条
- **THEN** 仅透明度变化，Sprite/标签对象数量不变

#### Scenario: 切班正确重建
- **WHEN** 教师切换班级
- **THEN** 所有星的亮度/大小按新班级数据更新（允许对象重建）

### Requirement: 性能预算
新增装饰层 MUST 满足：装饰点总数 ≤ 2000；新增 draw call ≤ 4；渲染循环内每帧新增 CPU 内存分配为 0；空闲巡航 10 秒平均 FPS MUST ≥ 改造前基线的 90%。

#### Scenario: 帧率不退化
- **WHEN** 验证台在 161 节点数据集上采集改造前后各 10 秒空闲巡航 FPS
- **THEN** 改造后平均值 ≥ 改造前平均值的 90%

#### Scenario: 对象预算
- **WHEN** 遍历场景统计 `Points` 与 `Mesh` 数量
- **THEN** 星尘恰好 3 个 `Points`、天空球恰好 1 个 `Mesh`，无重复挂载（多次 `bindFg` 不叠加）
