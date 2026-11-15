# Design: rag-eval-regression

## Context

- `evals/kb_eval` 是离线 CLI：固定阈值扫描 + 报告，无金标集概念、无逐题 diff、无界面。
- `rag-observability` 会持续产出带标注的坏案例，是金标集的天然来源。
- `kb-ingest-ui` 已有 jobId 轮询模式（`kb_jobs` 表 + `JOB_REGISTRY`），评测任务可复用同一套。

## Goals / Non-Goals

**Goals**
- 金标集可维护（手工 + 日志回流 + CSV）
- 一次评测能出整体指标与逐题结果
- baseline vs candidate 逐题 diff：修好了哪些、弄坏了哪些

**Non-Goals**
- 不做硬门禁（不阻断摄取/发布）
- 不做 LLM-as-judge 大模型评分（忠实度先用轻量规则）
- 不复刻 RAGAS 全套

## Decisions

**D1 — 评测跑在异步 job 里，复用 `kb_jobs` 的任务表与轮询端点模式（新表 `rag_eval_runs`，不复用 kb_jobs 的行）。**
评测要调嵌入 + 检索 N 次，可能分钟级；同步会超时且卡住 worker。

**D2 — 指标先做两项硬指标 + 两项预留：期望命中率、拒答正确率（硬）；忠实度、答案相关性（预留字段，本轮可为 null）。**
忠实度需要 LLM 判定，成本与稳定性都不适合当前阶段；先把"该召回的召回了没、该拒答的拒答了没"做扎实。

**D3 — 逐题 diff 按"期望块集合"比较，不看回答文本。**
检索回归是确定性的；回答文本比较噪声大。答案质量留给人工标注。

**D4 — 期望块消失的题标 `stale` 并跳过，不计入分母。**
否则重摄取一次会让命中率无故下跌，产生假回归。

**D5 — 快照式运行记录：每次运行存 `config_snapshot`（TopK/阈值/模型/索引版本）。**
baseline 与 candidate 必须能说清"差在哪"，否则 diff 没有解释力。

## Risks / Trade-offs

- [评测耗时长] → 金标集按章节分子集跑；单次上限 200 题。
- [金标集质量差导致指标失真] → 提供"问题-期望块"预览与批量删除；stale 自动标记。
- [并发跑两次评测] → 同一时间只允许一个评测任务（条件更新）。
- [与 `kb_eval` CLI 口径不一致] → 本 change 不改 CLI；文档说明两者分工（CLI = 阈值扫描，页面 = 金标回归）。

## Migration Plan

新表 `golden_questions` / `rag_eval_runs`；纯增量。回滚 revert，表保留无害。

## Open Questions

- 忠实度指标的具体实现（claim 抽取规则 vs 小模型判定）留到有真实标注数据后再定。
