# ChemAI — Domain Glossary

The shared vocabulary for the ChemAI AI-assisted chemistry teaching tool. No implementation details — this is a glossary of what things _are_, not how they're built.

---

## Core Entities

- **Student (学生)**: A learner enrolled in a class. Has a barrier profile (concept/reading/expression distribution) derived from error analysis.
- **Class (班级)**: A group of students taught by a teacher. Identified by name (e.g. "2419班"). Has exam history and aggregated learning analytics.
- **Teacher (教师)**: An instructor who manages classes, reviews diagnostics, assigns practice.
- **Parent (家长)**: A guardian who receives learning reports for their child.

## Learning Concepts

- **Barrier Type (障碍类型)**: The root cause behind a student's mistakes. Three categories (stored as lowercase strings in DB, enforced by `BarrierType(str, Enum)` in Python):
  - `concept` (概念理解障碍): Doesn't understand the underlying chemistry concept.
  - `reading` (审题障碍): Misreads the question or falls for traps.
  - `expression` (表述障碍): Understands the concept but can't express the answer properly.
  Attribution basis: every question declares the dimension it probes (`probe_dimension`). A wrong answer is attributed to that dimension, and a student's distribution is the dimension mix of their wrong answers — reproducible by replaying answer history, never inferred from raw accuracy.
- **Barrier Distribution (障碍分布)**: A per-student ratio across the three barrier types, e.g. `{concept: 0.3, reading: 0.5, expression: 0.2}`. The dominant barrier is the highest-valued one.
- **Knowledge Point (知识点)**: An atomic chemistry topic (e.g. "盐类水解", "氧化还原反应"). Questions are tagged with knowledge points. Error rates are tracked per knowledge point per class.
- **Diagnosis (诊断)**: The act of determining a student's barrier profile from their error history. Can be individual (one student) or class-level (aggregate distribution).
- **Adaptive Practice (自适应练习)**: Personalized exercises generated based on a student's barrier type and weak knowledge points. Targets the zone of proximal development.
- **Smart Practice (智能练习)**: A student-initiated adaptive practice session. Items are picked one at a time to maximize information about the student's ability, with a stop rule ending the session; it closes with an ability readout and a weak-point landing. Distinct from Adaptive Practice, which is teacher-assigned against a ZPD. _Avoid_: 自适应练习 (that's the teacher Assignment source), 刷题模式 (implies fixed order).
- **Memory Card (记忆卡)**: A spaced-repetition card created automatically from one wrong answer at grading time (no teacher action). Scheduling follows FSRS: each review sets the next due date. Due cards surface in the student's "今日复习" list inside the Learning Plan's review section. The primary recall signal is re-answering the original question (auto-graded); self-rating (忘了/模糊/记得) supplements where auto-grading is impractical. Supersedes the fixed-interval Ebbinghaus ReviewTask (1/3/7/14/30-day ladder): existing tasks migrate into cards, the student-facing review entry keeps its contract, and the ladder retires. _Avoid_: 错题本 (the static list of wrong questions — cards are generated from it but live on their own schedule), 复习任务 (the retired fixed-interval entity).
- **Review Streak (复习连击)**: A per-student daily asset earned by clearing all due Memory Cards for the day. Carries a current and best count, plus up to two **Freeze Cards (冻结卡)** that auto-consume on a missed day; clearing the day's due cards can award one. Deliberately the only gamification asset for now — no XP, no leaderboard, which reward volume over mastery. _Avoid_: 积分, 经验值, 排行榜.
- **Elimination Rate (消灭率)**: For a student's weak Knowledge Points, the share eliminated (brought to mastery since diagnosis), rendered as a checklist ("已消灭 3/7"). _Avoid_: 正确率 (per-question accuracy, not per-point closure).
- **Expression Training (表述特训)**: A training format for the expression Barrier Type. The student explains an experiment or solution to a virtual examiner; each sentence is annotated into three fault classes — 术语 / 条件 / 逻辑 — and scored per class; failed sentences re-enter the memory-card queue. _Avoid_: 口语练习, 作文批改.
- **Answer Composer (作答输入器)**: The structured surface a student answers subjective questions through — equation input with a chemistry symbol keypad (⇌ ↑↓ Δ 反应条件), live `\ce{}` preview, and per-blank fields for multi-blank questions. Answers persist as canonical text the Grading Center already normalizes. _Avoid_: 公式编辑器 (structure drawing is deliberately out of scope for now), 手写识别 (deferred; no open model reads chemical notation).
- **Assignment (练习分发)**: The act of delivering practice to specific students, from three sources: **Adaptive** (AI-generated per student against their ZPD, machine-gated by Four-Dimension Review with auto-regenerate), **Bank** (an existing Question Set), or **Manual** (teacher-picked questions). Produces one per-student practice record the student sees in their task list. Distinct from Exam publish (one paper to a whole class) and from Notification (makes students *aware*; Assignment delivers the content itself).
- **Assignment Batch (布置批次)**: A large Assignment (>5 students) processed in the background per student; identified by batch_id, progress is queryable. Mirrors the OCR batch concept.
- **Learning Plan (学习计划)**: A personalized study plan for one student — weekly goals, daily tasks, barrier interventions, motivation tips — generated from the student's Barrier Distribution and weak Knowledge Points. Once confirmed and sent, it becomes the student's current plan (visible on the student side) and one history entry. _Avoid_: 培优方案, 补习计划.
- **Plan Draft (计划草稿)**: The versioned, not-yet-confirmed form of a Learning Plan. Carries the teacher's revision feedback chain; at most one active draft per student at a time. Distinct from the sent plan history. _Avoid_: 临时计划, 草稿缓存.

