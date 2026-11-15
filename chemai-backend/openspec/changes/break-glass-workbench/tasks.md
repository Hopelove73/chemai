# Tasks: break-glass-workbench

## 1. 菜单登记

- [x] 1.1 `app/middleware/auth.py` MENUS 加 `{"key": "break_glass", "resource": "break_glass", "action": "read"}`，verify：pytest 断言 `menus_for('super_admin')` 含该键、`menus_for('admin')`/`menus_for('teacher')` 不含

## 2. 前端工作台

- [x] 2.1 `App.tsx` 注册 `break_glass` 菜单（元素符号 `Fr`，沿 admin-console-p0 design D8）+ `/admin/break-glass` 路由（`pages/admin/BreakGlassPage.tsx`），verify：vitest 菜单快照 + `npm run build` + `npm run lint:tokens` 通过
- [x] 2.2 `BreakGlassPage`：状态卡（生效中倒计时/空态引导，EmptyState 行动引导 R-S2）+ 发起表单（理由必填前端拦截、时长档位与上限提示、确认流含审计回显）+ 提前结束确认流 + 过期主动重查；发起/结束走 `useAsyncAction`+`ActionButton`（R-I6），verify：vitest（空理由拦截、倒计时递减、不叠开展示既有会话、revoke 确认后空态）

## 3. 验收（集成检查）

- [x] 3.1 e2e 手动全流程：super_admin 登录 → 空态 → 发起（理由+时长）→ 学校数据可见（P0 概览空态消失）→ 提前结束 → 回到空态；`/admin/logs` 可查起/停两条审计，verify：流程走通且日志两条
- [x] 3.2 全量回归：`python -m pytest tests/test_permission_matrix.py tests/test_permission_roles.py -q`、`npm run build` + `npm run lint:tokens`，verify：零新增失败 + 令牌守门零违规
