"""
向量检索服务
基于Chroma实现题目相似度检索
两层检索：第一层简单匹配（初筛），第二层向量检索（精筛）
"""
import logging
import os
import sys
import threading
import time
from typing import List, Dict, Optional

# 添加项目根目录到path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings

# 同 app/middleware/auth.py：本模块自己读一次 `.env`，不指望导入顺序。`load_dotenv` 不覆盖
# 已存在的环境变量，所以测试基座钉过的值仍然优先。
from dotenv import load_dotenv as _load_dotenv

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_load_dotenv(os.path.join(_BACKEND_ROOT, ".env"))

logger = logging.getLogger("chemai.vector")

# ── 本地 embedding ──────────────────────────────────────────────────────
# 为什么不用 dashscope `text-embedding-v3`：它要 `DASHSCOPE_API_KEY`，没配就静默退回伪向量
# （见 `_get_embedding` 的说明）。本机实测库里 1397 条有 1385 条就是那种指纹。
# 模型目录由 `HF_HOME` 决定（本机指向 D 盘 —— 模型不往 C 盘放）。
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")

_encoder = None
_encoder_error: Optional[BaseException] = None
_encoder_lock = threading.Lock()


def original_exam_id(chunk_id: str) -> str:
    """把库里 (题目, 考点) 粒度的 id 说回"题本身的 id"。

    `_chunk_id` 拼出来的是 `nat_2020_t4::kp-3`，而库外的人（`exam_bank_service`、出题端点）
    手里的 id 都是 `nat_2020_t4`。以前 `search_similar` 把 chunk id 直接放进 `exam_id` 字段
    返回，出题端点拿它去 `get_by_exam_id()` 精确比对 —— 本机实测 5 条命中解析出 0 条：向量
    这一层跑对了，题照样进不了提示词。对外一律剥回原题 id，chunk id 留在 `chunk_id` 字段里
    供追溯。
    """
    return chunk_id.split("::")[0] if "::" in chunk_id else chunk_id


def _chunk_id(exam_id: str, kp_index: int) -> str:
    """一条 (题目, 考点) 在这个库里的唯一 id。

    写库与"库全不全"的判据必须用同一个函数拼：上一版一边写 `f"{exam_id}::kp-{i}"`、一边只比
    条数，两边对"什么算一条"的理解一旦分叉，缺题是看不出来的。
    """
    return f"{exam_id}::kp-{kp_index}"


def _load_encoder():
    """真正去磁盘上把 bge 读进来。分成一个函数只是为了让"失败一次就记住"有地方挂。"""
    from sentence_transformers import SentenceTransformer

    # 直接指向本地模型目录（含 config.json / model.safetensors）时**不走**
    # snapshot_download：那一步无论本地有没有缓存都会连 huggingface 校验 revision
    # （2026-10 实测：断网环境下 bge-large 报 MaxRetryError/SSLError，连带「搜真题」
    # 「联网搜索」整轮 400 中止，而本地 1.3G 的模型就在盘上躺着）。
    local_dir = os.getenv("EMBEDDING_MODEL_DIR")
    if local_dir:
        if not os.path.isfile(os.path.join(local_dir, "config.json")):
            raise RuntimeError(
                f"EMBEDDING_MODEL_DIR={local_dir} 不是 SentenceTransformer 模型目录（缺 config.json）")
        return SentenceTransformer(local_dir, device=os.getenv("EMBEDDING_DEVICE", "cpu"))

    cache = os.getenv("EMBEDDING_CACHE_DIR") or os.getenv("HF_HOME")
    if not cache:
        # 不给目录就要吃默认值，而默认值是系统盘的 .cache/huggingface —— 1.3G 悄悄落在
        # C 盘上。宁可在这里说清楚，也不边下载边道歉。
        raise RuntimeError(
            "EMBEDDING_CACHE_DIR（或 HF_HOME）未设置：本地 embedding 模型约 1.3G，需要一个"
            "明确的目录（本机用的是 D 盘上的一个目录，例如 D:/hf-cache）；不给就拒绝加载。")
    from huggingface_hub import snapshot_download
    # 先解析成本地路径再交给 SentenceTransformer：直接传模型名 + cache_folder 实测仍会按
    # huggingface 的默认缓存再下一份到 C 盘。
    #
    # `snapshot_download(cache_dir=X)` 把 X 当**模型仓库的上一级**，模型进 `X/models--BAAI--...`；
    # HF 自己的约定是 `HF_HOME/hub`，所以普通目录要拼一层 hub。但拼之前得看盘上实际有什么：
    # 有人把 `EMBEDDING_CACHE_DIR` 写成 `D:/hf-cache/hub`（就是 hub 那一层本身），再拼一次就
    # 变成 `hub/hub` —— 空目录，于是 huggingface 会重新下 2.5G，而另一份已经在同一个盘上躺着。
    repo_dir = "models--" + EMBEDDING_MODEL.replace("/", "--")
    root = cache if os.path.isdir(os.path.join(cache, repo_dir)) else os.path.join(cache, "hub")
    local = snapshot_download(EMBEDDING_MODEL, cache_dir=root)
    return SentenceTransformer(local, device=os.getenv("EMBEDDING_DEVICE", "cpu"))


