# HANDOFF — gate-lifecycle（确认门与计划步骤的一致性）

> 交接时间：2026-10-17（本节及上一版把日期写成 10-17，机器实际本地日期是 **2026-10-16**，
> 证据时间戳都在 10-16；不逐条回改，读的时候按「同一轮」理解即可）。
> 计划与逐条证据在 **`tasks.md`**（那份是主文档，本文件只是入口）。
> 本次改动**一行都没提交**。

---

## 一句话现状

**端到端跑通了 —— 老师那句原话 6 步全走完，四扇门全部收场。**
`evals/e2e_long_chain.py` = **绿**（这是本变更的锚，锚绿了）。

最后那一环**不在网关、不在门、也不在模型** ——
**是老师点完「批准」之后，服务端把 persona 从 `teacher` 静默降级成了 `tutor`。**

---

## 本轮的结论：2.11（**别读代码，先取证据** 这条纪律是对的）

上一版在这里留了「下一件事：给工具清单加日志，跑一次端到端」。照做之后，
答案一次到手，**而且和上一版的三次推断全不一样**：

| 上一版说过 | 实际 |
|---|---|
| 关键词路由把工具裁掉了 | 关键词只**加**不裁 —— 而且**整条线是空的**：`intent_hints` 是 `create_chemai_agent` 从不读的参数，`pre_routed_agent` / `suggested_tools` 写进图状态后**全仓库无人消费**（v1 残留） |
| 是模型自己被 `error` 键名带偏（2.11b） | **模型两轮都没说谎。** 第 2 轮那句「这两个技能在环境里不存在」是**如实汇报** |
| persona 白名单把工具裁掉了 | 白名单没问题；**是 persona 本身被换了** |

**真正的机制**（服务端日志里两行，`persona=` 与工具名都在）：

| | persona | domain 工具数 | 有那两件吗 |
|---|---|---|---|
| 轮 1 `/stream` | `teacher` | 46 | ✅ |
| 轮 2 `/resume`（批准第一扇门之后） | **`tutor`** | **21** | ❌ |

- 老师的真实点击走前端 `postStream()`，它把 persona 默认成 `'teacher'` 并**总是**发出来
  （`agent-sse.ts:252`）。
- 服务端 `ChatRequest.persona` 的默认值却是 `"tutor"`，而对 teacher 角色 `"tutor"`
  是**合法** persona（`_PERSONAS_BY_ROLE["teacher"] = ("teacher", "tutor")`）——
  于是 `_resolve_persona` 里 `requested or allowed[0]` 那行的兜底**永远轮不到**。
  任何漏发 persona 的调用方都被静默降级。
- **修法**：`ChatRequest.persona: Optional[str] = None`。`_resolve_persona` 一行没动
  —— 它本来就是对的，是被上游默认值挡住的。

**为什么上一轮看不见**：agent 层的 `logger.info` **一条都没打出来过** —— 全仓库没有
一处 `logging.basicConfig`，root 停在默认 WARNING。这正是「只能读代码推、连错三次」的
根因。`app/main.py` 现在有一行 env 门控的 basicConfig（默认 WARNING = 行为不变，
排查时 `CHEMAI_LOG_LEVEL=INFO`）；端到端脚本把服务端日志落
`evals/_last_e2e_server.log`，并在结尾把每轮的工具清单抄出来。

---

## 顺带修掉的第二个真缺陷：2.11b（验收脚本查错了表）

persona 修好后，链路 6 步全 `completed`，但 A5「第 5 步没生效」**照样红**。

`assignment_service.assign` 按人头分两条路：`<= SYNC_LIMIT(5)` 走**同步路**，
只写 `exam_records`（`record_id = "asg_<摘要>"`），**根本不动 `assignment_batches`**
—— 那张表是 **>5 人异步批次**的进度源。原断言拿异步批次的表去验同步路的活，
做成了也永远查不到。**它把「第 5 步真没做」的观感钉在验收器里**，
和 2.11 的真 bug 混成一条线索。

已改查 `exam_records`（+ 30 分钟新鲜度，已对着真库验过不会误判存量）。

---

## 已完成

