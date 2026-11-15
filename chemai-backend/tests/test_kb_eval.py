"""KB2e 金集评测：命中判定与题面自洽的钉子。

命中按文档文件名比对（忽略 source_path 目录前缀）——M1 语料历史上有
带/不带 `必修一/` 前缀两种 source_path，按前缀比会产生假 miss。
"""
import json
from pathlib import Path

from evals.kb_eval import _doc_hit

KB = Path(__file__).resolve().parents[1] / "content" / "kb"
JSONL = Path(__file__).resolve().parents[1] / "evals" / "kb-questions.jsonl"


def test_doc_hit_ignores_source_prefix():
    assert _doc_hit(["必修一/气体摩尔体积.md"], ["气体摩尔体积.md"])
    assert _doc_hit(["必修二/硫单质.md"], ["必修二/硫单质.md"])
    assert not _doc_hit(["必修二/硫单质.md"], ["二氧化硫.md"])


def test_gold_set_is_self_consistent():
    """102 命中 + 8 应拒；期望文档都在五册+专题方法语料里，kp_id 都落词典（应拒用占位码）。"""
    from app.services.kb_dictionary import load_all_dictionaries
    known = {it["code"] for it in load_all_dictionaries(str(KB))["items"]}
    docs = {p.name for sub in ("必修一", "必修二", "选择性必修一", "选择性必修二", "选择性必修三", "专题方法")
            for p in (KB / sub).glob("*.md")}
    rows = [json.loads(l) for l in JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]

    hits = [r for r in rows if r["expected"]]
    refusals = [r for r in rows if not r["expected"]]
    assert (len(hits), len(refusals)) == (102, 8)
    for r in hits:
        assert all(Path(e).name in docs for e in r["expected"]), r["id"]
        assert r["kp_id"] in known, r["id"]
    for r in refusals:
        assert r["kp_id"] == "M1-T2-KP99", r["id"]
