# Proposal: star-map-student

## Why

"记忆星图"的产品命名目前只在教师端兑现。学生端（`frontend/src/pages/mobile/student/KnowledgeMapPage.tsx`）仍是**二维气泡分组图**（`KpBubbleMap`，第 37 行起）加卡式列表：能看状态，但没有"自己的星空"。

数据其实已经具备：

- 每个知识点有个人掌握度（BKT，`mastery_service.bkt_sequence`）与三态（`kms.resolve_states`）；
- FSRS 记忆卡（`MemoryCard`，`app/models/database.py:1092` 起）存有 `stability/difficulty/state/last_review`；
- `fsrs.Scheduler.get_card_retrievability(card, now)` 已可用（py-fsrs 6.x），能算"此刻还能想起的概率"。

**核验发现的三个硬约束**（决定本变更的范围）：

1. **键空间不一致**：`questions.knowledge_points` 存的是**自由文本标签**（库内 125 种，0 个等于 `KnowledgePoint.kp_id`；仅 16 个与 KP `name` 完全同名）。运行时真源是 `kp_tag_map` 表（160 行，标签 → `kp_id` 编码），`kb_error_rate` 正是走这条链。教师星图的节点、先修边（264/277 端点）、`KPLayout` 全在 **kp_id 编码空间**。→ 学生星图必须先做 `标签 → kp_tag_map → kp_id` 映射，否则星星与布局/边对不上；现有 `mastery/states` 字段是标签空间，**不能直接拿来当星图键**。
2. **学生没有 `question:read`**（`app/middleware/auth.py:71-120`）。现有 `GET /api/knowledge-map/layout` 挂在 `question:read` 上，学生端不能直接复用；也不能为此放宽矩阵（布局不是"本人数据"，`allow_self` 不适用）。→ 布局必须随学生星图响应一起下发。
3. **DESIGN.md R-C9** 把"深底画布"限定为 `/kmap` 图谱视图的唯一例外，并写明"NEVER 扩散到其他页区；新增深底需求必须先修订本条"。→ 学生星图要落深空底，必须显式修订 R-C9，把范围精确限定到学生页星图卡片。

## What Changes

- **后端数据契约（additive）**：`GET /api/knowledge-map/student/{student_id}` 增加 `stars[]`（kp_id/name/module/mastery/answers/retrievability/state/faded）与 `layout`；现有 `states/mastery/layers/edges/bindings/faded/cold_start` 一个不动（它们仍是标签空间，服务现有气泡图/列表）
- **标签映射聚合**：新增 `app/services/student_kp_stats.py`——沿 `questions → kp_tag_map → kp_id` 聚合该学生的作答次数、BKT 掌握度、FSRS 可提取性；复用 `kb_error_rate` 的"按 content 去重 + 首标签映射"口径
- **星图渲染组件共享化**：`components/teacher/StarMap.tsx` → `components/star/StarMap.tsx`，编码改为**可选访问器**（`kindOf/brightnessOf/sizeOf/tooltipHtml/twinklePeriodOf`），教师端不传即维持现状；新增 `quality`（`full|eco`）、`height`、`showErrorFilter`、`legendText` 展示参数
- **学生星图视图**：知识地图页新增「星图 / 地图」切换（默认星图），复用同一渲染组件；点星打开既有详情面板（状态/掌握度/作答次数/可提取性/前置/练习此点）
- **编码**：亮度 = 个人掌握度、大小 = 作答次数、灰星 = 未作答、**闪烁周期 = FSRS 可提取性**（高可提取性慢、低可提取性急促）；教师端永不闪
- **移动端 eco 模式**：学生页默认 `quality="eco"`——无 bloom、星尘减半、像素比 ≤1.5、天空球降分段
- **DESIGN.md R-C9 修订**：深底例外精确扩展到学生知识地图页星图卡片（仍禁止进全局 theme）

## Capabilities

### New Capabilities
- `star-map-student`: 学生端记忆星图——标签到词典码的聚合、个人学情编码、FSRS 可提取性闪烁、共享渲染组件、移动端降级、访问边界

### Modified Capabilities
- 无（`star-map-teacher` 的教师端可观察行为必须零变化；本变更只做参数化抽取）

## Impact

- **后端新增**: `app/services/student_kp_stats.py`（作答次数/BKT 掌握度，按 kp_id 聚合）
- **后端修改**: `app/api/knowledge_map.py`（学生端点 +stars/+layout）、`app/services/kb_error_rate.py`（抽出 `canonical_kp_by_content` 供复用，行为不变）、`app/services/memory_card_service.py`（+`retrievability_by_kp`，FSRS 知识留在该服务）
- **前端新增**: `frontend/src/pages/mobile/student/StudentStarView.tsx`（星图视图与数据适配）
- **前端移动**: `frontend/src/components/teacher/StarMap.tsx` → `frontend/src/components/star/StarMap.tsx`（导入方同步）
- **前端修改**: `frontend/src/lib/starMap.ts`（学生编码/闪烁纯函数）、`KnowledgeMapPage.tsx`（视图切换 + 面板信息）、`KnowledgeMapAdminPage.tsx`（导入路径）
- **设计规范**: `DESIGN.md` R-C9 修订
- **测试**: pytest（契约/键空间/聚合/权限）、vitest（学生编码/闪烁映射）、验证台（移动端截图 + 教师端回归）
- **非目标**: 教师端视觉改动（→ `star-map-atmosphere` / `star-map-constellation`）；现有学生气泡图/列表的键空间迁移（保留标签空间，不在本变更修数据）；学生桌面端；家长端
- **风险**: 移动端 WebGL 性能（eco 必须实测）；教师端抽取回归（以既有交互清单兜底）；R-C9 扩散（限定到学生页星图卡片）
