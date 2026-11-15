"""本地 embedding：不再有"失败就换一种向量"，库上必须有型号戳，换了就重建。

钉的是这台机器的真实事故：`exam_questions` 里 1397 条向量有 1385 条是 `_pseudo_embedding`
的 md5 指纹（1024 维里只有 29 个非零分量）。它能通过所有维度检查、让检索照常返回 top-k，
所以坏掉的相关性可以一声不响地潜伏着。
"""
import os

import pytest

import app.services.vector_search as vs


class _FakeEncoder:
    def __init__(self, dim=4, boom=False):
        self.dim = dim
        self.boom = boom
        self.seen = []

    def encode(self, text, normalize_embeddings=True):
        self.seen.append(text)
        if self.boom:
            raise RuntimeError("模型没下下来")
        return [0.25] * self.dim


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(vs.settings, "CHROMA_DB_PATH", str(tmp_path / "chroma"), raising=False)
    return vs.VectorSearchService()


def test_embedding_comes_from_the_local_encoder(monkeypatch):
    fake = _FakeEncoder(dim=8)
    monkeypatch.setattr(vs, "get_text_encoder", lambda: fake)
    svc = vs.VectorSearchService()
    got = svc._get_embedding("钠与水反应的方程式")
    assert got == [0.25] * 8
    assert fake.seen == ["钠与水反应的方程式"]


def test_a_failed_encode_raises_instead_of_returning_a_fingerprint(monkeypatch):
    """这是本次的核心断言：宁可这条请求报错，也不要一份看起来正常的假向量。"""
    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(boom=True))
    svc = vs.VectorSearchService()
    with pytest.raises(RuntimeError, match="模型没下下来"):
        svc._get_embedding("任意文本")
    # 伪向量那条路必须整个不存在，不是"暂时没走到"
    assert not hasattr(svc, "_pseudo_embedding"), "兜底还在，静默降级就还会发生"


def test_opening_an_existing_library_does_not_restamp_it(tmp_path, monkeypatch):
    """开机读库不得顺手改戳 —— 这正是上一版代码干出的事。

    `get_or_create_collection(metadata=...)` 在 chroma 0.4.22 里会把元数据**写到已存在的库上**：
    本机那个装着 1385 条 md5 指纹的旧库，仅仅被 `VectorSearchService()` 实例化过一次，戳就从
    None 变成了 "BAAI/bge-large-zh-v1.5"，于是它从此看起来"已经用当前模型建好了"。
    """
    monkeypatch.setattr(vs.settings, "CHROMA_DB_PATH", str(tmp_path / "chroma"), raising=False)
    import chromadb
    seed = chromadb.PersistentClient(
        path=str(tmp_path / "chroma"),
        settings=chromadb.config.Settings(anonymized_telemetry=False))
    # chroma 0.4.22 里唯一能给已存在的库写 hnsw 元数据的时机就是建库时（modify 会被拒），
    # 所以这里直接按"旧型号建好并塞了向量"来铺现场，而不是去 mock 一个假对象。
    seed.create_collection(name="exam_questions",
                           metadata={"hnsw:space": "cosine",
                                     "embedding_model": "some/older-model"})
    seed.get_collection("exam_questions").add(
        ids=["old"], embeddings=[[0.1] * 4], metadatas=[{"k": "1"}])

    reopened = vs.VectorSearchService()
    assert (reopened.collection.metadata or {}).get("embedding_model") == "some/older-model", \
        "重新打开一次就把戳改成了当前型号，等于给旧库发了张真凭据"


def test_a_new_library_is_stamped_with_the_model_it_will_hold(service):
    assert service.collection.metadata.get("embedding_model") == vs.EMBEDDING_MODEL


_ONE_QUESTION = [{
    "content": "等物质的量的钠与钾分别与水反应", "question_type": "选择题",
    "difficulty": "medium", "source": "测试", "year": 2025, "question_number": "1",
    "answer": "A", "knowledge_points": ["金属性质"], "options": ["A. a", "B. b"],
}]


