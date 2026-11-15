"""Tutoring tools — guided chemistry tutoring, experiment simulation, and equation balancing."""

import json
import logging
import re

logger = logging.getLogger(__name__)

# KB-T14：kb_context 模块级导入（无重依赖；函数内导入会让测试桩打不进、
# 失败也难定位）。真正不可用时 build_kb_context 内部自降级，这里不设防。
from app.services.kb_context import build_kb_context  # noqa: E402


# ── Tutoring tool factory ──

def _make_tutor_tool(name, title, step_guidance, step2_guidance, docstring, default_msg, step_titles=None):
    """Factory for guided tutoring tools (ionic/stoichiometry/redox/equilibrium pattern).

    Returns an async function that:
      - With equation/problem only → step 1 guidance
      - With student_input → step 2 feedback + guidance；若学生身份（服务端上下文）
        与该步预期答案（expected_answer）具备，则判定并经判卷中心落学情（source=chat）
      - With nothing → intro message
    """
    steps = step_titles or ["第一步", "下一步"]

    async def tutor_fn(equation: str = "", problem: str = "",
                       student_input: str = "", expected_answer: str = "") -> str:
        inp = equation or problem
        if inp and not student_input:
            return json.dumps({
                "step": 1, "title": steps[0], "input": inp,
                "guidance": step_guidance,
            }, ensure_ascii=False)
        if student_input and inp:
            judgment = _capture_chat_answer(name, inp, student_input, expected_answer)
            feedback = f"你的回答：{student_input}"
            if judgment is not None:
                feedback += "（已记录学情）"
            return json.dumps({
                "feedback": feedback,
                "guidance": step2_guidance,
                "judgment": judgment,
            }, ensure_ascii=False)
        return json.dumps({"title": title, "guidance": default_msg}, ensure_ascii=False)

    tutor_fn.__name__ = name
    tutor_fn.__doc__ = docstring
    return tutor_fn


def _capture_chat_answer(tool_name: str, problem: str, student_input: str,
                         expected_answer: str):
    """薄壳（ADR-0012）：学生身份取服务端上下文，判定与落库全在判卷中心。"""
    try:
        from agent.tools.context import current_student
        from app.models.database import get_db
        from app.services import grading_center

        sid = current_student()
        if not sid or not (expected_answer or "").strip():
            return None
        db = next(get_db())
        try:
            return grading_center.capture_chat_answer(
                db, tool_name=tool_name, problem=problem,
                student_input=student_input, expected_answer=expected_answer,
                student_id=sid,
            )
        finally:
            db.close()
    except Exception:
        import logging
        logging.getLogger(__name__).warning("chat capture failed", exc_info=True)
        return None




# ── 8 guided tutoring tools ──

ionic_equation_tutor = _make_tutor_tool(
    name="ionic_equation_tutor",
    title="离子方程式书写辅导",
    docstring="""离子方程式书写指导 — 引导学生逐步写出离子方程式，不直接给答案

    何时用：学生需要写离子方程式、检查离子方程式是否正确、不理解"拆/写/删/查"四步法
    会发生什么：逐步引导——判断可拆物质→写成离子→删除不变离子→检查守恒
    下一步：学生完成一步后检查；卡住给提示
    NOT for 化学方程式配平 — 用 balance_equation""",
    step_guidance="先判断反应物和产物中，哪些是可溶性强电解质（能拆成离子形式）？可拆：强酸、强碱、可溶性盐。不能拆：单质、氧化物、气体、沉淀、弱电解质。\n\n请把你认为可以拆的物质写出来：",
    step2_guidance="下一步是写成离子形式。把可拆的物质拆成离子，注意配平原子个数。",
    default_msg=(
        '请把化学方程式发给我，用"拆写删查"四步法带你写离子方程式。\n\n'
        '比如：碳酸钙与盐酸反应 / NaOH + HCl → NaCl + H₂O'
    ),
    step_titles=["第一步：判断哪些物质可以拆"]
)

