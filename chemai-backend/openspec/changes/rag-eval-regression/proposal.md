# Proposal: rag-eval-regression

## Why

教材、考纲、校本题库会持续更新，每次改 KB 都可能**悄悄弄坏原来答对的一批题**。今天唯一的防线是 `evals/kb_eval`（CLI + 固定阈值），它回答"整体过没过"，不回答"**这次改动让哪几道题从过变成没过**"。

调研里 Dify 的 Quality（金标问题 + bad case + 评测）、Langfuse 的 baseline/candidate 对比，都是同一件事：让"改完能不能发"变成可判定。我们还有额外优势 —— `rag-observability` 的问答日志能持续产出真实坏案例，天然是金标集的来源。

依据 `docs/管理后台V2方案_20261104.md` §3.8。

## What Changes

- **新增** 金标集表 `golden_questions`：问题 / 期望 chunk / 期望答案 / 章节与知识点标签 / 匹配规则（全部命中 · 任一命中）/ 状态（active / draft / stale / paused）/ 来源（手工 / 日志回流 / 召回测试）
- **新增** 页面 `/admin/rag/evals`（gate `knowledge:update`）：金标集管理（列表/导入导出 CSV/按章节组织）、跑评测、逐题结果、**baseline vs candidate 逐题 diff**（新增失败 / 修复项）
- **新增** 端点：`GET/POST /api/rag/golden-questions`、`POST /api/rag/evals/run`（异步 job，复用 `kb_jobs` 的 jobId 轮询模式）、`GET /api/rag/evals/{job_id}`
- **新增** 指标：期望命中率 / 拒答正确率 / 忠实度（faithfulness，先做简单 claim 支撑判断）/ 答案相关性
- **新增** 发布门禁（软）：KB 变更后再跑评测，未达标在 KB 页提示「最近评测未通过」
- **非目标**：不做硬门禁（不阻塞发布，先积累数据）；不做 LLM-as-judge 大规模自动评分；不复刻 RAGAS 全套指标

## Capabilities

### New Capabilities
- `rag-eval-regression`: 金标集与评测回归——金标管理、异步评测、baseline/candidate 逐题对比、发布提示

### Modified Capabilities
- 无

## Impact

- **新增文件**：`app/services/rag_eval.py`、`frontend/src/pages/admin/RagEvalsPage.tsx`
- **修改文件**：`app/api/rag_ops.py`（评测端点）、`app/models/database.py` + 迁移（`golden_questions`、`rag_eval_runs`）、`frontend/src/App.tsx`、KB 页（最近评测提示）
- **测试**：金标 CRUD 与 CSV 往返、匹配规则（全部/任一命中）、逐题 diff 正确性（构造 baseline 过/candidate 挂）、异步 job 状态流转、期望 chunk 被删后标 stale
- **依赖**：`rag-observability`（日志回流金标集）、`kb-ingest-ui`（jobId 轮询模式复用）
