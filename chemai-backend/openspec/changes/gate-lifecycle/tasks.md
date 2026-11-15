# 确认门与计划步骤的一致性 — 开发任务

> 每一步都带「验证」。**验证不通过不进下一步。**
> 排序依据：**先修症状的直接来源（① ② ⑤ ⑥），再修底层（③ ④），最后收存量。**
> 锚是 `evals/e2e_long_chain.py`：**它红着，说明有活干；它绿了，才算干完。**

---

## 0. 目标与验收标准（先说清楚什么叫「做完了」）

**目标**：老师那句原话端到端跑完 —— 7 步全走完，陈湘旻收到学习方案 + 一天练习，家长收到同步。

**验收**：一个**可重复的脚本**，跑完断言终态。不是「看着像跑通了」。

```
断言项（全部必须为真）：
  run.status == completed                     # 7 步全部到终态
  无任何 status == 'open' 的 agent_gates       # 门全部被正确处理
  learning_plan_drafts.status == 'sent'       # 方案已发
  practice 记录已建（布置练习生效）              # 第 5 步真做了
  parent_notifications > 0                     # 家长收到
```

现状对照（实测基线）：`run.status = running` · 1 行孤儿门 · 家长通知 **0** · 家长报告草稿 **0**。

---

## 批次 0 · 准备（不动产品代码）

- [x] 0.1 备份演示库到 `%LOCALAPPDATA%\chemai-db-backup-20261016`
  - **验证**：备份目录存在 ✅（2026-10-16 复核仍在）

- [x] 0.2 端到端验收脚本 `evals/e2e_long_chain.py`（终态断言 + 驱动老师真实点击路径）
  - **已完成**：316 行自足脚本。自己起一个 uvicorn（随机端口）指向 `app.shadow_data` 建的影子库，
    跑完关掉 —— 绝不写被 git 跟踪的 `chemai.db`（ADR-0026）。
    驱动的是**老师真实点击的两条入口**：聊天内联卡（REST 决定端点）+ 门决定处理器（`/resume` 带 `gate_id`），
    而不是内部函数。
  - ⚠️ **与原文的一处偏差**：计划原文写「把现有 phase1+phase2 合并」，但仓库里**不存在 phase1/phase2 脚本**
    （`**/phase*.py` 无命中）——那两个是上个会话临时写的、已不在仓库。故落成自足版本。
  - **验证**：2026-10-16 首次运行 → **红，6 条断言未过**（退出码 1），且失败模式与预测完全一致：

    ```
    第 1 轮 agent 序列正常：
      plan_task → show_students → memory_student_get → diagnose_barrier → generate_learning_plan
      *** 门开 g-draft-a681b901b60d4336 action=send_student
      >>> assign_adaptive_practice

    第 2 轮   内联卡 REST → HTTP 200「学习计划已确认发送」   /resume →「已确认发送，无需重复处理」
    第 3~12 轮 内联卡 REST → HTTP 409                      /resume →「已确认发送，无需重复处理」
              （门 g-draft-a681b901b60d4336 全程 status='open'，12 轮上限跑满）

    终态断言：
      ✓ step1-4  completed
      ✗ step5    [awaiting_confirmation] assign_adaptive_practice
      ✗ step6-7  [pending]               generate_parent_report / send_report_to_parent
      ✗ A1 run.status=running（应 completed）
      ✗ A2 仍有未到终态的步骤: ['assign_adaptive_practice','generate_parent_report','send_report_to_parent']
      ✗ A3 仍有开着的门: g-draft-a681b901b60d4336 (send_learning_plan)
      ✗ A5 第 5 步没生效（assignment_batches 无该生新记录）
      ✗ A6 家长没收到通知（parent_notifications 无新记录）
    ```

  - **当场复现的断点**（2026-10-16 用 `compute_tool_transition` 回放实测工具序列验证，
    **不是**从终态反推的——回放终态与实测一字不差：step1-4 completed / step5
    awaiting_confirmation / step6-7 pending）：

    | 时刻 | 回放结果 | 说明 |
    |---|---|---|
    | `generate_learning_plan`（门在此刻开） | `[(4,'completed')]` | **门没有绑到任何步** |
    | `assign_adaptive_practice`（被已开的门拦下） | `[(5,'awaiting_confirmation')]` | **是这条写的 step5** |

    - **⑤ 实锤**（真凶）：`gate_open_blocked` 分支（`plan_run_service.py:81-87`）给一个
      **压根没被门挂住**的工具凭空造出了一个 `awaiting_confirmation` 步。比原文描述的
      「把被拦错标成等确认」更糟——它制造了一个不存在的等待。
    - **① 修正：本次未触发**。先前据终态推断「fallback 命中无关 running 步」是**错的**。
      实际是：`generate_learning_plan` 的 `approval_needed` 分支把 step4 标完成后
      **不启动下一步**，故 step5 仍是 `pending`，而 fallback 只认 `running`/`failed`
      → `_find_step` 返回 `None` → 门谁也没挂。
      ① 仍是**真实的潜在危险**（换个时刻只要有别的步 running，门就会绑错），但**不是本次故障原因**。
    - **④ 实锤**：草稿已 `sent`（REST 200 成功），门仍 `open` 且 `/resume` 恒回「无需重复处理」
      ——既不占门也不关门。
    - **补充事实**：既有 `tests/test_phase1_step_ownership.py` 的 `STEPS` 夹具里**有**
      `send_learning_plan` 步（step 3）——即映射表假设的那一步。所以那组测试是绿的，
      却照不到真实计划形状。**1.1 的夹具必须按实测形状来（7 步，无 send_learning_plan）。**
  - **运行代价**：真调 LLM（DeepSeek），单次几分钟。所以它**不在 `--fast` 零花费层里**。

- [x] 0.3 确认影子库设施（ADR-0026）能覆盖新写的用例
  - **验证**：`python -m pytest tests/ -q` 基线绿 ✅ **1632 passed, 5 skipped**（136~177s）
  - **过程**：首跑 `1 failed, 1631 passed` —— 红的是一条**与本变更无关的既有 flake**
    （`test_artifacts_api.py` 的排序用例）。根因与处置见下方「执行杂记」。
    修掉后重跑两次全绿（第二次带修复，1632 passed）。
  - 注：`tests/test_gate_step_binding.py`（批次 1 的新用例）是在本次收集**之后**才写的，
    不计入 1632；它自带 2 条**故意红**的用例。

> **0.2 是整件事的锚**：它红着，才算有活干；它绿了，才算干完了。

---

## 批次 1 · 计划步骤与门的一致性（治 ① ② ⑤ ⑥ —— 症状的直接来源）

> **⚠️ 2026-10-16 修正（按回放实测）**：本批原文把 **1.1 当红用例**、预期「失败信息显示门挂到了
> `assign_adaptive_practice`」——**该前提已被证伪**。按实测形状写出用例后，1.1 **通过**（空过）：
> 门不是挂错步，而是**谁也没挂**。真正的红是原文里的 1.3（⑤）与 1.5（门未绑定）。
> 夹具见 `tests/test_gate_step_binding.py`（7 步、无 `send_learning_plan`）。
>
> **优先级据此调整**：**1.3 → 1.7（⑤，本次故障的直接来源）最先**；
> 然后 1.5（门必须绑生成步自己）；再 1.4（① 的潜在危险，本次未触发，但仍须堵）。

### ⚙️ 设计决定（2026-10-16）：把 2.4「重放目标从门实体构造」提前到批次 1

**为什么必须提前**：1.5 单独做，是把「卡在 step5」换成「卡在 step4」。齿轮是这样咬的：

```
门开 → 门绑到生成步 step4（1.5）
批准 → compute_decision_transition 认「awaiting_confirmation」的步 → step4 置 running ✓
重放回来 → compute_replay_transition 认 skill == tool_name（"send_learning_plan"）
         → 没有任何一步的 skill 是它 → 找不到 → step4 永远停在 running ✗
```

`compute_decision_transition` 与工具名无关（它认状态），`compute_replay_transition`
却认工具名——**两个函数对「门绑的是哪一步」各有一套说法**，这正是 ⑥「两个真源」的另一副面孔。

**做法**：`agent_gates` 加 `plan_step` 列，门成为「它绑哪一步」的唯一真源。

- 绑定时机：**投影 `awaiting_confirmation` 转移的那一刻**（`project_transitions`）——
  那里同时知道步号（转移元组）与开着的门，也是「门挂哪一步」的唯一判定点。
- 决定与重放都按门上的 `plan_step` 定位，不再猜工具名。
- `NULL`（存量门）→ 回退旧行为，**刻意不回填**（推错的绑定比没有绑定更难查）。

**分阶段落地**（每阶段跑全量）：

