# ADR-0024: 题目向量改本地 bge，"索引已就绪"必须能被证伪

**Date**: 2026-03-27
**Status**: Accepted（用户裁定：「用本地的embedding模型」「这个模型是不是太小太老了有点垃圾啊，
能不能换bge更好一点的」「下在minerU自己的目录里，不要放C盘就行」）

## Context

改动前 `app/services/vector_search.py` 直连 dashscope `text-embedding-v3`，失败就 `print` 一行
然后 `return self._pseudo_embedding(text)`。本机 2026-03-27 实测到的后果：

- `DASHSCOPE_API_KEY` 从来没配过，那条 API 通路一次都没成功过；
- collection `exam_questions` 的 1397 条向量里有 1385 条是 md5 指纹 —— 1024 维里只点 29 个
  分量。它维度对、能返回 top-k、能通过旧判据"维度 >= 1024*0.8 就算已就绪"的检查，所以坏掉的
  相关性可以潜伏着，谁都不会看见；
- 而且这一层当时根本没在工作：`collection.query()` 抛 `RuntimeError: Cannot open header file`
  （chroma 0.4.22 读不动 9 月 12 日留下的索引文件），`search_similar` 的 `except` 只 print 一行
  就退回简单匹配。也就是说"向量精筛"在整个观察期内是死的，而系统的表现是"检索正常"。

真正要修的不是"哪家嵌入便宜"，是**失败了换一种向量继续跑**这个形状。

## Decision

1. **嵌入不出网**：`BAAI/bge-large-zh-v1.5`（1024 维；本机 CPU 单条 encode ~250ms，冷加载
   20~99s）。进程级懒加载 + 双检锁（`get_text_encoder`），启动时由 `app/main.py` 在后台线程
   预热一次，不让它落在老师第一次搜索的那个请求上。
   先试过 `bge-small-zh-v1.5`（187MB、18.7ms），用户判为太小太旧，换 large。
   预热挡不住两件事，两条都补了：(a) 加载失败没有记忆 —— 每个搜索请求都要重付一遍几十秒的
   超时，等于把一个配置错误摊派给所有人，现在失败一次就记住并在重启前直接抛；(b) 加载没完成时
   的搜索请求。`search_similar` 是同步的，而它的调用方是 `async def`（`app/api/question.py`
   六处、`agent/tools/generation.py` 一处），于是本地推理一旦落在事件循环上，一个请求就把整个
   服务按住几十秒。这些调用点现在一律 `asyncio.to_thread`，并由
   `tests/test_local_embedding.py::test_vector_search_is_never_called_from_the_event_loop`
   用 ast 钉住（判"直接外层是不是 to_thread"，同步 helper 里的调用不算违规，它的调用方负责换线程）。
2. **模型目录必须显式给**：`EMBEDDING_CACHE_DIR`（或 `HF_HOME`）没设就抛错，不吃系统盘。
   `SentenceTransformer(模型名, cache_folder=...)` 实测仍然按 huggingface 默认缓存再下一份
   2.5G 到 C 盘，所以改成 `snapshot_download(模型名, cache_dir=...)` 解析出本地路径再加载 ——
   这条顺序是这台机器的硬约束，不是风格问题。
3. **编码失败就抛出去**，`_pseudo_embedding` 整个删掉。判据是"没有第二种向量可退"，不是
   "另一种向量质量差"。
4. **"库还能不能用"收敛成一个 `_stale_reason()`**，四道判据，每道对应本机真实发生过的事：
   距离函数不是余弦（第 10 条）；型号戳不是当前型号（换模型必须重建，光比维度看不出来：两个
   模型都是 1024 维时，旧向量照样躺在同一个余弦空间里被检索）；戳对但抽一条向量是稀疏的（那是
   指纹，不是编码）；连向量都读不动（就是 `Cannot open header file`）。`index_questions` 与启动期
   的 `build_index_from_exam_bank` 共用它 —— 两处各数一遍迟早分叉，分叉的样子是启动时认为索引
   已就绪、写入时又把它整个删掉。
5. **型号戳只在"新建那个空库"时写**。chroma 0.4.22 有两个方向相反的坑，都在这台机器上撞到了：
   - 元数据里只要带 `hnsw:space`，之后 `modify()` 一律被拒（`Changing the distance function
     ... is not supported`），所以"先建库、向量真落库之后再补戳"这条路走不通；
   - 而 `get_or_create_collection(metadata=...)` 会把元数据**盖到已存在的库上**。上线那版传的
     是 `{"description": ...}`，于是每次开机把库上原有的戳**擦掉**；本次改造中途改成带
     `embedding_model` 的版本，同一行代码立刻变成往那个 1385 条伪向量的旧库上盖"已是 bge-large"
     —— 一次实例化就给坏库发了张真凭据，判据从此永不触发。
   两个方向都坏，所以戳只在"确实新建了一个空库"时写。判存在与否用 `list_collections()`，不用
   try-get 控制流程：一个**存在但读不动**的库同样会让 `get_collection` 抛，那时再去 create 就撞
   `UniqueConstraintError`，而本模块在文件末尾建全局实例 —— 导入即炸，"库坏了"会升级成"应用起不动"。
   空库带着旧戳也算过期（第 4 条的戳判据不看条数），否则一次追加写入就把假戳变成既有事实。
