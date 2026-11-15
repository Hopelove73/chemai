"""教学技能注册表（ADR-0027）——教学内容生产的唯一扩展点。

四种内容形状高度相似（按模板产内容 → 工件 → 右栏预览 → 迭代），逐个造工具会让
工具注册表线性膨胀、工具选择越发散；这里把「prompt 模板 + 产物工件类型 + 参数
schema + 依赖的既有校验」四要素收成一个可插拔声明，agent 只见 invoke_teaching_skill
一个入口。新增内容种类 = 注册一个技能，agent 与 CI 冒烟断言都不动。

学科约定（ADR-0028，气质不是关卡）写死在模板里：
- 渲染层：自包含 HTML 里化学式用 <sub>/<sup> 或 Unicode 下标（工件禁外链 CDN，
  KaTeX/mhchem 这类渲染库不可用——聊天消息里才有它们）；视觉夸张必须配诚实标注。
- 语言层：教学动线「观察→猜→转→验证→合」（先猜后验）、宏观-微观-符号三重表征、
  浅底投屏大字大按钮。
- 工具层：涉及配平/审核时 agent 自主调既有工具（balance_equation、四维审核），
  模板只提示可用，不做强制。
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TeachingSkill:
    """教学技能四要素（ADR-0027 Decision 2）。

    artifact_kind 必须在 artifact_service.ALLOWED_KINDS 里（两侧契约测试钉住）；
    required_params 列必填参数（分发层按声明拦截缺参，不认识具体技能名）；
    checks 列声明该技能的产物要过哪些既有校验（复用不新造）。
    """
    name: str
    title: str
    slash: str
    artifact_kind: str
    prompt_template: str
    params: dict = field(default_factory=dict)
    required_params: tuple = ()
    checks: tuple = ()
    author: str = "builtin"  # 预留老师自定义技能；本期 fail-closed 只认 builtin


ANIMATION_PROMPT = """你是高中化学教学动画设计师——懂教学动线的前端工程师，不是程序员在画页面。
用户给你一个化学教学主题，你产出一个**自包含的单文件 HTML 交互教学动画**（Canvas 或 DOM）。

## 硬性产出规范（违反任何一条即为不合格）
1. **单文件自包含**：从 `<!DOCTYPE html>` 开始，纯原生 HTML+CSS+JS，不引任何外部
   CDN/字体/图标库（用 Unicode 符号画图标）。禁止 markdown 代码围栏，直接输出 HTML。
2. **参数集中区**：JS 顶部设 `const P = {...}` 集中全部教学参数（粒子数、速率、
   比例上限等），每个参数带中文注释说明教学含义。
3. **诚实标注**：微观比例/数量做了视觉放大的，页面上必须有一行诚实说明
   （如「粒子数量与比例已放大便于观察，实际水解率 <5%」）。
4. **化学式排版**：用 HTML `<sub>/<sup>` 或 Unicode 下标字符（CH₃COONa、Fe₂O₃），
   离子电荷用上标（SO₄²⁻）。不用 KaTeX/MathJax（要外链）。
5. **浅底投屏**：纸感浅底（#f7f4ee 一类）、大字号（正文 ≥16px）、大交互按钮、
   高对比；老师要在教室投影仪上放。

## 教学动线（页面结构照此编排）
- **观察**：初始展示一个可观察的稳态（如纯水的电离平衡），让学生先看到现象。
- **猜**：明确提出驱动问题（「都是盐溶液，为什么酸碱性不一样？」），配交互点让学生
  先猜再验证。
- **转**：核心交互（切换盐种/加料/拖滑块/暂停），每步有明确的视觉反馈。
- **验证**：交互后实时展示宏观现象（颜色/读数）与微观解释（粒子运动）联动，
  宏观-微观-符号三重表征对照（符号区给方程式与离子方程式）。
- **合**：总结卡（结论 + 课标定位 + 一个追问）。
- 快捷键支持（数字键切换场景、空格暂停）。

## 输出
只输出完整 HTML 文档本身。"""

REVIEW_DECK_PROMPT = """你是高中化学讲评课设计专家。用户给你一次考试的批改数据（JSON），你产出一份
**讲评课讲评稿**——自包含的单文件 HTML，老师可以直接投屏逐节讲。

## 硬性产出规范
1. 单文件自包含（同动画技能：无外链、化学式用 sub/sup/Unicode）。
2. 数据必须**忠于输入**：正确率、人数、错题分布直接引用给定数字，禁止编造或估算
   输入里没有的数据；输入缺某维度就明说「本次数据未覆盖」。
3. 结构：① 整体概览（均分/正确率带）→ ② 错题分布（按知识点排序列表）→
   ③ 典型错题讲评（每道：题干摘要 → 高频错选 → 错因归类[概念理解/审题/表述] →
   正解思路 → 一道变式）→ ④ 讲评动线建议（先讲哪道后讲哪道、时间分配）。
4. 正解思路里的方程式引用调用方传入的已核对数据与配平结果（本次生成没有工具可调，
   输入 JSON 里的数字与方程式即唯一事实来源）；引用数据时标明来源批次。