| 阶段 | 内容 | 验证 |
|---|---|---|
| 1 ✅ | schema + 管道：`plan_step` 列、迁移（CLI + startup 双挂）、`bind_step`、`get_open_gate`/`replay_target` 带出 | 门/计划子集 297 passed |
| 2 ✅ | `compute_decision_transition` / `compute_replay_transition` 接受显式步号；`_rebuild_pending_from_gate` → `pending["plan_step"]` → `_plan_completion_events` 传下去 | 全量绿 **1632 passed**（行为未变：无 step_no 时走旧路径） |
| 3 ✅ | 行为修复 1.4 / 1.5 / 1.7 + 绑定生效 | 1.3、1.5、1.2、1.8 四条红用例转绿；全量 **1636 passed** + 5 条既有测试按新语义改写 |

**连带作废的既有测试**（改语义必然——须改写并注明理由，不许直接删）：
- `tests/test_plan_step_advance_guard.py::test_blocked_tool_still_falls_back_to_running_step`
  —— 它钉的正是 1.4 要移除的 fallback（「门不只挂同名步骤」）
- `tests/test_phase1_sse_step_gate.py::test_gate_open_blocked_marks_awaiting_not_failed`
  —— 它钉的正是 1.7 要移除的「拦下即等待确认」

- [x] 1.1 用例 `tests/test_gate_step_binding.py::test_1_1_...`：计划里**没有** `send_learning_plan` 步时，门**不得**挂到别的步上
  - **验证修正**：这条**不是红用例**——实测下它当场通过（无步被写成等待确认）。
    保留为**回归钉**（防 ① 复发），但它不驱动任何实现改动，别把它当开工信号。
- [x] 1.2 红用例：计划已有一个 `running` 步时，另一个工具完成**不得**再启动新步
  - **已完成**：`tests/test_gate_step_binding.py::test_1_2_no_second_running_step`
  - **验证**：跑 → **失败** ✅ `[(2,'completed'), (3,'running')]`（② 并发双 running 实锤）
- [x] 1.3 红用例：`gate_open_blocked` 结果**不得**把步改成 `awaiting_confirmation`
  - **已完成**：`tests/test_gate_step_binding.py::test_1_3_blocked_tool_must_not_invent_an_awaiting_step`
  - **验证**：跑 → **失败** ✅ `assert (5,'awaiting_confirmation') not in [(5,'awaiting_confirmation')]`
    —— ⑤ 实锤，且比原文描述更糟：它给一个**没被任何门挂住**的工具凭空造出了等待
- [x] 1.4 改 `_find_step`：回退**整个删掉**（找不到同名步 = 该门不属于任何步，不硬塞）
  - **已完成**：`allow_fallback` 参数一并删除 —— 1.4+1.7 之后已无任何调用方用回退，
    留着就是死参数（简化，不是重构：删掉的正是本步要废掉的那条路径）
  - **验证**：1.1 保持绿 ✅（它本来就是空过，这条改动主要靠 1.2/改写后的用例兜）
- [x] 1.5 改 `_GATE_STEP_SKILL` 语义：`generate_*` 是「生成即门」，**门挂在生成步自己身上**（不再映射到一个可能不存在的发送步）
  - **新增红用例**（已就位）：`tests/test_gate_step_binding.py::test_1_5_gate_binds_to_the_generator_step_itself`
    —— 断言门开的瞬间有且只有 step4 处于 `awaiting_confirmation`
  - **验证**：跑 → **失败** ✅ `assert [] == [4]`（门谁也没挂）
  - **验证**：修后 → 该用例转绿，**且 1.1 保持绿**
- [x] 1.6 改并发推进：`next(pending)` 前先判「是否已有 running 步」
  - **已完成**：抽成 `_start_next_step()`，`compute_tool_transition` 与
    `compute_replay_transition` 两处「启动下一步」共用 —— 两处是同一个缺陷，
    改一处留一处等于没改。
  - **验证**：1.2 转绿 ✅
- [x] 1.7 改 `gate_open_blocked` 分支：不动步状态，**不产生任何转移**
  - **已完成**：整支改成 `return []`。也不再标 failed（2026-09-20 那条意图）
    —— 它没有失败；也不标 awaiting（它没在等什么）。「什么都不动」同时满足两边。
  - **验证**：1.3 转绿 ✅
- [x] 1.8 **⑥ 同源**：`/chat/history` 返回的 `run.steps` 要按**当前门的真实状态**校正 —— 门被判据过滤掉的，步骤状态不得还写「等待确认」
  - **已完成**：`conversation._active_run_payload` 末尾加同源校正（三个门的来源问完之后）。
    只改**下发的载荷**，不回写库 —— 收敛存量是批次 3 对账作业的活。
  - **一处自主决定（可推翻）**：校正后的状态取 `pending`。取 `completed` 是谎报
    （没被确认不等于办完了）；取 `failed` 也是谎报（它没失败）。`pending` 是「少声称」
    的那个。批次 3 的对账作业会按真实终态把这些行落定。
  - **验证**：`test_1_8_...`：跑 → 失败 ✅ 下发 `['generate_learning_plan']` 为等待确认；修后转绿

**批次出口**：`pytest tests/ -q` 全绿 + 端到端脚本的「步骤状态」相关断言转绿。

---

## 批次 2 · 门的生命周期（治 ③ ④ + 红点）

> **⚙️ 2026-10-17 范围变更：本批新增「入口分主」一条**（老师拍板，方案 a）。
> 详见下方 2.0。

- [x] 2.0 **入口分主**：聊天内联卡与聊天确认卡走同一条决定通路
  - **问题**：修完 ③④ 门能关上了，但 agent 照样不继续 —— 让 agent 续接的只有
    `/resume` 的重放，而聊天内联卡打的是 REST 端点（`InlinePanels.tsx` 批准后
    只 `setResolved` + toast，没有任何续接）。门关了、草稿也发了，run 还是 running。
  - **做法**（方案 a）：聊天内（**有会话**）走 `/resume`；学生管理页的
    `DraftGatePanel`（**没有**会话）继续走 REST。**每条入口各有各的主人。**
  - **已完成**：`MessageList` 把已有的 `onApproval`（= `ChatPage.handleDecision`
    → `/resume`）透传给内联卡；`InlineLearningPlan` 的 `approve` 改走它。
  - ⚠️ **留了一处不对称**（待办）：内联卡的 `reject` / `abandon` 仍走 REST。
    它们的服务端行为是自足的（打回就地算 v2 并原地重开门、放弃只改步状态），
    不产生「agent 必须继续」的缺口 —— 但**同一条面板上两个决定两个主人**，
    是个迟早要收的口子。
  - **已完成**：`evals/e2e_long_chain.py` 同步改（它原先每轮「先 REST 再 /resume」，
    按的是「两条都得收场」；方案 a 之后聊天场景的真实入口只有 `/resume`）。
  - **验证**：`npx tsc --noEmit` 干净；`npx vitest run` 431 passed

- [x] 2.1 红用例 `tests/test_gate_orphan.py`：**拿库里那行孤儿门当 fixture**（gate open + draft sent）
  - **已完成**：两条用例。断言必须看**门行本身**（`AgentGate.status`）而不是
    `get_open_gate` —— 判据对「草稿已 sent」的门本来就不露出，拿它当断言等于没测。
  - **验证**：跑 → 失败 ✅ `assert 'open' != 'open'` / `发送后仍留下 1 行 open 门`

- [x] 2.2 **执法点下沉**：`send_draft` 内关门（终态化的唯一出口），6 个调用点自动合规
  - **已完成**：`send_draft` 落 `STATUS_SENT` 后当场调
    `supersede_stale_gates(db, draft_id=...)`（收敛器加了 `draft_id` 过滤，
    **没有新增写库点**——「唯一的假门写库点」这条不变量还在）。
  - **副产品**：现在**造不出新的孤儿门**了 —— `open_gate` 末尾自带本会话收敛，
    草稿已 sent 时新门开出来当场被收。2.1 的用例因此得直接 `db.add` 造行，
    而那模拟的正是存量。
  - **验证**：2.1 转绿 ✅；`diagnosis.py` 的 REST 路径**未改一行**也合规

- [x] 2.④（原「门被判死」的另一半）`/resume` 发现草稿已被别处处理而早退时，一并判死门
  - **已完成**：新增 `_converge_orphan_gates(cid)`，在
    `langgraph_channel.py` 的 approve 早退分支调用 —— 原先那里 `return` 得
    干干净净，门一个字没动。
  - **验证**：扩了 `test_approve_conflict_with_already_sent_draft`（原有用例没有
    门行，钉不住这件事）；跑 → 失败 ✅ → 修后转绿
- [x] 2.3 `_direct_replay_events`：`_gid` 解析为空 ⇒ **拒绝执行** + 判死门 + 明确人话（对齐已有 `_gate_taken_events` 语义）
  - **验证**：新增用例
