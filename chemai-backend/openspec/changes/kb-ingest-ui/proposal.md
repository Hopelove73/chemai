# Proposal: kb-ingest-ui

## Why

KB 摄取（`kb_ingest`，含嵌入计算）今天只能开发机 CLI 执行，且受两条硬约束：必须 `EMBEDDING_CACHE_DIR` 本地嵌入模型（bge-large-zh），删 chroma 集合与重摄必须同进程（HANDOFF 已知坑 1/3）。`kb-review-ui` 交付后，审校批准已界面化，但「批准后的语料变更何时生效到检索索引」仍靠运维者记得去跑 CLI——内容运营闭环缺最后一环。本 change 界面化摄取触发与进度跟踪。

**前置门**：部署机必须具备嵌入模型环境。本 change 以环境探测为第一任务，不满足即中止并保持 CLI 流程（此时 `/admin/kb` 状态卡提供命令复制）。依据 `docs/管理后台建设方案_20261104.md` P1b 档。

## What Changes

- **新增** 进程内任务机制：摄取触发后立即返回 `job_id`，前端轮询进度（qiuqiu 异步 job 模式）；job 历史落库，重启可查
- **新增** 摄取执行采用**影子集合构建-切换**：新索引构建成功后才切换，失败清理影子集合——摄取失败 MUST NOT 令既有检索索引不可用
- **新增** 端点：`POST /api/kb/jobs`（门 `knowledge:update`；仅白名单任务类型 `ingest` + 受控 `module` 参数；触发前环境自检）、`GET /api/kb/jobs/{id}`（门 `knowledge:read`）
- **新增** 同步端点：词典同步、标签映射导入（幂等秒级，不走 job，`knowledge:update` 门，写审计）
- **新增** `/admin/kb` 页面任务面板：启动摄取、进度轮询、历史、失败摘要与重跑入口；词典/标签同步按钮
- **非目标**：评测触发（涉及 LLM/嵌入成本，永久 CLI）；任务取消（首版失败可重跑即可）；多机/分布式执行；从界面编辑语料正文（语料仍是 git 管理的 md，本 change 只触发重建）

## Capabilities

### New Capabilities
- `kb-ingest-jobs`: KB 摄取任务的界面触发与跟踪——job 触发与轮询、影子集合安全切换、词典/标签同步、任务历史与审计

### Modified Capabilities
- `kb-review`: `/admin/kb` 页面新增任务面板区（状态卡与审校队列行为不变）——若实施时发现无需 spec 级变更则回填为无修改

## Impact

- **新增文件**: 进程内 job registry（服务层）, 影子集合切换逻辑（复用/扩展 `app/services/kb_ingest.py`）
- **修改文件**: `app/api/kb_admin.py`（job 与同步端点）, `frontend/src/pages/admin/AdminKbPage.tsx`（任务面板）, 测试
- **约束**: 一切 chroma 写操作在主 FastAPI 进程内执行（chemai.db 文件锁 + WAL 竞态，禁止第二进程写）；触发前服务端自检嵌入环境，缺失时返回结构化错误而非 500
- **前置门（任务 0）**: 部署机环境探测（模型目录可达 + 模型加载冒烟），不满足则中止本 change
