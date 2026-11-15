# 学习技能契约（ADR-0029 批②的唯一事实源）

> 2026-10-07 统筹方案审查后补页（报告 Top recommendation 点名的缺口：不先钉死返回
> 契约，四个注册表项会各自发明形状）。每技能定：参数、返回分支、接棒载荷（navigate +
> location.state）。实现照此写，前端照此接；改契约先改本文。

## 通则

- 入口工具 `invoke_learning_skill(skill: str, params_json: str)`——分发层不认识具体
  技能名（ADR-0027 同构），返回统一为 `{"text": <给 LLM 的点评素材>, ...}`；
  接棒字段放顶层 `_route`（guard 已有投影链路：guard.pop → last_route → SSE navigate）。
- **开作答会话类**（smart_practice / variant_training / review_today 引导）：student
  专属；`_route = {"navigate": true, "page": <目标页>, "state": {…}}`，state 键沿
  smartKp 先例（不进 URL、页面消费后 replace 清空防重入）。
- **读侧**（mastery_report 等）：student+parent；无 _route。
- 数据范围（ADR-0014）：工具层 scope 以登录身份定 student_id——学生=本人，
  家长=绑定孩子（parent_* 既有 `scope: student_name` 模式）。

## 四技能契约

### review_today（今日复习）
- 参数：`{}`（读侧引导）/ `{"action": "status"}`（纯读，parent 可用）。
- 返回分支：
  - 有到期卡：`{"text": "今日到期 N 张（化学式 X / 概念 Y…前 3），连击 D 天、冻结 F 张",
     "due_count": N, "streak": {"current": D, "freeze": F},
     "_route": {"navigate": true, "page": "/m/review", "state": {"fromChat": "review"}}}`
  - 无到期卡：`{"text": "今日复习已清空，连击 D 天", "due_count": 0}`（无 _route）
  - debrief（学生清完回来说"我清完了"）：`{"action": "debrief"}` → 读
    `memory_card_service.due_count`（=0 佐证）+ `streak_view`（连击/冻结变化），
    `{"text": "已确认清空：连击 D→D'，冻结 F 张", "cleared": true}`
- 背后：`memory_card_service.due_cards / due_count / review_card`；`streak_view`。

### smart_practice（智能练习 CAT）
- 参数：`{"kps": ["盐类水解"], "max_items": 8}`（kps 缺省=薄弱前三）。
- 前置：批②先抽 `smart_practice_service.start_session(db, student_id, kps, max_items)`
  （现住 smart_practice.py 端点体内，ADR-0012）。
- 返回分支：
  - 可开（2026-10-07 批② 实现勘误，与 variant_training 同款：页面续接
    agent 已建会话直接进作答，不自调 /smart/start 留孤儿会话）：
    `{"text": "已开一轮智能练习（知识点：…，最多 8 题），去练习页开始",
     "session_id": "<practice_id>", "first_question": {...}, "theta": …,
     "_route": {"navigate": true, "page": "/m/practice",
                "state": {"smartSession": {"practice_id": …, "question": {...},
                                            "theta": …}}}}`
  - 无可练知识点：`{"text": "还没有可练的知识点：先做几道题，或告诉我具体考点"}`（400 同源文案）
  - debrief：`{"action": "debrief", "session_id": …}` → 读 finish 快照字段
    （theta_delta / answered / uncalibrated_ratio / weak_kps）拼点评。

### variant_training（错题变式）
- 参数：`{"question_id": "<错题 id>", "quantity": 2}`；缺 question_id 时列最近错题
  供学生指认（不瞎选）。
- 返回分支：
  - 已建会话（2026-10-07 批② 实现勘误：页面按 session_id 无拉取端点，
    接棒载荷带整会话对象供 WrongBookPage 直接 setTraining 进作答）：
    `{"text": "已基于「原题摘要前 20 字」生成 2 道变式，去错题本作答",
     "session_id": …, "questions": [...], "_route": {"navigate": true, "page": "/m/wrong",
     "state": {"training": {"session_id": …, "questions": [...], "total_count": N}}}}`
  - 原题不存在/非本人错题：`{"text": "没找到这道错题…", "error": …}`
  - 无错题可变式：`{"text": "错题本是空的，先做一轮练习攒错题"}`
- 背后：`wrong_question_trainer.generate_variant_questions / create_training_session`。

### mastery_report（掌握度报告，student+parent）
- 参数：`{"top_n": 6}`。
- 返回：`{"text": "课程掌握 63%：精通12/熟练5/熟悉8/摸索22；薄弱前 3：铁及其化合物(4%)…",
   "course_percent": 63, "levels": {"mastered": 12, …}, "weak": [{"kp": …, "p": 0.04}]}`
  （无 _route；家长侧同形，scope 收窄绑定孩子）
- 背后：`mastery_service.mastery_for_student / course_mastery_percent / weak_top`。

## 附：同批两个单工具（非注册表项）

- `parent_weekly_summary`（C2，parent）：拼 `build_cards` + elimination +
  `compose_summary_text` 三源；返回 `{"text": <三卡+人话+一件事摘要>, "cards": …}`。
- `decide_plan_draft`（C4，student）：参数 `{"draft_id": …, "action": "approve"|"revise",
  "comment": "…"}`；直接调 diagnosis decision 端点同源 service，本人归属校验即边界，
  不过门卡；返回 decision 落库结果一句话。

## evals 断言面（批⑥）

- 学生对话「出变式/来一轮练习」必须命中 `invoke_learning_skill`（裸出题=红）。
- 接棒：工具结果含 `_route.navigate` 且 page 在移动白名单。
- 家长对话「孩子这周怎样」命中 `parent_weekly_summary`。
