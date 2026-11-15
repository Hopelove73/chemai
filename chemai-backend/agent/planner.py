"""
ChemAgent Planner — decompose complex teaching goals into structured execution plans.
"""
import json
import re
from dataclasses import dataclass, field


PLAN_PROMPT = """你是化学教研助手的目标拆解器。将用户的复杂目标拆解为结构化执行步骤。

可用技能：
{skills}

已知学生（学号 → 姓名）：
{students}

返回 JSON（只返回 JSON，不要其他文字）：
{
  "goal": "原始目标（复述）",
  "steps": [
    {
      "step": 1,
      "skill": "技能名",
      "args": {"参数名": "参数值"},
      "depends_on": [],
      "description": "中文步骤描述"
    }
  ]
}

规则：
- 最多 8 个步骤
- 计划必须覆盖用户目标中的**全部**结果，不得遗漏：提到「家长/父母/同步/告知家长」
  必须包含 generate_parent_report（生成家长报告）与 send_report_to_parent（发送给家长）步骤；
  提到「发送给学生」必须包含 send_learning_plan 步骤
- assign_adaptive_practice **自带出题**（按 knowledge_points / question_count 现场生成），
  不要为它再单独安排一个 generate_questions 步骤：那一步的产出没有任何步骤消费，只会
  多一次模型调用、多一张失败卡（2026-10 真机实测：它为引用前一步的题目而失败重试）。
  只有老师明确要求「出题并存入题库」时才要 generate_questions（那要配 save_to_bank）。
- depends_on 引用前序步骤编号（如 [1, 2]）
- 需要前序步骤输出时，使用 ${step_N.field} 引用；field 必须是那一步工具**真实返回**的
  字段名：出题步骤 generate_questions 返回 questions（题目列表）。
  **不要把列表/字典类产物接到一个标量参数上**——比如 knowledge_points 要的是知识点
  名字（"化学平衡、氧化还原"），不是 diagnoses 那种诊断记录列表；接错了那一步会带着
  一串无意义的字符串去查库，什么都查不到。
  questions。**不要按语义猜字段**（question_ids / weak_points 这类没人产出）——猜错的
  引用会被整个丢掉，那一步就带着缺参数白跑一次（2026-10 真机实测：assign_adaptive_practice
  因此失败重试过一次）。拿不准就别引用：工具自带默认值与追问分支，缺参数是可恢复的。
  该引用由运行时从**已落库的步骤产物**注入，早期步骤输出的那条消息已从对话上下文
  消失也照样取得到——不要因为没有原始输出就跳过引用、把参数留空
- 禁止循环依赖
- 如果不需要拆解，返回单步 Plan
- 步骤描述用学生姓名（如「陈湘旻」），不要只写学号；名称未知时先安排查找学生的步骤

用户目标: {goal}

JSON:"""


# 双花括号也认：老模板把它教成 `${{step_1.x}}`，认不出就会把占位符当真实参数
# 灌进工具，且 `missing` 一声不响（丢参最坏的样子）。
_REF_RE = re.compile(r"\$\{\{?step_(\d+)\.(\w+)\}\}?")


# 只有「跟家长打交道」才该用的技能：用户没提家长，计划里就不该出现它们。
# 2026-10 真机实测（C10）：老师说「出一整套本单元复习资料」，计划末尾凭空多了一步
# generate_parent_report(241901)，把家长报告塞进了复习资料回合。
_PARENT_ONLY_SKILLS = {"generate_parent_report", "send_report_to_parent"}
_PARENT_INTENT_RE = re.compile(r"家长|父母|监护人|通知家|告知家|同步给家")


def drop_offscope_parent_steps(plan: "Plan", goal: str) -> "Plan":
    """目标里没提家长 → 把家长类步骤摘掉，并重编号/重映射 depends_on。

    为什么在代码里做而不是只靠提示词：模型对「必须包含家长步骤」那条规则记得很牢，
    对「不该包含」这种否定式规则记不牢——实测就是这么跑偏的。摘步骤是确定性的，
    可测；提示词只能求它别犯。
    """
    if not plan.steps or _PARENT_INTENT_RE.search(goal or ""):
        return plan
    kept = [s for s in plan.steps if s.skill not in _PARENT_ONLY_SKILLS]
    if len(kept) == len(plan.steps):
        return plan
    old_to_new = {s.step: i for i, s in enumerate(kept, 1)}
    for i, s in enumerate(kept, 1):
        s.step = i
        s.depends_on = [old_to_new[d] for d in (s.depends_on or []) if d in old_to_new]
    return Plan(goal=plan.goal, steps=kept)


def referenced_steps(args: dict) -> set:
    """args 里 ${step_N.field} 引用到的步号集合（纯函数，C2 的依赖判据）。"""
    refs = set()
    for value in (args or {}).values():
        if isinstance(value, str):
            refs.update(int(n) for n, _ in _REF_RE.findall(value))
    return refs


