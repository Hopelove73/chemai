"""讲评稿数据源 —— review_deck 的 context_data 自动补全（ADR-0027 注册表的读侧）。

老师那句「用这次考试做讲评稿」里，批改数据**本来就已经在库里**：要么是已保存成绩的
作答行（StudentAnswer），要么是刚批完还没保存的答题卡结果（OCRTask.grading_result）。
过去 context_data 声明为必填 + 缺参 fail-closed，等于让系统向老师要它自己已经握着的
数据（docs/agentchat-任务对话清单.md §1.3 第 9 条）。这里补上"自己取"这一段。

只读：不写库、不调模型。产出 REVIEW_DECK_PROMPT 真正消费的三块（见
app/services/teaching_skills/__init__.py 的 REVIEW_DECK_PROMPT：数据必须忠于输入，
输入缺某维度就明说未覆盖）：
  整体概览（人数/均分正确率/正确率带）→ 错题分布（按知识点）→ 典型错题
  （题干摘要/高频错选/正解）。

取不到返回 None，由调用方保持原来的 fail-closed —— 这里**不猜**：宁可向老师要，
也不能拿一场不存在或者不是本次的考试顶上去（讲评稿是学科论断型产物）。
"""
import json
from collections import Counter, defaultdict


# 正确率带：讲评课先讲整体位置再进错题，按学生正确率分三档（与 REVIEW_DECK_PROMPT
# 第 ② 节「整体概览（均分/正确率带）」对齐）
_BANDS = ((60.0, "正确率低于60%（重点补差）"),
          (80.0, "正确率60~79%（巩固为主）"),
          (101.0, "正确率80%以上（拔高延伸）"))

# 典型错题只取错得最多的这些道：讲评课一节课讲不完整张卷，全列反而没有动线
_MAX_ERROR_QUESTIONS = 10
_MAX_WRONG_ANSWERS = 3      # 每道题列前 3 个高频错选
_STEM_LIMIT = 120           # 题干摘要上限：讲评稿要的是"认得出是哪道题"


def latest_graded_context(db, scope):
    """该老师最近一次已完成批改的数据 → {"context_data": JSON 串, "source": 来源说明}。

    "最近"按批改动作发生的时间比：已保存成绩看考试日期，答题卡批改看任务创建时间。
    两条来源都查、取更新的那条——老师刚批完一批卷（还没保存）时，不能被上周保存的
    那场考试顶掉；反过来保存了新的成绩也不该还讲三个月前那批卡。

    scope 必传（身份由 adapter 从 token 解析）：空范围直接 None，学生/家长/无身份
    拿不到任何考试与批次（与判卷中心 fail-closed 同一口径）。
    """
    if not scope.caller_id:
        return None
    candidates = [c for c in (_from_saved_exam(db, scope), _from_ocr_batch(db, scope)) if c]
    if not candidates:
        return None
    best = max(candidates, key=lambda c: c["sort_key"])
    return {
        "context_data": json.dumps(best["payload"], ensure_ascii=False),
        "source": best["source"],
    }


# ── 来源一：已保存的考试成绩（StudentAnswer + Question）─────────────────

