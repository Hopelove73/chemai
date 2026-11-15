# ADR-0032: 门开后的回合收尾，暂时仍走模型边界短路

**Date**: 2026-10-20
**Status**: Accepted（推迟；来源：`docs/门视图契约-评审输入-20261020.md` 的 D3，
eng review 裁定「不能单独删」，本次实现期做了替代方案探针，结论见下）

## Context

门（确认卡）开出来之后，本轮必须**停下来**：不然后续每一步都会撞上 `waiting_for_gate`
（2026-10 实测同一条链连吃 4 次），计划卡停在等待态、run 永远 running。

`create_react_agent` 没有「工具跑完就结束这一轮」的钩子。于是 2026-10 引入
`agent/guard.py` 的 `_GatePausedModel`：把模型包一层，`GuardState.paused` 为真时
**直接返回一条不含 `tool_calls` 的 AIMessage**，ReAct 循环没有下一步可走，图自然收尾。

这个东西丑得有道理（它伪造了一条模型回复，还会进 checkpointer 的 `messages`），
评审因此问过 D3：删掉还是留成开关。当时的裁定是「不能单独删」——它是
`GuardState.paused` 的**唯一读者**，删了没有任何东西会阻止模型继续往下调。

## 本次做的探针（真机，不是推断）

问的是：有没有一个**不伪造回复**的等价物。在仓库锁定的 LangGraph 版本上实跑：

| 做法 | 结果 |
|---|---|
| `create_react_agent(pre_model_hook=hook)`，hook 返回 `Command(goto=END)` | 钩子被调用 1 次，**模型仍被调用 1 次**，`goto` 被忽略 |
| `create_react_agent(post_model_hook=hook)`，hook 返回 `Command(goto=END)` | 同上：钩子调用 1 次，模型调用 1 次，`goto` 被忽略 |
| 检查 `chat_agent_executor` 源码 | 有 `post_model_hook`，**没有** `jump_to` |

也就是说：这个版本的 `create_react_agent` 不认这两个钩子里的 `Command` 路由。
「在模型被调用之前结束这一轮」这件事，除了让模型自己返回一条没有 `tool_calls` 的消息，
没有第二个入口。

## Decision

**保留 `_GatePausedModel`，不加开关。** 本次不删。

理由：删掉它，`paused` 没有读者，门开出后模型会继续按计划往下调，A3 原样回归；
而上面三条探针说明没有替代物。在「伪造一条回复」和「每轮连撞 4 次门」之间，
前者是可控的丑，后者是老师面前的死路。

不加开关的理由：开关解决不了它真正的毛病（假回复落进 checkpointer 的 `messages`，
而且每一轮暂停都留一条），只是让它可关闭 —— 一个永远开着的开关不是设计。

## 什么条件下可以删

任意一条成立，就可以另开一轮把它删掉（届时按 D3 的原裁定，先有替代、再删）：

1. 升级到认 `Command(goto=END)` 的 LangGraph 版本（用本文的两条探针复验）；
2. 不再用 `create_react_agent`，改成手搭「agent → tools → 条件边」的图，
   条件边直接判 `GuardState.paused` 走 `END`；
3. LangGraph 提供工具边界钩子（`tool_node` 之后的路由）。

删之前必须有一条能证明「门开出后下一次模型调用 = 0」的测试；
现在这条能力只被 `tests/test_gate_pause_turn.py` 间接覆盖。

## Consequences

- 每一轮暂停会往会话 `messages` 里留一条「已暂停」的助手消息。这是已知代价，
  写在这里以免以后被当成 bug 重新发现一遍。
- 前端**不依赖**这条消息：暂停态由工具卡自己的 `paused` 状态渲染。
  所以将来删掉它时，前端不需要跟着改。