def test_switching_the_model_rebuilds_even_at_the_same_dimension(service, monkeypatch):
    """同维度是最危险的一种换模型：旧向量留在同一个余弦空间里，谁都发现不了。

    先按"旧型号"建一个库、塞进一条旧向量，再把 EMBEDDING_MODEL 换成新值 —— 维度完全一样，
    只有型号戳能逼它重建。
    """
    import chromadb
    from app.core.config import settings

    client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH,
                                       settings=chromadb.config.Settings(anonymized_telemetry=False))
    old = client.get_collection("exam_questions")
    old.add(ids=["stale"], embeddings=[[0.1] * 4], metadatas=[{"k": "1"}])

    monkeypatch.setattr(vs, "EMBEDDING_MODEL", "BAAI/some-newer-model")
    fake = _FakeEncoder(dim=4)   # 维度与旧向量一致：只有型号戳能发现这是两回事
    monkeypatch.setattr(vs, "get_text_encoder", lambda: fake)

    built = service.index_questions(_ONE_QUESTION, mode="replace")

    assert built == 1
    after = service.collection
    assert after.metadata.get("embedding_model") == "BAAI/some-newer-model"
    assert "stale" not in (after.get()["ids"] or []), "旧模型的向量还混在库里"


def test_a_fingerprint_library_under_a_correct_stamp_still_rebuilds(service, monkeypatch):
    """本机真实存在的状态：戳是被上一版代码误盖上去的，库里还是那些稀疏的 md5 指纹。

    光看戳会认为"已经是当前模型了"，于是永远不重建 —— 第二道判据（抽一条向量看稠密度）
    就是为了把这种库认出来。
    """
    col = service.collection          # 新建时已带上当前型号戳：这就是"戳对、库是假的"那种状态
    fingerprint = [0.0] * 1024
    for i in range(29):               # 真 `_pseudo_embedding` 的形状：1024 维里点 29 个分量
        fingerprint[i] = 0.37
    col.add(ids=["fake"], embeddings=[fingerprint], metadatas=[{"k": "1"}])

    assert "非零分量" in service._stale_reason()
    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=1024))
    service.index_questions(_ONE_QUESTION, mode="replace")
    assert "fake" not in (service.collection.get()["ids"] or []), "指纹还混在库里"


def test_an_unreadable_library_is_reported_stale(service, monkeypatch):
    """chroma 0.4.22 读到旧索引文件会抛 `Cannot open header file` —— 读不动就等于不可用。"""
    col = service.collection
    col.add(ids=["ok"], embeddings=[[0.1] * 8], metadatas=[{"k": "1"}])

    def _boom(self, *a, **kw):
        raise RuntimeError("Cannot open header file")
    monkeypatch.setattr(type(col), "get", _boom)
    assert "向量读不出来" in service._stale_reason()


def test_the_rebuild_source_is_the_table_not_the_memory_list(service, patch_get_db):
    """启动重建的来源必须是 `historical_exams`。

    旧写法遍历 `exam_bank_service.questions` —— 冷启动时那里只有 JSON 真题 12 条，AI 生成题只在
    生成那次会话里挂在内存上。本机实测：表里 775 题 / 811 个考点，内存列表 12 题 / 48 个；拿后者
    重建等于把 760 多题的向量整个删掉。
    """
    import inspect

    from app.models.database import HistoricalExam

    db = patch_get_db
    db.add_all([
        HistoricalExam(exam_id="q_ai_1", source="AI生成", year=2026, question_number="1",
                       knowledge_points=["化学平衡"], difficulty=3, content="题目A", answer="A"),
        HistoricalExam(exam_id="nat_2", source="全国卷2020", year=2020, question_number="2",
                       knowledge_points=["离子方程式"], difficulty=1, content="题目B", answer="B"),
        HistoricalExam(exam_id="nat_3", source="全国卷2020", year=2020, question_number="3",
                       knowledge_points=[], difficulty=3, content="没有考点，不该进库", answer=""),
    ])
    db.commit()

    got = service.iter_indexable_questions()
    assert [g["exam_id"] for g in got] == ["q_ai_1", "nat_2"], "表里的题没被读到，或空考点的混进来了"
    assert [g["difficulty"] for g in got] == ["medium", "easy"], \
        "表里存 1~5，向量文本历来写标签，不换算就是两种写法混在一个库里"
    src = inspect.getsource(vs.VectorSearchService.build_index_from_exam_bank)
    assert "exam_bank_service" not in src, "启动重建又回去遍历内存列表了"


def test_append_refuses_a_library_it_would_wipe(service, monkeypatch):
    """`mode="append"` 只带几条题目。拿它去"重建"等于把整个真题库清成这几条。"""
    col = service.collection
    col.add(ids=["old"], embeddings=[[0.1] * 4], metadatas=[{"k": "1"}])
    monkeypatch.setattr(vs, "EMBEDDING_MODEL", "BAAI/some-newer-model")
    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=4))
    with pytest.raises(RuntimeError, match="build_index_from_exam_bank"):
        service.index_questions(_ONE_QUESTION, mode="append")
    assert "old" in (service.collection.get()["ids"] or []), "拒绝之前就已经把库删了"


