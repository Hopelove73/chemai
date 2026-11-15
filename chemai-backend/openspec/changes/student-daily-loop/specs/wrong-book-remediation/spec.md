# Wrong Book Remediation

错题「举一反三」闭环：错因标签直达学生 → 标已掌握 → 变式训练 → 消灭率清单。错题本从只读死胡同变闭环。

## ADDED Requirements

### REQ-WBR-001: Barrier-tagged Wrong Book
错题本每题带错因标签。

**Acceptance:**
- 错题卡显示错因标签（读该题 `probe_dimension` 归因：概念 / 审题 / 表述），三色区分；与题干、错答、正解、解析并存
- 多空错题（answer-composer 之后）逐空对比展示（student_answer vs answer）

### REQ-WBR-002: Mark Mastered
学生可自标已掌握。

**Acceptance:**
- 「标已掌握」按钮调 `POST /api/practice/wrong/{qid}/master`（端点已有，allow_self）；已标题折叠或移出主列表
- 自标不回写掌握度（BKT 口径只由作答驱动），仅影响错题本视图——避免口径分裂

### REQ-WBR-003: Variant Training Session
一键生成变式训练。

**Acceptance:**
- 错题卡「举一反三」→ `POST /api/practice/wrong-topic/variant/generate` + `/training/create`（2 道同考点变式）→ 作答（主观变式题用 ChemComposer）→ `/training/submit`
- 训练历史可回看（`GET .../training/history/{id}`）
- 训练结果走既有判卷链路，错题自动再入错题本

### REQ-WBR-004: Elimination Rate
消灭率清单挂在错题本顶部。

**Acceptance:**
- `GET /api/analytics/student/{sid}/elimination`（新端点，allow_self + ScopeGuard）：薄弱知识点列表 + 各点 BKT 掌握概率 + 是否已消灭（≥0.9）
- 数据单一真源为 `mastery_service`——端点只做查询包装，不复制计算
- 学生页渲染「薄弱知识点 已消灭 n/m」+ 逐点进度条；与教师端 diagnose_barrier 数字同源一致（同一 service 的同一读法）
- Eedi Misconception Graph（CC BY 4.0）仅作标签展示格式参考；不引入其数据内容
