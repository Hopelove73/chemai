# Proposal: rag-retrieval-test

## Why

现在调检索参数只能改 `.env` 重启服务，或者跑 `evals/kb_eval` 等半天出一份报告。教研人员想问的是：「这个知识点，学生这么问，到底能不能召回到那一页？」—— 今天没有任何界面能回答。

调研里 Dify / FastGPT / RAGFlow **全部**把「召回测试」放在知识库一级入口：输入 query → 看命中 chunk 与分数 → 当场判断是语料问题还是参数问题。这是修召回的入口，也是金标问题的来源。

依据 `docs/管理后台V2方案_20261104.md` §3.6。

## What Changes

- **新增** 页面 `/admin/rag/retrieval`（gate `knowledge:update`）：Query 输入 + 过滤（册 / 知识点 / 文档）+ 临时配置面板（TopK / 阈值 / 图增强开关，**只影响本次测试，不写默认配置**）+ 命中列表（排名 / 内容 / 分数 / 来源 / 是否图扩展召回）
- **新增** 端点 `POST /api/rag/retrieval-test`：接受 query + 覆盖配置，调用现有检索管线（`kb_context` 的向量检索 + 图增强），返回带分数的命中块
- **新增** 动作：保存为金标问题（勾选期望 chunk，选"全部命中/任一命中"）、标记 bad case（原因七选一）、跳转 KB 页编辑该 chunk
- **非目标**：不做 A/B 并排对比（P1）；不改检索默认参数（默认值仍由 env/运行时开关控制）；不重排候选集以外的内容

## Capabilities

### New Capabilities
- `rag-retrieval-probe`: 召回测试——临时参数覆盖下调用检索管线、命中与分数展示、金标与 bad case 沉淀

### Modified Capabilities
- 无

## Impact

- **新增文件**：`frontend/src/pages/admin/RagRetrievalPage.tsx`、`app/api/rag_ops.py` 内的检索测试端点（与 rag-observability 共用模块）
- **修改文件**：`app/services/kb_context.py`（抽出可传覆盖配置的检索函数；现有 `build_kb_context` 行为不变）、`frontend/src/App.tsx`
- **测试**：覆盖配置只影响本次调用（不污染默认）、图增强开关生效、无命中返回空列表而非 500、chroma 不可用时结构化降级、权限断言
- **依赖**：`admin-shell-v2`；与 `rag-observability` 共用 `app/api/rag_ops.py`，两者可并行实施但需约定模块边界