def test_the_pseudo_shortcut_in_the_skip_check_is_gone():
    """`build_index_from_exam_bank` 以前拿伪向量长度当"维度大致对上"的凭据（>=1024*0.8），

    任何 1024 维的库都能过关 —— 那 1385 条指纹就是这样被判定为"索引已就绪"的。现在这里读
    的是型号戳。
    """
    import inspect

    src = inspect.getsource(vs.VectorSearchService.build_index_from_exam_bank)
    assert "_stale_reason()" in src, "跳过重建的判据又不看型号戳了"
    assert "_pseudo_embedding(" not in src, "还在拿伪向量当尺寸凭据"
    assert "except Exception" not in src, "判据一抛异常就被当成库是好的，于是永不重建"


def test_the_encoder_demands_a_cache_dir(monkeypatch, tmp_path):
    """没有目录就拒绝加载：默认值是系统盘的 .cache/huggingface，1.3G 会悄悄吃掉 C 盘。"""
    import sentence_transformers as st

    seen = {}

    class _Stub:
        def __init__(self, model, device=None):
            # 收到的是**已解析到指定目录的本地路径**，不是仓库名：传 cache_folder 那条路
            # 本机实测仍会按 huggingface 默认缓存（C 盘）再下一份 2.5G。
            seen["model"], seen["device"] = model, device

    import huggingface_hub
    def _fake_snapshot_download(repo, cache_dir=None):
        seen["cache_dir"] = cache_dir
        return os.path.join(cache_dir, *repo.split("/"))

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _fake_snapshot_download)
    monkeypatch.setattr(st, "SentenceTransformer", _Stub)
    monkeypatch.setattr(vs, "_encoder", None)
    monkeypatch.setattr(vs, "_encoder_error", None, raising=False)
    for k in ("EMBEDDING_CACHE_DIR", "HF_HOME"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="EMBEDDING_CACHE_DIR"):
        vs.get_text_encoder()
    # 上面那一次失败被记住了（这正是下一条测试要钉的行为），接着测成功路径前先清干净
    monkeypatch.setattr(vs, "_encoder_error", None, raising=False)

    # 用一个空的临时目录：写死真机路径会让这条测试取决于那台机器的盘上有什么
    # （见 `test_a_cache_dir_that_is_already_a_hub_root...`：目录里已有模型就不该再拼 hub）。
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path))
    assert vs.get_text_encoder() is not None
    # 生产代码用 os.path.join，Windows 上分隔符是反斜杠；这里只关心"落在哪个目录"
    root = str(tmp_path).replace(os.sep, "/")
    got = seen["cache_dir"].replace(os.sep, "/")
    assert got == f"{root}/hub", "解析目录没指到指定盘，还是会掉到 C 盘"
    got_model = seen["model"].replace(os.sep, "/")
    assert got_model == f"{root}/hub/{vs.EMBEDDING_MODEL}", got_model


def test_the_cache_root_follows_what_is_actually_on_disk(monkeypatch, tmp_path):
    """目录解析认盘上实际有什么，两种情况都要钉住。

    `snapshot_download(cache_dir=X)` 把 X 当模型仓库的上一级，而 HF 的约定是 `HF_HOME/hub`，
    所以普通目录要拼一层 hub。两个方向的错法都真实存在过：

    - 该拼的时候没拼：新机器（目录还不存在）把模型散在根上，和 `HF_HOME` 布局分叉。
    - 不该拼的时候拼了：有人把 `EMBEDDING_CACHE_DIR` 写成 `D:/hf-cache/hub`（已经是 hub
      那一层），再拼一次变成 `hub/hub` —— 空目录，于是 huggingface 把 2.5G 重下一遍，
      而同一个盘上正躺着一份完整的。这台机器的 `D:/hf-cache` 顶层那份重复副本就是这么来的。
    """
    import huggingface_hub
    import sentence_transformers as st

    seen = {}

    class _Stub:
        def __init__(self, model, device=None):
            pass

    def _fake(repo, cache_dir=None):
        seen["cache_dir"] = cache_dir
        return os.path.join(cache_dir, repo.split("/")[0])

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _fake)
    monkeypatch.setattr(st, "SentenceTransformer", _Stub)
    monkeypatch.delenv("HF_HOME", raising=False)

    def root_for(cache_dir):
        seen.clear()
        monkeypatch.setattr(vs, "_encoder", None)
        monkeypatch.setattr(vs, "_encoder_error", None, raising=False)
        monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(cache_dir))
        assert vs.get_text_encoder() is not None
        return seen["cache_dir"].replace(os.sep, "/")

    # 已经是 hub 布局：目录里有这个模型的 `models--BAAI--...`，就地用，不再拼
    repo_dir = "models--" + vs.EMBEDDING_MODEL.replace("/", "--")
    (tmp_path / repo_dir).mkdir()
    assert root_for(tmp_path) == str(tmp_path).replace(os.sep, "/"), "hub 那一层被再拼了一次"

    # 目录还不存在（新机器的第一次运行）：按 HF 约定拼 hub
    fresh = tmp_path / "fresh"
    assert root_for(fresh) == f"{str(fresh).replace(os.sep, '/')}/hub", "空目录没按 hub 布局走"


