# Design: star-map-teacher

## 1. 引擎选型（为什么是 react-force-graph-3d）

| 候选 | 结论 |
|---|---|
| react-force-graph-3d（ThreeJS） | **选它**。bloom 辉光（UnrealBloomPass）+ 发光 sprite 贴图是「星空感」的唯一现成路径；react 绑定成熟；161 节点/274 边对 WebGL 是零压力（万级才是门槛） |
| ECharts GL graphGL | 已在栈内，但自定义 shader/辉光/星型节点能力弱，做出来还是「力导向图」不是「星图」 |
| AntV G6 | 2D 企业图引擎，动画强但无深空质感；中文选型文章确认「星球亮度+辉光」需自研渲染，G6 无优势 |

依赖体积通过**路由级 code-split** 消化：`KnowledgeMapAdminPage` 已按页加载，three/react-force-graph 只进该页 chunk。

## 2. 深底独立接缝（R-C9 草案）

教师端 R-C7/C8「浅色工作台、无深色区域」是红线；星图是第一个真正需要深底的信息面板。处理方式对齐既有先例（登录页品牌渐变 = 全端唯一渐变例外）：

- 星图**画布区域**（含图例与下钻面板的星空侧）整块深底，页内其余 chrome（顶栏/边管理抽屉/list 视图）维持浅色；
- 深底令牌独立命名（`--star-canvas-*`：深空底 `#0b1220` 系、辉光青 `#2dd4bf`、暗星灰、星座线低透明白），定义在星图组件作用域内，**不进** globals 的 data-theme，不劫持全局令牌（R-K3 精神）；
- DESIGN.md 增补 R-C9：深底画布仅允许出现在显式声明的「独立接缝」页区，当前唯一实例 = /kmap 星图。

## 3. 数据与口径

### 3.1 班级聚合（复用而非新造）

`kb_error_rate.error_rates_for_kps`（作答事件级口径，2026-10-27 与老师对齐：错误率按作答事件算、题按 content 去重、一题多标签取首个映射）增加**可选 `class_id`**：只在作答查询上加 `sa.student_id IN (SELECT student_id FROM students WHERE class_id = :cid)`。口径、去重、MIN_ANSWERS=5 全部不变——教师端星图与学生端将来用同一把尺。

### 3.2 端点

- `GET /api/knowledge-map/class/{class_id}/graph`：节点 = 全量知识点（骨架复用 `teacher_graph` 的 nodes 组装，另带 `class_error_rate` / `class_answers`），边 = 全量（含 status，前端只画 active）；`ScopeGuard("class_id")` + `PermissionChecker("question", "read")`，与 panel.py 班级读侧同权限姿势
- `GET /api/knowledge-map/class/{class_id}/kp/{kp_id}/weak-students`：下钻懒加载——该知识点错误率最高的学生 top N（每人：姓名 + 错误次数 + 最近 1 条错误作答样本）；不做进 161 节点全量响应（避免 341 人 × 161 点大响应）

### 3.3 无样本与静默降级

- `class_answers < MIN_ANSWERS` 的知识点**整条压制**：不带 class_error_rate 且 class_answers 记 0（视同无可信样本，阈值口径与全局一致）；
- 聚合异常静默降级为空（与 teacher_graph 现行为一致），端点不 500——错误率是增强不是门槛。

## 4. 星编码与布局

- 亮度：`class_error_rate` 反转线性映射（错误率 0 → 最亮，0.6+ → 暗），带下限保证可见；无样本 = 固定灰星，**不参与**暗星观感判定；
- 大小：`class_answers` sqrt 归一到 [0.6, 1.8] 倍基准半径；
- 布局：6 册/专题 = 6 个星系团锚点（3D 空间均匀分布），团内力导向；d3-force-3d 的 forceX/Y/Z 按册锚定；active 先修边画低透明度连线（disabled 边不画）；
- 交互：OrbitControls 拖拽旋转/缩放 + 缓速自转（可关）；点击星 → 下钻面板；先修上下文（直接前驱的班级掌握状态）由前端用 nodes 本地计算，不发请求。

## 4.1 渲染质感（2026-10-31 实测迭代定案）

经五轮浏览器实测迭代收敛，记录参数取舍以免后人回退到已证伪的组合：

