# TODOS

延迟项台账。来源：2026-09-24 `/plan-ceo-review`（计划全文见
`~/.gstack/projects/weiz98417-crypto-chemai/ceo-plans/2026-09-24-gate-product-closure.md`）。
条目按组件分组，组内按优先级排。

## 确认门（agent gate）

### 老师侧通知表与推送

**What:** 给教师加一张通知表和一条读取/推送通路，让"有待确认的门"能在老师不打开页面时也送达。

**Why:** 确认门闭环目前唯一的通知形式是应用内角标（第 4a 项）。老师不带班时才看得到，
一周不上线的结果就是所有门一起变陈旧。E1 的"主动提醒"因此被降级为角标，缺的就是这一块。

**Context:** 仓库里只有 `StudentNotification`（`app/models/database.py:507`）与
`ParentNotification`（`:524`），没有老师侧通知表。幂等形状可照抄
`_notification_id`（`app/services/plan_draft_service.py:54-57`：由 `draft_id+version+student_id`
派生确定性 ID，重复投递得到同一条）。事件源已经就位——第 1 项的决定日志与第 5 项的七类结构化
事件，推送只是它的消费者，所以后置不丢信息。触发条件用第 4b 项的 `last_opened_at`：
满 7 天的 stale 是"该推一条"的自然时机（stale 本身是读侧计算，不写库）。
先定阅读/已读态的归属，再动推送渠道——站内已读与外部通知（邮件/webhook）是两件事。

**Effort:** M（CC 约 40min）
**Priority:** P1
**Depends on:** 第 1 项决定日志、第 4b 项 `last_opened_at` 与两段式 stale

### 待确认页上直接批准/打回布尔门

**What:** 给布尔门开一条带归属校验的 REST 决定通路，让老师能在待确认页就地决定，
不必跳回对应会话。

**Why:** 第 4a 项的待确认页只能"看到并跳进去"。草稿门已有可复用的点击即决定
（`app/api/diagnosis.py:580,669,684,692` → `plan_draft_service.record_decision`），
布尔门（删题库、执行 shell、按次放行的工作区外访问）只有聊天里的 `/resume` 一条通路。
更糟的情况：会话被重置时 `close_conversation`（`app/services/gate_service.py:176-184`）
会把它名下所有 open 门置 `superseded`，"跳回去"根本没有可批的东西。

**Context:** 这条通路必须复用决定入口的全部约束，不能新写一个简化版：`gate_id` 强绑定
（`gate_service.check_binding:145-154`）、按 DB `kind` 分类、教师身份取自请求（REST 侧
`current_user`）、决定日志与门状态同一工作单元（发1 的 fail-closed，日志写不进去则决定不生效）。
少任何一条，就等于给"拿别人的 gate_id 批准一次删库"开门——这是本仓库最不能省的一条校验。
批准之后还要能确定性重放原工具调用，重放依据是 `replay_target`（`gate_service.py:166-172`），
它依赖会话内缓存，跨会话重放怎么取载荷是本条目的主要设计题。

**Effort:** M（CC 约 30min）
**Priority:** P2
**Depends on:** 第 1 项单一决定入口；前置二（作废动作主体化）

### 门被作废时的 SSE 主动推送

**What:** 门被顶掉或过期时，向已打开该会话的前端推一帧事件，让那张确认卡立即置灰。

**Why:** 本期做的是惰性收权（发3）：老师点击时后端按当前状态重判，给人话说明并置灰。
剩下的缺口是"卡在屏幕上，点下去才发现已经作废"。

**Context:** 前置二（发8）已经把自愈式的作废写从读路径
（`gate_service.get_open_gate:118-124`、`134-135`）挪成了一个显式动作，
所以"何时该推"已经有一个明确的调用点。没解决的是"推给谁"：当前 SSE 只在一次会话的响应流里，
多标签页、断线重连、订阅生命周期都要算对，那是另一个子系统。做之前先确认惰性收权
是否已经够用——它对正确性没有影响，只影响观感。新终态文案（`expired` / `superseded`）
已随第 5 项落地，本项不需要再发明措辞。

