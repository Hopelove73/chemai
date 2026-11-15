"""ChemAI Tools — collected from domain sub-modules and re-exported.

TOOL_META maps tool function → {"personas": [...], "call_limit": N}.
Used by langgraph_agent_v2 to filter tools per persona.
"""

# ── Exam bank & search ──
from agent.tools.exam_bank import (
    search_exam_bank,
    web_search,
    show_exam_workbench,
    save_to_bank,
    list_banks,
    delete_bank,
)

# ── Tutoring ──
from agent.tools.tutoring import (
    chemistry_tutor,
    balance_equation,
    ionic_equation_tutor,
    stoichiometry_tutor,
    redox_tutor,
    equilibrium_tutor,
    periodic_law_tutor,
    organic_tutor,
)

# ── Diagnosis ──
from agent.tools.diagnosis import (
    diagnose_barrier,
    show_diagnosis,
    show_students,
    weekly_report,
    assign_adaptive_practice,
    query_assignment_progress,
)

# ── Question generation ──
from agent.tools.generation import generate_questions

# ── Class notifications ──
from agent.tools.notification import send_class_notification

# ── Experiment simulation ──
from agent.tools.simulation import simulate_experiment

# ── OCR & grading ──
from agent.tools.ocr_grading import (
    query_ocr_progress,
    grade_answer_sheets,
    save_grading_results,
    query_grading_queue,
    confirm_exam_grades,
    query_batch_grades,
    list_teacher_exams,
)

# ── Document parsing ──
from agent.tools.parser import (
    parse_chemistry_document,
    import_question_bank,
    check_parser_services,
)

# ── Long-term memory ──
from agent.tools.memory import (
    memory_student_get,
    memory_teacher_get,
)

# ── Filesystem & shell (Spec 03: read/write/edit/bash 沙箱四件套) ──
from agent.tools.filesystem import (
    read,
    write,
    edit,
    find,
    grep,
)
from agent.tools.shell import bash

# ── Planner (Spec 06: 复杂任务拆解接入 v2 agent) ──
from agent.tools.plan import plan_task

# ── Teaching skills (ADR-0027: 教学内容生产唯一入口，技能在 app/services/teaching_skills) ──
from agent.tools.teaching import invoke_teaching_skill, share_artifact
from agent.tools.learning import invoke_learning_skill
from agent.tools.plan import decide_plan_draft
from agent.tools.parent import parent_weekly_summary
from agent.tools.knowledge_map import (
    propose_knowledge_edges, accept_knowledge_edges, disable_knowledge_edge,
)
from agent.tools.parent import (
    parent_learning_plan, parent_mastery_levels, parent_barrier_distribution,
    parent_class_comparison, parent_artifact_log, parent_assign_task, parent_send_task,
)

# ── Learning plan ──
from agent.tools.diagnosis import (
    generate_learning_plan,
    revise_learning_plan,
    send_learning_plan,
    generate_parent_report,
    send_report_to_parent,
)

# ── All tools (flat list for agent factories) ──

TOOLS = [
    search_exam_bank,
    web_search,
    show_exam_workbench,
    show_diagnosis,
    show_students,
    diagnose_barrier,
    chemistry_tutor,
    simulate_experiment,
    balance_equation,
    query_ocr_progress,
    grade_answer_sheets,
    save_grading_results,
    query_grading_queue,
    confirm_exam_grades,
    query_batch_grades,
    list_teacher_exams,
    ionic_equation_tutor,
    stoichiometry_tutor,
    redox_tutor,
    equilibrium_tutor,
    periodic_law_tutor,
    organic_tutor,
    weekly_report,
    assign_adaptive_practice,
    query_assignment_progress,
    save_to_bank,
    list_banks,
    delete_bank,
    generate_questions,
    memory_student_get,
    memory_teacher_get,
    generate_learning_plan,
    revise_learning_plan,
    send_learning_plan,
    generate_parent_report,
    send_report_to_parent,
    parse_chemistry_document,
    import_question_bank,
    check_parser_services,
    read,
    write,
    edit,
    find,
    grep,
    bash,
    plan_task,
    invoke_teaching_skill,
    # 学习技能入口（ADR-0029）：会话类 student 专属、读侧 student+parent，
    # 面内细分在 learning_skills.dispatch 按身份裁定
    invoke_learning_skill,
    decide_plan_draft,
    parent_learning_plan,
    parent_mastery_levels,
    parent_barrier_distribution,
    parent_class_comparison,
    parent_weekly_summary,
    parent_artifact_log,
    parent_assign_task,
    parent_send_task,
    propose_knowledge_edges,
    accept_knowledge_edges,
    disable_knowledge_edge,
    send_class_notification,
    share_artifact,
]