def test_an_empty_library_with_a_stale_stamp_is_still_stale(service, monkeypatch):
    """空库不看戳是上一个版本留的洞：一次追加就把假戳变成既有事实。"""
    stamped = service.collection.metadata.get("embedding_model")   # 建库那一刻的型号
    monkeypatch.setattr(vs, "EMBEDDING_MODEL", "BAAI/a-different-model")
    assert service.collection.count() == 0
    reason = service._stale_reason()
    assert "型号戳" in reason,         f"空库就不查戳了（库里还写着 {stamped!r}），一次追加就把假戳变成既有事实"


def test_a_failing_encoder_leaves_the_library_alone(service, monkeypatch):
    """编码在前、写库在后。

    以前是"删库 → 逐条编码"，而伪向量兜底删掉之后编码器会抛 —— 一次模型没加载成功就把真题库
    清成空库，且没有任何东西可以回滚。
    """
    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=4))
    service.index_questions(_ONE_QUESTION, mode="replace")
    assert service.collection.count() == 1
    before = sorted(service.collection.get()["ids"])

    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(boom=True))
    monkeypatch.setattr(vs, "EMBEDDING_MODEL", "BAAI/next-model")   # 逼它走"过期→重建"
    with pytest.raises(RuntimeError, match="模型没下下来"):
        service.index_questions(_ONE_QUESTION * 3, mode="replace")

    after = sorted(service.collection.get()["ids"])
    assert after == before, "编码还没成功，库就已经被删了"


def test_append_into_an_empty_stale_library_rebuilds_instead_of_refusing(service, monkeypatch):
    """报错是为了保住有内容的库；空库没有可丢的东西，直接重建。"""
    monkeypatch.setattr(vs, "EMBEDDING_MODEL", "BAAI/yet-another")
    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=4))
    assert service.index_questions(_ONE_QUESTION, mode="append") == 1
    assert service.collection.metadata.get("embedding_model") == "BAAI/yet-another"


def test_the_skip_check_notices_a_missing_question_even_when_counts_look_fine(
        service, monkeypatch, patch_get_db):
    """`现有条数 >= 期望的 90%` 这类判据会被重复/残留 id 撑绿。

    本机实测就是这个形状：库里 855 条、表里 839 个考点 —— 用条数判永远"已就绪"，新题可以
    一百年不进库。
    """
    from app.models.database import HistoricalExam

    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=4))
    service.index_questions([
        {"exam_id": "nat_a", "content": "甲", "knowledge_points": ["考点一"], "question_type": "",
         "difficulty": "medium", "source": "S", "year": 2020, "question_number": "1", "answer": "A"},
    ], mode="replace")

    db = patch_get_db
    db.add(HistoricalExam(exam_id="nat_new", source="全国卷2026", year=2026, question_number="9",
                          knowledge_points=["氧化还原反应"], difficulty=3,
                          content="新题，库里还没有", answer="B"))
    db.commit()

    # 库里 1 条 vs 表里 2 个考点：条数判据（1 >= 2*0.9）也还没到"绿"，所以这里把残留 id 造出来
    service.collection.add(ids=["ghost::kp-0"], embeddings=[[0.1] * 4], metadatas=[{"k": "1"}])
    assert service.collection.count() >= 2, "先确认条数看起来是够的"
    built = service.build_index_from_exam_bank()
    assert "ghost::kp-0" not in service.collection.get()["ids"], "重建没发生，缺题被条数掩盖了"
    assert built == service.collection.count()


