# Design: kb-review-ui

## Context

`admin-console-p0` 已交付管理壳（菜单机制 / `/admin/logs` 审计消费 / 页面目录）。KB 侧现状：审校包是 `content/kb/` 下的结构化文件（提议边 + 词典 diff），落库入口是 `python -m app.services.kb_graph_import`，批准语义在会话里、记录在 `_reviews.json`。结构库为 SQLite，摄取（chroma）与本 change 无关。

## Goals / Non-Goals

**Goals:**
- 批准/驳回界面化、状态机化、可审计
- 落库与 CLI **同一服务函数**，语义零漂移
- 状态总览为推导式只读（读库 + 读产物，不写）

**Non-Goals:**
- 不做摄取触发/异步 job（kb-ingest-ui）
- 不做 chunk/向量直接编辑
- 不做评测触发（kb_eval 永久 CLI，涉及 LLM/嵌入成本）
- 不迁移历史 `_reviews.json` 为记录（旧包按需重新走界面批准）

## Decisions

**D1 — 落库复用 `kb_graph_import` 服务函数。** 该模块目前是 `python -m` 脚本形态；若落库逻辑内联在 `main()`，先抽函数（CLI 入口行为不变，原有调用方/文档不破坏）。备选：界面层重写落库——否决，双实现必然漂移，对拍测试也无法成立。

**D2 — 审校包状态进 DB，不进文件。** 新表 `kb_review_records`（package_id、来源册/路径、status、reviewer、reason、timestamps）。文件在 git 里、状态在库里——文件保持纯语料。迁移走 `app/utils/migrate.py` 增量函数（项目现状双轨惯例，跟随现状，不引 alembic 新用法）。

**D3 — 审校包解析器复用 `kb_graph_import` 现有读取函数。** 六册审校包格式演进过（多册词典 `load_all_dictionaries` 等），解析不另起炉灶；解析失败返回结构化错误而非 500。

**D4 — 权限落点。** `ROLE_PERMISSIONS['admin']['knowledge'] = ['read', 'update']`（super_admin 经镜像继承，与既有镜像机制一致，不必单独声明）；MENUS 加 `{"key": "admin_kb", "resource": "knowledge", "action": "update"}`。菜单 gate 用 `update` 而非 `read`：`knowledge:read` 是全体内容角色的既有读权，用它会让所有教师看到 KB 管理入口。

**D5 — 批准是同步事务。** 审校包规模为图谱边/词典条目级（几十~几百条），同步落库秒级完成，不引入 job 机制（job 归 kb-ingest-ui，那里才有真正的长任务）。

**D6 — 状态端点只读推导。** 摄取状态读 chroma 元数据（主进程内只读访问安全）、chunk 计数读库、词典覆盖读 `load_all_dictionaries`、阈值水位读最近评测产物文件；全部无副作用。

## Risks / Trade-offs

- [审校包文件格式历史不一致（M1 source_path 前缀等已知坑）] → 解析层对齐 `kb_graph_import` 既有读取函数，异常显式报结构化错误；对拍测试覆盖至少一个历史包。
- [界面批准后语料文件与库状态脱节（文件被改但已 approved）] → approve 时记录包内容指纹（哈希），再次查看时校验并在界面提示「文件已变化，请重新评估」。
- [`knowledge:update` 键无端点消费会被完整性断言拦] → 键与 approve/reject 端点同 PR 落地（tasks 顺序保证）。
- [多管理员并发批准同一包] → 状态机更新走单事务条件更新（pending → approved），失败方收到「已被处理」。

## Migration Plan

1. 后端：迁移（新表）→ 权限键 + 端点 → 测试绿 → 前端。
2. 回滚：revert 即可；新表留存无害（无外部消费），或增量函数逆操作删表。
3. 语义迁移：HANDOFF 记录「界面批准为权威载体、`_reviews.json` 为导出产物」。

## Open Questions

- 无。
