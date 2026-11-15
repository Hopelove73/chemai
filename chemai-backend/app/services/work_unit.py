"""单一工作单元 —— 一次决定 = 一次提交（计划「前置一」/ eng 二轮 R7 D8=A、R1 D2=A）。

改造前是"谁写库谁自己 commit"：一次决定要改门状态、写决定日志、动草稿行，
各处各提交一次。中间任何一次失败，前面已经提交的部分回不去 —— 于是出现"账上
记了批准、门上还挂着待确认"这种半截状态，老师再点一次就是把危险动作执行第二遍。

这里把提交权收上来：**service 的写方法不再自己提交，只 flush；提交发生在最外层
调用方那个单元的出口，整条决定一共一次。** 配套两条硬约束，都由运行时断言承载，
不靠点名调用方：

1. **写入口只认活动单元**（R7/D8=A）。检测不到单元就 `raise`，绝不"没人管我就自己
   提交"。理由：漏包一个调用点如果还能写进去，故障形态是"页面看着成功、数据其实
   没落"的静默丢写；当场抛错只是 500 或工具错误文案，测试第一次跑就能发现。
   同一条判据还管事务：单元进入时那次 `commit()`（或 `rollback()`）会把单元切成
   两笔提交 —— 前半段已经落库、出口那笔救不回来。所以 `require_unit` 另外比对
   "还是不是进入时那一个事务"，被中途提走就地抛（`WorkUnitCommittedEarly`）。
   仓里另有 40+ 个仍自行提交的 service，指望"每条新通路都记得补 commit 计数断言"
   不如让漏包当场响。
2. **单元内禁止模型调用**（R1/D2=A）。SQLite WAL 同一时刻只有一个写事务，而本仓
   引擎没设 `busy_timeout`（`app/models/database.py:612` 的 `connect_args` 只有
   `check_same_thread`，python sqlite3 默认 5 秒）。单元圈进一次数秒级的模型往返，
   就等于让全库写者排队 5 秒后抛 `database is locked`。所以需要模型输出的写入一律
   拆成"单元外算完 → 单元内只写"。

会话与线程的边界：活动单元记在 `ContextVar` 里。`asyncio.Task` 创建时复制上下文，
所以单元往下（工具子任务）看得见；往回看不见 —— 因此单元必须开在**最外层**调用方，
子任务里再开一个会被 `work_unit` 判成嵌套而抛错，不会悄悄变成两个事务。
"""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from sqlalchemy.orm import Session

__all__ = [
    "NoWorkUnit", "WorkUnitAlreadyOpen", "ModelCallInsideWorkUnit",
    "WorkUnitCommittedEarly",
    "work_unit", "new_work_unit", "session_scope",
    "active_unit", "in_work_unit", "require_unit", "after_commit",
]


class NoWorkUnit(RuntimeError):
    """写入口在没有活动单元时被调用（通路漏包）。"""


class WorkUnitAlreadyOpen(RuntimeError):
    """单元嵌套：外层还在提交，内层又开一个。"""


class ModelCallInsideWorkUnit(RuntimeError):
    """活动单元内调用模型 —— 会把 WAL 写锁 held 在一次数秒级网络往返上。"""


class WorkUnitCommittedEarly(RuntimeError):
    """单元跑到一半，事务已经被别人 commit()/rollback() 走了（等于这次决定分了两笔）。"""


_active: ContextVar[Optional[Session]] = ContextVar("chemai_work_unit_db", default=None)
_hooks: ContextVar[Optional[list]] = ContextVar("chemai_work_unit_hooks", default=None)
_unit_tx: ContextVar[object] = ContextVar("chemai_work_unit_tx", default=None)


def after_commit(fn) -> bool:
    """注册一个"提交成功之后再做"的动作；没有活动单元时立即执行。

    给不落库的副作用用（内存缓存这类）。它们必须排在 `commit()` 后面：先写缓存再提交，
    一旦整单回滚，读缓存的接口就把一份根本没落库的计划当事实返回给学生。
    """
    hooks = _hooks.get()
    if hooks is None:
        fn()
        return True
    hooks.append(fn)
    return False


