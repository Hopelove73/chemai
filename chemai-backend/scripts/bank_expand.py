"""题库扩容导入（KB4c）：content/question_bank/expansion-*.json → questions 表。

口径（KB4 计划修订版）：只看内容级去重后的独立题。本脚本：
  · 按规范化 content 去重（strip + 全角空格折叠）——与库中 34,330 行实例比，
    重复内容跳过，不产生新的"重复实例"；
  · 校验每题：4 个选项、答案在 ABCD、tags 全部落在 kp_tag_map（路由得到 KP）；
  · 行形状照既有 AI_GENERATED 行（source/audit_status 用枚举名字符串，
    difficulty 用整数 1~5，audit 四维自审全 1）；
  · 幂等：question_id 已存在或 content 已存在则跳过。
用法：python scripts/bank_expand.py [--dry-run]
"""
import glob
import json
import sqlite3
import sys
from pathlib import Path

BATCH_ID = "bank_kb4c"
KB_DIR = Path(__file__).resolve().parents[1] / "content" / "question_bank"
VALID = set("ABCD")


def norm(text: str) -> str:
    return " ".join((text or "").replace("\u3000", " ").split())


def main(dry_run: bool = False) -> int:
    db = sqlite3.connect("chemai.db")
    existing = {norm(c) for (c,) in db.execute(
        "SELECT content FROM questions WHERE content IS NOT NULL")}
    known_ids = {r[0] for r in db.execute("SELECT question_id FROM questions")}
    tag2kp = dict(db.execute("SELECT tag_text, kp_id FROM kp_tag_map").fetchall())

    files = sorted(glob.glob(str(KB_DIR / "expansion-*.json")))
    if not files:
        print(f"[ERR] 没有批次文件: {KB_DIR}")
        return 2

    inserted, skipped_dup, bad = 0, 0, []
    for f in files:
        batch = json.loads(Path(f).read_text(encoding="utf-8"))
        prefix = batch.get("batch_id", BATCH_ID)
        for i, q in enumerate(batch["questions"], 1):
            problems = []
            if len(q.get("options", [])) != 4:
                problems.append("选项数≠4")
            if q.get("answer") not in VALID:
                problems.append("答案非法")
            if not q.get("analysis"):
                problems.append("缺解析")
            routes = {}
            for t in q.get("tags", []):
                if t in tag2kp:
                    routes[t] = tag2kp[t]
                else:
                    problems.append(f"标签 {t!r} 无路由")
            if problems:
                bad.append((q.get("content", "?")[:30], problems))
                continue
            content = q["content"]
            if norm(content) in existing:
                skipped_dup += 1
                continue
            qid = f"{prefix}_{i:03d}"
            if qid in known_ids:
                skipped_dup += 1
                continue
            print(f"[NEW] {qid} {content[:36]}… tags={q['tags']} -> "
                  f"{sorted(set(routes.values()))}")
            if not dry_run:
                db.execute(
                    "INSERT INTO questions (question_id, record_id, content, options, "
                    "answer, analysis, knowledge_points, probe_dimension, difficulty, "
                    "source, source_exam, audit_status, audit_report, historical_matches, "
                    "coefficient_correct, condition_correct, product_correct, "
                    "structure_correct, question_type) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (qid, None, content,
                     json.dumps(q["options"], ensure_ascii=False),
                     q["answer"], q["analysis"],
                     json.dumps(q["tags"], ensure_ascii=False),
                     None, int(q["difficulty"]), "AI_GENERATED", None, "PASSED",
                     json.dumps({"self_audit": "LLM 起草自查通过（系数/条件/产物/结构），待老师抽审",
                                 "batch": prefix}, ensure_ascii=False),
                     None, 1, 1, 1, 1, "single_choice"))
            inserted += 1
    if bad:
        print("---- 校验失败 ----")
        for c, p in bad:
            print(f"  {c} | {'; '.join(p)}")
    print(f"\n合计：新题 {inserted}，内容重复跳过 {skipped_dup}，校验失败 {len(bad)}")
    if not dry_run and inserted:
        db.commit()
        n = db.execute("SELECT COUNT(DISTINCT content) FROM questions").fetchone()[0]
        print(f"库中独立题内容数 -> {n}")
    db.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
