"""
数据库迁移脚本
处理新增表和表结构变更

使用方式:
    python -m app.utils.migrate
"""
import os
import sys
from datetime import datetime

from sqlalchemy import text, inspect

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from app.models.database import get_engine, get_session_factory  # noqa: E402


def get_existing_tables(engine):
    """获取现有表列表（兼容不同SQLAlchemy版本）"""
    inspector = inspect(engine)
    return inspector.get_table_names()


def migrate():
    """执行数据库迁移"""
    engine = get_engine()
    session_factory = get_session_factory()
    session = session_factory()

    print("=" * 50)
    print("ChemAI Database Migration")
    print("=" * 50)

    try:
        # 获取现有表列表
        existing_tables = get_existing_tables(engine)
        print(f"\nExisting tables: {existing_tables}")

        # 检查是否需要迁移
        if 'schools' in existing_tables:
            print("\n[OK] schools table exists, skip")
        else:
            create_schools_table(engine)
            print("[OK] schools table created")

        if 'grades' in existing_tables:
            print("[OK] grades table exists, skip")
        else:
            create_grades_table(engine)
            print("[OK] grades table created")

        if 'teacher_class_subject' in existing_tables:
            print("[OK] teacher_class_subject table exists, skip")
        else:
            create_teacher_class_subject_table(engine)
            print("[OK] teacher_class_subject table created")

        if 'teacher_applications' in existing_tables:
            print("[OK] teacher_applications table exists, skip")
        else:
            create_teacher_applications_table(engine)
            print("[OK] teacher_applications table created")

        if 'operation_logs' in existing_tables:
            print("[OK] operation_logs table exists, skip")
        else:
            create_operation_logs_table(engine)
            print("[OK] operation_logs table created")

        # 处理现有表的结构变更
        migrate_classes_table(engine)
        migrate_classes_teacher_id(engine)
        migrate_teachers_table(engine)
        migrate_role_values(engine)
        backfill_legacy_conversations(engine)
        migrate_student_answers_source(engine)
        create_assignment_batches_table(engine)
        create_student_notifications_table(engine)
        migrate_exam_records_status(engine)
        migrate_loop_remediation(engine)
        migrate_questions_difficulty_enum(engine)
        migrate_question_type(engine)
        migrate_answer_attachments(engine)
        migrate_parent_engagement(engine)
        migrate_parent_notify_channel(engine)
        # 也挂 CLI：启动期已幂等跑过，这里跑第二次必须无副作用（幂等性验收面）
        migrate_agent_gates_last_opened(engine)
        migrate_knowledge_points_kb(engine)
        migrate_kb_graph(engine)
        migrate_kb_frontmatter(engine)
        migrate_kb_tag_map(engine)
        migrate_agent_gates_plan_step(engine)
        migrate_agent_gates_replay_payload(engine)
        create_kb_review_records_table(engine)
        create_kb_jobs_table(engine)
        create_kb_collection_state_table(engine)
        create_runtime_settings_table(engine)
        create_kb_chunk_overrides_table(engine)
        create_rag_tables(engine)
        create_integration_tables(engine)

        print("\n" + "=" * 50)
        print("Migration completed!")
        print("=" * 50)

    except Exception as e:
        print(f"\n[ERROR] Migration failed: {e}")
        session.rollback()
        raise
    finally:
        session.close()


def create_kb_review_records_table(engine):
    """创建 KB 审校记录表（kb-review-ui；create_all 之外为存量库补建）"""
    sql = text("""
        CREATE TABLE IF NOT EXISTS kb_review_records (
            source_path VARCHAR(512) PRIMARY KEY,
            status VARCHAR(16),
            reviewer_id VARCHAR(64),
            reason VARCHAR(512),
            content_hash VARCHAR(64),
            import_report JSON,
            reviewed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    with engine.connect() as conn:
        conn.execute(sql)
        conn.commit()


def create_kb_jobs_table(engine):
    """创建 KB 摄取任务表（kb-ingest-ui）"""
    sql = text("""
        CREATE TABLE IF NOT EXISTS kb_jobs (
            job_id VARCHAR(64) PRIMARY KEY,
            module VARCHAR(64),
            status VARCHAR(16),
            progress JSON,
            error VARCHAR(1024),
            shadow_collection VARCHAR(128),
            created_by VARCHAR(64),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        )
    """)
    with engine.connect() as conn:
        conn.execute(sql)
        conn.commit()


def create_kb_collection_state_table(engine):
    """创建 KB 活跃集合指向表（kb-ingest-ui 影子切换）：单行，缺省 kb_chunks"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS kb_collection_state (
                id INTEGER PRIMARY KEY,
                active_collection VARCHAR(128),
                updated_at TIMESTAMP
            )
        """))
        conn.execute(text(
            "INSERT OR IGNORE INTO kb_collection_state (id, active_collection) VALUES (1, 'kb_chunks')"))
        conn.commit()


def create_rag_tables(engine):
    """RAG 三表：问答证据链 / 金标问题 / 评测运行（rag-observability + rag-eval-regression）"""
    stmts = [
        """CREATE TABLE IF NOT EXISTS qa_traces (
            trace_id VARCHAR(64) PRIMARY KEY,
            user_id VARCHAR(64),
            role VARCHAR(32),
            conversation_id VARCHAR(128),
            question TEXT NOT NULL,
            retrieval_config JSON,
            hits JSON,
            zero_hit BOOLEAN,
            top1_score FLOAT,
            answer TEXT,
            refusal_flag BOOLEAN,
            latency_ms INTEGER,
            model_name VARCHAR(64),
            kb_index_version VARCHAR(128),
            feedback VARCHAR(16),
            feedback_text TEXT,
            ops_label VARCHAR(16),
            bad_reason VARCHAR(32),
            created_at TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS rag_retrieval_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            top_k INTEGER,
            threshold FLOAT,
            graph_enabled BOOLEAN,
            mode VARCHAR(16),
            hits_count INTEGER,
            top1_score FLOAT,
            elapsed_ms INTEGER,
            created_by VARCHAR(64),
            created_at TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS golden_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            expected_chunks JSON,
            expected_answer TEXT,
            match_mode VARCHAR(16),
            tags VARCHAR(256),
            status VARCHAR(16),
            source VARCHAR(32),
            source_trace_id VARCHAR(64),
            created_at TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS rag_eval_runs (
            run_id VARCHAR(64) PRIMARY KEY,
            status VARCHAR(16),
            metrics JSON,
            items JSON,
            config_snapshot JSON,
            error VARCHAR(512),
            created_by VARCHAR(64),
            created_at TIMESTAMP,
            finished_at TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS ix_qa_traces_created ON qa_traces (created_at)",
    ]
    with engine.connect() as conn:
        for sql in stmts:
            conn.execute(text(sql))
        conn.commit()


def create_kb_chunk_overrides_table(engine):
    """创建分块禁用表（KB 文档/分块集中管理）"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS kb_chunk_overrides (
                chunk_id VARCHAR(128) PRIMARY KEY,
                disabled BOOLEAN NOT NULL DEFAULT 1,
                updated_by VARCHAR(64),
                updated_at TIMESTAMP
            )
        """))
        conn.commit()


