"""捉虫沉淀回归（bug-hunt 2026-10-03，批 1-3 捉虫 10 处的固化复验）。

每条对应一个真实发现的 bug 模式，复现推理见提交 21cd2aa 与
捉虫两 agent 报告；修复后全部应绿。
"""
import sys
import base64
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token
from app.models.database import (
    Base, ExamRecord, LearningPlanDraft, Question, Student, get_db,
)
from app.api.practice import save_answer_attachments
from app.services import parent_task_service as pts
from app.services.plan_draft_service import plan_draft_service


# ── 捉虫 #1（P1）：家长派任务 sender_id 断链 ─────────────────────────────
# 复现推理：send_draft(sender_id=家长) → teacher_teaches_student 只查
# teacher_class_subject → 家长必被拒，批准重放永远失败。

def test_parent_send_draft_role_aware_scope():
    """家长派任务重放：parent:x 身份走亲子绑定校验（修前走授课关系必拒）；
    未绑定家长仍被拒（fail-closed 不松动）。"""
    from app.models.database import StudentParentBinding
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        from app.services.work_unit import work_unit
        db.add(Student(student_id="s-p", name="派送生", class_id="cls-1",
                       status="approved"))
        db.add(StudentParentBinding(binding_id="b-p", student_id="s-p",
                                    parent_id="p-1", status="active",
                                    bind_code="bc"))
        db.commit()
        computed = {"student_id": "s-p", "student_name": "派送生",
                    "plan_data": {"plan_title": "家长派发任务", "daily_tasks": []}}
        with work_unit(db) as unit_db:
            draft = plan_draft_service.create_draft(
                unit_db, computed, created_by="parent:p-1")
        from app.models.database import LearningPlanDraft
        row = (db.query(LearningPlanDraft)
               .filter(LearningPlanDraft.draft_id == draft["draft_id"]).first())
        row.teacher_approved = True
        db.commit()
        with work_unit(db) as unit_db:
            out = plan_draft_service.send_draft(
                unit_db, student_id="s-p", draft_id=draft["draft_id"],
                sender_id="parent:p-1")
        assert "error" not in out
        # 未绑定的家长仍被拒（fail-closed 不松动）
        db.add(LearningPlanDraft(
            draft_id="pd-stranger", student_id="s-p", version=1,
            status="awaiting_confirmation", content={"plan_title": "x"},
            created_by="parent:stranger"))
        db.commit()
        row2 = (db.query(LearningPlanDraft)
                .filter(LearningPlanDraft.draft_id == "pd-stranger").first())
        row2.teacher_approved = True
        db.commit()
        with work_unit(db) as unit_db:
            out2 = plan_draft_service.send_draft(
                unit_db, student_id="s-p", draft_id="pd-stranger",
                sender_id="parent:stranger")
        assert "error" in out2 and "未绑定" in out2["error"]
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_smart_record_has_owner_key(db_session):
    """会话 question_stats 顶层必须带 student_id（_belongs_to 归属键）；
    缺失时「智能练习-XX」进全班任务列表、他人可取题可提交。

    行为级断言（ADR-0029 批② 编排迁 service 后源码字面量不在端点，
    钉真实语义比钉源码文本结实）：start_session 生成的记录必须带归属键。"""
    from app.models.database import (
        ExamRecord, Question, QuestionSource, AuditStatus, Difficulty, Student,
    )
    from app.services.smart_practice_service import start_session
    db_session.add(Student(student_id="202610101", name="李四",
                           class_id="class_2025_1"))
    db_session.add(Question(
        question_id="ow_q1", record_id="ow_rec", content="平衡题", answer="A",
        options=["A", "B"], knowledge_points=["化学平衡"],
        difficulty=Difficulty.MEDIUM, source=QuestionSource.MANUAL_SELECTED,
        audit_status=AuditStatus.PASSED))
    db_session.commit()

    res = start_session(db_session, "202610101", kps=["化学平衡"])
    rec = db_session.query(ExamRecord).filter(
        ExamRecord.record_id == res["practice_id"]).one()
    assert rec.question_stats["student_id"] == "202610101"
    assert rec.question_stats["smart"]["student_id"] == "202610101"


def test_belongs_to_owner_semantics():
    from app.api.practice import _belongs_to
    rec = ExamRecord(record_id="r", class_id="cls-1", name="x",
                     question_stats={"student_id": "s-1"})
    assert _belongs_to(rec, "s-1")
    assert not _belongs_to(rec, "s-2")
    no_owner = ExamRecord(record_id="r2", class_id="cls-1", name="y",
                          question_stats={})
    assert _belongs_to(no_owner, "任何人")  # 全班卷


