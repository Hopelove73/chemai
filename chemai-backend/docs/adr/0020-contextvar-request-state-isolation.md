# ADR-0020: 单进程部署下的请求态隔离一律用 contextvars

**Date**: 2026-03-24
**Status**: Accepted（安全/门加固 Commit ①，grilling 决策 B；本轮 eng 审查复核；
2026-03-24 全量 code-review 补第 5 条「子任务写入边界」）

## Context

ChemAI 后端以**单进程、无 worker 复制**的方式部署（uvicorn 单实例，SQLite 文件库）。
这个部署形状决定了一件事：进程级可变全局不会在请求之间隔离，它会在同一进程里被
并发请求交替读写。v1/v2 共用工具层时，这类状态被当作「临时变量」写下来，实际是
跨会话泄漏面。本轮排查确认的三处：

1. `agent/tools/filesystem.py` 的四个模块级全局——文件访问档位 `_access_level`、
   一次性提权 `_elevate_once`、工作区覆盖 `_ws_override`、会话工作目录 `_session_cwd`。
   它们是老师 per-conversation 的安全边界：A 会话里批准的一次工作区外提权，会被
   B 会话的读文件看到；一个老师设的工作区路径影响另一个老师的读取范围。
2. persona（`agent/tools/context.py` 与 v1 的 `_current_persona`）决定 system prompt
   与工具白名单，是权限而不只是展示。教师省略 persona 时曾静默掉回学生 prompt。
3. `agent/tools/context.py` 的当前身份 / 当前学员同样经由全局传递，工具在异步任务
   里读取时未必还是发起者的上下文。

注释能约束单个文件，约束不了「下一个想加临时全局的人」，所以需要本 ADR 立规矩。

## Decision

1. **请求态用 `contextvars.ContextVar` 承载**，禁止新增模块级可变全局来传请求上下文。
   已落地的四组：
   - `agent/tools/filesystem.py`：`_access_level_var`、`_elevate_once_var`、
     `_ws_override_var`、`_session_cwd_box_var`（决策 B）。
   - `agent/tools/context.py`：当前身份、当前学员，以及本轮新增的
     `set_current_persona` / `current_persona`（随 ADR-0019 收敛 persona）。
2. **读写收在 setter/getter 里**，调用方不直接触碰 ContextVar 对象；提权一类的
   短命状态用「读一次即清」的形状，避免跨回合残留。
3. **约束的检验面**：任何新增的模块级可变容器（dict/set/list 做缓存除外）若被
   工具执行路径读写，视为违反本 ADR，需在 review 中拦下。
4. **「谁来 set」与「set 是否隔离」同等重要**：三个 agent 对话入口
   （`/stream` · 非流式 · `/resume`）各自注入文件档位、工作区根与请求身份，
   `create_chemai_agent` 注入 persona。漏一个入口就是该入口读不到老师前端下发的开关，
   而档位 ContextVar 的默认值是 `workspace`（偏宽松的那一边）——老师选了 `off`，
   走那个端点仍然能读工作区，开关形同虚设。这一步由
   `tests/test_request_context_wiring.py` 以 AST 扫入口函数体守住：隔离性测试全都直接调
   setter，把注入整行删掉它们照绿。
5. **区分「读得到」与「写得回」：工具要交给后续调用的状态，用可变盒子承载**。
   LangGraph 的 `ToolNode` 把同一条 AI 消息里的多个 tool_calls 放进
   `asyncio.gather(...)` 执行，每次调用跑在一个新建子任务里；子任务**复制**父上下文，
   于是 ContextVar 的读取向内层传播、`set()` 却只写进子任务自己那份副本，随任务结束
   消失。实测后果：`bash` 里 `cd sub` 之后再 `dir`，仍在列工作区根——写丢了，读侧还
   一切正常，既不报错也不落默认值，是本 ADR 第 3 条检验面抓不到的那类缺陷。
   落地形状：`_session_cwd_box_var` 存一个 `dict`，由入口的 `set_file_access_level()`
   每请求换一个新盒子，工具读写 `box["rel"]`——改的是对象内容，不是 ContextVar 绑定，
   因此跨子任务可见，同时「每请求新盒子」保住了请求间隔离。
   判据是**写入点与读取点是否必然同上下文**：`cd` 写在一轮的工具里、读在下一轮的
   工具里，必然跨任务 → 盒子；`_elevate_once_var` 的批准写入与重放消费都发生在
   channel 的同一上下文里（登记门 → 确定性重放），普通 var 就够，套盒子反而丢掉
   「读一次即清」的隔离语义。

## Consequences

- Positive：并发会话之间不再共享安全边界状态；单测可在同进程内反复设置而互不影响
  （`tests/test_filesystem_contextvars.py`、`tests/test_persona_context.py`）。
- Positive：第 5 条有专属检验面——`tests/test_session_cwd_across_tool_tasks.py`
  把每次 `bash` 调用真的放进 `asyncio.gather(...)` 的子任务里跑。直接调 setter 的
  隔离性测试做不到这件事：它们全在同一上下文里，写丢了也看不出来。
- Negative：盒子是每请求共享的可变对象，同一轮里并行的 tool_calls 互相看得见对方的
  写入（`cd a` 与 `cd b` 并发时后写覆盖先写）。这是刻意的取舍——会话工作目录本就是
  一条时间线上的状态；需要真正串行的语义（如登记确认门）仍要走 `GuardState` 那类
  显式锁，不能靠盒子。
- Positive：这一层与 ADR-0014 的接缝校验正交——接缝管「有没有权限」，本 ADR 管
  「权限判定的输入属于哪个请求」。
- Negative：contextvars 只在同一 asyncio 上下文内传播。若将来把工具执行扔进
  `ThreadPoolExecutor` 或后台任务，需要显式 `copy_context()`，否则状态读不到——
  这是**可测的失败**（读不到即默认档位/无身份，走 fail-closed），不是静默串号。
- Negative：单进程部署是本 ADR 的前提之一；若改为多 worker，进程内缓存
  （如 `_guard_state_cache`）需要另立决策，本 ADR 不覆盖那部分。

## Alternatives Considered

- **每个工具函数加参数透传上下文**：最明确，但要改所有工具签名与所有调用点，
  且 LangGraph 的工具入参由模型填充，注入内部上下文字段等于开一条伪造入口。否决。
- **保留全局，用 per-request 锁串行化**：吞吐直接压成单并发，且仍需人工保证
  每个入口都取锁。否决。
- **只改 filesystem 那一处，persona 留作 v1 遗留**：v1 删除后 persona 全局的唯一
  写入方消失，读取方仍在——正是「只有一条路径会走到的 bug」的形状。否决。
