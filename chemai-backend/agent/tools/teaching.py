"""教学技能入口工具（ADR-0027：agent 唯一能看到的教学内容生产接口）。"""
import json
import re

from dotenv import load_dotenv

load_dotenv()


async def invoke_teaching_skill(
    skill: str = "",
    topic: str = "",
    requirements: str = "",
    context_data: str = "",
    scene: str = "",
    student_sentences: str = "",
) -> str:
    """调用教学技能生成内容工件（动画/课件/讲评稿等），产物实时显示在右侧工件面板

    何时用：老师要「做个教学动画」「生成讲评稿/教案/评语」这类**成块教学内容**，
    或用了 /动画 /讲评 斜杠指令
    会发生什么：按技能模板生成自包含 HTML 工件 → 落盘存版本 → 右侧面板实时预览，
    返回的 artifact 引用会出现在消息流卡片上
    下一步：老师会在面板里看效果并提出修改 → 把修改要求作为 requirements 再次调用
    本工具（同一 skill + 同一主题会自动追加新版本）；动画涉及反应式可先调
    balance_equation 核对配平；讲评稿缺 context_data 时本工具会按老师身份自动取
    **最近一次已完成批改**的数据（已保存成绩 / 答题卡批改结果）并在回复里说明取自
    哪次考试；库里确实没有才需要老师提供，且没有数据不要编
    NOT for 简单的化学问答（chemistry_tutor）、出题（generate_questions）、
    写一句话解释——这些直接回答即可，不要套技能"""
    from agent.provider.deepseek import DeepSeekProvider
    from app.services import artifact_service
    from app.services.teaching_skills import get_skill
    from app.services.teaching_skills import REGISTRY
    from agent.tools.context import current_identity

    skill_def = get_skill(skill)
    if skill_def is None:
        return json.dumps({
            "error": f"未知技能: {skill!r}",
            "available": sorted(REGISTRY),
            "hint": f"可用技能：{_skill_menu()}",
        }, ensure_ascii=False)

    # 必填参数按技能声明拦截（ADR-0027：注册即扩展，分发层不认识具体技能名）
    _args = {"topic": topic, "requirements": requirements, "context_data": context_data,
             "scene": scene, "student_sentences": student_sentences}
    missing = [p for p in skill_def.required_params if not (_args.get(p) or "").strip()]
    # 讲评数据本来就在库里（已保存成绩 / 答题卡批改结果）：先自己取，取不到才回到
    # fail-closed 向老师要。系统没有理由向老师索要它自己已经握着的数字。
    data_source = ""
    if "context_data" in missing:
        filled = _autofill_context_data()
        if filled:
            context_data = filled["context_data"]
            data_source = filled["source"]
            missing = [p for p in missing if p != "context_data"]
    if missing:
        hint = ("讲评稿需要真实批改数据：已按你的身份查过最近一次批改（已保存成绩 / "
                "答题卡批改结果），库里没有。请老师指明是哪次考试（可先用 "
                "list_teacher_exams 列候选），或先完成一轮批改。"
                if "context_data" in missing else "")
        return json.dumps({
            "error": f"技能 {skill} 缺必填参数: {missing}。{hint}",
        }, ensure_ascii=False)

    # 输入钳制：超长输入会吃模型的 400（不可重试）→ 裸异常。上限远超正常用法。
    topic = (topic or "").strip()[:200]
    requirements = (requirements or "").strip()[:4000]
    context_data = (context_data or "").strip()[:80000]

    identity = current_identity()
    teacher_id = identity.get("user_id") or ""
    role = (identity.get("role") or "").lower()
    if not teacher_id:
        # 写路径 fail-closed（ADR-0009/0014 惯例）：没有身份不落盘，
        # 否则所有匿名调用共享一个幽灵 owner 桶，版本还会互相续接。
        return json.dumps({
            "error": "没有取得用户身份，无法保存工件。请从工作台发起对话。",
        }, ensure_ascii=False)

    user_content = f"教学主题：{topic or skill_def.title}"
    if requirements:
        user_content += f"\n老师附加要求（优先满足）：{requirements}"
    if context_data:
        user_content += f"\n批改数据 JSON（已过审核链，直接引用其中的数字）：\n{context_data}"
    if scene:
        user_content += f"\n训练场景：{scene}"
    if student_sentences:
        user_content += f"\n学生分句作答 JSON（逐句标注的对象，原样引用）：\n{student_sentences}"

    # 16k 非流式最坏约 74s（guard.py 实测口径），provider 默认 60s 读超时会掐断
    # 后再重试两次满等——大预算生成必须显式放宽超时。
    provider = DeepSeekProvider(timeout=180.0)
    _msgs = [
        {"role": "system", "content": skill_def.prompt_template},
        {"role": "user", "content": user_content},
    ]
    try:
        result = await provider.chat(
            messages=_msgs,
            temperature=0.4,
            max_tokens=16384,  # 一份完整 HTML 动画的预算（同对话主回路的 16k 口径）
        )
        html = _extract_html(result.content)
        if not html.strip():
            # C9（§11 真机实测）：空产出是一次性失败——同一技能第二轮就成功。
            # 与其甩一张红卡让老师换个说法重来，这里自己重试一次（只在失败时才多花
            # 一次模型调用）。仍然空才报错，并把这次的形状说清楚：上一版只让老师
            # 「换个说法重试」，等于让他替我们猜。
            retry = await provider.chat(messages=_msgs, temperature=0.2, max_tokens=16384)
            html = _extract_html(retry.content)
            if not html.strip():
                return json.dumps({
                    "error": ("模型连续两次没有给出可用的 HTML 文档（"
                              + _html_miss_reason(retry.content)
                              + "）。可以缩小主题范围再试，或改做讲评要点文字稿。"),
                }, ensure_ascii=False)
    finally:
        await provider.close()

    from app.models.database import Artifact, get_db
    db = next(get_db())
    try:
        # 同 owner + kind + skill + 同主题标题 → 追加新版本（修订迭代是快照，R6）；
        # 换主题或换技能则是新工件。带 skill 过滤：doc 类将来会有教案/评语等多个
        # 技能共用 kind，不按 skill 过滤会互相续版本。
        title = topic or skill_def.title
        existing = (
            db.query(Artifact)
            .filter(Artifact.owner_teacher_id == teacher_id,
                    Artifact.kind == skill_def.artifact_kind,
                    Artifact.source_skill == skill_def.name,
                    Artifact.title == title)
            .first()
        )
        saved = artifact_service.save_artifact(
            db, owner_teacher_id=teacher_id, kind=skill_def.artifact_kind,
            title=title, content=html,
            artifact_id=existing.id if existing else None,
            source_skill=skill_def.name)
    finally:
        db.close()

    version_note = f"已更新到 v{saved['version_seq']}" if not saved["created"] \
        else f"已生成（v{saved['version_seq']}）"

    # 表述特训（批 2 S5b / REQ-EXP-003）：解析文档尾部的机器判定块，
    # 命中 ≥2 类毛病的句子生成记忆卡（正面=场景问题，背面=标准表述）。
    # 建卡失败不阻断工件返回（与判卷建卡钩子同口径：失败不丢工件）。
    cards_created = 0
    if skill_def.name == "expression_training" and role == "student":
        try:
            m = re.search(
                r'<script[^>]*id="exp-verdicts"[^>]*>([\s\S]*?)</script>', html)
            if m:
                verdicts = json.loads(m.group(1).strip())
                from app.models.database import get_db as _gdb
                from app.services.memory_card_service import create_expression_card
                _db = next(_gdb())
                try:
                    scene_txt = scene or topic or skill_def.title
                    for i, v in enumerate(verdicts):
                        if not isinstance(v, dict) or v.get("pass", True):
                            continue
                        faults = v.get("faults") or []
                        scores = v.get("scores") or {}
                        low = [x for x in scores.values()
                               if isinstance(x, (int, float)) and x < 60]
                        if len(faults) < 2 and not low:
                            continue  # 建卡规则（REQ-EXP-003）：≥2 类毛病 或 任一栏 <60
                        create_expression_card(
                            _db, teacher_id,
                            prompt=f"【{scene_txt}】第{i + 1}句：请规范表述——{v.get('sentence', '')}",
                            model_answer=str(v.get("model_answer") or v.get("sentence", "")))
                        cards_created += 1
                finally:
                    _db.close()
        except Exception:
            cards_created = 0

    cards_note = f"，{cards_created} 个错句已进复习队列" if cards_created else ""
    text = (f"「{saved['title']}」{version_note}，右侧面板可预览。{cards_note}"
            f"要修改的话直接说改哪里（如「换浅色底」「加个暂停按钮」），我会出新一版。")
    payload = {
        "text": text,
        "artifact": {
            "artifact_id": saved["artifact_id"],
            "kind": saved["kind"],
            "title": saved["title"],
            "version_seq": saved["version_seq"],
            "url": saved["url"],
        },
    }
    if data_source:
        # 自动补来的数据必须让老师看见来源：讲评稿「数据忠于输入」，来源不明就没法核对
        payload["data_source"] = data_source
        payload["text"] = f"讲评稿的数据来源：{data_source}。{text}"
    return json.dumps(payload, ensure_ascii=False)


