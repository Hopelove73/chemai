"""decide_plan_draft（ADR-0029 批④，spec #22）：学生计划确认门工具编排测试。

service 层（plan_draft_service）已有自己的测试面（#9 批次）；这里 monkeypatch
service 钉**工具编排层**：归属校验 / action 映射（revise→reject+feedback）/
approve+send 同单元 / 错误透传。"""
import json

import pytest


class _FakeDraftService:
    """record/compute/send 的最小假件；按脚本注入返回。"""
    def __init__(self):
        self.drafts = {}          # draft_id -> draft dict（含 student_id）
        self.calls = []

    def get_draft(self, db, draft_id):
        return self.drafts.get(draft_id)

    def record_decision(self, db, draft_id, action, actor_id, feedback="", computed=None):
        self.calls.append(("record", draft_id, action, actor_id, feedback))
        if self.record_error:
            return {"error": self.record_error}
        return {"draft_id": draft_id, "status": action}

    def compute_revision(self, db, draft_id, feedback):
        self.calls.append(("compute", draft_id, feedback))
        if self.compute_error:
            return {"error": self.compute_error}
        return {"revision": "v2"}

    def send_draft(self, db, student_id, draft_id, sender_id):
        self.calls.append(("send", draft_id, student_id))
        if self.send_error:
            return {"error": self.send_error}
        return {"success": True}

    record_error = ""
    compute_error = ""
    send_error = ""


@pytest.fixture
def fake_svc(monkeypatch):
    from app.services import plan_draft_service as svc_mod
    svc = _FakeDraftService()
    svc.drafts["d-1"] = {"draft_id": "d-1", "student_id": "202610101"}
    monkeypatch.setattr(svc_mod.plan_draft_service, "get_draft", svc.get_draft)
    monkeypatch.setattr(svc_mod.plan_draft_service, "record_decision", svc.record_decision)
    monkeypatch.setattr(svc_mod.plan_draft_service, "compute_revision", svc.compute_revision)
    monkeypatch.setattr(svc_mod.plan_draft_service, "send_draft", svc.send_draft)
    yield svc


def _call(draft_id="d-1", action="approve", comment=""):
    import asyncio
    from agent.tools.context import set_current_identity
    from agent.tools.plan import decide_plan_draft
    set_current_identity("202610101", "student")
    try:
        return json.loads(asyncio.run(
            decide_plan_draft(draft_id=draft_id, action=action, comment=comment)))
    finally:
        set_current_identity(None)


def test_non_student_rejected(fake_svc, patch_get_db):
    import asyncio
    from agent.tools.context import set_current_identity
    from agent.tools.plan import decide_plan_draft
    set_current_identity("t-1", "teacher")
    try:
        out = json.loads(asyncio.run(decide_plan_draft("d-1", "approve")))
    finally:
        set_current_identity(None)
    assert "只面向学生本人" in out["error"]


def test_other_students_draft_rejected(fake_svc, patch_get_db):
    out = _call(draft_id="someone-else")
    assert "找不到你的计划草稿" in out["error"]
    assert fake_svc.calls == [], "归属拒绝后不得触碰 service"


def test_approve_records_and_sends_in_one_unit(fake_svc, patch_get_db):
    out = _call(action="approve")
    assert out["action"] == "approve" and "已确认" in out["text"]
    kinds = [c[0] for c in fake_svc.calls]
    assert kinds == ["record", "send"]


def test_approve_send_error_rolls_back_message(fake_svc, patch_get_db):
    fake_svc.send_error = "发送被门拒绝"
    out = _call(action="approve")
    assert "error" in out


def test_revise_requires_comment(fake_svc, patch_get_db):
    out = _call(action="revise", comment="")
    assert "修改意见" in out["error"]
    assert fake_svc.calls == []


def test_revise_computes_first_then_records(fake_svc, patch_get_db):
    out = _call(action="revise", comment="太难了，先从氧化还原开始")
    assert out["action"] == "revise"
    kinds = [c[0] for c in fake_svc.calls]
    assert kinds == ["compute", "record"], kinds
    assert fake_svc.calls[1][4] == "太难了，先从氧化还原开始"


def test_service_error_passthrough(fake_svc, patch_get_db):
    fake_svc.record_error = "该草稿已是终态"
    out = _call(action="approve")
    assert out["error"] == "该草稿已是终态"


def test_invalid_action(fake_svc, patch_get_db):
    out = _call(action="abandon")
    assert "approve" in out["error"] and "revise" in out["error"]
    assert fake_svc.calls == []
