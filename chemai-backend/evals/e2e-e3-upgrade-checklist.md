# E2E 测试清单 —— 三端升级（批1-3 + S6）

> 由 /plan-eng-review Test review + 捉虫清单沉淀。浏览器真跑按此执行；
> 每条含前置账号、操作步骤、断言。LLM 依赖项（agent 对话类）需 .env 有 key（已确认 DEEPSEEK_API_KEY 存在）。

## 前置
- 服务：`chemai-backend/start.bat`（uvicorn :8001）+ 前端构建产物由后端托管
- 账号：学生（登录 /m/login）、家长（/m/parent-login，需绑定码）、教师（/login）

## 批1 学生每日闭环
- [ ] E1 作答输入器：学生登录 → 练习 → 打开含 fill_blank 的任务 → 多空字段渲染
  - 断言：化学键盘点「⇌」在光标处插入；预览区 KaTeX 渲染；提交后成绩页逐题对错 + 评语
- [ ] E2 复习中心：底栏「复习」徽章 = /due 数量；进入后重做一题（客观题点选项）→ 连击 +1；漏答自评「忘了」→ 下次间隔变短
- [ ] E3 错题本：错因三色标签显示（概念蓝/审题黄/表述紫）；「标已掌握」点击后移入已掌握分组，刷新后仍在
- [x] E4 举一反三：错题卡「举一反三」→ 生成 2 道变式（LLM）→ 作答提交 → 训练结果页
  - ✅ 2026-10-03 浏览器真跑通过（2/2）。抓到并修复 2 个 bug：
    1. `/variant/generate` 漏 `allow_self=True` → 学生被 practice:create 403（补上 + 本人错题册归属校验）
    2. `/training/submit` 未做「选项下标→字母」映射（与 /api/practice/submit 同一客户端契约）→ 全对判 0/N
    回归测试：test_permission_roles.py::test_variant_generate_* / test_training_submit_maps_choice_index_to_letter
- [x] E5 移动对话：发消息 → 工具胶囊；触发工件分享 → 工件卡点击跳全屏预览；断网 5 秒恢复 → 「连接中断，已重新同步」且历史完整
  - ✅ 2026-10-03 浏览器真跑（部分）：工具胶囊渲染 ✓；流中强杀服务 → 页面如实报「network error」→ 服务恢复刷新 → 历史完整（中断轮用户消息保留、错误气泡不落库，ADR-0016 DB 为准）✓
  - 📌 「已重新同步」横幅的触发路径（onAbnormalEnd，FIN 无收尾）在 Windows 浏览器无法安全造出，已由新增单测锁定：frontend/tests/agent-sse.test.ts（掐断→onAbnormalEnd / [DONE]→onDone）
  - 📌 工件卡全屏预览归并到 E9（表述特训报告工件同链路验证）

## 批2 学情可视化与智能
- [x] E6 掌握环：「我的」页顶部环形进度 + 课程掌握% + 四级图例；到期知识点褪色态 → 点击跳复习中心
  - ✅ 2026-10-03 浏览器真跑通过：36% 环 + 精通12/熟练5/熟悉8/摸索22 四级 + 14 个数据不足说明；「有遗忘的知识点待复习」点击 → /m/review（连击/冻结卡队列正常）
