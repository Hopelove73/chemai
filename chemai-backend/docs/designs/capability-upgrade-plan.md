# 能力升级方案：工件链 · 记忆卡 · 掌握度（2026-09-30 定案）

> 状态：**已裁定**（grilling 两轮 18 项全部收口，用户"全部同意"）。
> 关联 ADR：[0027 技能注册表](../adr/0027-teaching-skills-registry-not-one-tool-per-content.md)、
> [0028 学科特异性是气质](../adr/0028-chemistry-specificity-is-temperament-not-gate.md)。
> 新词条已入 CONTEXT.md：Artifact、Artifact Panel、Teaching Skill、Memory Card。

## 0. 一句话

补齐"教学内容生产与交付形态"的短板（工件面板 + 教学技能 + 讲评稿），同时把已有的
批改/诊断数据闭环升级为通用 agent 结构上做不到的教育特异性（FSRS 记忆卡 → 掌握概率
→ 自适应出题）。

## 1. 背景与差距

对标 WorkBuddy（通用 agent 客户端 + 教育 prompt 技能包 + 产物预览栏）逐项对照结论：

- **不落后**：家校共育（版本化家长报告 + 确认门 + 发送记录）、学生辅导
  （chemistry_tutor + 6 分步辅导）、学情分析数据面（OCR 批改全链、错题本、周报）。
- **真空白**：教学动画 / 课件 / 教案 / 评语 / 讲评稿等内容生产（全部是要交付给
  右栏的**工件**）；可收缩右侧预览栏；@ 引用与斜杠指令。
- **结构性优势（竞品做不了）**：7.6 万条作答数据 + 心理测量模型 = FSRS / pyBKT /
  IRT-CAT 三层递进。

开源选型（license 已核验，详见 %TEMP% 调研报告）：

| 引入物 | License | 用途 |
|---|---|---|
| huashu-design 方法论 | MIT | 动画/幻灯片生成的 prompt 规范（教学动线、诚实标注） |
| LibreChat Artifacts 组件模式 | MIT | 右栏三态（预览/代码/版本）+ sandbox iframe |
| smilesDrawer（可选）/ 3Dmol.js | MIT / BSD-3 | 工件内结构式渲染素材层 |
| py-fsrs | BSD-3 | 批次 B 记忆卡调度 |
| pyBKT → girth/py-irt + catsim | 全 MIT 系 | 批次 C 掌握概率与自适应出题 |

**红线**：PhET（GPL+NC）仅借鉴教学设计范式；Anki 本体（AGPL）不碰；open-webui /
LobeChat 有附加条款，只借鉴交互设计不抄代码。

## 2. 批次总览与依赖

```
批次 A  工件链（右栏 + 工件实体 + 技能注册表 + 动画/讲评稿两技能 + @引用）
  └─→ 批次 C 分发触达依赖 A 的工件事件；IRT 冷启动标定不依赖 A，可与 A 并行开发
批次 B  FSRS 记忆卡（独立，随时可插，预计一天级接入 + 一周打磨）
批次 C  掌握度（先 pyBKT 概率 → 再 IRT 标定 + CAT 选题；吃 B 的复习信号与 A 之前已有的作答流）
二期    Manim 视频管线（异步任务基建就绪后）
```

## 3. 批次 A · 工件链

### 3.1 工件实体（后端）

表 `artifacts`（新）：

| 字段 | 说明 |
|---|---|
| id / owner_teacher_id | 工件归属 |
| kind | `animation` / `slides` / `doc` / `structure` / `table`（渲染器注册键） |
| title | 展示名（默认取首句） |
| conversation_id | 产生它的会话（可空：列表页也能打开） |
| file_path | 工作区 `AI生成/` 下按日期归档的落盘路径（唯一真源，SSE 只传引用） |
| current_version | 指向 versions 最大序 |
| created_at / updated_at | 索引列表排序 |

表 `artifact_versions`（新）：`artifact_id, seq, file_path, note, created_at`。
**每次保存 = 新版本文件 + 序号**，旧版本永不覆盖（快照式迭代）。

