# -*- coding: utf-8 -*-
"""用当前 embedding 模型重建真题向量库（私有工具，不入交付源码）

它做的正是启动时那一次该做的事，区别只在这里强制重建：
`python _datagen/reindex_vectors.py`。换 embedding 型号、或者向量库读不动之后跑一次。

流程：备份 data/chromadb → 读 historical_exams → 全量编码替换 → 抽样看检索结果
"""
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)   # 产品代码多处用相对路径（data/chromadb 等），统一以 backend 为 CWD


def backup_chroma() -> str:
    """把旧目录整个复制一份再动手。目标已存在就停下 —— 不覆盖任何不是本次生成的东西。"""
    src = Path("data/chromadb")
    if not src.exists():
        return "(没有 data/chromadb，无需备份)"
    dst = src.parent / f"chromadb.bak_pseudo_{datetime.now():%Y%m%d_%H%M%S}"
    if dst.exists():
        raise SystemExit(f"备份目标已存在，拒绝覆盖：{dst}")
    shutil.copytree(src, dst)
    return str(dst)


QUERIES = [
    ("钠与水反应的离子方程式", ["金属及其化合物"]),
    ("化学平衡常数与转化率", ["化学反应速率与化学平衡"]),
    ("电解池中阳极产物判断", ["电化学"]),
]


def main():
    import app.services.vector_search as vs

    svc = vs.VectorSearchService()
    print(f"型号：{vs.EMBEDDING_MODEL}")
    print(f"重建前：{svc.collection.count()} 条，判据 = {svc._stale_reason() or '可用'}")
    print(f"备份到：{backup_chroma()}")

    rows = svc.iter_indexable_questions()
    kps = sum(len(r["knowledge_points"]) for r in rows)
    print(f"来源：historical_exams 共 {len(rows)} 题 / {kps} 个考点")

    t0 = datetime.now()
    count = svc.index_questions(rows, mode="replace")
    print(f"编码 {count} 条向量，用时 {(datetime.now() - t0).total_seconds():.0f}s")

    print(f"重建后：{svc.collection.count()} 条，判据 = {svc._stale_reason() or '可用'}")
    for text, kpl in QUERIES:
        hits = svc.search_similar(text, kpl, "medium", limit=3)
        print(f"\n查询「{text}」")
        for h in hits[:3]:
            print(f"  {h['similarity']:.3f}  {h.get('kp_name', ''):<14} "
                  f"{h.get('source', '')} {h['exam_id']}")


if __name__ == "__main__":
    main()
