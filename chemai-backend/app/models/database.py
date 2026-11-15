"""
ChemAI Database Models
基于PRD v1.0完整版附录D数据字典
"""
import json
import re

from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Date,
    DateTime, Text, JSON, ForeignKey, Enum, Index, create_engine, LargeBinary,
    UniqueConstraint
)
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import relationship, declarative_base, sessionmaker
from enum import Enum as PyEnum, IntEnum

Base = declarative_base()


class BarrierType(PyEnum):
    """障碍类型枚举"""
    CONCEPT = "concept"        # 概念理解型
    READING = "reading"        # 审题障碍型
    EXPRESSION = "expression"  # 表述障碍型


class RecordType(PyEnum):
    """记录类型枚举"""
    EXAM = "exam"       # 月考
    PRACTICE = "practice"  # 练习
    ASSIGNMENT = "assignment"  # 作业


class ExamStatus(PyEnum):
    """考试生命周期状态（CONTEXT.md 六态 + cancelled；P4 做实）"""
    DRAFT = "draft"
    PUBLISHED = "published"
    IN_PROGRESS = "in_progress"
    GRADING = "grading"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    CANCELLED = "cancelled"


class AnswerSource(PyEnum):
    """作答来源（判卷中心）：一切作答经同一落库路径，来源可辨可过滤"""
    ONLINE = "online"      # 学生线上提交
    OCR = "ocr"            # 答题卡批量识别
    MANUAL = "manual"      # 教师手动录入
    CHAT = "chat"          # 分步导学对话作答
    TRAINING = "training"  # 错题训练


class QuestionSource(PyEnum):
    """题目来源枚举"""
    AI_GENERATED = "ai_generated"
    MANUAL_SELECTED = "manual_selected"
    DAILY_PRACTICE = "daily_practice"
    OCR_IMPORT = "ocr_import"


class AuditStatus(PyEnum):
    """审核状态枚举"""
    PASSED = "passed"
    WARNING = "warning"
    BLOCKED = "blocked"


class Difficulty(IntEnum):
    """难度枚举 — 整数 1~5（见 CONTEXT.md：1=基础 2=中等偏易 3=中等 4=较难 5=竞赛/拔高）

    API 传输与数据库存储均使用整数值；成员名仅供代码内部引用。
    """
    EASY = 1        # 基础
    MODERATE = 2    # 中等偏易
    MEDIUM = 3      # 中等
    HARD = 4        # 较难
    COMPETITION = 5 # 竞赛/拔高

    @classmethod
    def coerce(cls, value) -> "Difficulty":
        """归一 int / 数字字符串 / 旧标签字符串 / 枚举成员 为 Difficulty"""
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            return cls.MEDIUM
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError:
                return cls.MEDIUM
        if isinstance(value, str) and value.strip().isdigit():
            try:
                return cls(int(value.strip()))
            except ValueError:
                return cls.MEDIUM
        return cls.from_label(value)

    @classmethod
    def from_label(cls, label: str) -> "Difficulty":
        """兼容旧标签字符串（easy/medium/hard/competition，含枚举名 MEDIUM 等）"""
        labels = {
            "easy": cls.EASY, "basic": cls.EASY,
            "moderate": cls.MODERATE,
            "medium": cls.MEDIUM,
            "hard": cls.HARD,
            "competition": cls.COMPETITION,
        }
        return labels.get(str(label).lower(), cls.MEDIUM)


class DifficultyColumn(TypeDecorator):
    """难度列（1~5 int）：写入侧统一 coerce 成 int。

    SQLite 不校验列类型，历史上枚举名/标签字符串曾原样入库，并经
    每日练习/智能练习组卷复制成批量脏数据（E2E E7 实测：smart/start
    对 'MEDIUM' int() 直接 500）。任何写入方（导入/建题/组卷复制）
    到这里归一，读侧保证拿到 int。"""
    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return Difficulty.coerce(value).value

    def process_result_value(self, value, dialect):
        return value


class Teacher(Base):
    """教师表"""
    __tablename__ = "teachers"

    teacher_id = Column(String(64), primary_key=True)
    school_id = Column(String(64), ForeignKey("schools.school_id"), nullable=True)
    name = Column(String(128), nullable=False)
    phone = Column(String(32), nullable=True)
    status = Column(String(32), default="approved")  # pending/approved/rejected
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    school = relationship("School", back_populates="teachers")
    teacher_subjects = relationship("TeacherClassSubject", back_populates="teacher")
    barrier_configs = relationship("BarrierConfig", back_populates="teacher")
    account = relationship("Account", back_populates="teacher", uselist=False)


class Student(Base):
    """学生表"""
    __tablename__ = "students"

    student_id = Column(String(64), primary_key=True)
    class_id = Column(String(64), ForeignKey("classes.class_id"), nullable=False)
    name = Column(String(128), nullable=False)
    phone = Column(String(32), nullable=True)
    status = Column(String(32), default="approved")  # pending/approved/rejected
    # 障碍类型占比 (JSON: {concept: 0.3, reading: 0.5, expression: 0.2})
    barrier_type = Column(JSON, default={"concept": 0.33, "reading": 0.33, "expression": 0.34})
    barrier_last_updated = Column(DateTime, nullable=True)
    diagnosis_requested_at = Column(DateTime, nullable=True)  # 学生级诊断去抖窗口标记
    exercises_completed = Column(Integer, default=0)
    last_exercise_at = Column(DateTime, nullable=True)
    bind_code = Column(String(64), nullable=True)  # 家长绑定码
    current_plan = Column(JSON, nullable=True)  # 当前学习计划 {plan_title, daily_tasks, ...}
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    student_class = relationship("Class", back_populates="students")
    account = relationship("Account", back_populates="student", uselist=False)
    parent_bindings = relationship("StudentParentBinding", back_populates="student")


class Account(Base):
    """账户表（登录认证）"""
    __tablename__ = "accounts"

    account_id = Column(String(64), primary_key=True)
    role = Column(String(32), nullable=False)  # admin/teacher/student/parent
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    teacher_id = Column(String(64), ForeignKey("teachers.teacher_id"), nullable=True)
    student_id = Column(String(64), ForeignKey("students.student_id"), nullable=True)
    parent_id = Column(String(64), ForeignKey("parents.parent_id"), nullable=True)
    status = Column(String(32), default="active")  # active/disabled
    current_token = Column(String(128), nullable=True)  # 当前登录token
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    teacher = relationship("Teacher", back_populates="account")
    student = relationship("Student", back_populates="account")
    parent = relationship("Parent", back_populates="account")