- **混合模式 = NormalBlending（不是 Additive）**：Additive 下密集团（必修一 44 星）在任一投影密集角度重叠无上限累积 → 爆白巨星；Normal 下重叠只遮挡、不累积，物理上杜绝。辉光分层画进贴图（白核 0–0.32 → 色环 0.5 → 柔晕 0.72 → 透明）。
- **辉光链路终版（2026-11-01，八轮排查定案）**：RenderPass → UnrealBloomPass（threshold 0.05 / strength 1.5 / radius 0.6）→ **OutputPass**，renderer 开 ACESFilmicToneMapping（exposure 1.2）；背景不透明 #000103；星体 base 16 / linkOpacity 0.25 / 贴图核缘 0.22。两个根因必须了解：
  1. **bloom 被重复叠加**（历次「越点越爆」的真凶）：内联 ref 回调每次渲染都是新函数 → React 重放 ref(null)+ref(inst) → bindFg 每次都往 postProcessingComposer 再 addPass 一个 bloom。实测点星/暂停/切班后 bloom 数量 3→4→5 递增，N 个串联 = 指数泛光。修法 = bindFg 幂等守卫（scene.userData.bloomed）+ **ref 必须用 useCallback 稳定引用**（NEVER 内联箭头 ref 传给 ForceGraph3D）。
  2. **缺 OutputPass**：three-render-objects 的 composer 只建 RenderPass；UnrealBloom 输出 HDR 线性亮度（>1），无 OutputPass（色调映射 + sRGB 转换）时直接截断成纯白。补 OutputPass + ACES 后 bloom 的数学回到可控区间。
- **调参纪律**：渲染参数改动一律先过验证台（Playwright 无头 Chromium + route 拦截 chunk 实时替换参数，默认/拉近/点星三场景截图），选定后再落代码。盲调参数不可接受——本轮 s1-s4 四变体对比一次选定，替代此前七轮盲调。
- **选中高亮 = 独立白环 sprite（rAF 跟随星位），星本体不动**：曾改为「选中星白化+1.6 倍+不透明」——白核直接冲爆 bloom 当场爆闪；且 nodeThreeObject 依赖选中态会连重建 161 个 sprite 闪一帧。外科式白环两者皆无。
- **白环自身必须压在 bloom 阈值下（历次「点星爆白」的真凶）**：白环初版 scale 46（星体 2 倍大）+ 纯白 opacity 0.85 + depthTest false 常驻最上层——大面积高亮度远超 bloom threshold，bloom 把环糊成巨型白球，且每次点击都复现（故用户感知为「反复爆白」）。定案：scale = 星体实际尺寸 × 1.7、color #8896aa × opacity 0.5（有效亮度 0.27 < threshold 0.32，物理上不触发泛光）。
- **排查纪律**：渲染回归先扫 three 场景实物（sprite scale/opacity/blending 清单）定位光球归属，再动参数——本轮曾把白环误判为「星贴脸」连续调错三轮。
- **相机限位 OrbitControls minDistance 120 / maxDistance 2600**：相机穿进星团 → sprite 糊满全屏 + bloom = 全屏白，禁止怼穿。
- **近距亮度衰减（能量模型）**：bloom 泛光能量 = 屏幕面积 × 亮度���面积随距离增大不可控，用 opacity 按 dist^0.4 衰减（<520 起）补偿——近景星自动降亮度，远景不受影响。曾只���尺寸钳制（下限 22%）+ 怼脸淡出，近景大面积 sprite 仍喂爆 bloom（点星爆白残留），能量模型才是对症解。
- **星尺寸上限收敛**：starSize cap 1.8→1.3、饱和点 200→350 作答——371 作答的星曾顶格 1.8 倍 + 满亮度，天然巨型光球。
- **色相 = 册（6 色）**，亮度乘数底 0.55（暗星保留五成半体色，否则非青册星被压黑）；**斥力 charge -260**（默认值下密集团叠成白斑）。
- **选中高亮**：被下钻的星放大 1.6 倍、颜色向白插值 45%、满不透明——点击有视觉反馈。

## 5. 下钻 → 出题闭环

星图零写操作。下钻面板「出变式题」= 路由跳转 `出题工作台?kp=M1-T2-KP03`，工作台读 query 预选知识点，后续生成/审核/派发全部走既有流程与确认门。星图不复制任何出题逻辑。

## 6. 不做（防蔓延）

跨班聚合视图、框选多星批量出题、图内嵌 Agent 对话、模拟时间衰减、移动端适配（教师星图为桌面场景，/m/ 不挂）。
