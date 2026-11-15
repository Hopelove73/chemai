# 智辅化学（ChemAI）后端

FastAPI + LangGraph 单 Agent + React 前端（三端同一 SPA）的化学 AI 教学平台。
总览见仓库根 [`README.md`](../README.md)；领域词汇表见 [`CONTEXT.md`](CONTEXT.md)；
架构决策见 [`docs/adr/`](docs/adr/)（0001-0029）。

## 技术栈

| 层级 | 选型 |
|------|------|
| Agent | LangGraph v2 单 ReAct（`create_react_agent`）+ GuardState 护栏 + SSE 流式通道 |
| LLM | DeepSeek 为主（agent/摘要），mimo / dashscope 可选 provider；评测走录放磁带 |
| 后端框架 | FastAPI + SQLAlchemy + SQLite（WAL 模式活库） |
| 前端 | React 18 + Vite + Tailwind（令牌化设计系统，教师桌面段 + `/m/` 移动三端） |
| 教学算法 | FSRS（py-fsrs 记忆调度）、catsim（CAT 选题）、自研 BKT（掌握概率）、IRT（题目标定） |
| 结构式渲染 | 服务端 RDKit（教材白名单归一）+ 前端 smiles-drawer |
| 向量检索 | Chroma + 本地 BGE 嵌入 |
| 通知外发 | 飞书 / 企业微信 / 钉钉群机器人 webhook（Telegram 存量兼容） |

## 快速开始

```bash
cd chemai-backend
pip install -r requirements.txt
cp .env.example .env        # 填 DEEPSEEK_API_KEY 等
start.bat                   # 开发模式：端口 8001 + 热重载 + 自动开浏览器
# start.bat stable          # 稳定模式：端口 8000，无热重载
```

前端构建产物由后端托管；改前端后：

```bash
cd frontend
npx vite build
```

### 演示账号（内置演示库）

| 端 | 入口 | 账号 |
|---|---|---|
| 教师 | `/#/login` | qian / qian123456 |
| 学生 | `/#/m/login` | 241901 / 123456 |
| 家长 | `/#/m/parent-login` | 241901家长 / 123456（已绑定周翔） |

## 三端能力总览

### 教师端（PC 工作台）

- **Agent 工作台**：58 工具单 Agent（出题/题库/判卷/诊断/文件/解析），破坏性操作
  过确认门（数据强制，ADR-0015）；教学技能注册表一键生成教学动画/讲评稿/表述特训
  （ADR-0027），有机结构自动渲染（`[SMILES:...]` → RDKit 白名单归一）
- **判卷中心**：答题卡批量拖传 / 待确认队列 / 线上提交 / 装置图队列（Excalidraw 画布）
- **班级学情 / 学生管理 / 知识地图**（先修边治理：提议/采纳/禁用即时生效）
- 计划派发：生成 → 确认门 → 学生确认/打回 → 修订循环（Plan Run 跨回合存活）

### 学生端（/m/ 移动）

- **AI 助教**：苏格拉底三层协议（探状态 → L1/L2/L3 分级提示 → 变式闭环）、
  `[SMILES:...]` 结构式即时渲染、断流 resume 重同步
- **练习**：每日练习（判卷自动建卡）+ 智能练习（catsim 最大信息量选题，
  SE≤0.3 或 8 题停止，θ 快照）
- **复习中心**：FSRS 调度（到期队列/重做判定/自评双轨），连击 + 冻结卡资产
- **错题本**：错因三色标签（概念/审题/表述）→ 举一反三变式 → 标已掌握 → 消灭率
- **掌握环**：BKT 四级状态（摸索/熟悉/熟练/精通）+ 遗忘褪色；知识地图先修解锁

### 家长端（/m/ 移动）

- **概览三卡**（未完成/即将到期/本周动态，刻意不放分数）+ 错因人话解读（AI）
- **动态流**：练习完成/工件分享/连击里程碑（点赞）
- **派任务**：家长对话发起 → 确认门三值决定 → 落学生计划（每周频控）
- **设置**：通知类型开关 / 免打扰时段 / 摘要频率（周/日/关）/
  外发通道（飞书/企业微信/钉钉群机器人 webhook，host 白名单）/ 修改密码

## Agent 架构（ADR-0019 单 Agent）

```
用户消息 → persona 收窄（_PERSONAS_BY_ROLE）→ 单 ReAct 循环
  ├─ 工具白名单按 persona 过滤（teacher 41 / tutor 21 / student 12 / parent 12）
  ├─ GuardState：去重 / 逐工具调用上限 / 数据范围（ADR-0014）/ 确认门（ADR-0013/0015）
  ├─ 教学技能注册表（ADR-0027）+ 学习技能注册表（ADR-0029，会话接力 Hand-off）
  └─ SSE：tool_call / tool_result / text / gate_open / navigate / artifact / done
```

四大注册表/入口，新增能力不改 agent：教学技能（invoke_teaching_skill）、
学习技能（invoke_learning_skill）、知识边治理、家长派任务对。

## 通知与外发

- 站内：`parent_notifications` / `student_notifications`（幂等键去重，动态流消费）
- 外发：`NOTIFY_EXTERNAL_GATEWAYS` 开启后，按家长绑定的
  飞书/企业微信/钉钉 webhook 推送（host 白名单，平台分形载荷）；
  投递前过设置页投递门（类型开关 + 免打扰时段；学业预警始终送达）
- 摘要频率：weekly（周日 20:00）/ daily（早 8:00）/ off，调度器按 Parent 偏好执行

## 测试与评测

```bash
python -m pytest tests/ -q          # 1632 用例（含权限边界/护栏/契约/回归捉虫）
python evals/run_evals.py --fast    # fast 门禁：137 断言（确定性、CI 每日）
python evals/run_evals.py --slow    # slow 层：LLM 判官评分（needs_api，磁带可回放）
```

浏览器真跑联调清单（入账制）：`evals/e2e-e3-upgrade-checklist.md`、
`evals/e2e-ui-v23-checklist.md`、`evals/e2e-learning-skills-checklist.md`。

## 部署

- 开发：`start.bat`（8001 热重载）；生产：`uvicorn app.main:app --port 8000`，
  前端 `frontend/dist` 由后端托管
- 数据库迁移：`python -m app.utils.migrate`（幂等）；另有两处启动期幂等 DDL
  （agent_gates.last_opened_at、parents.notify_channel/notify_prefs）
- 数据库纪律：`chemai.db` 是 WAL 活库，**涉及它的提交前先跑
  `python scripts/db_checkpoint.py`**
- 影子数据：pytest 与 evals 全部跑在影子副本上（ADR-0026），绝不写生产件

## API 文档

- Swagger UI: `/docs`、ReDoc: `/redoc`（服务启动后访问）
- API 清单随 OpenAPI 自动生成；核心面：认证 / 用户 / 班级 / OCR / 考试 /
  出题 / 诊断 / 报告 / 练习（含 smart / wrong-topic）/ 复习 / 掌握度 /
  家长（三卡/派任务/通知偏好）/ 通知 / Agent 流式通道

## 文档

| 文档 | 说明 |
|---|---|
| [`CONTEXT.md`](CONTEXT.md) | 领域词汇表（唯一命名源） |
| [`DESIGN.md`](DESIGN.md) + [`design/`](design/) | UI 设计系统总册与三端分册 |
| [`docs/adr/`](docs/adr/) | 架构决策记录 0001-0029 |
| [`TODOS.md`](TODOS.md) / [`VERSION.md`](VERSION.md) | 待办与版本史 |
| [`openspec/`](openspec/) | 变更提案与规格 |

## License

私有项目，未授权分发。
