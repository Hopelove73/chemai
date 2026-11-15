# AgentChat 任务对话穷举清单

> 范围：`chemai-backend` 里的多角色 agent 对话入口（persona: teacher / student / parent / tutor）。
> 方法：4 个只读探索子智能体并行盘点（教师端 / 学生端 / 对话引擎 / 评测与样本），交叉复核后合并；标 ✓ 的条目由 lead 直读原文核实，其余来自子智能体报告（均带 file:line）。
> 路径均相对 `chemai-backend/`。生成于 2026-10，仓库工作区当时有 33 项未提交改动，本文件为纯新增。

---

## 0. 分类判据（先读，否则会误判）

**不要按"看起来多复杂"分，要按"用户要发几次话"分。**

| 形态 | 用户发话次数 | 代码里是什么在强制 |
|---|---|---|
| **A 单次** | 1 | 无门、参数齐。单轮内可串 N 个工具（`recursion_limit: 24`，`channel/langgraph_channel.py:142` ✓） |
| **A-multi** | 1 | 同上，只是一次输入里连调 ≥2 个工具（ReAct 单轮内完成） |
| **B 多轮** | 2~6 | ①确认门 ②强制澄清 ③persona prompt 分层协议 |
| **C 长对话拆解** | 多轮 + **跨中间产物/跨页面** | `plan_task`→`PlanGenerator` 结构化计划 + `plan_runs` 实体 + 学生 `_route` 接棒 |

三类强制的代码出处：

- **确认门**：`TOOL_APPROVAL_REQUIRED = {assign_adaptive_practice, delete_bank, share_artifact}`（`agent/guard.py:41` ✓）；生成即门 `_GATE_OPEN_TOOLS = {generate_learning_plan, generate_parent_report, parent_assign_task}` + 配对 `_GATE_OPEN_PAIRS`（`guard.py:45-53` ✓）。门开着时 agent 一切工具调用被拒（`guard.py:415-432`）→ 天然断点。
- **强制澄清**：`check_prerequisites` 对 search_exam_bank / diagnose_barrier / weekly_report / assign_adaptive_practice 声明必需信息（`guard.py:305-341`）。
- **prompt 分层**：学生三层协议（`agent/personas/student.yaml:16-38`）。

**C 类的硬证据现状：只有 2 条被断言钉住**（`evals/agent_eval_golden.yaml:648-666` 的 plan-complex-task、`golden_workflow.yaml` WORKFLOW-001~004 中真正跨轮的）。其余"像 C"的只写在 `docs/PRD-智辅化学-Agent产品需求文档.md` 的时序图里，没有断言。

> ⚠ 常见误判：`搜索真题→据真题出3道类似题`、`出题→保存`、`诊断→布置练习` 是**单轮内连调两个工具**（A-multi），不是 C。它们只发一次输入。

---

## 1. 教师端（persona=teacher，入口 `/chat` Agent 工作台）

可用工具 = `teacher.yaml:29-75` ∩ `TOOL_META[personas含teacher]`（`agent/langgraph_agent_v2.py:231-242`）。工具路由靠四段式 docstring，**没有意图分类树**。

### 1.1 A · 单次交付（19 条）

| # | 任务 | 工具 | 证据 |
|---|---|---|---|
| 1 | 出题（知识点/难度已给） | `generate_questions` | `teacher.yaml:8`、`tools/generation.py:122-134` |
| 2 | 「我要出题」但没说知识点 → 弹内联面板 | `show_exam_workbench` | `teacher.yaml:9`、`tools/exam_bank.py:393-409` |
| 3 | 搜高考真题 | `search_exam_bank` | `tools/exam_bank.py:39-50` |
| 4 | 联网查考纲/政策 | `web_search` | `exam_bank.py:263-272` |
| 5 | 班级/学生薄弱点取数 | `diagnose_barrier` | `tools/diagnosis.py:76-87` |
| 6 | 诊断结果出图 | `show_diagnosis` | `diagnosis.py:312-336` |
| 7 | 花名册 / 谁最薄弱 | `show_students` | `diagnosis.py:343-355` |
| 8 | 学生周报 | `weekly_report` | `diagnosis.py:571-715` |
| 9 | 给全班+家长发通知 | `send_class_notification` | `tools/notification.py:13-28`、`teacher.yaml:19-21` |
| 10 | 教研问答（怎么教这个考点） | `chemistry_tutor`(teacher 分支) | `tools/tutoring.py:188-217` |
| 11 | 配平方程式 | `balance_equation` | `tools/__init__.py:190` ✓ |
| 12 | 模拟实验 | `simulate_experiment` | `tools/simulation.py:10-51` |
| 13 | 题库列表 | `list_banks` | `exam_bank.py:524-530` |
| 14 | 考试列表 | `list_teacher_exams` | `tools/__init__.py:202` ✓ |
| 15 | OCR 进度查询 | `query_ocr_progress` | `tools/ocr_grading.py:9-17` |
| 16 | 成绩查询 | `query_batch_grades` | `ocr_grading.py:167-176` |
| 17 | 待确认判卷队列 | `query_grading_queue` | `ocr_grading.py:306-315` |
| 18 | 学生长期记忆 | `memory_student_get` / `memory_teacher_get` | `tools/memory.py:18-51` |
| 19 | 区内文件读写改查（6 个工具各算一条入口） | `read/write/edit/find/grep/bash` | `teacher.yaml:23-27`、`tools/filesystem.py:54-60` |

另有 **纯跳页**（`打开诊断页`/`查看学生列表`）：分类器判 navigate 后**零工具零模型回合**直接短路（`channel/langgraph_channel.py:456-462`）。

### 1.2 A-multi · 一次输入连调多工具（5 条）

| 任务 | 链 | 证据 |
|---|---|---|
| 搜真题→据真题出变种 | `search_exam_bank`→`generate_questions` | WORKFLOW-001（`tool_trajectory_order` 断言） |
| 出题→保存 | `generate_questions`→`save_to_bank` | WORKFLOW-003 |
| 扫题库→分析易错点 | `search_exam_bank`→`diagnose_barrier` | WORKFLOW-004 |
| 诊断→出题 | `diagnose_barrier`→`generate_questions` | `agent_eval_golden.yaml:636-645` |
| 读文件→改→bash 验证→grep 自查 | read/edit/bash/grep | 单轮 24 步配额（`tools/__init__.py:244`） |

### 1.3 B · 用户必须再发一次（13 条）

| # | 任务 | 哪来的第二轮 |
|---|---|---|
| 1 | 生成学习计划 → 打回/批准 → 修订第 2 版 → 再确认 → 发送 | 生成即门 + 草稿链（`guard.py:45-53` ✓、`diagnosis.py:912`） |
| 2 | 生成家长报告 → 预览卡 → 批准才发 | 同上（`send_report_to_parent`） |
| 3 | 布置自适应练习 | 布尔门 `assign_adaptive_practice` |
| 4 | 删题库 | 布尔门 `delete_bank` |
| 5 | 分享工件给学生 | 布尔门 `share_artifact` |
| 6 | 答案卡批改：先要标准答案 → 批 → 存成绩 | ⚠ 仅 docstring 约定（`ocr_grading.py:78-79`），**无代码强制** |
| 7 | 导入 PDF/图片题库：解析 → 老师确认 → 入库 | `tools/parser.py:31-43` |
| 8 | 成绩确认生效 | `query_grading_queue`→`confirm_exam_grades` |
| 9 | 做讲评稿：必须先有批改数据 | `context_data` fail-closed（`teaching_skills/__init__.py:94-98`） |
| 10 | 建知识点先修图：提议 → 逐边采纳 | `tools/knowledge_map.py:56-93` |
| 11 | 读工作区外文件 → 批准卡 → 重放 | `guard.py:519-543` |
| 12 | 同名学生会话 → 要学号 | `data_scope.py:136-144` 多匹配 |
| 13 | 无知识点出题 → 选项式追问一轮补齐知识点/题型/难度 | prompt 契约（`teacher.yaml:9`、`tutor.yaml:16`）+ 评测断言 `panel-show-on-exam-intent` |

### 1.4 C · 长对话拆解（3 条 + 2 条存疑）

| 任务 | 机制 | 证据 |
|---|---|---|
| 准备一次月考（诊断→按薄弱点出10题→复习计划） | `plan_task`→`PlanGenerator` ≤8 步 | `evals/agent_eval_golden.yaml:648-666`、`agent/tools/plan.py:12-69` |
| 出本单元整套复习资料 | 同上一句话多产出，`depends_on`/`${step_N.field}` 跨步取产物 | `agent/planner.py:9-44,106-161`、`teacher.yaml:14-16` |
| 教学动画多版本迭代（"改颜色"→"换个例子"） | 同 skill + 同主题追加 artifact 版本 | `tools/teaching.py:10-29` |
| 批答题卡全流程 | 存疑：仅 PRD 时序图，且 chat→OCRTask 未找到写入路径 | `docs/PRD...:423-442` |
| 出题+四维审核+发布到班级 | 存疑：REGISTRY 无"审核""发布"技能 | `docs/PRD...:444-463` |

### 1.5 声称有、实际没有