- [x] E7 智能练习：练习页「智能练习」卡 → 逐题作答 → 第 8 题或 SE≤0.3 停止 → 结束页 θ 变化 + 薄弱点快照 + 「再来一轮」
  - ✅ 2026-10-03 浏览器真跑通过（8 题停轮 → 快照页 θ Δ -0.01 + 未标定占比 + 薄弱点 chips + 去复习/再来一轮；错题自动进复习队列）。抓到并修复 3 个 bug：
    1. **difficulty 脏数据**（1.7 万行枚举名 'MEDIUM' 等原样入库，组卷复制扩散）→ `DifficultyColumn` TypeDecorator 写侧兜底 + `scripts/migrate_difficulty_names.py` 一次性清洗 + coerce 支持数字字符串（回归：test_difficulty_column.py）
    2. **daily_practice 复制题缺 question_type** → 主观题实例全部默认成 single_choice，前端选项/作答器都不渲染 → writer 修 + smart payload 读侧按选项实况纠偏（回归：test_smart_practice.py::test_session_payload_repairs_choice_type_without_options）
    3. **智能练习结束页空白**（result 视图闸在普通练习的 result 状态上，smart 流程只有 smartSummary）→ 前端拆闸（PracticePage.tsx，tsc+vitest 404 全绿后 vite build）
- [x] E8 苏格拉底协议（LLM eval）：贴题求答 → 首回合只探状态不给解；连续 L1→L2→L3 单步推进；直接要答案 → 给方向提示
  - ✅ 2026-10-03 跑分体系接线完成并出首份报告：`evals/test_socratic_protocol.py`（10 场景 × judge_rules，LLM 判官逐条二元判定）注册为 L2 `socratic_protocol` 慢跑条目，needs_api，全链走 httpx 出口可录放
  - 首份跑分（record 真打，磁带 45 盘入库）：18 条规则 10 过 8 挂，报告归档 evals/reports/20261003-113411-slow.json；replay 离线可复现（86s）
  - ⚠️ 真实行为发现（非基础设施问题）：前 3 场景（探状态/不给解/L1 方向）全过；「推进后升级提示」类场景 agent 频繁以「试试看吧」反问回避，未按协议在学生已推进时补步/给方法 —— 留给 agent prompt 调优批次
  - ⚠️ 评测脚本的 output 提取疑似抓到中间消息（socratic-04 磁带里的真实回复完全符合 L2，但 eval 记到一句「试试看吧！」）—— 复跑判定前需修 evals/test_langgraph_agent.py 的取尾逻辑（待调试回放确认）
- [x] E9 表述特训：对话「练练表述」→ invoke_teaching_skill → 三色标注报告工件；错句进复习队列
  - ✅ 2026-10-03 浏览器真跑通过：学生交「铁在氧气中燃烧发出耀眼的光，生成四氧化三铁…」→ invoke_teaching_skill·完成 → 工件卡「实验现象描述训练」→ 全屏预览 /#/m/artifact/…（逐句诊断 mark + 🔴术语不规范/🟡条件缺失/🔵逻辑断链 + 三栏评分 45/50/40）；错句落 memory_cards id=23「【学生表述特训】第1句…」due 当日 ✓
  - ✅ 附带完成 E5 归并项：工件卡点击跳全屏预览 ✓
- [x] E10 装置图：实验题画布 tab → 拖入试管/酒精灯 → 提交 → 教师判卷中心「装置图」tab 看图 → 判对 → 学生成绩页回流
  - ⚠️ **学生端画布不可用（真实缺陷，本批不修）**：移动端 h-72 容器里 Excalidraw 交互画布初始化异常 —— canvas 高 2200 万 px、偏移到容器外（y≈1245），点击/拖画全落在 Stack 背景上，画不了任何元素（420px 与 900px 视口均复现）。元件库（试管/烧杯/酒精灯等 14 件）随 initialData 正常加载但面板被 overflow 裁掉。修复建议：容器高度显式传入 + Excalidraw 挂载后触发一次 resize。
  - ✅ 后半链 2026-10-03 全链验证通过（附件管线 → 教师判卷 → 回流）：
    - smart/answer 带 PNG 附件 → 落盘 → 教师判卷中心「装置图」tab 显示「附件 1 张」→ 看图预览正常 → 判对 → 学生作答行翻转 is_correct=1、grade_reason「教师改判（teacher_qian）：装置图教师判定」✓
    - 抓到并修复 2 个 bug：① smart/answer 语义判定在途分支丢 attachments（PENDING 行无图，教师看不到图）② diagram-queue 用 `attachments IS NOT NULL` 过滤，但 SQLAlchemy JSON 列写 None 落库是文本 'null' → 过滤失效、队列被无图主观题灌满 21 条 → 改 `json_array_length>0` + Python 侧类型防御

