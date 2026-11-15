# E2E 联调清单 —— agent 统筹学习流程（ADR-0029 批②-⑥）

> spec #24 验收载体。每条含验证方式与状态；`编排测试` = monkeypatch/契约测试
> （tests/test_learning_skills.py 等），`真跑` = 浏览器 + 真 LLM（DEEPSEEK）。
> 基线：improve 分支批⑤后。evals fast 137/137（含 learning_skills 17 断言）。

## 前置
- 服务：uvicorn :8001/8002（dist 由后端托管）
- 账号：学生 241901/123456（周翔，29 张到期卡）、家长 241901家长/123456（周建国，绑定周翔）

## 学生侧
- [x] L1 SMILES 结构式回归（对话问苯/甲苯 → 围栏标记 → 结构图渲染）
  - ✅ 2026-10-05 真跑通过（f16a745 验收，截图在案）；R-H3 唯一约定
- [x] L2 复习接棒 + debrief：「我该复习啥」→ invoke_learning_skill → 接棒 /m/review；
      回聊「我清完了」→ debrief 读真实连击/冻结
  - ✅ 2026-10-07 真跑通过（见下方实跑记录）；接棒载荷/rebranch 编排测试钉死
- [x] L3 智能练习接棒：「来一轮盐类水解的智能练习」→ 开会话 → 接棒 /m/practice
      直接进作答（无孤儿会话）
  - ✅ 2026-10-07 真跑通过（见下方实跑记录）
- [x] L4 变式训练：「出两道这道错题的变式」→ 开会话 → 接棒 /m/wrong 训练视图
  - ✅ 编排层全覆盖（归属拒绝/生成失败/接棒载荷，test_learning_skills 13 条）+
    渲染层与 L2/L3 同一 handoff 解析器；LLM 变式生成链路为 E4 已真跑存量
    （wrong_question_trainer 未动，仅 prompt 增 [SMILES:...] 约定）
- [x] L5 计划确认门：「确认计划 / 太难了打回」→ decide_plan_draft
  - ✅ 编排层 8 条全覆盖（approve 回滚/revise 先算后写/归属拒绝）；浏览器全链
    需先走教师派发造活动草稿，随下次自然 E2E 回合补跑（不虚报）

## 家长侧
- [x] L6 周报三源：「孩子这周怎么样」→ parent_weekly_summary（三卡+消灭率+人话+一件事）；
      「哪个知识点最弱」→ mastery_report（E15 口径交叉一致）
  - ✅ 2026-10-07 真跑通过（见下方实跑记录）；未绑定拒绝编排测试钉死
- [x] L7 persona 面回归：teacher/tutor 不接学习技能（build_tools 过滤）
  - ✅ 注册面断言（evals learning: META/yaml 四 persona 白名单）+ 冒烟通过

## 实跑记录（2026-10-07，uvicorn :8002 + 430 视口，DEEPSEEK 真 LLM）

### L2 复习接棒（首跑抓到断点 → 修复 → 复跑通过）
- 学生对话「我该复习啥，今天该清多少张卡？」→ **学习技能·完成**卡 → 回复
  「今天有 29 张卡到期，去复习中心清卡吧……」（29 与底栏徽章一致，真实 due_count）
- **首跑暴露接棒断点**：SSE 原始流里有 navigate+state（服务端正常），但页面没跳——
  三处修复：① langgraph_sse_v2.py navigate 事件补发 state 字段（两条发射路径）；
  ② agent-stream-reducer.ts page 已是路径（/m/review）不再重复加 `/` 前缀（被
  白名单过滤的真凶）+ 透传 state；③ ChatPanel send() 每轮重建的 ctx 覆盖
  ctxRef 初值、onNavigate 丢第二参——两处都改为携带 state
- 复跑：自动跳 **/m/review**，页头「复习中心 · 来自 AI 助教」，到期队列可答 ✅
- debrief：回聊「我清完了」→ due_count 真实口径回复（未清时诚实纠偏「还有 N 张」）✅

### L3 智能练习接棒
- 「来一轮盐类水解的智能练习」→ 工具完成 → 自动跳练习页进入逐题作答视图
  （agent 建的会话直接续接，未自调 /smart/start，无孤儿会话）✅

### L6 家长周报
- 家长登录 → 概览 → 对话「孩子这周怎么样」→ parent_weekly_summary·完成 →
  回复含未完成/正确率环比/连击/消灭率 + 一件事建议；载荷与概览三卡同源 ✅

## 已知非目标
- 作答完成后自动推送点评（ADR-0029：按需回聊读取）
- 家长端接棒导航（无作答页，读侧结果在气泡内）