**Effort:** M（CC 约 30min）
**Priority:** P3
**Depends on:** 前置二（作废动作主体化）；第 4b 项过期语义

### 发送面板的「手填直发」通路未过门

**What:** 让 `POST /learning-plan/{student_id}/send-to-parent` 在「该生从来没有草稿」这条分支上
也先立一道确认门（登记 → 确认卡 → 批准 → 投递），而不是直接建家长通知。

**Why:** 这条分支是页面上老师自己填一段计划、不经草稿就发送的路径（`app/api/diagnosis.py:570-571`
的注释「无草稿（纯页面流程）→ 保持原有直发行为」）。它是 ADR-0015 阶段 2 收编面板旁路时唯一没
收进去的一条：发出去的是家长可见的内容，却没有任何人对「批准」这一步负责。2026-09-24
`/plan-eng-review` 的 R10（D11）选定了先封住更紧急的一半——「草稿已终态被误判为无草稿」（T-20），
把这一半留到单独一期，因为它要给页面流程加门登记 + 确认卡 + SSE 三件套（human 约 1.5d），
且会让老师的手填操作多一次确认。

**Context:** T-20 就位后，判据已经是三分支（`no_draft` / `awaiting` / `terminal:<status>`），
本期只缺 `no_draft` 那一支进门。可复用的现成件：门登记与绑定校验（`app/services/gate_service.py`
`open_gate` / `check_binding`）、确认卡形状与决定流（`agent/channel/langgraph_channel.py`）、
决定日志（第 1 项）。要先定的不是代码而是口径：手填内容算一种新的 `kind`，还是复用 `draft`
kind 并让 `agent_gates.draft_id` 允许为空——这个决定会牵动按 DB `kind` 分类的三处读取。

**Effort:** M（CC 约 40min，含口径 ADR）
**Priority:** P1
**Depends on:** T-20（R10 的三分支判据 + 409 文案）；第 1 项决定日志与唯一决定入口

### SQLite 写锁等待窗口的口径与实测

**What:** 给"一次写入必须在 N 秒内完成"写一条有据可依的口径：实测各写路径的持锁时长，
再决定 `engine` 的 `connect_args={"timeout": ...}` 要不要显式设置、设多少，并把结论写进 ADR。

**Why:** 仓库现在是 WAL 单写事务，引擎建连时没写 `timeout`（`app/models/database.py`），吃的是
sqlite3 默认 5 秒。也就是说：**任何持锁超过 5 秒的写事务，代价是全站其余写请求收到一条
`database is locked`**，用户看到的是失败而不是"在排队"。2026-09-24 `/plan-eng-review` 的 R11 已经
用两条断言把门这条路上已知的长持锁源（决定事务里调模型）消掉了，所以本期不改参数；
但"5 秒"这个数字目前只存在于审查正文里，没有 ADR、没有实测、也没人负责它。

**Context:** 起点是测量而不是调参：给写事务加一条耗时日志（决定路径已在第 5 项有结构化事件，
可复用），跑一轮真实并发（题库批量导入 + 若干会话同时决定），看 P99 持锁时长落在哪里。
有数据之后才有资格讨论设多大：调大 `timeout` 只是把"快速报错"换成"请求挂住"，
在单进程 + asyncio 的形状下更难发现，所以不要凭直觉改。若届时确认要动，一并评估
慢查询是否该被禁止持有写事务（与 R11 的断言同一形状，而不是新增一个超时值）。

**Effort:** S（CC 约 20min，主体是测量与写 ADR，不是改配置）
**Priority:** P2
**Depends on:** 第 5 项门路径结构化日志（持锁耗时可复用同一 helper）；R11 的单元内禁模型断言先落地