def _from_saved_exam(db, scope):
    from app.models.database import Class, ExamRecord, Question, StudentAnswer
    from app.services import grading_center

    class_ids = sorted(scope.class_ids)
    if not class_ids:
        return None

    # 生效行过滤（确认门 + 未判排除）与诊断/finalize 共用同一口径：未确认的成绩
    # 老师的讲评课根本不该讲，未判的行（ADR-0018：缺标准答案/待语义判定）更不是错题。
    rows = (
        db.query(StudentAnswer, Question, ExamRecord)
        .join(Question, Question.question_id == StudentAnswer.question_id)
        .join(ExamRecord, ExamRecord.record_id == StudentAnswer.exam_record_id)
        .filter(ExamRecord.class_id.in_(class_ids), grading_center.effective_conditions())
        .all()
    )
    if not rows:
        return None

    by_exam = {}
    for answer, question, exam in rows:
        by_exam.setdefault(exam.record_id, (exam, []))[1].append((answer, question))
    exam, graded = max(by_exam.values(),
                       key=lambda pair: _stamp(pair[0].exam_date or pair[0].created_at))

    klass = db.query(Class).filter(Class.class_id == exam.class_id).first()
    class_name = (klass.name if klass else "") or ""
    payload = _build_payload(
        source=f"已保存成绩的考试《{exam.name or exam.record_id}》"
               + (f"（{class_name}）" if class_name else ""),
        exam_meta={
            "exam_record_id": exam.record_id,
            "name": exam.name or "",
            "class_name": class_name,
            "exam_date": str(exam.exam_date or exam.created_at or ""),
            "saved": True,
        },
        graded=[{
            "student_id": answer.student_id,
            "question_id": question.question_id,
            "is_correct": bool(answer.is_correct),
            "student_answer": answer.student_answer or "",
            "correct_answer": question.answer or "",
            "stem": _clip(question.content),
            "question_type": question.question_type or "",
            "knowledge_points": question.knowledge_points or [],
            "barrier_type": _enum_value(answer.barrier_type) or question.probe_dimension or "",
        } for answer, question in graded],
    )
    return {"sort_key": _stamp(exam.exam_date or exam.created_at),
            "payload": payload, "source": payload["source"]}


# ── 来源二：答题卡批改结果（已批未保存 / 未关联考试）────────────────────

def _from_ocr_batch(db, scope):
    from app.models.ocr_task import OCRTask
    from app.services import grading_center

    owners = scope.visible_batch_owners()
    if not owners:
        return None
    tasks = (
        db.query(OCRTask)
        .filter(OCRTask.teacher_id.in_(owners), OCRTask.grading_result.isnot(None))
        .order_by(OCRTask.created_at.desc())
        .all()
    )
    if not tasks:
        return None

    # 按批次聚：同一批的不同卡是同一次批改动作
    by_batch = defaultdict(list)
    for t in tasks:
        by_batch[t.batch_id].append(t)
    batch_id, batch_tasks = max(by_batch.items(), key=lambda kv: _batch_stamp(kv[1]))

    graded = []
    for t in batch_tasks:
        for q in ((t.grading_result or {}).get("questions") or []):
            # 未判不参与统计（ADR-0018）：未判 ≠ 答错，混进来会把讲评稿的重点带偏
            if grading_center.is_unjudged(q.get("reason")):
                continue
            graded.append({
                "student_id": t.student_id or t.task_id,
                "question_id": str(q.get("q_number") or ""),
                "is_correct": bool(q.get("is_correct")),
                "student_answer": q.get("student_answer") or "",
                "correct_answer": q.get("correct_answer") or "",
                "stem": "",
                "question_type": "",
                "knowledge_points": [],
                "barrier_type": "",
            })
    if not graded:
        return None

    latest = _latest_dt(batch_tasks)
    payload = _build_payload(
        source=f"答题卡批改结果（批次 {batch_id}，尚未保存进成绩）",
        exam_meta={"batch_id": batch_id, "saved": False,
                   "graded_at": str(latest or "")},
        graded=graded,
    )
    # 答题卡批次本身不带试卷关联（OCRTask 无 exam_id 列），题干/知识点/错因都取不到，
    # 按模板「输入缺某维度就明说未覆盖」如实标注，别让模型编出知识点排序。
    payload["data_gaps"] = ["本批未关联考试题库：没有题干摘要、知识点分布与错因归类"]
    return {"sort_key": _stamp(latest), "payload": payload, "source": payload["source"]}


# ── 两种来源共用的归一 ────────────────────────────────────────────────

