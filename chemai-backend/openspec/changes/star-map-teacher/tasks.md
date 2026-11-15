# Tasks: star-map-teacher

## 1. 数据与端点

- [x] 1.1 `kb_error_rate.error_rates_for_kps` 增加可选 `class_id`（仅作答查询加班级过滤，口径/去重/MIN_ANSWERS 不变）
- [x] 1.2 `GET /api/knowledge-map/class/{class_id}/graph`（ScopeGuard + PermissionChecker；nodes 带 class_error_rate/class_answers；edges 全量带 status；静默降级）
- [x] 1.3 `GET /api/knowledge-map/class/{class_id}/kp/{kp_id}/weak-students`（下钻懒加载：top N 学生 + 错误次数 + 最近 1 条错误样本）
- [x] 1.4 出题工作台支持 URL 参数预选知识点（`?kp=`）

## 2. 测试（后端）

- [x] 2.1 班级过滤正确性：同批作答只计入任教班学生
- [x] 2.2 MIN_ANSWERS 阈值：<5 作答不出错误率；全局口径回归不受影响
- [x] 2.3 权限：无任教关系 403；ScopeGuard 生效
- [x] 2.4 静默降级：无数据/异常不 500
- [x] 2.5 weak-students：排序正确、样本字段完整、他班 403

## 3. 前端

- [x] 3.1 引入 react-force-graph-3d + three，路由级 code-split
- [x] 3.2 DESIGN.md 增补 R-C9（深底独立接缝规则）
- [x] 3.3 `StarMap.tsx`：星系团布局（6 册锚点）+ 亮度/大小/灰星编码 + 辉光 sprite + active 边连线 + 缓速自转（可关）
- [x] 3.4 班级切换器 + 图例（亮度/大小/无样本灰星）
- [x] 3.5 `StarDrilldownPanel.tsx`：档案 + 薄弱学生懒加载 + 先修上下文（本地计算）+「出变式题」跳转预选
- [x] 3.6 `KnowledgeMapAdminPage` graph 视图接 StarMap（list 视图与边管理保留，浅色不变）
- [x] 3.7 vitest：数据映射纯函数（错误率→亮度、作答量→大小、无样本降级）

## 4. 验收

- [x] 4.1 pytest 1925 过 / 2 挂（test_local_embedding×2，本机 C 盘有真实嵌入缓存所致的环境依赖存量失败，与本次 diff 无关）；evals fast 137/138（`send_class_notification` 缺 L0 清单为分支存量缺口，`git diff HEAD` 证实未触碰工具层）
- [!] 全量 vitest 在重负载下有抖动（两次运行挂的文件不同）；5 个受影响文件单独复跑 23/23 全过，含改动过的 ExamPage 用例
- [x] 4.2 前端 vitest 全绿 + vite build 通过
- [x] 4.3 手测（浏览器实测，2026-10-31）：深底画布 ✓ / 6 星系团分离 ✓ / 亮度·大小编码 ✓ / 下钻面板（硫单质 90.6%/191 答/方程式/细则）✓ / 巡航暂停 ✓ / 出题跳转预选待手点 / 班级切换待手点 / list 保底可用（未动）
- [x] 4.4 缺陷修复记录：① 13 条悬空边（老边以中文名为端点，如「物质的量→氧化还原反应」）使 force-graph 数据管道静默中断、全图叠在原点——StarMap 已过滤悬空边防御；**数据修复待教师审定**（边管理列表可见这 13 条，建议驳回重建）。② cluster 力挂载时机（Suspense 前跑 effect 拿到 null ref）——改 ref callback + fgReady 置位
