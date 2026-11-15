"""D17/R14 的共用判据：哪些 `open` 门行算数（T4 纯读 + T8 列表同一份定义）。

这批断言的全部价值在于「反例」：同 `draft_id` 的 v1 待确认 + v2 已发送。
按 `draft_id IN (…status='awaiting_confirmation')` 的省事写法，这一组判真，
假门照旧进列表、进角标；按 `MAX(version)` 那一行的状态判，才判对。
"""
import uuid

import pytest

from app.models.database import AgentGate, LearningPlanDraft
from app.services.gate_service import gate_service
from app.services.work_unit import work_unit


@pytest.fixture(autouse=True)
def _unit(db_session):
    """这一档断言只看判据形状，写入口的单元在这里统一开。

    "单元外必抛""一次决定一次提交"归 `tests/test_gate_unit_of_work.py`。
    """
    with work_unit(db_session):
        yield


def _draft(db, student_id="s1", status="awaiting_confirmation", version=1, draft_id=None):
    did = draft_id or uuid.uuid4().hex[:12]
    db.add(LearningPlanDraft(
        draft_id=did, student_id=student_id, version=version, status=status,
        content={"plan_title": "t"}))
    db.commit()
    return did


def _gate(db, conversation_id, kind="draft", draft_id=None, status="open",
          tool_name="", version=1, gate_id=None):
    """造一行门。`gate_id` 默认带随机后缀：草稿门编号按 draft_id 稳定，
    同会话真要用两行 open（历史双登记 = 一条草稿门 + 一条布尔门）时必须给不同编号。"""
    if not gate_id:
        gate_id = f"{kind}-{uuid.uuid4().hex[:8]}"
    row = AgentGate(
        gate_id=gate_id, conversation_id=conversation_id, kind=kind,
        tool_name=tool_name, kwargs={}, draft_id=draft_id or None,
        version=version, action="send_student", message="m", status=status)
    db.add(row)
    db.commit()
    return row


# ── ① 同会话多行 open：只认最新一行，且全程不写库 ──────────────────────

def test_only_latest_open_row_counts(db_session):
    did = _draft(db_session)
    _gate(db_session, "conv-dup", draft_id=did)
    newer = _gate(db_session, "conv-dup", draft_id=did)  # 同会话第二行（历史双登记）

    opened = gate_service.get_open_gate(db_session, "conv-dup")

    assert opened and opened["gate_id"] == newer.gate_id, "只认 id 最大的那一行"


def test_judgement_does_not_touch_the_database(db_session, monkeypatch):
    """D17：判定 NEVER 写库。`get_open_gate` 在决定单元中途被调用，
    读里写会把别人的事务提交掉（T4 立项理由）。"""
    commits = []
    real_commit = type(db_session).commit

    def _spy(self):
        commits.append(1)
        return real_commit(self)

    monkeypatch.setattr(type(db_session), "commit", _spy)
    did = _draft(db_session)
    _gate(db_session, "conv-nocommit", draft_id=did)
    _gate(db_session, "conv-nocommit", draft_id=did)
    commits.clear()

    assert gate_service.get_open_gate(db_session, "conv-nocommit") is not None
    gate_service.check_binding(db_session, "conv-nocommit", "whatever")
    gate_service.replay_target(db_session, "conv-nocommit")

    assert commits == [], f"读路径不得 commit，实际 {len(commits)} 次"
    # 多余的那行仍在库里等着收敛——判据只负责「不算数」，不负责删
    still_open = (db_session.query(AgentGate)
                  .filter_by(conversation_id="conv-nocommit", status="open").count())
    assert still_open == 2


# ── ② 草稿版本链：判据必须落在 MAX(version) 行上 ───────────────────────

def test_sent_newer_version_supersedes_older_awaiting_row(db_session):
    """R14 的关键反例夹具：v1 待确认 + v2 已发送 ⇒ 该门不算数。

    发送通路只改命中 `(draft_id, version)` 的那一行，v1 永远留在待确认态。
    """
    did = _draft(db_session, version=1, status="awaiting_confirmation")
    _draft(db_session, version=2, status="sent", draft_id=did)
    _gate(db_session, "conv-chain", draft_id=did)

    assert gate_service.get_open_gate(db_session, "conv-chain") is None, \
        "按 IN 写法会在这里判真（v1 仍 awaiting），假门复活"


def test_awaiting_newest_version_keeps_the_gate(db_session):
    did = _draft(db_session, version=1, status="superseded")
    _draft(db_session, version=2, status="awaiting_confirmation", draft_id=did)
    _gate(db_session, "conv-chain-ok", draft_id=did)

    assert gate_service.get_open_gate(db_session, "conv-chain-ok") is not None


# ── ③ boolean 门不做草稿校验 ────────────────────────────────────────────

def test_boolean_gate_ignores_draft_state(db_session):
    _gate(db_session, "conv-bool", kind="boolean", tool_name="assign_practice")

    opened = gate_service.get_open_gate(db_session, "conv-bool")

    assert opened and opened["kind"] == "boolean"


# ── ④ draft_id 为空的 draft 门：两处判定必须一致（R23） ──────────────────

