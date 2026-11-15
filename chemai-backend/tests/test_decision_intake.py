"""Decision Intake interface 测试（架构候选 A · S1）。

seam：app/services/decision_intake.py 的公开函数面。S1 的口径是**钉住现有行为**
（三个分类器从 channel 收拢过来，语义零变化），S2 才翻转布尔门 abandon 的丢弃。

expected 值全部来自收拢前的行为（langgraph_channel 的既有断言与实现），
不是从新实现反推 —— 这批用例在 S2 翻转语义时按 grilling 共识逐条改红。
"""

from app.services import decision_intake as di


class R:  # 最小请求桩（与 ChatRequest 同形字段）
    def __init__(self, message="", decision=None, conversation_id=""):
        self.message = message
        self.decision = decision
        self.conversation_id = conversation_id


# ── normalize_decision：原 _normalize_decision ──────────────────────

def test_normalize_structured_decision():
    d = di.normalize_decision(R(decision={
        "action": "Approve", "feedback": "改称呼", "draft_id": "d1", "gate_id": "g1"}))
    assert d == {"action": "approve", "feedback": "改称呼",
                 "draft_id": "d1", "gate_id": "g1"}


def test_normalize_legacy_strings_only_two_values():
    assert di.normalize_decision(R(message="approved"))["action"] == "approve"
    assert di.normalize_decision(R(message="cancelled"))["action"] == "abandon"
    assert di.normalize_decision(R(message="同意发送")) is None


def test_normalize_unknown_action_is_not_a_decision():
    # C1(a) fail-closed：decision 带非法 action 时，绝不回退用 message 当批准
    assert di.normalize_decision(R(message="approved",
                                   decision={"action": "modify"})) is None


# ── resume_intent：原 _resolve_resume_intent（无门时走工具名回退）───

def test_resume_intent_draft_gate_keeps_three_way(monkeypatch):
    monkeypatch.setattr(di, "_open_gate_kind", lambda cid: "draft")
    d, resp = di.resume_intent(
        R(message="", conversation_id="c1",
          decision={"action": "reject", "feedback": "太长", "gate_id": "g1"}),
        pending_name="send_learning_plan", plan_tools=frozenset({"send_learning_plan"}))
    assert d["action"] == "reject" and resp == "cancelled"


def test_resume_intent_boolean_approve_keeps_intent(monkeypatch):
    # 2026-09-14 断链修复的行为：布尔门批准不得被改写成 cancelled
    monkeypatch.setattr(di, "_open_gate_kind", lambda cid: "boolean")
    d, resp = di.resume_intent(
        R(message="", conversation_id="c1",
          decision={"action": "approve", "gate_id": "g-tool-x"}),
        pending_name="assign_adaptive_practice", plan_tools=frozenset())
    assert d is None and resp == "approved"


def test_resume_intent_boolean_non_approve_kept_for_decision_stream(monkeypatch):
    # S2（grilling Q5）翻转的钉子：布尔门 abandon/reject 不再被丢成 legacy ——
    # 旧行为（置 None 只留 "cancelled"）导致 /resume 落到 Command(resume) 空操作，
    # 门不关、账不记、计划步停在等待确认（审查 C1，唯一 CRITICAL）。
    monkeypatch.setattr(di, "_open_gate_kind", lambda cid: "boolean")
    d, resp = di.resume_intent(
        R(message="", conversation_id="c1",
          decision={"action": "abandon", "gate_id": "g-tool-x"}),
        pending_name="assign_adaptive_practice", plan_tools=frozenset())
    assert d and d["action"] == "abandon" and resp == "cancelled"
    d2, _ = di.resume_intent(
        R(message="", conversation_id="c1",
          decision={"action": "reject", "feedback": "题量太多", "gate_id": "g-tool-x"}),
        pending_name="assign_adaptive_practice", plan_tools=frozenset())
    assert d2 and d2["action"] == "reject"


def test_resume_intent_boolean_approve_still_legacy_replay(monkeypatch):
    # 批准保持 S1 形态：布尔门 approve 归 legacy（"approved"），由 /resume 的
    # _direct_replay_events 占门重放 —— 那条路才是它的执行面与记账面。
    monkeypatch.setattr(di, "_open_gate_kind", lambda cid: "boolean")
    d, resp = di.resume_intent(
        R(message="", conversation_id="c1",
          decision={"action": "approve", "gate_id": "g-tool-x"}),
        pending_name="assign_adaptive_practice", plan_tools=frozenset())
    assert d is None and resp == "approved"


def test_resume_intent_fallback_plan_tools_when_no_gate(monkeypatch):
    monkeypatch.setattr(di, "_open_gate_kind", lambda cid: "")
    d, resp = di.resume_intent(
        R(message="", decision={"action": "abandon"}),
        pending_name="send_report_to_parent",
        plan_tools=frozenset({"send_report_to_parent"}))
    # 回退判成草稿门 → 三值决定保留（abandon 不被丢弃）
    assert d is not None and d["action"] == "abandon" and resp == "cancelled"


def test_resume_intent_malformed_no_ghost_approval(monkeypatch):
    monkeypatch.setattr(di, "_open_gate_kind", lambda cid: "")
    d, resp = di.resume_intent(
        R(message="approved", decision={"action": "modify"}),
        pending_name="", plan_tools=frozenset())
    assert d is None and resp == ""


# ── classify_typed：原 _classify_typed_text 的分类半段 ─────────────

def test_classify_typed_no_gate_is_none():
    assert di.classify_typed("c1", "随便说", None, False) == ("none", "")


def test_classify_typed_confirmation_word_guides_to_card():
    kind, text = di.classify_typed("c1", "同意", {"name": "x"}, True)
    assert kind == "guidance"
    assert "确认卡" in text and "不会执行任何操作" in text


def test_classify_typed_negation_still_guidance_not_approval():
    # 「不要发送」含「发送」被词表命中 —— 但打字永不构成批准，只引导点卡
    kind, _ = di.classify_typed("c1", "不要发送", {"name": "x"}, False)
    assert kind == "guidance"


def test_classify_typed_draft_gate_other_text_is_reject():
    assert di.classify_typed("c1", "把称呼改一下", {"name": "send_learning_plan"},
                             True)[0] == "reject"


def test_classify_typed_boolean_gate_other_text_is_void():
    assert di.classify_typed("c1", "先别管这个", {"name": "assign_adaptive_practice"},
                             False)[0] == "void"


# ── interrupt_action：pilot 恢复的 _act 归一化 ──────────────────────

def test_interrupt_action_prefers_structured_decision():
    assert di.interrupt_action({"action": "reject"}, "approved") == "reject"


def test_interrupt_action_falls_back_to_protocol_string():
    assert di.interrupt_action(None, "approved") == "approve"
    assert di.interrupt_action(None, "cancelled") == "abandon"


# ── is_confirmation：自 guard 收拢（词表不变） ──────────────────────

def test_is_confirmation_wordlist():
    assert di.is_confirmation("同意") and di.is_confirmation("发吧")
    assert not di.is_confirmation("我想再改一版报告，别急着发")


def test_guard_reexport_preserved():
    # agent.guard 的再导出不能断（test_send_confirmation 等直接从那里导入）
    from agent.guard import is_confirmation as g
    assert g is di.is_confirmation