def get_text_encoder():
    """进程常驻的编码模型，懒加载 + 双检锁 + 失败记忆。

    CPU 冷加载实测 20~99s（bge-large 0.57B），所以绝不能"每次调用建一次"：启动时由
    `app/main.py` 在后台线程里预热一次，之后每次 encode 约 250ms/条。

    预热挡不住的是"预热还没完/预热失败"那两种情况：那时第一个搜索请求会去付这几十秒，
    而它付的位置是事件循环上（`app/api/question.py` 的相似题检索）。所以搜索的调用方一律
    `asyncio.to_thread`，这里只管"别反复付"。
    """
    global _encoder, _encoder_error
    if _encoder is not None:
        return _encoder
    if _encoder_error is not None:
        # 加载不起来是配置问题（目录没给对、盘没挂载），不是抖一下就好。不记住的话，
        # 每个搜索请求都要重付一遍那几十秒的超时 —— 一个配置错误被摊派给所有请求。
        raise RuntimeError(
            f"embedding 模型此前加载失败，已停止重试：{_encoder_error}（修好配置后重启服务）")
    with _encoder_lock:
        if _encoder is None and _encoder_error is None:
            try:
                _encoder = _load_encoder()
            except BaseException as exc:      # 记住原始异常类型，重启前不再重试
                _encoder_error = exc
                raise
    return _encoder


