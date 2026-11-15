"""KB-T17 评测 CLI：金问题集 → 向量召回命中率 + 应拒答正确率 + 逐题清单。

用法：EMBEDDING_CACHE_DIR=<dir> python -m evals.kb_eval [--top N] [--threshold F]
指标（设计 §6）：命中 ≥80%（本集 65 题 → 52）、应拒答 ≥4/5。
命中判定：expected 中任一文档（按文件名）出现在 top-N 结果的 source_path 中。
  不比较目录前缀：M1 语料历史上有带/不带 `必修一/` 前缀两种 source_path
  （摄取入口 path_prefix 行为不一致），按前缀比会产生假 miss；文档名在
  M1/M2 词典里唯一，按文件名判定才反映真实召回。
应拒答判定：全部结果距离 > 拒答线（miss）才算正确——模型端纪律由 prompt 保证，
评测只验检索层。
KB4a 三态口径：--threshold 是强命中线（默认 0.39），--weak-threshold 是拒答线
（默认 0.45）；命中题落在弱命中带（0.39~0.45）记"弱命中"，单列观察但计入命中率。
应拒答口径（KB4a 起）：出界题落入拒答线以上记"拒答"；落入弱带记"weak 拦截"
（模型会声明覆盖较浅、给 hedge 答案——这是弱命中带的设计内行为，不算硬答错）。
闸门只拦"自信作答"（mode=ok 且带资料）：拒答 + weak 拦截 ≥80%。801 块语料的
实测依据：超纲化学题（晶体场 0.392、sp3d 0.394）与语料共享深层词汇，距离与
覆盖内擦线题（43 道在 0.35~0.42）交错，任何单一拒线都无法既拒它们又保命中率。
"""
import json
import sys
import time
from pathlib import Path

KB_JSONL = Path(__file__).resolve().parent / "kb-questions.jsonl"


def _doc_hit(expected: list, srcs: list) -> bool:
    """expected 与召回结果按文件名比对（忽略目录前缀）。"""
    names = {Path(s).name for s in srcs}
    return any(Path(e).name in names for e in expected)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser("kb_eval")
    ap.add_argument("--top", type=int, default=4)
    ap.add_argument("--threshold", type=float, default=0.39,
                    help="强命中线，默认与 KB_MISS_THRESHOLD 一致")
    ap.add_argument("--weak-threshold", type=float, default=0.45,
                    help="拒答线，默认与 KB_WEAK_THRESHOLD 一致")
    ap.add_argument("--jsonl", default=str(KB_JSONL))
    args = ap.parse_args(argv)

    from app.services.kb_ingest.embed import LocalEmbedder
    from app.services.kb_context import build_kb_context
    from app.core.config import settings
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from app.models.database import get_engine

    # kb_context 的编码器协议是 encode(texts) → 向量列表（SentenceTransformer 同形）；
    # LocalEmbedder 的批量接口叫 embed——这里包一层，评测与生产同协议。
    class _Encoder:
        def __init__(self):
            self._inner = LocalEmbedder()

        def encode(self, texts, **kw):
            return self._inner.embed(texts)

    items = [json.loads(l) for l in open(args.jsonl, encoding="utf-8") if l.strip()]
    client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH,
                                       settings=ChromaSettings(anonymized_telemetry=False))
    names = {c.name for c in client.list_collections()}
    if "kb_chunks" not in names:
        print("[ERR] kb_chunks 集合不存在——先跑摄取")
        return 2
    col = client.get_collection("kb_chunks")
    encoder = _Encoder()
    graph = get_engine()

    hit_ok, weak_ok, miss_ok, hedged_ok, fail_hits = 0, 0, 0, 0, []
    rows = []
    started = time.time()
    for it in items:
        out = build_kb_context(it["question"], collection=col, encoder=encoder,
                               graph_engine=graph, top_k=args.top,
                               miss_threshold=args.threshold,
                               weak_threshold=args.weak_threshold)
        srcs = [h["source_path"] for h in out["hits"]]
        if not it["expected"]:
            ok = out["mode"] != "ok"          # 拒答或 weak 拦截都算挡住（不得自信作答）
            miss_ok += ok and out["mode"] == "miss"
            hedged_ok += ok and out["mode"] == "weak"
        else:
            ok = out["mode"] in ("ok", "weak") and _doc_hit(it["expected"], srcs)
            hit_ok += ok and out["mode"] == "ok"
            weak_ok += ok and out["mode"] == "weak"
            if not ok:
                fail_hits.append({"id": it["id"], "question": it["question"],
                                  "expected": it["expected"], "got": srcs,
                                  "mode": out["mode"]})
        rows.append({"id": it["id"], "ok": ok, "mode": out["mode"],
                     "got": srcs[:2]})
    total_ms = int((time.time() - started) * 1000)

    n_hit = sum(1 for i in items if i["expected"])
    n_miss = len(items) - n_hit
    rate = (hit_ok + weak_ok) / n_hit if n_hit else 0.0
    mrate = (miss_ok + hedged_ok) / n_miss if n_miss else 0.0
    print("=" * 64)
    print(f"金问题集评测：{len(items)} 题（命中 {n_hit} + 应拒 {n_miss}）"
          f" | 总耗时 {total_ms}ms | 平均 {total_ms // max(1, len(items))}ms/题")
    print(f"命中(强)：{hit_ok}/{n_hit}  命中(弱)：{weak_ok}/{n_hit}"
          f"（弱带 {args.threshold}~{args.weak_threshold}，单列观察）")
    print(f"命中合计：{hit_ok + weak_ok}/{n_hit} = {rate:.0%}  （阈值 ≥80%）"
          f"→ {'✓' if rate >= 0.8 else '✗'}")
    print(f"应拒拦截：{miss_ok + hedged_ok}/{n_miss} = {mrate:.0%}（阈值 ≥80%）"
          f"→ {'✓' if mrate >= 0.8 else '✗'}"
          f"  [纯拒答 {miss_ok} / weak 拦截 {hedged_ok}]")
    if fail_hits:
        print("---- 未命中的题 ----")
        for f in fail_hits:
            print(f"  {f['id']} {f['question'][:24]} | 期望 {f['expected']} | 得到 {f['got'][:2]} ({f['mode']})")
    print("=" * 64)
    ok_all = rate >= 0.8 and mrate >= 0.8
    print("结果：绿 —— 检索层达标" if ok_all else "结果：红 —— 未达标，先看上面的未命中清单")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
