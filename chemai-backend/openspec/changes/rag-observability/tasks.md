# Tasks: rag-observability

## 1. 后端：表与埋点

- [ ] 1.1 grep 全部检索调用点，确认 `tutoring.py:312` 为唯一入口（含图增强），verify：检查记录写入 design 或本任务备注
- [x] 1.2 `qa_traces` 表（model + `migrate.py` 幂等 DDL + 索引：created_at / user_id / zero_hit / refusal_flag / top1_score），verify：pytest 迁移幂等
- [x] 1.3 `app/services/qa_trace.py`：`record_trace()`（异步、独立 session、异常静默、开关 `QA_TRACE_ENABLED`），verify：pytest（正常写入；抛错时调用方不失败）
- [x] 1.4 `tutoring.py` 埋点：回答产出后写 trace（问题/配置快照/hits/zero_hit/top1/answer/延迟/模型/索引版本），verify：pytest 一次真实 tool 调用后库中有 trace 且 hits 与 kb_out 一致
- [x] 1.5 学生反馈端点 `POST /api/rag/traces/{id}/feedback`（up/down + 文本），verify：pytest 写入且幂等（重复提交覆盖不新增）

## 2. 后端：查询与指标

- [x] 2.1 `GET /api/rag/traces`（七筛选 + 分页 + 排序），verify：pytest 每个筛选单独生效
- [x] 2.2 `GET /api/rag/traces/{id}`（七段详情，含 chunk 快照），verify：pytest 字段完整
- [x] 2.3 `POST /api/rag/traces/{id}/label`（good/bad + 七类原因 + 修正答案），verify：pytest 状态与原因可筛
- [x] 2.4 `POST /api/rag/traces/{id}/to-golden`（回流金标集，状态 draft，来源 trace），verify：pytest 金标表新增且来源正确（跨 change 依赖，可先落占位接口）
- [x] 2.5 `GET /api/rag/metrics`（命中率/零召回率/拒答率/负反馈率/低分命中率 + 时间窗），verify：pytest 口径（命中+零召回=1）
- [ ] 2.6 保留期清理（180 天，分批 DELETE + 写日志），verify：pytest 过期数据被清、未过期不受影响
- [ ] 2.7 权限：全部端点 `knowledge:update`（读指标也用 update 门），进 `REFERENCE_ROUTERS`，verify：`pytest tests/test_permission_matrix.py`

## 3. 前端

- [x] 3.1 `RagTracesPage`：列表（七筛选 + 排序 + 分页），verify：vitest 筛选状态与空态
- [x] 3.2 详情抽屉：七段证据链 + 命中块分数条 + 标注/修正/回流/禁用块动作，verify：vitest（渲染七段、标注回填）
- [x] 3.3 指标条：五指标 + 时间窗切换，verify：vitest
- [ ] 3.4 学生端反馈按钮（消息级），verify：vitest 点击后调用端点

## 4. 验收

- [ ] 4.1 真实问答冒烟：提问 → 日志出现完整证据链 → 标注 → 回流金标，verify：浏览器全流程
- [ ] 4.2 全量回归：ruff / pytest / vitest / build / lint:tokens，verify：零新增失败
- [ ] 4.3 性能：埋点开启前后同一问题的响应时间差异 < 50ms，verify：手动计时记录


## 实施记录（2026-11-04）

已完成：表 + 异步埋点（失败静默、开关 QA_TRACE_ENABLED）、七筛选列表、详情七段、
标注（七类原因）、回流金标、五项指标（排除 retrieval_test 行）。

未完成（下一批）：
- [ ] 1.1 其他问答入口排查（当前只埋 tutoring 的 chemistry_tutor；练习/判卷讲评等入口未埋）
- [ ] 2.6 保留期清理任务（180 天）
- [ ] 3.4 学生端消息级反馈按钮（目前反馈只能用管理端接口写）
- [ ] 4.1 真实问答全链路冒烟（本轮只验证了召回测试链路）
- [ ] 4.3 埋点前后响应时间对比

