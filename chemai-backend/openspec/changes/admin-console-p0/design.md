# Design: admin-console-p0

## Context

RBAC 已在后端收严完成（`app/middleware/auth.py`：7 角色矩阵 + `PermissionChecker` deny-by-default + 守卫测试），前端已有能力清单驱动的菜单机制（后端 `MENUS`/`menus_for()` 下发 key → `frontend/src/App.tsx` 的 `MENU_REGISTRY`/`ROUTE_MENU` 映射路径），管理类 API 绝大多数已存在（user/school/log/teacher_application/class_api）。缺的只是：菜单登记、页面、按钮显隐、审计写入、一处矩阵外授权整改。

硬约束：
- `chemai.db` 被运行中的 uvicorn 锁定（HANDOFF 已知坑 10）→ 管理功能必须挂主 FastAPI 进程，禁止另起 admin 进程。
- 矩阵完整性断言（`tests/test_permission_matrix.py`）拦「没有端点消费的键」→ P0 只复用既有键，零新增。
- 前端对未知菜单 key 是「跳过 + console.warn 不崩」→ 发布顺序先后端后前端。

方案全貌与分期依据见 `docs/管理后台建设方案_20261104.md`。

## Goals / Non-Goals

**Goals:**
- admin/academic_admin 的全部日常管理动作有界面、可追溯（日志）
- 教师端不再存在「点击才 403」的按钮
- 权限语义 100% 走矩阵（消除函数体内第二套授权）
- 测试基线不降：权限矩阵 + 角色断言 + e2e 全绿

**Non-Goals:**
- 不做学生管理新页（StudentsPage 按钮显隐已覆盖，避免双份维护）
- 不做数据导入界面、break-glass 工作台、KB 治理（后续 change）
- 不动学生/家长移动端
- 不做 RBAC 编辑器（矩阵留在代码里，测试钉住）

## Decisions

**D1 — 复用教师 SPA 壳 + 菜单机制，不做独立 admin 应用。**
备选：qiuqiu 式独立 react-admin + 单 token 进程。否决理由：chemai.db 文件锁（坑 10）使独立进程写库不可行；已有 JWT + 7 角色矩阵，平行体系必然造成两处权限逻辑。成本对比：本路径后端仅 1 个新端点。

**D2 — 概览页用推导式只读数据，唯一新端点 `GET /api/admin/overview`。**
备选：前端并发调既有端点拼装。否决：6+ 次请求且角色裁剪逻辑散落前端；一个聚合端点 + 服务端按 `can()` 裁剪字段更可控（academic_admin 不该看到教师待办字段，就不能下发）。端点门 `student:create`——**不能**用 `school:read`：矩阵里 teacher/subject_lead 也有 `school:read`（治理域全体只读），用它会把概览页漏给全体教师（菜单测试实测抓到）；`student:create` 恰好只被 admin/academic_admin 持有。内部按 `can("teacher","create")` 决定是否返回教师待审数。super_admin 未 break-glass 时返回明确空态标记（数据范围为空集是 ADR-0011 设计行为，端点用独立的 `data_scope_empty` 字段表达，前端渲染提示而非空计数）。

**D3 — teacher_application 整改采用阶段 7.1 的 log.py 同款做法**：门改 `PermissionChecker("teacher","create")`，删函数体 `role != "admin"`。矩阵重新成为唯一授权真源；`test_permission_roles.py` 补 academic_admin 403 断言钉住。

**D4 — 审计直写 `OperationLog`，不走 HTTP `POST /api/logs`。**
照 `app/services/data_import.py:33` 模式在 service/api 层直写。备选：调用端点——否决，HTTP 自调用的鉴权语义混乱且该端点注释明确「内部调用」。落点：`/teacher/approve`、`/student/approve`、`/student/{id}/transfer`、`/student/{id}/reset-password`、`DELETE /student/{id}`、`/teacher/{id}/assign-classes` 六处，成功路径提交后写（失败不记）。

**D5 — 按钮显隐用前端既有 `can(permission)`（`lib/api.ts:163`），映射 `资源:动作`。**
不引新库、不做按钮组件抽象；逐按钮加条件。resource/action 口径以 `test_permission_roles.py` 决策表为准（重置密码 = `student:update`）。

**D6 — 管理页面放 `frontend/src/pages/admin/`，组件映射逐项对齐 DESIGN.md v2 与教师端分册（`design/教师端.md`）**，禁止发明新参数：

