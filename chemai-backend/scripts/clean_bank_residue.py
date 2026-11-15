# -*- coding: utf-8 -*-
"""题库残留清理：① historical_exams 里收藏集同步写进来的 AI 生成题（source='AI生成'）
② nat_2020_* 旧 id 方案的 2020 全国卷重复行（现行 id 为 national2020_T*）
③ 测试收藏集（question_sets，is_system=0 且无 teacher_id）连带 items
④ 上述收藏集遗留的孤儿题（questions.record_id IS NULL）及答题/记忆卡/校准引用

保留 bank_kb4c_* —— content/question_bank/expansion-*.json 导入的扩容题，record_id 本就为空。

用法：python scripts/clean_bank_residue.py            # dry-run
      python scripts/clean_bank_residue.py --apply    # 真跑
"""
import argparse
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy import text
from app.models.database import get_db

AI_SOURCE = "AI生成"
OLD_ID_PREFIX = "nat_2020_"
KEEP_PREFIX = "bank_kb4c_"
ORPHAN_LIKE = ("student_answers", "memory_cards", "question_calibration", "question_set_items")

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

db = next(get_db())
try:
    q = lambda sql, **kw: db.execute(text(sql), kw).scalar()

    ai_rows = q("SELECT COUNT(*) FROM historical_exams WHERE source = :s", s=AI_SOURCE)
    old_rows = q("SELECT COUNT(*) FROM historical_exams WHERE exam_id LIKE :p", p=OLD_ID_PREFIX + "%")
    set_rows = q("SELECT COUNT(*) FROM question_sets WHERE is_system = 0 AND teacher_id IS NULL")
    orphan_ids = [r[0] for r in db.execute(text(
        "SELECT question_id FROM questions WHERE record_id IS NULL AND question_id NOT LIKE :p"),
        {"p": KEEP_PREFIX + "%"})]
    kept = q("SELECT COUNT(*) FROM questions WHERE record_id IS NULL AND question_id LIKE :p",
             p=KEEP_PREFIX + "%")

    print(f"historical_exams: AI生成 {ai_rows} 行；{OLD_ID_PREFIX}* 旧 id {old_rows} 行")
    print(f"question_sets: 测试收藏集 {set_rows} 个")
    print(f"questions: 待删孤儿题 {len(orphan_ids)} 行（保留 {KEEP_PREFIX}* {kept} 行）")

    refs = {}
    for t in ORPHAN_LIKE:
        if not orphan_ids:
            refs[t] = 0
            continue
        n = 0
        for i in range(0, len(orphan_ids), 500):
            chunk = orphan_ids[i:i + 500]
            marks = ",".join(f":p{j}" for j in range(len(chunk)))
            params = {f"p{j}": v for j, v in enumerate(chunk)}
            n += q(f"SELECT COUNT(*) FROM {t} WHERE question_id IN ({marks})", **params)
        refs[t] = n
    print("关联行: " + "，".join(f"{t} {n}" for t, n in refs.items()))

    if args.apply:
        db.execute(text("DELETE FROM historical_exams WHERE source = :s"), {"s": AI_SOURCE})
        db.execute(text("DELETE FROM historical_exams WHERE exam_id LIKE :p"), {"p": OLD_ID_PREFIX + "%"})
        db.execute(text("""DELETE FROM question_set_items WHERE set_id IN (
            SELECT set_id FROM question_sets WHERE is_system = 0 AND teacher_id IS NULL)"""))
        db.execute(text("DELETE FROM question_sets WHERE is_system = 0 AND teacher_id IS NULL"))
        for i in range(0, len(orphan_ids), 500):
            chunk = orphan_ids[i:i + 500]
            marks = ",".join(f":p{j}" for j in range(len(chunk)))
            params = {f"p{j}": v for j, v in enumerate(chunk)}
            for t in ORPHAN_LIKE:
                db.execute(text(f"DELETE FROM {t} WHERE question_id IN ({marks})"), params)
            db.execute(text(f"DELETE FROM questions WHERE question_id IN ({marks})"), params)
        db.commit()
        print(f"已删：真题表 {ai_rows + old_rows} 行；收藏集 {set_rows} 个；孤儿题 {len(orphan_ids)} 行")
    else:
        print("（dry-run，未改动；加 --apply 生效）")
finally:
    db.close()