## Content Entities

- **Exam Paper (试卷)**: A sourced set of chemistry questions (e.g. "2024 湖南高考"). Has year, region, source.
- **Question (题目)**: An individual chemistry problem. Has content, options, answer, knowledge points, difficulty.
- **Question Set / Bank (题库)**: A named collection of questions, typically AI-generated and saved for reuse. Has a set_id, name, and question count.
- **Weekly Report (周报)**: A learning summary generated for a student or class, covering recent practice, progress, and suggestions.
- **Guardian Summary (家长摘要)**: The parent home's three cards — unfinished work, due soon, week's activity — deliberately without scores, on a fixed weekly cadence the parent can adjust (daily / weekly / off). _Avoid_: 周报 (the fuller generated report), 成绩单.
- **Parent Feed (家长动态)**: High-frequency small cards pushed to a parent as the child learns — artifact viewed, practice completed, streak milestones — each likeable. Distinct from the Guardian Summary (fixed cadence, three cards) and the Weekly Report (generated long-form). _Avoid_: 通知 (the Notification Dispatch entity), Class Story.
- **Notification Dispatch (通知分发)**: The single entry through which business events (Assignment delivered, grading done, weekly report, warning) reach recipients. Two adapters: the in-app **Student Inbox**, and optional external gateways (Discord/Slack/Telegram/Email) behind a config switch.
- **Student Inbox (学生收件箱)**: The in-app notification list a student pulls; powers the practice-tab badge so a student learns of a new Assignment without refreshing the practice page.
- **Chemical Figure (化学图)**: Images inside a question. Three kinds with different handling (ADR-0005): **Simple Formula (简单结构式)** — `\ce{}` text markers rendered by KaTeX+mhchem, not images; **Complex Structure (复杂结构式)** — ring/heterocycle/stereo structures rendered by RDKit from `[SMILES:...]` markers into PNG stored under `data/question_images/`; **Experiment Diagram (装置图)** — equipment/coordinate/dataset figures, source-of-material problem (teacher upload, not yet built). RDKit is declared in requirements.txt but must be pip-installed in the current env.
- **Paper Export (试卷导出)**: Print a composed exam via a "预览/打印" button — frontend renders full questions (KaTeX/mhchem + `<img>`) and calls `window.print()` for the browser to produce PDF. No backend PDF library. (ADR-0005)
- **Artifact (工件)**: A previewable product the Agent produces — interactive teaching animation, slide deck, document, structure image, data table. Lives in the teacher's workspace, indexed across conversations (the teacher has one library list and can reopen any artifact, not just ones from the current chat); every save snapshots a version. Iteration is lightweight (any number of snapshots while refining); distribution beyond the teacher (sending to students) passes a Confirmation Gate — a student sees exactly the versions that were shared to them, never newer ones automatically. File export produces a copy on the teacher's own device and does not gate (accepted deviation from the original plan, recorded in the spec). The Agent embeds chemistry conventions directly in artifacts: equations render via `\ce{}`, structures via SMILES-derived images, and visual exaggerations carry an honest in-artifact annotation (e.g. "比例已放大便于观察"). _Avoid_: 附件 (the teacher's upload — the reverse direction), 产物 (generic).
- **Artifact Panel (工件面板)**: The chat's collapsible right sidebar. Three tabs: **Attachments** (teacher uploads with original-image preview), **Artifacts** (rendered preview of what the Agent produced, sandboxed), **Versions** (iteration history of one artifact). One panel serves both "回看老师上传的图" and "预览 AI 生成的产物"; opening an attachment or artifact in the message flow focuses the panel.

## Question & Exam Concepts

- **Question Type (题目类型)**: The format of a chemistry problem. Canonical enum (backend):
  - `single_choice` / `multi_choice` — 单选/多选题
  - `true_false` — 判断题
  - `fill_blank` — 填空题
  - `short_answer` — 简答题
  - `essay` — 论述题
  - `calculation` — 计算题
  - `experiment` — 实验题
  - Frontend TypeScript maps to these with lower_snake_case. Frontend API may use shortened aliases (`choice`→`single_choice`) for user-facing labels, but the wire format is always the canonical enum.
- **Difficulty (难度)**: Integer 1–5 scale. 1=基础, 2=中等偏易, 3=中等, 4=较难, 5=竞赛/拔高。后端存储为Integer，前端API传输为integer。不使用字符串标签。
- **Four-Dimension Review (四维审核)**: AI quality check for AI-generated *questions*. Four dimensions:
  - **Scientific Accuracy** (科学性) — Is the chemistry correct?
  - **Difficulty Match** (难度匹配) — Does it match target difficulty (1–5)?
  - **Knowledge Point Coverage** (知识点覆盖) — Does it test the claimed knowledge points?
  - **Discrimination** (区分度) — Can it separate strong and weak students?
  Each scored 0–100, composite = weighted sum (科学性×0.4 + 难度×0.25 + 知识×0.2 + 区分×0.15).
  Note: Part 4 also defines a separate **Chemical Equation Four-Dimension Verification (化学方程式四维校验)** for auditing chemical equation *correctness* (系数配平/反应条件/产物正确性/结构正确性). Despite the similar name, these are different systems for different artifacts.
- **Exam State (考试状态)**: The lifecycle of an exam. States: `draft → published → in_progress → grading → completed → archived` (+ `cancelled` from any non-terminal state). Each transition records a timestamp.

- **Type Mapping (类型映射表)**: The same `question_type` travels across three layers with different representations. This is intentional (each layer has different constraints), but the mapping MUST be followed at API boundaries:

| Layer | Example value | Notes |
|-------|-------------|-------|
| Backend (Python/SQLAlchemy) | `single_choice` | Canonical enum. Stored in DB. All API validation uses this. |
| Frontend API (TypeScript wire) | `single_choice` | Matches backend exactly — no transformation at the API boundary |
| Frontend UI label | `单选题` | Display only. Never sent to backend. |
| Doc 25 TypeScript enum | `SINGLE_CHOICE` | TS enum convention (UPPER_SNAKE). Map to backend string via `QuestionType[value].toLowerCase()` |
| Part 4 design docs | `choice` | Shorthand used in design docs only. Maps to `single_choice` |

| Backend (canonical) | TS Enum | UI Label | Part 4 shorthand |
|---------------------|---------|----------|-------------------|
| `single_choice` | `SINGLE_CHOICE` | 单选题 | `choice` |
| `multi_choice` | `MULTI_CHOICE` | 多选题 | — |
| `true_false` | `TRUE_FALSE` | 判断题 | — |
| `fill_blank` | `FILL_BLANK` | 填空题 | `fill` |
| `short_answer` | `SHORT_ANSWER` | 简答题 | — |
| `essay` | `ESSAY` | 论述题 | — |
| `calculation` | `CALCULATION` | 计算题 | `calc` |
| `experiment` | `EXPERIMENT` | 实验题 | `experiment` |

## Diagnosis Concepts

- **Misconception Category (迷思概念类别)**: The chemistry *topic domain* where a student's error lies. Six categories: 化学平衡 (Chemical Equilibrium), 氧化还原 (Redox), 摩尔计算 (Stoichiometry), 有机化学 (Organic Chemistry), 化学用语 (Chemical Notation), 物构知识 (Structure & Properties). Used by the rule engine (doc 27) to classify errors by subject area.
- **Barrier Type vs Misconception Category**: These are ORTHOGONAL dimensions — Barrier Type (概念/审题/表述) answers *how* the student errs (pedagogical root cause). Misconception Category answers *where* the student errs (chemistry subject area). A single error has both: e.g. "概念理解障碍 in 化学平衡".

## Onboarding Concepts

- **School Data Initialization (学校数据初始化)**: The one-time act of standing up a school's baseline in a fresh deployment — grades, classes, teachers, students, accounts, and its academic-year exam/answer history. Initialized data is first-class: indistinguishable from data accumulated through daily use.
- **Historical Data Import (历史数据导入)**: Bringing a school's pre-existing records into the system at onboarding — student rosters (class-student lists) and past exam results. Imported data is first-class and merges by student ID; it never forms a separate data class.
- **Data Landing (数据落点)**: Every feature that produces an outcome must persist that outcome where the product's read paths can find it. A feature whose output is never written (or writes metadata shells without content) has a landing gap. _Avoid_: 落库 (ambiguous about which store), 持久化实现 (implementation-flavored).

## Access Control Concepts

**Role (系统角色)**: What a signed-in person is allowed to reach. Two kinds, and the split matters — **Staff Role** roles are layered and resource-scoped; **Self Role** roles carry no resource permissions at all, only "your own data". _Avoid_: 权限 (that's the thing a Role grants, not the Role), Persona (a different concept — see Agent Concepts).

**Staff Role (员工角色)**: A role that manages school resources. Five levels, widest to narrowest:

- `super_admin` — **Platform Super Admin (平台超级管理员)**: operates the platform itself; belongs to no school.
- `admin` — **School Admin (学校管理员)**: the top authority within one school. The only role that sees that school's reports.
- `academic_admin` — **Academic Admin (教务管理员)**: runs day-to-day academic affairs in one school; manages classes and students, but cannot create or remove teachers.
- `subject_lead` — **Subject Lead (学科组长)**: reads across one school, narrowed to a single subject. Chosen over "own classes only" because the point of the role is comparing classes side by side.
- `teacher` — **Teacher / 任课教师**: reads only the classes they actually teach.

**Self Role (本人角色)**: A role whose entire permission is "your own data", with no resource matrix. Two: `student` (学生) and `parent` (家长). A parent's reach is the students they are bound to.

**Data Scope (数据范围)**: The set of resources a Role may reach, derived from the relationships recorded against the person (which classes they teach, which subject, which school; for a Self Role, which class they are in, or which students they are bound to) rather than from the role name itself. A role decides which dimensions narrow the scope; the data decides the values. **Applies to every Role, not only Staff Roles** — a student's Data Scope is their own, a parent's is their bound children's. "Self Role" names the *shape* of the scope (identity-bound, no resource matrix); it does not mean "no Data Scope".

**Break-glass (临时提权)**: A time-bounded, audited elevation that lets a Platform Super Admin reach across schools for support or debugging. Deliberately **not** a standing capability: by default a Platform Super Admin's Data Scope is empty. _Avoid_: 超级权限, 后门.

---

## Agent Concepts

- **Intent (意图)**: What the user wants. Two types: `chat` (needs tool execution) or `navigate` (pure page open). Classified by the Gateway (LLM + keyword fallback).
- **Single Agent (单Agent)**: A LangGraph ReAct agent (`create_react_agent`) with all persona-filtered tools visible. The LLM selects tools based on their docstring descriptions — no routing layers. 这是唯一架构；早期的 v1 多 Agent 路由架构已物理删除，没有回退路径可切（ADR-0019）。
- **Tool (工具)**: A callable function exposed to the Agent. Each tool has a docstring in "何时用 / 会发生什么 / 下一步 / NOT for" format. Tool descriptions are the sole interface for tool selection — the LLM reads them and decides which to call.
- **Teaching Skill (教学技能)**: A pluggable recipe for producing one kind of teaching content (teaching animation, slide deck, lesson plan, student comment, review deck). Declares its prompt template, the Artifact type it produces, its parameters, and which existing checks apply. All skills are invoked through one agent entry tool instead of one tool per content kind — adding a skill never changes the agent. Slash commands (/动画 /讲评) are user-facing shortcuts to skills. _Avoid_: 技能市场 (a future teacher-authored marketplace — deliberately not built now), Tool (a skill rides on the tool layer, it does not replace it).
- **Learning Skill (学习技能)**: A pluggable recipe for one learning flow (review today's due cards, start adaptive practice, variant training, mastery report) the agent can start, hand off, or read results from. Declares its parameters and which existing service it rides on. All learning skills are invoked through one agent entry tool — adding a skill never changes the agent (same registry pattern as Teaching Skill, ADR-0029). _Avoid_: 全程对话练题 (answering flows live in dedicated components via Hand-off, never as turn-by-turn chat), REST 直调 (that's the page path; the skill is the conversational path).
- **Hand-off (会话接力)**: The one shape for answer flows: the agent opens a session in chat, navigates the student to the dedicated component (context via location.state), and reads real results to debrief when asked back in chat. The agent NEVER carries per-question turns in conversation (recursion budget, chemistry keypad, grading pipeline all live in the component, ADR-0029). _Avoid_: 聊天内做题.

- **Persona (Agent 视角)**: Which tools the Agent may use in one conversation. Four: `teacher` (教师端教研助手, 41 tools), `tutor` (教师权限的精简工具集——出题/题库/文件/解析，无横向查数据；教师可代开、无独立前端，**实际身份是离线评测的默认人格**：evals 全套用例 persona=tutor，且 `load_persona` 对未知 persona 名兜底到它, 21 tools), `student` (学生端AI助教, 10 tools), `parent` (家长端报告查看, 4 tools). Defined in `agent/personas/*.yaml`. Tools register their Persona membership via TOOL_META. At runtime, `langgraph_agent_v2.py` filters the tool list to only those matching the active Persona, and `langgraph_channel.py` 的 `_PERSONAS_BY_ROLE` decides which Personas each Role may open (student→student, parent→parent, staff→teacher/tutor). **Not the same thing as Role (系统角色)** — Persona gates the Agent's tools inside one conversation, Role gates a signed-in person's access to data. The values collide (`teacher`, `parent`, `student` appear in both) but never substitute for each other: a teacher may open a `tutor`-Persona conversation and stays a `teacher` throughout. _Avoid_: 角色 (ambiguous with Role), 把 tutor 说成「面向学生的助手」(它的对话内容是辅导，操作者始终是教师；学生开不了这个视角).
- **Guard State (护栏状态)**: Per-invocation shared state tracking deduplication, call limits, and approval status for destructive tools. Wraps every tool via `_make_guarded_tool`.
- **Plan Run (计划运行)**: One execution of a teacher's multi-step task — an ordered list of steps the agent works through, shown in chat as the todolist. While a Confirmation Gate is open the run does not advance — no later step starts until the gate is decided. A run survives turn boundaries (reject–revise–re-confirm happens across them) and survives being Paused. _Avoid_: 长任务, todolist (UI 投影的叫法，不是实体).
- **Confirmation Gate (确认门)**: A pause point in a Plan Run where work suspends until the teacher decides: **approve** (send and continue the remaining steps), **reject with feedback (打回)** — the artifact is revised per the feedback and re-gated — or **abandon (放弃)** — the gated step is cancelled, the run continues, the draft survives. A gate is a **row, not a memory**: it carries the action to run and the arguments to run it with, and a decision replays exactly that payload — nothing held in process is trusted to say what was approved. It also records which Plan Run step it belongs to, so both deciding and replaying locate the step from the gate instead of matching tool names. A gate is `open`, briefly `executing` (claimed by one approval; the action is under way and its outcome is not yet known), and then `executed` — or back to `open` if the action failed, so the teacher can retry rather than find the gate already consumed. A process that dies mid-replay leaves an `executing` row; the next startup settles it to `in_doubt`, a state only a human can resolve, because whether the action actually ran cannot be read off the record. A gate is enforced by data state, not conversation memory, and one decision unlocks exactly the gated action it was made on — approving one gate never executes another gate's action. Decisions arrive only as explicit three-way decisions; typed natural language while a gate is open counts as reject-with-feedback at most, never as an approval. While any gate is open, the agent performs no further tool work — and that holds across a restart, since "open" is asked of the record. Permission hangs on the student's Data Scope, not on the conversation. _Avoid_: 审批弹窗, 确认框 (frontend-only affordances).

- **Decision Intake (决定入口)**: The single module every teacher decision enters through — card buttons (/resume decision), typed text while a gate is open (/stream), interrupt resume (pilot), and in-doubt adjudication. Pure-read: it classifies a raw request into `Decision | Guidance | Rejected` (action × gate kind × source, carrying the gate snapshot and everything the router needs), so routers never re-classify or re-query the gate row. Fail-closed rejections are part of the interface, not per-endpoint prose. Writes never happen here — the ledger and gate transitions stay inside the executing route's work unit (ADR-0021). One deliberate divergence from mainstream assistants (Claude Code / OpenHands queue typed text while an approval is pending): typed text on a draft gate counts as reject-with-feedback; revisit if the HITL-middleware migration adds an edit affordance. _Avoid_: decision router, approval handler.
- **Paused Run (运行暂停)**: A Plan Run suspended by the system — client disconnect, server restart, or a crashed turn — as opposed to one waiting on a Confirmation Gate. A Paused Run keeps its step statuses and its open gates; it resumes only by an explicit teacher action (deciding a gate, sending the next message), never by automatic retry. _Avoid_: 中断 (ambiguous — also the name of internal machinery), 挂起 (reads as waiting-for-approval).
- **Gateway (网关)**: Pre-classifier that runs before the Agent. Outputs intent type (chat/navigate) and suggested tools. Navigate intents short-circuit the Agent entirely. Chat intents proceed to the ReAct loop with tool hints.

## OCR Pipeline Concepts

- **Upload Session (上传会话)**: Tracks a file from upload through preview to final processing. States: UPLOADED → PREVIEWING → READY → IMPORTING/GRADING → IMPORTED/GRADED → DONE. Persisted in SQLite.
- **Preview (预览)**: Fast, synchronous OCR using `doc_analysis` to show the user what's in their file before they commit to an expensive async operation.
- **Exam Import (试卷导入)**: Uses `paper_cut_edu_vlm` (async, multimodal) to segment and extract question text from exam paper images. The only path for building the question bank from PDFs/images.
- **Grading (判卷)**: Uses `correct_edu` (async) to grade student answer sheets against answer keys. Returns per-question correct/incorrect, reasons, and annotated images. This is the upload-session (`correct_edu`) path.
- **Grading Center (判卷中心)**: The single module through which every answer — online submission, OCR'd answer sheet, or teacher's manual judgment — is graded, saved, and correctable. Layered grading: deterministic normalized match for choice questions, batch LLM semantic judgment for the rest. Effect policy: **practice** results take effect immediately and feed Diagnosis (the teacher may override afterwards, which re-triggers Diagnosis); **exam** results require teacher confirmation before saving. Entry points: the answer-sheet batch page (the de-facto grading-center frontend) and the Agent's grading tools.
  - **Grading Confirmation (成绩确认门)**: Exam answers are written with `confirmed=False` and only become effective (visible to Diagnosis, statistics, wrong-question book) after the teacher confirms per exam. Practice/chat/training answers are effective immediately. Teachers may override any confirmed judgment afterwards.
  - **Pending Semantic (待判定)**: A subjective answer whose semantic judgment has not yet landed. Stored as a visible row (`PENDING_SEMANTIC`), excluded from Diagnosis and statistics until judged. Judgment never silently drops an answer (ADR-0018).
- **Answer Sheet Batch Grading (答题卡批量判卷)**: A separate pipeline from Grading — batch upload (≤10/批) → `ocr_scheduler` + OCR 引擎（PP-OCRv6_small，见 ADR-0004）→ `llm_grading` 分层对比（归一化精确匹配 + 非选择题批量 LLM 语义判定 ≤1 次/卡）→ 老师确认 → `save_grading_results`（须关联真实考试）→ barrier 诊断。判卷入口有两个并列的：Agent 对话（全链路内化，见 ADR-0005）或 `ocr-sheets.html` 批改区。标准答案来自老师粘贴（对话走 `grade_answer_sheets` 内置解析）或题库考试（关联 `exam_id` 后免贴答案）。识别是异步的（~7s/张），对话里 agent 建批后告知等待，老师稍后回来查进度。
- **Fallback (降级)**: When a multimodal API fails, the system falls back to `doc_analysis` (sync OCR) as a degraded but functional alternative. Users are informed of degraded quality.
- **Task Polling (任务轮询)**: Async operations (import, grading) use client-side polling: the server returns a task_id immediately and the frontend polls `GET /api/ocr/tasks/{upload_id}/status` every 2 seconds for progress.
- **Formula Result (公式结果)**: LaTeX-format chemical formulas returned by `doc_analysis`. Preview saves these; import reuses them as hints to the LLM for higher-accuracy structuring.
- **Student Submission (学生作答)**: A student's completed answer sheet, stored with original image, graded image, per-question results, and total score. Belongs to an Exam.
- **Exam (考试)**: A named assessment event (e.g. "2024期中考试") belonging to a Class, containing student submissions and associated questions.

## Background Job Concepts

- **Background Job (后台作业)**: Work executed outside the HTTP request/response cycle — OCR parsing, answer-sheet grading, barrier diagnosis, notification delivery, memory append. Two mechanisms coexist: the OCR path uses a task table + client polling (see Task Polling), and the non-OCR jobs run on a Redis/Celery queue (ADR-0006). _Avoid_: 长任务, 异步任务 (ambiguous with async/await).
- **Diagnosis Job (诊断作业)**: The post-practice LLM barrier diagnosis triggered for one `exam_record_id` after answers are saved. Runs in the background and is idempotent — it re-aggregates `Student.barrier_type` from diagnosed answers rather than incrementing.
- **At-least-once Delivery (至少一次投递)**: The delivery contract for background jobs — a job is delivered one or more times, so retries may duplicate its side effects. Consumers must therefore be idempotent.
- **Idempotency Key (幂等键)**: A stable identifier (e.g. `exam_record_id`, an exam-completion event) used to deduplicate a job's side effects when it is retried. Required for notification delivery and memory append, which currently re-send / double-record.

## Model Egress Concepts

- **Model Egress (模型出口)**: The one HTTP boundary at which the system asks a model for something — and, since the review round, everything whose bytes end up inside that ask: `exam_bank`'s search channels are egresses too, because otherwise replay cannot claim "nothing left this process". Every text-generation call shares the seam: `LLMService` (question generation, grading, parent reports), `DeepSeekProvider` (intent classification and a few skills), the Agent's `ChatOpenAI`, the ad-hoc clients inside `exam_bank`'s web-search channels, and the eval judge. The list is deliberately not counted anywhere — counting by module said "three" when there were five client constructions, and the count rotted the moment it was written down. An AST tripwire is the real inventory: a production file that builds an httpx client for an upstream host without `transport=` fails the suite. Vector embedding is not a network call at all any more (ADR-0024 made it local inference), and the two exam-paper OCR clients are not OpenAI-compatible HTTP shapes — neither belongs on this seam (ADR-0023 决策 8/15). _Avoid_: 模型客户端, provider (that names the vendor, not the boundary).
- **Cassette (录放带)**: One recorded request/response pair for an egress, keyed by provider + model + endpoint + (query string + normalized request body), with credential-shaped query values hashed out, replayed as the bytes this layer saw (transport-level compression is already undone by httpx, so the header saying it is stripped on both sides — see ADR-0023 决策 5). It is a fixture with a reviewable history, not a cache: a cache answers a repeat of the same question, a cassette answers a *regression* of the same question. Tape contents are bounded: answer-sheet images become `sha256 + bytes` digests, the machine's home directory becomes `{HOME}`, and stored URLs stop at the path — but conversation text is verbatim, which is why a server process refuses to boot in record mode (ADR-0023 决策 7/17). Record from evaluation entry points only, never from CI. _Avoid_: 缓存, mock (the seam deliberately does not mock), 快照 (that's a golden-output comparison).
- **Egress Mode (出口模式)**: A process-level switch — `live` (default, the seam is transparent), `record` (pass through and write tapes), `replay` (tapes only, no network). A miss in replay is a configuration error and fails loudly; it never falls back to `live`, because a silent fallback spends real money and writes a tape full of real student data (ADR-0021 keeps the work-unit assertion above this layer).
- **Thinking Mode (思考模式)**: Whether a reasoning model spends its budget on private reasoning before it writes an answer. `deepseek-flash` charges reasoning tokens **inside `max_tokens`**, so the budget decides whether a call returns text or nothing. The rule is per path, not global: fixed-shape work (question generation, diagnosis, parent reports, search summaries, intent classification, the eval judge) turns thinking off; the conversation-and-review path keeps it, because that quality is the reason for the model and it streams (first token still ~0.3s). A cap that the endpoint does not honour is worse than no cap — `ChatOpenAI(max_tokens=…)` becomes `max_completion_tokens`, which this endpoint ignores, so the real cap travels through `extra_body` (ADR-0023 决策 18). _Avoid_: 开关（it is a per-path policy, not one switch）.
