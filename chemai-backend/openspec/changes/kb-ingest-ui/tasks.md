# Tasks: kb-ingest-ui

## 0. 前置门：部署机环境探测

- [x] 0.1 环境探测：`EMBEDDING_CACHE_DIR` 目录可达且含 bge-large-zh 模型、嵌入函数加载冒烟、chroma 主进程读写验证，结果书面记录到本 change 目录 `env-check.md`；**不满足则中止本 change**（状态记 deferred，/admin/kb 退化为命令复制方案），verify：env-check.md 存在且三项结论明确

## 1. 后端：job 机制与端点

- [x] 1.1 `app/utils/migrate.py` 增量函数建 `kb_jobs` 表（类型/module/状态/进度/错误摘要/操作者/任务标识/timestamps）+ 启动时非终结态标 interrupted，verify：pytest 迁移幂等 + 重启中断标记用例
- [x] 1.2 进程内 JobRegistry 单例（运行态队列/进度；条件更新防同册并发），verify：pytest 并发触发同册第二次被拒并返回进行中任务标识
- [x] 1.3 `POST /api/kb/jobs`（`knowledge:update`；白名单 `ingest` + 受控册名；运行时环境自检，缺失返回结构化错误；立即返回任务标识；写 `OperationLog`），verify：pytest 权限断言（academic_admin/teacher 403）、非法参数拒绝、环境缺失结构化错误
- [x] 1.4 `GET /api/kb/jobs/{id}`（`knowledge:read`；queued/running/succeeded/failed + 进度 + 错误摘要；历史落库重启可查），verify：pytest 四态流转 + 重启后历史可查
- [x] 1.5 影子集合构建-切换：扩展 `app/services/kb_ingest.py` 支持目标集合参数与切换/清理（读侧 `vector_search.py` 按注册指向取集合，行为不变），verify：pytest 成功切换 + 失败清理影子集合 + 切换前后同 query 检索结果对拍
- [x] 1.6 词典同步/标签映射同步端点（`knowledge:update`；复用 `kb_dictionary`/`kb_tag_map_import` 服务函数；幂等；写 `OperationLog`），verify：pytest 重复同步幂等 + 审计留痕 + `REFERENCE_ROUTERS` 校验全绿

## 2. 前端：任务面板

- [x] 2.1 `AdminKbPage` 增任务面板：按册触发摄取（确认流）+ 进度轮询（终止态停轮询）+ 失败摘要（含 `EMBEDDING_CACHE_DIR` 提示）+ 重跑入口 + 历史；词典/标签同步按钮与结果反馈；触发/重跑走 `useAsyncAction`+`ActionButton`（R-I6），verify：vitest（触发确认流、轮询终止、失败提示、同册进行中禁用按钮）+ `npm run lint:tokens`
- [x] 2.2 审计联动：触发/同步动作在 `/admin/logs` 可追溯，verify：手动联动检查

## 3. 验收（集成检查）

- [x] 3.1 端到端真实摄取一册：界面触发 → 轮询至成功 → 检索冒烟（新内容可命中）+ `kb_eval` fast 回归，verify：全流程通过且评测零回归
- [x] 3.2 失败注入：模拟嵌入失败（改坏模型路径）触发摄取 → 任务 failed、影子集合清理、既有检索正常，verify：pytest + 手动各验证一次
- [x] 3.3 全量回归不降基线：`python -m ruff check app/ agent/`、`python -m pytest tests/ -q`、`python evals/run_evals.py --fast`、前端 `npm run build` + `npm run lint:tokens`，verify：零新增失败 + 令牌守门零违规
