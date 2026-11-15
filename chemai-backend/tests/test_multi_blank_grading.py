"""多空判卷（student-daily-loop X1 / REQ-CMP-004）。

split_answer_blanks（统一解析：`;` 分隔 / JSON 数组 / 单空）+
_compare_blanks（逐空归一化、错空定位、空数不一致退回整体比较）。
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.models.database import normalize_question_type, split_answer_blanks
from app.services.grading_center import _compare_blanks


def test_split_semicolon_blanks():
    assert split_answer_blanks("A; B；C") == ["A", "B", "C"]


def test_split_json_array():
    assert split_answer_blanks('["x","y"]') == ["x", "y"]


def test_split_single_stays_whole():
    assert split_answer_blanks("2NaOH") == ["2NaOH"]


def test_split_none_and_list():
    assert split_answer_blanks(None) == [""]
    assert split_answer_blanks(["a", "b"]) == ["a", "b"]


def test_compare_per_blank_normalization():
    ok, wrong = _compare_blanks("Fe2O3; MnO2", "Fe₂O₃; MnO₂")
    assert ok and wrong == []


def test_compare_locates_wrong_blank():
    ok, wrong = _compare_blanks("2KClO3; MnO2; O2", "2KClO3; MnO2; 3O2")
    assert not ok and wrong == [3]


def test_compare_blank_count_mismatch_falls_back_whole():
    # 旧数据单空整题写法：空数不一致 → 整体比较（与迁移前行为一致）
    ok, wrong = _compare_blanks("a;b", "a;b")
    assert ok
    ok2, _ = _compare_blanks("ab", "a;b")
    assert not ok2


def test_normalize_question_type_aliases():
    assert normalize_question_type("choice") == "single_choice"
    assert normalize_question_type("multiple_choice") == "multi_choice"
    assert normalize_question_type("fill") == "fill_blank"
    assert normalize_question_type("unknown-x") == "single_choice"
    assert normalize_question_type(None) == "single_choice"
