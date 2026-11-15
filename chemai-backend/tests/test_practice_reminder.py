"""B4：家长自选「每日练习提醒」时点（§11）。

钉的是行为，不是字段：
  - 到点 + 当天练习没做完 → 落一条站内提醒；
  - 已经做完 / 时点不匹配 / 开关关 / 当天没推送练习 → 一条都不落；
  - 同一小时内被重复触发（调度器抖动）→ 仍只有一条；
  - 偏好写入要挡住离谱时点（凌晨催作业）。
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.api.parent import NotifyPrefsRequest, get_notify_prefs, set_notify_prefs
from app.models.database import (
    ExamRecord, Parent, ParentNotification, Question, RecordType, Student,
    StudentAnswer, StudentParentBinding,
)
import app.scheduler.daily_practice as dp

CST = ZoneInfo("Asia/Shanghai")
TODAY = datetime(2026, 10, 20, 19, 0, tzinfo=CST)
PRACTICE_ID = "daily_s1_20261020"


@pytest.fixture
def env(db_session, monkeypatch):
    db_session.add(Student(student_id="s1", name="张三", class_id="c1", status="approved"))
    db_session.add(Parent(parent_id="p1", name="家长一", password_hash="x",
                         notify_prefs={"practice_reminder": {"enabled": True, "hour": 19}}))
    db_session.add(StudentParentBinding(binding_id=1, student_id="s1", parent_id="p1",
                                        status="active", bind_code="bc1"))
    db_session.add(ExamRecord(record_id=PRACTICE_ID, class_id="c1", type=RecordType.PRACTICE,
                              name="每日练习", exam_date=TODAY))
    for i in range(1, 4):
        db_session.add(Question(question_id=f"{PRACTICE_ID}_q{i:02d}",
                                record_id=PRACTICE_ID, content=f"题目{i}", difficulty=1))
    db_session.flush()
    # job 直接持有 get_db 的引用（模块导入时绑定），只patch app.models.database 不够
    monkeypatch.setattr(dp, "get_db", lambda: iter([db_session]))
    return db_session


def _answer(db, idx):
    db.add(StudentAnswer(answer_id=f"a{idx}", student_id="s1",
                         question_id=f"{PRACTICE_ID}_q{idx:02d}", is_correct=True))
    db.flush()


def test_reminder_lands_at_the_chosen_hour(env):
    _answer(env, 1)
    assert dp.practice_reminder_job(now=TODAY) == 1
    n = env.query(ParentNotification).one()
    assert "还没完成" in n.title
    assert "共 3 题，已答 1 题" in n.content
    # 同一小时重复触发 → 幂等
    assert dp.practice_reminder_job(now=TODAY) == 0
    assert env.query(ParentNotification).count() == 1
    # 换个时点也不再发（今天这条已经发过了）
    assert dp.practice_reminder_job(now=datetime(2026, 10, 20, 20, 0, tzinfo=CST)) == 0


def test_no_reminder_outside_the_chosen_hour(env):
    _answer(env, 1)
    assert dp.practice_reminder_job(now=datetime(2026, 10, 20, 18, 0, tzinfo=CST)) == 0
    assert env.query(ParentNotification).count() == 0


def test_no_reminder_when_practice_finished(env):
    for i in (1, 2, 3):
        _answer(env, i)
    assert dp.practice_reminder_job(now=TODAY) == 0
    assert env.query(ParentNotification).count() == 0


def test_no_reminder_when_switch_off(env):
    _answer(env, 1)
    env.query(Parent).filter(Parent.parent_id == "p1").update(
        {"notify_prefs": {"practice_reminder": {"enabled": False, "hour": 19}}})
    env.flush()
    assert dp.practice_reminder_job(now=TODAY) == 0


def test_no_reminder_when_nothing_was_pushed_today(env):
    env.query(Question).filter(Question.record_id == PRACTICE_ID).delete()
    env.query(ExamRecord).filter(ExamRecord.record_id == PRACTICE_ID).delete()
    env.flush()
    assert dp.practice_reminder_job(now=TODAY) == 0
    assert env.query(ParentNotification).count() == 0


def test_prefs_roundtrip_and_validation(env):
    out = asyncio.run(set_notify_prefs(
        NotifyPrefsRequest(practice_reminder={"enabled": True, "hour": 20}),
        db=env, parent_id="p1"))
    assert out["prefs"]["practice_reminder"] == {"enabled": True, "hour": 20}
    got = asyncio.run(get_notify_prefs(db=env, parent_id="p1"))
    assert got["prefs"]["practice_reminder"]["hour"] == 20

    for bad in ({"hour": 3}, {"hour": 24}, {"hour": "abc"}, {"enabled": "yes"}):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(set_notify_prefs(
                NotifyPrefsRequest(practice_reminder=bad), db=env, parent_id="p1"))
        assert exc.value.status_code == 400