def create_runtime_settings_table(engine):
    """创建运行时开关覆写表（runtime-settings）"""
    sql = text("""
        CREATE TABLE IF NOT EXISTS runtime_settings (
            key VARCHAR(64) PRIMARY KEY,
            value VARCHAR(256),
            updated_by VARCHAR(64),
            updated_at TIMESTAMP
        )
    """)
    with engine.connect() as conn:
        conn.execute(sql)
        conn.commit()


def create_integration_tables(engine):
    """创建 LMS 集成四表（integration-persistence；create_all 之外为存量库补建）"""
    stmts = [
        """CREATE TABLE IF NOT EXISTS integration_api_keys (
            key_id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(128),
            secret_hash VARCHAR(64),
            permissions JSON,
            status VARCHAR(16),
            created_by VARCHAR(64),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS integration_webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type VARCHAR(64) UNIQUE,
            url VARCHAR(512),
            secret VARCHAR(256),
            enabled BOOLEAN,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS integration_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_type VARCHAR(16),
            event_type VARCHAR(64),
            url VARCHAR(512),
            status VARCHAR(16),
            duration_ms INTEGER,
            detail JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS integration_config (
            id INTEGER PRIMARY KEY,
            config JSON,
            updated_at TIMESTAMP
        )""",
        "INSERT OR IGNORE INTO integration_config (id, config) VALUES (1, '{}')",
    ]
    with engine.connect() as conn:
        for sql in stmts:
            conn.execute(text(sql))
        conn.commit()


