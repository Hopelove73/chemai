# 能力升级工程 Spec：工件链 · 记忆卡 · 掌握度

| 版本 | 日期 | 状态 |
|------|------|------|
| 1.0 | 2026-09-30 | ready-for-agent |

> 来源：[定案方案](../designs/capability-upgrade-plan.md)（grilling 18 项裁定）。
> 词汇表：CONTEXT.md（Artifact / Artifact Panel / Teaching Skill / Memory Card /
> Confirmation Gate / Notification Dispatch / Plan Run）。ADR：0027（技能注册表）、
> 0028（学科气质）、0019（单 Agent）、0022（渲染住视图层）、0015（门唯一性与数据态强制）。

## Problem Statement

老师在教学内容生产上只有"文字回答"一种交付形态：没有可预览的工件、没有右侧面板、
上传图片看不到原图；学生的错题数据没有变成复习调度，掌握度停留在答对率。数据闭环
的优势没有转成产品能力。

## Solution

三个独立可演示的批次：A）工件实体 + SSE 工件事件 + 右侧工件面板 + 教学技能注册表
（首发动画、讲评稿两技能）+ @ 引用/斜杠指令 + 分享与导出；B）判卷钩子自动建记忆卡 +
FSRS 调度 + 今日复习；C）pyBKT 掌握概率 + IRT 冷启动标定 + CAT 选题（带自动降级）。

## 测试接缝（Seams）

原则：能复用既有接缝就不新开；新接缝取最高层。本特性共一个新主接缝 + 四个既有接缝：

| 接缝 | 层级 | 状态 | 承载 |
|---|---|---|---|
| **Artifact API**（REST：列表/详情/版本文件/分发/导出） | 服务边界 | **新（唯一新主接缝）** | 工件持久化、预览取数、分发过门、pptx 导出 |
| SSE `artifact` 事件 | 事件通道 | 既有扩展（先例：`exam_images` 由 adapter 从工具结果合成） | 生成时刻的实时通知（只传引用不传全文） |
| Agent 工具接口 | 工具层 | 既有（TOOLS 注册表 + docstring 选择，ADR-0019） | `invoke_teaching_skill` 单入口分发 |
| 判卷落库钩子 | 服务内部 | 既有（批改结果保存路径） | 记忆卡自动建卡（upsert） |
| 学习计划 compute 环节 | 服务内部 | 既有（计划草稿计算） | "今日复习"清单挂接 |

不新开：技能不做独立服务（注册表是进程内模块）；掌握度不做新端点（读现有诊断/
周报出参的升级）；CAT 不做新入口（接管自适应练习的选题内核，对外行为不变）。

## Implementation Decisions

### 批次 A · 工件链

**数据**：两张新表。`artifacts`（归属教师、kind、标题、产生会话可空、落盘路径、
current_version、时间戳）；`artifact_versions`（artifact_id、seq、file_path、note、
时间戳，(artifact_id, seq) 唯一）。每次保存 = 新版本文件 + 递增 seq，旧版本永不
覆盖。文件落工作区 `AI生成/` 日期归档目录，真源在盘上，库里只存路径与索引。
（实现注：file_path 落在版本行而非主表——每个快照各有自己的文件，主表声明按此理解。）

**SSE 工件事件**（来自定案方案的原型形状）：

```json
{ "type": "artifact", "artifact_id": "...", "kind": "animation",
  "title": "盐类水解教学动画", "version_seq": 1, "url": "/static/..." }
```

背景约束：adapter 的 tool_result 载荷有 8000 字符截断上限，因此事件**只传引用**，
全文走 `/static` 同源静态目录（RDKit 图先例）。前端三处落点：事件类型定义、reducer
新 case、消息流新 ChatItem kind `artifact`（工件卡片：标题 + 类型图标 + 在右栏打开）。

**API**（教师端，数据权限随 Data Scope）：

