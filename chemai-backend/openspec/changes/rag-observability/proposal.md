# Proposal: rag-observability

## Why

这是整个 V2 里最重要的一个 change。调研结论（Dify / FastGPT / RAGFlow / Coze / Langfuse / LangSmith 横向对比）：

> 是否记录每一次问答的「问题 → 命中 chunk + 分数 → 模型回答 → 用户反馈」，是「RAG 运营后台」和「知识库管理页」的分水岭。

我们目前只有知识生产（KB 审校 + 摄取），**线上观测为零**：一个学生问"电解池的阴阳极怎么判断"，模型答错了，我们无法回答三个问题 —— 是没检索到？检索到了但排名太低？还是检索对了模型编造？教师和家长质疑时拿不出证据链，评测集也无从积累。

`agent/tools/tutoring.py:312` 是全文**唯一**的知识检索调用点，`kb_out` 已含 hits / miss 标记 / 渲染上下文 —— 埋点位置干净，改造面小。

依据 `docs/管理后台V2方案_20261104.md` §5。

## What Changes

- **新增** 表 `qa_traces`：逐请求证据链（问题 / 改写 / 检索配置快照 / hits+分数/排名/图扩展标记 / zero_hit / top1_score / 回答 / 引用 / 拒答标记 / 延迟 / 模型 / KB 索引版本 / 用户反馈 / 运营标注 / bad_reason）
- **新增** 埋点：`tutoring.py` 回答产出后异步写 trace（短事务、失败静默 —— **绝不影响回答链路**）
- **新增** 学生侧反馈采集：回答下方「有帮助 / 没帮助」写入 `qa_traces.feedback`（复用现有消息反馈能力，如无则补一个轻量端点）
- **新增** 页面 `/admin/rag/traces`（gate `knowledge:update`）：
  - 列表 + 七筛选（时间 / 拒答 / 零召回 / 低分命中 / 负反馈 / 学生 / 班级）
  - 详情七段证据链
  - 标注好/坏（七类 bad_reason）+ 修正答案 + **回流金标集** + 一键禁用命中的坏 chunk
- **新增** 指标端点 `GET /api/rag/metrics`：检索命中率 / 零召回率 / 拒答率 / 负反馈率 / 低分命中率（口径见方案 §5.3）
- **非目标**：不做采样（日活几百全量记录成本可控，采样会漏掉坏案例）；不接 Langfuse 等外部观测平台；不做 LLM-as-judge 自动评分

## Capabilities

### New Capabilities
- `rag-qa-tracing`: 问答证据链留存与消费——trace 落库、日志页、标注与回流、线上质量指标

### Modified Capabilities
- 无

## Impact

- **新增文件**：`app/services/qa_trace.py`、`app/api/rag_ops.py`、`frontend/src/pages/admin/RagTracesPage.tsx`
- **修改文件**：`agent/tools/tutoring.py`（埋点，唯一检索调用点）、`app/main.py`、`app/models/database.py` + `app/utils/migrate.py`（新表）、`frontend/src/App.tsx`
- **测试**：trace 写入不影响回答（埋点抛错时回答仍成功）、证据链字段完整性、六指标口径、标注→回流金标集、权限（teacher 403）、数据保留策略
- **性能**：异步写 + 独立 session；列表查询走 `created_at` / `zero_hit` / `refusal_flag` 索引
- **依赖**：`admin-shell-v2`（路由与菜单）、`rag-eval-regression`（消费金标集，可反向解耦）
