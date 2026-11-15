"""知识地图工具（S6 / knowledge-edges REQ-KED-001..003）：教师提议-采纳-禁用。

证据统计（共现 + 错因迁移）为提议提供数据面；边内容由 LLM 依证据与学科常识
提议，教师逐边采纳后落库。无外部图谱数据（Q3 定案 B，NC 数据明确排除）。
"""
import json


async def propose_knowledge_edges(limit: int = 10) -> str:
    """提议知识点先修关系（只提议，不落库——采纳见 accept_knowledge_edges）

    何时用：老师要建知识点先修图，或说「帮我提议先修关系」
    会发生什么：统计两类证据（知识点共现、错因迁移），结合学科常识给出候选边
               （每条带依据一句话），以列表形式返回待老师确认
    下一步：老师逐边挑选后，把采纳的边交给 accept_knowledge_edges 落库
    NOT for 直接落库 — 本工具只提议，采纳必须显式调 accept_knowledge_edges"""
    from agent.tools.context import current_teacher
    from app.services.knowledge_map_service import evidence_stats

    if not (current_teacher() or ""):
        return json.dumps({"error": "缺少教师身份"}, ensure_ascii=False)
    db = next(_get_db())
    try:
        ev = evidence_stats(db, limit=min(max(int(limit), 5), 30))
    finally:
        db.close()
    evidence = json.dumps(ev, ensure_ascii=False)
    from agent.provider.deepseek import DeepSeekProvider
    provider = DeepSeekProvider(timeout=120.0)
    try:
        result = await provider.chat(
            messages=[
                {"role": "system", "content": (
                    "你是高中化学教研专家。根据给出的证据（知识点共现、错因迁移计数），"
                    "结合学科先修常识，提议知识点先修边（pre 应先于 post 学习）。"
                    "只输出 JSON 数组，每项 {\"pre\": \"知识点A\", \"post\": \"知识点B\", "
                    "\"reason\": \"依据一句话\"}，最多 12 条，宁缺毋滥；证据不足的边不要提。")},
                {"role": "user", "content": f"证据数据：{evidence}"},
            ],
            temperature=0.2, max_tokens=2000)
    finally:
        await provider.close()
    # provider.chat 返回的是 ChatResult **dataclass**，不是 dict：
    # 原来按 dict 取 content，先修图提议必然 AttributeError（2026-10 实测）。
    content = (result.get("content", "") if isinstance(result, dict)
               else getattr(result, "content", "") or "")
    try:
        if "```" in content:
            content = content.split("```json")[1].split("```")[0] if "```json" in content \
                else content.split("```")[1].split("```")[0]
        proposals = json.loads(content)
    except (ValueError, IndexError):
        proposals = []
    proposals = [p for p in proposals if isinstance(p, dict) and p.get("pre") and p.get("post")
                 and str(p.get("reason", "")).strip()]
    return json.dumps({"proposals": proposals, "evidence": ev}, ensure_ascii=False)


async def accept_knowledge_edges(edges: str = "") -> str:
    """采纳先修边（落库；成环的边会被拒并列出环路径）

    何时用：老师看过提议后明确说「采纳第 1、3 条」「就这两条可以」
    会发生什么：逐条环校验（含与已 active 边组合成环），通过落 active；
               重复边幂等（重新启用 disabled 边）
    下一步：告知采纳/拒绝结果；地图页即时生效
    NOT for 老师还没确认就直接调用 — 必须显式转达老师的采纳决定"""
    from agent.tools.context import current_teacher
    from app.services.knowledge_map_service import accept_edges

    teacher_id = current_teacher() or ""
    if not teacher_id:
        return json.dumps({"error": "缺少教师身份"}, ensure_ascii=False)
    try:
        parsed = json.loads(edges) if isinstance(edges, str) else edges
        if isinstance(parsed, dict):
            parsed = parsed.get("edges") or parsed.get("proposals") or []
    except ValueError:
        return json.dumps({"error": "edges 参数须为 JSON 数组"}, ensure_ascii=False)
    if not isinstance(parsed, list) or not parsed:
        return json.dumps({"error": "没有要采纳的边"}, ensure_ascii=False)
    db = next(_get_db())
    try:
        out = accept_edges(db, parsed, proposed_by=teacher_id)
        out["_route"] = {"navigate": False}
        return json.dumps(out, ensure_ascii=False)
    finally:
        db.close()


async def disable_knowledge_edge(edge_id: int = 0, enable: bool = False) -> str:
    """禁用（或重新启用）一条先修边——错边即时退出就绪过滤

    何时用：老师说「这条边不对，去掉」/「把第 3 条边恢复」
    会发生什么：边状态切 disabled/active，地图即时生效
    下一步：无需其他操作
    NOT for 删除历史 — 状态切换即可，不做物理删除"""
    from agent.tools.context import current_teacher
    from app.models.database import KnowledgeEdge

    if not (current_teacher() or ""):
        return json.dumps({"error": "缺少教师身份"}, ensure_ascii=False)
    db = next(_get_db())
    try:
        row = db.get(KnowledgeEdge, int(edge_id))
        if not row:
            return json.dumps({"error": f"边 {edge_id} 不存在"}, ensure_ascii=False)
        row.status = "active" if enable else "disabled"
        db.commit()
        return json.dumps({"success": True, "edge_id": edge_id, "status": row.status},
                          ensure_ascii=False)
    finally:
        db.close()


def _get_db():
    from app.models.database import get_db
    return get_db()
