# ChemAI · 智辅化学

FastAPI + LangGraph 单 Agent + React 前端（三端同一 SPA）的高中化学 AI 教学平台。

## 仓库结构

```
chemai-backend/          后端 + 前端源码（app/ agent/ frontend/ tests/ evals/ docs/ openspec/）
  chemai.db              演示库（340 学生 / 9 班级 / 7.6 万作答）
  content/kb/            KB 语料与词典（六册 md + kp_dictionary + kp_tag_map）
  data/exam_questions/   真题题库（全国卷 / 湖南卷，254 道）
MANIFEST.txt             源码快照清单
运行说明-换机器.md        换机必读：密钥、依赖、embedding、数据路径、自检清单
```

## 快速开始

```bash
cd chemai-backend
pip install -r requirements.txt
cp .env.example .env        # 填 DEEPSEEK_API_KEY 等
start.bat                   # 开发模式：端口 8001 + 热重载
```

详细说明见 [`chemai-backend/README.md`](chemai-backend/README.md) 与
[`运行说明-换机器.md`](运行说明-换机器.md)；领域词汇表见
[`chemai-backend/CONTEXT.md`](chemai-backend/CONTEXT.md)，架构决策见
[`chemai-backend/docs/adr/`](chemai-backend/docs/adr/)。

## 技术栈

| 层级 | 选型 |
|------|------|
| Agent | LangGraph v2 单 ReAct（`create_react_agent`）+ GuardState 护栏 + SSE 流式通道 |
| LLM | DeepSeek 为主（agent/摘要），mimo / dashscope 可选 provider |
| 后端 | FastAPI + SQLAlchemy + SQLite（WAL 模式） |
| 前端 | React 18 + Vite + Tailwind（教师桌面段 + `/m/` 移动三端） |
| 教学算法 | FSRS 记忆调度、catsim CAT 选题、自研 BKT、IRT 题目标定 |
| 检索 | Chroma + 本地 BGE 嵌入 |

## 说明

- `.env` 未入库，密钥请自行配置（见 `.env.example`）。
- `data/chromadb/` 向量索引、`node_modules/`、缓存与日志按 `.gitignore` 排除。
