# ADR-0015: 决策通道唯一化与门的数据强制统一——自然语言批准退役、布尔门实体化

**Date**: 2026-03-20
**Status**: Accepted（grilling 定案；架构评审报告 architecture-review-20260920）

## Context

架构评审（2026-03-20）沿 ADR-0013「两套门并存直至逐一实体化」的预告收尾时，钉出四类缺陷的
共同根因：Confirmation Gate 这一个领域概念在代码里是三个互不知情的实现，且批准语义存在两条
通道：

- **门覆盖与键错位**：门开着时 LLM 仍可继续调工具，第二个门直接覆盖
  `GuardState.pending_approval`（guard.py:284-289）；批准决定记在 `decision.draft_id` 上、
  重放却取 `pending_approval` 的 fn/kwargs（channel:907-924 vs 1154）——「批准发学生的卡片
  重放发家长的调用」由此而来。
- **自然语言批准**：/stream 上 ≤12 字含「发送」即判批准——「不要发送」= 批准并真发
  （guard.py:79、channel:313-336）；前端却把门开着时打字一律当打回，同一动作两种语义。
- **粘性 approved**：`guard_state.approved` 置 True 后永不复位，同轮后续破坏性工具全放行。
- **步进权错位**：SSE adapter 把 `approval_needed`（不含 error）当 success，门步骤被标
  completed、下一步立刻 running（langgraph_sse_v2.py:161/318/383-399）——「人工还没确认
  就往下推」。

## Decision

1. **决策通道唯一化**：批准只来自 /resume 的结构化三值决定（ADR-0013 协议）与「点击即
   决定」的面板/REST 入口（内部同样走决定原语，审计留痕）。/stream 上的自然语言确认词
   降级为引导（回复「请点击确认卡」，零副作用）；门开着时的其他打字一律 reject+feedback。
   ADR-0013 的「确认白名单→approve」向后兼容条款正式废止（同仓同部署，无旧客户端）。
2. **门统一数据强制**：布尔门（assign_adaptive_practice / delete_bank）实体化——
   `conversation_gates` 演进为通用 gate 实体（kind: draft|boolean，携带 tool+kwargs 或
   draft_id）。拦下→登记实体→返回等待；批准→按实体重放。draft 与 boolean 成为同一
   seam 后的两个 adapter。图内 `interrupt()` 与 `request_approval` 工具整体退役：门不再
   依赖图中断，孤儿中断以纯状态标记处理（不 ainvoke、不偷跑模型）。
3. **一决定一动作**：决定必带 gate_id，重放前强校验一致；不一致拒绝执行并重发当前门状态。
   粘性 `approved` 概念退役；out-of-workspace 文件提权与门分离，改为真·单次
   （一次调用后失效，与 `set_elevate_once` 的名字终于一致）。
4. **步进权归位**（ADR-0012 的应用）：Plan Run 步进状态机由 plan_run_service 独占；
   `awaiting_confirmation` 是绝对阻塞态——门开着，任何后续步骤不得标 running，GuardState
   机械拒绝 agent 的一切工具调用。「被门拦下（未批准）」与「批准后重放完成」由双驱动转移
   区分：`approval_needed`→awaiting，approve→running（重放中），重放成功→completed。

## Consequences

- Positive：学生/家长交叉发射、门覆盖、幽灵批准、「未确认就推进」四类缺陷在语义层根除，
  不再依赖提示词自觉。
- Positive：门的测试面收敛到一个 interface（open→decide→replay）＋两个 adapter；重启后
  布尔门与草稿门同等待遇（DB 可重建）。
- Negative：/stream 确认分支重写为引导文案后，依赖自然语言批准的旧脚本（如
  _e2e_plan_hitl.py 部分断言）需同步改造。
- Negative：`interrupt()` 退役后，LangGraph 线程上残留的旧中断状态需要一次性迁移处理。

## Alternatives Considered

- **收紧词表保留自动批准**：精确匹配可救「不要发送」，但「确认意图→直接执行副作用」的
  风险通道仍在。否决。