### 全站 `--color-text-muted` 的对比度整改（改 token 而不是改页面）

**What:** 把 `--color-text-muted` 的值压深到在白底上通过 WCAG AA（正文 4.5:1），并顺手把
`design/教师端.md` 的三级文字规格补上一条对比度下限。

**Why:** 现值 `#9aa0ab` 在 `#ffffff` 上实测 **2.63:1** —— 正文要求 4.5:1、大字号与非文本 UI 要求 3:1，
两项都不过。它被当成"次要文字"用在 `frontend/src/App.tsx:115`（10px 的「CHEMAI · 教师端」）和
`:147`（退出标签）上，意思是这些字对视力一般的老师就是看不清。2026-09-24 `/plan-design-review` 的
D13 已在新页禁止它承载信息，但既有页面仍踩着这个值。

**Pros:** 一个 token 改动全站生效，不需要逐页改用法；改完 4a 的禁令与这条整改正好闭环（新页不新增
违规、存量一次清）。
**Cons:** 所有浅字统一变深，需要一轮目视回归（教师端 8 页 + 学生/家长 8 页），玻璃卡上的浅字
可能变得"太重"，届时要按分册重排三级文字而不是回滚。

**Context:** 起点是**改值**不是改用法：在 `#9aa0ab` 同色系里压深（`#6b7280` 量级即可到 4.8:1），
用现成的对比度计算确认后再落。若压深后视觉不可接受，说明真正的毛病是"三级文字压得太平"，
那时再讨论合并 `text-secondary` 与 `text-muted`——那是设计系统决定，不是配色微调。
别顺手逐页把 `text-muted` 换成 `text-secondary`：那会让每一页各修各的，token 的洞继续漏。

**Effort:** M（CC 约 1h：改值 + 16 页面目 + 分册补一条下限规则）
**Priority:** P2
**Depends on:** 无（可与任何一轮换肤合并做）

### 教师端响应式断点体系与侧栏折叠（分册承诺未实现）

**What:** 给教师端建立真实的断点体系（`@media` 或 Tailwind 断点），并实现分册 R-T-L2 承诺的
侧栏平板折叠图标化；起点是**先量真实使用姿势**，不是先建体系。

**Why:** `frontend/src` 目前**零 `@media`、零 `lg:`/`md:` 断点**，侧栏是 `App.tsx:104` 的固定
`w-[var(--sidebar-width)] shrink-0` —— 而 `design/教师端.md` R-T-L2 白纸黑字写着"平板折叠图标化"。
文档承诺、代码没做，是 D10 那类"总册/分册与现实脱节"的同一个成因。2026-09-24 `/plan-design-review`
的 D12 已用"按列优先级隐藏"保证 768px 竖屏可读，所以折叠不再是"能不能用"，而是"要不要还这笔账"。

**Pros:** 教室里用 iPad 的老师（课间站着翻）不再被 232px 侧栏吃掉三成宽度；教师端从此有断点可写。
**Cons:** 全站工程，16 个页面跟着回归；如果没人真用平板看教师端，这套断点就是纯维护负担 ——
所以必须先测量再动工。

**Context:** 第一步是取数：现有会话/访问日志里教师端在 <1024px 视口下的访问量占多少（或一轮
真实访谈）。若结论是"几乎没人用平板看教师端"，正确动作是**把分册那句删掉并销账**，
而不是留着它继续诱导随机施工。若确实要用：折叠态宽度、图标 tooltip、角标在折叠态的挂载位置
（tile 右上角）三件事必须先写进分册再动代码 —— D2 的角标在折叠态会失去右缘位置，
这条要一起重审。

**Effort:** M（CC 约 2h，前提是先完成使用姿势测量）
**Priority:** P3
**Depends on:** 测量结论；`/plan-design-review` D2（角标）与 D12（列优先级）需一并重审

### 测试里"拿 `utcnow()` 做减法"的 16 处同类时点隐患

