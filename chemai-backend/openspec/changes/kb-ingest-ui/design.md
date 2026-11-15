# Design: kb-ingest-ui

## Context

`kb-review-ui` 已交付 `/admin/kb` 页面（状态卡 + 审校队列）与 `app/api/kb_admin.py`（含 `/api/kb` 进 REFERENCE_ROUTERS）。摄取链路现状：`python -m app.services.kb_ingest --module X`，嵌入用本地 bge-large-zh（`EMBEDDING_CACHE_DIR`），chroma 0.4.22 的集合操作有元数据覆盖与 WAL 竞态两个已知坑（HANDOFF 坑 1/3）。

## Goals / Non-Goals

**Goals:**
- 界面触发摄取 → 轮询进度 → 成功/失败可观测，失败不损旧索引
- 词典/标签映射同步界面化（幂等、审计）
- 全部 chroma 写操作留在主 FastAPI 进程内

**Non-Goals:**
- 评测触发（LLM/嵌入成本，永久 CLI）
- 任务取消（首版失败重跑即可）
- 语料正文编辑（语料仍 git 管理；本 change 只触发重建）
- 多机执行

## Decisions

**D1 — 前置门作为任务 0，而非假设。** 实施首件事是部署机环境探测（模型目录可达 + 加载冒烟 + chroma 同进程验证），结果书面记录；不满足则本 change 中止归档（status 记录 deferred），`/admin/kb` 状态卡退化为「摄取命令复制」。这个门保护的是：不部署模型环境的机器上出现一个永远 failed 的按钮。

**D2 — Job 机制：进程内 registry + DB 历史。** 运行态（队列/进度）在进程内单例；任务定义与历史落 `kb_jobs` 表（迁移同 `app/utils/migrate.py` 增量惯例），重启后历史可查、运行中任务标记 interrupted。备选 celery/RQ——否决：引入 broker 违背单机部署现实，且跨进程碰 chroma 直接踩坑 3。

**D3 — 影子集合构建-切换。** 摄取目标写入 `kb_{module}_{job_id}` 影子集合，成功后更新集合注册指向并清理旧集合；失败删除影子集合。备选「原地删了重摄」（CLI 现状）——否决：中途失败 = 索引不可用，界面化后失败概率与频次都会上升，必须有安全网。集合注册指向的读侧消费（vector_search.py）按现有 list→get 模式改造，行为不变。

**D4 — 触发端点自检双保险。** 除任务 0 的部署期探测外，触发时服务端运行时自检（模型目录存在 + 嵌入函数可加载），缺失返回结构化错误。环境可能在部署后被移除，不能只靠上线时的一次性检查。

**D5 — 词典/标签映射走同步端点。** 二者是幂等 JSON 导入、秒级，job 化是过度设计；复用 `kb_dictionary`/`kb_tag_map_import` 的服务函数（同 D1 of kb-review-ui 的抽取纪律）。

**D6 — 同册并发约束。** registry 条件检查 + DB 条件更新双保险；拒绝而非排队（排队会让「影子集合」的切换顺序变复杂，首版不需要）。

## Risks / Trade-offs

- [影子集合占用双倍磁盘（构建期）] → 六册语料规模可控；构建成功即清理旧集合，峰值双倍为可接受瞬态。
- [集合注册指向切换的读侧改造波及检索路径] → 对拍测试：切换前后同 query 检索结果一致；kb_eval 回归。
- [job 卡死（进程异常退出留下 running 态）] → 启动时把 DB 中非终结态任务标 interrupted，界面显示「服务重启中断，可重跑」。
- [嵌入环境变量遗漏（EMBEDDING_CACHE_DIR）] → 自检覆盖；HANDOFF 已有此坑记录，任务面板错误摘要直接提示该环境变量。
- [长任务占用主进程线程池] → BackgroundTasks 单并发槽（同册互斥已保证），摄取为低频运维动作，不做资源隔离。

## Migration Plan

1. 任务 0 环境探测 → 不满足即中止（回滚成本为零）。
2. 满足则：迁移（kb_jobs 表）→ job 机制与端点 → 影子集合改造 → 前端面板。
3. 回滚：revert 后 CLI 流程原样可用（服务函数抽取保持 CLI 入口不变是前提）；kb_jobs 表留存无害。

## Open Questions

- 无（进度粒度——按文档数还是 chunk 数——属实现细节，实施时按摄取函数现有回调能力定）。