- [x] 2.4 **删 `guard_state.pending_approval`**，重放目标一律从门实体构造（`gate_service.replay_target` 为唯一入口）
  - **验证**：全量回归；`_rebuild_pending_from_gate` 的裸 except 一并去掉

  **已做完的那一半（2026-10-17）：两处「以实体为准」，其中一处是真安全缺口。**

  **① 没预料到的收获 —— C1-Q3 重启后整段失效（真缺口，不是整洁问题）。**
  那条「有未决门时 agent 的一切工具调用都被拒」的判据一直是**内存里**的
  `guard_state.pending_approval`，而内存随进程一起没了。于是重启之后：老师看着一张
  还在等他的确认卡（卡片来自库，照常显示），**闸却没了** —— agent 能直接往下推进，
  把卡片还等着的那些动作先做了。`_rebuild_pending_from_gate` 的存在本身就承认了这个
  缺口，但它只被**重放**那条路调用，拦不拦得住工具这条路上没人问库。
  - **修法**：`GuardState.gate_is_open()` —— 内存先答（快路），答不出来就问实体；
    读不出来返回 `None`，调用方 fail-closed（与 `_replay_payload_mismatch` 同一口径）。
  - **复现先红**：`tests/test_gate_blocks_after_restart.py` 三条（重启后仍要拦 /
    没门一律放行 / 门关了要抬闸）。后两条是**反向钉子**：只写第一条会把 agent 锁死。

  **② 重放目标以实体为准。** 原先内存优先、实体只当兜底 —— 于是"要执行的东西"有
  两个来源。现在反过来：`_rebuild_pending_from_gate`（走 `gate_service.replay_target`
  这个唯一入口）是首选，内存只在**无会话**（纯内存评测）时兜底。
  - 裸 `except Exception: return None` 换成了留痕（任务原文点名要去掉的那处）：
    读失败时静默返回 None，调用方会悄悄退回内存那一份 —— 而"悄悄退回另一个来源"
    正是本步要消掉的东西。
  - **连带补的一个洞（第一版补错了，被契约抓回来）**：实体优先之后，"门没了"不再
    伴随内存里那份 pending，老师批一张过期卡会**什么都收不到**。第一版我写成
    `if cid: return _gate_taken_events(cid), None` —— **撑坏了契约**：
    `_direct_replay_events` 返回**空**是既有约定（调用点 `if not direct_events:
    "没有待处理的确认请求"`），而 `/resume` 那条路 `if _direct:` **非空**就走"直接
    重放"分支并 return。撑成非空等于把**正常 resume** 也拐进那条路：发一句假的
    "已处理过"、还不跑续接。
    改法：**只有客户端真的提交了 `gate_id` 且那一行还在**（说明批的是已经翻篇的门）
    才回"已处理过"，其余一律返回空。新增 `_gate_row_exists(cid, gate_id)`
    （按会话一起查，只凭 gate_id 会认下别人会话上的门；读失败当 False —— 这里判的
    是回哪句人话，不是放不放行）。
  - **一处语义变化（测试照出来的）**：重放按门那行的 `tool_name` 经 `_TOOL_BY_NAME`
    解析函数，所以往 `pending_approval` 里塞假工具**不再作数**。生产无影响
    （2.5 保证了门能开 ⇒ 工具必在注册表里，解析出来的就是同一个函数）；
    但 `test_gate_decision_loop` 里那条「批准两次不执行两遍」原来靠数假工具调用次数，
    已改成**看真实效果**（建一个真题库 → 批准后它真的没了 → 第二次绝不能再来一遍），
    比数假工具更硬。

  **✅ 收尾完成（2026-10-17）——字段本身删掉了。** 全仓库 `pending_approval`
  归零（生产 26 处 → 0，测试 42 处 → 0；只剩几段解释历史的注释）。
  读路径一律改成 `_pending_from_entity(cid)`（内部就是 `_rebuild_pending_from_gate`
  → `replay_target`，形状与原内存那份对齐，所以调用点几乎不用动）。

  **⚠️ 收尾过程中撞出两个真回归 —— 都是"删字段"这个动作本身引发的，不是原有 bug：**

  1. **并发双门齐开（W4a 失效）。** `acquire_gate` 的锁**只包住「检查」**：
     锁内写一个内存字段当**预订**，第二个进门的就看见了；登记在锁外。
     字段一删，锁内什么都不留 ⇒ 两个并发门都能通过检查、各自登记。
     这正是 W4a 当初要防的（repro-zhou-0920）。
     **修法**：把**登记搬进锁里** —— `acquire_gate(**register_kw)` 在同一个临界区
     完成「问实体 + 登记」，三态返回 `ok/busy/failed`（调用方要分开交代：
     "已有卡在等" vs "登记写不进去"，压不成一个布尔）。

  2. **布尔门上"打字=作废"变成一个空动作。** 原先它清内存；而**清内存其实早就拦不住
     批准**（重放走 `_rebuild_pending_from_gate` 读门行，清掉也会重建）。
     2.4 前半段让 C1-Q3 也改问实体之后，清零就更没意义了 —— 老师打了「先别管这个」，
     卡片还在、工具照样被全拦，那句话等于没说。
     **修法**：新增 `_void_open_gate(cid)`，把开着的那扇门落 `cancelled`。
     语义与 abandon 同源（ADR-0015：打字永不构成批准）。

  **测试侧**：29 条红，分三类处理 ——
  - **有据退休**（测的是已退役的机制，不是丢覆盖）：`request_approval` 工具的
    interrupt 行为、内存 pending 的存取。每条都在原地写明"原测什么 / 现覆盖在哪"。
    退休的 `_make_request_approval_tool` 工厂本身也删了（零调用，且会读已删字段）。
  - **改写靶子**：断言从"内存那个字典"换成"门那一行"（那本来就是唯一真源）。
    凡是需要"门开着"的用例，一律补**真库 + 真门行**。
  - **一处必须注意的替身**：`_gate_is_plan_gate(cid, pending)` 的 `pending` 是**参数**
    （一份描述），不是某个对象上的属性 —— 测试里直接造 dict 即可。

  ⚠️ **`set_elevate_once` / `_consume_elevation` 的"一次批准一次提权"现在没有单测
  直接覆盖**（原来那条用例是靠退役工具驱动的）。它仍被按次审批那条路使用，
  下次动到那里要留意。

  - ⚠️ **一处由此产生的冗余（记下来，别当成还能守堤）**：重放改用实体之后，
    2.6 的第二道校验（"要执行的就是批准的那一份"）在这条路上**恒真**了
    —— 要执行的就是门那行读出来的。它仍有意义（守住将来别的调用面），
    但别指着它挡"内存与实体漂开"：那个洞现在由"只有一个来源"来堵。

  - ⚠️ **顺带发现：这个锚本身是不稳的，与 2.4 无关。** 端到端是**真调 LLM 拆计划**的，
    而 `PLAN_PROMPT` 明确要求「提到同步家长必须包含 generate_parent_report **与**
    send_report_to_parent 步骤」——于是模型有时会把 `send_learning_plan` 也拆成单独一步。
    而 1.5 的门绑定策略是「生成即门，门挂在**生成步**自己身上」，那一对里的**发送步**
    就永远没人收尾：run 收不了官、A1/A2 红。
    **实测**：2026-10-17 有一次正是这样红的（8 步计划，step6 `send_learning_plan`
    卡在 `running`、step8 `send_report_to_parent` 拿到 `pending`），同一天的另外四次
    （7 步计划、无 `send_learning_plan` 步）全绿。
    ⇒ 看到这个锚红了，**先看计划几步、有没有那一对步骤**，别急着往门上查。

    **✅ 已修（2026-10-17，真凶和我先前猜的不一样）。** 我原以为是"1.5 只把门绑在生成步、
    发送步没人管"。**不是** —— 真凶是**同一个转移被落了两遍，两遍用两套规则**：

    | 调用面 | 找步规则 |
    |---|---|
    | `_plan_completion_events`（在 `_direct_replay_events` 内） | 门的 `plan_step`（新规则，唯一真源） |
    | `_persist_send_completion`（调用点上，**紧跟着**跑） | 按 `skill == tool_name` 找同名步（旧规则），**不传 `step_no`** |

    `_persist_send_completion` 诞生于 2.8 之前 —— 那时 `_plan_completion_events`
    **只发 SSE、不落库**，它是把 DB 补上的那一下。2.8 修完之后它就成了纯双写，
    而且用的是**批次 1 明令废掉的规则**（「重放按门上的 `plan_step` 定位，
    **不再按工具名猜**」）。

    两套规则各推一格的实测形态（确定性回放验过）：

    | 形状 | ① 按门的 plan_step | ② 再按工具名 |
    |---|---|---|
    | **7 步**（家长门绑生成步 6） | 完成 step6、**启动 step7** | 同名步 7 此刻正是 `running` → **被它完成** ✅ 全绿 |
    | **8 步**（学习门绑生成步 4） | 完成 step4、启动 step5 | `send_learning_plan` 那格还是 `pending`（旧规则只认 `running`/`awaiting`）→ **匹配不上，永远悬着** ❌ |

    ⇒ 7 步形状的"全绿"是**巧合**，不是设计。

    ⚠️ **修法我第一版搞反了，当场被端到端打回。** 第一版把 `_persist_send_completion`
    整个删掉（当成多余的双写）—— 端到端**立刻红在** `send_report_to_parent` 停在
    `running`（`A1`/`A2`）。这说明**第二个"完成"是承重的**：

    > 老师批准的是**生成步**（卡上确认的就是那份产物），但重放真正跑的是**发送工具**。
    > 一次批准办完两件事，计划也把这两件事写成两步，两步就该都收。

    它错的**只是过滤器**（只认 `running`/`awaiting_confirmation`），所以 7 步形状
    碰巧能匹配、8 步形状匹配不上。

    **最终修法**：把这条规则**搬进 `compute_replay_transition`**（转移规则的唯一家园）——
    按 `skill` 认、**`pending` 也算数**、并排除已由门定位的那一步。确定性回放验过两种形状：

    | 形状 | 转移 |
    |---|---|
    | 7 步（家长门绑生成步 6） | `[(6,'completed'), (7,'completed')]` |
    | 8 步（学习门绑生成步 4） | `[(4,'completed'), (6,'completed'), (5,'running')]` —— 原来永远悬着的那格收掉了 |

    **连带补的一处**：`_persist_send_completion` 原先"落库失败要留痕"
    （`test_failure_leaves_a_trace` 钉着），而存活的那条路 `project_transitions`
    是 `except Exception: pass` **静默吞** —— 删掉它等于把那条保证弄丢了。
    已把 `project_transitions` 改成**留痕**（并给 `plan_run_service` 加了 logger），
    用例靶子改写到它身上（**改写，不是删**）。

    **钉子**：`test_gate_step_binding.py::test_replay_completes_both_the_gate_step_and_the_tool_step`
    ＋反向钉子 `::test_replay_completes_a_send_step_even_while_it_is_pending`
    （钉的正是旧过滤器匹配不上的那一格）。

  - ⚠️ **同一族的第二个来源：`assign_adaptive_practice` 会报一次"成功的空操作"。**
    `_compose_adaptive` 一题都没过审时，`_assign_one` 返回 `status: "skipped"` 且
    **不建记录**，而工具返回里**没有 `error` 键** ⇒ `_success = True` ⇒ 门收成
    `executed`、计划步标 completed，库里什么都没有。给老师的话还是
    「已为 **0/1** 名学生布置练习（**题目已落库**…）」—— 既让 A5 随机红，
    也是一句假话。**与 2.4 两处改动无关**（我直接跑过一次，5 题全过审）。
    **✅ 已修（2026-10-17）**：`done == 0` 时返回带 `error` 的载荷。
    这样 `_success = False` ⇒ **门退回 `open`**（老师可重试 —— 这正是 2.7 那条
    「失败 → 退回 open，给老师重试机会」的语义，而不是把一次确认白白消费掉），
    模型也拿得到失败原因去换策略（换知识点 / 减题量）。
    挑这一种的另一个理由：另一条路（只把话说实话、仍算成功）**不会**让锚变可信 ——
    库里照样没记录，A5 照样红。

  - **顺带补强了锚：A3b。** 原来 A3 只查 `status='open'`，**卡在 `executing` 的门
    它看不见** —— 而那恰恰是 A5 红时要分清的第一件事：卡在 `executing` 是**重放没跑完**，
    落成 `executed` 却查无记录是**重放跑了但没做成**，两者处置完全不同。
    现在跑完会一并抓 `executing`（这一行跑完时进程还活着，启动扫描救不了它）。