- 落盘复用 filesystem 工作区（`~/ChemAI-Workspace/AI生成/`），静态回链走
  `/static` 同源目录（RDKit 图先例）；**SSE 不内联 HTML 全文**——现有
  `_json_safe_payload` 8000 字符截断（`langgraph_sse_v2.py:68-85`）决定了工件
  走"引用 + 独立事件"。
- 端点：`GET /api/artifacts`（教师工件库列表，按 kind/会话/时间筛选）、
  `GET /api/artifacts/{id}`（含版本列表）、`GET /api/artifacts/{id}/v/{seq}`
  （文件本体，Content-Type 按类型）、`POST /api/artifacts/{id}/distribute`
  （对外分发动作，内部走既有 Confirmation Gate 体系挂门）。

### 3.2 SSE 工件事件

仿 `exam_images` 先例（adapter 从 tool 结果抽字段合成事件）：

```
{type: "artifact", artifact_id, kind, title, version_seq, url}
```

三处落点：adapter 发事件（`langgraph_sse_v2.py` feed 内，tool_end 检测
ArtifactResult）→ `agent-sse.ts` 加类型 → reducer 新 case + 新 ChatItem kind
`artifact`（消息流里渲染工件卡片：标题 + 类型图标 + "在右栏打开"）。

### 3.3 工件面板（前端，教师端先行）

- ChatPage 根容器从两栏变三栏：左会话列表（既有）+ 中聊天列（既有）+ 右工件面板
  （新，可收缩；收缩交互抄现有左栏 `convOpen` 模式）。
- 三 tab：**附件**（upload_sessions 原图预览，点击消息流 UploadCard 联动）、
  **工件**（sandbox iframe 渲染当前工件；HTML 类 `sandbox="allow-scripts"` +
  禁同源 + 强制自包含禁外链 CDN，允许引用本站 /static）、**版本**（版本列表，
  点任一版本切预览，对照当前版）。
- 工件类型渲染器注册表（前端）：`animation/slides → iframe`、
  `structure → <img>`、`doc → ChemMd 渲染`、`table → antd Table`。
  新类型 = 注册一个渲染器，不动面板骨架。
- 幻灯片工件全屏放映：iframe 内自带页面流（huashu-design slide-decks 规范
  1920×1080）。
- .pptx 导出（二等能力）：后端 python-pptx 按 kind=slides 的版本生成下载文件，
  走导出端点（过门）。

### 3.4 技能注册表（ADR-0027）

`app/services/teaching_skills/`（新包）：

```python
@dataclass(frozen=True)
class TeachingSkill:
    name: str            # "teaching_animation" | "review_deck" | ...
    title: str           # "教学动画"
    slash: str           # "/动画"
    artifact_kind: str   # 产物工件类型（注册表键）
    prompt_template: str # 含方法论沉淀（教学动线/诚实标注/自包含规范）
    params: dict         # 参数 schema（topic 必填，grade/知识点可选）
    checks: tuple        # 依赖的既有校验（四要素之一，ADR-0027）
    author: str          # 预留，本期恒为 "builtin"
```

- agent 侧**单一入口工具** `invoke_teaching_skill(skill, params, context)`：
  docstring 按"何时用/会发生什么/下一步/NOT for"格式，进 TOOLS（工具总数 46→47），
  persona=teacher。
- 首发两技能：
  1. **teaching_animation**：prompt 规范移植 huashu-design 动画方法论（MIT）——
     自包含 HTML+Canvas、参数集中区、教学动线（观察→猜→转→验证→合）、浅底投屏
     大字、诚实标注（ADR-0028 语言层）；学科渲染约定写进模板（`\ce{}`、SMILES 图，
     ADR-0028 渲染层）。
  2. **review_deck**（讲评稿）：输入=一次考试的批改数据（正确率/错题分布/迷思
     归因），产出=带错因分析的讲评工件；学科论断走既有四维校验习惯（ADR-0028
     对表达型/论断型产物的分界）。
- 斜杠指令：输入框解析 `/动画 盐类水解` 直接触发技能调用（前端解析 → 塞进消息
  发送前缀），不新增后端机制。
