## Why

学生端移动页面只有 6 页、agent 仅 10 工具，而教师端 13 页 / 41 工具。走查结论：后端是「三端全量」的后端——复习全家桶（`/api/review/*`）、错题变式训练（`/api/practice/wrong-topic/*`）均已按 `allow_self` + ScopeGuard 建成并带测试，但学生端前端零调用；更严重的是**主观题（fill_blank/short_answer/calculation）在移动端没有任何输入控件，学生无法作答**，提交静默落空串。学生每日闭环（练习→判卷→错题→复习）卡在三个断点：作答断、复习断、错题练断。

## What Changes

- **修改** 移动 `ChatPanel.tsx` 换用教师端已测试的 `agent-stream-reducer.ts`（纯函数 + 11 用例），补消费 `artifact / navigate / populate / action / context`，`gate_open` 只读提示；接 `/api/agent/chat/langgraph/resume` 断流重同步
- **修改** 5 处重复的移动底栏 tabs 数组收敛为 `MobileLayout.tsx` 单一真源，预留徽章挂点
- **新增** 作答输入器 `ChemComposer`：多空字段 + 化学符号键盘（⇌ ↑↓ Δ 点燃 催化剂等一键插入）+ `\ce{}` 实时预览（MathLive，MIT）；主观题成绩页展示逐题对错 + 评语 + `pending_semantic` 状态
- **修改** Question/StudentAnswer 模型：补 `question_type` 列、answer 长度放宽（现 String(256) 且判卷截断 256，多空 JSON 会截断）、多空采用 JSON 数组约定、判卷按空拆分
- **新增** 复习中心页消费 `/api/review/*` 全家桶（到期→重做/自评→历史）+ 底栏到期徽章
- **新增** 复习连击（Review Streak）：`student_streak` 表 + 纯函数规则（每日清卡续连击、冻结卡上限 2、漏天自动消耗）+ py-fsrs / fsrs-optimizer 对齐与校准脚本
- **修改** 错题本升级：错因三色标签（读 probe_dimension 归因）+「标已掌握」（端点已有）+ 变式训练会话 + 消灭率清单（BKT 口径薄 REST）

## Capabilities

### New Capabilities
- `answer-composer`: 学生主观题作答输入器（多空字段、化学符号键盘、\ce{} 预览、多空判卷、逐题成绩）
- `review-center-streak`: 今日复习中心（FSRS 到期队列消费）+ 复习连击资产 + 官方 FSRS 对齐
- `wrong-book-remediation`: 错题举一反三闭环（错因标签、标已掌握、变式训练、消灭率）

### Modified Capabilities
- `mobile-stream-consumption`: 移动对话事件全消费 + 断流 resume + tabs 单一真源（现无 spec，本次建立移动端流消费契约）

## Impact

- **新增文件**: `frontend/src/components/mobile/ChemComposer.tsx`, `frontend/src/lib/chem-keypad.ts`, `app/services/streak_service.py`, `scripts/calibrate_fsrs.py`, `frontend/src/pages/mobile/student/ReviewCenterPage.tsx`（重建）
- **修改文件**: `frontend/src/components/mobile/ChatPanel.tsx`, `frontend/src/components/mobile/MobileLayout.tsx`, `frontend/src/pages/mobile/student/{PracticePage,WrongBookPage,ProfilePage,StudentChatPage}.tsx`, `app/models/database.py`（Question.question_type、长度迁移）, `app/services/grading_center.py`（按空拆分）, `app/api/analytics.py`（elimination 端点）, `app/api/practice.py`（payload 字段统一）
- **迁移**: alembic —— Question 加 `question_type`（默认 single_choice）；`questions.answer` 与 `student_answers.student_answer` 放宽至 Text；`student_streak` 新表
- **依赖**: MathLive (npm, MIT)、py-fsrs + fsrs-optimizer (pip, MIT/BSD-3)、genanki（可选，MIT）
- **测试**: streak 纯函数表驱动、chem-keypad 常量、多空判卷归一化、reducer 全事件类型、S7 之前的消灭率口径单测