**What:** 把余下 16 处 `utcnow() - timedelta(...)` 式的种时间改成日期锚点，并按每条断言
真正要问的东西选锚法：算「日历日」的锚到当日固定钟点（例：`combine(utcnow().date(), time(12))`），
算「最近 N 小时窗口」的保留相对量、但把断言侧的基准也换成同一个 `now` 变量。

**Why:** 2026-09-26 eng 第三轮 F6 实测到一颗真的响了：`tests/test_student_practice_stats.py`
在 00:00–02:59 UTC 必红 —— 它把"今天"的答题种在 `utcnow()-1h`，刚过零点那一小时里这一笔
实际落在昨天，streak 从 2 变 3。那一颗已修（锚到当日中午，红绿可证）。同一写法还剩 16 处，
分布在 `test_break_glass / test_early_warning / test_permission_roles /
test_spaced_repetition / test_student_notification_chains`（含本文件其余引用）。
不修的代价不是"某个测试红"，而是**一年里总有些时刻套件无因由地红**，
看多的人开始忽略红，真回归就混在里面了。

**Pros:** 一次拔干后 CI 与本地任何时刻同结果；改动全在测试种数据，生产代码零风险。
**Cons:** 至少三处（break_glass / early_warning / spaced_repetition）算的是"最近 N 小时"
而不是日历日，锚点选错会把它们从绿改成红 —— 必须逐条读断言再动，不能机械替换。

**Context:** 起点是清单不是搜索替换：`grep -rn "utcnow() - timedelta\|now - timedelta" tests/ evals/`
拿到 17 处（已修 1 处），逐条标「日历日 / N 小时窗口」两类，再分别按上面的锚法改。
改完在两个极端时刻各跑一遍全量：把机器钟临时调到 UTC 00:10 与 23:50（或用 freezegun 之类的
时钟夹具，若愿意引入就先在 ADR 里定下它），确认同一套断言两边都绿。三个月后回看：本条目
的"完成"判据是全量在两个极端钟点都绿，不是"改了 16 行"。

**Effort:** S（CC 约 25–35min，主体是逐条判锚法而非打字）
**Priority:** P2
**Depends on:** 无（F6 已把唯一实际响过的那颗拔掉，本条目不再阻塞任何一轮）

## 计划运行（plan run）

### 终态级联：前置步被放弃时，后续步不许留在 completed

**What:** 给 `plan_run_service` 的步状态加一条不变式：前置步 `cancelled` / `failed` 时，
依赖它的步不得停留在 `completed`，必须一并收敛并说明原因。

**Why:** 门视图契约评审（`docs/门视图契约-评审输入-20261020.md` §3.3）的现场：`run 0fce5093`
第 5 步 `generate_questions` = `cancelled`，而依赖它的第 6 步 `assign_adaptive_practice`
= `completed`，卡片上写着「出题被放弃、练习却布置了」。老师无法从这张卡判断系统到底做了什么。

这一条与门视图契约**无关**，所以评审把它从那一轮里拆了出来（D6 裁定）。同一族的另一半已经修好：
计划停在确认门上时不许做收尾对账（`_plan_tail_events` 的 skip 分支，测试见
`tests/test_plan_tail_gate_skip.py`）。剩下的是**跨步一致性**，不是视图问题。

**Context:** 转移规则在 `plan_run_service.compute_tail_transition`（纯函数，测试在
`tests/test_plan_run_tail.py`），投影与持久化统一走 `project_transitions` —— 新的级联规则要
长在这个入口上，不能另起一套。起点是 `grep -n "compute_tail_transition\|compute_replay_transition"`：
两条转移规则目前各自为政，级联是第三条，或是一条作用于转移结果的收敛函数。存量脏数据要不要回写
是另一个问题（过期作业 T17 那条通路已经在做同类收敛）。

**Effort:** M（CC 约 40min）
**Priority:** P3
**Depends on:** 无