- **教案 / 课件 / 评语**：docstring 与 PRD 反复提，但 `teaching_skills/__init__.py:122-155` REGISTRY 只有 `teaching_animation / expression_training / review_deck` 三个。
- **导出 PDF**：`agent/` 内 grep export 只命中 spec 文档自身；前端导出走 `app/api/artifacts.py:189-218`（工件→pptx）。
- **班级周报**：`weekly_report(class_name)` 的 `class_name` 是**死参数**（`diagnosis.py:571` 签名有、函数体从未引用）。
- **把工件发给家长**：`teaching.py:194` 明写 NOT for。

---

## 2. 学生端（只有 student persona；tutor 学生不可达）

**范围校正（两条前置事实）**
1. `tutor` 学生不可达：`channel/langgraph_channel.py:347-355` `_PERSONAS_BY_ROLE` 里 `student: ("student",)`；`personas/tutor.yaml:6-9` 自述只给教师后台/离线评测。
2. student 实际 13 个域工具 + 5 个 browser 工具，**没有** `balance_equation / generate_questions / search_exam_bank / show_exam_workbench / plan_task / diagnose_barrier`（`tools/__init__.py:178-286` ✓）。

> 因此 `agent/spec_student.md:319-328` 与前端 chip 宣称的「配平」「出题」「诊断」**都不是学生能发起的任务**，见 §2.4。

### 2.1 A · 单次（6 条）

| # | 任务 | 工具 | 证据 |
|---|---|---|---|
| 1 | 概念讲解（"什么是盐类水解"） | `chemistry_tutor` | `tools/tutoring.py:188-238` |
| 2 | 实验现象/步骤/安全 | `simulate_experiment`（一次 LLM 生成图文报告，非交互仿真） | `tools/simulation.py:10-51` |
| 3 | 我掌握得怎么样 / 薄弱点 | `invoke_learning_skill(mastery_report)` | `app/services/learning_skills/__init__.py:204-229` |
| 4 | 今天还有多少要复习 | `review_today(action=status)` | 同上 `:116-125` |
| 5 | 联网查资料 | `web_search` | `tools/__init__.py:180` ✓ |
| 6 | 我以前诊断过什么 | `memory_student_get` | `tools/memory.py:18-51` |

### 2.2 B · 多轮（5 条）

| # | 任务 | 强制来源 |
|---|---|---|
| 1 | 贴题求带（首轮绝不讲解，先问"你做到哪一步了"） | **只靠 prompt**（`student.yaml:16-38`）；工具返回值里没有答案字段 |
| 2 | 六类专项导学（离子/物质的量/氧化还原/平衡/周期律/有机） | 工具层机械 **2 档**状态机：无 `student_input`→只回 `guidance`；有→回 feedback（`tools/tutoring.py:20-37`） |
| 3 | 对话里作答 → 自动判卷 + 落学情（隐式，不是用户任务） | `_capture_chat_answer`→`grading_center.capture_chat_answer`（`tutoring.py:45-68`、`grading_center.py:719-772`） |
| 4 | 完整讲解 + 变式闭环（L3 后仍卡才允许） | prompt 强制「必须接 variant_training」（`student.yaml:30-34`） |
| 5 | 确认/打回老师发的学习计划 | `decide_plan_draft`（`tools/plan.py:72-146`）；⚠ 学生拿不到 `draft_id` |

### 2.3 C · 长对话拆解（5 条）—— 任务完成条件在对话之外

| # | 任务 | 接棒 | 证据 |
|---|---|---|---|
| 1 | 来一轮智能练习 | `_route:/m/practice` + smartSession（IRT 选题 ≤8 题）→ 作答后回来点评 | `learning_skills/__init__.py:138-160`、`PracticePage.tsx:232` |
| 2 | 这道错题出变式 | `_route:/m/wrong`；⚠ 需 `question_id`，学生无「列我的错题」工具 | `:163-201`、`WrongBookPage.tsx:146` |
| 3 | 今日复习清卡 → "清完了" → 读真实连击 | `_route:/m/review` + `debrief` | `:100-135`、`ReviewCenterPage.tsx:112` |
| 4 | 表述特训 | 工件 HTML（逐句三色标注）→ 提修改 → 同主题续版本；错句入 FSRS 复习队列 | `tools/teaching.py:10-152`、`memory_card_service.py:130` |
| 5 | 学习计划确认/打回（跨人跨版本：学生→老师修订→再确认） | `decide_plan_draft`→`plan_draft_service`；⚠ 无学生端 UI 入口 | `tools/plan.py:90-146` |

### 2.4 不成立 / 缺口（2 条）

| 任务 | 缺什么 | 证据 |
|---|---|---|
| 化学方程式配平检查 | chip 有（`StudentChatPage.tsx:196`）、渲染器有（`tool-renderers.tsx:932,455-490`），**工具没有**（`tools/__init__.py:190` 仅 tutor/teacher）→ 学生只能得到模型自答，无 `audit_chemical_equation` 校验 |
| 拍照/OCR 题目识别后答疑 | 上传端点员工专属 fail-closed（`channel/langgraph_channel.py:2276-2285`，注释明写"学生端将来若要传图改 allow_self=True"）；学生侧唯一非文字输入是**画布作答**（`openspec/.../diagram-answer/spec.md`） |

---

## 3. 家长端（persona=parent，4 条）

| # | 任务 | 工具 | 形态 |
|---|---|---|---|
| 1 | 孩子这周学习怎么样 | `weekly_report` | A |
| 2 | 需要重点关注什么 | `diagnose_barrier`（只读） | A |
| 3 | 在家怎么帮孩子 | 无工具，纯文本 | A |
| 4 | 给孩子派任务 | `parent_assign_task`→门→`parent_send_task` | B |

素材来源：`mobile/parent/ParentHomePage.tsx:285` 三条 chip（点击即发）；`evals/.../role_scenarios.yaml` ROLE-006 钉住「家长要出题/诊断应被拦」。

家长最薄的一档：只有 3 条 chip + 2 条评测。

---

## 4. 矛盾 / 断点清单（4.1 已修复，其余待定）

### 4.1 出题契约矛盾 —— 已解决（2026-10）
- 冲突：`agent/personas/teacher.yaml:9` 原写「完全没知识点 → 调 `show_exam_workbench` 开面板，**不追问**」；`evals/agent_eval_golden.yaml:583-593` `panel-show-on-exam-intent` 断言 **not_tool_called: show_exam_workbench** 且必须反问题型/难度/知识点。
- 裁定：以评测为准（用户确认）。
- 改动：`teacher.yaml:9` 与 `tutor.yaml:16` 的两条同文规则改为「用选项式追问一轮补齐知识点/题型/难度，禁止直接调 `show_exam_workbench`（面板只在用户明确要求时打开）」。工具 docstring `tools/exam_bank.py:404` 本来就已按追问口径写（"信息不全且未明确要求面板时…先追问"），无需改。
- 收紧：`tools/exam_bank.py:402` 原写「等任何出题意图时，**立即调用！**」→「出题意图时调用；但信息不全且用户未明确要求面板时，**先追问再调本工具**」，消除与该函数 404 行自相矛盾的绝对化指令。
- 未动：`openspec/changes/agent-exam-generation-flow/design.md:48` 仍是历史设计记录（"不追问。直接打开面板"），按 openspec 惯例保留原貌。

### 4.2 v2 那套大 SYSTEM_PROMPT 对四个正式 persona 全部失效
`agent/langgraph_agent_v2.py:161-175` ✓：yaml 有 `system_prompt` 就走 yaml 分支，`:62-130` 里「计划执行到底 / 确认门必须停 / 内联零复述」等代码级规则**不会到模型**。行为全靠 yaml + 工具 docstring 兜。

### 4.3「不给答案」没有任何代码护栏
全仓 grep「苏格拉底」仅 1 命中（`student.yaml:6`）；`guard.py` 不校验提示级数。正式需求 `openspec/changes/learning-insight-smart/specs/socratic-hint-protocol/spec.md:21` 明写「不改工具集、不加路由层」，28 行说验收=评估跑分（实体 `evals/socratic_hint_protocol.yaml`，10 场景 LLM judge）。
→ **线上跑偏无人兜底，只有离线评委。**

### 4.4 数字口径不一致
| 口径 | 值 | 位置 |
|---|---|---|
| recursion_limit（真实生效） | **24** ✓ | `channel/langgraph_channel.py:142` |
| RECURSION_LIMIT（模块级） | 12 | `langgraph_agent_v2.py:47`、`guard.py:94` |
| plan 步数上限 | 24 步实现 `valid[:8]`；注释写 ≤6、docstring 写 ≤8 | `planner.py:129`、`plan.py:5`、`plan.py:17` |

### 4.5 学生端 4 个断点
配平无工具 / 对话内出题不可用（`tools/learning.py:15-16` 明写裸出题禁止）/ `variant_training` 需 `question_id` 但无「列错题」工具 / `decide_plan_draft` 需 `draft_id` 但学生收件箱通知不带。