class VectorSearchService:
    """
    向量检索服务
    使用Chroma作为向量数据库，支持题目相似度检索
    """

    # historical_exams.difficulty 存 1~5（见 `Difficulty`），向量文本写标签
    _DIFFICULTY_LABEL = {1: "easy", 2: "moderate", 3: "medium", 4: "hard", 5: "competition"}

    def __init__(self):
        self.chroma_path = settings.CHROMA_DB_PATH
        self.collection_name = "exam_questions"
        self.chroma_client = None
        self.collection = None
        self._index_lock = threading.RLock()
        # 最近一次"这一层没干活"的事实（阶段 + 原因 + 时间）。成功一次就清空，所以它
        # 说的是"现在"，不是"历史上坏过"。
        self.last_failure: Optional[Dict] = None
        self._init_chroma()

    def _init_chroma(self):
        """初始化Chroma客户端。

        型号戳只在这里、**新建那个空库**的时候写。chroma 0.4.22 的
        `get_or_create_collection(metadata=...)` 会把元数据盖到已存在的库上 —— 本机实测：一个
        装着 1385 条伪向量指纹的旧库，仅仅被实例化一次就"变成"了 bge-large 已建好。所以这里是
        先 `get_collection`，读到就用读到的（戳保持原样），读不到才建新的。
        """
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            os.makedirs(self.chroma_path, exist_ok=True)
            self.chroma_client = chromadb.PersistentClient(
                path=self.chroma_path,
                settings=ChromaSettings(anonymized_telemetry=False)
            )
        except ImportError:
            self.note_failure("依赖", "chromadb 未安装，相似题检索只剩简单匹配")
            self.chroma_client = None
            self.collection = None
            return
        except Exception as e:
            # 建客户端本身也抛（`data/chromadb` 是个文件、目录没权限、sqlite 头坏掉），而本模块
            # 在文件末尾建全局实例：那时异常升级成"整个应用导入即炸"。接住，降级成没有向量检索。
            self.note_failure("打开向量库", f"{type(e).__name__}: {e}（本轮只用简单匹配）")
            self.chroma_client = None
            self.collection = None
            return
        try:
            # 先问"这个库里有没有它"，而不是 try get 再 except create：
            # `get_collection` 可能因为**库存在但读不动**而抛（chroma 0.4.22 就是
            # `Cannot open header file`），那时走"那就建一个"会撞 UniqueConstraintError，
            # 而本模块在文件末尾建全局实例 —— 导入即炸，向量检索坏了变成"整个应用起不动"。
            existing = {c.name for c in self.chroma_client.list_collections()}
            if self.collection_name in existing:
                self.collection = self.chroma_client.get_collection(name=self.collection_name)
            else:
                self.collection = self._create_collection()
        except Exception as e:
            # 连列目录都失败（sqlite 读不动之类）：降级成"只有简单匹配"，让应用还能起来，
            # 并把原因留在日志里。判据本身在 `_stale_reason`，不在这里。
            self.note_failure("打开向量库", f"{type(e).__name__}: {e}（本轮只用简单匹配）")
            self.collection = None
            return
        print(f"Chroma initialized at {self.chroma_path}")

    def note_failure(self, stage: str, reason: str) -> None:
        """把"这一层没干活"记进台账，并留一条日志。

        只 print 的话，事实就只活在服务器 stdout 里：老师看到的仍然是"这个考点没有相似
        真题"，而它真实的意思可能是向量库整个读不动 —— 这两种情况他要做的事完全不同
        （等一会儿再试 vs 告诉运维后端坏了）。工单 VS1 要的就是这条区别。
        """
        self.last_failure = {"stage": stage, "reason": reason,
                             "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        logger.warning("向量层降级（%s）：%s", stage, reason)

    def layer_status(self) -> Dict:
        """这一层现在到底能不能用、上次坏在哪。`/health` 与出题响应用的都是这一份事实。

        这里故意**不写台账**：`/health` 是被反复调的探针，而全量重建正跑在另一个线程里
        （`self.collection` 会被换成新对象），探针撞上那一瞬间读不到条目数是正常的。探针
        把一个正常窗口记成"这层坏了"，台账就再也不会自己清了 —— 读不动这件事只进本次返回值
        的 `read_error`。
        """
        available = self.collection is not None
        count, read_error = None, None
        if available:
            try:
                count = self.collection.count()
            except Exception as e:
                read_error = f"{type(e).__name__}: {e}"
                available = False
        return {
            "available": available,
            "vector_count": count,
            "embedding_model": EMBEDDING_MODEL,
            "chroma_path": str(self.chroma_path),
            "degraded": available is False or self.last_failure is not None,
            "last_failure": self.last_failure,
            "read_error": read_error,
        }

    def _create_collection(self):
        """新建一个空库并盖上当前型号：空库里没有任何向量，所以这句话不会说谎。

        戳必须在 `metadata` 里一次写定：chroma 0.4.22 只要看到 `hnsw:space` 就拒绝之后再
        `modify()` 元数据（`Changing the distance function ... is not supported`），"先建库、
        向量真落库后再补戳"那条路在这台机器上走不通。补 `space` 标记只能由
        `_stamp_space_marker()` 走（它会把 hnsw 键剔掉再写）。
        """
        return self.chroma_client.create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine",
                      "description": "历年真题题目向量库",
                      "embedding_model": EMBEDDING_MODEL}
        )

    def _space_is_cosine(self) -> bool:
        """实测距离函数是不是余弦 —— 标记被抹掉时唯一还能拿到的证据。

        同一对向量，余弦距离是 `1 - a·b/(|a||b|)`，chroma 的 l2 距离是平方欧氏
        `|a|² + |b|² - 2a·b`。做法是拿库里一条向量当 query，对**返回的那几条**逐个算两种式子，
        看实测值更接近哪一种，再投票。不能预先指定"拿第二条比对"：库里几百条向量，我抽到的那对
        通常不是彼此的近邻，它压根不会出现在结果里（上一版就是这么在这台机器上把余弦库误判成
        非余弦的）。两种式子算出来一样的那条（自身、或完全相同的向量）也不参与投票。
        """
        import math

        try:
            got = self.collection.get(limit=8, include=["embeddings"])
            ids, vecs = list(got.get("ids") or []), list(got.get("embeddings") or [])
        except Exception as e:
            # 不记台账的话，"读不动"就被下面当成"空间不是余弦"，触发一次 8 分钟重建，
            # 而日志里只剩 `Recreating collection` 那行 —— 真实原因看不见了。
            self.note_failure("距离函数实测", f"取向量失败 {type(e).__name__}: {e}")
            return False
        if not ids or vecs is None:
            return False
        a = [float(x) for x in vecs[0]]
        na = math.sqrt(sum(x * x for x in a))
        if na == 0:
            return False
        try:
            res = self.collection.query(query_embeddings=[a], n_results=min(6, len(ids) or 1),
                                        include=["distances"])
            near_ids, dists = res["ids"][0], res["distances"][0]
            if not near_ids:
                return False
            fetched = self.collection.get(ids=near_ids, include=["embeddings"])
        except Exception as e:
            self.note_failure("距离函数实测", f"{type(e).__name__}: {e}")
            return False
        by_id = dict(zip(list(fetched.get("ids") or []), list(fetched.get("embeddings") or [])))
        votes = []
        for near_id, raw in zip(near_ids, dists):
            if near_id == ids[0]:
                continue
            other = by_id.get(near_id)
            if other is None:
                continue
            b = [float(x) for x in other]
            nb = math.sqrt(sum(x * x for x in b))
            if nb == 0:
                continue
            dot = sum(x * y for x, y in zip(a, b))
            cos_d = 1 - dot / (na * nb)
            l2_d = na * na + nb * nb - 2 * dot
            if abs(cos_d - l2_d) < 1e-6:
                continue
            d = float(raw)
            votes.append("cosine" if abs(d - cos_d) <= abs(d - l2_d) else "l2")
        if not votes:
            return False
        return votes.count("cosine") > len(votes) / 2

    def _stamp_space_marker(self) -> None:
        """把 `space` 标记补回 metadata。失败不致命：下次开机再实测一遍。"""
        meta = {k: v for k, v in (self.collection.metadata or {}).items()
                if not k.startswith("hnsw:")}
        meta["space"] = "cosine"
        try:
            self.collection.modify(metadata=meta)
        except Exception as e:
            self.note_failure("补 cosine 标记",
                              f"{type(e).__name__}: {e}（下次开机再实测一遍）")

    def _stale_reason(self) -> str:
        """库里现有的向量还能不能用；能用返回 ''。

        四道判据，每道对应本机一个真实发生过的事：
        1. 距离函数不是余弦 —— `search_similar` 报的是 `similarity = 1 - dist`，这个式子只在
           余弦空间里等于相似度。标记缺失时不猜，实测。
        2. 型号戳不是当前型号 —— 换 embedding 模型必须重建，光比维度看不出来（两个模型都
           可能是 1024 维，旧向量照样躺在同一个余弦空间里被检索）。这一道**空库也要查**：
           空库一旦被追加写入就不再是空库，戳要是留着上一个型号的名字，那句"已建好"就成了谎。
        3. 戳对，但抽一条向量看形状：md5 指纹是"1024 维里点 29 个分量"，真编码是稠密的。
           这一道是为了自愈 —— 中间那一版代码在 `get_or_create_collection` 时把新型号戳盖到了
           旧库上，于是 1385 条指纹顶着 "bge-large" 的名义继续被当成已建好。
        4. 连向量都读不出来（chroma 0.4.22 读到旧的索引文件会抛 `Cannot open header file`）——
           读不动就等于不可用，别再假装它在工作。
        """
        if self.collection is None:
            return "chroma 不可用"
        meta = self.collection.metadata or {}
        space = meta.get("space") or meta.get("hnsw:space")
        if space is None:
            # 标记没了不等于空间错了：chroma 的 metadata 是整份替换的，`hnsw:space` 可能在
            # 某次 `get_or_create_collection`/`modify` 之后就不在读回来的字典里了，而索引本身
            # 没变。所以这里实测；是余弦就把标记补回去，别为了一次被抹掉的键重建 8 分钟。
            if not self.collection.count():
                return "库里既没有向量也没有 cosine 标记，无法确认距离函数"
            if self._space_is_cosine():
                self._stamp_space_marker()
                space = "cosine"
            else:
                space = "非 cosine（实测）"
        if space != "cosine":
            return f"距离函数是 {space!r}，不是 cosine（`1 - dist` 就不成立）"
        stamped = meta.get("embedding_model")
        if stamped != EMBEDDING_MODEL:
            return f"型号戳是 {stamped!r}，当前用的是 {EMBEDDING_MODEL!r}"
        if not self.collection.count():
            return ""
        try:
            vecs = self.collection.get(limit=1, include=["embeddings"]).get("embeddings")
        except Exception as e:
            return f"向量读不出来：{type(e).__name__}: {e}"
        if vecs is None or len(vecs) == 0:
            return "chroma 一条向量都没返回，库等于不可用"
        vec = vecs[0]
        nonzero = sum(1 for x in vec if float(x) != 0.0)
        if nonzero < len(vec) * 0.5:
            return f"抽到的向量 {len(vec)} 维只有 {nonzero} 个非零分量，是指纹不是编码"
        return ""

    def _recreate_collection(self) -> None:
        """丢掉整个库，重建一个空的、带当前型号戳的 collection。"""
        self.chroma_client.delete_collection(self.collection.name)
        self.collection = self._create_collection()

    def iter_indexable_questions(self) -> List[Dict]:
        """要进向量库的题目：直接读 `historical_exams` 表，不读内存里那份真题列表。

        为什么不能读 `exam_bank_service.questions`：那里冷启动时只有 JSON 真题 12 条，AI 生成题
        只在生成那次会话里挂在内存上。启动重建拿它当全量，等于把另外 760 多题的向量整个删掉
        （本机实测：表里 775 题 / 811 个考点，内存列表只有 12 题 / 48 个）。
        表才是权威：`add_question` 会 upsert 进去，启动时 `sync_all_to_db()` 先把 JSON 同步进去。
        """
        from app.models.database import HistoricalExam, get_db

        db = next(get_db())
        try:
            rows = db.query(HistoricalExam).all()
        finally:
            db.close()

        out: List[Dict] = []
        for r in rows:
            if not r.content or not r.knowledge_points:
                continue
            out.append({
                "exam_id": r.exam_id,
                "content": r.content,
                "knowledge_points": list(r.knowledge_points),
                # 表里存 1~5（`Difficulty`），向量文本历来写标签，这里换算回去，
                # 免得同一个字段在库里两种写法混着
                "difficulty": self._DIFFICULTY_LABEL.get(r.difficulty, "medium"),
                "source": r.source or "",
                "year": r.year or 0,
                "question_number": r.question_number or "",
                "answer": r.answer or "",
            })
        return out

    def _encode_questions(self, questions: List[Dict]):
        """把题目编成 (ids, embeddings, metadatas) 三份等长列表，一个字都不写进库。

        分成两步是有原因的：编码器现在会抛（没有伪向量兜底了），而"先删库再编码"意味着一次
        模型加载失败就把真题库删成空库，回滚的东西一点不剩。编码在前，写库在后。
        """
        ids, embeddings, metadatas = [], [], []
        for q in questions:
            exam_id = q.get("exam_id", "")
            content = q.get("content", "")
            knowledge_points = q.get("knowledge_points", [])
            difficulty = q.get("difficulty", "medium")
            source = q.get("source", "")
            year = q.get("year", 0)
            region = q.get("region", "")
            question_type = q.get("question_type", "")
            question_number = q.get("question_number", "")
            answer = q.get("answer", "")

            for kp_idx, kp_name in enumerate(knowledge_points):
                embed_text = self._build_embed_text(
                    kp_name, question_type, difficulty,
                    source, year, question_number, content, answer
                )
                ids.append(_chunk_id(exam_id, kp_idx))
                embeddings.append(self._get_embedding(embed_text))
                metadatas.append({
                    "exam_id": exam_id,
                    "kp_index": kp_idx,
                    "kp_name": kp_name,
                    "source": source,
                    "year": year,
                    "region": region,
                    "difficulty": difficulty,
                    "question_number": question_number,
                    "knowledge_points": ",".join(knowledge_points) if knowledge_points else "",
                    "content_preview": content[:200],
                })
        return ids, embeddings, metadatas

    def index_questions(self, questions: List[Dict], mode: str = "replace") -> int:
        """
        将题目索引到向量库。每个知识标签生成一个独立向量。
        questions: [{"exam_id": "...", "content": "...", "knowledge_points": [...], ...}]
        mode: "replace" — clear collection and rebuild; "append" — add to existing
        返回: 成功索引的向量数量（= Σ knowledge_points）
        """
        if not self.collection:
            self.note_failure("索引写入", "向量库不可用，本次索引没写进去")
            return 0

        if not questions:
            return 0

        ids, embeddings, metadatas = self._encode_questions(questions)
        if not ids:
            return 0

        # 整个"改库"的过程持锁，与 `search_similar` 互斥：重建会把 `self.collection` 换成
        # 另一个对象，读的一侧如果正好拿着旧对象，写进去就丢了（本机实测的重建窗口 ~8 分钟）。
        with self._index_lock:
            stale = self._stale_reason()
            if stale:
                if mode == "append" and self.collection.count():
                    # 一次 append 只带几条题目，拿它去重建等于把整个真题库清成这几条。
                    # 这里宁可报错，让全量重建（`build_index_from_exam_bank(force=True)`）来做。
                    raise RuntimeError(
                        f"向量库需要先全量重建，不接受追加写入：{stale}"
                        "（调用 build_index_from_exam_bank(force=True)）")
                print(f"Recreating collection: {stale}")
                self._recreate_collection()
            elif mode != "append":
                # 库没过期的全量替换：按 id 删旧条目。用 `get()`（只读元数据）而不是碰向量段 ——
                # 向量段正是 `_stale_reason` 可能判读不动的那一块。
                existing_ids = self.collection.get().get("ids", [])
                if existing_ids:
                    self.collection.delete(ids=existing_ids)
            self.collection.add(ids=ids, embeddings=embeddings, metadatas=metadatas)
        # 向量真的写进去了：这一层在干活，把启动期可能留下的那笔实测/补标记记录清掉，
        # 否则 /health 会一直报降级到下一次检索为止。
        self.last_failure = None
        return len(ids)

    def _build_embed_text(self, kp_name: str, question_type: str, difficulty: str,
                          source: str, year: int, question_number: str,
                          content: str, answer: str) -> str:
        """Build embedding text with knowledge point as the primary signal."""
        return (
            f"考点：{kp_name}。"
            f"题型：{question_type}。"
            f"难度：{difficulty}。"
            f"来源：{source} {year}年第{question_number}题。"
            f"题目：{content[:500]}。"
            f"答案：{answer}"
        )

    def search_similar(
        self,
        query_text: str,
        knowledge_points: List[str],
        difficulty: str,
        limit: int = 5
    ) -> List[Dict]:
        """
        搜索相似题目（两层检索）
        1. 第一层：简单匹配（知识点重叠度）
        2. 第二层：向量检索（精筛）

        返回: [{"exam_id": "...", "source": "...", "similarity": 0.85, "match_method": "vector"}, ...]
        """
        if not self.collection:
            return []

        # 获取所有已索引的题目。这一步要在 try 里：全量重建就在另一个线程里跑着，
        # 库被换掉/读不动的时候应该降级为"没有向量结果"，而不是把请求炸成 500。
        try:
            with self._index_lock:
                all_data = self.collection.get()
        except Exception as e:
            self.note_failure("读索引", f"{type(e).__name__}: {e}")
            return []
        if not all_data or not all_data.get("ids"):
            return []

        # 第一层：简单匹配筛选候选题
        candidate_ids = self._simple_match(knowledge_points, difficulty, all_data)

        # 第二层：向量检索精筛
        query_embedding = self._get_embedding(query_text)

        if candidate_ids:
            # 有候选时：全量向量检索后按候选 id 过滤（chromadb 0.4.x 的 query()
            # 不支持 ids 参数限定子集，where 又只过滤 metadata，故客户端过滤）
            candidate_set = set(candidate_ids)
            n_results = len(all_data.get("ids", []))
        else:
            # 无候选（自由文本搜索或无匹配知识点）：全量向量搜索
            candidate_set = None
            n_results = min(limit * 2, len(all_data.get("ids", [])))

        try:
            with self._index_lock:
                results = self.collection.query(
                    query_embeddings=[query_embedding],
                    n_results=n_results,
                )
        except Exception as e:
            self.note_failure("向量查询", f"{type(e).__name__}: {e}（本次退回简单匹配排序）")
            if not candidate_ids:
                return []
            # 降级到简单匹配结果
            return self._simple_match_results(candidate_ids[:limit], all_data, "simple")

        # 整理结果
        similar_questions = []
        for i, (id, dist) in enumerate(zip(results["ids"][0], results["distances"][0])):
            if candidate_set is not None and id not in candidate_set:
                continue
            metadata = results["metadatas"][0][i]
            # 余弦空间里 `1 - dist` 就是相似度；`dist == 0` 是**同一条向量**（完全一致），
            # 旧写法在这里返回 0.5，于是最匹配的那条反而排到 0.6 的那批后面去。
            similarity = 1 - float(dist)

            similar_questions.append({
                "exam_id": original_exam_id(id),
                "chunk_id": id,
                "kp_name": metadata.get("kp_name", ""),
                "source": metadata.get("source", ""),
                "year": metadata.get("year", 0),
                "region": metadata.get("region", ""),
                "knowledge_points": metadata.get("knowledge_points", "").split(","),
                "similarity": round(similarity, 3),
                "match_method": "vector"
            })

        # 向量查询真跑成了：这一层的健康台账清零，`degraded` 说的都是"现在"
        self.last_failure = None
        # 按相似度排序
        similar_questions.sort(key=lambda x: x["similarity"], reverse=True)

        return similar_questions[:limit]

    def _simple_match(
        self,
        knowledge_points: List[str],
        difficulty: str,
        all_data: Dict = None
    ) -> List[str]:
        """
        简单匹配筛选候选（第一层）
        基于知识点重叠度快速筛选
        """
        if all_data is None:
            if not self.collection:
                return []
            all_data = self.collection.get()

        if not all_data or not all_data.get("ids"):
            return []

        candidates = []
        kp_set = set(knowledge_points)

        for i, metadata in enumerate(all_data["metadatas"]):
            q_kps = set(metadata.get("knowledge_points", "").split(","))
            kp_name = metadata.get("kp_name", "")
            # Score: exact kp_name match gets bonus weight
            overlap = len(kp_set & q_kps)
            if kp_name and kp_name in kp_set:
                overlap += 2  # bonus for matching the specific chunk's kp

            if overlap > 0:
                # 难度匹配优先
                if metadata.get("difficulty") == difficulty:
                    candidates.append((all_data["ids"][i], overlap, True))
                else:
                    candidates.append((all_data["ids"][i], overlap, False))

        # 排序：先按匹配度，再按难度匹配
        candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)

        return [c[0] for c in candidates[:20]]  # 返回top20候选

    def _simple_match_results(
        self,
        exam_ids: List[str],
        all_data: Dict,
        method: str
    ) -> List[Dict]:
        """将简单匹配结果转换为标准格式"""
        id_to_metadata = {i: m for i, m in zip(all_data["ids"], all_data["metadatas"])}

        results = []
        for exam_id in exam_ids:
            if exam_id in id_to_metadata:
                metadata = id_to_metadata[exam_id]
                results.append({
                    "exam_id": original_exam_id(exam_id),
                    "chunk_id": exam_id,
                    "source": metadata.get("source", ""),
                    "year": metadata.get("year", 0),
                    "region": metadata.get("region", ""),
                    "knowledge_points": metadata.get("knowledge_points", "").split(","),
                    "similarity": 0.5,  # 简单匹配不给具体相似度
                    "match_method": method
                })

        return results

    def _get_embedding(self, text: str) -> List[float]:
        """本地 bge 编码。加载或推理失败就抛出去，绝不换一种向量顶上。

        原来这里是：try dashscope → 失败 print 一行 → `return self._pseudo_embedding(text)`。
        那个伪向量是 1024 维里只点 29 个分量的 md5 指纹，它既能通过所有维度检查，又让检索
        照常返回"看着合理"的 top-k —— 于是坏掉的相关性可以潜伏几个月。宁可让这条请求报错。
        """
        model = get_text_encoder()
        vector = model.encode(text[:2048], normalize_embeddings=True)
        return [float(x) for x in vector]

    def build_index_from_exam_bank(self, force: bool = False):
        """把 `historical_exams` 全量编码进向量库；库还能用且每条考点都在就跳过。"""
        # 只读表、不碰编码器，所以下面哪怕走完"跳过"分支也不会白加载模型
        questions_for_index = self.iter_indexable_questions()

        # 跳过重建的判据与 `index_questions` 共用同一个 `_stale_reason()`：两处各数一遍
        # "这库还能不能用"迟早分叉，分叉的样子是启动时认为索引已就绪、写入时又把它整个删掉。
        #
        # 旧写法是"拿伪向量的维度粗比"（>= 1024*0.8）：任何 1024 维的库都能过关，于是那 1385 条
        # 伪向量指纹被当成"索引已就绪"，年复一年跳过重建。它外面还套了一层裸吞异常的 pass
        # —— 索引读不动（本机就是 `Cannot open header file`）恰好也走"跳过"那条路。
        if not force and not self._stale_reason():
            # 比的是 id 集合，不是条数。条数判据（旧的 `>= 期望的 90%`）留了一个安静的洞：
            # 库里有重复/残留 id 时条数虚高，于是新题可以永远不进库，而谁都看不出少了什么。
            expected = {
                _chunk_id(q["exam_id"], i)
                for q in questions_for_index
                for i in range(len(q["knowledge_points"]))
            }
            missing = expected - set(self.collection.get().get("ids", []))
            if not missing:
                count = self.collection.count()
                print(f"Vector index up to date ({count} questions), skipping rebuild")
                return count
            print(f"Vector index behind: 缺 {len(missing)} 条（如 {sorted(missing)[0]}）")

        count = self.index_questions(questions_for_index)
        print(f"Indexed {count} vectors to vector store (from {len(questions_for_index)} questions)")
        return count

# 全局实例
vector_search_service = VectorSearchService()
