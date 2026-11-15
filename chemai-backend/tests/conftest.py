"""pytest fixtures for ChemAI tests."""
# test_smart_parse.py 是脚本式冒烟测试（依赖 OCR/MinerU 服务），模块顶层直接
# 执行 async 调用，pytest 收集会报错。从 pytest 收集中排除，保留为手动脚本。
collect_ignore = ["test_smart_parse.py"]
import sys, os, pytest

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.database import Base
from app.models.ocr_task import OCRTask  # ensure table in metadata


@pytest.fixture
def fake_deepseek_key(monkeypatch):
    """给 `get_langchain_model` 一把假 key：真建 `ChatOpenAI` 的用例不能指望开发机的 .env。

    `provider_keys` 是 `app.config._Config` 上的 property，往单例实例上塞值会被描述符协议
    拒掉，所以换的是类属性 —— monkeypatch 用完原样装回。
    """
    from app.config import config as _cfg
    monkeypatch.setattr(type(_cfg), "provider_keys",
                        property(lambda self: {"deepseek": "sk-test-fake"}), raising=False)
    return "***"


@pytest.fixture(scope="function")
def db_session():
    """In-memory SQLite database, created fresh per test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _restore_exam_bank_singleton():
    """用例结束后把 exam_bank_service 的内存真题库还原。

    question_bank 的保存路径（_sync_search_indexes）会把测试题 upsert 进这个进程级
    单例，还会写进影子库的 historical_exams。不还原的话，后续任何起 TestClient 的
    用例在启动钩子里发现影子库比「索引应有的 id 集」多出这几条，就会触发全量重编码
    （本机实测 18 分钟 CPU），TestClient 关闭时 join 编码线程直接把套件挂死
    （test_kp_mastery_endpoint_carries_bkt_fields 卡在 48% 的根因）。
    浅拷贝即可：用例只增删列表项，不改已有 HistoricalQuestion 对象。
    """
    from app.services.exam_bank import exam_bank_service

    saved = list(exam_bank_service.questions)
    yield
    exam_bank_service.questions[:] = saved


@pytest.fixture
def patch_get_db(db_session, monkeypatch):
    """把 `app.models.database.get_db` 指到本测试的内存 DB，返回该 session。

    生产代码里有大量 `next(get_db())` 的进程内取会话点（service 直调、不经 HTTP 依赖
    注入），测试要覆盖它们只能替换这个工厂。用 `monkeypatch.setattr` 而非裸赋值：
    补丁随测试自动还原。做成生成器是为了匹配 `get_db` 的依赖签名——`next()` 才拿到
    session，与 FastAPI 那边的用法一致。"""
    from app.models import database as db_mod

    def _fake_get_db():
        yield db_session

    monkeypatch.setattr(db_mod, "get_db", _fake_get_db)
    return db_session


@pytest.fixture
def unit():
    """在指定会话上开一个工作单元 —— 测试里调 service 写入口时用它包一层。

    `open_gate` / `create_draft` / `send_draft` 这些写入口只 flush 不提交，检测不到
    活动单元就抛 `NoWorkUnit`（前置一）。用法：`with unit(db_session): ...`。
    驱动 channel/工具那类自己会开单元的用例不要用它套在外层 —— 单元不许嵌套。
    """
    from app.services.work_unit import work_unit
    return work_unit


@pytest.fixture
def sample_student(db_session):
    """Create a sample student in the test DB."""
    from app.models.database import Student
    s = Student(
        student_id="202610101", name="test_student", class_id="class_2025_1",
        status="approved",
        barrier_type={"concept": 0.3, "reading": 0.4, "expression": 0.3},
    )
    db_session.add(s)
    db_session.commit()
    return s


@pytest.fixture
def sample_teacher(db_session):
    """Create a sample teacher account."""
    from app.models.database import Account
    from app.utils.init_db import hash_password
    a = Account(
        account_id="acc_test_teacher",
        username="test_teacher",
        password_hash=hash_password("123456"),
        role="teacher",
        teacher_id="teacher_test",
        status="active",
    )
    db_session.add(a)
    db_session.commit()
    return a
