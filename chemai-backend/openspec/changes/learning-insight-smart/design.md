# Design: learning-insight-smart

## D1. 掌握度口径收口（S2）

现状分裂：`mastery_service`（BKT，只喂教师 agent）vs `/api/report/student/{id}/kp-mastery`（朴素正确率，学生可见）。收口原则：**BKT 是唯一口径**，正确率字段保留向后兼容但不新增消费方。

- 四级映射（纯函数 + 表驱动单测）：`<0.4 摸索 / 0.4–0.7 熟悉 / 0.7–0.9 熟练 / ≥0.9 精通`；课程掌握 % = 精通+熟练知识点占比（Khan 语义：只有 Proficient+ 计入）
- IXL 教训照收：不做高惩罚斜率（90+ 冲刺区仅提示不扣分）
- 不做 ALEKS Knowledge Check 式「回收已掌握点」——CAT 的停止规则部分承担查漏，但不动学生已得状态（避免焦虑反噬）
- FSRS 联动：到期卡的知识点在掌握环上呈「褪色」态——只读渲染，不改 BKT 数值

## D2. CAT 集成（S7）

- catsim 提供选题（最大信息量）与停止规则（SE≤0.3 或 8 题）；θ 初值取学生既有 IRT 能力估计（assessment_service），无历史则 0
- **回退是硬要求**：IRT 参数覆盖不足的知识点（未标定题）随机选题并在会话汇总中标注「未标定题占比」——参数覆盖度是本项最大风险，回退保证功能不空转
- 会话端点（allow_self + ScopeGuard）：`POST /api/practice/smart/start`（定知识点范围）→ `POST .../answer`（判卷 + θ 更新 + 返回下一题）→ `POST .../finish`
- **薄弱点落点写法**：θ 更新与 BKT 更新都由作答经既有链路自动发生（消灭率自动反映）；finish 返回的是**能力估计快照 + 薄弱点列表供前端展示**——不直写学习计划（计划是教师 agent 产物，避免旁路）

## D3. 苏格拉底协议（S5a）

改 `agent/personas/student.yaml` system_prompt，三层协议：
1. 状态探测：先问「做到哪一步/卡在哪」，禁止上来就讲
2. hint ladder：L1 方向提示 → L2 方法提示 → L3 最后一小步；**每回合只推进一步**；学生明确答对才确认
3. L3 之后学生仍卡住才允许完整讲解，讲解后必须接一道同类变式
验收走评估样例（10 问回归集），不是只看 prompt 文本。MathDial（ETH）仅参考 hint 策略结构（其数据 license 未核实，不拷内容）。

## D4. 表述特训技能（S5b）

- 注册进 `teaching_skills` REGISTRY，走 `invoke_teaching_skill` 单入口（加技能不改 agent）；学生端 ChatPanel 渲染技能结果卡（批 1 S4 的 component 事件已承载）
- 交互：学生选场景（实验方案答辩 / 工业流程题讲解）→ 分句作答 → 每句三色标注（红=术语错 / 黄=条件缺 / 蓝=逻辑断链）→ 三栏反馈（各栏 0–100 分 + 一句改进）
- 错句生成记忆卡：复用判卷建卡钩子（`app/jobs/handlers.py` 既有路径），卡片正面=场景问题、背面=标准表述

## D5. 装置图落库（X2）

- ChemComposer 扩展第三种输入形态：文本 / 公式 / 画布 三 tab（Excalidraw 懒加载，≈344KB gzip 不进主包）
- **Data Landing**：PNG 存 `data/answer_attachments/{student_id}/{practice_id}/{question_id}.png`，`student_answers` 增 JSON 字段 `attachments`（路径数组）——提交即落库，不悬浮在前端状态
- 判卷：一期走 Grading Center 既有 `PENDING_SEMANTIC` 行 + 附件引用，教师判卷中心可看图人工判；多模态 LLM 视觉判留二期接口位（附件引用已含图片路径，升级不迁移数据）
- 器材元件库 v1：12–15 件（酒精灯/试管/烧杯/锥形瓶/铁架台/导管/集气瓶/量筒/天平/酒精喷灯/水槽/玻璃棒），Excalidraw library JSON 入 `frontend/src/assets/`
- 排除重申：tldraw（水印+商用付费）、ChemDoodle（GPL 双协议）、Ketcher 移动端（WASM 7.5–11.8MB + 触屏 issue 未解）
