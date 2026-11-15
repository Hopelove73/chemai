# Tasks: learning-insight-smart

## 1. S2 掌握环

- [x] 1.1 四级映射纯函数 + 表驱动单测
- [x] 1.2 `kp-mastery` 追加 `mastery_probability`（BKT 直读 mastery_service）
- [x] 1.3 掌握环卡组件 + FSRS 褪色态 + 点褪色节点跳复习中心
- [x] 1.4 与教师端 diagnose_barrier 抽样比对验收

## 2. S7 智能练习

- [x] 2.1 catsim 接入（选题 + 停止规则实现在 smart_practice_service；标定参数仍出自 assessment_service）
- [x] 2.2 `app/api/smart_practice.py` 三端点（allow_self + ScopeGuard）+ 路由注册
- [x] 2.3 未标定题回退 + 会话汇总「未标定题占比」
- [x] 2.4 θ 收敛与停止规则边界单测
- [x] 2.5 PracticePage 智能练习入口 + 结束页（θ 变化 + 薄弱点快照 + 建议）

## 3. S5a 苏格拉底协议

- [x] 3.1 `student.yaml` system_prompt 三层协议重写
- [x] 3.2 评估样例 10 问（首回合讲解率=0、提示逐级、单步推进断言）入 `evals/`
- [ ] 3.3 样例跑分（批 2 末验收门）——需 LLM 回放环境，待手测

## 4. S5b 表述特训

- [x] 4.1 `expression_training` 技能注册（prompt 模板 + 参数 + 产物类型）
- [x] 4.2 逐句三色标注 + 三栏反馈生成
- [x] 4.3 错句建卡钩子（复用判卷建卡路径）+ 复习中心可见
- [x] 4.4 答疑中的时机性推荐（一次性引导）

## 5. X2 装置图

- [x] 5.1 器材元件库 v1（12–15 件 excalidrawlib）
- [x] 5.2 ChemComposer 三 tab 扩展 + Excalidraw 懒加载
- [x] 5.3 `student_answers.attachments` 字段 + PNG 落 `data/answer_attachments/`
- [x] 5.4 PENDING_SEMANTIC + 附件引用 → 教师判卷中心可见 → 人工判定回流
- [x] 5.5 Data Landing 检查（提交后刷新不丢图）

## 6. 验收

- [x] 6.1 pytest 全绿；新增纯函数单测全绿
- [ ] 6.2 答疑评估样例跑分达标——同上待跑分
- [ ] 6.3 手测清单（掌握环/智能练习/表述特训/装置图四流）——待起服务手测