## 批3 家长端
- [ ] E11 家长摘要：绑定家长登录 → 概览三卡（未完成/即将到期/本周动态，无分数）；错因人话卡「生成一句话解读」
- [ ] E12 动态流：学生完成练习/教师分享工件 → 家长消息 tab 出现卡片 → 点赞切换
- [x] E13 摘要频率：设置 pane 切「每天」→ 保存 → 再读回显示每天
  - ✅ 2026-10-03 浏览器真跑通过（API 读回 frequency=daily）。抓到并修复：**SettingsPane 是死 UI** —— PARENT_TABS 只有 3 个 tab，setTab('settings') 无任何调用，摘要频率/外发订阅面板没有任何入口 → MobileLayout.tsx 补第 4 个「设置」tab（tsc+vite build 过）
- [x] E14 家长派任务：家长对话「给孩子派个盐类水解的任务」→ 三值门卡 → 批准 → 学生计划可见；一周第 3 次 → 频控拒绝文案
  - ✅ 2026-10-03 浏览器真跑全链通过：门卡弹出（批准/打回/放弃）→ 批准 → 「盐类水解强化练习已发送成功 ✅（1/2 次）」→ 学生端「学习计划」显示计划 → 第 2 次送达 2/2 → 第 3 次派发 `parent_assign_task·失败` + 频控文案（每周 2 次已用满，无门卡）✓ 频控按送达计口径验证（生成失败不计次）
  - 抓到并修复 3 个 bug：
    1. `parent_assign_task` 用 `new_work_unit()`（另一条会话）包 `create_draft(db)` → require_unit 身份校验必炸 NoWorkUnit，门卡从未弹出过 → 改同会话 `work_unit(db)`（回归：test_parent_engagement.py::test_parent_assign_task_opens_gate_card）
    2. `parent_send_task` 传裸 parent_id → send_draft 角色感知按 `parent:` 前缀分流，误入教师分支被 Data Scope 拒 → 补前缀
    3. 学生「学习计划」页读 `d.plan` 但端点包在 `data.plan` → 永远显示暂无，且回落分支会**自动 LLM 生成覆盖家长刚派的计划** → ProfilePage 解包兼容
- [x] E15 家长对话：问「孩子哪个知识点最弱」→ 返回真实掌握度数据（非幻觉）
  - ✅ 2026-10-03 浏览器真跑通过：parent_mastery_levels·完成，表格给出铁/硫 4%、化学平衡 7%（21 题） vs 氧化还原 97% 精通 —— 与 E7 结束快照的薄弱点 chips（铁 4%）交叉一致，为真实数据面

## S6 知识地图
- [ ] E16 教师建图：教师对话「帮我提议先修关系」→ 提议列表 → 「采纳第 1、3 条」→ /kmap 页边出现且 active
- [ ] E17 学生地图：/m/map 分层卡式列表；前置未达节点「未解锁」→ 详情「先掌握前置：X」；绑定工件节点有「讲解」入口
- [x] E18 禁用边：/kmap 禁用一条 → 学生地图锁定态即时解除
  - ✅ 2026-10-03 浏览器真跑通过：新增种子边 铁及其化合物(4%)→化学平衡(active) → 学生地图「未解锁」分组出现、详情「先掌握前置：铁及其化合物」→ 教师 /kmap 禁用（toast「已禁用，地图即时生效」）→ 学生地图「未解锁」分组消失、化学平衡回到当前可学 ✓

## 已知非目标（本批不做）
- 教师桌面地图页的图形化画布（卡式列表已够用）
- 多题练习中的画布（限单题：归属问题）
- 手写笔迹识别
