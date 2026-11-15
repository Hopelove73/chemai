"""影子数据基座的钉子（ADR-0026）。

`conftest.py` 已经把 pytest 这条路指到副本上了，这里要钉的是**剩下的那条**：评估里
`kind="script"` 与 `kind="function"` 两类条目不经过 pytest，读不到 conftest。少这一步，
"跑测试不写生产件"就只剩半句真话 —— 而 `evals/run_evals.py --fast` 正是 CI 每天跑的那条。
"""
import os
import sqlite3
from pathlib import Path

from app import shadow_data

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _real_db(path) -> None:
    """建一个真 SQLite 库：.db 复制走 backup API（b16394c），假字节文件会
    以 "file is not a database" 爆掉。"""
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE t (v TEXT)")
        con.execute("INSERT INTO t VALUES ('v1')")
        con.commit()
    finally:
        con.close()


def _activation_is_first(path: Path) -> bool:
    """`app.shadow_data` 的导入有没有排在 evals/app 那些模块之前。

    顺序错了不会当场报错，它只是让评估继续写生产库 —— 所以这条只能是结构判据。
    """
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    def index_of(name):
        return next((i for i, line in enumerate(lines)
                     if line.startswith(f"from {name}") or line.startswith(f"import {name}")),
                    None)

    at = index_of("app.shadow_data")
    if at is None:
        return False
    return all(index_of(n) is not None and index_of(n) > at
               for n in ("evals.runner", "evals.layers"))


def test_activate_points_both_paths_at_a_copy(tmp_path):
    """没指定过库时，两条路径都指进临时副本，且副本里真的能读到东西。"""
    src_root = tmp_path / "repo"
    (src_root / "data" / "chromadb").mkdir(parents=True)
    _real_db(src_root / "chemai.db")
    (src_root / "data" / "chromadb" / "chroma.sqlite3").write_bytes(b"vectors")

    home, env = shadow_data.create(str(src_root))

    assert env["DATABASE_URL"].startswith("sqlite:///")
    assert "chemai.db" in env["DATABASE_URL"]
    assert env["CHROMA_DB_PATH"].replace("\\", "/").endswith("chromadb")
    assert os.path.isfile(os.path.join(home, "chemai.db"))
    assert os.path.isfile(os.path.join(home, "chromadb", "chroma.sqlite3"))


def test_every_local_artifact_gets_a_shadow(tmp_path):
    """五件本机文件都要有影子，少一件就是"某些测试仍在写这台机器的真库"。

    `chemai_checkpoints.db` 里是真实对话的 checkpoint，`agent/channel/conversation.py` 里还有
    删它的语句；`memory_data/` 更是被 git 跟踪的目录（仓库里已经躺着 `students/test_001/
    profile.md` 这类测试残骸）。这两个以前都是各处 `__file__` 拼出来的，没法整体接管。
    """
    root = tmp_path / "repo"
    (root / "data" / "chromadb").mkdir(parents=True)
    (root / "chem_skills" / "chemistry_memory" / "memory_data" / "students").mkdir(parents=True)
    for f in ("chemai.db", "chemai_checkpoints.db", "chemai_store.db"):
        _real_db(root / f)
    (root / "data" / "chromadb" / "chroma.sqlite3").write_bytes(b"x")
    (root / "chem_skills" / "chemistry_memory" / "memory_data" / "students" / "s1.md").write_text("x")

    home, env = shadow_data.create(str(root))

    assert set(env) == set(shadow_data.ENV_KEYS), env
    for key in shadow_data.ENV_KEYS[1:]:
        where = env[key].replace("\\", "/")
        assert where.startswith(home.replace("\\", "/")), (key, where)
    assert os.path.isfile(env["CHECKPOINT_DB_PATH"]), "checkpoint 库没复制过去"
    assert os.path.isfile(env["STORE_DB_PATH"]), "store 库没复制过去"
    assert os.path.isfile(os.path.join(env["MEMORY_DATA_DIR"], "students", "s1.md")), env["MEMORY_DATA_DIR"]


def test_no_site_recomputes_those_paths():
    """绊线：本机库的路径只能有一个定义处（`app/core/config.py`）。

    判据空转比不判更糟，所以正向对照在同一条里：配置那个文件必须仍然含这三个名字。
    """
    # 例外只有两处：定义处的 `app/core/config.py`，和把源文件名当复制清单的
    # `app/shadow_data.py`（它必须知道原件叫什么）。别的文件再拼一遍就是第二个真源。
    allow = {"config.py", "shadow_data.py"}
    hits = []
    for base in ("app", "agent", "chem_skills", "evals"):
        for path in (BACKEND_ROOT / base).rglob("*.py"):
            if path.name in allow:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for literal in ('"chemai_checkpoints.db"', '"chemai_store.db"',
                            'parent / "memory_data"'):
                if literal in text:
                    hits.append(f"{path.relative_to(BACKEND_ROOT).as_posix()}:{literal}")
    assert not hits, f"这些文件又把本机库路径自己拼了一遍：{hits}"

    cfg = (BACKEND_ROOT / "app" / "core" / "config.py").read_text(encoding="utf-8")
    for name in ("CHECKPOINT_DB_PATH", "STORE_DB_PATH", "MEMORY_DATA_DIR"):
        assert name in cfg, f"定义处少了 {name}，这条绊线就成了空判据"