# ── 捉虫 #6（P2）：智能流画布附件随答落盘 ──────────────────────────────

def test_smart_answer_accepts_attachments():
    """/smart/answer 请求模型带 attachments 且走 save_answer_attachments
    （与 submit 对称——智能流不再丢装置图）。"""
    import inspect
    import app.api.smart_practice as sp
    assert "attachments" in sp.SmartAnswerRequest.model_fields
    assert "save_answer_attachments" in inspect.getsource(sp.smart_answer)


# ── 捉虫 #7（P3）：/due limit 参数 ─────────────────────────────────────

def test_due_endpoint_has_limit_param():
    import inspect
    import app.api.review as rv
    assert "limit: int = 20" in inspect.getsource(rv.get_due_reviews)


# ── 附件落盘：消毒 + 静默跳过 + 往返（捉虫跟进）────────────────────────

@pytest.fixture
def attach_root(tmp_path):
    return tmp_path / "data"


def test_save_attachments_rejects_path_traversal(attach_root):
    out = save_answer_attachments(
        [{"question_id": "../evil", "data_url": "data:image/png;base64,AAAA"}],
        "s-1", "p-1", base_dir=attach_root)
    assert out == {}
    out2 = save_answer_attachments(
        [{"question_id": "q;1", "data_url": "data:image/png;base64,AAAA"}],
        "../s-1", "p-1", base_dir=attach_root)
    assert out2 == {}


def test_save_attachments_roundtrip(attach_root):
    raw = base64.b64encode(b"\x89PNG-fake").decode()
    out = save_answer_attachments(
        [{"question_id": "q-1", "data_url": f"data:image/png;base64,{raw}"}],
        "s-1", "p-1", base_dir=attach_root)
    assert "q-1" in out
    assert (attach_root / "answer_attachments" / "s-1" / "p-1" / "q-1.png").is_file()


def test_save_attachments_skips_oversize_and_bad_payload(attach_root):
    big = base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode()
    out = save_answer_attachments([
        {"question_id": "q-big", "data_url": f"data:image/png;base64,{big}"},
        {"question_id": "q-bad", "data_url": "not-a-data-url"},
        {"no_question": True},
    ], "s-1", "p-1", base_dir=attach_root)
    assert out == {}


# ── 频控口径（用户定案 2026-10-03：按送达计）──────────────────────────

def test_frequency_counts_sent_only():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        monday = pts.week_start(now)
        # 本周：1 条送达 + 2 条确认中（放弃/打回形态）→ 只计 1
        seeds = [
            ("pd-a", "sent", 1),
            ("pd-w1", "awaiting_confirmation", 1),
            ("pd-w2", "awaiting_confirmation", 2),
        ]
        for did, status, offset in seeds:
            db.add(LearningPlanDraft(
                draft_id=did, student_id="s-1", version=1, status=status,
                content={"plan_title": did}, created_by="parent:p-1",
                created_at=monday + timedelta(days=offset)))
        # 上周送达与教师草稿均不计
        db.add(LearningPlanDraft(
            draft_id="pd-old", student_id="s-1", version=1, status="sent",
            content={"plan_title": "old"}, created_by="parent:p-1",
            created_at=monday - timedelta(days=3)))
        db.add(LearningPlanDraft(
            draft_id="pd-t", student_id="s-1", version=1, status="sent",
            content={"plan_title": "t"}, created_by="t-1", created_at=now))
        db.commit()
        # now 必须显式传入：can_assign 缺省走上海真实时钟取周窗口，而种子的
        # monday 锚自 UTC——上海已周一/UTC 仍周日的时段（北京 0~8 点）种子整体
        # 落在窗口外，used 恒 0。钉同一时钟后与挂钟无关。
        ok, used, _ = pts.can_assign(db, "s-1", now=now)
        assert ok and used == 1
        # 第二条送达 → 达上限
        db.add(LearningPlanDraft(
            draft_id="pd-b", student_id="s-1", version=1, status="sent",
            content={"plan_title": "b"}, created_by="parent:p-1", created_at=now))
        db.commit()
        ok2, used2, msg2 = pts.can_assign(db, "s-1", now=now)
        assert not ok2 and used2 == 2 and "每周" in msg2
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ── 捉虫 #5（P2）：地图「练习此点」不再双开（map 页无 start 调用）──────

def test_map_page_delegates_start_to_practice_page():
    """KnowledgeMapPage 不直接调 /smart/start（双开根因）；只带参转场。"""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] /
           "frontend/src/pages/mobile/student/KnowledgeMapPage.tsx").read_text(
               encoding="utf-8")
    assert "smart/start" not in src
    assert "state: { smartKp: kp }" in src
