# Proposal: admin-shell-v2

## Why

上一轮把管理功能直接追加到 `MENUS` 末尾，导致教师 8 项 + 管理 7 项 = **15 项平铺在一条侧栏**，管理入口与日常教学功能混在一起、无分组、无模式边界。调研（Moodle / Canvas / Google / Shopify / Vercel / Linear）的一致结论是：8-12 项需要分组折叠，12-15 项必须二级导航；管理员兼教师时最优形态是「侧栏底部管理入口 → 整栏切换为管理导航」。

同时概览页在真实数据下几乎没有信息量：近 14 天作答为 0（演示库 76202 条作答全部是历史批量，`近7天=0 / 近30天=15`），预警 211 条全部 pending（`处理率恒为 0%`）。图表维度选错了，不是数据不够。

依据 `docs/管理后台V2方案_20261104.md`（决策锁定版）。

## What Changes

- **新增** 双模式侧栏：教学态保持现有 8 项；侧栏底部「管理中心」入口（仅 `caps.menus` 含任一 `admin_*` 时渲染）→ 整栏切换为管理导航（5 组折叠），顶部「← 返回教学」，用 `sessionStorage` 记住最后的教学页路径
- **修改** 后端 `MENUS` 增加 `group` 字段（`overview` / `org` / `content` / `ops` / `system`）；**权限矩阵零改动**，全部复用既有资源键
- **修改** 管理页面拆分：`/admin/users` 拆为 `/admin/teachers` + `/admin/classes`（旧路径 redirect，书签不失效）
- **新增** 菜单 key：`admin_students`、`admin_teachers`、`admin_parents`、`admin_classes`、`rag_traces`、`rag_retrieval`、`rag_evals`、`admin_roles`；`admin_users` 退役
- **替换** 概览页为八图看板（作答趋势 12 个月 / 家长激活漏斗 / 班级对比点图 / 知识点热力 / 风险学生散点 / 薄弱知识点 TOP10 / 成绩分布 / 预警两段条），全部对准真实数据分布，每条可点出名单
- **新增** 小样本与空态规则：n<10 不显示百分比、n<4 只给名单、n=0 显示采集进度与下一步；未测留灰不填 0
- **取代** `admin-console-p0` 的导航与页面组织、`ops-dashboard` 的两张图

## Capabilities

### New Capabilities
- `admin-shell`: 双模式侧栏与导航分组——模式切换、分组折叠、返回教学、旧路由兼容、菜单按能力显隐
- `admin-overview-charts`: 管理概览八图——真实数据映射、小样本降级、每图可下钻名单

### Modified Capabilities
- 无（`admin-console` 既有规格中的权限与审计要求全部保留，只有页面组织被本 change 取代）

## Impact

- **修改文件**：`app/middleware/auth.py`（MENUS 加 group + 新增/退役 key）、`frontend/src/App.tsx`（双模式 Shell、MENU_REGISTRY 分组、路由拆分与 redirect）
- **新增文件**：`frontend/src/components/admin/AdminShell.tsx`、`frontend/src/pages/admin/AdminTeachersPage.tsx`、`AdminClassesPage.tsx`、`frontend/src/lib/admin-nav.ts`
- **重写文件**：`frontend/src/pages/admin/AdminOverviewPage.tsx`（八图）
- **测试**：菜单分组断言（后端矩阵 + 前端渲染）、模式切换与路径记忆、旧路由 redirect、八图数据契约 pytest、小样本降级 vitest
- **失效测试**：`scripts/_e2e_verify.admin.py` 中「管理菜单排在教师菜单之后」的平铺断言需改为「管理态分组可见」；`menu-capabilities.test.ts` 的 `slice(-4)` 断言同步调整
- **非目标**：不搬 `/integration`、不做移动端管理界面、不改权限矩阵