| 需求 | 用什么 | 依据 |
|---|---|---|
| 数据表格 | antd `Table as AntTable` + TableColumnsType（StudentsPage/ClassPage/IntegrationPage 同款先例；非自研 ui/Table） | 既有页面惯例 |
| KPI 卡 | `ui/StatCard`（tone/`kpi-number`，mono + tabular-nums 内置） | R-T-L3 / P-06 |
| 空态 | `ui/EmptyState`（烧杯图标 + 行动引导，NEVER 只写「暂无数据」） | R-S2 / P-11 |
| 页面三态 | 每页加载（`ui/Skeleton`）/空态/错误（描述+重试）全覆盖 | R-S1 |
| 审批/删除/重置等异步动作 | `useAsyncAction` + `ActionButton`（idle→busy→done/error），NEVER 页内手写状态机 | R-I6 |
| 审批类确认 | 确认卡片（R-I2）；不可逆方向（批准/删除）行内两段式微确认 | R-I5 |
| Toast | `useToast`（3s 自动消失） | R-I3 / P-09 |
| 弹窗 | `ui/Modal`（16px 圆角档） | P-08 |
| 图表 | `ui/EChart`（ClassPage/DiagnosisPage 同款） | P 系组件纪律 |
| 色彩 | 浅色工作台，主操作 Oxford Blue 实色；Teal 仅 AI 声音，NEVER 用于系统命令控件；只用分册色板 | R-C7 / R-C2 / R-I7 |
| 令牌纪律 | 容器走 `ui/Card`/语义类；`npm run lint:tokens` 过 CI | R-K1 / R-K2 |
| 窄屏 | 表格页遵守 <1100 次要列收进展开行、<860 关键列三档折叠 | R-T-L4 |

登录跳转：LoginPage 登录成功后读存储的菜单列表（`lib/api` 共享 `login()` 已落能力清单），含 `admin_overview` → `/admin`。

**D7 — 四个管理菜单的 gate 键选型**（写进 MENUS 注释，防后人误改）：
`admin_users` 用 `class:create` 而非 `teacher:create`——页面同时含教师管理（Tab 内 `can("teacher:create")` 二次显隐）与班级管理（academic_admin 可用）；若 gate 在 `teacher:create`，academic_admin 将看不到班级管理。

**D8 — 新菜单的元素符号 tile（R-T-L2 侧栏惯例）**。规则：元素符号风格的 1-2 字母短码，不与现用重复（已占用：H/Pd/Au/O/C/Fe/Cu/Ag/Zn/Km）。提案（语义贴切优先，实施时可换，落分册登记）：

| key | 符号 | 取意 |
|---|---|---|
| admin_overview | `Pt` | 铂——全景仪表 |
| admin_users | `Mn` | 锰——人员管理 |
| admin_school | `Pb` | 铅——校舍基座 |
| admin_logs | `Rn` | 氡——痕迹留存 |
| admin_kb | `B` | 硼——知识基座 |
| break_glass | `Fr` | 钫——最不稳定元素，破窗隐喻 |
| admin_settings | `Tc` | 锝——技术调校 |

## Risks / Trade-offs

- [前端比后端先上，老前端收到新菜单 key] → 未知 key 已是 warn 不崩；仍按「先后端后前端」发布。
- [overview 聚合端点成为权限盲区] → 门 `student:create` + 字段级 `can()` 裁剪 + `test_permission_roles.py` 断言 academic_admin 无教师待审字段。
- [审计遗漏未来新增的敏感动作] → tasks 里加一条备忘：新增管理写端点时对照六动作清单检查审计（写入 MENUS 附近的整改注释区）。
- [academic_admin 看到班级管理但删班级是破坏性操作] → 矩阵本就给 academic_admin `class:delete`（设计文档「教务可管班」语义），界面照矩阵，不另设限制。
- [super_admin 场景长期无工作台] → P0 仅做空态提示，break-glass 工作台在 P2；日常运营走 admin 账号。

## Migration Plan

1. 合并顺序：后端（MENUS + overview 端点 + 整改 + 审计）→ 测试绿 → 前端（菜单注册 + 4 页 + 按钮显隐 + 登录跳转）。
2. 回滚：前端功能纯增量，回滚前端即可恢复原状；后端 overview 端点与审计写入无 schema 变更，直接 revert 安全（无迁移）。
3. 上线后跑 `_e2e_verify.admin.py`（admin 四页可达 + teacher 无管理菜单 + 按钮显隐）。

## Open Questions

- 无（超管空态文案、审计 detail 字段结构属实现细节，实施时定）。