stoichiometry_tutor = _make_tutor_tool(
    name="stoichiometry_tutor",
    title="物质的量计算辅导",
    docstring="""物质的量计算指导 — 引导学生逐步完成化学计量计算，不代劳

    何时用：学生需要 n/m/V/c 四量换算、化学方程式计算、气体摩尔体积计算
    会发生什么：提取已知量→选公式→列关系式→分步计算→检查单位
    下一步：学生完成一步后反馈；卡住给提示
    NOT for 数学计算本身""",
    step_guidance=(
        "先读题，找出题目中给了哪些量？标出数值和单位。\n\n"
        "• n=m/M (质量→物质的量)\n• n=V/22.4 (标况气体)\n"
        "• n=c×V (浓度×体积)\n• N=n×NA (粒子数)\n\n"
        "请告诉我找到了哪些已知量："
    ),
    step2_guidance="选合适的公式，把已知量代入。先算物质的量 n，再根据需要求其他量。你准备先算什么？",
    default_msg="请把计算题发给我，一步步带你算。\n\n比如：10.6g Na₂CO₃ 的物质的量？/ 标况44.8L CO₂ 质量？",
    step_titles=["第一步：提取已知条件"]
)

redox_tutor = _make_tutor_tool(
    name="redox_tutor",
    title="氧化还原反应辅导",
    docstring="""氧化还原反应辅导 — 引导学生标化合价→找升降→电子守恒，不直接给答案

    何时用：学生要分析氧化还原反应、判断氧化剂还原剂、配平氧化还原方程式
    会发生什么：标化合价→找氧化数变化元素→电子守恒配平
    下一步：每步学生自己写，卡住给提示
    NOT for 普通配平 — 用 balance_equation""",
    step_guidance="先看看各元素的化合价。口诀：单质为零，H+1，O-2，金属正价。\n\n写出各元素化合价，找出哪些变了？",
    step2_guidance="找出化合价升高和降低的元素，用电子守恒计算升降的最小公倍数。",
    default_msg="请把要分析的方程式发给我，带你标化合价→找氧化还原→配平。\n\n比如：Cu+HNO₃→ 或 KMnO₄+HCl→",
    step_titles=["第一步：标化合价"]
)

equilibrium_tutor = _make_tutor_tool(
    name="equilibrium_tutor",
    title="化学平衡辅导",
    docstring="""化学平衡辅导 — 引导学生用勒夏特列原理+三段式分析平衡移动

    何时用：学生要判断平衡移动方向、计算K、分析条件改变对平衡的影响
    会发生什么：分析平衡体系→勒夏特列解释方向→三段式计算
    下一步：每步引导推导
    NOT for 反应速率计算 — 用 chemistry_tutor""",
    step_guidance=(
        "先写出反应方程式。判断：\n1. 放热(ΔH<0)还是吸热？\n2. 气体分子数增加还是减少？\n\n"
        "勒夏特列原理：改变条件，平衡向减弱改变的方向移动。"
    ),
    step2_guidance="现在用三段式（起始量/变化量/平衡量）具体计算。先写起始浓度，再设变化量。",
    default_msg=(
        "请把化学平衡题目发给我，用勒夏特列原理+三段式带你解。\n\n"
        "比如：2SO₂+O₂⇌2SO₃升温怎么移？/ N₂+3H₂⇌2NH₃的K=0.5..."
    ),
    step_titles=["第一步：确定平衡体系"]
)

periodic_law_tutor = _make_tutor_tool(
    name="periodic_law_tutor",
    title="元素周期律辅导",
    docstring="""元素周期律辅导 — 引导学生位置→结构→性质推断

    何时用：学生要推断元素性质、比较元素金属性/非金属性强弱、推断元素在周期表位置
    会发生什么：根据原子序数→电子排布→周期表位置→同族/同周期性质比较
    下一步：每步让学生自己推，卡住给提示
    NOT for 化学方程式书写 — 用 ionic_equation_tutor""",
    step_guidance="先写出该元素的原子结构示意图或电子排布式。确定它在周期表中的位置（第几周期、第几族）。周期数=电子层数，主族数=最外层电子数。你试试看？",
    step2_guidance="确定了位置后，根据同周期/同族元素的性质递变规律来推断。同周期从左到右金属性减弱非金属性增强；同族从上到下金属性增强。",
    default_msg=(
        "请把要推断的元素发给我，带你从位置→结构→性质一步步推。\n"
        "比如：原子序数为17的元素有什么性质？/ 比较Na和Mg的金属性强弱"
    ),
    step_titles=["第一步：确定位置和结构"]
)

