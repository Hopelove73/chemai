# Design: admin-shell-v2

## Context

现状：`MENUS` 里教师 10 个 key + 管理 7 个 key 顺序排列，`menus_for()` 一起下发，`navItemsFor()` 平铺渲染成 15 项。`Shell` 只有一套侧栏。`/admin/users` 一页三 Tab 混装教师审批、教师账号、班级管理。

调研结论（Shopify / Vercel / Linear）：管理区应是"独立区域而非普通菜单项"；进入后整栏替换，并有明确返回路径。

## Goals / Non-Goals

**Goals**
- 教学态侧栏回到 8 项，管理态 5 组折叠
- 模式由路由决定（无额外状态），往返可逆
- 权限矩阵零改动，旧路由可用
- 概览页在真实数据上有信息量

**Non-Goals**
- 不做独立子域/独立应用
- 不搬 `/integration`、不做移动端管理
- 不改任何端点的权限语义

## Decisions

**D1 — 模式由路由前缀决定，不用全局状态。**
`/admin/*` → 管理态，其余 → 教学态。理由：深链接、刷新、e2e 都可预测；用开关状态会出现"URL 是 /admin 但侧栏是教学"的不一致。

**D2 — `MENUS` 加 `group` 字段，分组顺序硬编码在前端常量。**
后端只声明"这项属于哪组"，组顺序与中文名是展示细节（与 `MENU_REGISTRY` 同一条纪律：权限真源在后端，展示在前端）。备选：后端下发组序 —— 否决，改一次顺序要动权限文件。

**D3 — 管理中心入口渲染条件 = `caps.menus` 含任一 `admin_*`。**
不判断角色名。这样 `break_glass`（super_admin 独有）也能自然出现入口。

**D4 — 「返回教学」用 `sessionStorage` 记住最后一个非 `/admin` 路径，默认 `/chat`。**
不用 `history.back()`（可能是外部链接或刷新的空历史）。

**D5 — 拆页用 redirect 而不是保留旧页。**
`/admin/users` → `<Navigate to="/admin/teachers" replace>`，避免两份实现并存腐化。

**D6 — 八图分两批查询：概览端点聚合 + 复用 `kb_error_rate`。**
班级对比、知识点热力、薄弱知识点走既有 `kb_error_rate`（含 MIN_ANSWERS 口径）；学生散点、成绩分布、家长漏斗走新聚合 SQL。所有查询加时间窗上限并走索引列。

**D7 — 图表库用既有 `ui/EChart`，不引新依赖。**
热力用 `heatmap` + `visualMap`，散点用 `scatter`，箱线图用 `boxplot`（echarts 均内置，需按需注册）。

## Risks / Trade-offs

- [15 项拆 14 页后每页变薄] → 接受：管理页本来就该"一页一件事"，薄页比混装页好维护。
- [八图聚合在演示库（76k 作答）上变慢] → 先按时间窗 + 索引；实测超 500ms 再加汇总表。
- [旧 e2e 断言失效] → 同步改 `_e2e_verify.admin.py` 与 `menu-capabilities.test.ts`，不静默删断言。
- [双模式切换引起路由动画抖动] → 复用现有 `pageTransition`，模式切换不额外加动画。

## Migration Plan

1. 后端：`MENUS` 加 group + 新增/退役 key → 菜单断言测试绿
2. 前端：`admin-nav.ts`（分组常量）→ `AdminShell` → 路由拆分 + redirect
3. 概览页重写（八图）
4. 回归：`test_permission_matrix` / `test_permission_roles` / 前端全量 / e2e 改断言后重跑
5. 回滚：前端整体可 revert；后端只改 `MENUS`（无迁移），revert 即恢复平铺

## Open Questions

- 无（分组顺序与组名已在方案文档确定）