- [x] 2.5 校验重放目标可构造（`_TOOL_BY_NAME` 命中 + kwargs 可序列化），否则**开门即失败**
  - **落点改在 `agent/guard.py:register_gate_entity`**（不是 `open_gate`）：它是唯一
    调用方，`_TOOL_BY_NAME` 在 `agent/` 层拿得到 —— 反过来让 `app/services/` import
    `agent/` 是把分层倒过来。该函数本来就有 fail-closed 契约（返回 False → 调用方
    撤回拦截并撤销调用痕迹）。
  - **已完成**：新增 `_replay_target_constructible(tool_name, kwargs)`；不满足即
    拒绝开门 + 留痕。
  - **验证**：`test_gate_refuses_to_open_when_the_replay_target_is_not_constructible`
    跑 → 失败 ✅（`assert True is False`，门照开）→ 修后转绿；
    反向钉子 `test_gate_still_opens_for_a_real_tool` 防"谁都不给开"
  - **连带作废**：`test_phase2_gate_unification.py` 的门卡用例用了 `fake_tool`
    —— 一个**根本不存在的工具名**。夹具走在了现实前面：生产里 `requires_approval`
    的工具全来自 `TOOL_APPROVAL_REQUIRED`，清一色真工具。改用真工具名。
- [x] 2.6 `agent_gates` 加 `args_hash` + `schema_version`，重放前校验
  - **验证**：新增用例：哈希不符 → fail-closed

  **已完成（2026-10-17）**。落点与前一条设想的不同，理由值得记下来：

  - **写侧**：`open_gate` 两个分支都写（新开 + 同 `gate_id` 复位重开）。复位分支
    漏写的话，打回重开那一次会带着上一版的指纹过去，校验立刻误报。
  - **读侧**：`gate_service.verify_replay_payload`，**两道都要过**：
    ① 门行自己还是开门时那一份吗（挡「卡片的来源被改过」）；
    ② 即将执行的就是批准的那一份吗（挡「卡片与执行脱节」）。
    **①单放不下**：门行是老师看到的卡片的来源；**②今天尤其要**：重放执行的是
    内存 `pending["fn"](**pending["kwargs"])`，不是门那行，①根本管不到它。
    —— 这条原本只打算做①，是用例把它顶出来的：改门行的用例红了，才发现光看①
    等于没验真正要跑的东西。
  - **调用点在 `claim_gate` 之后**（占门 → 校验 → 执行）。拒绝时必须自己收口：
    门此刻是 `executing`，丢下不管会被启动扫描落成 `in_doubt`，给老师冒出一张
    "结果未知"卡，而那次执行**根本没发生**。拒绝落 `superseded`（内容已不可信，
    退回 `open` 只会再撞一次同样的墙）。
  - **存量门放行**：`args_hash` 为 NULL = 「未记录」（同 `plan_step` 口径），
    **不回填** —— 回填等于按今天的值算一个指纹塞进去，把"开门后被改过"洗白成
    "一直如此"，恰恰是这列要挡的东西。迁移 `migrate_agent_gates_replay_payload`，
    幂等，已在临时库上验过「加列 / 二次 skip / 存量行保持 NULL」。
  - **前提核查**：动手前先确认了内存 pending 与门行的 `tool_name`+`kwargs` 在**四条
    开门路径**上一致（`guard.py` 草稿门 :460 / 按次审批 :480 / 布尔门 :396，
    `langgraph_channel.py` 打回重开 :1675+1721）——不然②会在正常路径上误报。
  - **验证**：`tests/test_gate_replay_payload_pin.py` 7 条（钉住 / 放行 /
    门行被改 / 执行值被换 / 工具名被换 / 存量放行 / 版本不认）。

  - ⚠️ **实地踩的坑（写下来给下一个人）**：迁移**只加了 `migrate()` 那一处**
    （CLI 路径），没挂 `app/main.py` 的 startup。`app/main.py:530` 那段注释写着
    「其余 DDL 仍是 CLI-only，这里是唯一例外」—— 也就是说 startup **不走**
    `migrate()`，是自己逐个 import 迁移函数。漏挂的代价当场兑现：影子库启动不跑
    DDL ⇒ `open_gate` `no such column: agent_gates.args_hash` ⇒
    `register_gate_entity` 全线失败 ⇒ **一扇门都开不出来、卡片全没有**，
    端到端从"绿"直接退化成"模型反复重试工具"（`generate_learning_plan` 连调 4 次）。
    **判据**：加 `agent_gates` 列 = 必须挂 `app/main.py` startup，两处都要。
    单元测试照不到这个 —— 它们自己建 schema，列一定在。**只有端到端能照到。**
