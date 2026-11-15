"""把生产数据复制成一份影子，并给出指过去要设的环境变量。

存在理由：跑测试与跑评估都会写 `chemai.db`（被 git 跟踪的演示数据）和 `data/chromadb`
（它的向量索引）。`pytest` 这条路有根 `conftest.py` 兜着，但评估里 `kind="script"` 与
`kind="function"` 两类条目根本不经过 conftest —— 少这一处，"跑测试不写生产件"就是半句真话
（ADR-0026）。所以两边共用这里的口径。

指过去靠的是两个环境变量：`DATABASE_URL`（`app/models/database.py` 认显式环境变量覆盖）与
`CHROMA_DB_PATH`（`app/core/config.py` 的 pydantic 字段，无 env_prefix）。两条都在 **import 期**
被读走，所以调用点必须排在任何 app 导入之前。
"""
import atexit
import os
import shutil
import sqlite3
import tempfile
import time
from typing import Dict, Optional, Tuple

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENV_KEYS = ("DATABASE_URL", "CHROMA_DB_PATH", "CHECKPOINT_DB_PATH", "STORE_DB_PATH",
            "MEMORY_DATA_DIR")
HOME_PREFIX = "chemai-shadow-"   # 测试靠这个前缀认出"我现在在影子上跑"

def _chromadb_source(root: str) -> str:
    """生产向量库的真实位置。

    2026-10-31 起默认在 `C:/chemai-chromadb`（.env 的 CHROMA_DB_PATH）：chroma-hnswlib 的
    C++ ofstream 在含中文的路径（D:\\化学\\...）上写不出 hnsw 段文件（pickle/sqlite 正常、
    bin 永远缺失），所以向量库搬去了纯 ASCII 路径。activate() 跑在任何 app 导入之前，
    pydantic-settings 还没读 .env，这里只能自己解析。
    """
    explicit = os.environ.get("CHROMA_DB_PATH")
    if explicit:
        return explicit
    env_file = os.path.join(root, ".env")
    if os.path.isfile(env_file):
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("CHROMA_DB_PATH="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return os.path.join(root, "data", "chromadb")


# (源路径, 影子里的名字, 是不是目录) —— 三个新键的存在理由和头两个一样：`agent/channel/conversation.py`
# 里有 DELETE `chemai_checkpoints.db` 的语句，同机并发跑测试会互踩真实对话数据；
# `memory_data/` 更是**被 git 跟踪的**，仓库里已经躺着 students/test_001/profile.md 这类测试残骸。
_COPIES = (
    ("chemai.db", "chemai.db", False),
    ("__chromadb__", "chromadb", True),   # 占位：create() 里经 _chromadb_source 解析
    ("chemai_checkpoints.db", "chemai_checkpoints.db", False),
    ("chemai_store.db", "chemai_store.db", False),
    (os.path.join("chem_skills", "chemistry_memory", "memory_data"), "memory_data", True),
)


def _copy_db_snapshot(src: str, dst: str) -> str:
    """活库的一致性快照：SQLite backup API 不受并发写/检查点影响。

    旧的「先 WAL 边车再主文件」文件复制在库静止时成立，但服务进程在写时
    会拿到盐值不匹配的配对 → SQLite 丢弃 WAL → 影子回落到陈旧快照甚至
    缺表（evals fast gate 15 连挂的根因，2026-10-04 勘误）。"""
    src_con = sqlite3.connect(f"file:{src.replace(os.sep, '/')}?mode=ro", uri=True)
    try:
        dst_con = sqlite3.connect(dst)
        try:
            with dst_con:
                src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()
    # 快照自包含：清掉可能被旧逻辑残留的边车，防盐值错配
    for suffix in ("-shm", "-wal"):
        sidecar = dst + suffix
        if os.path.isfile(sidecar):
            os.remove(sidecar)
    return dst


def _copy_into(src: str, dst: str) -> str:
    """把生产件复制成 dst；源不存在时原样返回 dst（让调用方自己建）。"""
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    elif os.path.isfile(src):
        if src.endswith(".db"):
            _copy_db_snapshot(src, dst)
        else:
            shutil.copyfile(src, dst)
    return dst


def _sweep_stale_homes(max_age_hours: float = 24.0) -> None:
    """清掉上次运行遗留的孤儿影子（进程被强杀时 atexit 没跑成的那批，几天能堆十几个 G）。

    只认 Temp 里 HOME_PREFIX 前缀且目录 mtime 超过 24h 的：正在跑的评估一直
    在写自己的影子（WAL 边车建了删、删了建），mtime 新，碰不到；就算真有
    顽固占用，rmtree(ignore_errors) 也只是跳过，绝不挡 create() 的正事。
    挂在 create() 开头 —— 影子只在跑测试/评估时生成，清扫频率自然跟着堆积频率走。
    """
    temp = tempfile.gettempdir()
    try:
        names = os.listdir(temp)
    except OSError:
        return
    deadline = time.time() - max_age_hours * 3600
    for name in names:
        if not name.startswith(HOME_PREFIX):
            continue
        home = os.path.join(temp, name)
        try:
            if os.path.getmtime(home) < deadline:
                shutil.rmtree(home, ignore_errors=True)
        except OSError:
            pass


def create(root_dir: Optional[str] = None) -> Tuple[str, Dict[str, str]]:
    """建一份影子，返回 (影子目录, 要设的环境变量)。"""
    _sweep_stale_homes()
    root = root_dir or BACKEND_ROOT
    home = tempfile.mkdtemp(prefix=HOME_PREFIX)
    env: Dict[str, str] = {}
    for i, (src_name, dst_name, is_dir) in enumerate(_COPIES):
        src = _chromadb_source(root) if src_name == "__chromadb__" else os.path.join(root, src_name)
        dst = _copy_into(src, os.path.join(home, dst_name))
        if i == 0:
            env["DATABASE_URL"] = "sqlite:///" + dst.replace("\\", "/")
        else:
            env[ENV_KEYS[i]] = dst
    return home, env


def activate(environ: Optional[Dict[str, str]] = None) -> str:
    """把影子路径设进环境（默认 `os.environ`），返回影子目录。

    用 `setdefault`：谁从外面明确给了库路径（专门跑某个影子库的脚本、CI 里的服务库），
    就以他给的为准 —— 影子是"没人指定时别写生产件"的兜底，不是抢控制权。
    """
    target = os.environ if environ is None else environ
    if all(target.get(k) for k in ENV_KEYS):
        # 已经指好了，不必再复制这几十 MB。但必须回报**现有影子目录**而不是空串：
        # 子进程（evals 跑分器 → pytest conftest）继承环境后再次 activate，
        # 拿到 "" 会把引擎钉到 /chemai.db（文件系统根的空库）→ 全体 pytest 条目
        # "no such table"（evals fast gate 15 连挂的第二个根因，2026-10-04 勘误）。
        db = (target.get("DATABASE_URL") or "").replace("sqlite:///", "")
        return db.rsplit("/", 1)[0] if db else ""
    home, env = create()
    for key, value in env.items():
        target.setdefault(key, value)
    atexit.register(shutil.rmtree, home, ignore_errors=True)
    return home