- `GET /api/artifacts` — 工件库列表（kind/会话/时间筛选）
- `GET /api/artifacts/{id}` — 详情含版本列表
- `GET /api/artifacts/{id}/v/{seq}` — 版本文件本体（Content-Type 按 kind）
- `POST /api/artifacts/{id}/distribute` — 对外分发（内部走既有 Confirmation Gate
  数据态，ADR-0015；分发动作 = Notification Dispatch + 学生收件箱卡片，学生点开
  **全屏预览**，消费者模式——无编辑、无版本、无面板三态；学生视野
  钉在**被分享过的版本**上（每版本一次门决策，教师出 v2 不自动可见））
- `POST /api/artifacts/{id}/export` — .pptx 导出（实现注，A5 审查定案：限 kind∈{slides,doc}；导出是老师自己设备上的副本，不产生新数据边界，**不过门**——与原计划「同样过门」的偏离在此记账，CONTEXT.md Artifact 词条已同步）

**工件面板（前端，教师端 ChatPage）**：三栏布局（左会话列表不动、中聊天列不动、
右新面板可收缩，收缩交互复用左栏模式）。三 tab：附件（upload_sessions 原图预览，
消息流 UploadCard 联动聚焦）/ 工件（当前工件渲染）/ 版本（版本列表切换预览）。
渲染器注册表（前端）：全类型走沙箱 iframe（A2 实装：面板统一 SandboxFrame 渲染
blob: URL；doc 类产物是**自包含 HTML** 而非 markdown——面板不做 markdown 渲染，
HTML 打印也更友好）。iframe 沙箱策略：`sandbox="allow-scripts"`、禁同源、
禁止外链 CDN（工件强制自包含）。取数走 blob（img/iframe 子资源带不上
Authorization header，面板 fetch 成 blob 再喂 src）。幻灯片全屏放映由工件
HTML 自带页面流承载。

**教学技能注册表**（ADR-0027；原型形状来自定案方案）：

```python
@dataclass(frozen=True)
class TeachingSkill:
    name: str            # "teaching_animation" | "review_deck" | ...
    title: str; slash: str
    artifact_kind: str   # 必须在前端渲染器注册表里存在（契约测试钉住）
    prompt_template: str # 方法论沉淀
    params: dict
    checks: tuple        # 依赖的既有校验（如四维审核；复用不新造，ADR-0027 四要素之一）
    author: str          # 本期恒为 "builtin"
```

进程内注册表 + agent 单入口工具 `invoke_teaching_skill(skill, params, context)`
（docstring 按"何时用/会发生什么/下一步/NOT for"格式；persona=teacher；TOOLS 46→47，
CI 冒烟断言一次改到位）。技能模板必须写入（ADR-0028 语言/渲染层）：自包含禁外链、
参数集中区、教学动线（观察→猜→转→验证→合）、诚实标注文案位；化学式在自包含
HTML 里用 `<sub>/<sup>` 或 Unicode 下标（KaTeX/mhchem 需要外链，进不了工件——
`\ce{}` 约定适用于聊天消息渲染层，ADR-0028 渲染层义务按此口径落进模板）。
首发两技能：`teaching_animation`（方法论移植 huashu-design，MIT）、
`review_deck`（吃批改数据：正确率/错题分布/迷思归因；学科论断沿用既有四维
审核习惯——ADR-0028 对表达型/论断型的分界；context_data 为必填参数，
分发层按技能声明 fail-closed）。依赖红线：chempy 不引入（ADR-0028）。

**@ 引用与斜杠指令**：输入框 @ 弹层选附件/工件，结构化引用进提示词（替换裸
upload_id 拼接）；`/动画 盐类水解` 前端解析为技能调用前缀。均为前端机制，不加后端端点。

### 批次 B · 记忆卡

**与既有系统的关系（升级替换，不并存）**：仓库现有一套固定节点艾宾浩斯复习体系
（`SpacedRepetitionEngine` 按 1/3/7/14/30 天阶梯推进、`ReviewTask` 表、`/api/review`
端点族、practice 按 `next_review_at` 取题）。FSRS 记忆卡**替换其调度内核**：
`/api/review` 对外契约（due/count/submit/history）保持不变，由 FSRS 内核供数；
存量 `ReviewTask` 迁移为 Memory Card 初始状态（由 review_level / consecutive_correct
映射初始难度与稳定度），迁移后艾宾浩斯阶梯代码退役。产品上学生端入口不变，调度从
"固定节点"升级为"按记忆表现自适应"。