- @ 引用：输入框 @ 弹出附件与工件列表，选中项以结构化引用进提示词，替换裸
  `upload_id` 拼接。

### 3.5 学生触达

工件卡片"分享给学生"（确认门）→ Notification Dispatch 既有链 → 学生收件箱出现
"老师分享"卡片 → 点开全屏预览（消费者模式，无编辑/版本/右栏）。

### 3.6 里程碑

| # | 内容 | 验收 |
|---|---|---|
| A1 | 工件表 + 落盘 + 静态回链 + SSE artifact 事件 + 消息流工件卡片 | 后端测试：生成→事件→按 id 取回全文；e2e 手工：对话产 HTML 落盘可打开 |
| A2 | 右栏三 tab + 沙箱渲染 + 版本切换 + 附件原图预览 | 前端测试（vitest）：tab 联动、iframe sandbox 属性断言、版本切换 |
| A3 | 技能注册表 + invoke_teaching_skill + teaching_animation 技能 | evals：给定"做个盐类水解动画"端到端产出可播放自包含 HTML（离线录放带） |
| A4 | review_deck 技能 + @ 引用 + 斜杠指令 + 工件库列表页 | evals：讲评稿吃批改数据；前端：@ 弹层与斜杠解析 |
| A5 | .pptx 导出 + 分享给学生（门+收件箱） | 后端：门决策记录；e2e：学生卡片全屏预览 |

## 4. 批次 B · FSRS 记忆卡

- **升级替换既有调度，不并存**：仓库已有固定节点艾宾浩斯体系（SpacedRepetitionEngine
  1/3/7/14/30 天阶梯、ReviewTask 表、`/api/review` 端点族）。FSRS 接管调度内核，
  `/api/review` 对外契约不变；存量 ReviewTask 迁移为记忆卡初始状态（review_level/
  consecutive_correct 映射初始难度与稳定度）后，艾宾浩斯阶梯代码退役。
- **建卡**：判卷完成钩子（confirm_exam_grades / 保存批改结果处）对每道错题
  upsert Memory Card（题目 id + 学生 id 唯一），零老师操作。
- **调度**：`pip install fsrs`（BSD-3）；复习信号=重做自动判定（客观题走既有
  判卷逻辑）+ 自评三档兜底（主观题）；两种信号都喂 FSRS 得下次到期日。
- **入口**：学生端"今日复习"清单挂进学习计划的复习板块（不建独立系统）；
  教师端班级复习进度进周报一节。
- 验收：判卷后卡片自动出现；复习一次调度滚动；今日清单只出到期卡。

## 5. 批次 C · 掌握度

1. **pyBKT**（先）：按知识点拟合掌握概率；诊断页/周报从"答对率"升级为概率条 +
   薄弱 Top N；家长报告维持文字化口径（不出现概率数字）。
2. **IRT 标定 + CAT**（后）：演示库 7.6 万条作答冷启动跑通链路；真实部署按
   "每题 ≥30 条作答"门槛自动降级回难度 1–5 先验——降级规则写死在选题服务，
   无"标定了一半"中间态。CAT 接管 `assign_adaptive_practice` 的选题内核，
   对外行为（作业批次、进度查询）不变。

## 6. 不做清单（本期明确出界）

论文写作/网站开发等通用长尾（架构不挡：技能注册表天然可承载）；老师自定义
技能与技能市场（author 字段已预留）；定时任务；学生端工件面板；molstar /
Remotion（license 或场景不匹配）；Manim 视频管线（二期，等异步任务基建）。

## 7. 测试与评估策略

- 后端：工件 CRUD/版本不变式/门挂接单测；技能注册表契约测试（四要素齐全、
  artifact_kind 必须在渲染器注册表里——两侧注册表对得上）。
- evals：动画技能加离线录放带用例（产出自包含 HTML 的结构性断言：无外链
  `<script src>`、含参数集中区、含诚实标注文案位）；讲评稿技能对拍批改数据。
- 前端：sandbox 属性、@ 弹层、斜杠解析、版本切换 vitest。
- CI：TOOLS 断言 46→47 一次到位（invoke_teaching_skill），后续技能新增不再动它。