def resolve_args(args: dict, artifacts: dict) -> tuple:
    """把 args 里的 ${step_N.field} 换成产物里的值（纯函数）→ (已解析参数, 未解析引用)。

    artifacts 是 {步号: 产物 dict}，来自库（plan_step_artifacts）而非对话历史 ——
    长任务里第 1 步的产物会被 trim_context 挤掉，能靠的只有这张表。

    **解析不到的引用 → 整个参数丢掉**，不留空串、更不留模板串：留 `""` 会让工具
    把"没这个参数"当成"用户说了空值"跑出一个看着成功的错答案；留 `${step_1.x}`
    则是把占位符当真实参数灌进工具。工具自己都有默认值，缺参数才会走它自己的
    追问/兜底分支，那才是可恢复的失败。丢掉的引用一并报回去，调用方负责留痕。
    """
    resolved, missing = {}, []
    for key, value in (args or {}).items():
        if not isinstance(value, str):
            resolved[key] = value
            continue
        if not _REF_RE.search(value):
            resolved[key] = value
            continue
        lost = []

        def _sub(m):
            step_no, ref_field = int(m.group(1)), m.group(2)
            val = (artifacts.get(step_no) or {}).get(ref_field)
            if val is None or val == "":
                lost.append((step_no, ref_field))
                return m.group(0)
            # 结构化值整个塞进一个标量参数，只会变成一串看得见但没意义的 repr
            # （2026-10 真机实测：计划把 ${step_1.diagnoses} 接到 knowledge_points，
            #  一串 {'key':...,'data':{...}} 进了题库查询 → 一道题都组不出来，
            #  那一步被标成 failed）。宁可当作没解析到：参数整个丢掉，工具走它
            #  自己的默认值，那是可恢复的。
            if isinstance(val, (dict, list)) and not isinstance(val, str):
                if isinstance(val, dict) or any(isinstance(v, (dict, list)) for v in val):
                    lost.append((step_no, ref_field))
                    return m.group(0)
            return ",".join(str(v) for v in val) if isinstance(val, list) else str(val)

        new_value = _REF_RE.sub(_sub, value)
        if lost:
            missing.extend(lost)
            continue
        resolved[key] = new_value
    return resolved, missing


@dataclass
class PlanStep:
    step: int
    skill: str
    args: dict = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)
    description: str = ""
    status: str = "pending"  # pending / running / completed / failed / skipped


@dataclass
class Plan:
    goal: str = ""
    steps: list[PlanStep] = field(default_factory=list)


class PlanGenerator:
    def __init__(self, provider):
        self._provider = provider

    async def generate(self, user_goal: str, available_skills: list[str],
                       student_context: str = "") -> Plan:
        """LLM 调用拆解用户目标为结构化执行计划。失败时 fallback 到单步 Plan。

        student_context: 目标中出现的学号 → 姓名映射（给描述用人名、args 用学号），
        空串时占位为「（无）」。"""
        # Build skills description
        skills_desc = "\n".join(f"  - {s}" for s in available_skills)
        prompt = (PLAN_PROMPT
                  .replace("{skills}", skills_desc)
                  .replace("{students}", student_context or "（无）")
                  .replace("{goal}", user_goal))

        try:
            result = await self._provider.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1024,
            )
            plan = self._parse(result.content, available_skills)
            plan = drop_offscope_parent_steps(plan, user_goal)
            if not plan.steps:
                return self._single_step_fallback(user_goal, available_skills)
            return plan
        except Exception:
            return self._single_step_fallback(user_goal, available_skills)

    def _parse(self, content: str, available_skills: list[str]) -> Plan:
        """Parse LLM JSON, validate steps."""
        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start < 0 or end <= start:
                return Plan()
            data = json.loads(content[start:end])
            steps = self._validate_steps(data.get("steps", []), available_skills)
            return Plan(goal=data.get("goal", ""), steps=steps)
        except (json.JSONDecodeError, KeyError):
            return Plan()

    def _validate_steps(self, steps_data: list[dict], available_skills: list[str]) -> list[PlanStep]:
        """Validate and sanitize steps. Cap at 8, check skill names, detect cycles."""
        valid = []
        seen_steps = set()
        for sd in steps_data:
            sn = sd.get("step", 0)
            if sn in seen_steps or sn < 1:
                continue
            seen_steps.add(sn)
            skill = sd.get("skill", "")
            if skill not in available_skills:
                continue  # skip unknown skills
            deps = sd.get("depends_on", [])
            # Detect self-reference cycles
            if sn in deps:
                deps = [d for d in deps if d != sn]
            valid.append(PlanStep(
                step=sn,
                skill=skill,
                args=sd.get("args", {}),
                depends_on=deps,
                description=sd.get("description", skill),
            ))
        return valid[:8]  # cap at 8 steps

    def _single_step_fallback(self, goal: str, available_skills: list[str]) -> Plan:
        """Fallback: single-step plan using the best-guess skill."""
        # Pick the most relevant skill based on keyword matching
        skill = available_skills[0] if available_skills else "search_exam_bank"
        if any(kw in goal for kw in ["题", "出题", "生成"]):
            skill = "generate_questions" if "generate_questions" in available_skills else skill
        elif any(kw in goal for kw in ["诊断", "障碍"]):
            skill = "diagnose_barrier" if "diagnose_barrier" in available_skills else skill
        elif any(kw in goal for kw in ["搜索", "真题", "查"]):
            skill = "search_exam_bank" if "search_exam_bank" in available_skills else skill

        return Plan(goal=goal, steps=[PlanStep(
            step=1, skill=skill, args={"keyword": goal[:50]},
            description=f"执行 {skill}"
        )])