class Class(Base):
    """班级表"""
    __tablename__ = "classes"

    class_id = Column(String(64), primary_key=True)
    # 原有 `teacher_id` 列已删（阶段 6.2）：它建表时被写死 `None`、从来没有数据，
    # 却让三处代码静默失效（班主任恒 null、教师所带班级恒空）。
    # **班主任的唯一真源是 `teacher_class_subject.is_class_teacher`**；
    # 顺带删掉的 `Class.teacher` / `Teacher.classes` 两个 relationship 也依赖它
    # （留着会因推不出 join 条件而报错），且全仓库零引用。
    name = Column(String(128), nullable=False)
    student_count = Column(Integer, default=0)
    grade = Column(String(32), nullable=False)  # 年级名称，如"高一"
    subject = Column(String(32), default="化学")
    grade_id = Column(String(64), ForeignKey("grades.grade_id"), nullable=True)  # 年级外键
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    grade_rel = relationship("Grade", back_populates="classes")  # 重命名避免与grade列冲突
    students = relationship("Student", back_populates="student_class")
    exam_records = relationship("ExamRecord", back_populates="exam_class")
    teacher_subjects = relationship("TeacherClassSubject", back_populates="class_obj")


class ExamRecord(Base):
    """考试/练习记录表"""
    __tablename__ = "exam_records"

    record_id = Column(String(64), primary_key=True)
    class_id = Column(String(64), ForeignKey("classes.class_id"), nullable=False)
    type = Column(Enum(RecordType), default=RecordType.EXAM)
    name = Column(String(256), nullable=False)  # e.g. "高二上月考化学"
    question_stats = Column(JSON, nullable=True)  # 错题统计数据
    avg_score = Column(Float, nullable=True)
    total_students = Column(Integer, default=0)
    present_students = Column(Integer, default=0)  # 实际参考人数
    source = Column(Enum(QuestionSource), default=QuestionSource.AI_GENERATED)
    exam_date = Column(DateTime, nullable=True)
    status = Column(Enum(ExamStatus), nullable=True)  # 生命周期；存量经 migrate 回填
    published_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    grading_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    exam_class = relationship("Class", back_populates="exam_records")
    questions = relationship("Question", back_populates="exam_record")


# ── 题型归一（student-daily-loop X1）────────────────────────────────
# 规范值（CONTEXT.md「Question Type」canonical）：生成端（llm_service）产 choice/
# multi/fill/calc/experiment 简写，历年真题库产 multiple_choice 等旧值——
# 落库前统一到规范集合，别名映射集中在此一处。
QUESTION_TYPES = (
    "single_choice", "multi_choice", "true_false", "fill_blank",
    "short_answer", "essay", "calculation", "experiment",
)
_QUESTION_TYPE_ALIASES = {
    "choice": "single_choice",
    "multiple_choice": "multi_choice",
    "multi": "multi_choice",
    "fill": "fill_blank",
    "calc": "calculation",
    "judge": "true_false",
}


def normalize_question_type(value) -> str:
    """把各来源的题型值收敛到 QUESTION_TYPES；未知值回退 single_choice（存量默认）。"""
    v = str(value or "").strip().lower()
    v = _QUESTION_TYPE_ALIASES.get(v, v)
    return v if v in QUESTION_TYPES else "single_choice"


def split_answer_blanks(answer) -> list:
    """标准答案 → 逐空列表。多空两种来源：`;`/`；` 分隔的字符串（生成端约定）
    或已是 JSON 数组。单空返回 [answer]。"""
    if answer is None:
        return [""]
    if isinstance(answer, (list, tuple)):
        return [str(a) for a in answer] or [""]
    s = str(answer)
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [str(a) for a in parsed] or [""]
    except (ValueError, TypeError):
        pass
    parts = [p.strip() for p in re.split(r"[;；]", s)]
    return parts if len(parts) > 1 else [s.strip()]


class Question(Base):
    """题目表（AI生成+手动选题）"""
    __tablename__ = "questions"

    question_id = Column(String(64), primary_key=True)
    record_id = Column(String(64), ForeignKey("exam_records.record_id"), nullable=True)
    content = Column(Text, nullable=False)  # 题目正文
    # 题型（X1）：single_choice/multi_choice/true_false/fill_blank/short_answer/
    # essay/calculation/experiment；写入路径统一过 normalize_question_type
    question_type = Column(String(16), nullable=False, default="single_choice", server_default="single_choice")
    options = Column(JSON, nullable=True)  # 选项列表 ["A. xxx", "B. xxx", ...]
    # 参考答案：单空纯字符串；多空 `;` 分隔（生成端约定）或 JSON 数组，
    # 读侧统一经 split_answer_blanks 拆分
    answer = Column(Text, nullable=True)
    analysis = Column(Text, nullable=True)  # 题目解析
    knowledge_points = Column(JSON, nullable=True)  # 知识点标签列表
    probe_dimension = Column(String(16), nullable=True)  # 考察维度: concept/reading/expression（答错时归因到该维度）
    difficulty = Column(DifficultyColumn, default=Difficulty.MEDIUM.value)  # 1~5，见 Difficulty
    source = Column(Enum(QuestionSource), default=QuestionSource.AI_GENERATED)
    source_exam = Column(String(256), nullable=True)  # 手动选题来源: "2024年全国卷T15"
    audit_status = Column(Enum(AuditStatus), default=AuditStatus.PASSED)
    audit_report = Column(JSON, nullable=True)  # 三维审核报告内容
    historical_matches = Column(JSON, nullable=True)  # 历年真题关联列表
    # 审核维度 (用于F2安全审核)
    coefficient_correct = Column(Boolean, default=True)  # 系数配平
    condition_correct = Column(Boolean, default=True)  # 反应条件
    product_correct = Column(Boolean, default=True)  # 产物正确性
    structure_correct = Column(Boolean, default=True)  # 分子结构
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    exam_record = relationship("ExamRecord", back_populates="questions")
    student_answers = relationship("StudentAnswer", back_populates="question")


class KnowledgeEdge(Base):
    """知识点先修边（S6 knowledge-map / REQ-KED-001..003）。

    from_kp 掌握是 to_kp 的先修。状态 active/disabled——禁用即时退出
    就绪过滤，不等重新生成。生成=教师 agent 依证据提议+确认；无外部图谱数据。
    """
    __tablename__ = "knowledge_edges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_kp = Column(String(128), nullable=False, index=True)
    to_kp = Column(String(128), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="active")  # active/disabled
    reason = Column(String(512), nullable=True)  # 提议依据一句话（共现/错因迁移）
    proposed_by = Column(String(64), nullable=True)  # 提议者（教师 user_id）
    # KB-T11 图谱三列（2026-10-27）：edge_type=prerequisite|relates_to（prerequisite
    # 参与环检测与出题先修链）；confidence 0-1（LLM 提议置信）；source_doc=提议来源文档。
    edge_type = Column(String(32), nullable=True)
    confidence = Column(Float, nullable=True)
    source_doc = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class KBSubstance(Base):
    """物质节点（KB-T11）：中文名主键语义、化学式别名；关联边由
    knowledge_edges(relates_to) 挂到知识点。文档起草与图谱共用一份物质表。"""
    __tablename__ = "kb_substances"

    substance_id = Column(String(64), primary_key=True)  # 化学式规范化（如 Cl2）
    name = Column(String(128), nullable=False)           # 中文名（氯气）
    aliases_json = Column(Text, nullable=True)           # 别名列表 JSON
    source_doc = Column(String(256), nullable=True)      # 首次收录来源文档
    created_at = Column(DateTime, default=datetime.utcnow)


