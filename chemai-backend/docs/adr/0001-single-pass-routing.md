# ADR-0001: 单遍意图路由（Single-Pass Intent Routing）

**Date**: 2025-12-25
**Status**: 已被 ADR-0019 取代

> 本 ADR 描述的三层路由（Gateway → Coordinator → Sub-Agent）属于 v1 多 Agent
> 架构，其代码（`agent/langgraph_agent.py`）已随 ADR-0019 物理删除。路由决策现
> 由单一 v2 ReAct agent 依据工具 docstring 直接完成，不再有 Coordinator/子Agent
> 之分。以下内容仅作历史记录保留。

## 背景

ChemAI 的路由管线让每条用户消息穿过三个 LLM 决策点：

1. **Gateway**（`agent/gateway.py`）：LLM 分类器 → `(intent_type, suggested_tools, provider)`。解析失败有关键词兜底。
2. **Coordinator**（`agent/langgraph_agent.py:_coordinator_node`）：带 `route_to_*` 工具的 LLM → 选择一个子 Agent。
3. **Sub-Agent**（`agent/langgraph_agent.py:create_sub_agent_node`）：带领域工具的 LLM → 决定调用哪个工具。

每一层都用不同的 prompt 重新评估同一条用户消息。层与层之间不共享结构化的决策状态。任何一层产出意外结果（prompt 规则缺失、幻觉），整条链就静默失败。

一个具体失败案例：用户说「我有几个班」。Gateway 路由正确，Coordinator 路由正确，但诊断专家子 Agent 手写的 prompt 规则没覆盖这种说法，于是 LLM 生成「请告诉我您想查看哪个班级的学生」而不是调用 `show_students()`——尽管 `show_students` 的 docstring 明确覆盖了"问有几个班"。

此外，子 Agent 的 prompt 重复了工具 docstring 里已有的指引，形成不可持续的维护负担：每一种新的用户说法都要新增一条 prompt 规则。

## 决策

分三阶段把三遍路由替换为单遍设计：

### 阶段 1：信任工具描述，而非手写 prompt 规则

- 移除 `_SUB_AGENT_DEFS` 中全部 6 个子 Agent prompt 里的编号式「规则」列表。
- 换成统一模板：「你必须调用一个工具。阅读每个工具的描述来决定用哪个。」
- 每个子 Agent 至多保留一行领域专属指引（如 `delete_bank` 需要审批）。
- 把 `_SUB_AGENT_OUTPUT_CONTRACT` 改写为正向约束：「必须先调用至少一个工具」，而不只是「严禁闲聊」。

### 阶段 2：黄金路由测试集

- 建 `evals/golden_routing.yaml`，收录 20 个路由测试用例。
- 建 `evals/test_routing.py` 作为跑分器。
- 每条：`(输入, 期望 agent, 期望工具)`。
- 覆盖：已知 bug、各子 Agent 的核心句式、边界情况。

### 阶段 3：Gateway 吸收 Coordinator

- Gateway 吸收 Coordinator 的路由职责。
- Gateway 输出变为 `(intent_type, target_agent, suggested_tools, provider)`。
- 关键词路由成为主分发器，LLM 按置信度分级兜底：
  - **高置信**（多关键词、无歧义）：直接分发，不调 LLM。
  - **低置信**（单词或歧义词）：调 LLM 复核。
  - **无匹配**：调 LLM。
- Coordinator 退化为确定性的状态读取器（约 10 行，无 LLM 调用）。
- Gateway 结果以结构化 `shared_context` 传入图状态——不再被丢弃。
- 子 Agent 行为不变。

## 已考虑的备选方案

### 保留 prompt 规则并继续扩充
用更多规则覆盖漏掉的说法是无底洞——每种说法变体都要一条新规则。判定为不可持续，否决。

### 每次请求 LLM + 关键词互相校验
两套系统检查每条消息并交叉验证。这消掉了阶段 3 的主要收益（省一次 LLM 调用），还引入了双方不一致时的新仲裁问题。否决，改为置信度分级分发。

### Coordinator 吸收 Gateway
把分类逻辑挪进 Coordinator 的 system prompt 会丢掉关键词兜底（它住在 Gateway 里）和 navigate 快捷方式优化。否决。

## 后果

- **正面**：路由路径从两次 LLM 调用降为一次（阶段 3）。子 Agent 信任工具描述，消灭了 prompt 规则维护负担（阶段 1）。
- **正面**：路由行为可用黄金测试集验证（阶段 2）。
- **负面**：阶段 3 需要重构 Gateway 接口与图状态 schema，实现风险更高。
- **负面**：移除手写 prompt 规则把信任转移给了 LLM 函数调用保真度——黄金测试集（阶段 2）通过捕获回归来缓解。
