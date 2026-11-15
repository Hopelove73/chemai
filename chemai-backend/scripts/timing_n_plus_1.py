"""N+1 专项基线/复测脚本（只读，不动库）。

用法：
    cd chemai-backend && python scripts/timing_n_plus_1.py

对真库 chemai.db 直接计时 student_tasks / build_cards 两处装配，
输出查询条数（SQL 语句计数）与耗时，供性能专项前后对比。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import event  # noqa: E402

from app.models.database import Student, get_session_factory  # noqa: E402


def main() -> None:
    query_count = {"n": 0}

    def _count_before(conn, cursor, statement, parameters, context, executemany):
        query_count["n"] += 1

    SessionLocal = get_session_factory()
    engine = SessionLocal.kw["bind"]
    event.listen(engine, "before_cursor_execute", _count_before)

    db = SessionLocal()
    try:
        student = db.query(Student).filter(Student.student_id == "241901").first()
        if not student:
            print("student 241901 not found")
            return
        print(f"student: {student.student_id} {student.name} class={student.class_id}")

        from app.api.practice import student_tasks
        from app.services.parent_summary_service import build_cards

        # warm-up（建表元数据/连接已热后测稳态）
        student_tasks(db, student)

        for label, fn in (
            ("student_tasks", lambda: student_tasks(db, student)),
            ("build_cards", lambda: build_cards(db, student.student_id)),
        ):
            query_count["n"] = 0
            t0 = time.perf_counter()
            rows = fn()
            dt = (time.perf_counter() - t0) * 1000
            print(f"{label:14s} {dt:8.1f} ms  SQL queries={query_count['n']:5d}  rows={len(rows) if isinstance(rows, list) else 'dict'}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