def _autofill_context_data() -> dict | None:
    """按服务端身份取该老师最近一次已完成批改的数据（review_deck 的 context_data）。

    只读、不猜：库里没有就返回 None，让调用方保持原来的 fail-closed 向老师要。
    取数失败一律降级为 None —— 补数据的旁支不该让整个讲评稿生成崩掉（同判卷建卡
    钩子口径：增强失败不阻断主流程，但留 warning 供排查）。
    """
    import logging

    from agent.tools.context import current_identity
    from app.models.database import get_db
    from app.services.grading_center import CallerScope
    from app.services.review_deck_context import latest_graded_context

    teacher_id = (current_identity().get("user_id") or "").strip()
    if not teacher_id:
        return None
    db = next(get_db())
    try:
        return latest_graded_context(db, CallerScope.from_user_id(db, teacher_id))
    except Exception:
        logging.getLogger(__name__).warning(
            "review_deck context autofill failed teacher=%s", teacher_id, exc_info=True)
        return None
    finally:
        db.close()


_HTML_START = r"(?:<!DOCTYPE|<html)"


def _extract_html(content: str) -> str:
    """从模型回复里取出文档本体。

    三档：① 围栏包裹（最常见，模型爱加一句「以下是您需要的 HTML 文档」）→ 剥围栏；
    ② 裸文档 → 截到 </html>，不把尾随客套话一起落盘；③ **有头无尾**（max_tokens 用尽
    被截断）→ 留着这半份：面板能渲染多少算多少，比甩一张「没有可用内容」的红卡把
    整份丢掉强（C9 实测就撞在这档上）。太短的碎片（<2000 字）仍判为失败。
    """
    text = (content or "").strip()
    if not text:
        return ""
    m = re.search(r"```(?:html)?\s*(" + _HTML_START + r"[\s\S]*</html>)\s*```", text, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(" + _HTML_START + r"[\s\S]*</html>)", text, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(" + _HTML_START + r"[\s\S]*)", text, re.I)
    if m and len(m.group(1).strip()) > 2000:
        return m.group(1).strip()
    return ""


