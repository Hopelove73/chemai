# Tasks: runtime-settings

## 1. 后端：表、服务与端点

- [x] 1.1 `app/utils/migrate.py` 增量函数建 `settings` 表（键/值/更新者/timestamps，键唯一），verify：pytest 迁移幂等
- [x] 1.2 RuntimeSettings 服务：白名单定义（3 键 + 类型/枚举）+ TTL 缓存（约 5s）+ `get`（覆写→env 种子默认）+ `set`（校验 + 条件写 + 失效缓存）+ `reset`（删覆写行），verify：pytest 白名单外拒绝/枚举校验/回落 env/恢复默认/缓存时序（测试用 `invalidate()`）
- [x] 1.3 `app/api/settings.py`：`GET /api/settings`（`settings:read`；键、生效值、默认值、是否覆写）与 `PUT /api/settings/{key}`（`settings:update`；写审计含旧值→新值），main.py 注册 + `REFERENCE_ROUTERS` 加 `/api/settings`，verify：`python -m pytest tests/test_permission_matrix.py -q` 全绿
- [x] 1.4 `app/middleware/auth.py`：新资源键 `settings`（admin read/update，镜像继承）+ MENUS 加 `admin_settings`，verify：pytest 角色断言（admin 200 / teacher·academic_admin 403 / `menus_for('admin')` 含 `admin_settings`、academic_admin 不含）

## 2. 后端：消费点改造

- [x] 2.1 全仓 grep 三个旗子的全部读取点；服务进程内的消费点（ocr_service / document_parse_service）改为逐调用 `runtime_settings.get(...)`，脚本路径维持 env 直读，verify：pytest 既有 OCR/解析回归全绿（默认行为不变）+ 新增用例（覆写后新请求行为变化、reset 后回落）
- [x] 2.2 审计用例：修改后 `/api/logs` 可查旧值→新值，verify：pytest

## 3. 前端：设置页

- [x] 3.1 `App.tsx` 注册 `admin_settings` 菜单（元素符号 `Tc`，沿 admin-console-p0 design D8）+ `/admin/settings` 路由；`AdminSettingsPage`：键列表（当前生效值/默认值/覆写标记，mono 数字 R-T-L3）、布尔开关与枚举选择、恢复默认、保存反馈；修改动作走 `useAsyncAction`+`ActionButton`（R-I6），verify：vitest（显隐/校验/恢复默认态）+ 手动修改后无重启验证新值生效 + `npm run lint:tokens`
- [x] 3.2 审计联动：修改动作在 `/admin/logs` 可追溯，verify：手动

## 4. 验收（集成检查）

- [x] 4.1 热生效端到端：改 `OCR_VISION_REVIEW_ENABLED` → 5s 内新识别请求行为变化 → 恢复默认 → 行为回落，verify：手动流程 + pytest 时序用例双通过
- [x] 4.2 全量回归不降基线：`python -m ruff check app/ agent/`、`python -m pytest tests/ -q`、`python evals/run_evals.py --fast`、前端 `npm run build` + `npm run lint:tokens`，verify：零新增失败 + 令牌守门零违规