def active_unit() -> Optional[Session]:
    """当前活动单元的会话；None = 没有人在管提交。"""
    return _active.get()


def in_work_unit() -> bool:
    return _active.get() is not None


@contextmanager
def work_unit(db: Session) -> Iterator[Session]:
    """把已经拿到的会话纳入一个工作单元：出口提交一次，异常整体回滚。

    不关会话 —— 会话的生命周期归调用方（`new_work_unit` / FastAPI 的
    `Depends(get_db)` / channel 的 `_entity_db`）。嵌套一律抛错：外层还没走到出口，
    内层提交就是把外层的半成品落库，回滚也救不回来。
    """
    if _active.get() is not None:
        raise WorkUnitAlreadyOpen(
            "工作单元不可嵌套：外层单元尚未提交，内层再开一个会把半成品落库")
    token = _active.set(db)
    hooks_token = _hooks.set([])
    tx_token = _unit_tx.set(None)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    else:
        # 副作用排在提交之后：回滚过的单元里注册的动作一个都不许发生
        for hook in _hooks.get() or []:
            hook()
    finally:
        _unit_tx.reset(tx_token)
        _hooks.reset(hooks_token)
        _active.reset(token)


@contextmanager
def _session() -> Iterator[Session]:
    """取一条会话，退出即关。

    延迟绑定 `app.models.database.get_db`：测试换库靠的就是替换这个属性，
    用 import 进来的名字会绕过它（与 `agent/channel/langgraph_channel.py:144`
    的 `_entity_db` 同一契约）。
    """
    from app.models import database as _db_mod
    db = next(_db_mod.get_db())
    try:
        yield db
    finally:
        db.close()


@contextmanager
def new_work_unit() -> Iterator[Session]:
    """自己取会话 + 开单元：给"这一次写入就是全部"的通路用（工具、门登记、作业）。

    需要"先算后写"的通路不要用这个：那样模型调用会落进单元里。改成
    `with session_scope() as db:` 取会话，算完之后 `with work_unit(db):` 只包写。
    """
    with _session() as db:
        with work_unit(db) as unit:
            yield unit


@contextmanager
def session_scope() -> Iterator[Session]:
    """取一条会话、退出即关，**不负责提交**。

    给"单元外先算"那半程用：读学生、读上下文、调模型都不需要事务；
    要写的时候在同一条会话上再开 `work_unit(db)`，不必换会话。
    """
    with _session() as db:
        yield db


def require_unit(db: Optional[Session] = None) -> Session:
    """写入口的进门闸：没有活动单元就抛，绝不自行提交（D8=A）。

    传 `db` 时同时校验身份：单元管的是**这一条**会话，拿另一条会话来写就等于写进
    一个没人提交的地方 —— 那种丢写没有任何症状，所以必须是硬失败而不是"算了照着写"。
    """
    active = _active.get()
    if active is None:
        raise NoWorkUnit(
            "写入口必须在最外层调用方的工作单元里调用（前置一）："
            "请包 `with work_unit(db):` 或 `with new_work_unit() as db:` 后再调")
    if db is not None and db is not active:
        raise NoWorkUnit(
            "写入口拿到的会话与活动工作单元不是同一条：这次写入不会被任何提交带走")
    if db is not None:
        tx = _unit_tx.get()
        if tx is None:
            # 第一次进写入口时把"这一次决定的事务"定下来：会话已经在事务里
            # （端点先用它读过东西，那次读属于同一次决定）就沿用，否则由单元发起。
            _unit_tx.set(db.get_transaction() or db.begin())
        elif db.get_transaction() is not tx:
            # commit()/rollback() 之后 get_transaction() 变 None，再写会自动开一笔新的
            # —— 两种都意味着单元已经被切成两笔提交，出口那次救不回前半段。
            raise WorkUnitCommittedEarly(
                "活动工作单元的事务在中途被提交了：这次决定实际落了两笔。"
                "单元内不得调用任何自行 commit 的 service（前置一 / ADR-0021）")
    return active