- [x] 2.7 门三态：`executing`（已占、结果未知）→ `executed` / 回 `open`
  - **为什么它不只是加固**：`claim_gate` 在执行**之前**占门（防同一扇门批两次，对），
    但原来占住就是 `executed` —— 动作一失败，门已被消费，**老师再也没有重试机会**，
    拿着卡点什么都只得到「已经处理过了」，而那个动作一次都没跑成。
    2.3（解析不出门即拒绝）把这条侥幸路也堵上之后，这个洞变成硬伤。
  - **已完成（状态机核心）**：
    - `claim_gate`：`open → executing`（占住 ≠ 做完）
    - 新增 `finish_gate(db, gate_id, ok)`：成功 → `executed`，**失败 → 退回 `open`**
      （`decided_at` 一并清掉），只从 `executing` 收口
    - `_direct_replay_events` 在 `_success` 算出后调用它收口
  - **验证**：4 条新用例跑 → 失败 ✅（`assert 'executed' == 'executing'`）→ 修后转绿；
    `test_gate_unit_of_work.py::test_claim_gate_is_a_one_shot_permit` 按新语义更新
    （它钉的「只许占到一次」没变，只是终态名换了）
  - **✅ 已做：in-doubt 的呈现**（老师拍板：显式「结果未知」卡，老师定）。
    落地记录见本节末尾「第 2 步与第 4 步已完成」——**下面这段判据原文保留**，
    它是那一步的设计依据（「看进程边界，不看到底等了多久」）。
    **判据已想清楚（2026-10-17）：看进程边界，不看到底等了多久。**

    `claim_gate` 与 `finish_gate` 之间那一段**永远在同一次请求、同一个进程里**
    （`_direct_replay_events` 是个 async 函数，占门/执行/收口都在里面；仓库单进程
    部署见 ADR-0020）。所以：

    | 情形 | 判据 | in-doubt？ |
    |---|---|---|
    | 正在执行 | 本进程里，`executing` 没经历过重启 | 否（该说「正在执行，请稍候」） |
    | 进程挂了 | **启动时**仍停在 `executing` | **是**（执行它的进程已不在，结果不会有回音） |
    | 动作抛异常 | `finish_gate(ok=False)` 已跑过 | 否（已退回 `open`） |
    | `finish_gate` 自己失败 | 进程还活着，与「正在执行」无法区分 | 罕见（单条 UPDATE 失败=库坏了），下次启动兜住 |

    ⇒ **启动时把仍停在 `executing` 的门落成 `in_doubt`。** 不用超时：阈值必须大于最慢的
    工具（`assign_adaptive_practice` 里头调 LLM，`_TURN_TIMEOUT` 是 180s），定短了正在跑的门
    会冒卡吓人、定长了真挂了要等很久。**能看事实就别猜** —— 与仓库一贯口径一致。

    **第 1 步已完成（2.7b）**：`GateService.sweep_stuck_executing(db)` —— 把仍停在
    `executing` 的门落成 `in_doubt`；挂在 `app/main.py` startup（紧挨
    `migrate_agent_gates_*`，**在任何读门的代码之前**，否则那张卡要等下次重启才出现）。
    失败不拒启（门数据原样未动，下次启动重扫）。状态值的含义写进了模型的列注释。
    验证：2 条用例（正扫 + 反向钉子「正常收口的门一律不碰」）先红后绿。

    ⚠️ 写测试时踩到的坑：草稿门的 `gate_id` 由 `draft_id` 生成，**两个门共用一个草稿
    就是同一行**（upsert 命中同一 `gate_id`）。要两扇门就得两个草稿。

    **第 2 步的设计判断（已定，2026-10-17）：另开判据，不要动 `pending_gate_criteria`。**

    第一直觉是「把 `in_doubt` 加进那个判据」——**那是错的**，两个理由：

    1. **问的不是同一件事。** 那个判据回答「哪些 open 门算数」（待确认）；in-doubt
       问的是「哪些门需要老师裁定」（待裁定）。混进一个断言，将来没人说得清它在保证什么。
    2. **它的草稿判据对 in-doubt 根本不成立。** 它要求「草稿 MAX(version) 那行仍在
       `awaiting_confirmation`」，而动作用**很可能已经成功了**（草稿已 sent），进程只是
       死在 `finish_gate` 之前。拿它筛 in-doubt 会**恰好漏掉最该报的那一类**。

    ⇒ 另开 `in_doubt_gates(db, user_id)`：自己的「同会话取最新一行」规则、自己的归属
    过滤（照 `pending_gates` 的 `_ownership_predicates`）。`pending_gate_criteria`
    一个字不动。

    **第 3 步的后端已完成（2.7b-3）**：`GateService.resolve_in_doubt(db, gate_id, retry)`
    —— 老师对那张卡的两种选择：
      - `retry=False`（确认已执行）→ `executed`（给"动作其实跑成功了、进程死在收口前"）
      - `retry=True`（重试）→ 退回 `open`（给"动作其实没跑成"；重放通路是现成的，
        不为 in-doubt 另造一条执行路径）
      - **只认 `in_doubt`**：一扇还在等老师决定的门被"确认已执行"，等于把一个从没
        执行过的动作记成做过。
    验证：3 条用例（确认 / 重试 / 反向钉子）先红后绿。

    **剩下的**：第 2 步的查询（`in_doubt_gates` + 归属过滤）与端点、第 4 步的前端卡形态。

    **第 2 步与第 4 步已完成（2026-10-17 结案）。**

    ⚠️ 这里曾有一段「剩下的三件事」，其中第 1 条写「得有**超时/心跳判据**」——
    **与上面 2026-10-17 的定论直接冲突**（定论是「看进程边界，不设超时」，理由：
    阈值必须大于最慢的工具，定短了正在跑的门会冒卡吓人）。那段是旧文本，删掉，
    以免下一个人照着它去加超时。

    - **读**：`GateService.in_doubt_gates(db, user_id)` —— **判据另起一条**，
      `pending_gate_criteria` 一个字没动。只有两条：`status=='in_doubt'` +
      每会话取最新一行。归属沿用同一套 `_ownership_predicates`，形状与待确认列表
      同构（前端共用渲染器）。
    - **端点**：`GET /api/agent/gates/in-doubt`（列表）+
      `POST /api/agent/gates/{gate_id}/in-doubt`（`{retry: bool}`）。
      **顺带发现一个更硬的缺口**：`resolve_in_doubt` 此前**一个调用方都没有**
      （只有测试），也就是老师根本没法对那张卡做任何事 —— 这个 POST 不是可选项，
      它是那条动作通路的唯一出口。归属另判（`owns_conversation`）：决定改的是别人
      会话的状态，光有权限不够。撞上非 in_doubt 的行返回 **409**（撞的是状态，
      不是请求写得不对）。
    - **前端**：`PendingGatesPage` 加 `InDoubtSection`（老师拍板：放待确认页，
      不放聊天内联 —— 内联要动 SSE 事件 + 流式 reducer，而这一态是**重启后**才产生的，
      还得另想"重进会话怎么补发那张卡"）。两个动作直连端点；「重试」额外
      `refetchPendingCount(true)` —— 退回 `open` 的那一扇随即属于待确认队列，
      不重拉的话老师刚放回去的门在另一张表上看不见，会以为没生效再点一次。
    - **D1 的一处例外**：那一页的文件头写着「决定不在这里做，行内放批准按钮会做出
      一个假控件」。D1 的理由是批准的**唯一**通路必须能叫醒 agent 续接；这张卡的两个
      动作不执行任何动作、也不续接，只是把状态对齐到事实，压根没有"聊天页那条通路"
      可用 —— 所以按钮在这里是真的。已在文件头写明这条例外，免得被当成违规删掉。
    - **顶栏那颗提示（老师拍板：单独一个标识，不并进待确认胶囊）**：
      `ChatPage` 加一颗 **warning 色**的「结果待核实」，**只在有货时出现**。
      为什么不并进上面那颗蓝胶囊：那颗数的是「在等**你决定**的门」，这颗数的是
      「在等**你裁定**的门」—— 并成一个数，老师看见「3」点进去可能只找到 1 扇
      待确认的（另 2 扇是待裁定的），反而多一次困惑。
      **它尤其不能省**：这一态成因是进程崩在重放中途，老师不会主动去找它 ——
      没有这点提示，那张卡就等于不存在。
      数据走新的 `frontend/src/lib/use-in-doubt.ts`（照 `use-review-due` 那个先例：
      另一个数、另一份 store），**不并进 `use-pending-count`** —— 那份的既有用例
      钉着「一次重拉只发一份请求」，而且它的 D2/D13 口径是"待确认"的。
    - **抽取**：`pending_gates` 的卡片拼装提成模块级 `_gate_label_sources` /
      `_gate_item`，两处共用（不是为抽象而抽象，是两处真要用）。抽完 26 条既有用例照绿。
    - ⚠️ **还没想过的两处后果**（记下来，没改）：① `close_conversation` 只收
      `status=='open'` 的门，`in_doubt` 的会留下 —— 它会出现在新清单里、链接指向
      已删除的会话。但归属行不删，所以仍落在原老师名下、**不跨教师露出**（已查证）；
      而且"留着"本身站得住：删聊天并不能回答那次动作跑没跑。② 1.8 的同源校正把
      `in_doubt` 门的计划步改成 `pending`（说「没开始」，而事实是「不知道」）。
    - **验证**：`tests/test_in_doubt_gates_endpoint.py` 12 条（含两条**反向钉子**：
      草稿已 `sent` / 已 `superseded` 的 in-doubt 门**必须照常出现** —— 那一类恰恰是
      最该报的，谁把 `pending_gate_criteria` 套上来就会整批筛掉，而空列表不报错）；
      前端 `tests/pending-gates-page.test.tsx` 加 4 条（两个动作真的打到端点、
      「重试」会把门放回队列、空列表不出这一段）。

