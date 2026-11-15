## Why

批 1 打通学生每日闭环后，学生端仍看不见「学得怎么样、下一步学什么」：BKT/IRT/CAT 掌握度只喂教师 agent，学生 REST 用的是另一套朴素正确率（口径分裂）；自主练习是固定顺序刷题，无能力估计；答疑只有一句苏格拉底宣言、无分级提示协议；障碍分布的「表述」维度有诊断、无训练；实验装置图无法作答。

## What Changes

- **新增** 学生首页掌握环卡：BKT → 四级状态（摸索/熟悉/熟练/精通）+ 课程掌握 %；`kp-mastery` 追加 BKT 口径字段（口径收口）
- **新增** 智能练习（Smart Practice）：catsim（BSD-3）选题 + 停止规则 + 每题 θ 更新 + 能力估计落点；无 IRT 参数题回退随机
- **修改** `student.yaml` 苏格拉底协议化：先探状态 → 三级 hint（方向/方法/最后一小步）→ 每回合推进一步
- **新增** 教学技能 `expression_training`（表述特训）：虚拟评委 + 逐句三色标注（术语/条件/逻辑）+ 三栏反馈 + 错句进记忆卡
- **新增** 装置图作答：Excalidraw（MIT）画布 + 自建器材元件库 v1 + PNG 落库 + 教师/LLM 判卷路径

## Capabilities

### New Capabilities
- `mastery-visualization`: 掌握度四级状态与掌握环，BKT 口径收口到单一真源
- `smart-practice`: CAT 自适应练习会话（选题/停止/能力估计/回退）
- `socratic-hint-protocol`: 学生答疑的分级提示协议（prompt 层）
- `expression-training`: 表述障碍训练技能（色块标注 + 三栏反馈 + 错句复习）
- `diagram-answer`: 实验装置图作答（画布 + 附件落库 + 判卷衔接）

### Modified Capabilities
- 无（全部新增；mastery-visualization 修改的是既有 report 端点的返回体，随 spec 记录）

## Impact

- **新增文件**: `app/services/smart_practice_service.py`, `app/api/smart_practice.py`, `frontend/src/pages/mobile/student/MasteryRing.tsx`（组件）, `frontend/src/assets/chem-lab-library.excalidrawlib`, teaching skill `expression_training` 注册
- **修改文件**: `app/services/assessment_service.py`（catsim 接入）, `app/api/report.py`（kp-mastery 加字段）, `app/api/practice.py`（smart 会话路由）, `agent/personas/student.yaml`, `app/services/teaching_skills/__init__.py` REGISTRY, `frontend/src/pages/mobile/student/PracticePage.tsx`（三 tab 输入形态）, `app/models/database.py`（附件字段）, `app/services/grading_center.py`（附件 pending 分支）
- **依赖**: catsim (pip, BSD-3)、@excalidraw/excalidraw (npm, MIT)
- **测试**: θ 收敛与停止规则单测、四级映射表驱动、键盘外新依赖的懒加载断言、附件落库 Data Landing 检查
