"""一次性清洗 questions.difficulty 的枚举名/标签脏数据 → 1~5 int。

背景（E2E E7 实测）：早期写入方把 Difficulty 枚举名（'MEDIUM' 等）原样
存进 Integer 列，SQLite 不校验类型照单全收；每日练习/智能练习组卷复制
原样传播，1.7 万行全脏 —— smart/start 的 int(difficulty) 直接 500。
写入侧已由 DifficultyColumn（TypeDecorator）兜底，本脚本清历史行。幂等：
typeof(difficulty)='integer' 的行不再触碰。其他库（影子测试库）建表即走
新列类型，无需跑本脚本。

用法：python scripts/migrate_difficulty_names.py [db_path]  # 缺省 chemai.db
"""
import sqlite3
import sys

NAME_TO_VALUE = {"EASY": 1, "MODERATE": 2, "MEDIUM": 3, "HARD": 4, "COMPETITION": 5}
LABEL_TO_VALUE = {"easy": 1, "moderate": 2, "medium": 3, "hard": 4, "competition": 5}


def main(path: str) -> None:
    db = sqlite3.connect(path)
    rows = db.execute(
        "SELECT question_id, difficulty FROM questions "
        "WHERE difficulty IS NOT NULL AND typeof(difficulty) != 'integer'"
    ).fetchall()
    updated = 0
    for qid, raw in rows:
        text = str(raw).strip()
        value = NAME_TO_VALUE.get(text.upper()) or LABEL_TO_VALUE.get(text.lower())
        if value is None:
            print(f"[skip] 未识别难度 {qid}: {raw!r}（保留原值，人工确认）")
            continue
        db.execute("UPDATE questions SET difficulty = ? WHERE question_id = ?",
                   (value, qid))
        updated += 1
    db.commit()
    db.close()
    print(f"[done] {path}: 清洗 {updated}/{len(rows)} 行 → int")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "chemai.db")