6. **全量重建的来源是 `historical_exams` 表**，不是内存里的 `exam_bank_service.questions`。
   本机实测（2026-03-27 那次重建）：表里 775 题 / 811 个考点，内存列表冷启动只有 12 题 / 48 个
   考点（AI 生成题只在生成那次会话里挂在内存上）。拿后者重建等于把 760 多题的向量整个删掉。
   表的条数会涨（见 Consequences 里记的测试污染），这两个数是那一次的事实，不是常量。
7. **先编码，后写库**。`index_questions` 以前是"删库 → 逐条编码"，而编码器现在会抛（没有伪
   向量兜底了）：一次模型加载失败就把真题库删成空库，回滚的东西一点不剩。改成整批编完再删再写
   （811 条 × 1024 维约 3.3MB，在内存里）。
8. **`mode="append"` 碰到过期库直接报错**，不去"顺手重建"：一次追加只带几条题目，拿它当全量
   就是把真题库清成这几条。重建是 `build_index_from_exam_bank` 的职责。唯一例外是库本来为空 ——
   没有可丢的东西，直接重建成带正确戳的空库再写。
   这件事的下游要知道：`question_bank._sync_search_indexes` 用一层 print 把这个报错咽掉了
   （`[QuestionBank] 索引同步跳过`），所以新出的题在有人跑全量重建之前进不了检索，而老师只看到
   "生成成功"。日志里那句带着 remedy（点名 `build_index_from_exam_bank(force=True)`）。
9. **"库全不全"比 id 集合，不比条数**。旧判据是 `现有条数 >= 期望的 90%`，而条数会被重复/残留
   id 撑高（本机实测库里 855 条 vs 表里 839 个考点），于是新题可以永远不进库 yet 判据一直绿。
   id 的拼法收进 `_chunk_id()` 一个函数：写库与判缺用同一个式子，两边对"什么算一条"的理解
   一旦分叉，缺题是看不出来的。
10. **余弦是真的了**：`_create_collection` 显式写 `hnsw:space: cosine`。上一版建库没写这一项，
    chroma 默认 l2，而 `search_similar` 算的是 `similarity = 1 - dist` —— 这个式子只在余弦空间里
    才等于相似度。也就是说以前即使向量是真的，报给老师的"相似度"也不是相似度。
10b. **但 `hnsw:space` 不能当唯一凭据，判不出来就实测**（2026-03-28 补，这条是提交之后在这台
    机器上撞出来的）。第 5 条说元数据会被整份擦掉，擦掉的后果比"戳没了"更糟：生产库读回来的
    元数据只剩 `{'embedding_model': ...}`，`hnsw:space` 不在里面，而索引本身**还是余弦的**。
    第一版判据写的是"标记不是 cosine 就过期"，于是这台机器每次开机都要全量重建（800 题 / CPU
    约 8 分钟），而且重建是"先删库再写"，中途挂了就是个空库。改法分三步：
    - 标记缺失时不猜：拿库里一条向量当 query，对**返回的候选**逐个比"余弦距离 `1 - cos`"和
      "平方欧氏 `|a|²+|b|²-2a·b`"哪种更接近实测值，投票判定。
    - 不能预先指定"拿第二条比对"：812 条向量的库里，随手抽的那对通常不是彼此的近邻，它根本
      不会出现在返回结果里 —— 上一版就是这么把真余弦库误判成"非 cosine"的。
    - 判出余弦就把一个普通键 `space: cosine` 写进元数据（`modify()` 拒绝带 `hnsw:space` 的字典，
      普通键可以），下次开机直接读它。实测生产库：首次判定 0.27s，重新打开 0.0s。
11. **一次性重建走 `_datagen/reindex_vectors.py`**，动手前先把 `data/chromadb` 整个复制到
   `data/chromadb.bak_pseudo_<时间戳>`（备份目标已存在就停下，不覆盖）。实测：811 条 / 484s，
   重建后判据从"读不出来"变成"可用"，三条抽样查询的 top-3 全是对口考点（0.575~0.668）。
12. **降级要说得出原因，并且走到老师眼前**（VS1 落地）。这一层坏掉的三种形状 —— 库开不起来、
    读索引抛异常、`query()` 抛异常 —— 以前各 print 一行然后 `return []`／退回简单匹配，事实
    只活在服务器 stdout。现在：
    - 服务实例上挂一份台账 `last_failure = {stage, reason, at}`，`_note_failure()` 写它并留
      `logging.getLogger("chemai.vector").warning(...)`；一次成功的向量检索把它清空，所以台账
      说的是"现在"而不是"历史上坏过"。
    - `layer_status()` 是唯一的对外口径：`available / vector_count / embedding_model /
      degraded / last_failure`。`/health` 里带 `vector` 段（以前 /health 只说 "healthy"，
      向量层整个坏掉它也照样绿）；`/api/question/generate` 的响应带 `retrieval`
      （`used / degraded / reason / available / vector_count`）。
    - 前端 `lib/retrieval-notice.ts` 把 `retrieval` 变成一句话，`ExamPage` 在生成结束时
      `toast.warning`。老师要做的事在两种情况下不同："这个考点确实没真题"= 什么都不用做，
      "向量库坏了"= 该找人修 —— 页面不写这行字，二者就无法区分。
    - 顺手补响应模型里缺的 `error` 字段：`return QuestionGenerateResponse(..., error="LLM生成
      失败…")` 早就在传，但模型没这个字段，pydantic 的 `extra=ignore` 在响应边界把它丢了，
      前端只能显示"生成失败"。蓝本变种那条通路不报降级（它不依赖向量检索）。
