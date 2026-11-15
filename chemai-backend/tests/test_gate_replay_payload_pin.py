"""重放载荷指纹（gate-lifecycle 2.6）：老师批的是哪一份，就只能执行哪一份。

**它挡的是什么。** 确认卡上的内容来自门那一行；老师点「批准」之后真正执行的却是
内存里的 `pending["fn"](**pending["kwargs"])`。这两者在四条开门路径上今天是一致的
（`guard.py` 草稿门 :460 / 按次审批 :480 / 布尔门 :396，`langgraph_channel.py` 打回重开
:1675+1721），但**一致性此前没有任何东西守着** —— 门行被人改一笔、或重放载荷换了
一个来源，老师批的和他拿到的东西就悄悄不是一回事了，而且没有任何迹象。

开门时把载荷指纹钉进那一行，重放前重新对一次。对不上就**不执行**（fail-closed）。

存量门（本列上线前的行）两个字段都是 NULL —— 按 `plan_step` 同一套口径，
NULL 是「未记录」这个有效语义，回退旧行为放行；**不为它回填**，回填就是拿推出来的
值冒充记录。
"""
from app.models.database import AgentGate
from app.services import work_unit as wu
from app.services.gate_service import REPLAY_PAYLOAD_SCHEMA, gate_service


def _open(db, tool="assign_adaptive_practice", kwargs=None, conv="conv-pin"):
    with wu.work_unit(db):
        return gate_service.open_gate(
            db, conv, kind="boolean", tool_name=tool,
            kwargs=kwargs if kwargs is not None else {"student_id": "241914"})


def _row(db, gid):
    return db.query(AgentGate).filter_by(gate_id=gid).one()


# ── 写侧：开门即钉 ──────────────────────────────────────────────

def test_open_gate_pins_the_payload(db_session):
    """开门那一刻就把载荷钉死 —— 没有指纹的重放校验是没法校验的。"""
    gid = _open(db_session)
    row = _row(db_session, gid)
    assert row.args_hash, "开门没落载荷指纹"
    assert row.schema_version == REPLAY_PAYLOAD_SCHEMA


# ── 认读侧：对得上才放行 ─────────────────────────────────────────

def test_payload_that_still_matches_is_let_through(db_session):
    """反向钉子：正常门必须放行 —— 校验写严了会把整条链路拦死。"""
    gid = _open(db_session)
    bad = gate_service.verify_replay_payload(
        db_session, gid, "assign_adaptive_practice", {"student_id": "241914"})
    assert bad is None, f"正常载荷被拒了：{bad}"


def test_tampered_row_is_refused(db_session):
    """门行的载荷被改动 ⇒ 拒绝执行，而不是照着改过的内容跑。

    这正是"老师批的是 A、执行的却是 B"的形态：卡上写的是一个学生的练习，
    库里被改成了另一个学生 —— 指纹对不上，停。
    """
    gid = _open(db_session)
    row = _row(db_session, gid)
    row.kwargs = {"student_id": "999999"}          # 批准之后被改
    db_session.flush()
    bad = gate_service.verify_replay_payload(
        db_session, gid, "assign_adaptive_practice", {"student_id": "241914"})
    assert bad, "载荷被改过却照跑"


def test_execution_payload_diverging_from_the_pin_is_refused(db_session):
    """门行没动，但**即将执行**的那一份不是批准的那一份 → 拦。

    今天最要紧的一道：重放跑的是内存里的 pending，不是门那行
    （`_direct_replay_events`：`pending["fn"](**pending["kwargs"])`）。
    只查门行自身的完整性管不到它 —— 内存一旦与门行漂开，老师批的和真正
    执行的就是两回事，而且完全没有迹象。上一条用例改的是门行，这条改的是
    调用方要执行的值，两条走的是不同分支。
    """
    gid = _open(db_session)
    bad = gate_service.verify_replay_payload(
        db_session, gid, "assign_adaptive_practice", {"student_id": "999999"})
    assert bad, "要执行的换了个人却照跑"


def test_tool_name_swap_is_refused(db_session):
    """换成另一个工具也要拦住 —— 指纹覆盖工具名，不只是参数。"""
    gid = _open(db_session)
    bad = gate_service.verify_replay_payload(
        db_session, gid, "delete_bank", {"student_id": "241914"})
    assert bad, "工具名换了却照跑"


def test_legacy_row_without_a_pin_is_let_through(db_session):
    """存量门（指纹列为 NULL）回退旧行为。

    不回填的理由和 `plan_step` 一样：NULL 是「未记录」。要是把 NULL 当"不符"
    拦下，存量那些开着的门当场全部作废 —— 老师手里的卡点下去只会得到一句失败。
    """
    gid = _open(db_session)
    row = _row(db_session, gid)
    row.args_hash = None
    row.schema_version = None
    db_session.flush()
    bad = gate_service.verify_replay_payload(
        db_session, gid, "assign_adaptive_practice", {"student_id": "241914"})
    assert bad is None, f"存量门被拦了：{bad}"


def test_unknown_schema_version_is_refused(db_session):
    """载荷格式变了（指纹算法/结构升级）→ 拒绝，不拿新代码去解旧载荷。"""
    gid = _open(db_session)
    row = _row(db_session, gid)
    row.schema_version = REPLAY_PAYLOAD_SCHEMA + 1
    db_session.flush()
    bad = gate_service.verify_replay_payload(
        db_session, gid, "assign_adaptive_practice", {"student_id": "241914"})
    assert bad, "版本不认识却照跑"
