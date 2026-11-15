"""DifficultyColumn 写侧兜底回归（E2E E7 实测）。

历史写入方曾把枚举名/标签字符串原样存进难度列（SQLite 不校验类型），
组卷复制成批量脏数据后 smart/start 的 int(difficulty) 直接 500。
列类型改为 TypeDecorator 后，任何写入方（导入/建题/组卷复制）落库即归一，
读侧保证拿到 int —— 用内存库锁该契约。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import Base, Difficulty, Question


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


@pytest.mark.parametrize("dirty,expected", [
    ("MEDIUM", 3),          # 枚举名（E7 的 500 元凶）
    ("COMPETITION", 5),     # 枚举名
    ("medium", 3),          # 旧标签
    ("hard", 4),            # 旧标签
    ("4", 4),               # 数字字符串
    (2, 2),                 # int 原样
    (Difficulty.EASY, 1),   # 枚举成员
])
def test_difficulty_column_coerces_on_write(db, dirty, expected):
    db.add(Question(question_id=f"q_{dirty!r}", content="题",
                    knowledge_points=["kp"], answer="A", difficulty=dirty))
    db.commit()
    q = db.query(Question).filter(
        Question.question_id == f"q_{dirty!r}").first()
    assert q.difficulty == expected
    assert isinstance(q.difficulty, int)