- **保留 interrupt() 只修清理**：LangGraph 取消中断必须 `Command(resume)` 恢复执行——
  「不跑模型的清理」在该机制下不可表达。否决。
- **布尔门用内容哈希当身份、不建实体**：轻量，但重启即失、/history 无法重建，seam 后
  只剩一个真 adapter。否决。

## 阶段 3 收尾

架构复审沿本 ADR「门由数据强制」的主线，又钉出五处把决定、门、权限当成可信输入处理的残留
口子，全部当次修完。它们不改变上面的决策，只是把决策落到每一行，故收进本 ADR 而不另立新篇。

- **决定通道 fail-closed**：三值决定只认 approve、reject、abandon 这一个枚举。拼错的、未来
  新增的、REST 客户端乱传的 action 一律判「非决定」，绝不原样透传到批准重放路径。并且当客户端
  确实发了决定对象却非法时，不再回退去读原始消息文本——否则一个畸形决定夹带「approved」字样
  就能复活成批准，触发幽灵发送。旧客户端只发协议字符串的兼容路径保持不变。
- **草稿号解析去崩溃去静默**：一处把三元组解包成两个变量，每次调用必抛 ValueError；另一处用裸
  except 把异常连同真实的解析失败一起吞掉。改为正确解包，失败时留痕并返回空串，让调用方降级
  可预期，而不是崩在门里。
- **敏感文件按咽喉拦截**：密钥、证书、.env 及其变体的拒读判定从只挡直接读取上移到所有按内容
  读取共用的唯一入口，一次性覆盖 read、grep、find。批准一个目录不再连带把它下面的私钥读进模型；
  grep、find 遇到敏感文件名直接跳过并回报跳过条数。名单同时补上 .env.local、prod.env 这类带
  后缀变体与单数 credential。
- **门登记装锁、失败撤回**：三处登记门的代码全部改为先拿门串行锁、拿到后重验是否已有开着的门，
  杜绝并行工具调用时布尔门覆盖草稿门的双门齐开。门实体落库失败不再发一张重启后重建不出的幽灵
  确认卡，而是撤回本次拦截、返回可恢复错误，让老师重试。
- **会话锁异步上下文化**：每会话串行锁原先只在流生成器的 finally 释放，进入流之前的准备阶段若
  抛异常就把锁永久泄漏，同会话后续请求全被 409 卡死。改为异步上下文管理器：准备阶段抛异常即时
  释放，正常进入流则把释放权交回流生成器，既不泄漏也不双重释放。

配套：拒绝后重开门、放弃后关门、批准后关门三处对门实体操作的静默 except 一并改为记录日志，
与「门实体失败不静默」的纪律对齐。

## 收尾（二）：门分类、门卡载荷、重放登记三处再次合一

全量审查沿「门由数据强制」这条线又抓出四处多真源与错序，全部当次修完。

- **门是不是计划门，只有一个问法**：草稿门（三值决定）与布尔门（只有批准/取消）的区分原先在
  三处各判一次——事件流入口、草稿号解析处、/history 重建门卡处，判法都是「工具名在不在
  `_GATE_PLAN_TOOLS` 集合里」。而门实体自己带着 `kind` 字段，两套真源并存：新增一个草稿门工具
  忘了同步集合就会被错分成布尔门，而布尔分支会清空 `pending_approval`，于是 C1-Q3 的
  「有未决门时全拦后续工具」随之失效——门没批，下一步照跑。改为唯一入口
  `_gate_is_plan_gate(conversation_id, pending)`：先问门实体的 `kind`（数据），只有门尚未落库
  才回退工具名（本轮内存）。三处调用点收敛到它，含 `agent/channel/conversation.py` 的重建分支。
  回归测试：`tests/test_gate_kind_single_source.py`。
- **本轮草稿号逐事件清零**：适配器上的 `_last_gate_draft_id` 原先跨工具存活，于是一次不带
  `approval_needed` 的后续工具调用会沿用上一张门的草稿号，把门卡绑到旧决定上。改为每个事件
  开头清空、只在真正的开门结果里赋值。回归测试：`tests/test_gate_draft_id_stale.py`。
