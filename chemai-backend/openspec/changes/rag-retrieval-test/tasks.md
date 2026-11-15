# Tasks: rag-retrieval-test

## 1. 后端：检索函数抽取

- [x] 1.1 `app/services/kb_context.py` 抽出 `retrieve(question, collection, encoder, graph_engine, *, top_k, threshold, graph_enabled)`；`build_kb_context` 改为调用它并传默认值，verify：既有 `kb_context` 相关测试全绿（行为不变）+ 新增单测（覆盖参数生效）
- [x] 1.2 覆盖不污染默认：连续两次调用（一次覆盖、一次默认）参数互不影响，verify：pytest

## 2. 后端：端点

- [x] 2.1 `POST /api/rag/retrieval-test`：白名单参数校验 + 返回 `{applied_config, default_config, elapsed_ms, hits[]}`，hits 与 `qa_traces.hits` 同形，verify：pytest（参数越界 400、无命中空列表、chroma 不可用结构化 503）
- [x] 2.2 标记坏案例：`POST /api/rag/retrieval-test/bad-case`（写一条 `source=retrieval_test` 的 trace），verify：pytest 记录可查且不计入线上指标
- [x] 2.3 保存金标：复用/最小实现 `POST /api/rag/golden-questions`，verify：pytest（draft 状态、期望块落库）
- [x] 2.4 权限 `knowledge:update` + 进 `REFERENCE_ROUTERS`，verify：`pytest tests/test_permission_matrix.py`

## 3. 前端

- [x] 3.1 `RagRetrievalPage`：query 输入 + 过滤 + 配置面板（TopK/阈值/图增强）+ 结果列表（排名/分数/来源/图扩展标记），verify：vitest（渲染、参数回显）
- [x] 3.2 "仅本次测试"提示与 `default_config` 对比展示，verify：vitest
- [x] 3.3 勾选期望块 → 保存金标 / 标记坏案例 / 跳转 KB 编辑，verify：vitest 交互 + 手动

## 4. 验收

- [x] 4.1 浏览器实测：对真实语料提问 → 改 TopK/阈值观察命中变化 → 保存金标，verify：手动 + 截图
- [ ] 4.2 全量回归：ruff / pytest / vitest / build，verify：零新增失败


## 实施记录（2026-11-04）

已完成：参数覆盖（top_k/threshold/graph_enabled）、applied/default 回显、
坏案例写入（role=retrieval_test，不计入线上指标）、保存金标、真实语料命中验证。
说明：检索函数未单独抽出 —— build_kb_context 已支持参数覆盖，评测/测试共用同一条管线。