def _build_payload(*, source, exam_meta, graded):
    total = len(graded)
    correct = sum(1 for r in graded if r["is_correct"])

    per_student = defaultdict(lambda: [0, 0])
    for r in graded:
        acc = per_student[r["student_id"]]
        acc[0] += 1 if r["is_correct"] else 0
        acc[1] += 1
    bands = {label: 0 for _, label in _BANDS}
    for c, t in per_student.values():
        pct = c / t * 100 if t else 0.0
        for upper, label in _BANDS:
            if pct < upper:
                bands[label] += 1
                break

    per_question = {}
    barriers = Counter()
    for r in graded:
        key = r["question_id"] or r["stem"] or ""
        q = per_question.setdefault(key, {
            "question_id": key, "stem": r["stem"], "question_type": r["question_type"],
            "knowledge_points": r["knowledge_points"], "correct_answer": r["correct_answer"],
            "answered": 0, "wrong": 0, "wrong_answers": Counter(),
        })
        q["answered"] += 1
        if not r["is_correct"]:
            q["wrong"] += 1
            wrong = (r["student_answer"] or "").strip()
            if wrong:
                q["wrong_answers"][wrong] += 1
            if r["barrier_type"]:
                barriers[r["barrier_type"]] += 1

    error_questions = []
    for q in per_question.values():
        if not q["wrong"]:
            continue
        error_questions.append({
            "question_id": q["question_id"],
            "stem": q["stem"],
            "question_type": q["question_type"],
            "knowledge_points": q["knowledge_points"],
            "correct_answer": q["correct_answer"],
            "answered": q["answered"],
            "wrong": q["wrong"],
            "correct_rate_pct": round((q["answered"] - q["wrong"]) / q["answered"] * 100, 1),
            "wrong_answers": [
                {"answer": a, "count": n}
                for a, n in q["wrong_answers"].most_common(_MAX_WRONG_ANSWERS)
            ],
        })
    error_questions.sort(key=lambda x: (-x["wrong"], x["question_id"]))
    error_questions = error_questions[:_MAX_ERROR_QUESTIONS]

    # 错题分布按知识点排序（同一条知识点被多道题共用时合并计数）
    kp = {}
    for q in per_question.values():
        for point in (q["knowledge_points"] or []):
            slot = kp.setdefault(point, {"knowledge_point": point, "wrong": 0, "answered": 0})
            slot["wrong"] += q["wrong"]
            slot["answered"] += q["answered"]
    knowledge = sorted(
        [{"knowledge_point": k, "wrong": v["wrong"], "answered": v["answered"],
          "correct_rate_pct": round((v["answered"] - v["wrong"]) / v["answered"] * 100, 1)}
         for k, v in kp.items() if v["wrong"]],
        key=lambda x: (-x["wrong"], x["knowledge_point"]),
    )

    payload = {
        "source": source,
        "exam": exam_meta,
        "overview": {
            "students": len(per_student),
            "answer_rows": total,
            "correct_rows": correct,
            "avg_accuracy_pct": round(correct / total * 100, 1) if total else 0.0,
            "score_bands": bands,
        },
        "error_questions": error_questions,
        "knowledge_point_errors": knowledge,
    }
    if barriers:
        # 错因归类（概念理解/审题/表述）来自判卷落行时的障碍判定，没有就整段不出现，
        # 免得模型把空表当"都在概念理解"讲
        payload["error_reasons"] = dict(barriers.most_common())
    return payload


def _clip(text):
    text = (text or "").strip()
    return text if len(text) <= _STEM_LIMIT else text[:_STEM_LIMIT] + "…"


def _latest_dt(tasks):
    return max(((t.processed_at or t.created_at) for t in tasks
                if (t.processed_at or t.created_at)), default=None)


def _batch_stamp(tasks):
    return _stamp(_latest_dt(tasks))


def _stamp(dt):
    """时间戳排序键：None 排最旧（SQLite/PG 两种取数都可能给 None）。"""
    try:
        return dt.timestamp() if dt else 0.0
    except AttributeError:
        return 0.0


def _enum_value(value):
    return getattr(value, "value", value) or ""
