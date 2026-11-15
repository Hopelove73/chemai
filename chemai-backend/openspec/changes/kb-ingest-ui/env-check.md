# kb-ingest-ui 环境探测记录（任务 0.1）

探测时间：2026-11-04（实施会话内）

| 项 | 结果 |
|---|---|
| EMBEDDING_CACHE_DIR | 本 shell 未设 env，但 `D:/hf-cache` 目录存在（3 项） |
| bge-large-zh-v1.5 本地加载 | ✅ `SentenceTransformer('BAAI/bge-large-zh-v1.5', cache_folder='D:/hf-cache')` 加载成功 |
| 嵌入冒烟 | ✅ encode 冒烟成功，输出维度 1024（与规格一致） |
| chroma 主进程读写 | ✅ 后端服务与摄取 CLI 共用 `data/chromadb`（kb_chunks 集合）；admin API 挂主进程内，天然满足同进程约束 |
| 实测嵌入测试 | 仓库既有 2 个 `test_local_embedding` 失败为环境依赖存量（HANDOFF 坑 8），不影响本判定 |

**结论：前置门通过，本 change 继续实施**（部署形态 = 本机开发/演示一体机，嵌入环境齐备）。
注意：运行摄取 job 时服务进程需带 `EMBEDDING_CACHE_DIR=D:/hf-cache`（启动脚本/start.bat 已含）。