organic_tutor = _make_tutor_tool(
    name="organic_tutor",
    title="有机推断辅导",
    docstring="""有机推断辅导 — 引导学生逆合成分析+官能团转化

    何时用：学生要做有机推断题、分析官能团转化、设计合成路线
    会发生什么：分析目标产物→逆推中间体→官能团转化条件→逐步推导
    下一步：每步让学生自己推，卡住给提示
    NOT for 无机反应 — 用 chemistry_tutor""",
    step_guidance="先看目标产物，找出它的官能团和碳骨架。然后逆推：上一步应该是什么？\n常用线索：卤代烃→醇(NaOH水溶液)，醇→醛→酸(氧化)，醇+酸→酯(浓H₂SO₄)。你试试逆推第一步？",
    step2_guidance="逆推到原料后，再正着写一遍合成路线，标注每步的反应条件和反应类型（取代/加成/消去/氧化/酯化）。",
    default_msg=(
        "请把有机推断题发给我，带你从目标产物逆推→官能团转化→合成路线。\n"
        "比如：如何从乙烯合成乙酸乙酯？/ A→B→C的转化条件？"
    ),
    step_titles=["第一步：逆推分析"]
)


# ── chemistry_tutor ──

# ── KB 挂钩设施（KB-T14，2026-10-27）─────────────────────────────

def _kb_enabled() -> bool:
    """知识库挂钩总开关：环境变量 KB_ENABLED=0 关闭（默认开）。"""
    import os
    return os.getenv("KB_ENABLED", "1") != "0"


def _kb_resources() -> dict:
    """build_kb_context 的资源组装：Chroma 集合 + 编码器 + 图 engine。

    任何一件不可用都返回 None 值（build_kb_context 对 collection=None 降级），
    保证挂钩失败不影响回合。编码器/客户端都是进程级单例，这里只取不建。
    集合名经 kb_jobs resolver 解析（kb-ingest-ui 影子切换只改指向行），
    指向的集合不存在时回落历史缺省 kb_chunks，再回落 None。
    """
    out = {"collection": None, "encoder": None, "graph_engine": None}
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        from app.core.config import settings
        client = chromadb.PersistentClient(
            path=settings.CHROMA_DB_PATH,
            settings=ChromaSettings(anonymized_telemetry=False))
        from app.models.database import get_session_factory
        from app.services.kb_jobs import resolve_kb_collection
        s = get_session_factory()()
        try:
            out["collection"] = resolve_kb_collection(s, client)
        finally:
            s.close()
    except Exception:
        pass
    try:
        from app.services.vector_search import get_text_encoder
        out["encoder"] = get_text_encoder()
    except Exception:
        pass
    try:
        from app.models.database import get_engine
        out["graph_engine"] = get_engine()
    except Exception:
        pass
    return out


def _kb_conversation_id() -> str:
    from agent.tools.context import current_student
    return f"tutor:{current_student() or 'anonymous'}"


def _log_kb_audit(cid: str, question: str, kb_out: dict) -> None:
    """命中与降级写服务端日志（ADR-0004：审计不上前端）。"""
    hits = kb_out.get("hits") or []
    logger.info(
        "kb_audit cid=%s mode=%s ms=%s hits=%s q=%s",
        cid, kb_out.get("mode"), kb_out.get("ms"),
        json.dumps([h.get("source_path") for h in hits], ensure_ascii=False),
        question[:60])