### 4.6 上下文与长任务的实际约束
- `trim_context`：>30 条触发，保留最近 6 条 + 关键词命中 ≤10 条，其余压 ≤200 字摘要（`agent/context_manager.py:20-73`）→ **C 类早期步骤产物会从模型视野消失**，只剩 plan_run 步骤状态。
- `context_meter` 纯计量不压缩（`context_meter.py:58-88`，注释自承）。
- 单回合 `_TURN_TIMEOUT=180s`（`channel:42`），到点 `mark_run_paused`。
- 对话回合内**没有**异步队列；异步只在工具内部（`assign_adaptive_practice` >5 人 → Celery/APScheduler，`assignment_service.py:58-70`）。断线续跑靠「持久化 + 人再点一次」。

---

## 5. 数量分布

| 端 | A | A-multi | B | C | 缺口/不成立 |
|---|---|---|---|---|---|
| 教师端 | 19 | 5 | 13 | 3（+2 存疑） | 4 |
| 学生端 | 6 | — | 5 | 5 | 2（另有 3 个断点） |
| 家长端 | 3 | — | 1 | — | — |

---

## 6. 来源与置信度

| 来源 | 覆盖 | 关键文件 |
|---|---|---|
| 教师端探索（子智能体） | 29 类 + 11 条缺口 | `personas/teacher.yaml`、`tools/{generation,exam_bank,ocr_grading,diagnosis,notification,parser,knowledge_map,teaching}.py` |
| 学生端探索（子智能体） | 18 条 + 13 条待确认 | `personas/student.yaml`、`tools/tutoring.py`、`app/services/learning_skills/__init__.py`、`openspec/changes/learning-insight-smart/specs/*` |
| 引擎探索（子智能体） | 27 条机制清单 + 8 条缺口 | `agent/guard.py`、`langgraph_agent_v2.py`、`langgraph_sse_v2.py`、`channel/langgraph_channel.py`、`planner.py` |
| 证据与样本（子智能体） | 74+110 条评测用例、12 条前端 chip | `evals/agent_eval_golden.yaml`、`evals/socratic_hint_protocol.yaml`、`评测文档/evals/**` |
| lead 直读核实 ✓ | 工具 persona 白名单、确认门集合、plan_task 铁律、recursion=24、persona prompt 分支、chip 半句模板、工作区状态 | 见上文 ✓ 标记 |

**已知未覆盖**：`chemai-backend/docs/` 89 个文件中仅精读 3 个 agent-chat 相关；`openspec/changes/archive/` 56 个 proposal 未逐一读；`curriculum/` 39 个文件属教程材料，非用户任务样本，已排除。

---

## 7. C 类断言现状（P0-a 已落地）

C 类原先只有 2 条断言（plan-complex-task 只断言"调了 plan_task"、e2e 几条断言单工具），
**没有任何断言检查"计划里的步骤是否真的执行了"**。已补：

| 用例 | 位置 | 断言 | 当前预期 |
|---|---|---|---|
| `plan-steps-executed-month-exam` | `planning_scenarios` | `plan_task` + 新增 `plan_steps_executed` | **红**（修 C1 计划/执行对账后转绿） |
| `plan-steps-executed-chain` | `planning_scenarios` | `tool_trajectory_order` + `plan_steps_executed(allow_no_plan)` | 绿（不强制拆解） |
| `student-handoff-smart-practice` | `langgraph_scenarios` | `invoke_learning_skill` + `route{navigate,/m/practice}` | 绿（需 API） |
| `student-handoff-review` | `langgraph_scenarios` | `invoke_learning_skill` + `route{navigate,/m/review}` | 依赖演示数据有到期卡 |

新增断言类型 `plan_steps_executed`（`evals/test_langgraph_agent.py`）：解析 `plan_task` 工具返回值里的
`steps[].skill`，逐个要求在 `tools_called` 里出现过；`allow_no_plan: true` 时未产计划视为通过。
用合成轨迹做过 6 种情形的单测（全执行/漏一步/无计划严格/无计划放行/result 为 dict/result 非 JSON）。

**顺带修正**：`langgraph_scenarios.multi-turn-context` 原期望 `show_exam_workbench`×3，
与 `inline_panel_scenarios.panel-no-search-on-generate`（同样输入→`generate_questions`）以及
出题契约第 1 条相反，已改为 `generate_questions`×3。改后实测 PASS。

### 7.1 首轮回归实测（key 取自 `chemai-backend/.env`）

| 组 | 结果 | 说明 |
|---|---|---|
| `--boundary` | 20/20 绿 | 无 API |
| `--inline-panel` | 13/13 绿 | 含 `panel-explicit-workbench-request`（改 prompt 后仍能开面板） |
| `--planning` | 9/10 绿 | 唯一红 = `plan-steps-executed-month-exam/plan_steps_executed`，**设计如此** |
| `--langgraph` | 6/13 绿 | 6 挂是历史遗留（`interrupt()` / `request_approval` 已按 ADR-0015 退役，用例仍在测旧机制）；1 挂是新用例抓到的真崩，已修 |

**C1 现场证据**（`plan-steps-executed-month-exam` 实测轨迹）：
计划 5 步 `show_students → diagnose_barrier → show_diagnosis → generate_questions → generate_learning_plan`，
实际只执行到第 3 步就收尾：`plan_task, show_students, memory_teacher_get, diagnose_barrier, show_diagnosis`。
用户说「准备一次月考」，**出题和复习计划两步凭空消失且无任何提示** —— 这就是 §2 说的「计划/执行无对账」。

**新用例抓到的真崩**（已修）：`student-handoff-smart-practice` 首跑报
`ValueError: invalid literal for int() with base 10: 'COMPETITION'`。
根因 `app/services/smart_practice_service.py:171/:327` 对 `q.difficulty` 裸 `int()`，而库里可能存枚举名
（`app/models/database.py:116-119` 早已记录 "smart/start 对 'MEDIUM' int() 直接 500" 这个同类问题）。
修法：加 `_difficulty_value()` 走现成的 `Difficulty.coerce()`，两处调用点替换；`None` 仍按中等（3）。
修后 `start_session` 正常返回会话，用例三条断言全绿。

**建议**：`react-approval-flow` / `interrupt-resume` / `guardrail-approval-enforcement` / `guardrail-tool-dedup`
这 4 条用例测的是已退役机制，应标 `expected_fail` 或迁到 channel 层测试，而不是当成回归红灯。

### 7.2 C1 修复：收尾对账（把静默变成可见）

改动三处，**不改执行策略**（不自动续跑——那要动流式主链路，风险与收益不匹配）：

| 位置 | 改动 |
|---|---|
| `app/services/plan_run_service.py` | 新增纯函数 `compute_tail_transition(steps)`：把仍是 `pending`/`running` 的步骤标 `cancelled` 并返回转移表 |
| `agent/channel/langgraph_channel.py` | 新增 `_plan_tail_events(guard_state, cid)`：走 `project_transitions` 投影 + 落库，再补一条 text 说明「本轮有 N 步没有执行：第4步…」；挂在两个流式端点的 `finalize` 之前 |
| `tests/test_plan_run_tail.py` | 5 条纯函数单测（残留→cancelled / running 也关 / awaiting_confirmation 不动 / 全完成无转移 / 空计划） |

为什么不新增 `skipped` 状态：前端 reducer 的状态白名单是固定集合
（`frontend/src/lib/agent-stream-reducer.ts:49`），未知状态会被丢弃，新增取值要前后端同步改一版。

为什么不自动续跑：`create_react_agent` 是裸 prebuilt，续跑必须在流式端点里重入一轮，
属核心链路改造；本次先保证「不静默」，续跑作为独立一轮。

**验证**：`py_compile` 通过；`_plan_tail_events` 探针输出正确（残留 2 步 → 2 条 plan_progress + 1 条 text，
无残留/无计划→空，二次调用幂等）；`pytest tests/ -k plan` **84 passed**；`--boundary` 仍全绿。
eval 用例 `plan-steps-executed-month-exam` 标 `expected_fail`——agent 层看不到 channel 的补偿。

### 7.3 自动续跑（2026-10，有界）

`agent/channel/langgraph_channel.py`：

| 件 | 作用 |
|---|---|
| `_PLAN_CONTINUE_ROUNDS = 2` + `_PLAN_CONTINUE_MSG` | 有界轮数；注入消息带 `_INTERNAL_NOTE_PREFIX`（历史过滤依赖该约定，不会被渲染成用户消息） |
| `_plan_pending_desc(guard_state)` | 与 `_plan_tail_events` 共用判据（pending/running），避免两套口径 |
| `/stream` 主循环之后、`aget_state` 之前 | 还有残留步骤 → `_run_continuation`（复用批准/打回那条续接骨架）再跑一轮，每轮后回写 `adapter._plan_steps` |

超界或仍不全 → 落到 §7.2 的收尾对账（标 cancelled + 明确告知），不会无限续跑。

**未覆盖**：`/resume` 端点没接续跑（它本身已由 `_run_continuation` 驱动门后继续）；
自动续跑无法在 agent 层验证，需起服务跑端到端。

### 7.4 退役用例清理

按 ADR-0015（`request_approval` 与 `interrupt()` 退役）删除 2 条、改写 1 条、修正 1 条：

