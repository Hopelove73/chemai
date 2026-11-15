"""A3 —— 教学技能注册表与入口工具（ADR-0027，capability-upgrade-spec 批次 A）。

三组口径：
1. 注册表契约：四要素齐全、artifact_kind 必须在后端 ALLOWED_KINDS 里、slash 唯一
   （前端键集合对照已有 test_artifacts_api::test_contract_kind_sets_match_frontend）。
2. 入口行为：未知技能报可用清单；review_deck 缺数据当场拒绝（学科论断型产物的
   fail-closed）；LLM 被 mock 成固定 HTML 后走完整生成流（工件落盘 + 返回引用）。
3. 版本续接：同 owner+kind+主题再生成 = 追加版本不新建（R6 快照迭代）。
"""
import asyncio
import json

import pytest

from app.services import artifact_service
from app.services.teaching_skills import REGISTRY, get_skill, skill_by_slash, list_skills
from agent.tools.teaching import invoke_teaching_skill


# ── 注册表契约 ──────────────────────────────────────────────────────────

def test_skills_have_all_four_elements():
    for s in list_skills():
        assert s.prompt_template.strip(), f"{s.name} 缺 prompt 模板"
        assert s.artifact_kind in artifact_service.ALLOWED_KINDS, \
            f"{s.name} 的产物类型 {s.artifact_kind} 不在渲染契约里"
        assert isinstance(s.params, dict) and s.params, f"{s.name} 缺参数 schema"
        assert isinstance(s.checks, tuple), f"{s.name} 缺 checks 声明"
        assert s.author == "builtin"


def test_slash_commands_unique_and_resolve():
    slashes = [s.slash for s in list_skills()]
    assert len(slashes) == len(set(slashes)), "斜杠指令撞名"
    assert skill_by_slash("/动画").name == "teaching_animation"
    assert skill_by_slash("/讲评").name == "review_deck"
    assert skill_by_slash("/不存在的") is None


def test_registry_covers_batch_a_scope():
    """批次 A 首发两技能 + 批 2 S5b 表述特训（学生侧训练，无强制校验）。"""
    assert set(REGISTRY) == {"teaching_animation", "review_deck", "expression_training"}
    assert REGISTRY["teaching_animation"].checks == ()
    assert any("review" in c for c in REGISTRY["review_deck"].checks)
    exp = REGISTRY["expression_training"]
    assert exp.required_params == ("scene", "student_sentences")  # 无学生作答 fail-closed


# ── 入口行为 ────────────────────────────────────────────────────────────

def test_unknown_skill_lists_available():
    out = json.loads(asyncio.run(invoke_teaching_skill(skill="ppt_maker")))
    assert "error" in out and "teaching_animation" in out["available"]
    assert "教学动画" in out.get("hint", ""), "hint 应带中文名，模型择名不再靠猜"


def test_tool_description_enumerates_registry_names():
    """工具描述（= docstring）动态枚举注册表技能名（2026-10-05 教师端首调
    传 "animation" 失败实录）：新注册技能即出现在描述里，模型无需猜名。"""
    doc = invoke_teaching_skill.__doc__ or ""
    for s in list_skills():
        assert s.name in doc, f"{s.name} 未出现在工具描述里"
    assert "精确名" in doc


def test_get_skill_tolerates_model_shorthand():
    """别名容错：模型缩写/大小写/中文名都能落到唯一技能（首调即中）。"""
    assert get_skill("animation").name == "teaching_animation"
    assert get_skill("TEACHING_ANIMATION").name == "teaching_animation"
    assert get_skill("讲评稿").name == "review_deck"
    assert get_skill("deck").name == "review_deck"
    # 歧义与过短不猜：命中多个或 <4 字符仍走「未知技能」
    assert get_skill("e") is None
    assert get_skill("完全不存在") is None
    assert get_skill("") is None


def test_review_deck_without_data_rejected():
    # 缺 context_data 现在会按身份自动取批改数据，这条用例钉的是「取不到 → 仍旧拒绝」，
    # 所以先把身份清空：否则别的用例留下的身份会让它去影子库取到真实数据（顺序相关）。
    from agent.tools import context as ctx
    ctx.set_current_identity("", "")
    out = json.loads(asyncio.run(invoke_teaching_skill(skill="review_deck", topic="期中讲评")))
    assert "批改数据" in out["error"]


