# Tasks: star-map-student

## 1. 后端：映射与聚合
- [ ] 1.1 `kb_error_rate.py`：抽出 `canonical_kp_by_content(conn)`（按 content 去重 + 首标签映射），`error_rates_for_kps` 改用它，行为零变化
- [ ] 1.2 `app/services/student_kp_stats.py`：`student_kp_stats(db, student_id)` → `{kp_id: {answers, mastery}}`（BKT 复用 `bkt_sequence`，MIN_SAMPLES=3）
- [ ] 1.3 `memory_card_service.py`：`retrievability_by_kp(db, student_id, kp_by_content)`（JOIN Question、跳过 last_review=None/stability<=0、按码均值）
- [ ] 1.4 同模块：`practice_tags_by_kp(db)`（`kp_id → tag_text[]` 反向映射，字典序，不筛 status）
- [ ] 1.5 `state`/`faded`：码空间经 `resolve_states` 与 due_cards 计算
- [ ] 1.6 pytest：标签映射、未映射标签、多标签首映射、样本<3 → null、均值、新卡 null、stability=0、逾期更低、反向映射覆盖 69/161、越权 403、既有字段不变

## 2. 后端：端点
- [ ] 2.1 `GET /student/{id}` 增加 `stars[]`（覆盖 `_graph_base_nodes` 全部码，含 `practice_tags`）与 `layout`（读 `KPLayout` 全表）
- [ ] 2.2 现有 `states/mastery/layers/edges/bindings/faded/cold_start` 不动
- [ ] 2.3 权限门不变；不新增端点；不放宽 `/layout` 的 `question:read`

## 3. 前端纯函数
- [ ] 3.1 `lib/starMap.ts`：`studentStarKind(answers, mastery)`、`studentStarBrightness(mastery)`、`twinklePeriod(r, answers)`、`starPhase(kpId)`
- [ ] 3.2 vitest：未作答灰星、mastery null/阈值 0.7、r→周期映射（0→1.5s / 1→8s）、null 不闪、相位稳定
- [ ] 3.3 `KnowledgeMapPage` 的 `MapData` 类型补 `stars`/`layout`（含 `practice_tags`）

## 4. 组件共享化（教师端零回归）
- [ ] 4.1 移动 `components/teacher/StarMap.tsx` → `components/star/StarMap.tsx`，更新两处导入
- [ ] 4.2 新增可选访问器：`kindOf/brightnessOf/sizeOf/tooltipHtml/twinklePeriodOf`，默认值 = 现有教师逻辑
- [ ] 4.3 新增展示参数：`showErrorFilter`、`legendText`、`height`、`quality`
- [ ] 4.4 闪烁回路：仅当 `twinklePeriodOf` 存在时在 `onBeforeRender` 计算（`performance.now()` + `starPhase`）
- [ ] 4.5 教师端回归：搜索/飞行/高亮/筛选/标签/布局上报逐项手工核对

## 5. 学生页
- [ ] 5.1 `pages/mobile/student/StudentStarView.tsx`：数据适配（stars → StarNode）、学生访问器、eco、height≈520、legendText
- [ ] 5.2 `KnowledgeMapPage.tsx`：视图切换（默认星图）、星图卡片、详情面板补「作答次数 / 可提取性」
- [ ] 5.3 码 → 展示名：点星后用 `stars[].name` 进现有面板；「练习此点」传 `practice_tags[0]` 作 `smartKp`；`practice_tags` 为空时隐藏按钮并提示
- [ ] 5.4 学生页不传 `onLayoutSettled`、不调用 `POST /layout`
- [ ] 5.5 冷启动提示条保留；锁定星详情的练习按钮逻辑不变

## 6. 设计规范
- [ ] 6.1 `DESIGN.md` R-C9 修订：学生页星图卡片纳入深底例外，明确范围与禁止项

## 7. 验收
- [ ] 7.1 验证台：移动端星图截图（默认视图/详情面板/全屏/减弱动效）
- [ ] 7.2 教师端对照截图：改造前后一致（除既有 atmosphere/constellation 变更）
- [ ] 7.3 eco 断言：无 BloomPass、星尘 700/260/36、像素比 ≤1.5
- [ ] 7.4 练习闭环：有标签星 → 定向练习题目池非空；无标签星 → 按钮隐藏 + 提示
- [ ] 7.5 性能：中端安卓/主流移动浏览器巡航 10 秒无卡死、无 WebGL 上下文丢失
- [ ] 7.6 `pytest` 全绿、`npm run check`（typecheck + vitest + build）全绿

## 8. 前置依赖
- [ ] 8.1 `star-map-atmosphere` 的三层星尘/天空球/eco 参数必须已落地（学生页复用同一渲染组件）
- [ ] 8.2 `star-map-constellation` 的 hover 聚焦为可选项：移动端无 hover，不阻塞本变更
