"""CLI：python -m app.services.kb_ingest --module 必修一 [--fake]

--fake 用确定性伪向量离线冒烟（校验/切块/幂等全真，仅无语义）。
默认用本地 bge 编码器（vector_search 单例，1024 维）；模型未配置时
报错并指路，不悄悄降级——"假向量当真入库"是本项目踩过的坑。
"""
import argparse
import json
import sys
from pathlib import Path

KB_DIR = Path(__file__).resolve().parents[3] / "content" / "kb"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser("kb_ingest")
    ap.add_argument("--module", default="", help="只摄取该册目录（默认全量）")
    ap.add_argument("--fake", action="store_true", help="确定性伪向量冒烟")
    ap.add_argument("--kb-dir", default=str(KB_DIR))
    args = ap.parse_args(argv)

    from app.services.kb_dictionary import load_all_dictionaries
    data = load_all_dictionaries(str(KB_DIR))
    known = {it["code"] for it in data["items"]}

    kb_dir = Path(args.kb_dir)
    if args.module:
        kb_dir = kb_dir / args.module
    if not kb_dir.is_dir():
        print(f"[ERR] 目录不存在: {kb_dir}")
        return 2

    if args.fake:
        from .embed import FakeEmbedder
        embedder = FakeEmbedder(dim=32)
    else:
        from .embed import LocalEmbedder
        try:
            embedder = LocalEmbedder()
        except Exception as e:
            print(f"[ERR] 本地嵌入模型不可用：{e}\n"
                  "      设 EMBEDDING_MODEL_DIR/EMBEDDING_CACHE_DIR 后重试，"
                  "或用 --fake 做无语义冒烟。")
            return 2

    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from app.core.config import settings
    path = settings.CHROMA_DB_PATH
    Path(path).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=path,
                                       settings=ChromaSettings(anonymized_telemetry=False))
    existing = {c.name for c in client.list_collections()}
    if "kb_chunks" in existing:
        collection = client.get_collection("kb_chunks")
    else:
        collection = client.create_collection(
            "kb_chunks", metadata={"hnsw:space": "cosine",
                                   "embedding_model": "fake" if args.fake else "local-bge"})

    from .ingest import ingest_directory
    report = ingest_directory(kb_dir, collection=collection, embedder=embedder,
                              known_codes=known, module=args.module)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
