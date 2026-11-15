# ADR-0021: 确认门决定的一次提交主权归最外层调用方的工作单元

**Date**: 2026-03-25
**Status**: Accepted（计划 `docs/designs/gate-product-closure.md` 前置一 / T3；
R7 D8=A「收编五条 + 缺单元即抛」、R1 D2=A「算与写分开」、R8 D9=A「关不掉的决定不算生效」）

## Context

确认门的一次决定要改好几张表：门状态（`agent_gates`）、草稿行的审批位
（`learning_plan_drafts` / `parent_report_drafts`）、发送效果（学生收件箱、历史、
`students.current_plan`）。改造前是"谁写库谁自己 `commit()`"：service 的每个写方法
末尾各提交一次，一次批准实际产生 2–3 个事务。

这个形状有两个实测代价：

1. **半截状态可持久**。中间任何一步失败，前面已提交的部分回不去。最坏的一条：
   批准写进去了、门没关上 —— 老师回头看见同一张卡，再点一次，`delete_bank`
   这类动作就执行第二遍（R8 的靶子）。
2. **读路径能提交别人的写**。`get_open_gate` 曾"顺手"改库并 `commit()`，而
   `check_binding` / `replay_target` 都经过它；于是一次纯查询把活动事务中途提交掉
   （T4 摘掉的那两处写，就是这条的前置）。

 SQLite 侧还有一个硬约束：WAL 模式同一时刻只允许一个写事务，而本仓引擎的
 `connect_args` 只有 `check_same_thread`（`app/models/database.py:612`），没设
 `busy_timeout`，python sqlite3 默认 5 秒。把一次数秒级的模型往返圈进写事务，等于
 让全库写者排队到 `database is locked`。计划里"打回"路径正好长这样：
 `record_decision(reject)` 传递进 `revise_draft`，后者在写库前调模型。

## Decision

新增 `app/services/work_unit.py`，把提交权上收：**service 写入口只 `flush()`，
唯一那次 `commit()` 发生在最外层调用方的工作单元出口。**

三条规则都由运行时断言承载，不靠点名调用方：

1. **写入口只认活动单元**（`require_unit(db)`）。检测不到单元 → 抛 `NoWorkUnit`，
   绝不"没人管就自己提交"。同时校验会话身份：拿另一条会话来写 = 写进一个没人提交
   的地方，那种丢写没有任何症状，所以是硬失败。还校验**事务身份**（eng 三轮 D3 补）：
   单元第一次进写入口时记下当时的活动事务，之后再进写入口时若已经不是同一个对象
   （中途被 `commit()` 或 `rollback()` 带走、随后自动开了新的一笔），抛
   `WorkUnitCommittedEarly`。这一条把下面「适用范围」里那句"交给它们同一条会话时会
   中途提交"从已知风险变成当场失败 —— 只靠通路级 `commit 次数 = 1` 断言覆盖不到
   没被测到的新通路。
   收编的写入口 = `gate_service.open_gate / close_gate / close_conversation /
   supersede_stale_gates` + 两个草稿 service 各自的 `create_draft / revise_draft /
   send_draft / record_decision`。读入口（`get_open_gate`、`check_binding`、
   `replay_target`、`pending_gates`、`get_draft`、`get_active_draft`）不要求单元。
2. **单元内禁止模型调用**。判定本体在模型出口那条缝上
   （`llm_transport.refuse_model_call_in_work_unit`，挂在 `_CassetteEngine.plan` 入口）：
   record/replay 下五处构造都必经那一点。live 模式下缝不介入（transport 是 None，且
   `ChatOpenAI` 用 langchain 自己缓存的客户端），所以三个业务入口各自再判一次当早失败：
   `LLMService._call_with_retry`（`generate_text`、`ocr_image`、
   `analyze_paper_with_vision` 都汇到这一点）、`agent/provider/deepseek.py` 的
   `chat` / `chat_stream`（eng 三轮 F3/D5 补）、`exam_bank._seam_http_client`
   （出题联网检索那三处的共同入口，ADR-0023 决策 16）。
   **已知残余**：live 模式下的 agent 回路（`get_langchain_model` 那条）没有 transport 钩子，
   只有它经由的 `LLMService`/provider 出口被护住 —— 把"所有真实网络往返"说成全覆盖是假的。
   向量嵌入（`app/services/vector_search.py`）不在决定链上，未挂断言。
   因此需要模型输出的写入必须拆两半：
   `compute_draft` / `compute_revision` 在单元外算，`create_draft` / `revise_draft`
   在单元内只写行。打回通路把算好的结果经 `record_decision(computed=...)` 带进单元。
