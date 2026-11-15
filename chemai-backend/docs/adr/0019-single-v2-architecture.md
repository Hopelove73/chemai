# ADR-0019: 单一 v2 Agent 架构——物理删除 v1 多 Agent

**Date**: 2026-03-23
**Status**: Accepted（安全/门加固收尾，Commit ①）

## Context

ChemAI 长期并存两套 Agent 架构：

- **v1**（`agent/langgraph_agent.py` + `agent/langgraph_sse.py`）：多 Agent，Gateway → Coordinator → 6 子Agent 三层路由，路由决策靠各层 prompt 规则。
- **v2**（`agent/langgraph_agent_v2.py` + `agent/langgraph_sse_v2.py`）：单一 ReAct agent，所有工具直接可见，靠工具 docstring 选择。

生产环境自 v2 上线起只走 v2（`langgraph_channel` 的 `version` 参数虽在，但请求方从不传 v1）。v1 名义上是「回退」，实际早已无人触发，且：

1. **回归风险集中在共享代码**。v1/v2 共用 `agent/guard.py` 与工具层，本轮安全/门加固（contextvars 化、data_scope、门绑定）任何一次改动都要同时验证两条路径，测试面翻倍。
2. **v1 内有 v2 没有的隐性状态**。`agent/langgraph_agent._current_persona` 是模块级全局，v2 从不设置它——教师 persona 在 v2 路径下曾错误落到学生 prompt。保留 v1 只会让这类「只有一条路径会走到的 bug」继续潜伏。
3. **死代码成本**。`ruff.toml`、CI smoke、`CLAUDE.md` 都为 v1 保留着说明与豁免，维护者需分辨哪些仍有效。

grilling 六项定案中，v1 全物理删除列为 Commit ① 的前置：先把架构收敛成一条，后续的安全/门修复才只需在一个模型上成立。

## Decision

1. **物理删除** `agent/langgraph_agent.py` 与 `agent/langgraph_sse.py`。
2. **收敛导入接缝**：
   - `_unwrap_tool_message` 从 v1 提升为 `agent/langgraph_sse_v2.py` 的唯一实现，v1 版随文件删除。
   - 原指向 v1 的 `GuardState` / `TOOL_APPROVAL_REQUIRED` 导入改指 `agent.guard`（本已是中立模块）。
   - `agent/context_manager.py` 的 `get_langchain_model` 改从 `agent.guard` 导入。
3. **`agent/guard.py`** 保留（v2 唯一消费者），docstring 更新为 v2-only 说明。
4. **persona 隔离**改用 `contextvars`：`agent/tools/context.py` 新增 `set_current_persona` / `current_persona`，`create_chemai_agent` 每次按 persona 设置，`tutoring.py` 读取——替代原 v1 全局。
5. **配置/文档**：`ruff.toml` 去掉 v1 lint 豁免；CI 去掉 v1 import smoke；`CLAUDE.md` 改为 v2-only；`agent/channel/langgraph_channel.py` 的 `_get_or_create_agent` 去掉 `version` 分支，固定返回 v2。
6. **ADR-0001**（single-pass routing）标记为 Superseded by 本 ADR——三层路由模型随 v1 一并作废，路由现由单 agent 依据 docstring 完成。

## Consequences

- Positive：架构收敛为一条路径，安全/门加固只需在一个模型上验证；`_current_persona` 全局泄漏 bug 连根拔除（persona 改 contextvars）。
- Positive：CI/lint/文档不再为死代码买单。
- Negative：失去「一键回退 v1」的选项——若 v2 出现阻塞性缺陷，回退需从 git 历史找回，而非改 `version` 参数。判定为可接受：v1 上线以来从未被真实触发，其回退价值低于双路径维护成本。
- Neutral：`docs/adr/0001`、`openspec/changes/*` 中的 v1 引用作为历史保留，不改写。
