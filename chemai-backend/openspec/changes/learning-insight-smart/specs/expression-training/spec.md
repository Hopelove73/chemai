# Expression Training

表述障碍训练技能（CONTEXT.md「Expression Training」）：把障碍分布中独有的「表述」维度变成训练闭环。

## ADDED Requirements

### REQ-EXP-001: Teaching Skill Registration
表述特训以教学技能注册。

**Acceptance:**
- 注册进 `teaching_skills` REGISTRY，经 `invoke_teaching_skill` 单入口调用；加技能不改 agent 结构
- 学生端技能结果卡渲染依赖批 1 S4 的 component 事件消费

### REQ-EXP-002: Sentence-level Annotation
逐句三色标注。

**Acceptance:**
- 场景选择（实验方案答辩 / 工业流程题讲解）→ 学生分句作答 → 每句标注三类毛病：红=术语不规范 / 黄=条件缺失 / 蓝=逻辑断链
- 三栏反馈：术语 / 条件 / 逻辑各栏分数（0–100）+ 每栏一句改进建议
- 学生「表述」障碍占比越高，答疑 agent 在合适时机推荐本技能（一次性引导，不纠缠）

### REQ-EXP-003: Failed Sentences Re-enter Review
错句进记忆卡队列。

**Acceptance:**
- 标注 ≥2 类毛病或任一栏 <60 的句子生成记忆卡，正面=场景问题、背面=标准表述；
  复用「(学生, 题目) 一卡」实体与 ensure_card 机制（memory_card_service.create_expression_card，
  probe_dimension=expression）——判定块由技能模板的机器可读 JSON 下发，invoke_teaching_skill
  侧解析建卡（判卷钩子面向判卷事件，此处是技能完成事件，故为同实体的第二入口而非复用同一钩子）
- 卡片在复习中心可见、按 FSRS 调度（依赖批 1 review-center-streak）

### REQ-EXP-004: Honest Annotation
化学约定嵌入标注。

**Acceptance:**
- 标注遵循 CONTEXT.md Artifact 约定精神：化学记号错误必须给出 `\ce{}` 规范写法；场景描述与装置比例夸张处如实标注
