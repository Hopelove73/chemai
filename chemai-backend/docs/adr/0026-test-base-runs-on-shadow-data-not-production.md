# ADR-0026: 测试基座落在影子库上，跑测试不许写生产件

**Date**: 2026-03-28
**Status**: Accepted（用户裁定：「别挂着了啊，一起做了」，工单 ISO1）

## Context

`chemai-backend/chemai.db` 是**被跟踪**的数据文件（`930e80c chore(data): 演示数据集入库`，
31MB；340 个学生、几千道真题、上万条通知），`data/chromadb` 是它的向量索引。整套后端测试
里有几百个用例自己建内存库（`create_engine("sqlite://")`），但凡走 `app.models.database`
默认 engine 的那一部分，写的就是这台机器上的生产文件。

本机实测（2026-03-28，跑一次 `pytest tests` 之后与工作区提交版对比行数）：
`exam_records` 2443→2787、`student_notifications` 2380→3404、`parent_notifications`
2588→3274、`conversation_owners` 0→46、`question_sets` 0→4、`learning_plan_history` 0→5，
外加测试造出来的题目 id 进了 `data/chromadb`（上一轮实测 812→825 条向量，`Add of existing
embedding ID: q_20260928002521_2::kp-1` 就在日志里）。

代价不是"脏一点"这么简单：

- 谁 `git add -A` 就把测试垃圾提交进库（这条仓库已经出现过一次要专门 `fix(data)` 换回
  迁移后的 chemai.db）；
- `git checkout <sha> && pytest` 之后工作区必然变脏，bisect 与"单独验证某个提交"都被污染；
- 用例之间通过"上一轮测试留下的行"互相看见，同一套测试在两台机器上是两种结果。

## Decision

1. **根 `conftest.py` 在任何 app 导入之前把两个路径改指临时目录里的副本**。这两个路径都是
   import 期定死的（`app/core/config.py` 用 `BASE_DIR` 锚出绝对路径；`app/models/database.py`
   认 `settings.DATABASE_URL` 但允许显式环境变量覆盖），所以唯一能改的地方就是这里 —— 和
   钉 `.env` 那些行是同一个理由。
2. **复制，不建空库**。不少用例断言的就是这份演示数据里的具体东西（340 个学生、某道真题的
   考点）。空基座会把它们全打成 skip 或者假绿。
3. **`setdefault` 而不是直接赋值**：谁从外面明确给了 `DATABASE_URL` / `CHROMA_DB_PATH`
   （比如专门对某个影子库跑的脚本），就以他给的为准。
4. **WAL 边车一起复制**（`-wal` / `-shm`）：SQLite 的 WAL 里可能还有没合并回主文件的页，
   只拷主文件会读到落后的快照。
5. **`atexit` 删目录**，不做 per-test 清理：会话级一份副本，跑完整个 pytest 都用它。用例
   之间的串扰本来就是被钉的问题之一（见 Context 第 3 条），但它换成"跑一次测试"这个边界之
   内，不再跨进程、不再跨机器，也不再写进仓库。
6. **判据是文件指纹，不是"看起来没报错"**：`tests/test_*` 全绿之后，还要 `chemai.db` 的
   sha1 与 `data/chromadb` 的 mtime 在跑前跑后逐位相同才算 ISO1 关闭（实测：
   `7a6a34af81b4 → 7a6a34af81b4`，mtime `1790583004 → 1790583004`，1349 passed / 5 skipped）。

6. **评估正门走同一个基座**。`pytest` 会加载根 `conftest.py`，但 `evals/run_evals.py` 里
   `kind="script"`（直接 `python evals/xxx.py`）与 `kind="function"`（runner 进程内调用）两类
   条目根本不经过它 —— 只修 conftest 的话，CI 每天跑的 `--fast` 仍然写生产库。所以口径抽进
   `app.shadow_data`（建副本并返回该设的两条环境变量），conftest 与 `evals/run_evals.py` 各自
   调用；正门那句 `activate()` 必须排在 `from evals.runner import ...` 之前，这条有绊线加正向
   对照。两条环境变量都已被显式指定时不再复制 47MB；源库不存在时也不返回一个不存在的路径 ——
   SQLite 会照着建出空库，把"读不到演示数据"变成静默的语义翻转。

## Consequences

- 跑测试不再弄脏仓库，`git checkout <sha> && pytest` 与 bisect 重新可用；演示数据仍然读得到，
  所以没有为了干净而砍掉任何用例。
- 每次 pytest 多复制 ~47MB（31MB 库 + 16MB 索引），实测增加不到一秒，换来的是"这台机器的
  生产件不会被测试写"。
- **已知还没管的**（这一轮审出来，记在这儿）：`chemai_store.db` 与 `chemai_checkpoints.db`
  （`agent/channel/conversation.py` 里还有 DELETE）是另一套硬编码路径，gitignored 所以不脏仓库，
  但同机并发跑测试会互踩真实对话数据；`chem_skills/chemistry_memory` 写的 `memory_data/`
  **是被 git 跟踪的**，仓库里已经躺着 `students/test_001/profile.md` 这类测试残骸。这三处要一起
  收进同一个基座，得单独一轮。
- **没覆盖的**：仍然直连 `chemai.db` 的脚本（`_datagen/*`、手工 sqlite 连接）照旧写生产文件 ——
  那是它们的职责，不是测试。测试基座管的是 pytest 这一路。
- 副本是会话级的：如果有用例将来需要"干净的库"，应该自己建内存 engine（现有做法），而不是
  指望生产副本是空的。
