"""门第三态的呈现面（gate-lifecycle 2.7 第 2 步）：
`GET /api/agent/gates/in-doubt` 与 `POST /api/agent/gates/{id}/in-doubt`。

**这一态为什么存在。** `claim_gate` 在执行**之前**把门占成 `executing`（防同一扇门
批两次，对），正常由 `finish_gate` 收口。两者永远在同一次请求、同一个进程里，
所以**启动时**还停在 `executing` 的门 = 执行它的那个进程已经不在了：动作可能跑过、
也可能没跑，系统无从判断。判据不设超时（阈值必须大于最慢的工具，定短了正在跑的
门会冒卡吓人）—— **能看事实就别猜**，于是落成 `in_doubt` 等老师裁定。

**这一档最要紧的一条是「草稿判据的反向钉子」**（见文件末尾）：in-doubt 最典型的
情形就是动作**已经成功**（草稿已 sent）、进程死在收口之前。谁要是图省事把
`pending_gate_criteria` 直接套上来，会**恰好漏掉最该报的那一类**，而且列表看着
一切正常 —— 空列表不报错。
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token
from app.models.conversation_owner import ConversationOwner
from app.models.database import (AgentGate, Base, Class, LearningPlanDraft,
                                 Student, get_db)


@pytest.fixture
def db_session():
    """StaticPool 版内存库（同 `test_pending_gates_endpoint` 的理由：
    TestClient 在另一个线程跑请求，默认连接池会给它一个全新的空库）。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


# ── 夹具 ────────────────────────────────────────────────────────────────

def _student(db, sid="s-1", name="王梓涵"):
    if not db.get(Class, "cls-1"):
        db.add(Class(class_id="cls-1", name="高一(2)班", grade="高一", subject="化学"))
    if not db.get(Student, sid):
        db.add(Student(student_id=sid, name=name, class_id="cls-1", status="approved"))
    db.flush()
    return sid


def _owner(db, thread, user_id):
    db.add(ConversationOwner(thread_id=thread, user_id=user_id, role="teacher"))
    db.flush()


def _draft(db, status="awaiting_confirmation"):
    did = uuid.uuid4().hex[:12]
    db.add(LearningPlanDraft(draft_id=did, student_id="s-1", version=1,
                             status=status, content={"plan_title": "t"}))
    db.flush()
    return did


def _gate(db, conversation_id, status, kind="draft", draft_id=None,
          tool_name="send_learning_plan", action="send_student", message="方案待发"):
    row = AgentGate(
        gate_id=f"g-{kind}-{uuid.uuid4().hex[:8]}",
        conversation_id=conversation_id, kind=kind, tool_name=tool_name,
        kwargs={"student_id": "s-1"}, draft_id=draft_id, version=1 if draft_id else None,
        action=action, message=message, status=status)
    db.add(row)
    db.flush()
    return row.gate_id


def _hdr(user_id, role="teacher"):
    return {"Authorization": f"Bearer {create_access_token(user_id, role)}"}


def _get_in_doubt(client, user_id, role="teacher"):
    return client.get("/api/agent/gates/in-doubt", headers=_hdr(user_id, role))


def _post_resolve(client, gate_id, retry, user_id, role="teacher"):
    return client.post(f"/api/agent/gates/{gate_id}/in-doubt",
                       json={"retry": retry}, headers=_hdr(user_id, role))


# ── 读：谁该出现 ────────────────────────────────────────────────────────

def test_owner_sees_their_in_doubt_gate(db_session, client):
    _student(db_session)
    _owner(db_session, "t-a", "u-a")
    gid = _gate(db_session, "t-a", "in_doubt")

    body = _get_in_doubt(client, "u-a").json()
    assert [i["gate_id"] for i in body["items"]] == [gid]
    assert body["owned_total"] == 1


def test_only_in_doubt_rows_appear(db_session, client):
    """`open` / `executed` 一律不进这张卡 —— 它们各有各的去处。

    `open` 进这一列的危险最实在：老师会看到一扇还在等他**决定**的门，被摆成
    「结果未知，请裁定」，于是要么白裁一次、要么把一扇正常的门按掉。
    """
    _student(db_session)
    _owner(db_session, "t-a", "u-a")
    _gate(db_session, "t-a", "open")
    _gate(db_session, "t-a", "executed")
    _gate(db_session, "t-a", "executing")   # 正在跑，也不算 in-doubt
    _gate(db_session, "t-a", "in_doubt", kind="boolean", draft_id=None,
          tool_name="assign_adaptive_practice", action="")

    body = _get_in_doubt(client, "u-a").json()
    assert body["owned_total"] == 1
    assert body["items"][0]["tool_name"] == "assign_adaptive_practice"