# ── Per-tool metadata for persona filtering & call limits ──

TOOL_META = {
    search_exam_bank:         {"personas": ["tutor", "teacher"], "call_limit": 3},
    web_search:               {"personas": ["student", "tutor", "teacher", "parent"], "call_limit": 2},
    show_exam_workbench:      {"personas": ["tutor", "teacher"], "call_limit": 3},
    show_diagnosis:           {"personas": ["teacher"], "call_limit": 1,
                               "scope": {"student_id": "student", "student_name": "student", "class_id": "class"}},
    show_students:            {"personas": ["teacher"], "call_limit": 3,
                               "scope": {"class_id": "class", "class_name": "class", "student_name": "student"}},
    diagnose_barrier:         {"personas": ["teacher", "parent"], "call_limit": 2,
                               "scope": {"student_id": "student", "student_name": "student", "class_id": "class"}},
    chemistry_tutor:          {"personas": ["student", "tutor", "teacher"], "call_limit": 3},
    simulate_experiment:      {"personas": ["student", "tutor", "teacher"], "call_limit": 2},
    balance_equation:         {"personas": ["tutor", "teacher"], "call_limit": 3},
    query_ocr_progress:       {"personas": ["teacher"], "call_limit": 3,
                               "scope": {"teacher_id": "teacher"}},
    grade_answer_sheets:      {"personas": ["teacher"], "call_limit": 2,
                               "scope": {"teacher_id": "teacher"}},
    save_grading_results:     {"personas": ["teacher"], "call_limit": 2,
                               "scope": {"teacher_id": "teacher"}},
    query_grading_queue:      {"personas": ["teacher"], "call_limit": 3,
                               "scope": {"teacher_id": "teacher"}},
    confirm_exam_grades:      {"personas": ["teacher"], "call_limit": 1},
    query_batch_grades:       {"personas": ["teacher"], "call_limit": 3,
                               "scope": {"teacher_id": "teacher"}},
    list_teacher_exams:       {"personas": ["teacher"], "call_limit": 3,
                               "scope": {"teacher_id": "teacher"}},
    send_class_notification:  {"personas": ["teacher"], "call_limit": 2,
                               "scope": {"class_id": "class"}},
    ionic_equation_tutor:     {"personas": ["student"], "call_limit": 5},
    stoichiometry_tutor:      {"personas": ["student"], "call_limit": 5},
    redox_tutor:              {"personas": ["student"], "call_limit": 5},
    equilibrium_tutor:        {"personas": ["student"], "call_limit": 5},
    periodic_law_tutor:       {"personas": ["student"], "call_limit": 5},
    organic_tutor:            {"personas": ["student"], "call_limit": 5},
    weekly_report:            {"personas": ["teacher", "parent"], "call_limit": 2,
                               "scope": {"student_id": "student", "student_name": "student", "class_name": "class"}},
    assign_adaptive_practice: {"personas": ["teacher"], "call_limit": 1,
                               "scope": {"class_id": "class", "student_id": "student", "student_name": "student"}},
    query_assignment_progress: {"personas": ["teacher"], "call_limit": 3},
    save_to_bank:             {"personas": ["tutor", "teacher"], "call_limit": 1},
    list_banks:               {"personas": ["tutor", "teacher"], "call_limit": 1},
    delete_bank:              {"personas": ["tutor", "teacher"], "call_limit": 1},
    generate_questions:       {"personas": ["tutor", "teacher"], "call_limit": 5},
    memory_student_get:       {"personas": ["student", "tutor", "teacher", "parent"], "call_limit": 3,
                               "scope": {"student_id": "student"}},
    memory_teacher_get:       {"personas": ["teacher"], "call_limit": 3,
                               "scope": {"teacher_id": "teacher"}},
    generate_learning_plan:   {"personas": ["teacher"], "call_limit": 5,
                               "scope": {"student_id": "student", "student_name": "student",
                                         "class_id": "class", "class_name": "class"}},
    revise_learning_plan:     {"personas": ["teacher"], "call_limit": 8,
                               "scope": {"student_id": "student"}},
    send_learning_plan:       {"personas": ["teacher"], "call_limit": 8,  # 打回-修订-再确认循环会多次到门（ADR-0013）
                               "scope": {"student_id": "student", "teacher_id": "teacher"}},
    generate_parent_report:   {"personas": ["teacher"], "call_limit": 5,
                               "scope": {"student_name": "student", "student_id": "student"}},
    send_report_to_parent:    {"personas": ["teacher"], "call_limit": 3,
                               "scope": {"student_id": "student"}},
    parse_chemistry_document: {"personas": ["tutor", "teacher"], "call_limit": 3},
    import_question_bank:     {"personas": ["tutor", "teacher"], "call_limit": 2},
    check_parser_services:    {"personas": ["tutor", "teacher"], "call_limit": 2},
    read:                     {"personas": ["tutor", "teacher"], "call_limit": 5},
    write:                    {"personas": ["tutor", "teacher"], "call_limit": 3},
    edit:                     {"personas": ["tutor", "teacher"], "call_limit": 3},
    find:                     {"personas": ["tutor", "teacher"], "call_limit": 3},
    # grep 5：工件自检流（生成动画后 grep 数 keyframe/参数）一轮要用 4~5 次，
    # 3 会把正当 QA 循环掐在半截（2026-10-05 教师端动画迭代实录）；再往上不加——
    # 单轮总步数 recursion_limit=24，grep 配额过宽只会挤掉生成/写入步骤
    grep:                     {"personas": ["tutor", "teacher"], "call_limit": 5},
    bash:                     {"personas": ["tutor", "teacher"], "call_limit": 2},
    plan_task:                {"personas": ["tutor", "teacher"], "call_limit": 2},
    # 教学技能入口（ADR-0027）：内容生产是教师场景，学生辅导端不见
    invoke_teaching_skill:    {"personas": ["teacher", "student"], "call_limit": 4},
    # 学习技能入口（ADR-0029）：四技能单一入口；会话类/读侧在 dispatch 按
    # 登录身份裁定（student=本人，parent=绑定内解析）。student_name 走
    # ADR-0014 scope 集中校验：学生缺参跳过（本人语义），家长传名经绑定
    # 校验并回填 student_id（dispatch 内 _resolve_student 二道防线）
    invoke_learning_skill:    {"personas": ["student", "parent"], "call_limit": 6,
                               "scope": {"student_name": "student"}},
    # 学生计划确认门（ADR-0029 批④）：本人对收到的草案表态，直接执行不过门
    # 卡（Decision 5）；draft_id 非身份参数，归属校验在实现内按登录身份硬校验
    decide_plan_draft:        {"personas": ["student"], "call_limit": 3},
    # 对外分发必须过门（R6）；student_id 走数据范围校验（ADR-0014）
    share_artifact:           {"personas": ["teacher"], "call_limit": 5,
                               "scope": {"student_id": "student"}},
    # 家长端（批 3 P2）：读 5 件 = 既有 service 薄包装（绑定内收窄）；
    # 派任务对 = 生成即门（parent_assign_task 弹卡 → parent_send_task 重放落计划）
    parent_learning_plan:     {"personas": ["parent"], "call_limit": 3,
                               "scope": {"student_name": "student"}},
    parent_mastery_levels:    {"personas": ["parent"], "call_limit": 3,
                               "scope": {"student_name": "student"}},
    parent_barrier_distribution: {"personas": ["parent"], "call_limit": 3,
                               "scope": {"student_name": "student"}},
    # 周报拼三源（ADR-0029 批③）：三卡 + 消灭率 + AI 人话一次拿全
    parent_weekly_summary:    {"personas": ["parent"], "call_limit": 3,
                               "scope": {"student_name": "student"}},
    parent_class_comparison:  {"personas": ["parent"], "call_limit": 3,
                               "scope": {"student_name": "student"}},
    parent_artifact_log:      {"personas": ["parent"], "call_limit": 3,
                               "scope": {"student_name": "student"}},
    parent_assign_task:       {"personas": ["parent"], "call_limit": 2,
                               "scope": {"student_name": "student"}},
    parent_send_task:         {"personas": ["parent"], "call_limit": 2,
                               "scope": {"student_id": "student"}},
    # 知识地图（S6）：内容生成走技能化提议（LLM 提议只读不落库），采纳/禁用是
    # 教师显式写操作
    propose_knowledge_edges:  {"personas": ["teacher"], "call_limit": 2},
    accept_knowledge_edges:   {"personas": ["teacher"], "call_limit": 3},
    disable_knowledge_edge:   {"personas": ["teacher"], "call_limit": 5},
}

# verify all TOOLS are in TOOL_META
assert set(TOOLS) == set(TOOL_META.keys()), f"TOOL_META missing: {set(TOOLS) - set(TOOL_META.keys())}"
