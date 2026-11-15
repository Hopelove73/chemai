# Tasks: integration-persistence

## 1. 后端：表与服务

- [x] 1.1 `app/utils/migrate.py` 增量函数建三表（`integration_api_keys`/`integration_webhooks`/`integration_logs`），verify：pytest 迁移幂等
- [x] 1.2 Webhook 服务改造为读写穿透（写落表、启动加载、接口不变；`trigger_webhook_event` 与测试投递结果写 `integration_logs`），verify：pytest 注册重启不丢（新建 store 实例读取）+ 测试投递成功/失败两态留痕（含 URL/事件/耗时）
- [x] 1.3 密钥服务：签发（高熵随机、明文仅响应一次、SHA-256 落库）/列表（掩码）/删除；`verify_api_key` 校验依赖（常量时间比对 + 权限清单比对 + 已删除失效），verify：pytest 只展示一次/掩码/删除后校验不过/权限比对拒绝/未接入业务端点断言（无端点声明该依赖）

## 2. 后端：端点转正

- [x] 2.1 `integration.py` 消除 8 处占位：LMS 配置 GET/POST 真实读写、密钥三端点走表、Webhook 三端点持久化、`GET /integration/logs` 读表（分页 + 事件/时间筛选），verify：pytest 往返用例（保存重启可读、日志筛选）+ `test_permission_matrix.py`/`test_permission_roles.py` 全绿（权限语义不变）
- [x] 2.2 各端点移除 `placeholder` 标记下发，verify：pytest 断言响应不再含占位特征

## 3. 前端

- [x] 3.1 `IntegrationPage` 移除 PlaceholderNotice 与占位态逻辑；签发流程改为「明文仅此一次」强提示（复制确认后才可关闭），verify：vitest（横幅移除、签发一次性提示流）+ 手动保存配置刷新仍在 + `npm run lint:tokens`
- [x] 3.2 联动确认：`ops-dashboard` 集成健康卡自动转真实数据（无代码改动，冒烟验证），verify：手动查看健康卡展示真实配置状态

## 4. 验收（集成检查）

- [x] 4.1 e2e 手动：配 LMS 配置 → 签发密钥（明文一次性）→ 注册 Webhook → 测试投递 → 重启后端 → 配置/密钥掩码/Webhook 注册全在 → `/integration/logs` 见投递记录，verify：流程走通
- [x] 4.2 全量回归不降基线：`python -m ruff check app/ agent/`、`python -m pytest tests/ -q`、`python evals/run_evals.py --fast`、前端 `npm run build` + `npm run lint:tokens`，verify：零新增失败 + 令牌守门零违规