| 用例 | 处理 |
|---|---|
| `react-approval-flow` | **删**（期望 `request_approval`，工具已不存在） |
| `interrupt-resume` | **删**（`interrupt_triggered`/`after_resume_tool_called` 测的是已退役的图内中断） |
| `guardrail-approval-enforcement` | **改写**为现行语义：`assign_adaptive_practice` 被调 → 结果含 `approval_needed`（安全属性保留，只是换了表达） |
| `react-multi-tool` | **修正期望**：`search → generate_questions`（给了真题上下文按契约直出，不开面板） |

结果：`--langgraph` 从 6 挂 13 断言 → **12 过 1 挂**（`13 → 12`，仅剩 `guardrail-tool-dedup`，
其判据 `len(seen_calls) < len(tools_called)` 在现行去重实现下不成立，属检测口径问题，非退役机制）。
---

## 8. 真实浏览器 E2E 实测（2026-10，三端登录）

环境：`python -m uvicorn app.main:app --port 8001`（前端 dist 同端口）。
账号：教师 `zhou/zhou123456`（subject_lead）、学生 `241901/123456`（周翔）、家长 `parent001/123456`（陈国平，绑定 241914）。学生/家长走 `#/m/login`。

### 8.1 通过

| 场景 | 观测 |
|---|---|
| 教师「我要出题」 | 追问知识点/题型/难度三件事，**选项式**，未弹面板（§4.1 契约修复成立） |
| 教师「出3道氧化还原的选择题，中等难度」 | 直出 3 题、四维审核通过，未追问、未搜真题 |
| 教师「准备一次月考」（第 3 轮） | 计划 5 步：1-4 完成，第 5 步显式「失败」（修复前是永久卡在第 3 步） |
| 学生「来一轮智能练习」 | 接棒跳 `#/m/practice`，首题已出、能力估计 −0.06（`difficulty` 崩溃消失；θ 实测会动） |
| 学生「我做完了」（实际零作答） | debrief 直接报「今天复习提交 0 张…那轮练习一题都没作答就离开了」，**不替学生确认**（C4 成立） |

### 8.2 失败（按严重度）

| # | 现象 | 根因 | 状态 |
|---|---|---|---|
| **F1** | `show_diagnosis() got an unexpected keyword argument 'diagnosis_id'` → TypeError 打死整轮，计划卡**永久停在「执行中」**，第 4、5 步不推进 | 计划器幻觉参数；`_guarded` 把 kwargs 原样传给工具，异常穿透 LangGraph tool node | **已修**：按工具签名过滤未知键 + 同义词改名表（`question_type→question_types`、`count→quantity`）。修后同场景 4/5 步完成 |
| **F2** | 计划第 5 步「根据薄弱点生成**班级**复习计划」必败 | `generate_learning_plan(student_id, student_name)` 只支持单个学生 | **已修**：加 `class_id/class_name` 分支 → 班级诊断 + 按薄弱点排 4 周，产出**教研用内联计划**（`delivery=teacher_only`），不产生 `approval_needed` 所以不开确认门（没有收件人就没有「发送」这一步）。`TOOL_META` 补 `class_id/class_name → class` 范围校验。实测同场景 **4/4 步全部完成** |
| **F3** | 家长问「孩子这周学习怎么样」→「**无权访问该学生**」 | 前端把 **parent_id** 当 student_id 调学生维度接口 → 403。根因不是参数写错，是**家长被路由进了学生端**（学生页拿 `user.id` 当学号） | **已修**（与 F4 同一处）：家长走家长端后，`ChatPanel` 传的是 `active.student_id`（本来就对）。实测周报正常产出真实数据 |
| **F4** | 家长登录后落在**学生端 UI**（底栏 AI助教/练习/复习/错题） | 角色路由未按 parent 分流 | **已修**：①`StudentLoginPage` 登录后按 `role` 分流（parent→`/m/parent`、教师→`/chat`）；②新增 `RequireStudentMobile` 路由守卫，家长误入 `/m*` 直接弹回 `/m/parent`。实测家长登录落在家长概览（孩子 陈湘旻/2419班） |
| **F5** | 工具异常时计划卡不收尾（F1 那次的「执行中」永不结束） | 异常从 tool node 抛出后 SSE 提前结束，绕过 `finalize` 与 `_plan_tail_events` 对账 | **已修**：两个流式端点各补 `except Exception`——步骤落终态（`compute_abort_transition`：running→failed、pending→cancelled，`awaiting_confirmation` 不动，门还开着不能替老师放弃）、明确报错、正常收帧（done + [DONE]）。单测 4 条钉住 + 幂等；正常路径回归无影响 |

### 8.3 未验证

- **自动续跑未观测到触发**：三次「准备月考」都停在失败/门前，没走到「模型自己收尾但计划未完成」的情形，`[PlanContinue]` 无日志。功能在，但**尚无现场证据**。
- 未跑：出题面板显式请求、讲评稿自动补全、批改标准答案自动补全、布置练习门、门批量批准、家长周报/重点关注。

---

## 9. 全清单 E2E 回归（2026-10，51 条任务对话）

跑法：对 `/api/agent/chat/langgraph/stream` 逐条新建会话 → 发消息 → 解析 SSE（tool_call / error / gate_open / text）。
覆盖 §1 教师端 A/A-multi/B/C、§2 学生端 A/B/C、§3 家长端，共 **51 条**。

**结论：51 条中 51 条无崩溃/无报错**（首轮 2 条崩溃已修并复测）；另有 8 条属「未达成」软失败。

| 组 | 条数 | 结果 |
|---|---|---|
| 教师端 A 单次 | 19 | 全通过 |
| 教师端 A-multi | 5 | 全通过 |
| 教师端 B 多轮 | 10 | 全通过 |
| 教师端 C 拆解 | 2 | 全通过 |
| 学生端 | 12 | 全通过 |
| 家长端 | 3 | 全通过 |

### 9.1 首轮崩溃（已修，复测通过）

| 用例 | 报错 | 根因 | 修法 |
|---|---|---|---|
| **B2** 生成家长报告 | `'NoneType' object is not subscriptable` | `diagnosis.py:1128` 无条件 `sid = rows[0][0]`，而按姓名解析命中时 `rows` 就是 None | 加 `if not sid:` 守卫 |
| **B10** 知识点先修图 | `'ChatResult' object has no attribute 'get'` | `knowledge_map.py:43` 把 `provider.chat` 返回的 **dataclass** 当 dict 取 content | dict/dataclass 双兼容读取 |

### 9.2 软失败：没报错但任务没达成（8 条）

| 用例 | 现象 |
|---|---|
| `A5` | 问班级薄弱点却连调 weekly_report×2（选错工具） |
| `A7` | show_students 连调 5 次（重复浪费） |
| `B4` | 删题库只调 list_banks，没走到布尔确认门 |
| `B6` | 批答题卡只查进度/队列，没调 grade_answer_sheets |
| `B8` | 成绩确认没调 confirm_exam_grades |
| `C1` | 「整套复习资料」只回 379 字，没产出整套材料 |
| `M3` | 只到「任务计划已生成」，后续未见产出 |
| `S10` | invoke_learning_skill 连调 3 次 |

这几条说明「PASS=没崩」不等于「任务完成」：搜索类/工具选择类问题不会抛异常，只会安静地少做一步。

### 9.3 逐条结果

