"""C3/C5（§11）：读工具必须把「下一步该调什么」写进同一条结果。

这两条的失败形态一样——模型拿到列表/队列就停在「汇报」，没去做那件真正要做的
事（删题库进确认门 / 成绩确认）。修法不是再加一段提示词，而是把下一步要用的
id 直接放进工具返回值：话术落在做决定的当口。断言是内容级的（点名工具 + id）。
"""
import json

import pytest

import app.models.database as db_mod
import agent.tools.context as tool_ctx
import app.services.grading_center as gc
from agent.tools.exam_bank import list_banks
from agent.tools import ocr_grading


@pytest.fixture
def db(monkeypatch, db_session):
    monkeypatch.setattr(db_mod, "get_db", lambda: iter([db_session]))
    return db_session


@pytest.mark.asyncio
async def test_list_banks_points_at_delete_bank(db):
    """C3：删题库的请求只调了 list_banks 就停 → 列表里要自带「接着调 delete_bank」。"""
    out = json.loads(await list_banks())
    assert "delete_bank" in out["next_step"]
    assert "不要只把列表报给老师就停" in out["next_step"]
    assert out["banks"] == [] and out["total"] == 0


@pytest.mark.asyncio
async def test_grading_queue_next_step_names_confirm_with_exam_id(monkeypatch, db):
    """C5：队列里有待确认成绩 → 下一步点名 confirm_exam_grades 并带上 exam_id。"""
    monkeypatch.setattr(tool_ctx, "current_teacher", lambda: "t1")
    monkeypatch.setattr(ocr_grading, "_caller_scope", lambda _db: object())
    monkeypatch.setattr(
        gc, "grading_queue",
        lambda *a, **kw: {"pending_confirm": [],
                          "unconfirmed_exams": [{"exam_record_id": "ex_1", "name": "月考"}],
                          "recent_saved": 0})
    out = json.loads(await ocr_grading.query_grading_queue())
    assert "confirm_exam_grades" in out["next_step"]
    assert "ex_1" in out["next_step"]
    assert "不要停在汇报队列这一步" in out["next_step"]


@pytest.mark.asyncio
async def test_grading_queue_next_step_names_save_when_only_batches_wait(monkeypatch, db):
    """没有待确认成绩、只有答题卡批 → 指向 save_grading_results 而不是确认。"""
    monkeypatch.setattr(tool_ctx, "current_teacher", lambda: "t1")
    monkeypatch.setattr(ocr_grading, "_caller_scope", lambda _db: object())
    monkeypatch.setattr(
        gc, "grading_queue",
        lambda *a, **kw: {"pending_confirm": [{"batch_id": "b_9", "tasks": 2}],
                          "unconfirmed_exams": [], "recent_saved": 0})
    out = json.loads(await ocr_grading.query_grading_queue())
    assert "save_grading_results" in out["next_step"] and "b_9" in out["next_step"]


@pytest.mark.asyncio
async def test_grading_queue_empty_has_no_next_step(monkeypatch, db):
    """队列空的时候别硬塞下一步，免得模型凭空去确认一场不存在的考试。"""
    monkeypatch.setattr(tool_ctx, "current_teacher", lambda: "t1")
    monkeypatch.setattr(ocr_grading, "_caller_scope", lambda _db: object())
    monkeypatch.setattr(
        gc, "grading_queue",
        lambda *a, **kw: {"pending_confirm": [], "unconfirmed_exams": [], "recent_saved": 3})
    out = json.loads(await ocr_grading.query_grading_queue())
    assert "next_step" not in out