def test_required_params_declared_not_hardcoded():
    """缺参拦截按技能声明走（ADR-0027：注册即扩展）——review_deck 声明 context_data
    必填，teaching_animation 不声明；下一个吃数据的技能注册即可，入口不动。"""
    assert REGISTRY["review_deck"].required_params == ("context_data",)
    assert REGISTRY["teaching_animation"].required_params == ()


def test_no_identity_fails_closed(monkeypatch, tmp_path):
    """无身份写路径 fail-closed（ADR-0009 惯例）：不落盘、不产生幽灵 owner。"""
    from agent.tools import context as ctx
    monkeypatch.setenv("CHEMAI_WORKSPACE", str(tmp_path / "ws"))
    ctx.set_current_identity("", "")

    out = json.loads(asyncio.run(invoke_teaching_skill(
        skill="teaching_animation", topic="匿名生成")))
    assert "用户身份" in out["error"]


def test_review_deck_positive_path_saves_doc(fake_provider):
    """讲评稿正向通路：数据透传进 user message、产物 kind=doc、续版本走 skill 隔离。"""
    out1 = json.loads(asyncio.run(invoke_teaching_skill(
        skill="review_deck", topic="期中讲评",
        context_data=json.dumps({"batch_id": "batch-42", "accuracy": 0.62}, ensure_ascii=False))))
    assert "error" not in out1
    assert out1["artifact"]["kind"] == "doc"
    out2 = json.loads(asyncio.run(invoke_teaching_skill(
        skill="review_deck", topic="期中讲评",
        context_data=json.dumps({"batch_id": "batch-42"}, ensure_ascii=False))))
    assert out2["artifact"]["artifact_id"] == out1["artifact"]["artifact_id"]
    assert out2["artifact"]["version_seq"] == 2


class _FakeResult:
    def __init__(self, content):
        self.content = content
        self.tool_calls = []
        self.usage = {}
        self.model = "fake"


@pytest.fixture(autouse=True)
def _db_tables():
    """工具走 get_db() 默认引擎（影子库是生产快照，没有新表）——补建工件两表。
    影子库本身就是 tmp 副本（ADR-0026），建了也不脏仓库。顺带清掉既有工件行
    （含版本行）：影子库是会话级共享的，别的测试留下的同主题工件会把版本号顶上去。"""
    from app.models.database import Artifact, ArtifactVersion, Base, engine
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session
    session = Session(bind=engine)
    try:
        session.query(ArtifactVersion).delete()
        session.query(Artifact).delete()
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture
def fake_provider(monkeypatch, tmp_path):
    """mock 掉 DeepSeekProvider（不碰网络）；工作区指 tmp；身份定为 t-1。"""
    from agent.tools import context as ctx

    monkeypatch.setenv("CHEMAI_WORKSPACE", str(tmp_path / "ws"))
    ctx.set_current_identity("t-1", "teacher")

    async def fake_chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        # system 提示词必须真是技能模板（而不是空串混过去）
        assert "自包含" in messages[0]["content"]
        assert messages[1]["content"].startswith("教学主题：")
        if "批改数据 JSON" in messages[1]["content"]:
            assert "batch-42" in messages[1]["content"]  # context_data 必须透传
        return _FakeResult("<!DOCTYPE html><html><head><title>t</title></head>"
                           "<body><h1>盐类水解动画</h1></body></html>")

    monkeypatch.setattr("agent.provider.deepseek.DeepSeekProvider.chat", fake_chat)
    yield
    ctx.set_current_identity("", "")   # contextvar 跨测试存活，teardown 必须清掉自己设的身份


def test_generation_flow_saves_artifact_and_returns_ref(fake_provider, tmp_path):
    out = json.loads(asyncio.run(invoke_teaching_skill(
        skill="teaching_animation", topic="盐类水解")))

    assert "error" not in out
    art = out["artifact"]
    assert art["kind"] == "animation" and art["version_seq"] == 1
    assert art["url"].startswith("/api/artifacts/") and "/v/1" in art["url"]
    # 工件真落盘了（工作区在 tmp）
    resolved = _resolve_for_test(art["artifact_id"], 1)
    assert resolved is not None and "盐类水解动画" in resolved.read_text(encoding="utf-8")
    # text 给老师的回话带版本号与修改引导
    assert "v1" in out["text"]


def test_same_topic_regenerates_as_new_version(fake_provider):
    v1 = json.loads(asyncio.run(invoke_teaching_skill(
        skill="teaching_animation", topic="盐类水解")))
    v2 = json.loads(asyncio.run(invoke_teaching_skill(
        skill="teaching_animation", topic="盐类水解", requirements="加个温度滑块")))

    assert v2["artifact"]["artifact_id"] == v1["artifact"]["artifact_id"]
    assert v2["artifact"]["version_seq"] == 2
    assert "v2" in v2["text"]