12b. **台账的三条规矩**（审查后补，都属于"降级要可信"）：
    - 探针不写台账。`layer_status()` 读条目数失败只进本次返回值的 `read_error` ——
      全量重建跑在另一个线程里，`/health` 撞上 `delete+create` 那一瞬间读不到条目数是正常的，
      把它记成这一层的故障就成了假警，而且假 degraded 会粘到下一次成功检索为止。
    - 一次成功的**写入**也清台账（不只清成功检索）。启动期那笔"实测/补标记"如果只能等检索
      来清，索引明明重建好了 `/health` 还在报降级。
    - 启动重建失败必须进台账：`build_index_from_exam_bank` 是"先删库再写"，半路挂了留下的就是
      一个空库，而 collection 仍在、`available` 仍 true。为此把那个后台任务抽成
      `app.main.build_vector_index_in_background()` —— 藏在 startup 闭包里就没办法单独测。
13. **`dist == 0` 的相似度是 1.0**：`search_similar` 原来写 `similarity = 1 - dist if dist
    else 0.5`，而余弦距离 0 就是"同一条向量"，被 `else 0.5` 记成半像 —— 最该排第一的那条反而
    掉到 0.6 那批后面。按蓝本重生成、老师改考点后重查都会走到这里。
14. **`search_similar` 对外的 `exam_id` 是题 id，不是 chunk id**（审查翻出来的真回归，比前面
    几条都贵）。库里存的是 `_chunk_id()` 那种 `nat_2020_t4::kp-3`，而返回值以前直接把它塞进
    `exam_id` 字段。出题端点拿它去 `exam_bank_service.get_by_exam_id()` 精确比对 —— 本机实测：
    一次搜索 5 条命中，能解析出的题是 **0** 条；剥掉 `::kp-N` 之后是 5/5。也就是说向量这一层
    从修好那天起就没真的把题送进提示词，而页面上一切正常。现在 `exam_id` 是原题 id，chunk id
    留在 `chunk_id` 字段里供追溯。
15. **命中要变成提示词里的题，就得查表**（决策 6 的另一半）。内存那份 `exam_bank_service
    .questions` 冷启动只有 JSON 的 12 条，而向量库覆盖的是 `historical_exams` 全量（本机实测：
    库里抽 200 个 id，内存能查到的只有 12 个）。所以 `exam_bank_service.get_reference(exam_id,
    db=...)` 先查内存、落空查表，session 由调用方传下来 —— 不传的话测试管不住它读的是哪个库。
16. **`retrieval.used` 由进了提示词的题各自带的 `match_method` 倒推**，不在各分支手写：手写会
    自相矛盾（向量层内部退回简单匹配排序时仍报 used=vector），也会在"命中一条都没解析出来"时
    替坏掉的通路说谎。

## Consequences

- 检索第一次有了能核对的质量基线，启动也不再依赖任何嵌入 API；`DASHSCOPE_API_KEY` 从必配项
  降级为可选（其余通路仍走 ADR-0023 那条缝）。
- CPU 上全量重建 ~8 分钟，放在后台线程；换型号一定要重算。可接受，因为不重算的代价就是这次
  看到的样子：相关性坏掉而表现正常。
- `sentence-transformers` 进 `requirements.txt`，带来 torch 这一坨依赖；换来的是离线、可复现、
  以及"库里的向量确实是某个公开型号的输出"这种可核对性。
- **VS1 已落地**（决策 12）：三种降级形状都有台账、日志、`/health` 与响应字段；`/health` 的
  `vector` 段是运维那一侧的入口，`retrieval` 是老师那一侧的入口。本 ADR 原先"建库与判据处
  可见、读路径不可见"的后半句就此关闭。
- 本通路不在 ADR-0023 的磁带接缝上：它根本不出网，也就没有"离线重放"这件事（0023 决策 8/15
  的口径不变）。

## 留下的窗口

`index_questions` 是"整批编码 → 删库 → 一次 `add`"。编码失败不会再伤到库（这是这次修的），
但 `add` 本身失败（磁盘满、进程被杀）仍会留下"空库 + 当前戳"的状态：三道判据此时都返回
"可用"，而本会话内没有第二次尝试（启动任务只跑一次）。下一启动由第 9 条的 id 集合判据发现缺题
并重建。彻底的做法是重建到临时库再原子换名 —— 那需要先确认 chroma 0.4.22 的换名语义，另开一刀。
