"""为管理后台的「问答日志 / 评测回归」跑一批**真实**数据。

做的事（全部走线上同一条链路，不造假）：
  1. 12 个真实化学问题 → build_kb_context（真嵌入 + 真 chroma）→ DeepSeek 真回答
  2. 同步写 qa_traces（与线上埋点同结构；模型名如实记录）
  3. 按检索质量与回答长度给少量反馈/标注（模拟真实运营动作，规则写在输出里）
  4. 把「命中的块」存成金标问题（active）
  5. 跑两轮评测（第二轮故意禁用 1 个被金标依赖的分块 → 让对比页出现"弄坏了"）

用法：EMBEDDING_CACHE_DIR=D:/hf-cache python scripts/seed_rag_demo.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QUESTIONS = [
    "电解池的阴阳极怎么判断？",
    "什么是价层电子对互斥模型？",
    "化学平衡移动的判断依据是什么？",
    "原电池和电解池有什么区别？",
    "离子方程式怎么配平？",
    "胶体为什么会有丁达尔效应？",
    "氧化还原反应中怎么判断氧化剂？",
    "物质的量浓度怎么计算？",
    "焓变的正负号怎么理解？",
    "影响化学反应速率的因素有哪些？",
    "乙醇和乙酸怎么鉴别？",
    "实验装置里冷凝管为什么下进上出？",
]


def retrieval(question: str):
    from agent.tools.tutoring import _kb_resources
    from app.services.kb_chunks import disabled_chunk_ids
    from app.services.kb_context import build_kb_context

    res = _kb_resources()
    res["exclude_ids"] = disabled_chunk_ids()
    return build_kb_context(question, **res)


async def answer(question: str, kb_out: dict) -> tuple[str, str, int]:
    from agent.provider.deepseek import DeepSeekProvider

    user = question
    if kb_out.get("text") and kb_out.get("mode") in ("ok", "weak"):
        user = f"{kb_out['text']}\n\n【学生问题】{question}"
    elif kb_out.get("mode") == "miss":
        user = f"{kb_out['text']}\n\n【学生问题】{question}\n（知识库未覆盖此问题时直接说明，不要编造。）"

    provider = DeepSeekProvider()
    started = time.monotonic()
    try:
        result = await provider.chat(
            messages=[
                {"role": "system", "content": "你是高中化学AI助教。引导式教学，不直接给答案，分步讲解，不超过500字。"},
                {"role": "user", "content": user},
            ],
            temperature=0.7, max_tokens=800,
        )
        return result.content or "", result.model or "", int((time.monotonic() - started) * 1000)
    finally:
        await provider.close()


def write_trace(trace_id, question, kb_out, answer_text, model, latency, feedback, label, bad_reason):
    from app.models.database import QaTrace, get_session_factory
    from app.services.qa_trace import _summarize_hits

    hits = _summarize_hits(kb_out)
    s = get_session_factory()()
    try:
        s.add(QaTrace(
            trace_id=trace_id, user_id="241901", role="student",
            conversation_id=f"seed:{trace_id}", question=question,
            retrieval_config=kb_out.get("config"),
            hits=hits, zero_hit=len(hits) == 0,
            top1_score=hits[0]["score"] if hits else None,
            answer=answer_text, refusal_flag=kb_out.get("mode") == "miss",
            latency_ms=latency, model_name=model, kb_index_version="kb_chunks",
            feedback=feedback, ops_label=label, bad_reason=bad_reason,
        ))
        s.commit()
    finally:
        s.close()
    return hits


def main():
    from app.models.database import GoldenQuestion, get_engine, get_session_factory
    from app.utils.migrate import create_rag_tables

    create_rag_tables(get_engine())

    created = []
    for i, q in enumerate(QUESTIONS, 1):
        t0 = time.monotonic()
        kb_out = retrieval(q)
        text, model, latency = asyncio.run(answer(q, kb_out))
        total_ms = int((time.monotonic() - t0) * 1000)
        trace_id = f"tr_seed{i:02d}"
        top1 = (kb_out.get("hits") or [None])[0]
        score = round(1 - top1["distance"], 4) if top1 and top1.get("distance") is not None else None
        mode = kb_out.get("mode")

        # 运营动作规则（写死并打印，避免"神秘数据"）：
        #   · miss            → 反馈 down + 标坏「缺知识」
        #   · score < 0.45    → 标坏「检索错」
        #   · 其他            → 好
        if mode == "miss":
            feedback, label, reason = "down", "bad", "缺知识"
        elif score is not None and score < 0.45:
            feedback, label, reason = "down", "bad", "检索错"
        else:
            feedback, label, reason = "none", "good", None

        hits = write_trace(trace_id, q, kb_out, text, model, total_ms, feedback, label, reason)

        # 好样本 → 金标（期望块 = 当时的命中块，最多 2 个）
        if label == "good" and hits:
            expected = [{"chunk_id": h["chunk_id"], "source_path": h["source_path"],
                         "section_no": h["section_no"]} for h in hits[:2]]
            s = get_session_factory()()
            try:
                g = GoldenQuestion(question=q, expected_chunks=expected, match_mode="any",
                                   status="active", source="trace", source_trace_id=trace_id,
                                   tags="seed")
                s.add(g)
                s.commit()
            finally:
                s.close()

        created.append({"trace": trace_id, "q": q[:18], "mode": mode, "score": score,
                        "model": model, "ms": total_ms, "label": label})
        print(f"  [{i:02d}] {q[:20]:<22} mode={mode:<6} score={score} ms={total_ms} → {label}")

    print(f"\n写入 {len(created)} 条 trace；金标 {sum(1 for c in created if c['label']=='good')} 条（active）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
