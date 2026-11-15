# Mastery Visualization

掌握度四级状态与掌握环；BKT 口径收口为唯一真源。

## ADDED Requirements

### REQ-MSV-001: Mastery Ring Card
学生端展示掌握环卡（实现落位「我的」页顶部——掌握度专属面板位；components/mobile/MasteryRing.tsx）。

**Acceptance:**
- 环形进度 + 「课程掌握 %」（精通+熟练知识点占比）+ 四级状态图例
- 课程掌握 % 由服务端 canonical 计算（kp-mastery 返回 course_mastery_pct），前端不自算
- 四级映射（BKT 概率）：`<0.4 摸索 / 0.4–0.7 熟悉 / 0.7–0.9 熟练 / ≥0.9 精通`——纯函数 + 表驱动单测
- 数据源 `GET /api/report/student/{id}/kp-mastery`

### REQ-MSV-002: Single Source of Truth
掌握度口径收口。

**Acceptance:**
- `kp-mastery` 返回体**追加** `mastery_probability`（BKT 口径，mastery_service 直读）；既有正确率字段保留不删（向后兼容），不新增消费方
- 学生掌握环与教师端 diagnose_barrier 数字同源一致（同一 service 同一读法，验收时抽样比对）
- 无第二处掌握度计算产生

### REQ-MSV-003: FSRS Fade-out
遗忘可视化。

**Acceptance:**
- 存在到期记忆卡的知识点在环上呈「褪色」态（只读渲染，不改 BKT 数值）
- 点褪色节点 → 跳复习中心（依赖批 1 review-center-streak）

### REQ-MSV-004: No Punishment Spiral
不复制 IXL 高惩罚设计。

**Acceptance:**
- 90+ 冲刺区仅提示文案，无扣分/回退机制
- 掌握环只升不降的展示承诺：BKT 数值自然更新，但 UI 无「掉级」动画（褪色态除外）