判卷保存钩子按（学生, 题目）upsert Memory Card；调度库 FSRS（py-fsrs，BSD-3）。
复习信号双轨：重做自动判定（走既有判卷逻辑）为主，主观题自评三档（忘了/模糊/记得）
兜底，两种信号都喂调度。"今日复习"挂学习计划复习板块；班级复习进度进周报一节。
不建独立复习系统、不加学生端新入口。

### 批次 C · 掌握度

先 pyBKT 按知识点拟合掌握概率 → 诊断页/周报升级为概率条 + 薄弱 Top N；家长报告
文字化口径（不出现概率数字）。IRT 冷启动用演示库作答数据跑通链路；真实部署按
"每题 ≥30 条作答"门槛自动降级回难度 1–5 先验——降级规则写死在选题服务内部，
不存在标定中间态。CAT 接管自适应练习选题内核，作业批次/进度查询对外行为不变。

**实现注（批次 C 偏离记账）**：
- pyBKT 的 fit/predict 与本环境 sklearn 版本不兼容（log_loss numpy API 断裂），
  且本场景只需要「按学生序列推掌握概率」——落为**自实现顺序 BKT**（固定经典
  先验 0.25/0.15/0.15/0.20，约 40 行，零新依赖，确定性可测，
  app/services/mastery_service.py）。已知取舍：P_GUESS=0.20 对填空/计算题
  （无猜测可蒙）系统性偏高，掌握概率只宜读作粗粒度信号。
- 1PL 标定 + 网格 EAP + 最大 Fisher 信息选题为 numpy 自实现（catsim 的 API
  面向完整仿真流程；两个纯函数自实现更可控，catsim 不进依赖）；
  app/services/assessment_service.py。门槛口径收严为"每题 ≥30 条**不同学生**
  作答"（重做不刷满门槛）；标定入口 = 应用启动幂等全量重算（冷启动即跑通）。
- CAT 的实装形态是 **θ 定向**：θ 可估时，自适应布置的难度定向（zpd）由 θ 映射
  替代正确率估计。bank 源检索结果不带题目 id，CAT 选序待检索服务返回 id 后再接
  （审查 P0-1 修正：原 bank 接线是 no-op，已删）。
- 作答数据生效门：掌握概率/标定/能力估计三条查询都只认 `confirmed = 1` 且
  非未判定（PENDING_SEMANTIC/缺标准答案）的作答——与 zpd 的既有口径一致。

## Testing Decisions

**好测试只打外部行为**：经接缝（API/事件/工具）断言可见结果，不测内部函数。先例：

- 端点+门：`tests/test_pending_gates_endpoint.py`、`test_approval_resume_plan.py`
 （分发/导出过门的验收打法照此）
- adapter 事件合成：`tests/test_sse_adapter.py`（`artifact` 事件同型）
- 离线 LLM 行为：录放带（`tests/test_llm_transport.py` / ADR-0023）——技能生成
  动画的结构性断言（无外链 `<script src>`、含参数集中区、含诚实标注文案位）用
  离线重放跑，不打网络
- 工具注册契约：`tests/test_gate_kind_single_source.py` 风格——技能注册表**四要素
  齐全**（prompt 模板/产物类型/参数/依赖校验）且与前端渲染器注册表的 kind 集合
  对得上（两侧契约测试）
- 前端：`frontend/tests/*.test.tsx` 先例（sandbox 属性、@ 弹层、斜杠解析、版本切换）

分模块：工件 CRUD/版本不变式/门挂接（API 接缝）；技能分发与模板义务（工具接缝）；
建卡钩子与调度滚动（判卷/计划接缝）；掌握概率与降级（诊断接缝，含"数据不足题回退
先验"的负路径）。

## Out of Scope

与 PRD 一致：通用办公能力、自定义技能与市场、定时任务、学生端工件面板、Manim（二期）、
molstar/Remotion、化学事实强制校验管线（ADR-0028）。

## Further Notes

里程碑 A1–A5 与批次依赖见定案方案第 3.6 节。CI：TOOLS 断言 46→47 一次到位。
所有 license 红线见 PRD Further Notes。批次 B 与 A 无依赖可并行。
