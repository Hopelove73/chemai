# Tasks: kb-review-ui

## 1. 后端：权限、迁移与只读端点

- [x] 1.1 `app/middleware/auth.py`：`ROLE_PERMISSIONS['admin']['knowledge'] = ['read','update']` + MENUS 加 `admin_kb` 项，verify：pytest 断言 `permissions_for('admin')` 含 `knowledge:update`、`menus_for('admin')` 含 `admin_kb`、`menus_for('academic_admin')`/`menus_for('teacher')` 不含
- [x] 1.2 `app/utils/migrate.py` 增量函数建 `kb_review_records` 表（package_id/来源/status/reviewer/reason/指纹/timestamps），verify：pytest 迁移用例（幂等重跑不炸）
- [x] 1.3 新增 `app/api/kb_admin.py`：`GET /api/kb/status`（`knowledge:read`；六册摄取状态/chunk 计数/词典覆盖/最近评测阈值，全推导式只读），verify：pytest 空库与有库两态、无副作用断言
- [x] 1.4 审校包列表/详情端点（`knowledge:read`；复用 `kb_graph_import` 读取函数解析为 diff 结构；解析失败返回结构化错误），verify：pytest 至少覆盖一个历史审校包 + 一个坏包

## 2. 后端：批准落库与审计

- [x] 2.1 `app/services/kb_graph_import.py` 如落库逻辑内联于脚本入口则抽出服务函数（CLI 行为不变），verify：`python -m app.services.kb_graph_import --help` 行为不变 + 既有评测/摄取测试全绿
- [x] 2.2 `POST /api/kb/review-packages/{id}/approve` 与 `/reject`（`knowledge:update`；approve 复用服务函数同步落库；状态机 pending→approved/rejected 条件更新；内容指纹校验；成功写 `OperationLog`），router 注册进 `REFERENCE_ROUTERS`（`/api/kb`），verify：`python -m pytest tests/test_permission_matrix.py -q` 全绿
- [x] 2.3 测试三件套：角色断言（admin 200 / academic_admin·teacher 批准端点 403）；幂等（已 approved 再批准被拒且不重复落库）；并发条件更新（两请求仅一成功）；审计用例（批准/驳回后 `/api/logs` 可查；失败不写），verify：pytest 全绿

## 3. 前端：KB 管理页

- [x] 3.1 `App.tsx` 注册 `admin_kb` 菜单（元素符号 `B`，沿 admin-console-p0 design D8）+ `/admin/kb` 路由（页面 `pages/admin/AdminKbPage.tsx`），verify：vitest 菜单快照 + `npm run build` + `npm run lint:tokens` 通过
- [x] 3.2 `AdminKbPage`：状态卡（空态分支）+ 审校队列（列表/筛选）+ diff 视图（新增/修改/冲突分类）+ 批准/驳回确认流（含「文件已变化」指纹提示）+ 结果反馈；批准为不可逆方向走行内两段式（R-I5），动作走 `useAsyncAction`+`ActionButton`（R-I6）；页面三态 R-S1，verify：vitest（diff 渲染、空态含行动引导、重复批准的拒绝提示、驳回理由表单、两段式状态机）
- [x] 3.3 审计联动：批准/驳回后从页面可跳转 `/admin/logs` 看到对应记录，verify：手动联动检查

## 4. 验收（集成检查）

- [x] 4.1 对拍验收：同一审校包界面批准与 CLI `kb_graph_import`（对照库）落库结果一致（图谱边 + 词典内容逐项比对），verify：pytest 对拍用例通过
- [x] 4.2 全量回归不降基线：`python -m ruff check app/ agent/`、`python -m pytest tests/ -q`、`python evals/run_evals.py --fast`、前端 `npm run build` + `npm run lint:tokens`，verify：零新增失败 + 令牌守门零违规
- [ ] 4.3 HANDOFF 记录批准语义变更（界面为权威载体、`_reviews.json` 为导出产物）与新表说明，verify：文档存在且含迁移/回滚口径

## 5. 文档与分块集中管理（2026-11-04 追加，用户要求"像球球那样管块和文档"）

- [x] 5.1 `app/services/kb_chunks.py`：按 source_path 聚合文档、列出分块、禁用开关（30s 缓存）
- [x] 5.2 端点：`GET /api/kb/documents`、`GET /api/kb/documents/chunks?path=`、`POST /api/kb/chunks/{id}/status`
- [x] 5.3 禁用语义：`KbChunkOverride` 表 + `kb_context.build_kb_context(exclude_ids=...)` —— 禁用后检索立即不命中；全部被排除按 miss 处理
- [x] 5.4 `/admin/kb` 增「文档与分块」页签：文档表（模块/主题/分块数/已禁用）+ 分块抽屉（正文渲染、知识点、禁用/恢复）
- [x] 5.5 测试：`tests/test_kb_chunks.py`（聚合、禁用标记、**禁用确实影响检索**、全排除→miss）+ 角色门断言

## 6. 正文在线编辑（2026-11-04 追加）

- [x] 6.1 `GET /api/kb/documents/content`（含 module 回退解析：source_path 前缀不统一，HANDOFF 坑 7）
- [x] 6.2 `PUT /api/kb/documents/content`：只写 md（git 真源），写审计；保存后提示「重新摄取才生效」
- [x] 6.3 页面「编辑正文」→ 文本域 + 保存 + 一键触发该册摄取