5. 浅底投屏、大字号、讲评节奏（时间轴）可视化。

## 输出
只输出完整 HTML 文档本身。"""

_ANIMATION_PARAMS = {
    "topic": "教学主题，如「盐类水解」「原电池」。必填。",
    "requirements": "附加要求，如「加温度滑块」「重点讲平衡移动」。可选。",
}

_EXPRESSION_PARAMS = {
    "scene": "训练场景，如「实验方案答辩：配制 FeSO4 溶液并说明每步理由」。必填。",
    "student_sentences": "学生的分句作答 JSON 数组字符串，如 [\"句1\",\"句2\"]。必填——"
                         "没有学生作答就不要调本技能（先在对话里让学生逐句说）。",
}

_REVIEW_PARAMS = {
    "topic": "讲评主题，如「期中考试讲评」。必填。",
    "context_data": "批改数据 JSON 字符串。可省略——省略时由 review_deck_context 自动取该老师"
                    "最近一次已完成批改（已保存成绩或刚批完的答题卡），并在结果里说明数据来源；"
                    "库里一次批改都没有时才 fail-closed 并给候选路径。",
}

EXPRESSION_TRAINING_PROMPT = """你是高中化学「化学表述训练」的虚拟评委。学生正在练习把实验方案/解题思路
**规范地讲出来**（对应错因体系里的「表述障碍」）。用户给你训练场景与学生的一组分句作答。

## 硬性产出规范
1. 单文件自包含 HTML（同讲评稿：无外链、化学式用 sub/sup/Unicode 上下标）。
2. **逐句色块标注**：每句后标注三类毛病，有则标、无则标「√」——
   红=术语不规范（给 \ce{} 规范写法）、黄=条件缺失（少温度/浓度/用量等条件词）、
   蓝=逻辑断链（因果跳步）。句内高亮用 <mark> 带背景色。
3. **三栏反馈表**：术语 / 条件 / 逻辑 三栏各 0–100 分 + 每栏一句改进建议。
4. **诚实标注**：化学记号错误必须给出规范写法；场景描述与比例夸张处如实标注。
5. 浅底大字，学生手机可读。

## 输出
自包含完整 HTML 文档。**文档末尾**必须附一段机器可读判定块（评分与建卡的数据源，
学生不可见，样式保持隐藏）：
<script type="application/json" id="exp-verdicts">
[{"sentence": "原句", "faults": ["terms", "condition", "logic" 的子集],
  "scores": {"terms": 0-100, "condition": 0-100, "logic": 0-100},
  "pass": true/false}]
</script>
pass=false 当且仅当：命中 ≥2 类毛病，或任一栏分数 <60。"""

REGISTRY: dict[str, TeachingSkill] = {
    s.name: s
    for s in (
        TeachingSkill(
            name="teaching_animation",
            title="教学动画",
            slash="/动画",
            artifact_kind="animation",
            prompt_template=ANIMATION_PROMPT,
            params=_ANIMATION_PARAMS,
            checks=(),  # 表达型产物：数值诚实靠标注约定（ADR-0028），无强制校验
        ),
        TeachingSkill(
            name="expression_training",
            title="表述特训",
            slash="/表述",
            artifact_kind="doc",
            prompt_template=EXPRESSION_TRAINING_PROMPT,
            params=_EXPRESSION_PARAMS,
            required_params=("scene", "student_sentences"),  # 无学生作答 fail-closed
            checks=(),
        ),
        TeachingSkill(
            name="review_deck",
            title="讲评稿",
            slash="/讲评",
            artifact_kind="doc",
            prompt_template=REVIEW_DECK_PROMPT,
            params=_REVIEW_PARAMS,
            required_params=("context_data",),  # 缺真实数据当场拒绝（学科论断型 fail-closed）
            checks=("four_dimension_review",),
        ),
    )
}

_SLASH_MAP: dict[str, str] = {s.slash: s.name for s in REGISTRY.values()}


def get_skill(name: str) -> TeachingSkill | None:
    """技能查找：精确名 → 大小写归一 → 标题精确 → 唯一包含匹配。

    模型实测会把技能名缩写（"animation" → teaching_animation，2026-10-05
    教师端首调失败实录）；包含匹配只在恰好命中一个技能时放行，命中多个或
    过短（<4 字符）仍返回 None 走「未知技能」错误，不让歧义蒙混过关。"""
    skill = REGISTRY.get(name)
    if skill is not None:
        return skill
    lowered = (name or "").strip().lower()
    if not lowered:
        return None
    for key, s in REGISTRY.items():
        if key.lower() == lowered or s.title.lower() == lowered:
            return s
    if len(lowered) >= 4:
        hits = [s for key, s in REGISTRY.items()
                if lowered in key.lower() or lowered in s.title.lower()]
        if len(hits) == 1:
            return hits[0]
    return None


def skill_by_slash(slash: str) -> TeachingSkill | None:
    return REGISTRY.get(_SLASH_MAP.get(slash, ""))


def list_skills() -> list[TeachingSkill]:
    return list(REGISTRY.values())