def test_same_conversation_counts_once(db_session, client):
    """同会话多行只算最新一行 —— 与待确认列表同一条口径。"""
    _student(db_session)
    _owner(db_session, "t-a", "u-a")
    _gate(db_session, "t-a", "in_doubt")
    _gate(db_session, "t-a", "in_doubt")

    assert _get_in_doubt(client, "u-a").json()["owned_total"] == 1


def test_another_teachers_gate_is_invisible(db_session, client):
    """归属收权：登记在别人名下的会话两段都不出现。"""
    _student(db_session)
    _owner(db_session, "t-b", "u-b")
    _gate(db_session, "t-b", "in_doubt")

    body = _get_in_doubt(client, "u-a").json()
    assert body["owned_total"] == 0 and body["unowned_total"] == 0
    assert body["items"] == []


def test_requires_auth(db_session, client):
    assert client.get("/api/agent/gates/in-doubt").status_code == 401


# ── 写：两个动作 ────────────────────────────────────────────────────────

def test_confirm_executed_settles_the_gate(db_session, client):
    """「确认已执行」→ `executed`：给"动作其实跑成功了、进程死在收口之前"。"""
    _student(db_session)
    _owner(db_session, "t-a", "u-a")
    gid = _gate(db_session, "t-a", "in_doubt")

    r = _post_resolve(client, gid, False, "u-a")
    assert r.status_code == 200 and r.json()["status"] == "executed"
    assert db_session.query(AgentGate).filter_by(gate_id=gid).one().status == "executed"


def test_retry_puts_it_back_to_open(db_session, client):
    """「重试」→ 退回 `open`：给"动作其实没跑成"。

    退回 `open` 而不是另造一条执行路径 —— `claim_gate → 重放 → finish_gate`
    那条通路已经有了，重开门之后老师再批一次就是走它。
    """
    _student(db_session)
    _owner(db_session, "t-a", "u-a")
    gid = _gate(db_session, "t-a", "in_doubt")

    r = _post_resolve(client, gid, True, "u-a")
    assert r.status_code == 200 and r.json()["status"] == "open"
    assert db_session.query(AgentGate).filter_by(gate_id=gid).one().status == "open"


def test_resolving_a_non_in_doubt_gate_is_409(db_session, client):
    """只认 `in_doubt`。一扇**还在等老师决定**的门被"确认已执行"，
    等于把一个从没执行过的动作记成做过 —— 必须拒绝，而且要拒绝得干净。"""
    _student(db_session)
    _owner(db_session, "t-a", "u-a")
    gid = _gate(db_session, "t-a", "open")

    assert _post_resolve(client, gid, False, "u-a").status_code == 409
    assert db_session.query(AgentGate).filter_by(gate_id=gid).one().status == "open"


def test_cannot_resolve_someone_elses_gate(db_session, client):
    """越权裁定改的是别人会话的状态：光有权限不够，还要归属。"""
    _student(db_session)
    _owner(db_session, "t-b", "u-b")
    gid = _gate(db_session, "t-b", "in_doubt")

    assert _post_resolve(client, gid, False, "u-a").status_code == 403
    assert db_session.query(AgentGate).filter_by(gate_id=gid).one().status == "in_doubt"


def test_unknown_gate_is_404(db_session, client):
    _student(db_session)
    assert _post_resolve(client, "g-nope", False, "u-a").status_code == 404


# ── 反向钉子：草稿判据不许套到这里来 ────────────────────────────────────

def test_draft_already_sent_still_shows_up(db_session, client):
    """**这一档的核心断言。**

    in-doubt 最典型的形态：动作其实**成功了**（草稿已 sent），进程死在
    `finish_gate` 之前。`pending_gate_criteria` 的草稿判据要求
    「草稿 MAX(version) 那行仍是 awaiting_confirmation」，套上来就会把这一类
    **整批筛掉** —— 而那恰恰是最需要老师裁定的（他得知道学校那边到底收没收到）。
    空列表不报错，所以这个洞只有断言能照到。
    """
    _student(db_session)
    _owner(db_session, "t-a", "u-a")
    sent = _draft(db_session, status="sent")
    gid = _gate(db_session, "t-a", "in_doubt", draft_id=sent)

    body = _get_in_doubt(client, "u-a").json()
    assert [i["gate_id"] for i in body["items"]] == [gid], \
        "草稿已 sent 的 in-doubt 门被筛掉了 —— 最该报的那一类反而没报"


def test_superseded_draft_still_shows_up(db_session, client):
    """草稿被新版顶掉（superseded）也一样要报：门说的是"那次动作跑没跑"，
    与草稿链当前停在哪一版无关。"""
    _student(db_session)
    _owner(db_session, "t-a", "u-a")
    old = _draft(db_session, status="superseded")
    gid = _gate(db_session, "t-a", "in_doubt", draft_id=old)

    assert [i["gate_id"] for i in _get_in_doubt(client, "u-a").json()["items"]] == [gid]