def create_schools_table(engine):
    """创建学校表"""
    sql = text("""
        CREATE TABLE IF NOT EXISTS schools (
            school_id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            region VARCHAR(200),
            address VARCHAR(255),
            phone VARCHAR(20),
            current_term VARCHAR(50),
            subjects JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    with engine.connect() as conn:
        conn.execute(sql)
        conn.commit()


def create_grades_table(engine):
    """创建年级表"""
    sql = text("""
        CREATE TABLE IF NOT EXISTS grades (
            grade_id VARCHAR(64) PRIMARY KEY,
            school_id VARCHAR(64),
            name VARCHAR(50) NOT NULL,
            year INTEGER NOT NULL,
            status INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (school_id) REFERENCES schools(school_id)
        )
    """)
    with engine.connect() as conn:
        conn.execute(sql)
        conn.commit()


def create_teacher_class_subject_table(engine):
    """创建教师-班级-科目关联表"""
    sql = text("""
        CREATE TABLE IF NOT EXISTS teacher_class_subject (
            id VARCHAR(64) PRIMARY KEY,
            teacher_id VARCHAR(64) NOT NULL,
            class_id VARCHAR(64) NOT NULL,
            subject VARCHAR(50) NOT NULL,
            is_class_teacher BOOLEAN DEFAULT 0,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers(teacher_id),
            FOREIGN KEY (class_id) REFERENCES classes(class_id),
            UNIQUE (teacher_id, class_id, subject)
        )
    """)
    with engine.connect() as conn:
        conn.execute(sql)
        conn.commit()


def create_teacher_applications_table(engine):
    """创建教师入驻申请表"""
    sql = text("""
        CREATE TABLE IF NOT EXISTS teacher_applications (
            id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            phone VARCHAR(32) NOT NULL,
            school VARCHAR(200),
            subject VARCHAR(50),
            status VARCHAR(20) DEFAULT 'pending',
            reviewer_id VARCHAR(64),
            reviewed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    with engine.connect() as conn:
        conn.execute(sql)
        conn.commit()


def create_operation_logs_table(engine):
    """创建操作日志表"""
    sql = text("""
        CREATE TABLE IF NOT EXISTS operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id VARCHAR(64),
            action VARCHAR(50) NOT NULL,
            target_type VARCHAR(50),
            target_id VARCHAR(64),
            detail JSON,
            ip_address VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    with engine.connect() as conn:
        conn.execute(sql)
        conn.commit()


def migrate_classes_table(engine):
    """迁移classes表：添加grade_id列"""
    with engine.connect() as conn:
        # 检查grade_id列是否存在
        result = conn.execute(text("PRAGMA table_info(classes)"))
        columns = [row[1] for row in result.fetchall()]

        if 'grade_id' not in columns:
            # SQLite不支持直接ADD FOREIGN KEY，需要分步
            # 1. 添加grade_id列
            conn.execute(text("ALTER TABLE classes ADD COLUMN grade_id VARCHAR(64)"))
            conn.commit()
            print("[OK] classes.grade_id column added")
        else:
            print("[OK] classes.grade_id column exists, skip")

        # 检查grade列是否需要保留（保留作为年级名称）
        if 'grade' not in columns:
            conn.execute(text("ALTER TABLE classes ADD COLUMN grade VARCHAR(32) DEFAULT '高一'"))
            conn.commit()
            print("[OK] classes.grade column added")
        else:
            print("[OK] classes.grade column exists, skip")


def migrate_classes_teacher_id(engine):
    """删除 `classes.teacher_id` 列（阶段 6.2）。幂等。

    那一列建表时被写死 `None`、从来没有数据，却让三处代码静默失效：
    班主任恒为 null、`panel` 的「教师管理的班级」恒空、`report` 的教师总览恒空。
    **班主任的唯一真源是 `teacher_class_subject.is_class_teacher`**（6.2 上半已改过去）。

    ⚠️ **不能直接 `ALTER TABLE ... DROP COLUMN`** —— 实测报
    `error in table classes after drop column: unknown column "teacher_id" in
    foreign key definition`：该列出现在 `classes` **自己的表级外键**里
    （`FOREIGN KEY(teacher_id) REFERENCES teachers(teacher_id)`），而 SQLite
    不允许删被表级 FK 引用的列，也没有 `DROP CONSTRAINT`。（`teachers.role` 能直接删，
    是因为它没有外键 —— 别照着那个先例想当然。）

    故走 SQLite 标准的**整表重建**：建新表 → 拷数据 → 删旧表 → 改名。

    两个必须用底层 DBAPI 连接的理由（踩过两次）：
      1. `PRAGMA foreign_keys` 是**逐连接**的 —— 用另一个连接设它，对这次重建无效
      2. 它**在事务里是空操作** —— 而 SQLAlchemy 一执行语句就 autobegin，
         所以 `conn.execute(text("PRAGMA foreign_keys=off")); conn.begin()` 那样写
         既设不上、还会报 "already initialized a Transaction"
    重建期间必须关外键：`students.class_id → classes.class_id` 有 66 行子记录，
    开着外键 `DROP TABLE classes` 会被隐式 DELETE 的外键检查拦下。
    """
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        columns = [r[1] for r in cur.execute("PRAGMA table_info(classes)").fetchall()]

        if 'teacher_id' not in columns:
            print("[OK] classes.teacher_id column already absent, skip")
            return

        keep = [c for c in columns if c != 'teacher_id']
        cols = ", ".join(keep)

        cur.execute("PRAGMA foreign_keys=off")
        cur.execute("BEGIN")
        try:
            cur.execute("""
                CREATE TABLE classes_new (
                    class_id VARCHAR(64) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    student_count INTEGER,
                    grade VARCHAR(32) NOT NULL,
                    subject VARCHAR(32),
                    grade_id VARCHAR(64),
                    created_at DATETIME,
                    updated_at DATETIME,
                    PRIMARY KEY (class_id),
                    FOREIGN KEY(grade_id) REFERENCES grades (grade_id)
                )
            """)
            cur.execute(f"INSERT INTO classes_new ({cols}) SELECT {cols} FROM classes")
            cur.execute("DROP TABLE classes")
            cur.execute("ALTER TABLE classes_new RENAME TO classes")

            broken = cur.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(f"重建后外键校验失败: {broken}")
            raw.commit()
            print(f"[OK] classes.teacher_id column dropped（整表重建，保留 {len(keep)} 列）")
        except Exception:
            raw.rollback()
            raise
        cur.execute("PRAGMA foreign_keys=on")
    finally:
        raw.close()


def migrate_teachers_table(engine):
    """迁移teachers表：删除已弃用的 role 列（角色唯一真源是 accounts.role，见 ADR-0008）"""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(teachers)"))
        columns = [row[1] for row in result.fetchall()]

        if 'role' in columns:
            conn.execute(text("ALTER TABLE teachers DROP COLUMN role"))
            conn.commit()
            print("[OK] teachers.role column dropped")
        else:
            print("[OK] teachers.role column already absent, skip")



def migrate_question_type(engine):
    """练习题题型列（student-daily-loop X1）：questions.question_type，存量回填
    single_choice。列型放宽（String(256)→Text）不做 DDL——SQLite 的 VARCHAR
    是长度不敏感的亲和类型，真正截断发生在应用层（已随本批次删除）；新建库
    由 create_all 直接得到 Text。"""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(questions)"))
        cols = [row[1] for row in result.fetchall()]
        if 'question_type' not in cols:
            conn.execute(text(
                "ALTER TABLE questions ADD COLUMN question_type VARCHAR(16) "
                "NOT NULL DEFAULT 'single_choice'"
            ))
            conn.commit()
            print("[OK] questions.question_type added (default single_choice)")
        else:
            print("[OK] questions.question_type exists, skip")


def migrate_answer_attachments(engine):
    """装置图作答附件列（批 2 X2）：student_answers.attachments（JSON 路径数组）。"""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(student_answers)"))
        cols = [row[1] for row in result.fetchall()]
        if 'attachments' not in cols:
            conn.execute(text("ALTER TABLE student_answers ADD COLUMN attachments TEXT"))
            conn.commit()
            print("[OK] student_answers.attachments added")
        else:
            print("[OK] student_answers.attachments exists, skip")


def migrate_parent_engagement(engine):
    """家长端批 3：parents.summary_frequency（摘要频率）、
    parent_notifications.liked（动态点赞）。均带默认值，存量零感知。"""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(parents)"))
        cols = [row[1] for row in result.fetchall()]
        if 'summary_frequency' not in cols:
            conn.execute(text(
                "ALTER TABLE parents ADD COLUMN summary_frequency VARCHAR(16) "
                "NOT NULL DEFAULT 'weekly'"
            ))
            conn.commit()
            print("[OK] parents.summary_frequency added")
        else:
            print("[OK] parents.summary_frequency exists, skip")

        result = conn.execute(text("PRAGMA table_info(parent_notifications)"))
        cols = [row[1] for row in result.fetchall()]
        if 'liked' not in cols:
            conn.execute(text("ALTER TABLE parent_notifications ADD COLUMN liked BOOLEAN DEFAULT 0"))
            conn.commit()
            print("[OK] parent_notifications.liked added")
        else:
            print("[OK] parent_notifications.liked exists, skip")


def migrate_parent_notify_channel(engine):
    """外发通道中国化（ADR-0029 批⑦前置）：parents.notify_channel/notify_webhook。

    飞书/企业微信/钉钉群机器人 webhook（家长建群机器人即可，零额外账号）；
    均可空——不绑定=仅站内，存量零感知。"""
    with engine.connect() as conn:
        cols = [row[1] for row in
                conn.execute(text("PRAGMA table_info(parents)")).fetchall()]
        if 'notify_channel' not in cols:
            conn.execute(text(
                "ALTER TABLE parents ADD COLUMN notify_channel VARCHAR(16)"))
            conn.execute(text(
                "ALTER TABLE parents ADD COLUMN notify_webhook VARCHAR(512)"))
            conn.commit()
            print("[OK] parents.notify_channel/notify_webhook added")
        else:
            print("[OK] parents.notify_channel exists, skip")
        if 'notify_prefs' not in cols:
            conn.execute(text("ALTER TABLE parents ADD COLUMN notify_prefs TEXT"))
            conn.commit()
            print("[OK] parents.notify_prefs added")
        else:
            print("[OK] parents.notify_prefs exists, skip")


def migrate_questions_difficulty_enum(engine):
    """questions.difficulty 旧版存整数 1-5（CONTEXT.md 旧约定），新模型是
    Enum(Difficulty) 按 NAME 存——把遗留整数值归一化为枚举名，否则 ORM 读即炸。"""
    mapping = {"1": "EASY", "2": "MEDIUM", "3": "MEDIUM", "4": "HARD", "5": "COMPETITION"}
    with engine.connect() as conn:
        result = conn.execute(text("SELECT DISTINCT difficulty FROM questions"))
        legacy = [str(r[0]) for r in result.fetchall()
                  if str(r[0]) in mapping]
        for v in legacy:
            conn.execute(text("UPDATE questions SET difficulty = :nv WHERE difficulty = :ov"),
                         {"nv": mapping[v], "ov": v})
        if legacy:
            conn.commit()
            print(f"[OK] questions.difficulty normalized from legacy ints: {legacy}")
        else:
            print("[OK] questions.difficulty already enum names, skip")


def migrate_loop_remediation(engine):
    """整改批次列：student_answers.confirmed（存量 True 保持历史成绩生效）、
    students.diagnosis_requested_at（诊断去抖）、student_answers.answer_key
    （落行时用的标准答案键，供待判定行扫描重判对齐；存量留空回退题库答案）。"""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(student_answers)"))
        cols = [row[1] for row in result.fetchall()]
        if 'confirmed' not in cols:
            conn.execute(text("ALTER TABLE student_answers ADD COLUMN confirmed BOOLEAN DEFAULT 1"))
            conn.commit()
            print("[OK] student_answers.confirmed added")
        else:
            print("[OK] student_answers.confirmed exists, skip")
        conn.execute(text("UPDATE student_answers SET confirmed = 1 WHERE confirmed IS NULL"))
        conn.commit()

        if 'answer_key' not in cols:
            conn.execute(text("ALTER TABLE student_answers ADD COLUMN answer_key VARCHAR(256)"))
            conn.commit()
            print("[OK] student_answers.answer_key added")
        else:
            print("[OK] student_answers.answer_key exists, skip")

        result = conn.execute(text("PRAGMA table_info(students)"))
        cols = [row[1] for row in result.fetchall()]
        if 'diagnosis_requested_at' not in cols:
            conn.execute(text("ALTER TABLE students ADD COLUMN diagnosis_requested_at DATETIME"))
            conn.commit()
            print("[OK] students.diagnosis_requested_at added")
        else:
            print("[OK] students.diagnosis_requested_at exists, skip")


def migrate_exam_records_status(engine):
    """迁移exam_records表：加 status/时间戳列并回填（P4 考试生命周期）。

    回填规则：考试 + question_stats 含 published:true → published；其余 → draft。
    练习/作业记录状态列置 draft（生命周期语义仅考试使用，统一非空便于查询）。
    """
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(exam_records)"))
        columns = [row[1] for row in result.fetchall()]

        for col in ("status", "published_at", "started_at", "grading_at",
                    "completed_at", "cancelled_at"):
            if col not in columns:
                conn.execute(text(f"ALTER TABLE exam_records ADD COLUMN {col} VARCHAR(32)"))
                conn.commit()
                print(f"[OK] exam_records.{col} column added")
            else:
                print(f"[OK] exam_records.{col} column exists, skip")

        result = conn.execute(text(
            "SELECT record_id, question_stats, type FROM exam_records WHERE status IS NULL"
        ))
        rows = result.fetchall()
        for record_id, stats, rtype in rows:
            stats_obj = None
            if isinstance(stats, str):
                import json as _json
                try:
                    stats_obj = _json.loads(stats)
                except (ValueError, TypeError):
                    stats_obj = None
            published = isinstance(stats_obj, dict) and stats_obj.get("published")
            # Enum 列按成员 NAME 存储校验（ORM 契约）——写小写会让 ORM 读取 LookupError
            target = "PUBLISHED" if (str(rtype).lower() == "exam" and published) else "DRAFT"
            conn.execute(text(
                "UPDATE exam_records SET status = :st WHERE record_id = :rid"),
                {"st": target, "rid": record_id})
        if rows:
            conn.commit()
        print(f"[OK] exam_records.status backfilled for {len(rows)} rows")


def create_student_notifications_table(engine):
    """创建学生站内通知表（P3 notification-dispatch；create_all 之外为存量库补建）"""
    sql = text("""
        CREATE TABLE IF NOT EXISTS student_notifications (
            notification_id VARCHAR(64) PRIMARY KEY,
            student_id VARCHAR(64) NOT NULL,
            type VARCHAR(32) NOT NULL,
            title VARCHAR(256) NOT NULL,
            content TEXT,
            payload JSON,
            is_read BOOLEAN,
            read_at DATETIME,
            idempotency_key VARCHAR(128) UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    with engine.connect() as conn:
        conn.execute(sql)
        conn.commit()
        print("[OK] student_notifications table ready")


def create_assignment_batches_table(engine):
    """创建练习分发布次表（P2 assignment-service；create_all 之外为存量库补建）"""
    sql = text("""
        CREATE TABLE IF NOT EXISTS assignment_batches (
            batch_id VARCHAR(64) PRIMARY KEY,
            targets JSON NOT NULL,
            source JSON NOT NULL,
            name_hint VARCHAR(128),
            status VARCHAR(16),
            failed_targets JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    with engine.connect() as conn:
        conn.execute(sql)
        conn.commit()

        # 存量表补列（幂等可重跑）：旧库没有 status/failed_targets，批次无终态可查
        result = conn.execute(text("PRAGMA table_info(assignment_batches)"))
        columns = [row[1] for row in result.fetchall()]
        for col, ddl in (("status", "status VARCHAR(16)"),
                         ("failed_targets", "failed_targets JSON")):
            if col not in columns:
                conn.execute(text(f"ALTER TABLE assignment_batches ADD COLUMN {ddl}"))
                conn.commit()
                print(f"[OK] assignment_batches.{col} column added")
            else:
                print(f"[OK] assignment_batches.{col} column exists, skip")
        print("[OK] assignment_batches table ready")


def migrate_student_answers_source(engine):
    """迁移student_answers表：加 source / grade_reason 列并回填来源（判卷中心，ADR-0012）。

    回填规则：ocr_ 前缀 answer_id 来自答题卡批量管线 → ocr；其余按 online。
    """
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(student_answers)"))
        columns = [row[1] for row in result.fetchall()]

        if 'source' not in columns:
            conn.execute(text("ALTER TABLE student_answers ADD COLUMN source VARCHAR(32)"))
            conn.commit()
            print("[OK] student_answers.source column added")
        else:
            print("[OK] student_answers.source column exists, skip")

        if 'grade_reason' not in columns:
            conn.execute(text("ALTER TABLE student_answers ADD COLUMN grade_reason VARCHAR(256)"))
            conn.commit()
            print("[OK] student_answers.grade_reason column added")
        else:
            print("[OK] student_answers.grade_reason column exists, skip")

        result = conn.execute(text(
            "SELECT COUNT(*) FROM student_answers WHERE source IS NULL"
        ))
        null_count = result.scalar() or 0
        if null_count:
            # Enum 列按成员 NAME 存储校验（ORM 契约）——写小写 value 会让 ORM 读取 LookupError
            conn.execute(text(
                "UPDATE student_answers SET source = CASE "
                "WHEN answer_id LIKE 'ocr_%' THEN 'OCR' ELSE 'ONLINE' END "
                "WHERE source IS NULL"
            ))
            conn.commit()
            print(f"[OK] student_answers.source backfilled for {null_count} rows")
        else:
            print("[OK] student_answers.source already backfilled, skip")

        # 重跑修复：早期迁移写过小写 value，这些行 ORM 一读就崩，等值过滤也会静默漏掉
        result = conn.execute(text(
            "SELECT COUNT(*) FROM student_answers "
            "WHERE source IN ('online','ocr','manual','chat','training')"
        ))
        lower_count = result.scalar() or 0
        if lower_count:
            conn.execute(text(
                "UPDATE student_answers SET source = UPPER(source) "
                "WHERE source IN ('online','ocr','manual','chat','training')"
            ))
            conn.commit()
            print(f"[OK] student_answers.source normalized to enum NAME for {lower_count} rows")
        else:
            print("[OK] student_answers.source enum names, skip")


# 角色值中英统一（ADR-0008）：旧中文值 → 英文 snake_case
ROLE_RENAME = {
    '学科组长': 'subject_lead',
    '教务管理员': 'academic_admin',
}


def migrate_role_values(engine):
    """角色值迁移：中文 → 英文 snake_case。幂等——已是新值的库不受影响。

    只处理 accounts.role —— 它是角色唯一真源。teachers.role 已弃用并在
    migrate_teachers_table 中被删除，故不在此处迁移。
    """
    with engine.connect() as conn:
        total = 0
        for old, new in ROLE_RENAME.items():
            result = conn.execute(
                text("UPDATE accounts SET role = :new WHERE role = :old"),
                {"new": new, "old": old},
            )
            if result.rowcount:
                print(f"[OK] accounts.role: {old} -> {new} ({result.rowcount} 行)")
                total += result.rowcount
        conn.commit()
        print(f"[OK] 角色值迁移完成，共 {total} 行")


# ── 会话归属回填（阶段 4.6 批次 E）─────────────────────────────
# 存量老会话（归属表上线前的）在 checkpoint 数据里**没有任何身份线索** ——
# msgpack 里只有 messages / 路由信息，ChatRequest 的 student_id 没有落进图状态。
# 所以只能按「这个部署是谁在用」显式回填，不做任何猜测。
#
# 本部署的演示家庭是「家长 lgh(parent_demo_001) + 学生 202610201(李雨萱)」，
# 存量自助端会话只有这两个账号产生的。`t-*` 不回填 —— 员工角色本来就按前缀
# 认领老会话（见 app/middleware/conversation.py）。
LEGACY_CONVERSATION_OWNERS = {
    "p-": ("parent", "parent_demo_001"),   # lgh
    "m-": ("student", "202610201"),        # 李雨萱
}


def backfill_legacy_conversations(engine):
    """把存量老会话的归属**显式**回填给演示账号。幂等、可重跑。

    - 只处理本表里还没有归属记录的 thread（已有的不动，不会覆盖）
    - 账号不存在就整条跳过（换部署 / 清了演示数据时不会误认）
    - checkpoint 库不存在就跳过（还没跑过对话的环境）
    """
    import sqlite3

    from app.middleware.conversation import checkpoints_db_path
    from app.models.conversation_owner import ConversationOwner

    ConversationOwner.__table__.create(engine, checkfirst=True)

    db_path = checkpoints_db_path()
    if not os.path.exists(db_path):
        print("[SKIP] 会话归属回填：checkpoint 库不存在")
        return

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
    finally:
        conn.close()
    thread_ids = [r[0] for r in rows if r[0]]

    with engine.connect() as db:
        existing = {
            r[0] for r in db.execute(
                text("SELECT thread_id FROM conversation_owners")
            ).fetchall()
        }
        total = 0
        for prefix, (role, user_id) in LEGACY_CONVERSATION_OWNERS.items():
            account = db.execute(
                text("SELECT 1 FROM accounts WHERE role = :role AND "
                     "COALESCE(parent_id, student_id) = :uid AND status = 'active'"),
                {"role": role, "uid": user_id},
            ).fetchone()
            if not account:
                print(f"[SKIP] {prefix}* 回填：账号 {user_id}({role}) 不存在")
                continue
            targets = [
                t for t in thread_ids
                if t.startswith(prefix) and t not in existing
            ]
            for t in targets:
                db.execute(
                    text("INSERT INTO conversation_owners (thread_id, user_id, role, created_at) "
                         "VALUES (:t, :u, :r, :c)"),
                    {"t": t, "u": user_id, "r": role, "c": datetime.utcnow()},
                )
            db.commit()
            total += len(targets)
            print(f"[OK] {prefix}* -> {user_id}({role})：回填 {len(targets)} 条")
        print(f"[OK] 会话归属回填完成，共 {total} 条")


def migrate_kb_tag_map(engine):
    """KB2a（2026-10-27）：kp_tag_map 表——题库标签 → 知识点词典码的审定映射。

    映射内容来源 content/kb/kp_tag_map_m*.json 的 mapped 桶（老师审定），
    导入方 app/services/kb_tag_map_import（幂等 INSERT OR IGNORE）；
    错误率聚合（kb_error_rate）与出题定向读这张表。"""
    from app.models.database import KBTagMap
    KBTagMap.__table__.create(engine, checkfirst=True)
    print("[OK] kp_tag_map ready")


def migrate_kb_frontmatter(engine):
    """KB-T13（2026-10-27）：kb_frontmatter 表——知识点 → 文档 frontmatter 摘要。

    检索图增强读 equations（先修链读 knowledge_edges）。行由摄取管线
    （kb_ingest.ingest）在文档入库时 upsert；本迁移只管 schema。
    KB2b 补 misconceptions_json（易错三元），存量库走 ALTER，幂等。
    """
    from app.models.database import KBFrontmatter
    KBFrontmatter.__table__.create(engine, checkfirst=True)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(
            text("PRAGMA table_info(kb_frontmatter)")).fetchall()}
        if "misconceptions_json" not in cols:
            conn.execute(text(
                "ALTER TABLE kb_frontmatter ADD COLUMN misconceptions_json TEXT"))
            conn.commit()
            print("[OK] kb_frontmatter.misconceptions_json column added")
        else:
            print("[OK] kb_frontmatter.misconceptions_json column exists, skip")
    print("[OK] kb_frontmatter ready")


def migrate_kb_graph(engine):
    """KB-T11（2026-10-27）：knowledge_edges 补图谱三列 + 建 kb_substances 表。

    幂等。旧形状库走 ALTER；全新库 create_all 按含新列的 ORM 建表，此函数等于无操作。
    环检测（kb_graph.would_create_cycle）只读这两张表，这里只管 schema。
    """
    from app.models.database import KBSubstance
    KBSubstance.__table__.create(engine, checkfirst=True)
    tables = get_existing_tables(engine)
    if "knowledge_edges" not in tables:
        print("[OK] knowledge_edges table absent (create_all will build it), skip")
        return
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(knowledge_edges)"))
        columns = [row[1] for row in result.fetchall()]
        for col, ddl in (("edge_type", "VARCHAR(32)"),
                         ("confidence", "FLOAT"),
                         ("source_doc", "VARCHAR(256)")):
            if col not in columns:
                conn.execute(text(f"ALTER TABLE knowledge_edges ADD COLUMN {col} {ddl}"))
                conn.commit()
                print(f"[OK] knowledge_edges.{col} column added")
            else:
                print(f"[OK] knowledge_edges.{col} column exists, skip")
        # KB3（2026-10-28）：active 边防重唯一索引——历史导入查重只认 proposed，
        # 同对边多次重导后产生并行 active 行（图谱上同一条先修出现多次）。
        # 索引要求先去重；库内已有重复时先跑清理（保留最小 id）再建索引。
        dup = conn.execute(text(
            "SELECT COUNT(*) FROM (SELECT 1 FROM knowledge_edges "
            "WHERE status='active' GROUP BY from_kp, to_kp, edge_type HAVING COUNT(*) > 1)"
        )).scalar()
        if dup:
            for f, t_, et in conn.execute(text(
                "SELECT from_kp, to_kp, edge_type FROM knowledge_edges "
                "WHERE status='active' GROUP BY from_kp, to_kp, edge_type "
                "HAVING COUNT(*) > 1"
            )).fetchall():
                conn.execute(text(
                    "DELETE FROM knowledge_edges WHERE from_kp=:f AND to_kp=:t "
                    "AND edge_type=:e AND status='active' AND id > "
                    "(SELECT MIN(id) FROM knowledge_edges WHERE from_kp=:f AND to_kp=:t "
                    "AND edge_type=:e AND status='active')"),
                    {"f": f, "t": t_, "e": et})
            conn.commit()
            print(f"[OK] knowledge_edges active 重复清理 {dup} 组")
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_edges_active_pair "
            "ON knowledge_edges(from_kp, to_kp, edge_type) WHERE status='active'"))
        conn.commit()
        print("[OK] ux_edges_active_pair ready")


def migrate_knowledge_points_kb(engine):
    """KB1a（2026-10-27）：knowledge_points 补词典四列 code/theme/module/order_no。

    幂等（"column exists, skip" 模式，同 exam_records 迁移）。表不存在时跳过——
    全新库由 create_all 按含新列的 ORM 直接建表，这一支只服务存量库。
    列的写入方是 `app/services/kb_dictionary.sync_dictionary`（词典 JSON 同步），
    这里只管 schema。"""
    tables = get_existing_tables(engine)
    if "knowledge_points" not in tables:
        print("[OK] knowledge_points table absent (create_all will build it), skip")
        return
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(knowledge_points)"))
        columns = [row[1] for row in result.fetchall()]
        for col, ddl in (("code", "VARCHAR(32)"),
                         ("theme", "VARCHAR(64)"),
                         ("module", "VARCHAR(32)"),
                         ("order_no", "INTEGER")):
            if col not in columns:
                conn.execute(text(f"ALTER TABLE knowledge_points ADD COLUMN {col} {ddl}"))
                conn.commit()
                print(f"[OK] knowledge_points.{col} column added")
            else:
                print(f"[OK] knowledge_points.{col} column exists, skip")


def migrate_agent_gates_last_opened(engine):
    """确认门启动期幂等迁移（T5 + T19）：加列 → 回填 → 盖 `gate_epoch` 章。

    三件事必须同批，且盖章与 ALTER 解耦（R12①）：新装库由 `create_all` 按含新列的
    ORM 直接建表，`if 'last_opened_at' not in columns` 这一支永不触发，若盖章挂在
    加列成功分支上，新环境就永远没有章，4b 的过期作业整轮空转。所以章的判据只有一
    句：行不存在即写。

    挂载在 `app/main.py` 的 startup（`create_all` 之后、`init_database()` 之前）。
    这是全仓 DDL 走 CLI 惯例的唯一例外，其余重活仍须人工 `python -m app.utils.migrate`。
    """
    from app.models.database import AgentGateDecision, AgentGateMeta

    # 新表走 ORM，checkfirst 保证重复启动不报错（启动路径不依赖有人记得建表）
    AgentGateMeta.__table__.create(engine, checkfirst=True)
    AgentGateDecision.__table__.create(engine, checkfirst=True)

    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(agent_gates)"))
        columns = [row[1] for row in result.fetchall()]

        if 'last_opened_at' not in columns:
            conn.execute(text("ALTER TABLE agent_gates ADD COLUMN last_opened_at TIMESTAMP"))
            conn.commit()
            print("[OK] agent_gates.last_opened_at column added")
        else:
            print("[OK] agent_gates.last_opened_at column exists, skip")

        # 回填（发9）：存量门的计时起点取首次开门时刻。NOT NULL 列在加列时
        # 只能先留 NULL，回填不完成就等于把老门判成 1970 年开门。
        null_count = conn.execute(text(
            "SELECT COUNT(*) FROM agent_gates WHERE last_opened_at IS NULL"
        )).scalar() or 0
        if null_count:
            conn.execute(text(
                "UPDATE agent_gates SET last_opened_at = created_at "
                "WHERE last_opened_at IS NULL"
            ))
            conn.commit()
            print(f"[OK] agent_gates.last_opened_at backfilled for {null_count} rows")
        else:
            print("[OK] agent_gates.last_opened_at already backfilled, skip")

        # 复合索引：过期作业与待确认列表都是 status 过滤 + 等待时长排序（随 T5 建）
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_gates_status_last_opened "
            "ON agent_gates (status, last_opened_at)"
        ))
        conn.commit()

        # 章（R5/R12①）：已有章不改写——重启不改部署时刻，否则存量老门会被重新宽限一轮
        has_epoch = conn.execute(text(
            "SELECT 1 FROM agent_gate_meta WHERE key = 'gate_epoch'"
        )).fetchone()
        if has_epoch:
            print("[OK] gate_epoch already stamped, skip")
        else:
            # 首次盖章 = 本批门改动的唯一一次性窗口。布尔门的存量 open 行在这里收敛
            # （T18）：旧编号只哈希 tool+kwargs，两个会话能共用同一行、归属已不可信，
            # 下次工具触发时自然重开成正确的门。挂在「首次」而不是每次启动——
            # 无条件跑会让服务每重启一次就清掉老师正在等的所有布尔门卡。
            stale = conn.execute(text(
                "UPDATE agent_gates SET status = 'superseded', decided_at = :u "
                "WHERE kind = 'boolean' AND status = 'open'"
            ), {"u": datetime.utcnow()}).rowcount
            conn.commit()
            if stale:
                print(f"[OK] superseded {stale} legacy boolean gates (re-open on next call)")
            conn.execute(text(
                "INSERT INTO agent_gate_meta (key, value, updated_at) "
                "VALUES ('gate_epoch', :v, :u)"
            ), {"v": datetime.utcnow().isoformat(timespec="seconds"),
                "u": datetime.utcnow()})
            conn.commit()
            print("[OK] gate_epoch stamped")


def migrate_agent_gates_plan_step(engine):
    """确认门加「绑定的计划步号」（gate-lifecycle 批次 1）：加列，**不回填**。幂等。

    与 `last_opened_at` 不同，本列刻意不回填 —— NULL 是**有效语义**：「这扇门没有
    记录过步归属」，读路径据此回退旧行为（按工具名找步）。回填反而会造假：
    `plan_runs.steps[].gate_draft_id` 只在部分通路被写入，拿它反推就是拿半份数据
    当全部，而**推错的绑定比没有绑定更难查**。

    必须挂 startup（与 `last_opened_at` 同理由）：ORM 已映射本列，老库缺列时
    第一个 `agent_gates` 查询就报 no such column，不是等到有人开门才 500。
    """
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(agent_gates)"))
        columns = [row[1] for row in result.fetchall()]

        if 'plan_step' not in columns:
            conn.execute(text("ALTER TABLE agent_gates ADD COLUMN plan_step INTEGER"))
            conn.commit()
            print("[OK] agent_gates.plan_step column added")
        else:
            print("[OK] agent_gates.plan_step column exists, skip")


def migrate_agent_gates_replay_payload(engine):
    """确认门加「重放载荷指纹」两列（gate-lifecycle 2.6）：加列，**不回填**。幂等。

    与 `plan_step` 同一套取舍：NULL 是**有效语义**（「这扇门没有记录过载荷指纹」），
    读路径据此回退旧行为放行。回填在这里尤其不能做 —— 存量门当初开门时没有这个
    概念，事后按现值算一个指纹塞进去，看着像"记录"，其实是拿现在的样子冒充当时的
    样子：一旦那扇门在开门之后被人动过，回填的指纹会把它**洗白成一直如此**，
    而这正是这一列要挡的东西。

    必须挂 startup（同 `plan_step`）：ORM 已映射这两列，老库缺列时第一个
    `agent_gates` 查询就报 no such column，不是等到有人开门才 500。
    """
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(agent_gates)"))
        columns = [row[1] for row in result.fetchall()]

        if 'args_hash' not in columns:
            conn.execute(text("ALTER TABLE agent_gates ADD COLUMN args_hash VARCHAR(64)"))
            conn.commit()
            print("[OK] agent_gates.args_hash column added")
        else:
            print("[OK] agent_gates.args_hash column exists, skip")

        if 'schema_version' not in columns:
            conn.execute(text("ALTER TABLE agent_gates ADD COLUMN schema_version INTEGER"))
            conn.commit()
            print("[OK] agent_gates.schema_version column added")
        else:
            print("[OK] agent_gates.schema_version column exists, skip")


if __name__ == "__main__":
    migrate()