| 批次 | 状态 |
|---|---|
| 0 准备 | ✅ 3/3 |
| 1 计划步骤与门的一致性 | ✅ 8/8 |
| 2 门的生命周期 | ✅ 10/10（2.11 / 2.11b 结案） |

**七个断点全修完**：① 门绑错步（1.4）② 并发双 running（1.6）③ 孤儿门（2.2）
④ 门关不上（2.④）⑤ 伪造等待（1.7）⑥ 两个真源漂开（1.8）
⑦ **批准后 persona 降级致工具消失（2.11）**。

---

## 未完成（下一件事）

| 项 | 内容 | 备注 |
|---|---|---|
| 2.4（收尾） | ~~删 `guard_state.pending_approval` 字段本身~~ **✅ 已完成** | 全仓库归零。过程中撞出**两个真回归**：① 并发双门齐开（`acquire_gate` 的锁只包住检查，内存写入本是"预订"）→ 把登记搬进锁里；② 布尔门「打字=作废」变空动作 → 新增 `_void_open_gate`。详见 `tasks.md` 2.4 |
| 2.9 | ~~续接提示不提计划~~ **✅ 已结案（诊断错了，没改代码）** | 取证结果：四次绿运行、每次续接后模型都在继续计划（「继续第 5 步」「继续第 6 步」）。它偏离计划是 2.11（手里没工具）造成的，**不是提示叫它收尾**。症状，不是病因 |
| 批次 3 | ~~`scripts/repair_gates.py` + T17 每日作业~~ **✅ 已完成** | ⚠️ 原文把库里那批 open 门当存量垃圾 —— **实测不是**：74 扇里 **33 扇是假门**（草稿已 sent / 不存在）、**41 扇算数的是真实待决定项**（全是 t- 老师会话、最近 1–3 天产生）。脚本只收前者，后者**刻意不碰**（等于替老师扔卡片）。详见 `tasks.md` 批次 3 |
| 批次 4 | ~~`CONTEXT.md` 词条 + ADR~~ **✅ 已完成** | 词条补了「门是行不是内存 / 门记着属于哪一步 / 三态含 in_doubt / 全拦跨重启也成立」；新增 `docs/adr/0030-gate-replay-payload-has-one-source.md` |

**没有一条再挡着端到端。** 剩下的都是存量收敛与文档。

✅ **已完成（本会话）**：批次 3（存量对账 + T17）/ 批次 4（文档 + ADR-0030）/ 2.11 / 2.11b / 2.6
/ 2.7（含第 2 步查询与端点、第 4 步前端卡）
/ 2.4 的两处「以实体为准」（**顺带补掉一个真安全缺口**：C1-Q3「门开着就全拦」
重启后整段失效 —— 判据只有内存，而内存随进程消失，卡片照显示、闸却没了）。
详见 `tasks.md` 各条目。

---

## 三道闸的实测

| 闸 | 结果 |
|---|---|
| ① `pytest tests/ -q` | ✅ **1685 passed, 5 skipped** |
| ② `run_evals.py --fast` | ⚠️ 138 条 / 137 通过 / 1 失败，GATE BLOCKED |
| ③ `evals/e2e_long_chain.py` | ✅ **绿 —— 长链路端到端跑通** |
| ④ 前端 `tsc --noEmit` / `vitest run` | ✅ 干净 / **436 passed**（53 文件） |

**③ 每次动到门/重放这条路之后都重跑过**（它照得见单元测试照不到的东西：
迁移有没有挂 startup —— 2.6 第一次就是因为只挂了 CLI 路径而红，见 `tasks.md` 2.6 的
"实地踩的坑"；2.4 改完之后也重跑了一次）。

**锚的两个随机红源已修（2026-10-17）**：

- **计划形状**：`generate_X` 与 `send_X` 两步并存时发送步没人收尾。真凶不是"1.5 只绑生成步"，
  而是"**第二次完成**"的过滤器太窄（只认 `running`/`awaiting`，8 步形状里那格还是 `pending`）。
  规则已搬进 `compute_replay_transition`：一次批准收**两步**（生成步 + 重放真跑的那个工具自己的步）。
  ⚠️ **我第一次修反了**（整个删掉那次写入），端到端当场打回 —— 别再把"第二次完成"当多余。
