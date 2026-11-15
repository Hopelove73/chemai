# Tasks: admin-shell-v2

## 1. 后端：菜单与分组

- [x] 1.1 `app/middleware/auth.py`：`MENUS` 每项加 `group` 字段（**本批只加 admin_teachers/admin_classes；admin_students/admin_parents/rag_*/admin_roles 在批次 2–4 随页面加入**）；新增 `admin_students`(student:update)、`admin_teachers`(teacher:create)、`admin_parents`(student:update)、`admin_classes`(class:update)、`rag_traces`/`rag_retrieval`/`rag_evals`(knowledge:update)、`admin_roles`(school:update)；退役 `admin_users`，verify：`pytest tests/test_permission_matrix.py -q` 全绿且新 key 均有 `can()` 持有者
- [x] 1.2 菜单断言改为分组口径：admin 五组齐全、academic_admin 只有 overview+org 组、teacher 零管理键，verify：`pytest tests/test_permission_matrix.py -k menu -q`
- [x] 1.3 `test_permission_roles.py`：新增 `rag_traces`/`admin_parents` 角色断言（teacher 403、admin 200），verify：全绿

## 2. 前端：双模式壳

- [x] 2.1 新增 `src/lib/admin-nav.ts`：五组常量（key/label/顺序）+ `groupNavItems()`，verify：vitest 分组顺序与空组不渲染
- [x] 2.2 `App.tsx`：`MENU_REGISTRY`/`ROUTE_MENU` 更新为新 key 集合；`navItemsFor` 支持按 section 过滤，verify：vitest 菜单派生（教师 8 项 / 管理员管理态 14 项）
- [x] 2.3 新增 `components/admin/AdminShell.tsx`：管理态侧栏（分组折叠 + 当前组自动展开）、顶部「← 返回教学」、`sessionStorage` 记忆最后教学路径，verify：vitest（折叠状态、返回路径记忆、刷新后仍可返回）
- [x] 2.4 `App.tsx` 的 `Shell`：按 `pathname.startsWith('/admin')` 切换教学态/管理态；教学态侧栏底部渲染「管理中心」入口（条件：`caps.menus` 含 `admin_` 前缀），verify：vitest（教师无入口、admin 有入口）
- [x] 2.5 路由拆分：`/admin/teachers`、`/admin/classes` 新路由；`/admin/users` → redirect `/admin/teachers`，verify：vitest redirect + 手动访问旧路径
- [x] 2.6 `design/教师端.md` 增补「管理态」章节（分组、符号、模式切换规则），verify：文档存在且与实现一致

## 3. 前端：八图概览

- [x] 3.1 后端 `GET /api/admin/overview-charts`（或扩展现有 overview）：返回八图数据（趋势 12 月 / 家长漏斗 / 班级对比 / 知识点热力 / 学生散点 / 薄弱 TOP10 / 成绩分布 / 预警两段），verify：pytest 字段契约 + 空库降级
- [x] 3.2 口径复用：班级对比/热力/薄弱 TOP 走 `kb_error_rate`，verify：pytest 与教师端同班同知识点数值一致
- [x] 3.3 重写 `AdminOverviewPage`：八图 + 每图下钻入口 + KPI（含家长激活率），verify：vitest 渲染 + 数据契约
- [x] 3.4 小样本降级与诚实空态：n 分档、未测留灰、预警两段不画处理率、超管空态，verify：vitest 四档分支 + 手动核对演示库
- [x] 3.5 EChart 按需注册 heatmap/scatter/boxplot，verify：`npm run build` + 实际页面渲染

## 4. 验收

- [x] 4.1 更新 `scripts/_e2e_verify.admin.py`：平铺断言改为「管理态分组 + 返回教学 + 旧路由 redirect」，verify：脚本 14+ 项全绿
- [x] 4.2 全量回归：`ruff` / `pytest tests/ -q` / `vitest run` / `npm run build` / `npm run lint:tokens`（新增文件零违规），verify：零新增失败
- [x] 4.3 浏览器实测：教师无入口、admin 往返、八图在演示库非空、旧书签可用，verify：手动 + 截图

## 5. 审查认定推迟到后续批次（不阻塞批次 1 验收）

- [x] 5.1 小样本四档完整实现：n<4 不画点只给名单；4≤n<9 只给人数；10≤n<30 点+中位数（已补：n<4 不画点入"未展示"名单、4–9 只给人数、10–29 用中位数并标注；后端 `_class_compare` 增 median）
- [ ] 5.2 成绩分布补箱线图（n≥30）与 n<30 切换点图（本批仅直方图）
- [x] 5.3 家长漏斗三段可点击下钻（已下钻到 /admin/parents?seg=activated|unactivated；班级对比"样本不足未展示"名单已做）
- [ ] 5.4 家长「近 7 天活跃」数据源：登录时记录 Account 活跃时间（当前诚实返回「未采集」）
- [ ] 5.5 预警两段条改为条形图（当前为两张 StatCard）