def test_a_collection_that_exists_but_cannot_be_opened_does_not_kill_the_import(tmp_path, monkeypatch):
    """开机建实例不能因为"库在但读不动"就把整个应用带崩。

    判存在如果写成 `try get_collection / except: create`，那么一个**存在但 get 会抛**的库
    （本机那个 HNSW 头文件坏掉的就是）会走到 create，撞上 UniqueConstraintError；而这个模块在
    文件末尾建全局实例 —— 报错从"检索降级"升级成"导入即炸"。
    """
    import chromadb

    monkeypatch.setattr(vs.settings, "CHROMA_DB_PATH", str(tmp_path / "chroma"), raising=False)
    seed = chromadb.PersistentClient(
        path=str(tmp_path / "chroma"),
        settings=chromadb.config.Settings(anonymized_telemetry=False))
    seed.create_collection(name="exam_questions", metadata={"hnsw:space": "cosine"})

    real_get = type(seed).get_collection
    monkeypatch.setattr(type(seed), "get_collection",
                        lambda self, **kw: (_ for _ in ()).throw(
                            RuntimeError("Cannot open header file")))
    try:
        svc = vs.VectorSearchService()          # 不许抛
    finally:
        monkeypatch.setattr(type(seed), "get_collection", real_get, raising=False)
    assert svc.collection is None, "读不动的库被当成不存在，于是又去建一个同名库"
    assert svc.search_similar("钠", ["金属性质"], "medium") == [], "降级路径要还能返回结果"


def test_the_model_stamp_survives_reopening_the_library(tmp_path, monkeypatch):
    """重开一次就把戳改掉，是这次修掉的第一个洞；换个进程再验一遍。"""
    monkeypatch.setattr(vs.settings, "CHROMA_DB_PATH", str(tmp_path / "chroma"), raising=False)
    first = vs.VectorSearchService()
    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=6))
    first.index_questions(_ONE_QUESTION, mode="replace")
    stamped = (first.collection.metadata or {}).get("embedding_model")

    monkeypatch.setattr(vs, "EMBEDDING_MODEL", "BAAI/yet-another")
    again = vs.VectorSearchService()
    assert (again.collection.metadata or {}).get("embedding_model") == stamped
    assert "型号戳" in again._stale_reason()


def test_a_failed_encoder_load_is_not_repaid_by_every_request(monkeypatch):
    """加载失败是配置问题，不是抖动；不记住它，每个搜索都要重付一遍几十秒。"""
    boom = RuntimeError("目录不存在")
    monkeypatch.setattr(vs, "_encoder", None)
    monkeypatch.setattr(vs, "_encoder_error", None, raising=False)
    monkeypatch.setattr(vs, "_load_encoder", lambda: (_ for _ in ()).throw(boom))
    with pytest.raises(RuntimeError, match="目录不存在"):
        vs.get_text_encoder()

    calls = []
    monkeypatch.setattr(vs, "_load_encoder", lambda: calls.append(1) or object())
    with pytest.raises(RuntimeError, match="已停止重试"):
        vs.get_text_encoder()
    assert calls == [], "又去加载了一次"


def _seed_probe_vectors(client, dim=8):
    """往 `exam_questions` 里放三条**稠密**向量，够实测判空间，又不至于让判据变成作弊。

    - 稠密：判据里还有一道"是指纹不是编码"，稀疏向量会被它先拒掉，实测那条路就走不到。
    - 一条 a、一条和 a 几乎重合、一条明显更远：真实库里几百条向量，随手抽的那对通常不是
      彼此的近邻，所以实测必须对 query **返回**的候选逐个投票（这台机器的生产库就是被上一版
      "拿第二条比对"的写法误判成非余弦的）。
    - a 与远的那条：余弦距离 ≈ 0.051、平方欧氏 = 1.0，两种式子差得足够远。
    """
    col = client.get_collection("exam_questions")
    a = [1.0] * dim
    near = [0.999 if i == 0 else 1.0 for i in range(dim)]
    far = [1.0] * (dim // 2) + [0.25] * (dim // 2)
    col.add(ids=["a", "near", "far"], embeddings=[a, near, far])
    return col


def test_a_library_measured_in_l2_is_stale_even_with_the_right_stamp(service, monkeypatch):
    """距离函数不核对的话，`similarity = 1 - dist` 就是一句假话 —— 其余判据全过也白过。"""
    import chromadb

    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=8))
    service.index_questions(_ONE_QUESTION, mode="replace")
    assert service._stale_reason() == ""

    client = chromadb.PersistentClient(
        path=str(service.chroma_path),
        settings=chromadb.config.Settings(anonymized_telemetry=False))
    client.delete_collection("exam_questions")
    # 上一版建库就是这样（没写 hnsw:space）：chroma 默认 l2
    client.create_collection(name="exam_questions",
                             metadata={"embedding_model": vs.EMBEDDING_MODEL})
    _seed_probe_vectors(client)
    reopened = vs.VectorSearchService()
    reason = reopened._stale_reason()
    assert "cosine" in reason, f"l2 库被当成可用，报出来的相似度是错的：{reason}"
    assert "实测" in reason, f"没走实测就没法证明这台机器分辨得出 l2：{reason}"