- **`assign_adaptive_practice` 报"成功的空操作"**：组不到题时不建记录、返回里却没有 `error`，
  于是门被消费成 `executed`、计划步标 completed，而老师收到「已为 0/1 名学生布置练习
  （**题目已落库**）」。现在 `done == 0` 返回带 `error` 的载荷 ⇒ 门退回 `open` 老师可重试。

两条的完整经过见 `tasks.md` 的 2.4 条目末尾。

**② 那条红的归属**：`[FAIL] [L1] ui_v23 → text-[11px] 不回潮`。全仓库只有两处
`text-[11px]`，都在 `frontend/src/pages/mobile/student/ProfilePage.tsx`（260、298）——
**那是本会话开始前就在工作区里的未提交改动**，不属于本变更。

**② 的警告已定归属**：`tool 'send_class_notification' missing from L0 coverage manifest`
—— 该工具定义在 `agent/tools/notification.py`，而该文件**尚未纳入 git**
（并行工作的新增工具）。收尾动作在那边：登记进 `evals/tool_coverage.py`。

---

## 陷阱清单（每条都是实打实踩过的）

**方法论三条（最贵的）**

- **测试里改模块全局 / 模块级 store，一定要能还原。** 本会话踩了两次，形状不同根因同一个：
  ① `tests/test_gate_blocks_after_restart.py` 里 `_db_mod.get_db = lambda: ...` 直接赋值
  —— 后面**5 个无关的测试文件**当场全红（`test_notification_dispatch` 等），单跑又全绿；
  ② 前端 `use-in-doubt` 是模块级 store，头一条用例把它标成 `loaded`，后面的用例再也不去拉。
  判据：**改了某个全局还没还原，就先当作"后面凡是碰它的用例都不可信"**。
  Python 用 `monkeypatch`，前端用 `beforeEach` 里那个 `__reset*ForTests()`。

- **看不见就别推，先把日志打开。** 上一轮在 2.11 上连错三次，根因不是推理能力，
  是 agent 层的 `logger.info` 因为 root 停在 WARNING **从来没输出过**。
  现在 `CHEMAI_LOG_LEVEL=INFO` 就能看见；端到端脚本还会把工具清单抄出来。
- **验收脚本必须和真实客户端发一样的东西。** 脚本的 `/resume` 漏了 `persona`，
  而真实前端 `postStream()` 默认 `'teacher'` 并总是发 —— 脚本「驱动老师真实点击路径」
  这句话因此不成立，整条链路的失败被记在了错误的账上。

**机制四条**

- **网关的工具清单对模型手里那份毫无影响**：`intent_hints` 是死参数，
  `pre_routed_agent` / `suggested_tools` 无人消费。别再顺着路由器查工具。
- **`persona` 是权限不是展示**：它在 `/resume` 上会重新解析一遍，链路中途换 persona
  ＝ 中途换工具白名单。改任何「多轮共享一个 agent」的逻辑都要盯住它。
- **同步路 vs 异步批次是两张表**：`<=5` 人只写 `exam_records`；
  `assignment_batches` 是 `>5` 人的进度源。断言前先看清走的是哪条路。
- **草稿门的 `gate_id` 由 `draft_id` 生成** —— 两个门共用一个草稿就是**同一行**
  （upsert 命中同一 `gate_id`）。写测试要两扇门，就得两个草稿。
- **给 `agent_gates` 加列要挂两处**：`app/utils/migrate.py` 的 `migrate()`（CLI）
  **和** `app/main.py` 的 startup —— 后者**不走** `migrate()`，是自己逐个 import 的。
  只挂 CLI 的代价（本会话实测）：影子库启动不跑 DDL → `open_gate` 报
  `no such column` → `register_gate_entity` 全线失败 → **一扇门都开不出来**，
  端到端从绿直接退化成"模型反复重试工具"。**单元测试照不到这个**（它们自己建 schema，
  列一定在），只有端到端能。

**契约一条**

- **`_direct_replay_events` 返回空是约定，不是"没东西"**：调用点靠它区分
  「没有待处理的确认请求」（`if not direct_events:`），而 `/resume` 那条路
  `if _direct:` **非空**就走"直接重放"分支并 return。把它撑成非空
  = 把**正常 resume** 拐进那条路（本会话实测：一句假的"已处理过"，还不跑续接）。
  改这个函数的返回值前，先看两个调用点怎么用它。

