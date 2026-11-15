"""T8 —— 4a 端点 `GET /api/agent/gates/pending` 的验收（计划 Verify ①–⑫）。

这一页的全部风险都在「口径」上：列表、角标、聊天页必须对同一份集合说同一个数。
所以这里的断言几乎都是**成对**的（页面看到的 == 计数说的 == 聊天页判定的），
而不是单点返回值。⑨ 单独打的是「`app/main.py` 漏一行 `include_router`」——
那是唯一一个写完代码全绿、上线才 404 的缺口，所以它必须发一次真请求。
"""
import json
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token
from app.middleware.conversation import owns_conversation
from app.models.conversation_owner import ConversationOwner
from app.models.database import AgentGate, Base, Class, LearningPlanDraft, Student, get_db
from app.services.gate_service import gate_service
from app.services.work_unit import work_unit


@pytest.fixture(autouse=True)
def _unit(db_session):
    """这一档测试验列表口径，写入口的单元统一在这里开。

    走真 HTTP 的那几条不受影响：TestClient 在另一个线程里跑请求，工作单元记在
    `ContextVar` 里，那个线程看得见的是它自己的上下文。
    """
    with work_unit(db_session):
        yield

T0 = datetime(2026, 9, 1, 0, 0, 0)


@pytest.fixture
def db_session():
    """StaticPool 版内存库（覆盖 `tests/conftest.py` 的默认夹具）。

    `client` 那几条走真 HTTP：TestClient 在另一个线程里跑请求，默认
    SingletonThreadPool 会给那个线程一个**全新的空库** ⇒ `no such table`。
    StaticPool 让所有线程共用同一条连接，直调服务层与走 HTTP 才是同一个库。
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


# ── 夹具 ────────────────────────────────────────────────────────────────

def _class(db, class_id="cls-1", name="高一(2)班"):
    """幂等：`healthy` 夹具已经建过一次，测试体再调一次不该撞 UNIQUE。"""
    if not db.get(Class, class_id):
        db.add(Class(class_id=class_id, name=name, grade="高一", subject="化学"))
        db.flush()
    return class_id


def _student(db, sid="s-1", name="王梓涵", class_id="cls-1"):
    if not db.get(Student, sid):
        db.add(Student(student_id=sid, name=name, class_id=class_id,
                       status="approved"))
        db.flush()
    return sid


def _owner(db, thread, user_id):
    db.add(ConversationOwner(thread_id=thread, user_id=user_id, role="teacher"))
    db.flush()


def _draft(db, student_id="s-1", status="awaiting_confirmation", version=1,
           draft_id=None):
    did = draft_id or uuid.uuid4().hex[:12]
    db.add(LearningPlanDraft(
        draft_id=did, student_id=student_id, version=version, status=status,
        content={"plan_title": "t"}))
    db.flush()
    return did


def _gate(db, conversation_id, kind="draft", draft_id=None, status="open",
          tool_name="", kwargs=None, action="send_student", message="方案待发",
          opened_at=None, gate_id=None):
    did = draft_id or None
    row = AgentGate(
        # 默认编号恒带随机后缀：同一个 draft_id 可以挂在两个会话上（真实场景：
        # 两个老师各自触发同一份草稿的确认门），按 draft_id 派生必然撞
        # `agent_gates.gate_id` 的 UNIQUE。要稳定编号的用例自己传 `gate_id=`。
        gate_id=gate_id or f"g-{kind}-{uuid.uuid4().hex[:8]}",
        conversation_id=conversation_id, kind=kind, tool_name=tool_name,
        kwargs=kwargs or {}, draft_id=did, version=1 if did else None,
        action=action, message=message, status=status,
        last_opened_at=opened_at or T0)
    db.add(row)
    db.flush()
    return row


def _pending(db, user_id, kind=""):
    return gate_service.pending_gates(db, user_id, kind=kind)


@pytest.fixture
def healthy(db_session):
    """一个学生 + 一条待确认草稿：draft 门的最短健康形态。"""
    _class(db_session)
    _student(db_session)
    return _draft(db_session)


@pytest.fixture
def client(db_session):
    """真 app 上的 HTTP 断言（⑨）：只覆盖 get_db，不进 lifespan ⇒ 不碰 chemai.db。"""
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _get(client, user_id, role="teacher", **params):
    return client.get("/api/agent/gates/pending", params=params,
                      headers={"Authorization": f"Bearer {create_access_token(user_id, role)}"})


# ── ①②⑥⑦ 集合口径 ──────────────────────────────────────────────────────

def test_same_conversation_two_open_rows_count_once(db_session, healthy):
    """① 同会话两行 open（历史双登记）只算 1 件。"""
    _owner(db_session, "t-a", "u-a")
    _gate(db_session, "t-a", draft_id=healthy, gate_id="g-old")
    _gate(db_session, "t-a", draft_id=healthy, gate_id="g-new")

    out = _pending(db_session, "u-a")

    assert out["owned_total"] == 1, out
    assert [i["gate_id"] for i in out["items"]] == ["g-new"]


def test_sent_draft_gate_absent_from_items_and_counts(db_session, healthy):
    """② 草稿已发送：列表不出现、不计数。"""
    _owner(db_session, "t-a", "u-a")
    _gate(db_session, "t-a", draft_id=healthy)
    row = db_session.query(LearningPlanDraft).filter_by(draft_id=healthy).first()
    row.status = "sent"
    db_session.flush()

    out = _pending(db_session, "u-a")

    assert out == {"items": [], "owned_total": 0,
                   "unowned_items": [], "unowned_total": 0}, out


def test_limit_applies_to_the_filtered_set(db_session, healthy):
    """⑥ `LIMIT 50` 落在过滤后的集合上：假门不占展示名额。

    先插 40 个假门再插 40 个真门——"先截断再过滤"的写法只会剩下不到 40 条真门。
    """
    for n in range(40):
        conv = f"t-fake-{n}"
        _owner(db_session, conv, "u-a")
        did = _draft(db_session, draft_id=f"fake-{n}")
        _gate(db_session, conv, draft_id=did)
        row = db_session.query(LearningPlanDraft).filter_by(draft_id=did).first()
        row.status = "sent"
        db_session.flush()
    for n in range(40):
        conv = f"t-real-{n}"
        _owner(db_session, conv, "u-a")
        _gate(db_session, conv, draft_id=healthy, gate_id=f"g-real-{n}")

    out = _pending(db_session, "u-a")

    assert out["owned_total"] == 40, "40 个假门一件都不算"
    assert len(out["items"]) == 40
    assert {i["gate_id"] for i in out["items"]} == {f"g-real-{n}" for n in range(40)}


def test_four_field_contract(db_session, healthy):
    """⑦ 四字段各管一处，两个计数是同谓词在 `owned` 两分支上的 COUNT。"""
    _owner(db_session, "t-mine", "u-a")
    _gate(db_session, "t-mine", draft_id=healthy)
    _gate(db_session, "t-legacy", kind="boolean", tool_name="bash",
          kwargs={"command": "python grade.py"}, message="")

    out = _pending(db_session, "u-a")

    assert set(out) == {"items", "owned_total", "unowned_items", "unowned_total"}
    assert (out["owned_total"], out["unowned_total"]) == (1, 1)
    assert [i["conversation_id"] for i in out["items"]] == ["t-mine"]
    assert [i["conversation_id"] for i in out["unowned_items"]] == ["t-legacy"]


def test_owned_over_limit_truncates_items_not_counts(db_session, healthy):
    """⑥ 的另一半：60 件真门 ⇒ `owned_total` 仍是 60，`items` 只有 50（尾行的 N 由此来）。"""
    for n in range(60):
        conv = f"t-{n}"
        _owner(db_session, conv, "u-a")
        _gate(db_session, conv, draft_id=healthy, gate_id=f"g-{n}",
              opened_at=T0 + timedelta(hours=n))

    out = _pending(db_session, "u-a")

    assert out["owned_total"] == 60
    assert len(out["items"]) == 50
    # 等最久的在前（D1 排序键 = last_opened_at ASC）
    assert [i["gate_id"] for i in out["items"][:3]] == ["g-0", "g-1", "g-2"]
    assert 60 - len(out["items"]) == 10, "尾行「另有 N 件未显示」的 N"


def test_unowned_segment_has_its_own_cap(db_session, healthy):
    """⑦ 折叠段自己的 20 条上限，与上段的 50 互不占用。"""
    _owner(db_session, "t-mine", "u-a")
    _gate(db_session, "t-mine", draft_id=healthy)
    for n in range(25):
        _gate(db_session, f"t-old-{n}", draft_id=healthy, gate_id=f"go-{n}",
              opened_at=T0 + timedelta(hours=n))

    out = _pending(db_session, "u-a")

    assert (out["owned_total"], len(out["items"])) == (1, 1)
    assert out["unowned_total"] == 25
    assert len(out["unowned_items"]) == 20
    assert out["unowned_items"][0]["gate_id"] == "go-0", "段内同样按等待时长倒序"


# ── ③ 只读 ──────────────────────────────────────────────────────────────

def test_endpoint_read_never_commits(db_session, monkeypatch, healthy):
    """③ 判定全程不 commit：4a 是只读页，不能因为看一眼列表就改数据。"""
    commits = []
    real_commit = type(db_session).commit

    def _spy(self):
        commits.append(1)
        return real_commit(self)

    monkeypatch.setattr(type(db_session), "commit", _spy)
    _class(db_session)
    _student(db_session)
    did = _draft(db_session)
    _owner(db_session, "t-a", "u-a")
    _gate(db_session, "t-a", draft_id=did)
    _gate(db_session, "t-legacy", draft_id=did)
    commits.clear()

    out = _pending(db_session, "u-a")

    assert out["unowned_total"] == 1
    assert commits == [], f"读路径不得 commit，实际 {len(commits)} 次"
    open_rows = db_session.query(AgentGate).filter_by(status="open").count()
    assert open_rows == 2, "两行都还在库里等收敛作业，页面只是不露出"


# ── ④⑧ 人话与版本链 ─────────────────────────────────────────────────────

def test_every_row_carries_a_non_empty_plain_language_subject(db_session, healthy):
    """④/D18：第一列由后端算好；页面上不出现 `kwargs` JSON 与机器字面量。"""
    _owner(db_session, "t-a", "u-a")
    _gate(db_session, "t-a", draft_id=healthy)
    _gate(db_session, "t-b", kind="boolean", tool_name="bash",
          kwargs={"command": "python grade.py --all"})
    _owner(db_session, "t-b", "u-a")
    _gate(db_session, "t-c", kind="boolean", tool_name="some_future_tool",
          kwargs={"payload": {"secret": 1}})
    _owner(db_session, "t-c", "u-a")

    out = _pending(db_session, "u-a")
    labels = [i["subject_label"] for i in out["items"]]

    assert all(labels), labels
    assert "王梓涵(高一(2)班)" in labels
    assert any("执行命令" in s and "grade.py" in s for s in labels), labels
    assert any(s == "some_future_tool" for s in labels), "映射表没有该工具 → 原值，NEVER 整段 JSON"
    for label in labels:
        assert "{" not in label and "}" not in label, label
    dumped = json.dumps(out, ensure_ascii=False)
    for needle in ("kwargs", "student_id", "secret"):
        assert needle not in dumped, f"响应不得把机器字段露给老师：{needle}"


def test_draft_label_follows_the_newest_version_row(db_session):
    """⑧/D18 R17（D23=A）：学生 id 以 MAX(version) 草稿行为权威源，不取 kwargs 副本。"""
    _class(db_session)
    _student(db_session, sid="s-old", name="李承宇")
    _student(db_session, sid="s-new", name="陈雨桐")
    did = _draft(db_session, student_id="s-old", version=1)
    _draft(db_session, student_id="s-new", version=2, draft_id=did)
    _owner(db_session, "t-a", "u-a")
    _gate(db_session, "t-a", draft_id=did, kwargs={"student_id": "s-old"})

    out = _pending(db_session, "u-a")

    assert out["items"][0]["subject_label"] == "陈雨桐(高一(2)班)"
    assert "李承宇" not in json.dumps(out, ensure_ascii=False)


def test_version_chain_sent_newest_hides_the_gate(db_session, healthy):
    """⑧ 的反例本体：v1 待确认 + v2 已发送 ⇒ 该门不算数（按 `IN` 写会判真）。"""
    did = _draft(db_session, version=1, status="awaiting_confirmation")
    _draft(db_session, version=2, status="sent", draft_id=did)
    _owner(db_session, "t-a", "u-a")
    _gate(db_session, "t-a", draft_id=did)

    assert _pending(db_session, "u-a")["owned_total"] == 0


def test_missing_student_falls_back_to_id(db_session, healthy):
    """学生被删 → 显示 id 原值，NEVER 空单元格（D18 验收②）。"""
    _class(db_session)
    did = _draft(db_session, student_id="s-gone")
    _owner(db_session, "t-a", "u-a")
    _gate(db_session, "t-a", draft_id=did)

    out = _pending(db_session, "u-a")

    assert out["items"][0]["subject_label"] == "s-gone"


# ── ⑨⑤ 注册与权限 ───────────────────────────────────────────────────────

def test_route_is_registered_on_the_real_app(client, db_session, healthy):
    """⑨ 真请求拿 200，不是 404 —— 专防 `app/main.py` 漏 `include_router`。"""
    _class(db_session)
    _student(db_session)
    _owner(db_session, "t-a", "u-a")
    _gate(db_session, "t-a", draft_id=healthy)

    res = _get(client, "u-a")

    assert res.status_code == 200, res.status_code
    assert res.json()["owned_total"] == 1


ROLES = ["super_admin", "admin", "academic_admin", "subject_lead", "teacher"]


@pytest.mark.parametrize("role", ROLES)
def test_each_staff_role_sees_only_its_own_count(client, db_session, healthy, role):
    """⑤ 五种员工角色都能用这一页（含 super_admin），且各拿自己那条。

    夹具故意造到五种：`super_admin` 按矩阵镜像 `admin` 全权，只测四种会漏掉它。
    """
    _class(db_session)
    _student(db_session)
    for r in ROLES:
        _owner(db_session, f"t-{r}", f"u-{r}")
        _gate(db_session, f"t-{r}", draft_id=healthy, gate_id=f"g-{r}")

    body = _get(client, f"u-{role}", role=role).json()

    assert [i["gate_id"] for i in body["items"]] == [f"g-{role}"]
    assert body["owned_total"] == 1


def test_self_roles_cannot_open_the_page(client):
    """学生/家长没有任何教师端资源 ⇒ 菜单不下发、端点 403（D19）。"""
    assert _get(client, "s-1", role="student").status_code == 403
    assert _get(client, "p-1", role="parent").status_code == 403


def test_kind_filter_narrows_every_field(db_session, healthy):
    """D14 的唯一筛选维度：筛选在 LIMIT 之前，四字段一起收窄。"""
    _owner(db_session, "t-a", "u-a")
    _gate(db_session, "t-a", draft_id=healthy)
    _gate(db_session, "t-b", kind="boolean", tool_name="bash",
          kwargs={"command": "ls"})
    _owner(db_session, "t-b", "u-a")

    draft_only = _pending(db_session, "u-a", kind="draft")
    boolean_only = _pending(db_session, "u-a", kind="boolean")

    assert draft_only["owned_total"] == 1 and draft_only["unowned_total"] == 0
    assert boolean_only["owned_total"] == 1
    assert {i["kind"] for i in boolean_only["items"]} == {"boolean"}


# ── ⑪⑫ 归属：跨教师隔离与等价式 ─────────────────────────────────────────

def test_two_teachers_same_tool_same_kwargs_each_count_theirs(db_session):
    """⑪/T18 ④：两位教师同工具同参数的布尔门各得一行 open、各计各的。"""
    for u in ("u-a", "u-b"):
        _owner(db_session, f"t-{u}", u)
        gate_service.open_gate(db_session, f"t-{u}", kind="boolean",
                               tool_name="assign_adaptive_practice",
                               kwargs={"student_id": "s-1"},
                               message="")
    _class(db_session)
    _student(db_session)

    a, b = _pending(db_session, "u-a"), _pending(db_session, "u-b")

    assert a["owned_total"] == 1 and b["owned_total"] == 1
    assert a["items"][0]["gate_id"] != b["items"][0]["gate_id"]
    assert a["items"][0]["subject_label"] == b["items"][0]["subject_label"] == "发放自适应练习 · 王梓涵(高一(2)班)"


def test_teachers_do_not_see_each_others_gates(db_session, healthy):
    """⑪ A 的 `owned_total` 不含 B 的门（登记在别人名下的会话两段都不出现）。"""
    _owner(db_session, "t-a", "u-a")
    _owner(db_session, "t-b", "u-b")
    mine = _gate(db_session, "t-a", draft_id=healthy)
    _gate(db_session, "t-b", draft_id=healthy, gate_id="g-b")

    out = _pending(db_session, "u-a")

    assert [i["gate_id"] for i in out["items"]] == [mine.gate_id]
    assert out["unowned_items"] == []


def test_segments_union_equals_chat_page_visibility(db_session, healthy):
    """⑫ 两段并集 == 该员工在聊天页可见的会话集合（R20 的等价式）。

    含 `tXabc`：LIKE 't-%' 里的 `_` 是单字符通配，会把这条算进来，而
    `startswith(("t-","c"))` 不会 —— 谓词用 substr 就是为了这一条。
    """
    threads = ["t-legacy", "clegacy", "tXabc", "m-student", "p-parent",
               "t-mine", "t-hers"]
    for t in threads:
        _gate(db_session, t, draft_id=healthy, gate_id=f"g-{t}")
    _owner(db_session, "t-mine", "u-a")
    _owner(db_session, "t-hers", "u-b")
    user = {"user_id": "u-a", "role": "teacher"}

    out = _pending(db_session, "u-a")
    seen = {i["conversation_id"] for i in out["items"] + out["unowned_items"]}

    assert seen == {t for t in threads if owns_conversation(db_session, t, user)}
    assert seen == {"t-legacy", "clegacy", "t-mine"}, "无登记的 m-/p- 会话与 tXabc 都不该出现"


def test_three_numbers_stay_consistent(db_session, healthy):
    """⑫ 三数一致：`owned_total == 上段条数`（≤50 时）、`unowned_total ≥ 折叠条数`。"""
    _owner(db_session, "t-mine", "u-a")
    _gate(db_session, "t-mine", draft_id=healthy)
    for n in range(5):
        _gate(db_session, f"t-old-{n}", draft_id=healthy, gate_id=f"o{n}")

    out = _pending(db_session, "u-a")

    assert out["owned_total"] == len(out["items"])
    assert out["unowned_total"] >= len(out["unowned_items"])
    assert out["unowned_total"] == len(out["unowned_items"]) == 5


# ── ⑩ 最小可证伪夹具集（一次造齐） ──────────────────────────────────────

def test_falsifiable_fixture_set(db_session, healthy):
    """⑩：登记会话 + 无主 `t-` + 无登记 `m-` + 同会话两行 + 版本链 + `draft_id` 为 NULL。

    这一条是 ①②⑧⑪⑫ 的合订本：缺任一夹具，本轮某条验收就变成可以空转通过的断言。
    """
    _class(db_session)
    _student(db_session)
    # 自有会话：两行 open，只算最新一行
    _owner(db_session, "t-mine", "u-a")
    _gate(db_session, "t-mine", draft_id=healthy, gate_id="mine-old")
    _gate(db_session, "t-mine", draft_id=healthy, gate_id="mine-new")
    # 版本链反例（不出现）
    chain = _draft(db_session, draft_id="chain", version=1,
                   status="awaiting_confirmation")
    _draft(db_session, draft_id="chain", version=2, status="sent")
    _owner(db_session, "t-chain", "u-a")
    _gate(db_session, "t-chain", draft_id=chain, gate_id="chain-gate")
    # draft_id 为 NULL 的 draft 门：②′ 两处同时可见
    db_session.add(AgentGate(
        gate_id="nullid", conversation_id="t-null", kind="draft", tool_name="send_learning_plan",
        kwargs={}, draft_id=None, version=None, action="send_student",
        message="家长报告待确认", status="open", last_opened_at=T0))
    _owner(db_session, "t-null", "u-a")
    # 无主老会话 + 无登记的学生会话
    _gate(db_session, "t-legacy", draft_id=healthy, gate_id="legacy-gate")
    _gate(db_session, "m-student", draft_id=healthy, gate_id="student-gate")
    # 别人的登记会话
    _owner(db_session, "t-hers", "u-b")
    _gate(db_session, "t-hers", draft_id=healthy, gate_id="hers-gate")

    out = _pending(db_session, "u-a")
    mine = {i["gate_id"] for i in out["items"]}
    theirs = {i["gate_id"] for i in out["unowned_items"]}

    assert mine == {"mine-new", "nullid"}, mine
    assert theirs == {"legacy-gate"}, theirs
    assert out["owned_total"] == 2
    assert "chain-gate" not in mine | theirs, "按 IN 写法会在这里判真（v1 仍待确认）"
    assert "hers-gate" not in mine | theirs
    assert "student-gate" not in mine | theirs, "无登记的学生会话是跨角色露出"
    nullid = [i for i in out["items"] if i["gate_id"] == "nullid"][0]
    assert nullid["subject_label"] == "家长报告待确认"
    assert gate_service.get_open_gate(db_session, "t-null") is not None, \
        "②′：聊天页与 4a 必须同时可见"


def test_deleted_conversation_leaves_no_row_in_either_segment(db_session, healthy):
    """删会话后残留的登记行不影响 4a（测试计划第 5 条的现场判定）。

    `conversation_owners` 没有任何删除路径，会话删掉后登记行还在。这一步要定的是它还
    能不能把一扇门带进列表：答案是不能，因为删除通路 `close_conversation`
    （`gate_service.py:474`）会先把该会话的 open 门全落成 `superseded`，而 4a 只认 open。
    残留行的唯一作用是把那个 thread 永远留在"有主"分支，不漂进折叠段冒领别人的门。
    """
    _owner(db_session, "t-gone", "u-a")
    _gate(db_session, "t-gone", draft_id=healthy)
    assert _pending(db_session, "u-a")["owned_total"] == 1

    gate_service.close_conversation(db_session, "t-gone")
    out = _pending(db_session, "u-a")

    assert (out["owned_total"], out["unowned_total"]) == (0, 0), out
    assert db_session.query(AgentGate).filter_by(conversation_id="t-gone").one().status \
        == "superseded"


def test_anonymous_token_gets_401(client):
    """无 token 时中间件就拦下（不是这一页自己发明的第二套授权）。"""
    assert client.get("/api/agent/gates/pending").status_code == 401