3. **单元不许嵌套、异常整单回滚**。嵌套抛 `WorkUnitAlreadyOpen` —— 内层提交会把
   外层的半成品落库。
4. **批准的执行许可 = 占到这扇门**（R8 D9=A 的落地形态）。`GateService.claim_gate(db,
   gate_id)` 是一条条件更新 `WHERE gate_id=? AND status='open'` + 影响行数判定，
   在**执行不可逆动作之前**单独提交一次。占不到门就不执行，回"这次确认已经处理过了"
   并重发当前门，同时留一条 `close_miss` 结构化日志。
   `close_gate`（返回有没有关上）只留给"放弃"这类不落工具动作。

   这条与 R8 原文有一处刻意的偏离：原文写"关不到 ⇒ 整单回滚"，但布尔门的动作在关门前
   就已经跑完了，回滚救不回一个已经执行的 shell 命令。把关门从"执行后"挪到"执行前"
   并做成条件更新，才是那个语义想防的事：两次批准都读到 open → 破坏性动作跑两遍。
   代价是"批准生效但工具失败"时门已是 executed，老师的重试路径是重跑工具（guard 会
   以同一 `gate_id` 重开门并复位成 open），不是原地再点一次批准。

状态载体用 `ContextVar`（与 ADR-0020 同一套理由）：`asyncio.Task` 创建时复制上下文，
所以单元往下（工具子任务）看得见，往回看不见 —— 单元必须开在最外层，子任务里再开
一个会撞上第 3 条而不是悄悄变成两个事务。

五条通路的开单元点：`agent/channel/langgraph_channel.py` 决定流、
`app/api/diagnosis.py` 决定与生成端点、`agent/guard.py` 门实体登记、
`agent/tools/diagnosis.py` 工具通路、`agent/channel/conversation.py` 会话删除。
工具通路的 `next(get_db())` 裸会话同时收编进 `session_scope()`，退出即关。
会话删除路径原先包着 `try/except Exception: pass`，一并去掉：门没关上、会话照删，
正是"点不进去的幽灵门"的来源，与 fail-loud 直接冲突。

## Consequences

- 一次决定一次提交成为可断言的契约：`tests/test_gate_unit_of_work.py` 对写入口名单
  逐条参数化"单元外必抛"，数 `Session.commit` 真实次数验证通路级"各开一个单元"，
  另有两条钉住事务身份绊线 —— 中途被提交要抛、只 `flush()` 不许误报。
- 漏包一个新的调用点会当场 500 / 工具错误文案，而不是静默丢写。这是刻意的取舍：
  报错可见，丢写不可见。
- `record_decision` 的打回分支多了 `computed=` 必填项，调用方必须先算。多出来的
  一次函数调用换掉一类"数秒持写锁"的偶发故障。
- **适用范围只有确认门这条链**。`plan_run_service`（计划态推进）、通知投递、
  题库与考试等模块的 service 仍然各自 `commit()`，它们不在本 ADR 的约束面内；
  把活动单元交给它们同一条会话时会中途提交 —— 现在会被第 1 条的事务身份绊线
  当场抛出来，而不是留下两笔提交。扩面（让这些 service 也接受外部单元）是独立一轮，
  不在本期。
- 门实体登记失败（`guard.register_gate_entity` 的宽 except）仍按 B1 契约撤回拦截、
  不发幽灵卡，与本 ADR 无冲突：那条路径的写入本来就该整单不存在。