def test_the_space_marker_survives_reopening_the_library(tmp_path, monkeypatch):
    """生产形状：开机时库是**重新打开**的，不是本进程刚建的那个对象。

    只盯着 `_create_collection()` 返回的那个 Collection 实例看 metadata 会漏事：chroma 的
    客户端对象把建库时传的字典原样留在内存里，而重新打开时读回来的是磁盘上那份 —— 这台机器
    的生产库读回来就少了 `hnsw:space`，判据因此每次开机都判它"过期"，一遍 8 分钟的全量重建。
    """
    monkeypatch.setattr(vs.settings, "CHROMA_DB_PATH", str(tmp_path / "chroma"), raising=False)
    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=8))
    first = vs.VectorSearchService()
    first.index_questions(_ONE_QUESTION, mode="replace")
    assert first._stale_reason() == ""

    reopened = vs.VectorSearchService()
    assert reopened._stale_reason() == "", \
        f"重新打开一次就说库不能用，每次开机都要重建：{reopened._stale_reason()}"
    meta = reopened.collection.metadata or {}
    assert (meta.get("space") or meta.get("hnsw:space")) == "cosine", meta


def test_a_cosine_library_that_lost_its_marker_is_measured_not_rebuilt(tmp_path, monkeypatch):
    """标记被别的代码抹掉（metadata 是整份替换的）之后，实测出余弦就不该重建。

    这台机器的 `data/chromadb` 就在这个状态：索引是建库时按余弦配的，但某次带 metadata 的
    打开/改写把 `hnsw:space` 从读回来的字典里抹了。判据要能自己把标记补回去，否则一次抹除
    就换来永久每次重建。
    """
    import chromadb

    monkeypatch.setattr(vs.settings, "CHROMA_DB_PATH", str(tmp_path / "chroma"), raising=False)
    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=8))
    first = vs.VectorSearchService()
    first.index_questions(_ONE_QUESTION, mode="replace")

    client = chromadb.PersistentClient(
        path=str(tmp_path / "chroma"),
        settings=chromadb.config.Settings(anonymized_telemetry=False))
    col = client.get_collection("exam_questions")
    ids = col.get()["ids"]
    col.delete(ids=ids)
    _seed_probe_vectors(client)                        # 空的元数据字典里没有任何空间键
    col.modify(metadata={"embedding_model": vs.EMBEDDING_MODEL})

    reopened = vs.VectorSearchService()
    assert reopened._stale_reason() == "", reopened._stale_reason()
    assert (reopened.collection.metadata or {}).get("space") == "cosine", \
        "实测出余弦却没把标记补回去，下次开机还得再测一遍"


def test_search_degrades_instead_of_500_while_the_library_is_being_replaced(service, monkeypatch):
    """重建在另一个线程里跑，读的一侧不能因此把请求炸掉。"""
    real_get = type(service.collection).get

    def flaky(self, *a, **kw):
        raise RuntimeError("Cannot open header file")
    monkeypatch.setattr(type(service.collection), "get", flaky)
    try:
        assert service.search_similar("钠与水", ["金属性质"], "medium") == []
    finally:
        monkeypatch.setattr(type(service.collection), "get", real_get)


SEARCH_CALLS = ("search_similar", "find_similar_questions")


def test_vector_search_is_never_called_from_the_event_loop():
    """绊线：本地推理挂到事件循环上，一个请求就能把整个服务按住几十秒。

    扫的是 ast，不是文本：判的是"这个调用的直接外层是不是 `asyncio.to_thread(...)`"，所以
    `await asyncio.to_thread(svc.search_similar, ...)` 与 `await asyncio.to_thread(lambda...)`
    分得清，改个空格骗不过它。
    """
    import ast
    import pathlib as _p

    root = _p.Path(__file__).resolve().parents[1]
    offenders = []
    for rel in ("app/api/question.py", "agent/tools/generation.py",
                "app/services/assignment_service.py"):
        path = root / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        wrapped = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "to_thread" and node.args):
                wrapped.add(id(node.args[0]))
        # 只判 async def 里的直接调用：同步函数里的检索调用是合法的（它的调用方负责丢线程，
        # 比如 assignment_service 那几个 helper 整体跑在工作线程里），一刀切会误伤。
        holders: dict = {}
        for fn in (f for f in ast.walk(tree)
                   if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))):
            for inner in ast.walk(fn):
                holders.setdefault(id(inner), fn)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in SEARCH_CALLS and id(node) not in wrapped:
                holder = holders.get(id(node))
                if isinstance(holder, ast.AsyncFunctionDef):
                    offenders.append(f"{rel}:{node.lineno} in {holder.name}")
    assert not offenders, "这些检索调用还在事件循环上跑：" + "; ".join(offenders)


