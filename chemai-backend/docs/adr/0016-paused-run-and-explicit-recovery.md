# ADR-0016: Paused Run 与显式恢复契约——恢复永不自动

**Date**: 2026-03-20
**Status**: Accepted（grilling 定案；架构评审报告 architecture-review-20260920）

## Context

「打断后智能体自动尝试和恢复都有问题」的排查结论（2026-03-20）：恢复的实现方式是让 agent
再跑一遍——

- 清中断 = 偷偷 `ainvoke(Command(resume="cancelled"))` 跑一个完整 agent turn（修复前的实现方式，
  该分支已删除）：事件全丢、副作用照跑；若该 turn 再中断，后续流挂死至 180s 超时。
- GuardState 在进程内存：重启后点批准只改 DB、重放无物，草稿搁浅在 approved-never-sent。
- checkpoint 任务带 error 即永久毒化线程（生产日志 chemai_agent_errors.log 的 OpenAI 400
  「insufficient tool messages」已实证），只能「请新对话」。
- 无会话锁：双击批准 / 双标签页并发两条流，在同一 thread 上交错写 checkpoint。
- 客户端断开即静默取消，前端把无 [DONE] 的流当正常收尾——「看不见的取消」。

领域语言里「等待老师决定」（Confirmation Gate）与「被系统挂起」（Paused Run）此前共用一个
词汇（`phase:awaiting_approval`），两个状态、两个出口被压成一个。

## Decision

1. **Paused Run 是一等状态**（CONTEXT.md 已立词条）：客户端断开、服务重启、回合崩溃、
   残留中断 → run 标 paused，步骤状态与开着的门原样保留。gated（等待人工）与 paused
   （系统挂起）是不同状态、不同出口。
2. **恢复永不自动**：图级不重跑（重跑 = 双发副作用）；重试只存在于模型调用层
   （ChatOpenAI max_retries，维持）。恢复的唯一路径是老师的显式动作：决定一个门、发下一条
   消息、或刷新后经 /history 重同步再操作。
3. **断线即取消 + 立刻 paused**：取消点维持现状（不 shield 工具协程），但 run 立即标
   paused、执行到哪步如实入库；绝不自动重试。
4. **毒化自动迁移**：checkpoint 任务带 error → 自动开新 thread，messages 历史 + 活跃
   run/gate 实体迁过去，旧 thread 作废，老师无感。
5. **每会话一把 asyncio 锁**：/stream 与 /resume 共用；争用立即 409「已有进行中的回复」，
   不排队。前端流式期间禁用确认按钮与输入（handleDecision 补 streaming 检查）。

## Consequences

- Positive：「看不见的取消/重试」消失，恢复行为可预测（永远显式）。
- Positive：断线 / 重启 / 毒化三类故障共用一个恢复契约（paused + /history 重同步）。
- Negative：断线后当前工具可能执行到一半即停，副作用不回滚——与 At-least-once Delivery
  契约一致，靠幂等键兜底。
- Negative：自动迁移逻辑本身是新风险面，需迁移后校验（run/gate 计数一致）。

## Alternatives Considered

- **后台跑完当前 turn**：老师回来看到结果就绪，体验顺；但「看不见的副作用在跑」与确认门
  理念相悖，还需后台任务生命周期管理。否决。
- **锁争用排队等待**：免改前端防重，但第二次点击延迟生效，感知成「幽灵双发」。否决。
- **shield 当前工具协程跑完再停**：半途而废最少，但多一层 asyncio 复杂度，收益有限。否决。

## 实施后收尾：重建路径自身的四处失真

本 ADR 把「重启 / 断线后靠实体重建」定为契约。全量审查随后发现，重建这条路径本身还有四处
让老师看到失真画面或埋下资源隐患，全部当次修完——它们不改决策，只把「重建」二字做实。

- **计划态的真源是库里的 PlanRun，恢复时两处一起写**。旧实现从进程内 `guard_state.plan_steps`
  取步骤，重启后那里是空的：门照样批准、DB 里的 run 照样推进，唯独计划卡补发不出
  `plan_progress`，老师看到的是流程走完而步骤条冻在原地。改为 `DB PlanRun > 内存` 的优先级
  （`_restore_plan_state`）。恢复函数必须同时写 adapter 与 `guard_state` 两处——计划发射器
  （`_plan_completion_events` / `_decision_unlock_events` / `_gate_step_events`）读的是
  `guard_state.plan_steps`，而 adapter→guard 的回写只存在于 `/stream` 的生成循环，`/resume`
  路径没有。回归测试：`tests/test_plan_restore_from_run.py`，其中一条在恢复之后直接调用批准
  路径的发射器、断言仍能发出带真实步骤号的 `plan_progress`，专门防止「只写一处也能过」。
- **plan_summary 如实带状态，不再硬写 pending**。首版把每一步的 `status` 写死成
  `"pending"`，于是已完成的第一步在界面上永远转圈——与本 ADR「执行到哪步如实入库」是同一条
  要求的前端半边。改为读步骤自己的状态、缺字段才回落 pending。断言并入
  `tests/test_plan_step_advance_guard.py`。
- **实体读写统一走 `_entity_db()`，退出关的是 Session 不是生成器**。端点内的会话由
  `Depends(get_db)` 管，端点之外（本 ADR 的重建路径全在这里）只能 `next(get_db())` 自取。
  写 `db = next(get_db())` 而不关，要等 CPython 引用计数回收生成器才走到 `get_db` 的
  `finally`——那是运行时实现细节，不是契约，而这些读写挂在每条 SSE 流的高频路径上。
  统一收进 `agent/channel/langgraph_channel._entity_db()`，两个刻意的细节：
  `get_db` 每次调用时从模块上取（换测试库靠的就是替换 `app.models.database.get_db`
  这个属性，用文件顶 import 进来的名字会绕过它）；退出时关**会话**而不是关生成器——
  `gen.close()` 会向 `get_db` 抛 `GeneratorExit`，块内一旦提前 `return`/`raise`，收尾时
  冒出「generator ignored GeneratorExit」，顶掉真正的业务异常。两个上传端点自带的
  `try/finally` 会话不在此列。
- **重建路径的静默失败改为留痕**。读活跃 run、把步骤挂上 adapter、run 落库、断线时标 paused、
  `/stream` 里的步骤回写这五处原先全是裸 `except: pass`，把「重建失效」吞成「这个会话没有
  计划」。同一份 B1 纪律（门实体失败不静默）适用于计划实体：改为 `logger.warning` 带上
  conversation_id 与原因，降级行为不变（读不到就回退内存、挂不上就不挂），但失效发生在有人能
  看见的地方。这条纪律此前零断言——把它改回 `pass` 不会有任何测试变红，故配套
  `tests/test_failure_leaves_a_trace.py` 逐处钉住（既断言留痕，也断言降级行为不变）。