| # | 用例 | persona | 耗时 | 卡片 | 工具 |
|---|---|---|---|---|---|
| A1 | PASS | teacher | 22.1s | 0 | generate_questions |
| A2 | PASS | teacher | 4.9s | 0 | — |
| A3 | PASS | teacher | 12.7s | 0 | search_exam_bank |
| A4 | PASS | teacher | 17.1s | 0 | web_search |
| A5 | PASS | teacher | 19.5s | 0 | show_students,show_students,diagnose_barrier,show_diagnosis,weekly_report |
| A6 | PASS | teacher | 15.6s | 0 | show_diagnosis,show_students,show_students,show_diagnosis,diagnose_barrier |
| A7 | PASS | teacher | 21.9s | 0 | show_students,show_students,show_students,show_students,diagnose_barrier |
| A8 | PASS | teacher | 9.9s | 0 | weekly_report |
| A9 | PASS | teacher | 9.7s | 0 | show_students,send_class_notification |
| A10 | PASS | teacher | 17.8s | 0 | chemistry_tutor |
| A11 | PASS | teacher | 7.2s | 0 | balance_equation |
| A12 | PASS | teacher | 11.2s | 0 | simulate_experiment |
| A13 | PASS | teacher | 7.8s | 0 | list_banks |
| A14 | PASS | teacher | 11.4s | 0 | list_teacher_exams |
| A15 | PASS | teacher | 8.6s | 0 | query_ocr_progress,query_grading_queue |
| A16 | PASS | teacher | 15.3s | 0 | query_grading_queue,list_teacher_exams,query_batch_grades |
| A17 | PASS | teacher | 5.4s | 0 | query_grading_queue |
| A18 | PASS | teacher | 9.7s | 0 | show_students,memory_student_get |
| A19 | PASS | teacher | 5.1s | 1 | read |
| M1 | PASS | teacher | 42.0s | 0 | plan_task,search_exam_bank,web_search,generate_questions |
| M2 | PASS | teacher | 55.4s | 0 | generate_questions,save_to_bank,save_to_bank,write |
| M3 | PASS | teacher | 62.0s | 0 | plan_task,list_banks,show_students,search_exam_bank,diagnose_barrier |
| M4 | PASS | teacher | 25.6s | 0 | plan_task,show_students,diagnose_barrier,generate_questions |
| M5 | PASS | teacher | 9.2s | 0 | write,bash,bash |
| B1 | PASS | teacher | 23.1s | 1 | generate_learning_plan,show_students,generate_learning_plan |
| B2 | PASS(修复后复测) | teacher | 3.5s | 0 | generate_parent_report |
| B3 | PASS | teacher | 5.3s | 1 | assign_adaptive_practice |
| B4 | PASS | teacher | 6.9s | 0 | list_banks |
| B5 | PASS | teacher | 14.0s | 0 | find,show_students,grep,grep,grep |
| B6 | PASS | teacher | 12.6s | 0 | query_ocr_progress,query_grading_queue,list_teacher_exams |
| B7 | PASS | teacher | 8.3s | 1 | find,find |
| B8 | PASS | teacher | 11.3s | 0 | query_grading_queue,list_teacher_exams |
| B9 | PASS | teacher | 50.6s | 0 | invoke_teaching_skill |
| B10 | PASS(修复后复测) | teacher | 43.2s | 0 | propose_knowledge_edges |
| C1 | PASS | teacher | 8.7s | 0 | memory_teacher_get,show_students |
| C2 | PASS | teacher | 43.2s | 0 | invoke_teaching_skill |
| S1 | PASS | student | 8.5s | 0 | chemistry_tutor |
| S2 | PASS | student | 10.4s | 0 | simulate_experiment |
| S3 | PASS | student | 4.7s | 0 | invoke_learning_skill |
| S4 | PASS | student | 4.3s | 0 | invoke_learning_skill |
| S5 | PASS | student | 10.0s | 0 | chemistry_tutor |
| S6 | PASS | student | 8.2s | 0 | invoke_learning_skill,memory_student_get |
| S7 | PASS | student | 5.1s | 0 | ionic_equation_tutor |
| S8 | PASS | student | 6.9s | 0 | ionic_equation_tutor |
| S9 | PASS | student | 4.7s | 0 | invoke_learning_skill |
| S10 | PASS | student | 10.5s | 0 | invoke_learning_skill,invoke_learning_skill,invoke_learning_skill |
| S11 | PASS | student | 3.8s | 0 | invoke_learning_skill |
| S12 | PASS | student | 8.7s | 0 | invoke_teaching_skill |
| P1 | PASS | parent | 12.7s | 0 | parent_weekly_summary,parent_mastery_levels,parent_learning_plan,parent_barrier_distribution |
| P2 | PASS | parent | 10.6s | 0 | parent_weekly_summary,parent_mastery_levels,parent_barrier_distribution |
| P3 | PASS | parent | 13.8s | 0 | parent_weekly_summary,parent_mastery_levels,parent_barrier_distribution |


---

## 10. 状态机断言（门生命周期 / 计划推进）——补 §9 的盲区

§9 只统计了「有没有 gate_open」「调了哪些工具」，**验不了顺序与状态语义**。这里按状态机重跑：
全程记录有序事件流（含时间戳），批准后继续收流，再与 DB 的 `plan_runs.steps` 对账。

### 10.1 计划推进（C 类，「准备一次月考」）