def test_different_topic_is_new_artifact(fake_provider):
    a = json.loads(asyncio.run(invoke_teaching_skill(
        skill="teaching_animation", topic="盐类水解")))
    b = json.loads(asyncio.run(invoke_teaching_skill(
        skill="teaching_animation", topic="原电池")))
    assert b["artifact"]["artifact_id"] != a["artifact"]["artifact_id"]


def test_fenced_html_is_stripped(fake_provider, monkeypatch):
    async def fenced_chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        return _FakeResult("```html\n<!DOCTYPE html><html><body>围栏版</body></html>\n```")

    monkeypatch.setattr("agent.provider.deepseek.DeepSeekProvider.chat", fenced_chat)
    out = json.loads(asyncio.run(invoke_teaching_skill(
        skill="teaching_animation", topic="围栏测试")))
    resolved = _resolve_for_test(out["artifact"]["artifact_id"], 1)
    assert resolved.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_empty_llm_output_rejected(fake_provider, monkeypatch):
    async def empty_chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        return _FakeResult("抱歉，我无法……")

    monkeypatch.setattr("agent.provider.deepseek.DeepSeekProvider.chat", empty_chat)
    out = json.loads(asyncio.run(invoke_teaching_skill(
        skill="teaching_animation", topic="空输出")))
    assert "error" in out and "HTML" in out["error"]


# ── 工具注册面 ──────────────────────────────────────────────────────────

def test_tool_registered_for_teacher_and_student_persona():
    """批 2 S5b：学生 persona 也拿到技能入口（表述特训）；教师照旧。"""
    from agent.tools import TOOL_META, invoke_teaching_skill
    assert TOOL_META[invoke_teaching_skill]["personas"] == ["teacher", "student"]


def _resolve_for_test(artifact_id, seq):
    """测试内直接按盘上路径核对（归属 t-1）。"""
    from app.models.database import Artifact, ArtifactVersion, get_db
    db = next(get_db())
    try:
        row = (db.query(ArtifactVersion, Artifact)
               .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
               .filter(ArtifactVersion.artifact_id == artifact_id,
                       ArtifactVersion.seq == seq,
                       Artifact.owner_teacher_id == "t-1").first())
        if row is None:
            return None
        path = row[0].file_path
        from pathlib import Path
        return Path(path) if Path(path).is_file() else None
    finally:
        db.close()


def test_tool_count_matches_meta():
    """CI 冒烟（REQ-PAT-004 动态化）：数量断言改为**内部一致性**——TOOLS 与
    TOOL_META 完全对齐且非空。具体个数由 ci.yml 的动态 python 断言兜底，
    persona 扩容不再需要同步改测试。"""
    from agent.tools import TOOLS, TOOL_META
    assert len(TOOLS) == len(TOOL_META)
    assert len(TOOLS) >= 48  # 批 2 末基线，只增不减


def test_get_skill_exact():
    assert get_skill("teaching_animation") is not None
    assert get_skill("nope") is None


def test_long_topic_revision_keeps_same_artifact(db_session, tmp_path, monkeypatch, request):
    """批次 C 审查边界回归：topic >255 字符，同主题再次生成必须续版本
    而非被 title 截断边界切成新工件。"""
    from agent.tools import context as ctx

    monkeypatch.setenv("CHEMAI_WORKSPACE", str(tmp_path / "ws"))
    ctx.set_current_identity("t-1", "teacher")
    request.addfinalizer(lambda: ctx.set_current_identity("", ""))

    async def fake_chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        class R:
            content = "<!DOCTYPE html><html><body>x</body></html>"
            tool_calls = []
            usage = {}
            model = "fake"
        return R()

    monkeypatch.setattr("agent.provider.deepseek.DeepSeekProvider.chat", fake_chat)

    long_topic = "超长主题" * 70  # 280 字符 > 255
    v1 = json.loads(asyncio.run(invoke_teaching_skill(
        skill="teaching_animation", topic=long_topic)))
    v2 = json.loads(asyncio.run(invoke_teaching_skill(
        skill="teaching_animation", topic=long_topic)))
    assert v2["artifact"]["artifact_id"] == v1["artifact"]["artifact_id"]
    assert v2["artifact"]["version_seq"] == 2