def test_the_producer_files_are_untouched_by_a_copy_then_a_write(tmp_path):
    """影子存在的唯一目的：往副本写东西，原件一个字节都不动。"""
    src_root = tmp_path / "repo"
    (src_root / "data" / "chromadb").mkdir(parents=True)
    _real_db(src_root / "chemai.db")
    original = (src_root / "chemai.db").read_bytes()
    (src_root / "data" / "chromadb" / "chroma.sqlite3").write_bytes(b"VECTORS")

    home, env = shadow_data.create(str(src_root))
    shadow_db = env["DATABASE_URL"].replace("sqlite:///", "")
    con = sqlite3.connect(shadow_db)
    try:
        con.execute("INSERT INTO t VALUES ('written by a test')")
        con.commit()
    finally:
        con.close()

    assert (src_root / "chemai.db").read_bytes() == original
    assert (src_root / "data" / "chromadb" / "chroma.sqlite3").read_bytes() == b"VECTORS"
    assert home != str(src_root)


def test_an_explicitly_given_database_is_left_alone(monkeypatch):
    """外面明确给了路径就不复制，且回报**现有影子目录**（不新建、不抢控制权）。

    返回值曾是空串——但 evals 跑分器 → pytest 子进程继承环境后再次 activate 时，
    拿到空串会把引擎钉到 /chemai.db 空库（fast gate 连挂根因，2026-10-04 勘误），
    所以现在必须回报现有 DATABASE_URL 的目录。"""
    for key in shadow_data.ENV_KEYS:
        monkeypatch.setenv(key, "/somewhere/already/decided")

    assert shadow_data.activate() == "/somewhere/already"
    assert os.environ["DATABASE_URL"] == "/somewhere/already/decided"


def test_wal_only_rows_land_in_the_selfcontained_snapshot(tmp_path):
    """快照经 SQLite backup API：只落在 WAL 里的行也进影子，且影子自包含无边车。

    旧的「先边车后主文件」文件复制在源库静止时成立，但生产进程边写边复制会给出
    盐值错配的配对 → SQLite 丢弃 WAL → 影子回落陈旧快照（fast gate 15 连挂根因，
    2026-10-04 勘误，改 backup API）。"""
    src_root = tmp_path / "repo"
    src_root.mkdir()
    src = src_root / "chemai.db"
    con = sqlite3.connect(src)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE t (v TEXT)")
        con.execute("INSERT INTO t VALUES ('in-wal')")
        con.commit()
        # 连接保持打开：关掉最后一个连接会 checkpoint 并删 -wal，前置就不成立了
        assert (src_root / "chemai.db-wal").is_file(), "前置失效：行没留在 WAL 里"

        home, env = shadow_data.create(str(src_root))
    finally:
        con.close()

    shadow_db = env["DATABASE_URL"].replace("sqlite:///", "")
    con = sqlite3.connect(shadow_db)
    try:
        (v,) = con.execute("SELECT v FROM t LIMIT 1").fetchone()
    finally:
        con.close()
    assert v == "in-wal"
    assert not os.path.isfile(shadow_db + "-wal"), "影子应自包含，不带 WAL 边车"
    assert not os.path.isfile(shadow_db + "-shm")
    assert home != str(src_root)


def test_the_running_session_is_actually_on_the_shadow():
    """这一场 pytest 确实在副本上跑：engine 与 settings 两条路径都指进影子目录。

    这条不是复述 `ENV_KEYS` —— 它读的是 app 真正用来连库的那两个值。哪天 `DATABASE_URL` 改了
    名、或者 `app/core/config.py` 不再认环境变量，影子的生效性就当场变红，而不是继续安安静静
    写生产库。
    """
    from app.core.config import settings
    from app.models.database import _DEFAULT_DB_URL

    where_db = _DEFAULT_DB_URL.replace("\\", "/")
    assert shadow_data.HOME_PREFIX in where_db, where_db
    where_chroma = str(settings.CHROMA_DB_PATH).replace("\\", "/")
    assert shadow_data.HOME_PREFIX in where_chroma, where_chroma


def test_the_eval_gate_activates_the_shadow_before_importing_anything_else():
    assert _activation_is_first(BACKEND_ROOT / "evals" / "run_evals.py"), \
        "评估正门不再在导入 evals/app 之前立影子了（ADR-0026）"


def test_a_reordered_gate_would_be_caught(tmp_path):
    """正向对照：顺序倒过来、或者根本没接影子时，判据必须说不成立。"""
    bad = tmp_path / "run_evals.py"
    bad.write_text("from evals.runner import run_group\n"
                   "from app.shadow_data import activate\n"
                   "activate()\n", encoding="utf-8")
    assert not _activation_is_first(bad)

    missing = tmp_path / "run_evals2.py"
    missing.write_text("from evals.runner import run_group\n", encoding="utf-8")
    assert not _activation_is_first(missing)