**GuardState 两条**

- **`check_limit` 在 `check_dedup` 之前跑** —— 只把 `forget_call` 补到门拦下的那条路上没用，
  配额那道关先撞上；两个痕迹必须一起撤（`undo_unexecuted_call`）。
- **重放前 `claim_gate` 已把门占成 `executing`** —— 事后用 `get_open_gate` 读不到它，
  得按 `gate_id` 读（`gate_service.gate_plan_step` 就是为此存在）。

**数据两条**

- **`pending_gate_criteria` 对「草稿已 sent」的门本来就不露出** —— 拿 `get_open_gate`
  当断言等于什么都没测。
- **别把 `agent_gates.plan_step` 回填** —— NULL 是「未记录」这个有效语义。
- **本机 `datetime.utcnow()` 粒度约 15.6ms**，同一 tick 内两次写入时间戳完全相同。

---

## 未提交改动（`git status` 对账用）

**本变更动过的**：

- 产品：`app/models/database.py` · `app/utils/migrate.py` · `app/main.py` ·
  `app/services/gate_service.py` · `app/services/plan_run_service.py` ·
  `app/services/plan_draft_service.py` · `agent/guard.py` ·
  `agent/langgraph_agent_v2.py` · `agent/channel/langgraph_channel.py` ·
  `agent/channel/gateway.py` · `agent/channel/conversation.py`
- 前端：`frontend/src/components/chat/MessageList.tsx` ·
  `frontend/src/components/chat/InlinePanels.tsx` ·
  `frontend/src/pages/teacher/PendingGatesPage.tsx` ·
  `frontend/src/pages/teacher/ChatPage.tsx` · 新增 `frontend/src/lib/use-in-doubt.ts`
- 作业/调度：`app/jobs/handlers.py` · `app/scheduler/__init__.py` ·
  新增 `app/scheduler/gate_expiry.py` · 新增 `scripts/repair_gates.py`
- 测试：`conftest.py` + `tests/` 下 —— **本会话新增 5 个**（`test_persona_resolution.py`、
  `test_gate_replay_payload_pin.py`、`test_in_doubt_gates_endpoint.py`、
  `test_gate_blocks_after_restart.py`、`test_gate_expiry_job.py`），
  另有上一轮的 2 个（`test_gate_step_binding.py`、`test_gate_orphan.py`）+
  既有 `frontend/tests/pending-gates-page.test.tsx` 里加的 5 条
  （该文件本身不是新增的）
- 验收：`evals/e2e_long_chain.py`
- 文档：`openspec/changes/gate-lifecycle/`（本目录）

**⚠️ 别把两个 gateway 看混（本轮真看混过一次）**：

| 文件 | 属于 | 内容 |
|---|---|---|
| `agent/channel/gateway.py` | **本变更**（会话开始时是干净的） | 只有 `ChatRequest.persona` 那 7 行 |
| `agent/gateway.py` | **并行工作**（会话开始前就是 M） | `_ALL_TOOL_DESCRIPTIONS` 补 22 条描述 + `send_class_notification` 关键词（注释署 2026-10-14） |

两边**没有混在同一个文件里**，按文件 stage 即可 —— 不需要 `git add -p`。
（`agent/langgraph_agent_v2.py` 只加了日志，不在并行那批里。）

**⚠️ 工作区里还有一批不属于本变更的未提交改动**：
`agent/personas/teacher.yaml` · `agent/tools/__init__.py` ·
`app/api/smart_practice.py` · `chemai.db` · `ToolCard.tsx` · `ProfilePage.tsx` ·
以及未跟踪的 `agent/tools/notification.py`。

**提交时只 stage 自己动过的文件**（那批是并行工作）。

**`chemai.db`**：`plan_step` 列还没落（迁移在服务启动时跑）。提交前先
`python scripts/db_checkpoint.py`。

**顺带一提（未动）**：`agent/channel/langgraph_channel.py:84` 的 `_agent_cache`
声明了但全仓库无人读；`create_chemai_agent` / `_get_or_create_agent` 的
`persona="tutor"` 默认值同样没人用到（三个调用点都显式传了）。同属「tutor 默认值」
这一族，这轮只改了会真正生效的那一处。
