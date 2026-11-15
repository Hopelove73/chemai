# Tasks: student-daily-loop

## 0. 前置核实

- [x] 0.1 核实 `POST /api/agent/chat/langgraph/resume` 对无 Plan Run 会话的行为；需要则补 history-only 重同步分支
- [x] 0.2 核实现有判卷测试对 `_compare_answers` 拆分改造的回归面

## 1. S4 移动流底座

- [x] 1.1 ChatPanel 换用 `agent-stream-reducer.ts`，删内联 reducer（240 行）
- [x] 1.2 补消费：artifact 工件卡 / gate_open 只读卡 / plan_summary·plan_progress 折叠卡
- [x] 1.3 `lib/mobile-routes.ts` navigate 白名单 + 单测；populate/action/context 忽略路径
- [x] 1.4 断流 resume 接入 + 手测（断网→恢复→回合完整）
- [x] 1.5 tabs 收敛进 MobileLayout（5 页替换 + badge 挂点）
- [x] 1.6 reducer 移动端裁剪行为单测

## 2. X1 作答输入器

- [x] 2.1 alembic：`questions.question_type`（默认 single_choice）+ 两 answer 列放宽 Text + 删 256 截断
- [x] 2.2 OCR 导入 / AI 生成 / 造数三路径写入 question_type
- [x] 2.3 `lib/chem-keypad.ts` 键盘布局常量 + 单测
- [x] 2.4 `ChemComposer.tsx`（多空字段 + 键盘 + MathLive 懒加载 + \ce{} 预览）
- [x] 2.5 `grading_center` 按空拆分逐空归一化 + prompt 空位编号 + 回归
- [x] 2.6 PracticePage 按题型渲染 + payload 统一 `answer` 字段 + Question 接口扩展
- [x] 2.7 成绩页逐题对错 + 评语 + pending_semantic 状态行
- [x] 2.8 端到端手测：多空方程式「输入→预览→提交→归一化判对」

## 3. S1 复习中心 + 连击

- [x] 3.1 重建 ReviewCenterPage（due 列表 → 重做/自评流 → history）
- [x] 3.2 底栏到期徽章（badge 挂点接线）
- [x] 3.3 `student_streak` 表 + alembic
- [x] 3.4 streak 纯函数（清卡/漏天/冻结/修复四分支表驱动单测）
- [x] 3.5 py-fsrs 接口适配层（现有调度测试全绿）
- [x] 3.6 `scripts/calibrate_fsrs.py` 离线校准 + 参数入库 + memory_card_service 读取（待真实日志 ≥400 条后首跑 --save）
- [x] 3.7 （可选）genanki Anki 导出端点

## 4. S3 错题闭环

- [x] 4.1 WrongBookPage 错因三色标签 + 逐空对比
- [x] 4.2 标已掌握按钮接线
- [x] 4.3 变式训练会话 UI（生成→作答→提交→历史）
- [x] 4.4 `GET /api/analytics/student/{sid}/elimination` 薄端点 + 消灭率清单 UI
- [x] 4.5 消灭率与教师端同源一致性核对（同一 service 读法）

## 5. 验收

- [x] 5.1 后端 pytest 全绿（1489 passed）；新增纯函数单测全绿（streak 11 + 多空 8）
- [ ] 5.2 移动端视觉验收：375 / 430 两档截图走查（四页 + 作答流 + 复习流）——待起服务手测
- [ ] 5.3 手测清单 5–8 条附 PR 描述——待起服务手测
