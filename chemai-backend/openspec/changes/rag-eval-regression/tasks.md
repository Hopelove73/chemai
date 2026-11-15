# Tasks: rag-eval-regression

## 1. 后端：表与金标集

- [x] 1.1 `golden_questions` 表（问题/期望块 JSON/期望答案/章节标签/匹配规则/状态/来源）+ 索引，verify：pytest 迁移幂等
- [x] 1.2 `rag_eval_runs` 表（run_id/状态/指标 JSON/逐题结果 JSON/config 快照/时间），verify：pytest 迁移幂等
- [ ] 1.3 金标 CRUD：`GET/POST/PUT/DELETE /api/rag/golden-questions` + CSV 导入导出，verify：pytest（CSV 往返一致、非法匹配规则 400）
- [ ] 1.4 stale 标记：期望块不存在时自动标 stale 并跳过，verify：pytest 构造缺失块

## 2. 后端：评测执行

- [x] 2.1 `POST /api/rag/evals/run`：立即返回 run_id，异步执行（同时间仅一个任务），verify：pytest 并发第二次被拒
- [x] 2.2 `GET /api/rag/evals/{run_id}`：状态 + 指标 + 逐题，verify：pytest 四态流转
- [ ] 2.3 指标计算：期望命中率（按匹配规则：全部/任一）、拒答正确率；忠实度/相关性留 null 字段，verify：pytest 构造 10 题中 9 题命中 → 90%
- [x] 2.4 逐题 diff：`GET /api/rag/evals/compare?baseline=&candidate=`，返回新增失败/由败转过/仍失败/仍通过，verify：pytest 构造 baseline 过 candidate 挂
- [x] 2.5 权限（读 `knowledge:read`、写 `knowledge:update`）+ 进 `REFERENCE_ROUTERS`，verify：`pytest tests/test_permission_matrix.py`

## 3. 前端

- [x] 3.1 `RagEvalsPage`：金标集列表（状态/标签/筛选）+ 新增/编辑 + CSV 导入导出，verify：vitest（CRUD、CSV 流程）
- [x] 3.2 跑评测：选择配置 → 进度轮询 → 指标卡 + 逐题表，verify：vitest（轮询终止、失败摘要）
- [x] 3.3 回归对比：选 baseline/candidate → 四类差异 + 下钻单题命中块差异，verify：vitest（四类计数正确）
- [ ] 3.4 KB 页"最近评测未通过"提示（软门禁），verify：vitest + 手动

## 4. 验收

- [ ] 4.1 真实跑一轮：≥10 题金标集 → 指标产出 → 改动阈值后再跑 → diff 显示差异，verify：浏览器全流程
- [ ] 4.2 全量回归：ruff / pytest / vitest / build，verify：零新增失败


## 实施记录（2026-11-04）

已完成：金标表与 CRUD、异步评测（jobId + 轮询，同时间仅一轮）、
期望命中率（any/all）与拒答正确率、baseline/candidate 逐题四类归类、前端三块。

未完成（下一批）：
- [ ] 1.3 金标 CSV 导入导出
- [ ] 1.4 期望块消失 → stale 自动标记（当前按 id/来源+小节双条件匹配，不会误判为通过）
- [ ] 2.3 忠实度/答案相关性（字段未加，先只做两项硬指标）
- [ ] 3.4 KB 页「最近评测未通过」软门禁提示

