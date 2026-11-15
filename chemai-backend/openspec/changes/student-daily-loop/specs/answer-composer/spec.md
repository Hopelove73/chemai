# Answer Composer

学生主观题作答输入器：多空字段 + 化学符号键盘 + `\ce{}` 实时预览 + 多空判卷 + 逐题成绩。补上「主观题无法作答」的功能空洞。

## ADDED Requirements

### REQ-CMP-001: Subjective Answer Input
fill_blank / short_answer / calculation 题在移动端可作答并提交。

**Acceptance:**
- PracticePage 答题视图按 `question_type` 渲染：选择题按钮（现状）或 ChemComposer（主观题）
- 单空 = 一个输入行；多空 = N 个输入行（N = 标准答案 JSON 数组长度），逐空编号
- 提交 payload 统一为 `answer` 字段（单空纯字符串 / 多空 JSON 数组）；后端 `_extract_student_answer` 兼容旧 `student_answer`
- 前端 `Question` 接口扩展 `question_type` 与空位数

### REQ-CMP-002: Chemistry Keypad
化学符号键盘覆盖常规化学记号输入。

**Acceptance:**
- 键盘布局常量独立 `lib/chem-keypad.ts`（纯数据，单测钉住）：⇌ → ↑ ↓ Δ 加热 点燃 催化剂 沉淀符号 气体符号 高温 常见离子与下标快捷
- 点按在光标处插入；移动端无需系统键盘即可完成常见方程式骨架
- MathLive（MIT）承载公式结构编辑；组件懒加载（进入答题才 import，见 bundle 风险）

### REQ-CMP-003: Live Preview
作答实时渲染为标准化学式。

**Acceptance:**
- 输入经 `\ce{}` 包装后由现有 `chem-markdown.ts`（KaTeX+mhchem）渲染预览；与题干渲染同源
- 预览失败（非法记号）不阻塞提交，显示原文

### REQ-CMP-004: Multi-blank Grading
多空判卷按空拆分、逐空归一化。

**Acceptance:**
- `grading_center._compare_answers` 前置拆分：两侧解析 JSON 数组，逐空走现有归一化（LaTeX 化学标准化 + 上下标直译 + 去空白）；任一空错即整题错，`reason` 注明错在第几空
- 单空行为与迁移前完全一致（回归：现有判卷测试全绿）
- LLM 语义判定 prompt 带空位编号，返回体 `{i, equivalent, reason}` 结构不变

### REQ-CMP-005: Schema Migration
题型与长度的破坏性迁移一次到位。

**Acceptance:**
- `questions.question_type` 列（默认 single_choice）；OCR 导入、AI 生成、造数三条写入路径全部带题型
- `questions.answer` 与 `student_answers.student_answer` 放宽至 Text；删除判卷落库 256 截断
- 旧数据零感知：存量单空字符串行为不变

### REQ-CMP-006: Per-question Result Page
成绩页展示逐题结果。

**Acceptance:**
- 逐题对错列表 + 评语（`reason`）+ 主观题 `pending_semantic` 状态行（「AI 判定中」）
- 正确率与现有 correct/total 大数字并存
