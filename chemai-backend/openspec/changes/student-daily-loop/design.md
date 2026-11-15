# Design: student-daily-loop

## D1. 事件消费的职责切分（S4）

移动端与教师端走同一条 `POST /api/agent/chat/langgraph/stream`（SSE 17 类事件）。不复制 reducer——直接复用 `frontend/src/lib/agent-stream-reducer.ts`（纯函数、有测试），移动端只做**渲染投影差异**：

| 事件 | 教师端 | 移动端（本 change） |
|---|---|---|
| text / done / phase / tool_call / tool_result / error | 全消费 | 全消费（同 reducer） |
| artifact | ArtifactPanel 三 tab | 工件卡（标题+类型）→ 点击跳 `StudentArtifactPage` |
| navigate | 跳教师页 | **白名单：仅 `/m/*` 路由执行**，其余忽略（Gateway 的 navigate 目标表是教师页面集合，学生 persona 正常不产生，防御性过滤） |
| populate / action | 教师 inline panels 预填 | 未知目标忽略 + console 计数（不静默崩溃）；后续按需逐个接 |
| context | ContextMeter | 忽略（移动端无计量 UI） |
| gate_open | ApprovalCard 三值决定 | **只读提示卡**——学生对话永不产生 gate_open（学生 persona 无开门工具），防御性渲染「老师正在处理一个确认步骤」 |

**为什么学生确认门不放聊天里**：gate 机制（`gate_open` / stream 内 decision / `/api/agent/gates/pending`）面向 Plan Run 与教师决策者；学生 persona 无任何开门工具，聊天里的学生门卡永远不触发。学生学习计划确认走 **Plan Draft REST**（`/api/diagnosis/learning-plan/draft/{sid}/{draft_id}/decision`，allow_self 已放行），入口在任务/计划页卡片（两值：同意开始 / 打回），不在 ChatPanel。

## D2. 断流 resume

复用 `POST /api/agent/chat/langgraph/resume`（`agent/channel/langgraph_channel.py:1680`，ADR-0016 语义：异常断开→重拉 history 以 DB 为准）。**前置核实任务**：resume 对「无 Plan Run 的纯对话」的行为（学生对话通常无 plan run）——若路径上有 plan_run 前置假设，补 history-only 重同步分支。移动网络差是学生端最痛一项，此项优先级高于新事件渲染。

## D3. 多空填空的数据约定（X1）

- `questions.answer`：单空 = 纯字符串（向后兼容）；多空 = JSON 数组 `["空1","空2",...]`，空位数 = 数组长度
- `student_answers.student_answer`：同约定
- 判卷：`grading_center._compare_answers` 前置「按空拆分」——两侧都解析为数组，逐空归一化（现有 LaTeX 化学标准化 + 上下标直译 + 去空白逻辑不变）；任一空错即该题错，`reason` 注明错在第几空
- LLM 语义判定 prompt 带空位编号，返回体结构不变（`{i, equivalent, reason}`）
- **长度迁移是硬前提**：两列现均 `String(256)`，且判卷落库截断 256（`grading_center.py:226`）——JSON 数组极易超限，迁 Text，删截断逻辑
- **payload 字段统一**：前端提交改发 `answer`（后端 `_extract_student_answer` 以 `answer` 为主、`student_answer` 仅兼容旧）； PracticePage 的 TS `Question` 接口同步扩展 `question_type` 与空位信息

## D4. 连击规则的确定性（S1）

- 时区与日界：**Asia/Shanghai 自然日**（服务器统一按该时区计算 last_active_date，不依赖客户端时钟）
- 续连击：当日清完全部到期卡（自评或重做任一信号完成每张卡）
- 冻结卡：**常量规则**——每周（ISO 周）首次清空当日全部到期卡奖励 1 张，持有上限 2；漏天自动消耗 1 张冻结（有冻结则连击不断）；补签修复窗口 1 次/周（漏天 48h 内清卡可回连，消耗 1 张冻结或直接回连一次，取不消耗冻结的宽容实现：每周 1 次免费修复）
- 上述全部写成纯函数（输入：日期序列 + 事件，输出：streak 状态），scheduler 与 API 共用；表驱动单测覆盖清卡/漏天/冻结/修复四分支
- py-fsrs 对齐：现有自研调度包一层接口适配（不重写调度内核）；`scripts/calibrate_fsrs.py` 离线跑真实答题日志拟合权重，参数入库后由适配层读取——**不在线训练**

## D5. 消灭率口径（S3）

消灭率 = 该生薄弱知识点中 BKT 掌握概率越阈值（0.9）的比例。数据单一真源是 `mastery_service`，学生端经新薄 REST `GET /api/analytics/student/{sid}/elimination`（allow_self + ScopeGuard）读——**不复制计算**。Eedi schema（CC BY 4.0）只作错因标签展示格式参考，不引入其数据。

## D6. tabs 单一真源

5 处重复 tabs 数组（4 学生页 + 1 家长页）收敛为 `MobileLayout.tsx` 导出常量（学生 4-tab、家长 3-tab 两个变体）；页面只引用不定义。S1 到期徽章在常量上预留 `badge` 挂点（key=review），由各页传入计数。
