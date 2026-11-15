# ADR-0029: 学习流程走会话接力 + 学习技能注册表，不给作答流造对话内交互

**Date**: 2026-04-07
**Status**: Accepted（用户裁定：agent 统筹调研 grilling 三轮 Q1-Q12 全按推荐）

## Context

2026-09/10 三端升级新建的学习能力（FSRS 复习、CAT 智能练习、错题变式、BKT 掌握度、
家长三卡/消灭率/AI 摘要、学生计划草稿确认门）全部走「REST(allow_self) + 前端直调」
单轨，agent 工具面零覆盖。后果有两层：一是学生 persona 的 system_prompt 宣称
「完整讲解后出同类变式当场练」，但白名单里没有任何变式/CAT/FSRS 工具——模型在
裸出题（不经审核链、不判卷、不建记忆卡），宣称与供给不符；二是 agent 作为项目
核心（LangGraph 单 ReAct，ADR-0019）却触达不了四条深模块管线。

「把练习搬进对话」存在两种形态：

- **全程对话练题**：agent 逐题出题、用户逐题文字作答。撞三堵墙：一轮 8 题 CAT
  ≈ 16+ 次工具往返 > recursion_limit=24；逐题文字作答失去专用组件（化学键盘/
  选项/装置图画布）；语义判卷与 FSRS 建卡挂在 REST 判卷路径上，绕开即丢管线。
- **会话接力（hand-off）**：agent 在对话中开启会话 → 跳专用组件作答 → 完成后
  学生回到对话，agent 读真实结果点评。

## Decision

1. **会话接力是作答流（智能练习/变式训练/复习清卡）的唯一形态**。agent 负责
   开启、解释、跟进（读结果点评），NEVER 在对话内逐题往返。此边界为终审，
   后续「为什么不能在聊天里直接做题」的讨论以本 ADR 为准。
2. **接棒协议复用既有机制，零新原语**：工具返回 `_route`（navigate 事件，SSE
   adapter 既有投影）跳转目标页；会话上下文（智能练习知识点/变式原题 id/复习
   来源）经 `location.state` 传递（知识地图「练习此点」`smartKp` 先例：state
   不进 URL、页面消费后 replace 清空防重入）。作答完成后不自动回推——学生回
   对话询问时，agent 按需调读侧技能拿真实数据（θ 变化/消灭率/连击）点评。
3. **学习技能注册表（learning skills registry）**与 ADR-0027 教学技能同构：
   `app/services/learning_skills/` 注册表声明 {名称/中文标题/参数 schema/背后
   service 调用}；agent 只见一个入口工具 `invoke_learning_skill`。首批注册
   四项（review_today / smart_practice / variant_training / mastery_report）。
   persona 面（2026-04-07 审查勘误：家长端 ChatPanel 不接导航且无学生作答页，
   开作答会话在家长侧无法接棒）：**开作答会话类（smart_practice / variant_training /
   review_today 清卡引导）student 专属；读侧（mastery_report、复习/练习状态读）
   student+parent**。家长要孩子练走已工具化的 parent_assign_task 派任务。
   **注册表是学习流程的唯一扩展点**——新增
   学习流程 = 注册一项，agent/TOOLS 数/前端映射零改动。
4. **家长周报单工具拼三源**：`parent_weekly_summary` = build_cards + elimination
   + compose_summary_text 一次拿全（三个现成函数拼装，与 mastery_report 共享取数）。
5. **学生计划确认门直接执行不过门卡**：`decide_plan_draft`（approve/revise+留言）
   调 decision 端点同源 service，allow_self + 本人归属校验即安全边界；低频决策
   动作，门卡仪式冗余（与 ADR-0015 教师发送侧的门语义不同：那是**发出**他人
   可见的副作用需要确认，这是**本人**对收到的草案表态）。

## Consequences

- 学生/家长 persona 从「无学习流程工具」到四项技能 + 周报 + 确认门；teacher
  persona 不接学习技能（教师走自己的生成/派发工具链）。
- 作答组件的入口多一个来源（对话接棒），但会话启动仍走既有 REST（smart/start、
  training/create、review 页），agent 工具是「带上下文的启动器」不是旁路。
- evals 需补学习技能的基线条目（工具选择 + 接棒事件断言）；「裸出题」回归以
  「对话出题必须走 variant_training/generate_questions 工具」为断言面。
- 实施前置（2026-04-07 审查补记）：① smart_practice 的会话编排（候选池→建会话→
  发题）现住端点体内（smart_practice.py:124 起），批②须先抽
  `smart_practice_service.start_session()`（ADR-0012 口径），其余三技能背后
  service 函数已可直接调用；② student.yaml 第 3 层「出题并等学生作答」与本文
  接力终审冲突，批②注册 variant_training 后同批改为「调 variant_training 开启
  变式训练（跳练习组件作答）」——该修订是 evals 裸出题断言的前提。
- 各技能的返回契约（含分支形状与接棒载荷）单一事实源：
  docs/designs/learning-skills-contract.md。

（相关：ADR-0027 注册表模式先例、ADR-0019 单 agent、ADR-0014 工具层数据范围、
ADR-0015 门语义对照）
