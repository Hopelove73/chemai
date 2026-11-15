"""判卷标准答案的另一条通路 —— exam_id → 题库里的标准答案（只读）。

`agent/tools/ocr_grading.py` 的 docstring 一直写着「判卷前必须先向老师要标准答案」，
但那只是 prompt 约定、没有任何代码强制（docs/agentchat-任务对话清单.md §1.3 第 6 条）。
而 exam_id 指向的真考试（ExamRecord → Question.answer）本来就带着标准答案，判分内核
`llm_grading.grade_batch_answers` 也早就会在只有 exam_id 时自动取题库。

这里补的是**入口处的判定**：让「题库自动取」与「老师粘贴」两条通路并存——能解析出
标准答案就不该再反问老师；解析不到才交还给老师，并且给得出可选路径（候选考试）。
判分内核仍只有一处（ADR-0012），本模块不复制题号约定、不复制 options→choice 映射。
"""


def resolve_exam_answer_key(db, scope, exam_id):
    """exam_id → 标准答案可用性：ok 时给来源说明与可用题数，否则给待补原因。

    归属校验与 `grading_center.grade_ocr_batch` 同一口径（考试 class_id 必须在调用者
    范围内），否则老师能拿别班的答案批自己的卡。
    """
    exam_id = (exam_id or "").strip()
    if not exam_id:
        return {"ok": False, "reason": "未指定考试"}

    from app.models.database import ExamRecord, Question

    exam = db.query(ExamRecord).filter(ExamRecord.record_id == exam_id).first()
    if exam is None:
        return {"ok": False, "error": f"考试记录不存在：{exam_id}"}
    if not scope.allows_exam_class(exam.class_id):
        return {"ok": False, "error": "无权用该考试的答案批改：不在你的班级范围内"}

    questions = db.query(Question).filter(Question.record_id == exam_id).all()
    answer_count = sum(1 for q in questions if (q.answer or "").strip())
    name = exam.name or exam_id
    if not answer_count:
        # 建了考试但题库没填答案：仍然要问老师，别让整批卡以「未判」收场
        return {
            "ok": False,
            "exam": {"exam_id": exam.record_id, "name": name},
            "error": f"考试《{name}》题库里没有标准答案（{len(questions)} 道题都没填答案），"
                     f"请把答案原文传给 correct_answers",
        }
    return {
        "ok": True,
        "exam": {"exam_id": exam.record_id, "name": name},
        "answer_count": answer_count,
        "source": f"标准答案取自题库考试《{name}》（{answer_count} 题）",
    }


def candidate_exams(db, scope, limit=5):
    """调用者班级范围内最近的考试 + 各自可用的标准答案题数 —— 老师没指定考试时的选项。

    只读、按班级范围收窄（空范围返回空列表，fail-closed）；题数是给老师挑的线索，
    精确解析仍由 `resolve_exam_answer_key` 做。
    """
    from sqlalchemy import func

    from app.models.database import Class, ExamRecord, Question

    class_ids = sorted(scope.class_ids)
    if not class_ids:
        return []
    rows = (
        db.query(ExamRecord, Class)
        .join(Class, Class.class_id == ExamRecord.class_id)
        .filter(ExamRecord.class_id.in_(class_ids))
        .order_by(ExamRecord.exam_date.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return []
    ids = [exam.record_id for exam, _ in rows]
    counts = dict(
        db.query(Question.record_id, func.count(Question.question_id))
        .filter(Question.record_id.in_(ids),
                Question.answer.isnot(None), Question.answer != "")
        .group_by(Question.record_id)
        .all()
    )
    return [{
        "exam_id": exam.record_id,
        "name": exam.name or "",
        "class_name": klass.name or "",
        "answer_count": counts.get(exam.record_id, 0),
    } for exam, klass in rows]