def _html_miss_reason(content: str) -> str:
    """空产出时给一句能查的形状描述（C9：报错要能当证据用，不让老师替我们猜）。"""
    text = (content or "").strip()
    if not text:
        return "模型这次返回了空内容"
    head = text[:40].replace("\n", " ")
    return f"模型这次只回了 {len(text)} 字，开头是「{head}」，里面没有 HTML 文档起始标记"


async def share_artifact(artifact_id: str = "", student_id: str = "") -> str:
    """把工件分享给指定学生 — 分发走确认门，学生收件箱出现「老师分享」卡片

    何时用：老师看完工件（动画/课件/文档）说「发给XX」「分享给XX」
    会发生什么：系统先弹确认卡（对外分发必须过门）；批准后工件进学生收件箱，
    学生点开全屏预览（只能看，不能改）
    下一步：告知老师已发送；学生端在收件箱查看
    NOT for 发给家长（工件暂不支持家长端）；NOT for 发学习计划（send_learning_plan）"""

    from agent.tools.context import current_identity
    from app.models.database import Artifact, get_db
    from app.services.notification_service import NotificationService

    if not artifact_id or not student_id:
        return json.dumps({"error": "请提供 artifact_id 与 student_id"}, ensure_ascii=False)

    identity = current_identity()
    teacher_id = identity.get("user_id") or ""
    if not teacher_id:
        return json.dumps({"error": "没有取得教师身份，无法分享工件"}, ensure_ascii=False)

    db = next(get_db())
    try:
        art = (db.query(Artifact)
               .filter(Artifact.id == artifact_id,
                       Artifact.owner_teacher_id == teacher_id)
               .first())
        if art is None:
            return json.dumps({"error": f"工件不存在或不属于你: {artifact_id}"}, ensure_ascii=False)

        notification_id = NotificationService.send_to_student(
            student_id=student_id,
            notification_type="artifact_share",
            # send_to_student 不截断 title（P2-7），String(256) 列在 MySQL 严格
            # 模式下长标题会让门重放翻车——调用侧钳住
            title=f"老师分享了{_kind_label(art.kind)}：{art.title}"[:240],
            content=json.dumps({
                "artifact_id": art.id, "kind": art.kind, "title": art.title,
                "version": art.current_version,
            }, ensure_ascii=False),
            # 同一工件同一版本发给同一学生幂等（门重放/重复分享不重复投递）
            idempotency_key=f"artifact_share:{art.id}:v{art.current_version}",
        )

        # 家长动态（批 3 P1 / REQ-PFD-001）：分享成功 → 家长流「学习了《X》」卡
        # 不带版本细节（工件分发契约：家长只见事件，不见版本）；
        # db 关闭前执行（hook 用同一会话），失败不阻断分享
        try:
            from app.services.notification_service import notification_service
            notification_service.notify_student_parents(
                db, student_id, notification_service.FEED_ARTIFACT_SHARED,
                title=f"孩子学习了《{art.title}》",
                content=json.dumps({"kind": art.kind, "version": art.current_version},
                                   ensure_ascii=False),
                idempotency_key=f"feed_art_{artifact_id}_{student_id}")
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "parent feed dispatch failed artifact=%s student=%s",
                artifact_id, student_id, exc_info=True)
    finally:
        db.close()

    if notification_id is None:
        return json.dumps({"error": f"学生不存在: {student_id}"}, ensure_ascii=False)

    return json.dumps({
        "message": f"「{art.title}」已分享给学生（收件箱可见，点开可全屏预览）。",
        "shared": {"artifact_id": art.id, "student_id": student_id,
                   "version": art.current_version, "notification_id": notification_id},
    }, ensure_ascii=False)


_KIND_LABELS = {"animation": "教学动画", "slides": "课件", "doc": "文档",
                "structure": "结构图", "table": "数据表"}


def _kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, "工件")


def _skill_menu() -> str:
    """「名（中文标题，快捷/斜杠）」清单——错误 hint 与工具描述共用一个渲染，
    格式不漂移；注册即生效（ADR-0027）。"""
    from app.services.teaching_skills import list_skills
    return "；".join(f"{s.name}（{s.title}，快捷{s.slash}）" for s in list_skills())


def _append_skill_names_to_doc() -> None:
    """把注册表技能名拼进 invoke_teaching_skill 的 docstring（= 模型看到的工具
    描述）。工具描述静态、注册表可插拔（ADR-0027），不动态拼就靠模型猜技能名
    ——实测猜成 "animation"（2026-10-05 教师端首调失败）。导入时拼一次，注册
    即生效。"""
    invoke_teaching_skill.__doc__ = (
        (invoke_teaching_skill.__doc__ or "").rstrip()
        + f"\n\nskill 参数必须传以下精确名（不要自造缩写）：{_skill_menu()}。"
    )


_append_skill_names_to_doc()
