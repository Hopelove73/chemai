"""KB4c-A 题库回填：含镁独立题补打「镁及其化合物」标签（幂等）。

口径：只动 knowledge_points 里没带该标签的行；audit_report 追加批次记录。
真源是 questions 表本身，重跑不重复打标。
"""
import json
import sqlite3

TAG = "镁及其化合物"
MARK = "kb4c-A 回填"


def main() -> int:
    db = sqlite3.connect("chemai.db")
    rows = db.execute(
        "SELECT question_id, knowledge_points, audit_report FROM questions "
        "WHERE content LIKE '%镁%' AND knowledge_points IS NOT NULL AND json_valid(knowledge_points)"
    ).fetchall()
    touched = 0
    for qid, raw, report in rows:
        tags = json.loads(raw)
        if TAG in tags:
            continue
        tags.append(TAG)
        r = json.loads(report) if report else {}
        hist = r.setdefault("backfill_history", [])
        if MARK not in hist:
            hist.append(MARK)
        db.execute(
            "UPDATE questions SET knowledge_points = ?, audit_report = ? WHERE question_id = ?",
            (json.dumps(tags, ensure_ascii=False), json.dumps(r, ensure_ascii=False), qid))
        print(f"[BACKFILL] {qid} -> {tags}")
        touched += 1
    db.commit()
    # 验证
    n = db.execute(
        "SELECT COUNT(*) FROM questions WHERE knowledge_points LIKE '%镁及其化合物%'").fetchone()[0]
    print(f"本次回填 {touched} 行；库中带「{TAG}」标签 {n} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