@pytest.mark.asyncio
async def test_a_similar_question_request_does_not_hold_the_loop(monkeypatch):
    """一次搜索 = 一次本地编码。它排在事件循环上时，老师点"生成题目"别人全都得等。"""
    import asyncio
    import time

    from app.api import question as question_api
    from app.services.exam_bank import exam_bank_service

    def _slow(**kw):
        time.sleep(0.3)          # 模拟冷加载/编码：同步 CPU 活
        return []

    monkeypatch.setattr(exam_bank_service, "find_similar_questions", _slow)
    ticks = []

    async def _ticker():
        while True:
            await asyncio.sleep(0.02)
            ticks.append(1)

    task = asyncio.create_task(_ticker())
    req = question_api.SimilarQuestionRequest(knowledge_points=["化学平衡"],
                                              difficulty="medium", limit=3)
    await question_api.find_similar_questions(req)
    during = len(ticks)
    task.cancel()
    assert during >= 3, f"检索期间事件循环只跳了 {during} 下"


_KP_QUESTION = [{
    "exam_id": "q_kp_1", "content": "钠与水反应的离子方程式", "question_type": "选择题",
    "difficulty": "medium", "source": "测试", "year": 2025, "question_number": "1",
    "answer": "A", "knowledge_points": ["金属性质"],
}]


def test_a_degraded_vector_layer_can_say_why(service, monkeypatch):
    """降级要能被问出来（工单 VS1）。

    以前这三处只 `print` 一行就 `return []`：事实只活在服务器 stdout 里。老师在出题页看到
    "这个考点没有相似真题"，与向量库整个读不动，长得一模一样 —— 而前者不用管、后者要人去修。
    """
    assert service.layer_status()["degraded"] is False, service.layer_status()

    def broken_get(self, *a, **kw):
        raise RuntimeError("Cannot open header file")

    monkeypatch.setattr(type(service.collection), "get", broken_get)
    assert service.search_similar("钠与水的反应", ["金属性质"], "medium") == []

    status = service.layer_status()
    assert status["degraded"] is True, status
    assert status["last_failure"]["stage"] == "读索引", status
    assert "Cannot open header file" in status["last_failure"]["reason"], status


def test_a_successful_search_clears_the_ledger(service, monkeypatch):
    """台账说的是"现在"：一次成功检索要抹掉上一次的失败，否则 /health 从此永远报降级。"""
    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=8))
    state = {"broken": True}
    real_get = type(service.collection).get

    def maybe_broken(self, *a, **kw):
        if state["broken"]:
            raise RuntimeError("Cannot open header file")
        return real_get(self, *a, **kw)

    monkeypatch.setattr(type(service.collection), "get", maybe_broken)
    assert service.search_similar("钠", ["金属性质"], "medium") == []
    assert service.layer_status()["degraded"] is True

    state["broken"] = False
    assert service.index_questions(_KP_QUESTION, mode="replace") == 1
    assert service.search_similar("钠与水反应的离子方程式", ["金属性质"], "medium", limit=3)
    assert service.layer_status()["degraded"] is False, service.layer_status()


def test_an_exact_match_scores_one_not_half(service, monkeypatch):
    """`dist == 0`（同一条向量）时报的必须是 1.0。

    旧写法是 `1 - dist if dist else 0.5`：0 是假值，于是最像的那条被记成 0.5，反而排在 0.6
    那批后面 —— 而"完全一致"恰恰出现在拿同一段文本再查一次的时候（按蓝本重生成、老师改考点
    后重查），是这条路最常走的一种输入。
    这条测试自己就是红绿核对过的：把查询向量塞成"接近但不等于"的话，两种写法都给 1.0，
    断言就白写了；所以这里钉住 `distances=[[0.0]]`。
    """
    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=8))
    assert service.index_questions(_KP_QUESTION, mode="replace") == 1

    stored = service.collection.get(include=["metadatas"])
    canned_ids = stored["ids"]
    canned_meta = stored["metadatas"]

    def zero_distance_query(self, *a, **kw):
        return {"ids": [canned_ids], "distances": [[0.0] * len(canned_ids)],
                "metadatas": [canned_meta]}

    monkeypatch.setattr(type(service.collection), "query", zero_distance_query)
    hits = service.search_similar("钠与水反应的离子方程式", ["金属性质"], "medium", limit=3)

    assert len(hits) == 1, hits
    assert hits[0]["kp_name"] == "金属性质", hits
    assert hits[0]["similarity"] == 1.0, f"完全一致的这条报了 {hits[0]['similarity']}"