- [x] 2.6 `agent_gates` 加 `args_hash` + `schema_version`，重放前校验
  —— **见上文 2.6 的完整条目**（此处原是一份重复的第二条目，无附加内容）。

- [x] 2.8 **`_plan_completion_events` 没过 `project_transitions`**（2026-10-17 从影子库挖出来的）
  - **症状**：重放成功后计划卡不动 —— 内存态 `completed`、SSE 事件也发了，但
    `plan_runs.steps` 里那一步**还是 `running`**。端到端终态断言读库，于是红。
  - **根因**：`agent/channel/langgraph_channel.py` 的 `_plan_completion_events`
    算出 `compute_replay_transition` 的转移后**只 append SSE 事件，从不落库**。
  - **它违反的是本模块自己写的原则**：`plan_run_service` 文件头那句「收权：所有
    调用面只算转移（纯函数），**投影与持久化走这一个入口**（`project_transitions`）」
    —— 2026-09-21 那次架构审查专门收敛过，而这是**唯一没走那个入口的调用面**。
    「重放成功但计划卡不动」这类 bug 有固定滋生地，原因就在这里。
  - **修法**：让它走 `project_transitions`（一行级别的改动 + 一条红用例）。
  - **附带证据**：同一次影子库盘点里，`g-draft-85554f18ff054e49` 的
    `plan_step = 4` —— **门绑步在真实系统里生效了**；前 101 行存量门全是 `None`。

- [x] 2.11 **批准之后 persona 被静默降级成 `tutor`，teacher 才有的工具当场消失**（2026-10-17 结案）
  - ⚠️ **标题原先写的是「意图路由按关键词裁工具」—— 查完了，两处都不是。**
    本会话按 HANDOFF 的指示「先取证再下结论」，在组装工具清单处加了一行日志
    （`langgraph_agent_v2.create_chemai_agent` 打印真正下发的工具名），跑一次端到端，
    答案一次性到手：

    | | persona | domain 工具数 | 有那两件吗 |
    |---|---|---|---|
    | 轮 1 `/stream` | `teacher` | 46 | ✅ |
    | 轮 2 `/resume`（老师批准第一扇门之后） | **`tutor`** | **21** | ❌ |

    **模型两轮都没说谎**，第 2 轮那句「这两个计划内的技能名在环境里不存在（非有效工具）」
    是**如实汇报当下手里有什么**。不是它被 `error` 键名带偏（2.11b 的推断也不成立）。

  - **机制**：老师的真实点击走前端 `postStream()`，它把 persona 默认成 `'teacher'`
    并**总是**发出来（`agent-sse.ts:252`）；服务端 `ChatRequest.persona` 的默认值是
    `"tutor"`，而对 teacher 角色 `"tutor"` 是**合法** persona
    （`_PERSONAS_BY_ROLE["teacher"] = ("teacher", "tutor")`），于是
    `_resolve_persona` 里 `requested or allowed[0]` 那行的兜底**永远轮不到**。
    任何漏发 persona 的调用方（旧缓存前端、第三方、脚本）都会被静默降级。
  - **修法**：`ChatRequest.persona: Optional[str] = None`（缺省 = 「没指定」，
    交回角色决定）。`_resolve_persona` 一行没动 —— 它本来就是对的，是被上游默认值挡住的。
    受影响面只有教师类角色（学生/家长端 'tutor' 本就不在允许集，结果不变）。
  - **顺带查清的两件事**：
    - 网关 `IntentClassifier` 的工具清单**对模型手里那份毫无影响**：
      `intent_hints` 是 `create_chemai_agent` 从不读的参数；
      `pre_routed_agent` / `suggested_tools` 写进图状态后**全仓库无人消费**（v1 残留）。
      「路由器裁工具」这条线整条是空的。
    - agent 层的 `logger.info` **一条都没打出来过** —— 全仓库没有一处
      `logging.basicConfig`，root 停在默认 WARNING。这正是上一轮「看不见清单只能读代码推」
      的根因（连错三次）。现在 `app/main.py` 加了一行 env 门控的 basicConfig
      （默认 WARNING = 行为不变，排查时 `CHEMAI_LOG_LEVEL=INFO`），
      端到端脚本把服务端日志落 `evals/_last_e2e_server.log` 并在结尾抄出工具清单。
  - **验证**：端到端 7 步全 `completed`，四扇门全部收场，A1/A2/A3/A4/A6 绿。
    `tests/test_persona_resolution.py` 钉住（缺省按角色取 / 显式仍算数 / 越权降回）。

  <details><summary>原来的推断（留档：这条线整条是错的）</summary>

  - **模型的证词**（端到端第 2 轮，它的原话）：
    「抱歉，前两步我误用了不存在的工具（`assign_adaptive_practice` /
      `generate_parent_report` 无效）。改用现有工具继续完成：布置一天练习 → 生成家长报告。」
    —— **它没说谎，那两件工具确实不在它手里。**
  - **机制**：`agent/gateway.py:292-340` 的 `_keyword_fallback` 按**子串**挑工具：

    | 工具 | 命中词 | 老师原话 |
    |---|---|---|
    | `assign_adaptive_practice` | 自适应 / 布置练习 / 针对性练习 | 「**布置一天的练习题**」——中间隔了「一天的」，**不匹配** |
    | `generate_parent_report` | 发给家长 / 家长报告 / 学习报告发给家长 | 「**同步给她的家长**」——**不匹配** |
    | `generate_learning_plan` | 学习方案 | 「个性化**学习方案**」——命中 ✅ |

  - **实测**：拿原话喂 `IntentClassifier._keyword_fallback`，得到
    `['show_exam_workbench','generate_questions','weekly_report','generate_learning_plan']`
    —— 正缺那两件。
  - **更普遍的设计问题**：`if not tools: tools = None`（一个都没命中才放开全部工具）。
    **「命中一部分」比「一个都不命中」更危险** —— 后者放开全部，前者给出一个被裁过、
    恰好少了关键工具的清单。老师换个说法（「布置练习」→「布置一天的练习题」）
    就能让整条链路瘫掉。
  - ⚠️ **2026-10-17 修正：上面的机制说反了。** 关键词**不是「裁」，是「加」**。
    `classify()`（`gateway.py:227-242`）的真实形状是：

    ```
    LLM 分类 ──▶ llm_result ─┐
                              ├─ 并集合并 ──▶ _route_agent ──▶ 子 agent
    关键词兜底 ─▶ kw_result ──┘
    ```

    合并那行是 `merged = list(set(llm_result.tools + kw_result.tools))`，注释写着
    「LLM 可能漏掉关键工具」—— **关键词只往 LLM 的清单里加，从不删**。
    所以「路由按关键词把工具裁掉了」这个说法不准确。真实情况只能是两者之一：
      a. **LLM 分类那一步自己就漏了**这两个工具，关键词兜底也没补上（原话不含那两个词）
      b. **LLM 调用失败 / JSON 解析失败**，整条走了纯关键词兜底

    先前的探查只覆盖了 `_keyword_fallback`（兜底那条路），没覆盖主路径 —— 结论下早了。

  - **治本方向据此改**：不是「补关键词」，也不是「命中一部分就别裁」，而是
    **工具清单根本不该是个限制**。路由器该做的是选**子 agent**（`_route_agent` 已在做），
    子 agent 该握着它职责内的**全部工具**——拿一份靠关键词/LLM 猜出来的清单去卡它，
    无论猜得多准都是拿估计当事实，而模型只能如实汇报「工具不存在」。
  - **新的入口**：**`_TOOL_TO_AGENT` → 子 agent → 工具清单**这条线。
    要追清「最终下发给模型的清单在哪一行拼出来」（`_route_agent` 只设子 agent 名，
    清单是后面某处按 agent 名筛的）。`_keyword_fallback` 只是兜底，不是主嫌。
  - **它不是门的问题**，是网关路由的问题 —— 但它是这条链路跑不通的**最后一环**。

  </details>

- [x] 2.11b **验收脚本 A5 断言查错了表**（2026-10-17，与 2.11 同一次端到端暴露）
  - **症状**：persona 修好之后链路 6 步全 `completed`、四扇门全部收场、模型汇报闭环，
    但 A5「第 5 步没生效」**照样红**。
  - **根因**：`assignment_service.assign` 按人头分两条路——`len(students) <= SYNC_LIMIT(5)`
    走**同步路**，只写 `exam_records` 一行（`_assign_one`：`record_id = "asg_<摘要>"`、
    `type = PRACTICE`、`question_stats.student_id`），**根本不动 `assignment_batches`**；
    那张表是 **>5 人异步批次**的进度源。原断言拿异步批次的表去验同步路的活，
    做成了也永远查不到。
  - **危害不只是假红**：它把「第 5 步真没做」的观感钉在验收器里，与 2.11 的真 bug
    混成一条线索 —— 上一轮「六个断点全修完链路仍跑不通」的判断有它一份。
  - **修法**：改查 `exam_records`（`record_id LIKE 'asg_%'` + `question_stats LIKE '%学号%'`
    + 30 分钟新鲜度）。新鲜度这一条已对着真库验过：库里 `asg_` 记录最大 `created_at`
    是 2026-10-14，不会被误判为新。
  - **验证**：端到端绿；跑中直接查影子库，该生新记录 `asg_5fd427362d35ad80`
    （6 题、`status: assigned`）确实在。

