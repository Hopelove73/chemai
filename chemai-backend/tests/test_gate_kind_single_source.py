"""门分类的单一真源：DB AgentGate.kind，工具名集合只在无门时兜底（决策 A 收尾）。

grilling 决策 A 说「门按 DB kind 分类」，但只在 /decision（`_resolve_resume_intent`）
落了地；`/stream` 打字语义（`langgraph_channel.py:434`）、`_gate_draft_id` 的活跃
草稿兜底（`:1249`）、`/history` 的内存补捞（`conversation.py:288`）仍拿
`pending["name"] in _GATE_PLAN_TOOLS` 猜。两套分类法今天结果恰好相同（登记草稿门
用的两个名字正好都在集合里），所以没有现行 bug；错分是**结构性潜伏**：新增一个
草稿门工具而忘了同步这个 4 元素集合，老师打一行字就会被当成布尔门处理，
`pending_approval` 被清空 → C1-Q3「有未决门时全拦工具」失效 → 门没批后续步骤照跑。

本测试钉死：只要 DB 有开放门，kind 就是权威；无门才回退工具名集合。
"""
import pytest

from app.services.gate_service import gate_service


@pytest.fixture
def env(patch_get_db):
    """本测试的内存 DB（`get_db` 已指向它）——补丁见 tests/conftest.py。"""
    return patch_get_db


def _pending(name: str, **kwargs) -> dict:
    """待批调用（**参数**，不是某个状态对象上的属性）。

    `_gate_is_plan_gate(cid, pending)` 收的就是这样一份 dict —— 2.4 收尾把内存
    字段删掉之后，调用方从门实体拼出它（`_pending_from_entity`）；这里直接造一份，
    测的是分类函数本身。
    """
    return {"name": name, "fn": None, "kwargs": kwargs}


def test_db_draft_kind_wins_over_unknown_tool_name(env, unit):
    """DB 说这是草稿门，工具名却不在集合里 → 必须仍判为草稿门（集合不再是真源）。"""
    from app.services.decision_intake import is_plan_gate as _gate_is_plan_gate
    with unit(env):                             # 写入口须落在工作单元里（前置一）
        gate_service.open_gate(env, "conv-kind-1", "draft", "send_brand_new_draft",
                               {"student_id": "s1"})
    pending = _pending("send_brand_new_draft", student_id="s1")

    assert _gate_is_plan_gate("conv-kind-1", pending) is True


def test_db_boolean_kind_wins_over_name_set(env, unit):
    """DB 说这是布尔门，工具名在草稿集合里 → 必须判为布尔门。

    错分的代价不对称：把布尔门当草稿门会走「打回+反馈」分支去修订一张不存在的
    草稿；把草稿门当布尔门会清空待批。两边都必须以 DB 为准。"""
    from app.services.decision_intake import is_plan_gate as _gate_is_plan_gate
    with unit(env):
        gate_service.open_gate(env, "conv-kind-2", "boolean", "send_learning_plan",
                               {"student_id": "s1"})

    assert _gate_is_plan_gate("conv-kind-2",
                              _pending("send_learning_plan")) is False


def test_falls_back_to_tool_name_set_when_no_open_gate(env):
    """DB 无开放门（登记尚未落库的窗口）→ 回退工具名集合，保持既有行为。"""
    from app.services.decision_intake import (
        GATE_PLAN_TOOLS as _GATE_PLAN_TOOLS, is_plan_gate as _gate_is_plan_gate,
    )
    known = sorted(_GATE_PLAN_TOOLS)[0]

    assert _gate_is_plan_gate("conv-nope", _pending(known)) is True
    assert _gate_is_plan_gate("conv-nope",
                              _pending("bash")) is False


def test_gate_draft_id_does_not_resolve_draft_for_boolean_gate(env, unit):
    """`_gate_draft_id` 的活跃草稿兜底同样以 DB kind 为准：布尔门不得抓草稿。

    旧实现只看工具名，门实体明明登记为 boolean 时仍会把该生活跃草稿的 id 塞进
    决定里，后续按草稿门流程处理一张布尔门 → 状态错位。
    （正向兜底——无门时解析活跃草稿——由
    test_gate_decision_loop.py::test_gate_draft_id_resolves_active_draft_after_unpack_bug 守住）"""
    from agent.channel.langgraph_channel import _gate_draft_id

    with unit(env):
        gate_service.open_gate(env, "conv-kind-3", "boolean", "send_learning_plan",
                               {"student_id": "s1"})
    guard = _pending("send_learning_plan", student_id="s1", draft_id="")

    assert _gate_draft_id(guard, {"draft_id": ""}, "conv-kind-3") == ""
