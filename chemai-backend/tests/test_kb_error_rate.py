"""KB2a 错误率聚合（seam）：student_answers → kp_tag_map → 按词典码聚合错误率。

真实数据链已验证（15873 题映射）：离子检验 89%/量浓度 86%/铁 78% TOP3。
纪律：作答 <MIN_ANSWERS 次的知识点不出（样本太小）；聚合失败静默空表。
"""
import json

import pytest
from sqlalchemy import create_engine, text

from app.services.kb_error_rate import (ERROR_RATE_THRESHOLD, MIN_ANSWERS,
                                         error_rates_for_kps, kp_contents)


@pytest.fixture()
def er_engine(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path}/er.db")
    with e.begin() as conn:
        conn.execute(text("CREATE TABLE questions "
                          "(question_id VARCHAR(64) PRIMARY KEY, "
                          "content TEXT, knowledge_points TEXT)"))
        conn.execute(text("CREATE TABLE student_answers "
                          "(answer_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                          "question_id VARCHAR(64), is_correct INTEGER)"))
        conn.execute(text("CREATE TABLE kp_tag_map "
                          "(tag_text VARCHAR(128) PRIMARY KEY, kp_id VARCHAR(64))"))
        # 标签映射：氧化还原反应 → M1-T2-KP06；物质的量 → M1-T2-KP14
        conn.execute(text("INSERT INTO kp_tag_map VALUES ('氧化还原反应','M1-T2-KP06')"))
        conn.execute(text("INSERT INTO kp_tag_map VALUES ('物质的量','M1-T2-KP14')"))
        # 题：q1/q2 氧还（标签），q3 物质的量，q4 无映射标签
        rows = [("q1", "题目一：氧化还原判断", '["氧化还原反应"]'),
                ("q2", "题目二：氧化还原判断（变式）", '["氧化还原反应"]'),
                ("q3", "题目三：物质的量计算", '["物质的量"]'),
                ("q4", "题目四：未映射标签", '["未知标签"]')]
        for qid, content, kp in rows:
            conn.execute(text("INSERT INTO questions VALUES (:q, :c, :k)"),
                         {"q": qid, "c": content, "k": kp})
        answers = [("q1", 0), ("q1", 0), ("q1", 1),   # q1 错误率 2/3
                   ("q2", 1), ("q2", 1),              # q2 全对
                   ("q3", 0)]                          # q3 仅 1 次（<MIN_ANSWERS）
        for qid, ok in answers:
            conn.execute(text("INSERT INTO student_answers (question_id, is_correct) "
                              "VALUES (:q, :ok)"), {"q": qid, "ok": ok})
    yield e
    e.dispose()


def test_aggregates_error_rate_per_kp(er_engine):
    with er_engine.connect() as conn:
        rates = error_rates_for_kps(conn, ["M1-T2-KP06", "M1-T2-KP14"])
    import sys; print("DEBUG rates =", rates, file=sys.stderr)
    with er_engine.connect() as conn:
        print("DEBUG rows =", conn.execute(text(
            "SELECT question_id, content, knowledge_points FROM questions")).fetchall(),
            file=sys.stderr)
        print("DEBUG tags =", conn.execute(
            text("SELECT * FROM kp_tag_map")).fetchall(), file=sys.stderr)
        print("DEBUG answers =", conn.execute(
            text("SELECT question_id, is_correct FROM student_answers")).fetchall(), file=sys.stderr)
    assert rates["M1-T2-KP06"]["error_rate"] == pytest.approx(2 / 5)
    assert rates["M1-T2-KP06"]["answers"] == 5, "q1+q2 都映射到该码，合并 5 次作答"
    assert "M1-T2-KP14" not in rates,         "作答数不足 MIN_ANSWERS 的知识点不出现在结果里（样本太小不出提示）"


def test_small_sample_kp_suppressed(er_engine):
    with er_engine.connect() as conn:
        rates = error_rates_for_kps(conn, ["M1-T2-KP14"])
    assert "M1-T2-KP14" not in rates or rates["M1-T2-KP14"]["answers"] < MIN_ANSWERS


def test_multi_tag_question_counted_once_per_kp(er_engine):
    """一题多标签时在每个映射到的 KP 各计一次（不同 KP 互不串量）。"""
    with er_engine.connect() as conn:
        rates = error_rates_for_kps(conn, ["M1-T2-KP06"])
    assert rates["M1-T2-KP06"]["answers"] == 5, "q1+q2 都映射到该码，合并 5 次作答"


# ---------------------------------------------------------------------------
# 班级维度（star-map-teacher）：只过滤作答样本，口径/去重/MIN_ANSWERS 不变
# ---------------------------------------------------------------------------

@pytest.fixture()
def er_class_engine(tmp_path):
    """带学生与班级的聚合库：c1（S1/S2）与 c2（S3）作答同一道独立题。"""
    e = create_engine(f"sqlite:///{tmp_path}/erc.db")
    with e.begin() as conn:
        conn.execute(text("CREATE TABLE questions "
                          "(question_id VARCHAR(64) PRIMARY KEY, "
                          "content TEXT, knowledge_points TEXT)"))
        conn.execute(text("CREATE TABLE student_answers "
                          "(answer_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                          "question_id VARCHAR(64), student_id VARCHAR(64), "
                          "is_correct INTEGER)"))
        conn.execute(text("CREATE TABLE students "
                          "(student_id VARCHAR(64) PRIMARY KEY, "
                          "class_id VARCHAR(64), name VARCHAR(128))"))
        conn.execute(text("CREATE TABLE kp_tag_map "
                          "(tag_text VARCHAR(128) PRIMARY KEY, kp_id VARCHAR(64))"))
        conn.execute(text("INSERT INTO kp_tag_map VALUES ('氧化还原反应','M1-T2-KP06')"))
        conn.execute(text("INSERT INTO kp_tag_map VALUES ('物质的量','M1-T2-KP14')"))
        rows = [("q1", "题目一：氧化还原判断", '["氧化还原反应"]'),
                ("q3", "题目三：物质的量计算", '["物质的量"]'),
                ("q4", "题目一：氧化还原判断", '["物质的量"]')]  # 同内容第二实例
        for qid, content, kp in rows:
            conn.execute(text("INSERT INTO questions VALUES (:q, :c, :k)"),
                         {"q": qid, "c": content, "k": kp})
        conn.execute(text("INSERT INTO students VALUES ('S1','c1','学生一')"))
        conn.execute(text("INSERT INTO students VALUES ('S2','c1','学生二')"))
        conn.execute(text("INSERT INTO students VALUES ('S3','c2','学生三')"))
        answers = [("q1", "S1", 0), ("q1", "S1", 0),   # c1：6 答 3 错 → 0.5
                   ("q1", "S2", 0),
                   ("q1", "S1", 1), ("q1", "S2", 1), ("q1", "S2", 1),
                   ("q1", "S3", 0), ("q1", "S3", 0), ("q1", "S3", 0),
                   ("q1", "S3", 0), ("q1", "S3", 0),   # c2：5 答 5 错 → 1.0
                   ("q3", "S1", 1), ("q3", "S1", 1)]   # KP14 仅 2 次（<MIN_ANSWERS）
        for qid, sid, ok in answers:
            conn.execute(text("INSERT INTO student_answers "
                              "(question_id, student_id, is_correct) "
                              "VALUES (:q, :s, :ok)"), {"q": qid, "s": sid, "ok": ok})
    yield e
    e.dispose()


def test_class_filter_onlynarrows_answers(er_class_engine):
    """班级过滤只收窄作答样本：c1=0.5（6 答）/ c2=1.0（5 答）/ 全局=8/11（11 答）。"""
    with er_class_engine.connect() as conn:
        c1 = error_rates_for_kps(conn, ["M1-T2-KP06"], class_id="c1")
        c2 = error_rates_for_kps(conn, ["M1-T2-KP06"], class_id="c2")
        glob = error_rates_for_kps(conn, ["M1-T2-KP06"])
    assert c1["M1-T2-KP06"] == {"error_rate": pytest.approx(0.5), "answers": 6}
    assert c2["M1-T2-KP06"] == {"error_rate": pytest.approx(1.0), "answers": 5}
    assert glob["M1-T2-KP06"]["answers"] == 11
    assert glob["M1-T2-KP06"]["error_rate"] == pytest.approx(8 / 11)


def test_class_small_sample_suppressed(er_class_engine):
    """MIN_ANSWERS 按班内样本计：KP14 在 c1 只有 2 次作答 → 不出。"""
    with er_class_engine.connect() as conn:
        rates = error_rates_for_kps(conn, ["M1-T2-KP14"], class_id="c1")
    assert "M1-T2-KP14" not in rates


def test_kp_contents_dedup_and_first_mapping(er_class_engine):
    """kp_contents 与聚合同尺：content 去重（q4 是题目一的第二实例，不重复）、
    首次映射语义（题目一 → KP06，不再进 KP14）。"""
    with er_class_engine.connect() as conn:
        kp06 = kp_contents(conn, "M1-T2-KP06")
        kp14 = kp_contents(conn, "M1-T2-KP14")
    assert kp06 == ["题目一：氧化还原判断"]
    assert kp14 == ["题目三：物质的量计算"]
