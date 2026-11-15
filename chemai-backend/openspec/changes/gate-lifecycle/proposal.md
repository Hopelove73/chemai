## Why

老师那句原话端到端跑不通：

> 帮我找一下陈湘旻，看看她最近的学习情况，然后针对她的问题给她出一个专门的个性化学习方案，
> 顺便给她先布置一天的练习题，然后把她的学习情况同步给她的家长。

实测基线（2026-10-16）：**卡在第 5 步** · `run.status` 永远 `running` · **家长通知 0** ·
库里残留 41 行陈旧门 + 孤儿门。

失败不是"某处写错了检查"，而是**门与计划步骤是两个各写各的真源，中间没有任何东西保证它们一致**：

| # | 断点 | 位置 |
|---|---|---|
| ① | 门挂到错的步骤上（`_find_step` fallback 命中无关的 running 步） | `plan_run_service.py:33-45` |
| ② | 并发推进产生两个 `running` 步（是①的助力） | `plan_run_service.py:103-106` |
| ③ | 内联卡走 REST，从不碰 `gate_service` → 孤儿门 | `diagnosis.py:673-687` |
| ④ | `/resume` 对已 `sent` 草稿直接 return，**既不占门也不关门** | `langgraph_channel.py:1678-1693` |
| ⑤ | `gate_open_blocked` 把「被拦」写成「等确认」 | `plan_run_service.py:81-87` |
| ⑥ | 计划卡读 `plan_runs.steps`、待确认读 `agent_gates`，**两个真源已漂开** | `MessageList.tsx` ↔ `use-pending-count.ts` |

此外老师还报了两个同源症状：**待确认红点不消**、**待确认与执行中对话来回切会反复重发工作进度**（即 ⑥ 的用户可见面）。

**断点①④ 已于 2026-10-16 由 `evals/e2e_long_chain.py` 首次运行当场复现**，证据见 `tasks.md` 的 0.2。

## What Changes

- **计划步骤与门同源**：门挂哪一步由**步骤身份**决定，不再靠 fallback 猜；找不到对应步就不挂（宁可不挂，不可乱挂）。并发推进前先判「是否已有 running 步」。
- **门的生命周期收敛为单一出口**：`send_draft` 内关门（终态化的唯一出口），6 个调用点自动合规；`guard_state.pending_approval` 删除，重放目标一律从门实体构造（`gate_service.replay_target` 为唯一入口）。
- **重放 fail-closed**：`_direct_replay_events` 解析不出 gate_id ⇒ 拒绝执行 + 判死门 + 给明确人话；`open_gate` 校验重放目标可构造；`agent_gates` 加 `args_hash` + `schema_version`，哈希不符即拒。
- **门三态**：`executing`（已占、结果未知）→ `executed` / 回 `open`，in-doubt 不被盲目重试。
- **存量对账**：一次性修复脚本 + T17 每日作业挂进程内调度器，把 41 行陈旧门/孤儿门/卡死 run 收敛掉。
- **文档改真**：`CONTEXT.md` 的 Confirmation Gate 词条、ADR 记录「重放载荷以数据为唯一真源」。

## Capabilities

### New Capabilities

- `gate-plan-binding`: 门与计划步骤的同源绑定（禁止 fallback 兜底、并发推进互斥、history 按门真实状态校正）
- `gate-replay-fail-closed`: 重放目标的构造校验与哈希校验，解析失败即拒绝执行
- `gate-state-machine`: `open` / `executing` / `executed` 三态与 in-doubt 处置

### Modified Capabilities

- `confirmation-gate` — 关门出口收敛到 `send_draft` 单点；重放载荷改由门实体唯一构造
- `plan-run` — 步骤推进与门绑定解耦为「同源」；`gate_open_blocked` 不再改步骤状态
- `pending-count` — 待确认来源与计划卡同源，红点随门关闭而消

## Impact

- `app/services/plan_run_service.py` — `_find_step` / `_GATE_STEP_SKILL` / 并发推进 / `gate_open_blocked`
- `app/services/gate_service.py` — `replay_target` 唯一入口 + 三态 + `args_hash`
- `agent/channel/langgraph_channel.py` — 只动门相关几处（`_direct_replay_events`、`/resume` 草稿已 sent 分支）
- `agent/gateway.py` — 删 `guard_state.pending_approval`
- `app/api/diagnosis.py` — REST 决定端点接入门生命周期（**不改调用方代码**，靠 `send_draft` 内关门自动合规）
- `agent/models`（或等价表定义）— `agent_gates` 加 `args_hash` / `schema_version`
- `frontend/src/components/chat/MessageList.tsx`、`use-pending-count.ts` — 待确认与计划卡同源
- 新增 `evals/e2e_long_chain.py`（已建）· `tests/test_gate_step_binding.py` · `tests/test_gate_orphan.py` · `scripts/repair_gates.py`

**明确不做**（防止再次跑偏）：

- ❌ 不引持久化执行引擎（Q17 已定）
- ❌ 不重构 `langgraph_channel.py` 的其它部分（2099 行，只动门相关那几处）
- ❌ 不碰那批未提交的 `agent/tools/` 重构
- ❌ 不做门的产品面改造（通知、4a 列表 UI 等，`TODOS.md` 里那些）
- ❌ I5 的 in-doubt **完整对账**留到批次 3 的作业里，批次 2 只落状态机