- [x] 2.9 ~~续接提示不提计划~~ **✅ 已结案：诊断错了，没改代码**（2026-10-17 取证）
  - **取证方法**：把四次绿运行里模型**每次续接后的原话**抠出来，而不是读提示词推。
  - **证据**（四次一致，逐字）：
    「学习计划已确认发送 ✅ **现在继续第 5 步**：布置一天的针对性练习。」
    「第 5 步完成 ✅ …… **继续第 6 步**：生成家长报告。」
    「学习计划已确认发送。**继续完成剩余的**布置练习和家长报告两步。」
    「练习已布置完成。**继续第 6 步**：生成家长报告。」
  - **结论**：模型偏离计划**是因为手里没有工具**（2.11），不是因为提示叫它收尾。
    续接提示原文一个字没改。
  - 下面那段是当时的推断，留档：
  - **现象**：老师批准、重放成功后，模型不再执行计划第 5 步
    `assign_adaptive_practice`，改调 `generate_questions` + `save_to_bank` + `write`
    想从别的路凑「布置练习」。三步都成功，它认为自己干完了，收工。
    第 5 步永远是 `running`，家长通知跟着落空。
  - **`langgraph_channel.py:51-57` 的续接提示全文里没有一个字提到计划**：
    「…请基于该结果继续完成老师的请求，**给出明确的最终答复**。如果结果中包含
     错误，请向老师说明失败原因并给出下一步建议，**不要重复调用刚才的工具**。」
  - **两个可疑点**：①「给出明确的最终答复」是收尾指令，而模型此刻正处在计划中途；
    ②「不要重复调用刚才的工具」可能被泛化成「刚才出错的工具别再碰」——
    而 `assign_adaptive_practice` 第 1 轮正是被门拦下（出错）的那个。
  - ⚠️ **这是推断，不是证据**。已确认的事实只有：续接提示不提计划、模型确实没再试
    那个工具。**下一步是取证**：让 `evals/e2e_long_chain.py` 的 `show()` 把模型的
    **文字输出**也打出来（现在只打 tool_call / gate_open / error），看它自己怎么说的。

- [x] 2.10 **被门拦下的调用占掉了工具调用配额**（2026-10-17 查 2.9 时撞出来的）
  - **机制**：`GuardState` 三个计数器撤销不一致——`seen_calls` 有 `forget_call`，
    `call_counts` **完全没有撤销**。`check_limit` **无条件累加**，于是被门拦下、
    **根本没执行**的那次调用，永久占掉了配额（`assign_adaptive_practice` 上限只有 1 次）。
  - **后果**：模型重试时收到「已调用 2 次（上限 1 次），**请基于已有结果继续**」
    —— 一句直接劝退的指令。
  - ⚠️ **易修错的点**：`check_limit` 在 `check_dedup` **之前**跑。只把 `forget_call`
    补到 `_waiting_out` 那条路上没用——配额那道关先撞上，两个痕迹必须一起撤。
  - **已完成**：新增 `GuardState.undo_unexecuted_call(name, kwargs)`（同时撤去重键 +
    计数），挂在 `_waiting_out` 与 `_register_failed_out` 两个「未实际执行」出口上。
  - **验证**：`test_gate_orphan.py::test_gate_blocked_call_can_be_retried_after_the_gate_closes`
    跑 → 失败 ✅（`已调用 assign_adaptive_practice 2 次（上限 1 次）`）→ 修后转绿
  - ⚠️ **它不是 2.9 的原因**：模型压根没再发起那个调用，护栏说什么它都听不到。
    这条是独立查出来的真缺陷，修了，但没解开 2.9。

**批次出口**：端到端脚本的「无孤儿门」断言转绿。

✅ **2026-10-17 达成**：`evals/e2e_long_chain.py` 全绿（6 步全 `completed`、四扇门全部收场、
A1–A6 全过）。最后两环是 2.11（批准后 persona 降级）与 2.11b（验收脚本查错表）。

---

## 批次 3 · 收敛与对账（存量 + T17）

- [x] 3.1 T17 每日作业挂进程内调度器：收敛假门 + 修 `plan_runs`（门与 run 一致性一起管）
- [x] 3.2 一次性修复脚本 `scripts/repair_gates.py`：陈旧门 → `superseded`；卡死的 run → 校正步骤状态
- [x] 3.3 **跑之前先 `python scripts/db_checkpoint.py`**（仓库纪律），跑完再 checkpoint

**已完成，2026-10-17。但本批的计划原文有一处判断错了，值得记：**

### ⚠️ 「41 行陈旧门」不是陈旧门

原文（以及 HANDOFF）把库里那批 open 门当成存量垃圾。**实测不是。**
按 `pending_gate_criteria` 数一遍：

| | 扇数 | 是什么 |
|---|---|---|
| open 门总数 | 74 | |
| **算数**的 | **41** | 40 布尔 + 1 草稿。**真实的待老师决定项** —— 全是 `t-` 老师会话、最近 **1–3 天**产生<br>（工具分布：`assign_adaptive_practice` 15 / `share_artifact` 14 / `read` 6 / `delete_bank` 2 / `find` 2 / `send_learning_plan` 1 / `bash` 1；`read`/`find` 那些带 `Desktop\...` 路径，是文件区外按次审批留下的） |
| **不算数**的 | **33** | 31 扇草稿已 `sent`/`superseded` + 2 扇草稿压根不存在。**这才是垃圾** |

两条直接后果：

1. **T17 的 14 天过期规则今天一个都收不到**（最老才 3 天）。
2. **脚本绝不能替老师作废那 41 扇** —— 那等于替老师扔卡片。它们的出路是待确认页，
   或满 14 天后由 T17 收。

**顺带纠正一个我自己的误判**：我把这次改动说成"C1-Q3 修好后 41 个会话被锁死，得紧急止血"。
锁是真的（修好之后那些门**确实**会拦），但**拦得没错**：那些会话本来就在等一个
没人点的决定。真正该紧急的是另一件事 —— **本该每天收假门的作业从来没挂上**
（`expire_gate` / `gate_expiry.py` 压根不存在）。

### 落地

- **`app/jobs/handlers.py`**：`expire_gate`（超期 → `superseded`，**逐扇一个工作单元**，
  单扇失败不连累其余，每条留日志）、`reconcile_plan_runs`（卡死 run 对账）、
  `supersede_stale_gates_full`、`handle_gate_expiry`（同步入口，自开会话）。
  阈值 `GATE_EXPIRY_DAYS = 14`。
- **`app/scheduler/gate_expiry.py`**（新增）：`asyncio.to_thread` 包装 —— 整轮要开好几次库、
  可能扫几十行，直接在 AsyncIOScheduler 的循环里同步跑会卡住所有请求。
- **`app/scheduler/__init__.py`**：每日 **03:00**（Asia/Shanghai）注册 `gate_expiry`
  （挑凌晨：别跟老师的使用高峰抢）。
- **`scripts/repair_gates.py`**（新增）：**调的就是 T17 那几个函数**，一行业务逻辑不重写
  —— 两份实现必然漂开。带 `--dry-run`。**刻意不跑过期那一步**：手动跑一轮时顺手把
  老师手上正在处理的卡收掉，是最不该发生的事。
- **对账的取舍**：只收「会话**没有**算数开门撑着」的 run。那扇门还在的时候，
  那一步是真在等老师，动它就是**把一个还能继续的任务提前判死**。
  落库走 `project_transitions`（仓库收权那句话：调用面只算转移，投影与持久化走这一个入口），
  **但它 `except Exception: pass`** —— 所以回读校验一遍，它说"做了"不算数。

### 实测

```
[前] open 门 74：算数 41、不算数 33      [前] 卡死的 run 30 个（共 70 个非终态步）
[①] 假门收敛 33 行 → superseded
[②] 对账 run 30 个 · 回读校验通过：这些 run 已无未终态步骤
[后] open 门 41：算数 41、不算数 0
```

独立核对（不信脚本自己的报告）：`superseded` 44 行、`plan_runs` **42 completed / 4 running**
—— 那 4 个各有一扇 `share_artifact` 活门撑着，**刻意没动**（门一旦被批，重放可能让 run
继续走；先标死会挡住恢复），交给 T17 过期后再收。