async def chemistry_tutor(
    question: str = "",
    student_level: str = "中等",
    persona: str = "tutor",
) -> str:
    """聊天辅导 — 根据角色提供教学辅导或教研分析

    何时用：用户提问化学概念、解题思路、知识点讲解、教研分析等非出题类问题
    会发生什么：教师→教研分析（考点分布、教学策略、学生常见误区）；学生→引导式教学
    下一步：如用户仍有疑问 → 继续解答
    NOT for 实验步骤/现象 — 用 simulate_experiment
    NOT for 检查方程式配平 — 用 balance_equation"""
    from agent.provider.deepseek import DeepSeekProvider

    provider = DeepSeekProvider()

    print(f"[DEBUG] chemistry_tutor called: persona={persona}, question={question[:50]}", flush=True)
    if not persona or persona == "auto" or persona == "tutor":
        from agent.tools.context import current_persona
        persona = current_persona() or persona
    if persona == "teacher":
        system_prompt = """你是高中化学教研助手，正在协助一位化学教师备课和教研。

回答原则：
1. 专业深度：提供高考考点分布、命题趋势、评分标准等教研级信息
2. 教学策略：给出课堂讲解思路、实验演示建议、习题设计方向
3. 学生误区：列举该知识点的常见错误认知和纠正方法
4. 效率优先：直接给出结构化分析，不要反问教师基础问题
5. 教辅联动：如涉及具体题目可建议打开考试工作台出题
6. 单次回复不超过800字"""
    else:
        system_prompt = f"""你是高中化学AI助教。学生水平: {student_level}。

教学原则:
1. 引导式教学: 不直接给答案，先问学生"你是怎么想的"
2. 分步讲解: 复杂问题拆成2-3步，每步确认理解
3. 联系考点: 提及高考中的常见题型
4. 鼓励为主: 对正确思路给予肯定
5. 单次回复不超过500字
6. 不确定时诚实说不知道
7. 有机结构输出契约（硬约束，前端靠它渲染图）：讲到苯环、同分异构体、官能团等
   环状/立体结构时，**必须在文字旁附 [SMILES:...] 标记**，例如苯 [SMILES:C1=CC=CC=C1]、
   甲苯 [SMILES:CC1=CC=CC=C1]、乙醇 [SMILES:CCO]、乙酸 [SMILES:CC(=O)O]。
   只写结构名不附标记，学生端就没有图可看（2026-10 实测：学生问"苯和甲苯长什么样"，
   回复只有"苯"两个字、零张图 —— 契约原来只写在 persona 里，而这条回复的作者是
   本工具，模型只是原样转述）。"""

    # KB-T14 知识库挂钩（2026-10-27）：检索装配的资料段拼进 user 消息
    # （资料在前、问题在后）。miss/degraded/开关关闭三种形态都不拼——
    # miss 的边界提示（"暂未覆盖"）随 kb_context 的 text 送达，模型据此
    # 直说没覆盖而不是硬编。KB4a 起 weak（弱命中）与 ok 同路注入：资料
    # 照常交付，text 头部的【覆盖提示】会让模型标注推断依据、谨慎作答。
    # 命中写回合日志（ADR-0004：审计不上前端）。
    kb_out = None
    if _kb_enabled():
        try:
            kb_resources = _kb_resources()
            try:
                from app.services.kb_chunks import disabled_chunk_ids
                kb_resources["exclude_ids"] = disabled_chunk_ids()
            except Exception:
                pass
            kb_out = build_kb_context(question, **kb_resources)
            _log_kb_audit(_kb_conversation_id(), question, kb_out)
        except Exception:
            logger.warning("kb 挂钩失败，本轮无资料段", exc_info=True)
            kb_out = None
    user_content = question
    if kb_out and kb_out.get("text") and kb_out.get("mode") in ("ok", "weak"):
        user_content = f"{kb_out['text']}\n\n【学生问题】{question}"
    elif kb_out and kb_out.get("mode") == "miss":
        user_content = (f"{kb_out['text']}\n\n【学生问题】{question}\n"
                        "（知识库未覆盖此问题时直接说明，不要编造。）")

    import time as _time
    _started = _time.monotonic()
    result = await provider.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.7, max_tokens=1024,
    )
    _elapsed_ms = int((_time.monotonic() - _started) * 1000)

    await provider.close()

    # 问答证据链（rag-observability）：异步写、失败静默，绝不影响回答
    if kb_out is not None:
        try:
            from agent.tools.context import current_student
            from app.services.kb_jobs import active_collection_name
            from app.models.database import get_session_factory
            from app.services.qa_trace import record_trace
            _s = get_session_factory()()
            try:
                _index = active_collection_name(_s)
            finally:
                _s.close()
            record_trace(
                question=question,
                answer=result.content or "",
                kb_out=kb_out,
                model_name=result.model or "",
                latency_ms=_elapsed_ms,
                user_id=current_student() or "",
                role="student",
                conversation_id=_kb_conversation_id(),
                index_version=_index,
            )
        except Exception:
            logger.warning("qa trace 埋点失败（不影响回答）", exc_info=True)

    return json.dumps({"answer": result.content, "model": result.model}, ensure_ascii=False)


# ── balance_equation ──

async def balance_equation(equation: str = "") -> str:
    """方程式配平 — 审核化学方程式的配平正确性

    何时用：用户发来一个化学方程式要求检查配平，或出题后需要审核题目中的方程式
    会发生什么：检查等式两侧各元素的原子数量，返回配平状态和各元素计数
    下一步：如配平正确 → 告知用户；如有问题 → 指出不平衡的元素和修正建议
    NOT for 化学原理讲解/概念解释 — 用 chemistry_tutor"""
    from app.services.chemical_balance import audit_chemical_equation

    result = audit_chemical_equation(equation)
    return json.dumps(result, ensure_ascii=False)


# ── Chemical formula normalizer (shared by generation) ──