def test_draft_gate_without_draft_id_is_visible_in_both_places(db_session):
    """`open_gate` 允许 `kind='draft'` 而 `draft_id` 为 NULL（编号退化成工具哈希），
    聊天页今日照常发卡 ⇒ 判据也必须放行，否则列表与聊天页对同一会话结论相反。"""
    row = AgentGate(
        gate_id="g-draft-nullid", conversation_id="conv-nullid", kind="draft",
        tool_name="send_learning_plan", kwargs={}, draft_id=None, version=None,
        action="send_student", message="m", status="open")
    db_session.add(row)
    db_session.commit()

    assert gate_service.get_open_gate(db_session, "conv-nullid") is not None
    from sqlalchemy import func
    from app.models.database import AgentGate as _G
    from app.services.gate_service import pending_gate_criteria
    counted = (db_session.query(func.count(_G.id))
               .filter(_G.conversation_id == "conv-nullid", _G.status == "open",
                       *pending_gate_criteria()).scalar())
    assert counted == 1, "4a 列表与聊天页共用同一判据，可见性必须相同"


# ── ⑤ supersede_stale_gates：把不算数的行落库（前置二 / R22） ────────────

def test_supersede_converges_duplicate_and_fake_gates(db_session):
    did = _draft(db_session, version=1, status="awaiting_confirmation")
    _draft(db_session, version=2, status="sent", draft_id=did)
    stale = _gate(db_session, "conv-conv", draft_id=did)      # 假门（草稿已发送）
    live_did = _draft(db_session)                             # 双行夹具用真草稿
    dup_old = _gate(db_session, "conv-conv2", draft_id=live_did)
    dup_new = _gate(db_session, "conv-conv2", draft_id=live_did)
    healthy_did = _draft(db_session)
    healthy = _gate(db_session, "conv-conv3", draft_id=healthy_did)

    count = gate_service.supersede_stale_gates(db_session)

    assert count == 2, f"假门 + 同会话旧行各一，实际 {count}"
    db_session.refresh(stale)
    db_session.refresh(dup_old)
    db_session.refresh(dup_new)
    db_session.refresh(healthy)
    assert stale.status == "superseded"
    assert dup_old.status == "superseded"
    assert dup_new.status == "open", "同会话只收敛旧行，最新行留着"
    assert healthy.status == "open", "真门一律不动"


def test_supersede_single_conversation_scope(db_session):
    did = _draft(db_session, version=1, status="sent")
    mine = _gate(db_session, "conv-mine", draft_id=did)
    theirs = _gate(db_session, "conv-theirs", draft_id=did)

    assert gate_service.supersede_stale_gates(db_session, "conv-mine") == 1
    db_session.refresh(mine)
    db_session.refresh(theirs)
    assert mine.status == "superseded"
    assert theirs.status == "open", "单会话收敛不得越界动别人的门"


def test_supersede_is_idempotent(db_session):
    did = _draft(db_session, version=1, status="sent")
    _gate(db_session, "conv-idem", draft_id=did)

    first = gate_service.supersede_stale_gates(db_session)
    second = gate_service.supersede_stale_gates(db_session)

    assert first == 1 and second == 0


def test_open_gate_converges_its_own_conversation(db_session):
    """R22：摘掉读路径的自愈写之后，收敛必须有主人——开门顺带清本会话。"""
    did = _draft(db_session, version=1, status="sent")
    fake = _gate(db_session, "conv-open", draft_id=did)

    new_did = _draft(db_session)
    gate_service.open_gate(db_session, "conv-open", kind="draft",
                           tool_name="send_learning_plan",
                           kwargs={"draft_id": new_did}, draft_id=new_did,
                           version=1, action="send_student", message="m")

    db_session.refresh(fake)
    assert fake.status == "superseded", "开新门时本会话的假门必须一并落库"


# ── ⑥ 面板直发通路（不碰 agent_gates）留下的假门可被清掉（T4 验收④） ─────

def test_direct_send_leaves_a_gate_the_convergence_cleans(db_session):
    """`app/api/diagnosis.py` 的 REST 直发只改草稿行状态，永远不碰门表。

    这条是 R22 的红绿夹具：没有 `supersede_stale_gates` 时假门永久堆积——
    页面不露出，但 `stats`、决定日志与 4b 的过期计数全被污染。
    """
    did = _draft(db_session, version=1, status="awaiting_confirmation")
    gate = _gate(db_session, "conv-direct", draft_id=did)
    assert gate_service.get_open_gate(db_session, "conv-direct") is not None

    # 模拟面板直发：只动草稿
    row = (db_session.query(LearningPlanDraft)
           .filter_by(draft_id=did, version=1).first())
    row.status = "sent"
    db_session.commit()

    assert gate_service.get_open_gate(db_session, "conv-direct") is None, "页面不露出"
    db_session.refresh(gate)
    assert gate.status == "open", "但库里还是 open —— 这正是需要收敛主人的原因"

    assert gate_service.supersede_stale_gates(db_session, "conv-direct") == 1
    db_session.refresh(gate)
    assert gate.status == "superseded"
