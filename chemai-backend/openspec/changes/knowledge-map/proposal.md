## Why

知识点只有名字，没有先修关系；学生回答不了「下一步学什么」，工件库按时间排列无处着陆。学而思已验证「知识图谱 + 绑定微课」是学生端最强导航形态。决策 Q3：**不碰任何 NC 协议数据**（K12-KGraph 数据 CC BY-NC-SA），先修边由教师 agent 半自动生成 + 教师逐边确认，纯自研。

## What Changes

- **新增** `knowledge_edges` 表（from_kp / to_kp / status）与教师 agent 工具 `propose_knowledge_edges`：基于知识点共现（同卷/同批错题）+ 错因迁移模式提议先修边，教师确认门逐边采纳/驳回，边可随时禁用
- **新增** 知识地图页（学生端 + 教师端视图）：节点着色 = BKT 四级；就绪过滤（只呈现前置达标的「可学」节点）；节点点击 → 绑定工件（只链接已分享给该学生的版本）+ 「练习此点」（定向智能练习）
- **新增** 学习路径 = 拓扑遍历 + 掌握度阈值（无外部库、无外部数据）

## Capabilities

### New Capabilities
- `knowledge-edges`: 先修边的数据模型、agent 提议工具、教师确认门
- `knowledge-map-views`: 学生/教师地图视图、就绪过滤、工件绑定与练习入口

### Modified Capabilities
- 无（artifact kind 集合不变，契约测试不受影响）

## Impact

- **新增文件**: `app/models/database.py`（KnowledgeEdge）, `alembic` 迁移, `agent/tools/knowledge_map.py`, `app/api/knowledge_map.py`, `frontend/src/pages/mobile/student/KnowledgeMapPage.tsx` + 路由, 教师端地图视图
- **修改文件**: `agent/tools/__init__.py`（TOOL_META + teacher.yaml）, `app/main.py`（router 注册）, `frontend/src/App.tsx`（/m 路由）
- **依赖**: 无新外部依赖（无库无数据集）
- **测试**: 拓扑遍历/就绪过滤纯函数单测、边状态机单测、工件绑定只读已分享版本的契约测试