def _normalize_chem_formulas(text: str) -> str:
    """归一化化学式格式：将 $...$ 内部的 → 替换为 \\rightarrow，检测裸化学式并包装。"""
    # Step 0: 保护 [SMILES:...] 标记（与 structure_render 共用同一正则，ADR-0005 H1），
    #         避免裸化学式包装误伤 SMILES 字符串（如 CC1CC1）
    from app.services.structure_render import _SMILES_MARKER_RE
    _smiles_blocks = []

    def _protect_smiles(m: re.Match) -> str:
        _smiles_blocks.append(m.group(0))
        return f"\x00SMILES{len(_smiles_blocks) - 1}\x00"

    text = _SMILES_MARKER_RE.sub(_protect_smiles, text)

    # Step 1: fix arrows inside $...$
    def _fix_arrow_in_math(m: re.Match) -> str:
        content = m.group(1)
        content = content.replace("→", "\\rightarrow")
        content = content.replace("⇌", "\\rightleftharpoons")
        content = content.replace("↑", "\\uparrow")
        content = content.replace("↓", "\\downarrow")
        content = content.replace("≡", "\\equiv")
        content = content.replace("·", "\\cdot")
        return f"${content}$"

    text = re.sub(r"\$([^$]+)\$", _fix_arrow_in_math, text)


    # Step 1.5: 裸 LaTeX 命令（模型直接吐 \rightarrow / \equiv 而**没有** $ 包裹）→
    # 把所在的那一段化学式整体包进 $...$。少了这一步，第 2 步会因为「文本里已经有 $」
    # 整段跳过，LaTeX 就裸奔到界面上（2026-10 实测：NaOH + HCl\rightarrow NaCl + H_2O）。
    _BARE_LATEX = re.compile(
        r"\\(?:rightleftharpoons|rightarrow|leftarrow|equiv|cdot|times|pm|to|uparrow|downarrow)\b")
    if _BARE_LATEX.search(text):
        # 「公式样」的连续字符集合：化学式、系数、括号、运算符、空白与 LaTeX 命令本身。
        # 中文不在其中 → 中文自然打断，整句不会被塞进数学模式。
        _FORMULA_RUN = re.compile(r"[^\u3000-\u303f\u4e00-\u9fff\uff00-\uffef$\n]+")

        def _wrap_latex_run(m):
            run = m.group(0)
            if not _BARE_LATEX.search(run) or not run.strip():
                return run
            body = run.strip()
            start = run.index(body)
            core = body
            # 这段马上要进数学模式了，裸字符在里面不渲染 —— 顺手换成对应命令
            for _raw, _tex in (("→", "\\rightarrow"), ("⇌", "\\rightleftharpoons"),
                               ("↑", "\\uparrow"), ("↓", "\\downarrow"),
                               ("≡", "\\equiv"), ("·", "\\cdot")):
                core = core.replace(_raw, _tex)
            return run[:start] + "$" + core + "$" + run[start + len(body):]

        _parts = re.split(r"(\\$[^$]*\\$)", text)
        text = "".join(p if p.startswith("$") else _FORMULA_RUN.sub(_wrap_latex_run, p)
                        for p in _parts)

    # Step 2: wrap bare chemical formulas in $...$ (no existing $ delimiters)
    if "$" not in text:
        chem_pattern = re.compile(r'\b([A-Z][a-z]?\d*[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)*)')
        known_formulas = {
            "H2O", "CO2", "NaCl", "NaOH", "HCl", "Fe2O3", "FeO", "CaCO3",
            "NH4Cl", "NH3", "Cl2", "Na", "Fe", "Cu", "O2", "H2", "N2",
            "SO2", "SO3", "H2SO4", "CuSO4", "FeSO4", "CaO", "MnO2",
            "KClO3", "KCl", "Na2CO3", "FeCl3", "CuO", "MgO", "Al2O3",
            "NO2", "NO", "CO", "CH4", "C2H5OH", "CH3COOH", "HNO3",
            "H3PO4", "Na2SO4", "BaSO4", "AgCl", "AgNO3", "CaCl2",
            "Fe(OH)3", "Al(OH)3", "Mg(OH)2", "Ca(OH)2", "NaOH",
            "NaHCO3", "KMnO4", "K2Cr2O7",
        }

        def _wrap_chem(m: re.Match) -> str:
            w = m.group(0)
            if re.search(r'[a-z]{3,}', w):
                return w
            if w in known_formulas or any(c.isdigit() for c in w):
                subbed = re.sub(r'([A-Za-z)])(\d+)', r'\1_\2', w)
                return f"${subbed}$"
            return w
        text = chem_pattern.sub(_wrap_chem, text)

    # Step 3: 恢复 [SMILES:...] 标记
    for i, block in enumerate(_smiles_blocks):
        text = text.replace(f"\x00SMILES{i}\x00", block)

    return text