class KBTagMap(Base):
    """题库标签 → 知识点词典码的审定映射（KB2a）。

    映射内容经老师审定（kp_tag_map_m*.json 的 mapped 桶导入）；
    错误率聚合与出题定向按 kp_id 读这张表。"""
    __tablename__ = "kp_tag_map"

    tag_text = Column(String(128), primary_key=True)  # 题库原始标签
    kp_id = Column(String(64), nullable=False, index=True)  # 词典码
    confidence = Column(String(16), nullable=True)  # high/medium/low
    status = Column(String(16), nullable=True, default="accepted")
    accepted_by = Column(String(64), nullable=True)
    accepted_at = Column(DateTime, nullable=True)


class KBFrontmatter(Base):
    """知识点 → 文档 frontmatter 摘要（KB-T13）：检索图增强读 equations。

    摄取管线（kb_ingest）落库文档时 upsert（kp_ids 里的每个 kp 一行）；
    先修关系真源仍是 knowledge_edges。misconceptions_json 为 KB2b 从
    「## 常见易错点」提取的 [{wrong, right, why}] 三元，供出题 hint
    做陷阱定向；解析失败存空列表（不阻塞入库）。"""
    __tablename__ = "kb_frontmatter"

    kp_id = Column(String(64), primary_key=True)
    module = Column(String(32), nullable=True)
    theme = Column(String(64), nullable=True)
    equations_json = Column(Text, nullable=True)   # ["方程式1", …]
    prerequisites_json = Column(Text, nullable=True)
    misconceptions_json = Column(Text, nullable=True)  # [{"wrong","right","why"}…]
    source_doc = Column(String(256), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KPArtifactBinding(Base):
    """知识点 → 工件绑定（S6 / REQ-KMV-003）：节点播放入口。

    只允许绑定**本人（教师）名下**工件；学生视图只呈现已通过
    share_artifact 分享给该学生的版本（工件分发契约，读侧过滤）。"""
    __tablename__ = "kp_artifact_bindings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kp = Column(String(128), nullable=False, index=True)
    artifact_id = Column(String(64), nullable=False, index=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class StudentStreak(Base):
    """复习连击（student-daily-loop S1 / CONTEXT.md「Review Streak」）。

    规则内核在 streak_service（纯函数）；本表只存投影。current/best 天数，
    freeze_count 冻结卡（上限 2），freeze_week/repair_week 分别记冻结奖励与
    免费修复的 ISO 周配额（两者独立，共用会锁死修复）。"""
    __tablename__ = "student_streaks"

    student_id = Column(String(64), primary_key=True)
    current = Column(Integer, default=0)
    best = Column(Integer, default=0)
    freeze_count = Column(Integer, default=0)
    last_active_date = Column(Date, nullable=True)
    freeze_week = Column(String(12), nullable=True)   # 最近一次发冻结的 ISO 周键
    repair_week = Column(String(12), nullable=True)   # 最近一次用修复的 ISO 周键
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BarrierConfig(Base):
    """障碍诊断配置表"""
    __tablename__ = "barrier_configs"

    config_id = Column(String(64), primary_key=True)
    teacher_id = Column(String(64), ForeignKey("teachers.teacher_id"), nullable=False)
    # 各障碍类型触发阈值
    concept_threshold = Column(Integer, default=3)  # 概念理解型：连续错误N次触发
    reading_threshold = Column(Integer, default=2)  # 审题障碍型：连续错误N次触发
    expression_threshold = Column(Integer, default=3)  # 表述障碍型：连续错误N次触发
    mastery_threshold = Column(Integer, default=3)  # 掌握标准：连续答对N次
    auto_sync_to_student = Column(Boolean, default=False)  # 诊断结论是否自动同步学生端
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关系
    teacher = relationship("Teacher", back_populates="barrier_configs")


class StudentAnswer(Base):
    """学生作答记录表（用于障碍类型诊断）"""
    __tablename__ = "student_answers"

    answer_id = Column(String(64), primary_key=True)
    student_id = Column(String(64), ForeignKey("students.student_id"), nullable=False)
    question_id = Column(String(64), ForeignKey("questions.question_id"), nullable=False)
    exam_record_id = Column(String(64), ForeignKey("exam_records.record_id"), nullable=True)

    student_answer = Column(Text, nullable=True)  # 学生作答内容；多空为 JSON 数组（与 Question.answer 同约定）
    is_correct = Column(Boolean, nullable=False)
    confirmed = Column(Boolean, default=True)  # 成绩确认门：考试作答默认 False，教师确认后生效
    grade_reason = Column(String(256), nullable=True)  # 判定理由（PENDING_SEMANTIC=待语义判定）
    # 落行时真正用的标准答案键（老师粘贴的答案 > 题库）：扫描重判要拿它对齐，
    # 否则会用题库里的草稿值重判，把学生正确作答固化成语义判定错答
    answer_key = Column(Text, nullable=True)
    # 装置图等作答附件（批 2 X2）：文件相对路径数组，存 data/answer_attachments/
    attachments = Column(JSON, nullable=True)
    source = Column(Enum(AnswerSource), nullable=True)  # 作答来源；存量经 migrate 回填
    barrier_type = Column(Enum(BarrierType), nullable=True)  # 判定障碍类型

    # 连续错误/正确计数（用于触发障碍诊断）
    consecutive_errors = Column(Integer, default=0)
    consecutive_correct = Column(Integer, default=0)

    answered_at = Column(DateTime, default=datetime.utcnow)

    # 关系
    student = relationship("Student")
    question = relationship("Question")


class AssignmentBatch(Base):
    """练习分发布次（>SYNC_LIMIT 人异步逐生布置的清单与进度源）"""
    __tablename__ = "assignment_batches"

    batch_id = Column(String(64), primary_key=True)
    targets = Column(JSON, nullable=False)  # [student_id]
    source = Column(JSON, nullable=False)   # 取题来源 {"kind": adaptive|bank|manual, ...}
    name_hint = Column(String(128), nullable=True)
    # 终态：pending/running/done/partial/failed —— 老师据此区分「还在跑」与「已失败」
    status = Column(String(16), nullable=True)
    failed_targets = Column(JSON, nullable=True)  # {student_id: 失败原因}
    created_at = Column(DateTime, default=datetime.utcnow)


class KnowledgePoint(Base):
    """知识点表（化学知识图谱）"""
    __tablename__ = "knowledge_points"

    kp_id = Column(String(64), primary_key=True)
    name = Column(String(256), nullable=False)  # e.g. "盐类水解"
    category = Column(String(128), nullable=True)  # e.g. "电解质溶液"
    description = Column(Text, nullable=True)
    # PubChem关联CID
    pubchem_cid = Column(String(64), nullable=True)
    # 关联题目数
    question_count = Column(Integer, default=0)
    # 错误率 (动态计算)
    error_rate = Column(Float, nullable=True)
    # KB1a 知识点词典四列（2026-10-27）：code 是稳定主键语义（审定后不改），
    # theme/module/order_no 来自课标结构化清单；写入方 kb_dictionary.sync_dictionary。
    code = Column(String(32), nullable=True)
    theme = Column(String(64), nullable=True)
    module = Column(String(32), nullable=True)
    order_no = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HistoricalExam(Base):
    """历年真题表"""
    __tablename__ = "historical_exams"

    exam_id = Column(String(64), primary_key=True)
    source = Column(String(64), nullable=False)  # e.g. "全国卷2024"
    year = Column(Integer, nullable=False)
    question_number = Column(String(16), nullable=False)  # e.g. "T15"
    knowledge_points = Column(JSON, nullable=True)  # 知识点列表
    difficulty = Column(DifficultyColumn, default=Difficulty.MEDIUM.value)  # 1~5，见 Difficulty
    discrimination = Column(Float, nullable=True)  # 区分度 0-1
    content = Column(Text, nullable=False)  # 题目内容
    answer = Column(Text, nullable=True)
    analysis = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class QuestionSet(Base):
    """真题集表（用户自定义收藏集）"""
    __tablename__ = "question_sets"

    set_id = Column(String(64), primary_key=True)
    name = Column(String(256), nullable=False)  # 真题集名称
    teacher_id = Column(String(64), nullable=True)  # 创建者教师ID
    region = Column(String(64), nullable=True)  # 来源地区，如"全国卷"
    year = Column(Integer, nullable=True)  # 年份
    source = Column(String(64), nullable=True)  # 来源说明
    description = Column(Text, nullable=True)  # 描述
    question_count = Column(Integer, default=0)  # 题目数量
    is_system = Column(Boolean, default=False)  # 是否系统预设
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    questions = relationship("QuestionSetItem", back_populates="question_set")


class QuestionSetItem(Base):
    """真题集包含的题目关联表"""
    __tablename__ = "question_set_items"

    item_id = Column(String(64), primary_key=True)
    set_id = Column(String(64), ForeignKey("question_sets.set_id"), nullable=False)
    question_id = Column(String(64), ForeignKey("questions.question_id"), nullable=False)
    sort_order = Column(Integer, default=0)  # 排序顺序
    added_at = Column(DateTime, default=datetime.utcnow)

    # 关系
    question_set = relationship("QuestionSet", back_populates="questions")
    question = relationship("Question")


# ==================== 新增模型（Phase 1） ====================

class School(Base):
    """学校表"""
    __tablename__ = "schools"

    school_id = Column(String(64), primary_key=True)
    name = Column(String(100), nullable=False)
    region = Column(String(200), nullable=True)
    address = Column(String(255), nullable=True)
    phone = Column(String(20), nullable=True)
    current_term = Column(String(50), nullable=True)
    subjects = Column(JSON, nullable=True)  # ["语文","数学","英语","化学",...]
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    grades = relationship("Grade", back_populates="school")
    teachers = relationship("Teacher", back_populates="school")


class Grade(Base):
    """年级表"""
    __tablename__ = "grades"

    grade_id = Column(String(64), primary_key=True)
    school_id = Column(String(64), ForeignKey("schools.school_id"), nullable=True)
    name = Column(String(50), nullable=False)  # "高一"
    year = Column(Integer, nullable=False)  # 2025
    status = Column(Integer, default=1)  # 1:在读 0:已毕业
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关系
    school = relationship("School", back_populates="grades")
    classes = relationship("Class", back_populates="grade_rel")


class TeacherClassSubject(Base):
    """教师-班级-科目关联表（多对多）"""
    __tablename__ = "teacher_class_subject"

    id = Column(String(64), primary_key=True)
    teacher_id = Column(String(64), ForeignKey("teachers.teacher_id"), nullable=False)
    class_id = Column(String(64), ForeignKey("classes.class_id"), nullable=False)
    subject = Column(String(50), nullable=False)
    is_class_teacher = Column(Boolean, default=False)  # 是否班主任
    assigned_at = Column(DateTime, default=datetime.utcnow)

    # 关系
    teacher = relationship("Teacher", back_populates="teacher_subjects")
    class_obj = relationship("Class", back_populates="teacher_subjects")


class TeacherApplication(Base):
    """教师入驻申请表"""
    __tablename__ = "teacher_applications"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    phone = Column(String(32), nullable=False)
    school = Column(String(200), nullable=True)
    subject = Column(String(50), nullable=True)
    status = Column(String(20), default="pending")  # pending/approved/rejected
    reviewer_id = Column(String(64), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class OperationLog(Base):
    """操作日志表"""
    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=True)
    action = Column(String(50), nullable=False)  # create/update/delete/login
    target_type = Column(String(50), nullable=True)  # student/teacher/class/grade
    target_id = Column(String(64), nullable=True)
    detail = Column(JSON, nullable=True)
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Parent(Base):
    """家长表"""
    __tablename__ = "parents"

    parent_id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    phone = Column(String(32), nullable=True)
    email = Column(String(128), nullable=True)
    password_hash = Column(String(256), nullable=False)
    status = Column(String(32), default="active")  # active/disabled
    # 家长摘要频率（批 3 P1 / Guardian Summary）：weekly（默认）/daily/off，
    # 站内推送与外发摘要同一偏好驱动
    summary_frequency = Column(String(16), default="weekly", server_default="weekly")
    # 外发通知通道（ADR-0029 后家长侧中国化）：feishu/wecom/dingtalk/telegram；
    # webhook 为对应群机器人地址（host 白名单校验见 parent.py notify-channel 端点）
    notify_channel = Column(String(16), nullable=True)
    notify_webhook = Column(String(512), nullable=True)
    # 通知偏好（设置页个性化）：{"types": {"practice_completed": true, ...},
    # "quiet_hours": {"enabled": true, "start": "22:00", "end": "07:00"}}；
    # warning 不入键（安全类始终送达）。缺省=全开 + 免打扰 22:00-07:00
    notify_prefs = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    bindings = relationship("StudentParentBinding", back_populates="parent")
    notifications = relationship("ParentNotification", back_populates="parent")
    account = relationship("Account", back_populates="parent", uselist=False)


class StudentParentBinding(Base):
    """亲子绑定表"""
    __tablename__ = "student_parent_bindings"

    binding_id = Column(String(64), primary_key=True)
    student_id = Column(String(64), ForeignKey("students.student_id"), nullable=False)
    parent_id = Column(String(64), ForeignKey("parents.parent_id"), nullable=False)
    relation = Column(String(32), default="家长")  # 父亲/母亲/其他
    status = Column(String(32), default="active")  # pending/active/inactive
    bind_code = Column(String(64), nullable=False)  # 绑定验证码
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    student = relationship("Student", back_populates="parent_bindings")
    parent = relationship("Parent", back_populates="bindings")


class StudentNotification(Base):
    """学生通知记录表（与 ParentNotification 对称）"""
    __tablename__ = "student_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    notification_id = Column(String(64), nullable=False, unique=True, index=True)
    student_id = Column(String(64), ForeignKey("students.student_id"), nullable=False)
    type = Column(String(32), nullable=False)  # practice/diagnosis/warning/weekly_report/reminder
    title = Column(String(256), nullable=False)
    content = Column(Text, nullable=True)
    is_read = Column(Boolean, default=False)
    sent_at = Column(DateTime, nullable=True)

    # 关系
    student = relationship("Student")


class ParentNotification(Base):
    """家长通知记录表"""
    __tablename__ = "parent_notifications"

    notification_id = Column(String(64), primary_key=True)
    parent_id = Column(String(64), ForeignKey("parents.parent_id"), nullable=False)
    student_id = Column(String(64), ForeignKey("students.student_id"), nullable=False)
    type = Column(String(32), nullable=False)  # weekly_report/score_alert/learning_plan/reminder/daily_report
    title = Column(String(256), nullable=False)
    content = Column(Text, nullable=True)
    is_read = Column(Boolean, default=False)
    liked = Column(Boolean, default=False)  # 家长动态点赞（批 3 P1 / Parent Feed）
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关系
    parent = relationship("Parent", back_populates="notifications")
    student = relationship("Student")


# ⚠️ 合并说明（2026-09-14）：两支各定义过一份 StudentNotification，这里删掉了
# 「notification_id 主键 + payload / read_at / idempotency_key」那一版 —— 它和
# **真实库**对不上（三个库都是 `id` + `sent_at`，主库 2380 行），跑起来学生收件箱
# 直接 500。保留的定义在本文件下方（与库、与 feature/zcode 最新版一致）。
# 幂等不靠列：用 idempotency_key 参数派生出确定性的 notification_id。

class UploadSession(Base):
    """OCR 上传会话状态机"""
    __tablename__ = "upload_sessions"

    id = Column(String(64), primary_key=True)
    file_data = Column("file_data", LargeBinary, nullable=True)
    file_name = Column(String(512), nullable=True)
    mime_type = Column(String(128), nullable=True)
    status = Column(String(32), default="uploaded", index=True)
    preview_text = Column(Text, nullable=True)
    formula_result = Column(Text, nullable=True)
    detected_type = Column(String(32), nullable=True)
    baidu_task_id = Column(String(128), nullable=True)
    page_count = Column(Integer, default=0)
    pages_completed = Column(Integer, default=0)
    result_json = Column(Text, nullable=True)
    error_msg = Column(Text, nullable=True)
    degraded = Column(Boolean, default=False)
    version = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StudentSubmission(Base):
    """学生作答记录 — 关联考试和班级"""
    __tablename__ = "student_submissions"

    submission_id = Column(String(64), primary_key=True)
    exam_id = Column(String(64), ForeignKey("exam_records.record_id"), nullable=True)
    class_id = Column(String(64), nullable=True, index=True)
    student_name = Column(String(128), nullable=True)
    original_image = Column(String(512), nullable=True)
    graded_image = Column(String(512), nullable=True)
    answers_json = Column(Text, nullable=True)
    total_score = Column(Integer, default=0)
    submitted_at = Column(DateTime, default=datetime.utcnow)
    graded_at = Column(DateTime, nullable=True)


# 数据库初始化函数
_default_engine = None
_default_session_factory = None

# 钉死数据库路径：相对路径曾导致「从不同目录启动服务器 → 打开不同库文件 →
# 重启后数据看似全部消失」（用户反馈出现 N 次，2026-08-16 修复）。
# 无论进程从哪个目录启动，一律使用项目根目录下的 chemai.db。
# 2026-09-20 起单一真相 = settings.DATABASE_URL（config.BASE_DIR 锚定），
# 不再在此文件重复一套 __file__ 锚定口径。
import os as _os  # noqa: E402
from app.core.config import settings as _settings  # noqa: E402

_DEFAULT_DB_URL = _settings.DATABASE_URL
# 兼容：显式 DATABASE_URL 环境变量（绝对路径）仍可覆盖
# （注意 .env 里的 DATABASE_URL 只进 settings、不进 os.environ，对这里是不可见的）
_DEFAULT_DB_URL = _os.environ.get("DATABASE_URL") or _DEFAULT_DB_URL


def get_engine(database_url: str = _DEFAULT_DB_URL):
    """获取数据库引擎（单例）"""
    global _default_engine
    if _default_engine is None:
        _default_engine = create_engine(
            database_url,
            echo=False,
            connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
        )
        # Enable WAL mode for better concurrency
        if "sqlite" in database_url:
            from sqlalchemy import event
            @event.listens_for(_default_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                # 等锁而不是立刻报错（2026-10 演示实测）：WAL 是单写事务，定时作业
                # （OCR 每 5s、语义扫描每 120s）与一条长 Agent 回合撞上时，默认 5s
                # 就抛 `database is locked` —— 前台看到的是 500 + 页面空白 + 链路
                # 走到一半卡住。演示场景里「多等几秒」远好过「立刻失败」。
                cursor.execute("PRAGMA busy_timeout=15000")
                cursor.close()
    return _default_engine

def get_session_factory(database_url: str = _DEFAULT_DB_URL):
    """获取会话工厂（单例）"""
    global _default_session_factory
    if _default_session_factory is None:
        engine = get_engine(database_url)
        _default_session_factory = sessionmaker(bind=engine)
    return _default_session_factory

def get_db():
    """获取数据库会话（用于FastAPI依赖注入）"""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class LearningPlanHistory(Base):
    """学习计划历史记录"""
    __tablename__ = "learning_plan_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String(64), ForeignKey("students.student_id"), nullable=False)
    plan_title = Column(String(256), nullable=False)
    plan_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class LearningPlanDraft(Base):
    """学习计划草稿 —— 确认门的实体（ADR-0013）。

    一条草稿是一条版本链：同 draft_id 追加不可变版本行（version+1，带触发它的
    老师反馈），最新一行即当前版本。状态机：
    awaiting_confirmation →（revise: 新版本行，审批归零）* → sent；
    regenerate 将旧活跃版本行标 superseded 并另开新链。
    teacher_approved 是确认门的决定记录——发送由数据状态强制，不再靠进程级布尔。
    """
    __tablename__ = "learning_plan_drafts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    draft_id = Column(String(64), nullable=False, index=True)
    student_id = Column(String(64), ForeignKey("students.student_id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="awaiting_confirmation", index=True)
    content = Column(JSON, nullable=False)
    feedback = Column(Text, nullable=True)
    teacher_approved = Column(Boolean, nullable=False, default=False)
    approved_by = Column(String(64), nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PlanRun(Base):
    """计划运行 —— todolist 的实体（ADR-0013）。

    一次多步任务的有序步骤及其状态，跨回合存活：确认门/修订循环发生时 run
    挂起持久化，刷新页面、服务重启后可重同步。每会话最多一个活跃 run，
    新 plan_task 作废旧 run（status=superseded）。
    """
    __tablename__ = "plan_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, index=True)
    conversation_id = Column(String(64), nullable=False, index=True)
    goal = Column(Text, nullable=False)
    steps = Column(JSON, nullable=False)
    status = Column(String(32), nullable=False, default="running", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PlanStepArtifact(Base):
    """计划步骤的结构化产物 —— 让 `depends_on` 从对话历史里解耦（C2）。

    原先跨步引用（`${step_N.field}`）只能靠**对话历史**带：早期步骤的输出就是
    一条消息，而 `trim_context`（agent/context_manager.py:20-73）超过 30 条只留
    最近 6 条 + ≤10 条关键词命中，其余压成 ≤200 字摘要。于是「出本单元整套复习资料」
    这类任务里第 5 步要引用的第 1 步产物，早就从模型视野里消失了——只剩 plan_runs
    里那串状态。产物按步落在这张表，执行下一步时按键读回，与还剩几条消息无关。

    为什么是**新表**而不是 plan_runs 的新列：`create_all` 只建缺失的表、不给存量表
    加列（tests/conftest.py:41 记过这个坑），而演示库 chemai.db 是 git 跟踪的活库，
    加列必须配 startup DDL + 手工迁移，否则第一个 plan_runs 查询就 no such column。
    新表由 create_all 直接建出，零迁移、零启动期 DDL。

    按 run_id 归属而非 conversation_id：新 plan_task 会作废旧 run（status=superseded），
    旧 run 的产物不得漏进新 run 的参数表——产物是**这一次拆解**的证据。
    """
    __tablename__ = "plan_step_artifacts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, index=True)
    conversation_id = Column(String(64), nullable=False, index=True)
    step = Column(Integer, nullable=False)
    artifact = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# 合并说明（2026-09-14）：这里曾出现第二份 StudentNotification（与上面同名同表），
# 已删 —— 两份列定义都与真实库一致，保留上面那份（与 feature/zcode 最新形状相同）。

class ParentReportDraft(Base):
    """家长报告草稿 —— 第二个确认门工件（ADR-0013；issue #12）。

    与 LearningPlanDraft 同构：draft_id 版本链、status 状态机、feedback 审计链、
    teacher_approved 门钥匙。不做通用工件表合并——第三种工件出现时再议。
    """
    __tablename__ = "parent_report_drafts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    draft_id = Column(String(64), nullable=False, index=True)
    student_id = Column(String(64), ForeignKey("students.student_id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="awaiting_confirmation", index=True)
    content = Column(JSON, nullable=False)
    feedback = Column(Text, nullable=True)
    teacher_approved = Column(Boolean, nullable=False, default=False)
    approved_by = Column(String(64), nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConversationGate(Base):
    """会话↔草稿门的持久映射（ADR-0013；#13 冒烟补丁 2）。

    门开时写入、读取时校验草稿状态自愈——服务重启后 /history 仍能重建
    待确认门卡（run 步骤路径只覆盖 todolist 内的门，裸生成门靠本表）。
    """
    __tablename__ = "conversation_gates"
    conversation_id = Column(String(64), primary_key=True)
    draft_id = Column(String(64), nullable=False, index=True)
    artifact = Column(String(32), nullable=False, default="learning_plan")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentGate(Base):
    """通用确认门实体（ADR-0015 阶段 2：门合一）。

    draft / boolean 两个 adapter 落同一 seam：kind=draft 绑定草稿
    （draft_id+version），kind=boolean 携带工具名+参数快照（重放依据）。
    gate_id 是决定与重放的唯一绑定键——一决定一动作，批准门 A 永不执行
    门 B 的动作。

    「每会话最多一个 open」是不变量而非数据库约束（R18）：历史双登记与
    面板直发都能留下多行 open 或假门，判定侧必须按 `MAX(id)` 取最新一行，
    收敛由 `supersede_stale_gates` 显式负责，不靠读路径顺带自愈。
    """
    __tablename__ = "agent_gates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    gate_id = Column(String(80), nullable=False, unique=True, index=True)
    conversation_id = Column(String(64), nullable=False, index=True)
    kind = Column(String(16), nullable=False, default="draft")  # draft | boolean
    tool_name = Column(String(64), nullable=False, default="")
    kwargs = Column(JSON, nullable=False, default=dict)
    draft_id = Column(String(64), nullable=True, index=True)
    version = Column(Integer, nullable=True)
    action = Column(String(32), nullable=False, default="")  # send_student | send_parent | ""
    message = Column(Text, nullable=False, default="")
    # open      待老师决定
    # executing 已被某次批准占住、动作执行中（结果未知，但那个进程还活着）
    # in_doubt  熬过一次进程重启的 executing —— 动作可能跑过、可能没跑，等老师裁定
    #           （见 gate_service.sweep_stuck_executing，启动时扫描）
    # executed / cancelled / superseded：终态
    status = Column(String(16), nullable=False, default="open", index=True)
    # 本门绑定的 Plan Run 步号（gate-lifecycle 批次 1）——门的**唯一**步归属。
    # 决定与重放都按它定位，不再按工具名去猜：`_GATE_STEP_SKILL` 那张映射表假设
    # 计划里存在一个 `send_learning_plan` 步，而计划是 LLM 生成的——2026-10-16
    # 端到端实测里根本没有那一步，于是门谁也没挂、run 永远收不了尾。
    # NULL = 未记录（本列之前的存量门）→ 调用方回退旧行为。
    plan_step = Column(Integer, nullable=True)
    # 重放载荷指纹（gate-lifecycle 2.6）：开门时按 `tool_name + kwargs` 算一次钉在这里，
    # 重放**之前**重新对一次 —— 老师批的是哪一份，就只能执行哪一份。
    # 门那行是卡片的来源、内存 pending 是执行的对象，两者在四条开门路径上今天一致，
    # 但此前没有任何东西守着这个一致性：门行被改一笔、或载荷换了来源，老师批的和
    # 他拿到的东西就悄悄不是一回事了，且毫无迹象。对不上 → 拒绝执行（fail-closed）。
    # NULL = 未记录（本列之前的存量门）→ 回退旧行为放行，**不回填**（同 plan_step：
    # 回填就是拿推出来的值冒充记录，而且会把存量开着的门当场作废）。
    args_hash = Column(String(64), nullable=True)
    # 上面那个指纹的算法/载荷结构版本。将来改载荷形状时 +1，旧行即被拒
    # （拿新代码去解旧载荷 = 按今天的理解执行昨天的东西）。
    schema_version = Column(Integer, nullable=True)
    # 计时起点（发9）：created_at 是首次开门时刻，重开门不刷新它，
    # 用 created_at 倒计时会把两个月前的门判成超期。两个开门分支都必须写本列。
    last_opened_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)
    # 过期作业（T17）与待确认列表（T8）都走 status 过滤 + 等待时长排序
    __table_args__ = (Index("ix_agent_gates_status_last_opened", "status", "last_opened_at"),)


class AgentGateDecision(Base):
    """门决定账本（第 1 项前置一）：只 insert 不 update。

    `agent_gates` 是当前态表，重开门会覆盖上一轮的门面，"谁在什么时候
    批准或打回了哪扇门"必须另有落点。统计端点、待确认页、审批人显示只认
    本表，不读 `agent_gates.version - 1` 或草稿行的 `approved_by`。

    `conversation_id` 可空：`send_plan_to_parent` 的 REST 请求只带 student_id，
    没有会话上下文。这类记录归属留空并在统计里显式排除，实现期不得静默丢。
    """
    __tablename__ = "agent_gate_decisions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    gate_id = Column(String(80), nullable=False, index=True)
    conversation_id = Column(String(64), nullable=True, index=True)
    decision = Column(String(16), nullable=False, index=True)  # approved | rejected | superseded | expired | cancelled
    actor_user_id = Column(String(64), nullable=True)
    tool_name = Column(String(64), nullable=False, default="")
    kind = Column(String(16), nullable=False, default="")
    detail = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class AgentGateMeta(Base):
    """门相关的全库单行元数据（key/value）。

    当前只有一个键 `gate_epoch`：本次带门改动的部署时刻，由启动期幂等迁移盖章。
    4b 的过期口径是 `max(last_opened_at, gate_epoch)`——缺章时 fail-closed，
    只提示 stale、不转 expired（R5）。
    """
    __tablename__ = "agent_gate_meta"
    key = Column(String(64), primary_key=True)
    value = Column(String(64), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Artifact(Base):
    """工件 —— AI 生成的可预览产物（能力升级批次 A1，spec：Artifact API 接缝）。

    一条工件是一条版本链：每次保存追加不可变版本行（seq+1，见 ArtifactVersion），
    最新一行即当前版本。迭代轻量（任意次快照）；对外分发（分享给学生/导出文件）
    才走确认门。文件真源在盘上（默认工作区 AI生成/artifacts/<日期>/，见
    artifact_service.artifact_base_dir），库表只存索引——SSE artifact 事件只传
    引用不传全文（tool_result 载荷 8000 截断）。归属收窄：工件是老师的跨会话
    资产，别人的工件在读路径上一律不存在。
    """
    __tablename__ = "artifacts"
    id = Column(String(64), primary_key=True)
    owner_teacher_id = Column(String(64), ForeignKey("teachers.teacher_id"), nullable=False, index=True)
    kind = Column(String(32), nullable=False)  # animation/slides/doc/structure/table
    source_skill = Column(String(64), nullable=True, index=True)  # 产出的技能名（版本续接按它隔离）
    title = Column(String(255), nullable=False, default="")
    conversation_id = Column(String(64), nullable=True, index=True)
    current_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ArtifactVersion(Base):
    """工件的不可变版本行：seq 递增，file_path 指向盘上快照，旧文件永不覆盖。

    (artifact_id, seq) 唯一约束钉死并发追版本的读-改-写竞态：两个并发 save
    只可能成功一个，另一个吃 IntegrityError（service 捕获后报冲突），不会出现
    两条同 seq 行互相覆盖文件。
    """
    __tablename__ = "artifact_versions"
    __table_args__ = (UniqueConstraint("artifact_id", "seq", name="uq_artifact_version"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_id = Column(String(64), ForeignKey("artifacts.id"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    file_path = Column(String(512), nullable=False)
    url = Column(String(512), nullable=False, default="")
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class MemoryCard(Base):
    """记忆卡（能力升级批次 B）——错题的间隔重复调度实体（CONTEXT.md「Memory Card」）。

    判卷完成按（学生, 题目）自动 upsert（零老师操作）；调度内核是 FSRS
    （py-fsrs，Anki 同款记忆模型），**升级替换**既有固定节点艾宾浩斯阶梯
    （ReviewTask）：学生端复习入口契约不变，阶梯退役。字段直接落 FSRS Card
    的状态量（due/stability/difficulty/state），不用 JSON 列——到期查询要吃索引。
    复习信号双轨：重做自动判定（rating 1/3）为主，自评三档（忘了/模糊/记得 →
    1/2/3）兜底。
    """
    __tablename__ = "memory_cards"
    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String(64), ForeignKey("students.student_id"), nullable=False, index=True)
    question_id = Column(String(64), ForeignKey("questions.question_id"), nullable=False)
    # FSRS Card 状态
    due = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    stability = Column(Float, nullable=False, default=0.0)
    difficulty = Column(Float, nullable=False, default=0.0)
    state = Column(Integer, nullable=False, default=0)  # 0=New 1=Learning 2=Review 3=Relearning
    step = Column(Integer, nullable=True)
    last_review = Column(DateTime, nullable=True)
    reps = Column(Integer, nullable=False, default=0)
    lapses = Column(Integer, nullable=False, default=0)
    # 最近一次信号（重做对/错 或 自评 1/2/3）——诊断与家长报告的原料
    last_signal = Column(String(16), nullable=True)  # correct/wrong/forgot/vague/remembered
    source = Column(String(16), nullable=False, default="grading")  # grading/migrated
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("student_id", "question_id", name="uq_memory_card"),)


class QuestionCalibration(Base):
    """题目 IRT 标定（能力升级批次 C）——1PL：区分度 a 固定 1.0，难度 b 是
    经验正确率的 logit（按样本量向 0 收缩：n 越小越靠近先验 0）。

    n_answers 是「数据门槛」的唯一事实来源：选题服务的降级规则（≥30 条作答
    才用标定参数，否则退回难度 1-5 先验）读这里的 n，不存在"标定了一半"的
    中间态（CONTEXT.md 批次 C 口径）。
    """
    __tablename__ = "question_calibration"
    question_id = Column(String(64), primary_key=True)
    a = Column(Float, nullable=False, default=1.0)
    b = Column(Float, nullable=False, default=0.0)
    n_answers = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KPLayout(Base):
    """知识星图坐标（star-map-interactions REQ-SMI-006）：全校一份。

    布局只取决于知识点与先修边，与班级无关（班级只影响亮度）——所以不按班存。
    写入语义是「只插入缺失、从不覆盖」：首个打开星图的教师在布局收敛后补齐
    坐标，之后所有人看到同一张图；多位教师并发首访也幂等。新表走 create_all，
    零迁移。
    """
    __tablename__ = "kp_layout"
    kp_id = Column(String(64), primary_key=True)
    x = Column(Float, nullable=False)
    y = Column(Float, nullable=False)
    z = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KBReviewRecord(Base):
    """KB 审校包状态（kb-review-ui）。

    审校包本体是 git 管理的 `content/kb/**/*.proposals.json`（纯语料，状态不进
    文件）；这里只存「批准/驳回」这张状态单，键是相对 content/kb 的路径。
    content_hash 是批准时的文件指纹——再次查看时比对，文件被改过就提示重新评估。
    状态机 pending → approved / rejected，approved 后拒绝重复落库。
    """
    __tablename__ = "kb_review_records"
    source_path = Column(String(512), primary_key=True)  # 相对 content/kb 的路径
    status = Column(String(16), nullable=False, default="pending")  # pending/approved/rejected
    reviewer_id = Column(String(64), nullable=True)
    reason = Column(String(512), nullable=True)
    content_hash = Column(String(64), nullable=True)  # 批准落库时的 sha256
    import_report = Column(JSON, nullable=True)  # import_proposals 的报告
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class KBJob(Base):
    """KB 摄取任务（kb-ingest-ui）。

    运行态在进程内 JobRegistry，历史落这里（重启可查）。服务启动时把
    queued/running 标成 interrupted —— 进程死了任务不可能还活着。
    摄取采用影子集合构建-切换：成功才切 active，失败清理影子，旧索引不受影响。
    """
    __tablename__ = "kb_jobs"
    job_id = Column(String(64), primary_key=True)
    module = Column(String(64), nullable=False)  # content/kb 下的册名
    status = Column(String(16), nullable=False, default="queued")  # queued/running/succeeded/failed/interrupted
    progress = Column(JSON, nullable=True)  # {"docs": n, "chunks": n, ...}
    error = Column(String(1024), nullable=True)
    shadow_collection = Column(String(128), nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


class KBCollectionState(Base):
    """KB 检索活跃集合指向（kb-ingest-ui）：单行表，影子切换只改这一行。

    读侧（agent/tools/tutoring._kb_resources 等）经 resolver 读它；缺省行
    不存在 = 指向 "kb_chunks"（历史缺省名，行为零变化）。
    """
    __tablename__ = "kb_collection_state"
    id = Column(Integer, primary_key=True, default=1)
    active_collection = Column(String(128), nullable=False, default="kb_chunks")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class QaTrace(Base):
    """问答证据链（rag-observability）：一次检索问答的完整快照。

    与 OperationLog 分开：语义不同（问答 vs 管理动作）、量级差百倍、保留期不同。
    hits / retrieval_config 存 JSON 快照 —— 块之后可能被删改，快照保证事后可读。
    """
    __tablename__ = "qa_traces"
    trace_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=True)
    role = Column(String(32), nullable=True)
    conversation_id = Column(String(128), nullable=True)
    question = Column(Text, nullable=False)
    retrieval_config = Column(JSON, nullable=True)
    hits = Column(JSON, nullable=True)
    zero_hit = Column(Boolean, default=False)
    top1_score = Column(Float, nullable=True)   # 1 - distance，越高越相关
    answer = Column(Text, nullable=True)
    refusal_flag = Column(Boolean, default=False)
    latency_ms = Column(Integer, nullable=True)
    model_name = Column(String(64), nullable=True)
    kb_index_version = Column(String(128), nullable=True)
    feedback = Column(String(16), default="none")   # up / down / none
    feedback_text = Column(Text, nullable=True)
    ops_label = Column(String(16), nullable=True)   # good / bad
    bad_reason = Column(String(32), nullable=True)  # 检索错/chunk差/缺知识/幻觉/不完整/超纲/误拒
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class RagRetrievalTest(Base):
    """召回测试历史（rag-retrieval-test）：每次测试留一条，进页就能看到最近跑过什么。"""
    __tablename__ = "rag_retrieval_tests"
    id = Column(Integer, primary_key=True, autoincrement=True)
    query = Column(Text, nullable=False)
    top_k = Column(Integer, nullable=True)
    threshold = Column(Float, nullable=True)
    graph_enabled = Column(Boolean, default=True)
    mode = Column(String(16), nullable=True)      # ok / weak / miss
    hits_count = Column(Integer, default=0)
    top1_score = Column(Float, nullable=True)
    elapsed_ms = Column(Integer, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class GoldenQuestion(Base):
    """金标问题（rag-eval-regression）：评测回归的期望集。"""
    __tablename__ = "golden_questions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    question = Column(Text, nullable=False)
    expected_chunks = Column(JSON, nullable=True)   # [{chunk_id, source_path, section_no}]
    expected_answer = Column(Text, nullable=True)
    match_mode = Column(String(16), default="any")  # any / all
    tags = Column(String(256), nullable=True)       # 章节/知识点，逗号分隔
    status = Column(String(16), default="draft")    # draft / active / stale / paused
    source = Column(String(32), default="manual")   # manual / trace / retrieval_test
    source_trace_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class RagEvalRun(Base):
    """一次评测运行（rag-eval-regression）。"""
    __tablename__ = "rag_eval_runs"
    run_id = Column(String(64), primary_key=True)
    status = Column(String(16), default="queued")   # queued/running/succeeded/failed
    metrics = Column(JSON, nullable=True)
    items = Column(JSON, nullable=True)             # 逐题结果
    config_snapshot = Column(JSON, nullable=True)
    error = Column(String(512), nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


class KbChunkOverride(Base):
    """分块禁用表（KB 文档/分块集中管理）。

    chroma 的 chunk 是**派生数据**：正文仍在 content/kb 的 md 里，重摄取会重建。
    所以"禁用某个分块"不写进 chroma metadata（会被重摄取覆盖），而是单独记在这里，
    检索时按 id 排除（kb_context.build_kb_context 的 exclude_ids）。
    """
    __tablename__ = "kb_chunk_overrides"
    chunk_id = Column(String(128), primary_key=True)
    disabled = Column(Boolean, nullable=False, default=True)
    updated_by = Column(String(64), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RuntimeSettingRow(Base):
    """运行时开关覆写（runtime-settings）。

    白名单键的**覆写值**；未覆写的键回落 config 的 env 种子默认。
    白名单是封闭集合（service 层定义），这里只是存储。凭据类配置
    （API key/密钥）NEVER 入此表 —— 那是 env/密管的领地。
    """
    __tablename__ = "runtime_settings"
    key = Column(String(64), primary_key=True)
    value = Column(String(256), nullable=False)
    updated_by = Column(String(64), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IntegrationApiKey(Base):
    """LMS 集成 API 密钥（integration-persistence）。

    明文只在签发响应里出现一次，落库是 SHA-256 哈希（密钥为高熵随机串，
    无盐哈希在此场景可接受：防拖库读明文，不防高熵输入的彩虹表）。
    删除 = 删行，verify 立即失效。
    """
    __tablename__ = "integration_api_keys"
    key_id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    secret_hash = Column(String(64), nullable=False)
    permissions = Column(JSON, nullable=True)  # 与 PERMISSION_PRESETS 同口径的资源:动作 列表
    status = Column(String(16), nullable=False, default="active")  # active/revoked
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class IntegrationWebhook(Base):
    """LMS 集成 Webhook 注册（integration-persistence）：重启不丢的真源。

    WebhookService 的内存字典退化为缓存；表是权威。secret 需原文签名投递，
    必须可逆 —— 不脱敏，但永不进任何列表响应/日志。
    """
    __tablename__ = "integration_webhooks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False, unique=True)
    url = Column(String(512), nullable=False)
    secret = Column(String(256), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class IntegrationLog(Base):
    """LMS 集成日志（integration-persistence）：投递与配置动作留痕，只读消费。"""
    __tablename__ = "integration_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    log_type = Column(String(16), nullable=False)  # delivery/config
    event_type = Column(String(64), nullable=True)
    url = Column(String(512), nullable=True)
    status = Column(String(16), nullable=True)  # success/failure
    duration_ms = Column(Integer, nullable=True)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class IntegrationConfig(Base):
    """LMS/SFTP 配置单行表（integration-persistence）：JSON 存储，重启不丢。"""
    __tablename__ = "integration_config"
    id = Column(Integer, primary_key=True, default=1)
    config = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db(database_url: str = "sqlite:///./chemai.db"):
    """初始化数据库（创建所有表）"""
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    return engine

# 为了兼容旧代码，也导出默认engine
engine = get_engine()
