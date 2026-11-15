# Proposal: kb-review-ui

## Why

KB 知识库运维 100% CLI 化：审校包落库靠 `python -m app.services.kb_graph_import`，词典靠 `kb_dictionary`，老师终审是**会话里口头批准 + `_reviews.json` 落盘**——不可追溯、不可规模化，批准语义只存在于对话记录里（`chemai-backend/HANDOFF.md` 第三节）。知识库是本产品 AI 回答质量的地基（kb_error_rate/图谱上下文都源于此），内容侧的人工终审必须有界面化、可审计的载体。依据 `docs/管理后台建设方案_20261104.md` P1a 档：先做纯 DB 写的审校落库界面，不碰嵌入模型。

## What Changes

- **新增** 资源键动作：`knowledge` 增加 `update`（仅 admin，super_admin 经镜像继承）——矩阵完整性断言要求其与消费端点同批落地
- **新增** 管理菜单 `admin_kb`（gate `knowledge:update`，仅 admin/super_admin 可见）
- **新增** 后端 router `app/api/kb_admin.py`：`GET /api/kb/status`（推导式只读：六册摄取状态/chunk 计数/词典覆盖/评测阈值水位）、审校包列表与结构化详情（提议边/词典条目 diff）、`POST .../approve`（**复用 kb_graph_import 服务函数落库**）、`POST .../reject`
- **新增** 审校记录表（状态机 pending → approved/rejected，含操作者/时间），落库与驳回全量写 `OperationLog`
- **新增** 前端 `/admin/kb` 页面：状态卡 + 审校队列 + diff 视图 + 批准/驳回
- **变更** 批准语义：界面批准为权威载体，`_reviews.json` 降级为导出产物（保留生成，不再作为批准记录）
- **非目标**：摄取触发与异步 job（kb-ingest-ui change，受嵌入模型环境前置门约束）；`kb_eval` 评测触发（永久 CLI）；直接改 chunk/向量（只改源，重建走摄取）

## Capabilities

### New Capabilities
- `kb-review`: KB 内容审校与批准落库——审校包结构化浏览、界面批准/驳回、落库与 CLI 同语义、全程审计

### Modified Capabilities
- 无（无既有内容类 spec）

## Impact

- **新增文件**: `app/api/kb_admin.py`, 审校记录表迁移, `frontend/src/pages/admin/AdminKbPage.tsx`
- **修改文件**: `app/middleware/auth.py`（`knowledge` 加 `update` + MENUS 加 `admin_kb`）, `app/main.py`, `app/services/kb_graph_import.py`（如为脚本形态则抽出可复用落库函数，CLI 行为不变）, `frontend/src/App.tsx`
- **测试**: 角色断言（admin 可批 / academic_admin·teacher 403）、**对拍测试**（同一审校包界面批准与 CLI 落库结果一致）、幂等（重复批准拒绝）、审计用例、vitest（diff 渲染/空态/批准确认流）
- **约束**: 本 change 不触发嵌入/chroma 写操作（删集合与重摄必须同进程——HANDOFF 已知坑 1/3；job 机制归 kb-ingest-ui）