def test_the_health_endpoint_reports_the_vector_layer():
    """`/health` 原本只说 "healthy"：向量层整个坏了它也照样绿（工单 VS1）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)          # 不进 with：不跑 startup（启动会后台重建索引）
    got = client.get("/health").json()
    assert got["status"] == "healthy"
    vector = got.get("vector")
    assert vector is not None, "/health 没带向量层的事实"
    assert set(vector) >= {"available", "degraded", "embedding_model", "vector_count"}, vector


def test_search_results_carry_the_question_id_not_the_chunk_id(service, monkeypatch):
    """`exam_id` 说的是"题"，chunk id 是库内部的 (题目, 考点) 粒度。

    出题端点拿这个字段去 `exam_bank_service` 查题，而那边的 id 全是 `nat_2020_t4` 这种原值 ——
    以前这里回的是 `nat_2020_t4::kp-3`，于是"命中 5 条、解析出 0 条"，向量算对了也进不了
    提示词。原题 id 放 `exam_id`，chunk id 留在 `chunk_id` 里供追溯。
    """
    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=8))
    assert service.index_questions(_KP_QUESTION, mode="replace") == 1
    hits = service.search_similar("钠与水反应的离子方程式", ["金属性质"], "medium", limit=3)

    assert hits[0]["exam_id"] == "q_kp_1", hits
    assert hits[0]["chunk_id"] == "q_kp_1::kp-0", hits
    assert vs.original_exam_id(hits[0]["chunk_id"]) == hits[0]["exam_id"]


def test_a_read_probe_does_not_write_the_ledger(service, monkeypatch):
    """`/health` 这类探针不该改被探测对象的状态。

    全量重建跑在另一个线程里，探针正好撞上 `delete+create` 那一瞬间读不到条目数，是正常的；
    要是把它记进台账，降级就会粘到下一次成功检索为止 —— 于是这条探针自己把监控做成了假警。
    """
    def boom(self, *a, **kw):
        raise RuntimeError("Cannot open header file")

    monkeypatch.setattr(type(service.collection), "count", boom)
    status = service.layer_status()

    assert status["degraded"] is True and status["available"] is False, status
    assert "Cannot open header file" in status["read_error"], status
    assert status["last_failure"] is None, "探针把一次正常窗口里的读不到写成了这一层的故障"


def test_a_successful_index_clears_the_ledger(service, monkeypatch):
    """写入成功也是"这层在干活"的证据：否则启动期那笔实测记录会一路跟到下一次检索为止。"""
    service.note_failure("启动重建索引", "RuntimeError: 演示用")
    assert service.layer_status()["degraded"] is True

    monkeypatch.setattr(vs, "get_text_encoder", lambda: _FakeEncoder(dim=8))
    assert service.index_questions(_KP_QUESTION, mode="replace") == 1
    assert service.layer_status()["degraded"] is False, service.layer_status()


@pytest.mark.asyncio
async def test_a_failed_startup_rebuild_reaches_the_ledger(monkeypatch):
    """启动重建挂了必须进台账。

    这条是审查里翻出来的最坏形状：`build_index_from_exam_bank` 是"先删库再写"，编码器半路
    抛异常时库里已经没东西了，而 `/health` 那边 collection 仍然存在、`available` 仍是 true ——
    以前这件事只留一行 `Vector index build skipped` 在 stdout。
    """
    import app.main as main_mod
    from app.services.vector_search import vector_search_service as svc

    def boom(*a, **kw):
        raise RuntimeError("模型没下下来")

    monkeypatch.setattr(vs, "get_text_encoder", lambda: object())
    monkeypatch.setattr(svc, "build_index_from_exam_bank", boom)
    monkeypatch.setattr(svc, "last_failure", None)

    await main_mod.build_vector_index_in_background()      # 不许抛

    status = svc.layer_status()
    assert status["degraded"] is True, status
    assert status["last_failure"]["stage"] == "启动重建索引", status
    assert "模型没下下来" in status["last_failure"]["reason"], status