跑完 `db_checkpoint.py` 已做。`scripts/repair_gates.py` 再跑是幂等的（实测第二遍：0 假门、0 卡死 run）。

**验证**：`tests/test_gate_expiry_job.py` 8 条 —— 超期作废 + 留痕 / 未超期不碰 /
**单扇失败不连累其余** / 幂等 / run 收口 / `awaiting` 无门记 `cancelled` /
**反向钉子「有活门的 run 一个字都不许改」** / run 对账幂等。
另单独验过 `init_scheduler` 真把 `gate_expiry` 注册上了（全量测试覆盖不到那一行）。

---

## 批次 4 · 文档

- [x] 4.1 `CONTEXT.md` 的 Confirmation Gate 词条改真：补「重放载荷以数据为唯一真源」「门与计划步骤同源」「三态含 in-doubt」
- [x] 4.2 ADR-00xx：确认门的重放载荷与提交主权

**已完成，2026-10-17。**

**4.1** —— `CONTEXT.md` 的 Confirmation Gate 词条（**英文**，那份词条体例就是英文）
补进三段，都写成「是什么」而不是「怎么实现」（词条开头的体例要求）：

- **门是行不是内存**：它带着要跑的动作与参数，决定重放的就是那一份载荷 ——
  进程里存的东西**不被信任**来说"批准的是什么"。
- **门记着它属于哪一计划步**：决定与重放都从门定位那一步，不靠对工具名。
- **三态**：`open` →（被占，结果未知）`executing` → `executed`；动作失败退回 `open`
  （老师可重试，而不是发现门已被消费）；进程死在重放中途留下的 `executing` 行，
  下次启动落成 `in_doubt` —— **只有人能裁定**，因为动作到底跑没跑读不出来。

另在末句补了「门开着就全拦」**跨重启也成立**（判据问记录，不问内存）。

**4.2** —— 新增 `docs/adr/0030-gate-replay-payload-has-one-source.md`
（「确认门的重放载荷以数据为唯一真源」）。Date 取 **2026-04-16** ——
仓库有过一次「全部成文日期统一前移 6 个月」的提交，最近一条 ADR-0029 是 2026-04-07，
按同一约定换算今天（2026-10-16）。

三条最值得留在 ADR 里的：

- **为什么不做成"只校验、不改来源"**：那样只能说"发现两者漂开了"，而**让漂开不可能
  发生**更好 —— 你不信任的第二个来源，直接不使用它。
- **一处由此产生的冗余**：重放改用实体后，2.6 的第二道校验在这条路上**恒真**。
  别指着它挡"内存与实体漂开"。
- **一条教训**：把一道闸从松改成紧，**得同时清点它现在会拦住谁** ——
  修好 C1-Q3 的重启缺口之后，库里 41 扇陈旧门从"无害历史行"变成"真的锁死会话"，
  本 ADR 直接催生了 T17。

---

## 每个批次结束都要跑的三道闸

```bash
python -m pytest tests/ -q                    # 全量单测（基线 1632）
python evals/run_evals.py --fast              # fast 门禁 137 断言
python evals/e2e_long_chain.py                # 端到端验收（批次 0 建的）
```

**三道全绿才算批次完成**，任一红就停在那批修。

### 三道闸的当前实测（2026-10-17）

| 闸 | 结果 | 说明 |
|---|---|---|
| ① `pytest tests/ -q` | ✅ **1685 passed, 5 skipped** | 本会话新增五个文件合计 30 条（`test_persona_resolution` 5 / `test_gate_replay_payload_pin` 7 / `test_in_doubt_gates_endpoint` 12 / `test_gate_blocks_after_restart` 3 / `test_gate_expiry_job` 8）。另外 2.4 收尾时**改写/退休**了十几条既有用例（见 2.4 条目），所以总数不能简单相加 —— 以实测 1685 为准 |
| ② `run_evals.py --fast` | ⚠️ **138 条 / 137 通过 / 1 失败**，GATE BLOCKED | **唯一的红与本批无关**，见下 |
| ③ `evals/e2e_long_chain.py` | ✅ **绿 —— 长链路端到端跑通**（2026-10-17 结案） | 6 步全 `completed`、四扇门全部收场、A1–A6 全过 |
| ④ 前端 `npx tsc --noEmit` | ✅ 干净 | |
| ④ 前端 `npx vitest run` | ✅ **436 passed**（53 文件） | 含 `pending-gates-page.test.tsx` 新增 5 条；另有 1 条**既有的故意抛异常**（`ui-upgrade` 验 ActionButton 上抛），与本批无关 |

**② 那条红的归属**：`[FAIL] [L1] ui_v23 → ui-v23 回归: text-[11px] 不回潮`。
全仓库只有两处 `text-[11px]`，都在
`frontend/src/pages/mobile/student/ProfilePage.tsx`（260、298 行）——
**那是会话开始前就在工作区里的未提交改动**（学生端通知红点 `data-testid="notif-unread"`
与那行 `sent_at`），不是我这一轮碰过的文件。修它属于那份改动的收尾，不在本变更范围内。

**② 的另一条警告**（不影响门禁，归属已确认）：
`tool 'send_class_notification' missing from L0 coverage manifest (evals/tool_coverage.py)`

2026-10-17 查明：`send_class_notification` 定义在 `agent/tools/notification.py`，
而该文件**尚未纳入 git**（`git ls-files` 无此文件）—— 属于并行工作中的新增工具，
漏登记的是那份工作。本变更没增删过工具。**收尾动作在那边**：把它登记进
`evals/tool_coverage.py`（按 Runner 的约定，新增工具必须同步登记，否则快跑层一直提醒）。

---

## 风险与回滚

| 风险 | 处置 |
|---|---|
| 改 `plan_run_service` 影响所有长任务的 todolist | 1.1–1.3 三个红用例先钉住语义；`tests/` 里有既有 Plan Run 用例兜底 |
| 删 `pending_approval` 触及重放主路径 | 2.4 单列一步，前面 2.1–2.3 已把语义钉死；`evals --fast` 的工具轨迹断言会抓回归 |
| 改 `send_draft` 影响 6 个调用点 | 正是目的；用 `tests/` 全量 + 端到端脚本兜 |
| 写演示库 | 已有备份；批次 3 前再 checkpoint 一次 |
| `--reload` 下改代码会中途重启 | 批次内改完再跑验收，不在流式跑动中改 |

**回滚**：每批次一个 commit；库回滚用备份目录。

---

## 执行杂记

- **2026-10-16**：会话 `8f744bf6` 在写完 0.2 脚本后进程崩溃（后端 8001 被一并带走）。
  本文件即由该会话的聊天记录恢复落盘 —— 此前计划只存在于对话中，无任何持久化。
  恢复后重跑 0.2，结果与崩溃前预测一致（红）。

### 待办：工件列表的排序在同一时钟 tick 内是随机的（**本次不修**）

0.3 跑基线时发现的一条**既有 flake**，与本变更无关，但根因是真实产品缺陷，记在这里：

- **症状**：`tests/test_artifacts_api.py::test_list_scoped_to_owner_and_filterable_by_kind`
  全量跑偶发红（首次 1 failed / 1631 passed），单独跑 20 次全绿。
- **根因**（已实测坐实）：
  1. `artifact_service.save_artifact` 用 `now = datetime.utcnow()`（`artifact_service.py:74`），
     而**本机该调用粒度约 15.6ms**（连续 8 次调用返回同一个值，distinct = 1/8）。
  2. 同一 tick 内保存的两件工件 → `updated_at` **完全相同**。
  3. `list_artifacts` 的排序是 `order_by(Artifact.updated_at.desc(), Artifact.id.desc())`
     （`artifact_service.py:151`）——平局时落到 `id` 兜底，而 `id` 是
     `f"art_{int(time.time()*1000):x}_{secrets.token_hex(4)}"`（`artifact_service.py:76`），
     **含随机后缀** → 平局即掷硬币。
  - **复现**：冻结时钟强制平局后跑 20 轮，「教案」排在「动画」后面 **4 次**（顺序随机）。
- **影响面**：老师端「工件列表」在两次保存落在同一 tick 时，刷新顺序会在两次调用间跳变。
  其余 ~15 处 `order_by(X.created_at.desc())`（`exam.py:110`、`parent.py:312`、`question.py:1495` 等）
  **也没有平局兜底**，只是没被测试钉住，所以没人发现。`artifact_service` 是全仓库唯一加了
  兜底、而兜底恰好建立在随机值上的地方。
- **本次处置**：只修测试 —— 用例显式给「教案更晚」的时间戳，不再依赖时钟粒度，
  让测试只测它要测的东西（归属隔离 / kind 过滤 / 最近更新在前）。
- **待修**（单独一轮，需决定排序契约）：同 tick 平局应该按什么排？
  候选是 SQLite `rowid`（落库顺序）或加自增列；涉及可移植性与 schema，不在本变更范围内。