\`\`\`
PLAN steps: [(1,show_students),(2,diagnose_barrier),(3,show_diagnosis),(4,generate_questions),(5,generate_learning_plan)]
PROGRESS  : 1 running→completed, 2 running→completed, 3 running→completed, 4 running, 5 completed, 4 completed
\`\`\`

| 断言 | 结果 |
|---|---|
| 同一时刻只有一个步骤 running | ✅ 无并发 running |
| 末态与 DB `plan_runs.steps` 一致 | ✅ 5/5 全部 completed，两边一致 |
| 未到终态的步骤 | ✅ 无 |
| **按序推进（第 N 步完成后才该有 N+1）** | ❌ **乱序：第 5 步先于第 4 步 completed** |

乱序根因：`plan_progress` 按**工具实际完成顺序**投影，而模型把第 5 步的工具先跑完了。
最终状态没错，但步骤卡的「当前步」在那一刻是漂的——用户会看到第 5 步先打勾。

### 10.2 确认门生命周期（批准后是否真的发送）

| 用例 | 开门 | 批准后重放的工具 | 发送是否真的执行 | 是否重复开同一张卡 |
|---|---|---|---|---|
| B1 学习计划 | ✅ | `send_learning_plan` | ✅「学习计划已发送成功」 | 否 |
| B2 家长报告 | ✅ | `send_report_to_parent` | ✅「已送达家长：周建国、杨玉华」 | 否 |
| B3 布置练习 | ✅ | `assign_adaptive_practice` | ⚠ **执行了但「没有布置成功：本次没有组出合格题目」** | 否 |

「门消失」的证据链：history 返回 `gate=null`（已无开门）+ 重放工具真的跑 + 没有第二张同 id 卡。

### 10.3 新发现（软失败，不崩溃）

| # | 现象 | 影响 |
|---|---|---|
| ~~G1~~ | ~~计划步乱序~~ → **改判：非缺陷**。第 4、5 步都只依赖第 3 步，属并列分支，谁先完成都合法；真正该断言的是**依赖序**（见 §10.4） | — |
| **G2** | `assign_adaptive_practice` **间歇性**「组不出合格题目」（三轮里一轮失败、一轮成功） | 布置练习时灵时不灵 |
| **G3** | `generate_learning_plan` 单轮内被调用 **2 次** 才开门 | 多一次模型往返；原因未查（首次返回可能非预期） |

### 10.4 依赖序断言（可复用脚本）

新增 `scripts/verify_agentchat_state.py`，把两类状态语义做成可重复执行的断言：

| 断言 | 判据 | 数据源 |
|---|---|---|
| 计划依赖序 | 某步 completed 时，它 `depends_on` 里的步必须已终态 | SSE `plan_progress` 顺序 × DB `plan_runs.steps`（含 depends_on） |
| 门落定三态 | 重放成功 → `executed`；重放失败 → 退回 `open`（老师可重试） | DB `agent_gates.status` + 重放 `tool_result.success` |
| 重放配对 | 批准后跑的是**配对的那个**发送工具 | SSE `tool_result.name` |

跑法：`python scripts/verify_agentchat_state.py`（需后端在 8001 运行）。
取步骤的坑：不能只查「活跃 run」——跑完就归档，断言会空转；要按会话取 `plan_runs` 最新一条。

#### 三轮实测

| 轮次 | 计划依赖序 | B1 草稿门 | B3 布尔门 |
|---|---|---|---|
| 1 | ✅ 全序 | open→executed | ⚠ 仍 open（**当时误判为缺陷**） |
| 2 | ❌ **违例**：步 2 completed 时依赖步 1 尚未终态（1 最终 failed） | ✅ | open（重放业务失败，**符合设计**） |
| 3 | ✅ 全序 | ✅ | ✅ 重放成功 → executed |

两条更正：

1. **B3「门没关」不是缺陷**：`gate_service.finish_gate` 的语义就是「成功→executed、失败→退回 open 让老师重试」。
   第一轮我只读了 DB 状态、没看 `tool_result.success`，属于断言写错，已修正为按成败定期望值。
2. **依赖序违例是偶发的**：三轮出现一次。`compute_tool_transition` 只要工具返回就推进那一步，
   **不校验 `depends_on` 是否已终态**——依赖步还在跑（后来还 failed）时，下游步照样被标 completed。

#### 仍待修（本轮只做验证，未改核心调度）

| # | 问题 | 影响 |
|---|---|---|
| **D1** | 计划步推进不校验依赖（偶发违约） | 「A 没做完就做 B」；依赖步 failed 时下游仍标完成 |
| **D2** | 自适应练习间歇性组不出题 | 布置练习时灵时不灵 |

---

## 11. 待修清单（2026-10-20 立，逐条跟踪）

来源：真实浏览器 E2E + 三端联动实测。**每条都必须用界面/跨端证据验收**，不接受「工具被调用了」这种弱判据。

### A. 本轮引入的回归（最高优先级）

- [x] **A1** 计划卡状态与实际相反：第 5/6/7 步显示「已放弃」，同屏上方却写「已确认发送 / 已送达 2 位家长」
- [x] **A2** 根因：门重放走 `pending["fn"]`，不经 `_guarded` → 不落产物、不产生步转移 → 收尾对账把已执行步标 cancelled
- [x] **A3**（2026-10-20 真机）确认门死锁：老师点不动门卡 → 后面每一步写操作都被挡 → 模型越重试越死
- [x] **A4**（2026-10-20 演示实况）链路走到一半卡住 / 页面数据"消失"——根因是 `database is locked`
  - 证据（后端日志）：`sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked` 抛在 ASGI 里，同屏还有 `GET /api/agent/chat/history/p-…  500`；另有两条 `IntentClassifier: timed out after 5s, falling back to all tools`。数据本身完好（学生 341 / 考试 5536 / 题目 29072 / 作答 76208）。
  - 机制：SQLite WAL 是单写事务，引擎只设了 `journal_mode=WAL` + `synchronous=NORMAL`，**没有 busy_timeout**（默认 5 秒就抛锁错）。定时作业（OCR 每 5s、语义扫描每 120s）与一条长 Agent 回合撞上时，前台就是 500 → 页面空白 → 看起来「数据没了」「链路卡住」。
  - 修复：`app/models/database.py` 的连接钩子加 `PRAGMA busy_timeout=15000`（等锁而不是立刻报错）；按仓库纪律先 `scripts/db_checkpoint.py` 再重启。验证：重启后 `/api/classes` 9、`/api/classes/class_2419/students` 40、`/api/agent/gates/pending` 0，均 200。
  - 同类修掉一条：`no_draft_to_send` 曾被当成普通 error → 那一步被标 `failed`（演示那次第 8 步 send_report_to_parent 显示 ✗，其实什么都没发）。现在 `compute_tool_transition` 对它与 `gate_open_blocked` 同口径：不动状态；`tests/test_plan_step_advance_guard.py` 加 1 条。
  - 仍留着：那次 run 里第 5 步 `generate_questions` 被标 `cancelled`、而依赖它的第 6 步 `assign_adaptive_practice` 却 `completed`（卡片状态与实际相反，属 A1/A2 同一类收尾对账问题）。未修。
  - 实况：周翔的学习计划（draft `7403ddb776174c7d`）那扇门已 `executed`，模型随后又调 `send_report_to_parent`（此时**没有**待确认的报告草稿）→ guard 的 replay-send 分支在「查不到已开着的门」时落到了**布尔门分支**，登记了 `g-tool-send_report_to_parent-33663132`（`action` 为空）。这扇门被判据算作「有一张待确认卡」，于是 `generate_questions / assign_adaptive_practice / generate_parent_report / send_report_to_parent` 逐一返回 `waiting_for_gate`——老师看到的那四条「⛔ 全部被同一道拦截挡住」就是它。
  - 第二条因（同屏）：门卡按钮由父级 `disabled={streaming}` 锁着。门开着时模型在同一轮里继续跑（始终流式）→ 按钮整段时间是灰的 → 老师根本点不了 → 门关不掉 → 正反馈。
  - 修复①（后端·根因）：`agent/guard.py` 里 replay-send 工具在「没有任何开着的门」时**不再退化为布尔门**，返回 `no_draft_to_send`（`recoverable`、明说不要重复调用、下一步去生成草稿）；门状态查不出来时按「有门」处理（三态 fail-closed）。
  - 修复②（前端·因）：`ApprovalCard` 自管在途状态（连点只发一次、1.2s 自动放开），父级不再按 `streaming` 锁卡；并发交给后端会话锁（409 → toast），不在 UI 上假装点不动。
  - 验证：`tests/test_gate_send_no_draft.py` 2 条（没门 → 不建门且给可继续的路 / 已有门 → 仍等待且不换门）；`frontend/tests/approval-card-not-locked.test.tsx` 4 条（不再接受 disabled 入口 / 连点只一次 / 1.2s 放开 / 放弃可用）；gate 相关 5 档 58 passed；全库 1782 passed、前端 457 passed。
  - 存量：那扇布尔门已于 11:04 在老师端处理掉（`executed`）；库里仍有 3 扇 `open` 的 **draft** 门，都是真实的待老师决定卡片，未动（`scripts/repair_gates.py` 的立场同理）。
  - 附注：`frontend/tests/exam-page-retrieval-notice.test.tsx` 单跑通过（2 passed），全量跑偶发 1 条超时——既存 flaky，与本轮改动无关。

### B. 界面/体验（用户直接可见）

- [x] **B1** 家长报告重复投递：教师端正常，家长端收到 2 条相同通知
  - 根因：通知幂等键把 `draft_id` 算进哈希（`parent_report_draft_service._notification_id`）。`_aggregate` 是纯查库聚合，数据没变时重新生成的报告逐字节相同，新草稿只换来新键 → 家长再收一条一模一样的卡片。实测 `parent_241914` 两条 `rep_6c4b628f3a6e`(08:02) / `rep_ecdb910a25d4`(08:23) 内容 hash 同为 `2e534b1f96`，标题同为「📋 陈湘旻 的学习报告」；`parent_241901` 同型（`cf7803c633`，06:21 / 06:31）。
  - 修复：幂等键改为「学生 × 报告内容 × 家长」，不含 `draft_id`。内容变了照旧投一条新的；内容相同则不重复投递，并把「未重复投递」明说给老师（新增 `already_delivered`）。草稿仍落 `sent`，确认门照常收敛。
  - 验证：`tests/test_report_draft_service.py` 新增 2 条（同内容二次发送只 1 条 / 内容变化再投 1 条）；该档 14 passed，连带 gate/notification 相关 8 档 123 passed。API 层：走两轮「生成→批准→发送」后家长收件箱接口只返回 1 条。存量重复行属 E2 清理范围，未动 `chemai.db`。
- [x] **B2** 生成学习计划先弹「失败」红卡（计划参数解析失败→丢参→工具报错→重试才成功）
  - 根因：`agent/planner.py` 的 `PLAN_PROMPT` 按 `.format()` 的写法把花括号转义成 `{{`/`}}`，但提示是 `.replace()` 拼的、全程没有 format —— 发给模型的 JSON 骨架本身就是坏的（实测 `{{`×5），跨步引用也被教成 `${{step_1.x}}`。模型照抄 → `_parse` 抛错 → 空计划 → `_single_step_fallback` 把真实参数换成 `{"keyword": goal[:50]}` → 工具拿不到 `student_id` 报错弹红卡；重试时输出恰好规范才成功。
  - 更静默的一半：`_REF_RE` 只认单花括号，`${{step_1.x}}` 匹配不到 → `resolve_args` 把占位符**当真实参数原样灌进工具**且 `missing=[]`（不 throw、不留痕），工具收到的是 `"${{step_1.question_ids}}"` 这样的字符串。
  - 修复：骨架与引用示例改回单花括号（对齐 spec-06 与 `_REF_RE`）；`_REF_RE` 增加 `\{\{?` / `\}\}?` 容忍，旧模板残留的双花括号也能解析、缺产物时照常留痕。
  - 验证：`tests/test_plan_step_artifacts.py` 新增 2 条（骨架必须本身就是合法 JSON 且引用为单花括号 / 双花括号引用仍解析、缺产物留痕）；该档 19 passed，连带 plan·gate 相关 7 档 100 passed。
  - 残留（未改）：单步兜底仍会给不接 `keyword` 的工具塞 `keyword`，属兜底策略本身的设计，触发条件已从「几乎必现」降到「模型两次都不合规」。
- [x] **B3** 家长端 `ReportPane`（学习报告页）仍静态，只有 `SummaryPane` 三卡支持下钻
  - 现状核实：ReportPane 并不是不取数（它拉 `/child/{id}/report` 与 `/weekly`），「静态」指的是**所有块都是死卡片**——hero 正确率、4 个 KPI、知识点卡、家庭配合卡都没有可点入口，而 SummaryPane 三卡早就能弹底部明细层。
  - 修复：六块全部可点，复用 SummaryPane 的底部详情层写法；明细只用**已返回字段**（不动后端）：答对/答错拆分 + 滚动 7 天区间、本周量与累计量、最近一次练习时间、知识点按错题数排名前 5、「家庭配合建议」摊出 concept/reading/expression 三类占比（此前只显示最强那一个）。卡片带 `role=button` / `tabIndex` / Enter·Space 键盘可达。
  - 验证：新增 `frontend/tests/parent-report-pane.test.tsx` 4 条（DOM 级：点卡后断言「本周共练 12 道，答对 8 道、答错 4 道」「2026-10-14 ~ 2026-10-20」「1. 氧化还原」「50%/30%/20%」、无数据时给「本周还没有练习记录」）；`npx tsc --noEmit` 通过；全前端 **451 passed**。
  - 既存红灯（与本改动无关，改前即红）：`chat-thread-param` / `plan-card-progress` / `pending-gates-page` 共 6 条，这三个档不引用家长端页面。
  - 说明：三类障碍占比取自 `/report` 的 `barrier_type`（对象）；`/weekly` 的同名字段是 `str`（单个权重转成的字符串，见 `WeeklyReportResponse`），不能拿来做分布。ReportPane 仅加 `export` 以便 DOM 验收，未搬进 `panes.tsx`。
- [x] **B4** 消息通知开关 + 练习提醒时间（需后端偏好表；现做只能是假开关）
  - 核实：**通知开关那半句已过期**——类型开关与免打扰早就是真后端（`parents.notify_prefs` JSON + `GET/PUT /api/parent/notify-prefs` + `parent_delivery_gate` 在 dispatch 里真生效）。真正的缺口只有「练习提醒时间」：原来不存在，`daily_practice_job` 的推送时点硬编码 `CronTrigger(hour=8)`。
  - 语义（定下来）：家长自选一个整点，当天练习**没做完**才发一条站内「今天的练习还没完成」；与 8:00 那条「今日练习已推送」不是同一条，**默认关**（否则每个家庭凭空多一条，就是 B1 的翻版）。
  - 实现（不新建表，复用 `parents.notify_prefs` 加 `practice_reminder`）：① `DEFAULT_DELIVERY_PREFS` 增 `{enabled:false, hour:19}`；② PUT 校验 `enabled` 布尔、`hour` 6–23 整点；③ 新增整点 job `practice_reminder_job`（Asia/Shanghai、幂等键=学生×日期×家长、当天没推送练习则不发）；④ 设置页加「每日练习提醒」开关 + 时点下拉（17–22 点），改动即 PUT。
  - **顺带修掉一个真 bug（很可能就是"假开关"观感的来源）**：`notify_prefs` 是 JSON 列，PUT 里 `stored = parent.notify_prefs` 原地改完再赋回去 → SQLAlchemy 判定"没变" → commit 空跑。实测「先存 hour=19、改成 20、回读仍是 19」；加 `flag_modified` 后第二次及以后的保存才真的落库。
  - 验证：后端新增 6 条（到点发 / 时点不符不发 / 做完不发 / 关掉不发 / 当天没推送不发 / 偏好校验+roundtrip）；前端新增 3 条（默认关不显示时点 / 开关落库 `enabled=true` / 改时点落库 `hour=21`）。全库 **1764 passed**、前端 **454 passed**，两边红灯与改前完全一致（embedding 2 条、plan/待确认卡 6 条）。
  - 未改（报告）：`daily_practice_job` 的 `CronTrigger(hour=8)` 落在**UTC** 调度器上（`AsyncIOScheduler(timezone=timezone.utc)`），而注释写的是"每日早上8点"——实际北京 16:00；同文件里家长摘要/周报都显式传了 `timezone=_TZ`。改它会移动整条每日推送的时刻，属行为变更，没动。

### C. 工具选择类软失败（§9 遗留，不报错但任务没做成）

- [x] **C1** `A5` 问班级薄弱点却调 `weekly_report`
- [x] **C2** `A7` `show_students` 连调 5 次
- [ ] **A5（2026-10-20 真机浏览器实测发现，硬停引入）** 门开出即收尾带来两个新问题
  - 现象（重启 + `vite build` 后用浏览器在教师端实测一条 7 步计划）：
    ① 对话里出现**三段重复**的「已暂停，等待老师在确认卡上决定」——同一轮里并行/多次工具结果各触发一次模型调用，每次都被短路成同一句话；
    ② **确认卡没渲染出来**（DOM 里 `需要确认` / `发送给学生` 各 0 处，`data-gate-id` 节点 0 个），而计划卡显示「周翔 个性化学习计划 第 1 版 / 正文见下方计划卡」——老师看不到可点的卡，等于又一条死路。
  - 好的一面（同一屏可见）：计划卡 7 步里第 1 步「完成」、其余全部「等待中」，**没有出现「被放弃的步」与「已完成的后续步」并存的 A1/A2 老问题**；门卡按钮在另一条会话里实测为 `[ok]`（不再是灰的）。
  - 判断：这是硬停（A3/B5）的回归，比原来的「一直往下冲」更危险——**没有卡 = 老师无从决定**。未修。
- [x] **B5**（2026-10-20 真机）在对话里点「确认发送」，页面被弹到待确认队列——方向反了
  - 实况：`ChatPage.handleApproval` 在批准成功后直接 `navigate('/pending', {state:{justApproved}})`，而且是在流读完之前跳，于是老师既看不到「我做成了什么」，也接不上刚说的话。这是 D8/D9/R19② 当时的取舍（用队列那一行的「查看执行 →」补回来），实际体验是从对话被弹到队列、从队列点一行又跳回对话——两边互相抛。
  - 修复：批准后**留在对话**，`await runStream(resp)` 照常把这一轮读完；「乐观减一」与「流读完强制重拉」保持不变。队列侧「打开 → 跳对话」不动（那才是正常流向）。
  - 契约变更：`frontend/tests/chat-thread-param.test.tsx` 的「批准后的回程（R19 第②点）」按新契约改写（不跳走 + 仍减一 + 仍重拉），用例注释写明为什么推翻 R19②。
  - 踩到的坑：happy path 里不要调 `toast.success` —— 测试环境 antd `message` 未初始化，一调就抛，把整段回程带进 catch（本轮实测踩到，已去掉）。
  - 验证：该档 16/17（另 1 条是既存红灯）；全前端 458 passed，仍是原来那 6 条红灯。
  - 门开出后不停（同一条实测）：模型继续按计划往下调、连续吃 `waiting_for_gate`（周翔那条链第 5~8 步四条）。计划步状态机本身不因被拦前进（`plan_run_service.compute_tool_transition` 对 `gate_open_blocked` 直接 return），停不下来的是「模型还在同一个回合里继续试」。
  - **硬停（已实现）**：`create_react_agent` 只有 `pre_model_hook / post_model_hook / interrupt_before|after`，没有「工具跑完就结束」的钩子，所以在**模型边界**短路——`agent/guard.py` 新增 `_GatePausedModel`：`GuardState.paused` 置位后，下一次模型调用直接返回一条**不含 `tool_calls`** 的收尾消息（`async astream` 给单个 `AIMessageChunk`），ReAct 循环没有下一步可走，图自然收尾。
  - 置位点（四处，都是「门已经开出来了」的时刻）：草稿门登记成功、布尔门登记成功、被已开的门拦下（`_waiting_out`）、replay-send 无草稿（`_send_needs_draft_out`）。`begin_turn()` 复位——老师决定后的下一轮照常干活；批准重放本身走 channel 的确定性重放，不经模型。
  - 验证：`tests/test_gate_pause_turn.py` 5 条（暂停时 ainvoke 不调内层模型且不带 tool_calls / astream 只出一块 / 未暂停照常委托 / `begin_turn` 复位 / 被门拦下置位），全绿；全库 **1787 passed**（+5），红灯仍是 embedding 那 2 条。
  - **注意**：`uvicorn` 是以 `app.main:app` 直接起的、**没有 `--reload`**，所以这套修复要**重启后端**才会在线生效；本轮验证是测试级 + 全库回归，没有重启服务做真机会话复跑。
- [ ] **C3** `B4` 删题库只调 `list_banks`，没进确认门
  - 已改（2026-10，待 E2E 复验后勾选）：失败机制是「读工具把下一步只写在 docstring 里，模型拿到列表就当回合结束」。改法是把下一步写进**同一条返回值**——`list_banks` 现在带 `next_step`，点名 `delete_bank(set_id=…)`，并说明首次被确认门拦下返回 `approval_needed` 是正常的、不要停；多条同名候选时才给老师选项。
  - 证据：`tests/test_tool_next_step_hints.py` 断言返回值里点名 `delete_bank` 且含「不要只把列表报给老师就停」。
  - 局限：这是引导不是强制。真正判定要看再跑一次「删掉XX题库」的整段对话（本轮没做浏览器 E2E）。
- [ ] **C4** `B6` 批答题卡没调 `grade_answer_sheets`
- [ ] **C5** `B7` 成绩确认没调 `confirm_exam_grades`
  - 已改（2026-10，待 E2E 复验后勾选）：同 C3 一类——`query_grading_queue` 现在按队列实况注入 `next_step`：有待确认成绩就点名 `confirm_exam_grades(exam_id=…)` 并说明确认前 finalize/诊断/错题本都看不到这些作答；只有答题卡批待入成绩则点名 `save_grading_results(batch_id=…)`；队列空则**不注入**，免得模型去确认一场不存在的考试。
  - 证据：`tests/test_tool_next_step_hints.py` 四条（有成绩→确认 / 只有批→保存 / 空队列无 next_step），全绿。
  - **真机会话实测（2026-10-20，`zhou` 账号，演示库）**：说「这次月考的成绩没问题，确认生效吧」→ `query_grading_queue` 返回 `{"pending_confirm":[],"unconfirmed_exams":[],"recent_saved":0}` → 转 `list_teacher_exams`(3227 场) → `query_ocr_progress`(无进行中批次) → `query_batch_grades(exam_class_2419_202609)`（已保存 40 人）→ 如实回答。**在当前数据状态下这条不可复现**：库里根本没有未确认成绩，模型没有确认对象。要判定 C5 是否真修好，必须先造一场 `confirmed=False` 的考试再复跑；`§9` 里那次 `find,find` 是另一条路径（未走队列工具），我这次没重现出来。
- [ ] **C6** `C1` 整套复习资料只回 379 字，未产出整套材料
  - 本轮未动：这条要判断「是否产出整套材料」，只能靠真实 LLM 会话看产出体量，没有可离线钉住的机制。
  - **真机会话实测（2026-10-20）**：说「帮我出一整套高一化学本单元的复习资料」→ `plan_task`/`diagnose_barrier`/`generate_questions`(10 题)/`save_to_bank`/`generate_learning_plan`(班级)/`invoke_teaching_skill(review_deck)`×2/`generate_parent_report`。**材料是产出了的**（讲评稿 v1 落到面板），但**结尾给用户的正文只有 16 字**。所以 C6 的「只回 379 字」很可能不是「没产出」，而是「产出在面板、回复太空」——两种成因的修法完全不同，得先定口径。C4 `B6`（没调 `grade_answer_sheets`）、C7 `M3`（只到「任务计划已生成」）、C8 `S10`（`invoke_learning_skill` 连调 3 次，`call_limit` 现为 6）同属此类——都需要先跑一次 E2E 拿到真实调用序列，才知道该改 docstring、调 `call_limit` 还是加确定性护栏。
- [ ] **C7** `M3` 扫题库分析只到「任务计划已生成」
- [ ] **C8** `S10` 错题变式 `invoke_learning_skill` 连调 3 次
- [x] **C9（新，2026-10-20 真机实测发现）** `invoke_teaching_skill(review_deck)` 首次调用红卡失败：「模型没有返回可用的 HTML 内容」→ 第二轮同技能重试即成功。与 B2 同型（首调必红、重试才成）。
  - 先量了原始输出（一次性探针）：正常那次的回复是 29432 字、有 `<!DOCTYPE>` 也有 `</html>`，抽取成功（29348 字）——**不是能力问题，是空产出的偶发**；同一探针里 `context_data` 为空会改变提示规模，所以线上那次空产出与数据态有关，抓不到确定性触发条件。
  - 修复（两层）：① **工具内部自己重试一次**（只在失败时多花一次模型调用），不再把红卡甩给老师；② `_extract_html` 放宽到「有头无尾」——被 `max_tokens` 截断时留半份让面板渲染（<2000 字的碎片仍判失败），并支持模型从 `<html>` 起笔（无 DOCTYPE）；③ 仍然失败时错误信息带**形状描述**（「只回了 N 字，开头是…」），不再是「换个说法重试」让老师替我们猜。
  - 验证：`tests/test_teaching_skill_html.py` 7 条（围栏+前言 / 无 DOCTYPE / 截断保留 / 碎片拒绝 / 诊断文案 / 空产出自动重试一次 / 连续两次空才报），全绿；复跑同一条真机会话，`invoke_teaching_skill` 无失败结果（红卡未再出现，但该次首调就成功，**无法区分「重试救回」与「本来就没坏」**）。
- [x] **C10（新，2026-10-20 真机实测发现）** 「出整套复习资料」的会话末尾凭空调了 `generate_parent_report(student_id=241901)`——跨任务跑偏。
  - 根因：`PLAN_PROMPT` 把「提到家长就必须包含家长步骤」写得很硬，没有反向约束；模型对否定式规则记不牢（实测就是这么跑偏的）。
  - 修复：`agent/planner.py` 新增 `drop_offscope_parent_steps(plan, goal)`——目标里没有家长/父母/监护人/告知家长等字样时，**确定性摘掉** `generate_parent_report` / `send_report_to_parent` 步骤，并重编号、重映射 `depends_on`（悬空依赖直接清掉）。挂在 `generate()` 上，调用方不用记得调。
  - 验证：`tests/test_plan_scope_guard.py` 5 条（摘掉+重编号 / 依赖重映射 / 提到家长则保留 / 无关计划原样返回 / 接在 `generate()` 上），全绿；**复跑同一条真机会话：`generate_parent_report` 不再出现**（`parent_report_called: False`）。
- [ ] **C11（新，2026-10-20 复跑发现）** 确认门开着时 `send_learning_plan` / `share_artifact` 被 guard 正确拦下（`waiting_for_gate` / `gate_open_blocked: true`），但模型**原地又各重试一次**（同一轮 4 次无效调用）。提示已经写明「老师处理完确认卡后可再次发起」，模型没吃进去。未修。
  - 更新（2026-10-20，A3/B5 硬停之后）：这条的现场形态是「门开着时模型原地又试一次」。现在门一开出本轮即收尾（`_GatePausedModel`），模型没有"下一轮"可试；真机复跑一条 8 步计划，`blocked_results: 0`（修复前同一条链连吃 4 次）。
  - 仍保留：若模型在**同一批并行 tool_calls** 里带上多个工具（ToolNode 并发执行），拦截仍会发生多次——这属于同一轮内的并行，硬停管不到。要看真实发生率需单独造场景。
- [ ] **C12（新，2026-10-20 复跑发现）** 同一条「出整套复习资料」会话里还调了 `share_artifact`（分享给学生）——用户没要求分发。与 C10 同属跨任务跑偏，但这次不是计划步骤而是模型自发调用，`drop_offscope_parent_steps` 那种确定性摘步骤挡不住。未修。

### D. 验证缺口（不是 bug，但影响结论可信度）

- [x] **D1** 计划依赖序未强制（三轮出现一次违约），当前只有断言
  - 现场证据（2026-10-20 演示 run `0fce5093`）：第 5 步 `generate_questions` 标 `cancelled`，依赖它的第 6 步 `assign_adaptive_practice` 却 `completed` —— 卡片上就是「出题被放弃、练习却布置了」。此前**执行侧从不校验依赖序**，只有状态转移那一层「同名步才归属」，等于只记账不执法。
  - 修法：`agent/guard.py` 新增 `plan_dep_block(guard_state, tool_name)`，挂在 `check_prerequisites` 之后、配额/去重之前。判据是 `guard_state.plan_steps`（与计划卡同一份状态），纯函数不查库。
  - **边界（关键，避免把校验变成死锁）**：① 前置步**还没终态**（pending/running/awaiting_confirmation）→ 拦，返回 `plan_dependency_unmet` + `blocked_by`，`recoverable=True` 且**不占调用配额**；② 前置步**已终态但不是 completed**（cancelled/failed/skipped）→ **放行**——老师放弃了某一步，后面的事不该永远做不了；③ 依赖指向计划里不存在的步（计划被裁过）→ 不拦，无从满足的等待只会卡死。
  - 验证（TDD：先写测试跑红，再实现跑绿）：`tests/test_plan_dependency_order.py` 6 条——未终态拦下并点明是第几步 / 依赖被放弃时放行 / 不在计划里的工具不受影响 / 无计划不干扰 / 依赖指向不存在的步不拦 / 走真包装时工具未执行且调用配额没被占。
  - 残留：只覆盖「工具名对得上计划步」的调用；模型自作主张换一个工具做同一件事，不在管辖内（那是 C 组工具选择的范畴）。
- [ ] **D2** 自动续跑 / 异常兜底（F5）无现场证据，只有单测
- [x] **D3** `--workflow` / `--e2e` 两组评测从未跑过
  - 首跑（2026-10-20，真实 key + 真实身份）：`--workflow` **24 项 / 23 过 1 挂**（`wf-persona-cross-parent`）；`--e2e` **41 项 / 40 过 1 挂**（`e2e-tool-chain-six`）。复跑后 `--workflow` **24/24 全绿**。
  - 修掉两条**结构性过不了**的断言（不是产品 bug，是评测口径漂了）：
    ① `interrupt_triggered` 自 ADR-0015 起永远为假——确认门已改成**数据强制**（guard 返回 `approval_needed` / `gate_id`），langgraph 的 `interrupt()` 退役了。新增断言类型 `gate_opened`（从工具结果里认门），`e2e-tool-chain-six` 与 `fs-agent-outside-approval` 改用它；实测三次稳定通过。
    ② `wf-persona-cross-parent` 还指望通用 `weekly_report`，而家长端早换成专用 `parent_weekly_summary` / `parent_mastery_levels` 一套——改成当前契约后 24/24。
  - 标了 1 条 flaky：`e2e-tool-chain-six` 把 6 件事压在一句话里、`recursion_limit=12`，模型对「在文件里搜索」有时用 `read` 代替 `grep`（三次跑 6/6、4/6、5/6）。断言本身没错，是场景太挤。
  - 结论：C4–C8 需要的「真实调用序列」工具已经跑通并顺手修好了，但**这几条软失败在评测里没有对应场景**——黄金集里没有「删题库只说一句就停」「成绩确认不调 confirm」「整套复习资料」这类用例。要把 C 组变成可验证，得先把它们写成场景（下一轮的事）。
- [ ] **D4** `guardrail-tool-dedup` 断言口径问题（唯一红灯）
- [ ] **D5** 知识地图连线仅 10 条；标签截断 5 字，全名需点击

### E. 环境/数据残留（演示前必须清）

- [x] **E1** `agent_gates` 52 条 open 门（测试开启未批准），在「待确认 8」里可见
- [ ] **E2** 演示库堆积测试会话（`t-*`）与测试通知（`e2e 验证通知` / `INFO验证` / `三端联动通知`）

### 验收口径（本轮定死，不再放宽）

1. **界面优先**：卡片状态、正文是否重复、按钮是否消失，一律在浏览器里逐项核，DOM 量测优先于肉眼；
2. **跨端对看**：涉及多端的（学习计划/家长报告）必须三端都打开，任一端异常即判失败；
3. **内容级**：不能只验「调了哪个工具」，要验产出内容（题数=10、题型=选择题、计划薄弱点与诊断一致）；
4. **合理性**：单学生任务不应出现班级名单步骤；报告口径（本周 vs 累计）不能自相矛盾。