- **重放失败要撤销去重登记**：确定性重放在执行前已把这次调用登记为「本轮已发过」，重放本身
  失败时登记仍留着，老师看到的是「工具报错 + 再问一遍被判定重复调用」。改为按结果分支：解析
  后先看载荷里有没有 error，成功才 `remember_call`，失败则 `forget_call` 让老师能原样重试。
  同一处顺带把 `tool_result` 事件的 `success` 字段接到同一个判定上，不再各算一遍。回归测试：
  `tests/test_replay_failure_dedup.py`。
- **门卡载荷只有一个构造器**：`gate_open` 事件的字典原先手工拼装 3 处，`recipient` 由
  `action` 内联映射 3 处，`gate_id` 由 `g-draft-` 字面量拼接 4 处，散在适配器与通道两个文件。
  改完一处文案或加一个 action，其余几处静默漂移。现在 wire 契约的唯一家在
  `app/services/gate_service.py` 的 `gate_open_event()` 与 `draft_gate_id()` /
  `gate_recipient()`，上述 10 个拼装点全部改为调用。配一条
  结构性测试守住这件事：扫 `agent/` 与 `app/` 源码，`"type": "gate_open"` 与 `g-draft-` 两个
  字面量只允许出现在 service 文件里。回归测试：
  `tests/test_gate_open_payload_single_source.py`。

## 收尾（三）：退役没退干净——说明书还在教模型调不存在的工具

工具 docstring 与 persona 提示词是**逐字送进模型上下文的说明书**，所以「退役一个工具」在这个
系统里不等于从 `ALL_TOOLS` 里删掉它。阶段 2 当时解绑了工具、给 system prompt 加了禁止条款，
说明书却没跟着改：`read` `find` `grep` `delete_bank` `assign_adaptive_practice` 等处仍在写
「先调 request_approval 确认」，`CLAUDE.md` 的护栏一节与已知缺口表也还把它当成现行机制。后果
不是文档不准，是**同一条链路自相矛盾**——system prompt 禁止调，工具说明指示调，模型真去调一个
不存在的工具，拿回错误，再按「失败治理」条款重试一轮。这类漂移没有编译器会报、没有测试会红，
只能靠结构断言钉住。

- **说明书改为描述真实机制**：首次调用被确认门拦下 → 老师在确认卡上决定 → 系统确定性重放，
  期间不要重复调用本工具、也没有独立确认工具可调。`agent/guard.py` 里把 `request_approval`
  写成重放触发方的注释同步改为「由 channel 确定性重放」；`CLAUDE.md` 的「LLM 跳过 approval」
  缺口按本表既有的 ✅ fixed 样式改判为已闭合——闭合它的正是本 ADR 的主线。
- **三条结构断言**：`tests/test_no_retired_tool_instructions.py` 扫全部注册工具的
  docstring——出现退役工具名即红；讲了「需要批准 / 需要老师确认」却没讲自动重放即红（只讲门
  不讲下文，正是这批说明能长期存活的原因）；persona 提示词里退役名只允许以禁止句式出现
  （「你不需要自己调」留着有用，写成指导句就是复发）。

两处同源遗留如实记账，未当次处理各有原因：

- `agent/tools/shell.py` 的 `bash` 说明是同一处缺陷。本轮该文件另有一处改动在途（白名单拒绝
  提示语），不与它混作同一处修改；该工具进上述测试的例外名单，名单是棘轮——修好了不删条目
  一样红。
- `_make_request_approval_tool` 与 `GuardState.approved` 在 v2 已无生产调用方，却仍被 CI 冒烟
  import（`.github/workflows/ci.yml`）和三个测试引用；`evals/agent_eval_golden.yaml` 里数处
  断言仍在要求模型调 `request_approval`。删除退役符号要同时改 CI 工作流，改写跑活模型的 eval
  需要能真跑起来验证，两者都超出「把说明书改对」的范围，留作独立决策。
