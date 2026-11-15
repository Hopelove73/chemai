"""gate_open 事件载荷的单一构造点（ADR-0015 阶段 4 收尾，eng 审查 D6）。

同一张确认卡的载荷此前在四处手写（`langgraph_channel.py:884 / :1410 / :1497`
与 `langgraph_sse_v2.py:237`），已分叉出两处不一致：
  · `:1498` 用字符串字面量重述 `gate_service.draft_gate_id` 的格式；改格式时卡片
    编号与实体编号不再相等 → `check_binding` 判定错门，老师点批准被拒、修订死锁。
  · `:1501` 的 recipient 只映射 parent|student，缺其余三处的 system 分支。
  · 可选字段形状不一：三处恒发 draft_id/version（空串/0），adapter 按有无塞。

本测试钉死：载荷只能由 `gate_service.gate_open_event` 产出，各处输出逐字段相同。
"""
import json
import pathlib
import re

import pytest

from app.services.gate_service import (
    draft_gate_id, gate_open_event, gate_recipient, gate_service,
)


@pytest.fixture
def env(patch_get_db):
    """本测试的内存 DB（`get_db` 已指向它）——补丁见 tests/conftest.py。"""
    return patch_get_db


def test_recipient_mapping_has_the_system_branch():
    """三类收件人：家长 / 学生 / 系统（布尔门）。缺 system 会把非发送类动作
    渲染成「发送给学生」按钮（前端 ApprovalCard 按 recipient 出文案）。"""
    assert gate_open_event("g1", "send_parent", "m")["recipient"] == "parent"
    assert gate_open_event("g1", "send_student", "m")["recipient"] == "student"
    assert gate_open_event("g1", "", "m")["recipient"] == "system"
    assert gate_open_event("g1", "未来新增动作", "m")["recipient"] == "system"


def test_empty_optional_fields_are_omitted():
    """布尔门没有草稿工件：draft_id 空、version None 一律不上线。前端这两个
    字段是可选的，四处必须同形状，否则同一张卡在不同路径下字段集不同。"""
    boolean = gate_open_event("g-tool-x", "", "请确认")
    assert "draft_id" not in boolean and "version" not in boolean

    draft = gate_open_event(draft_gate_id("d9"), "send_student", "请确认",
                            draft_id="d9", version=2)
    assert draft["draft_id"] == "d9" and draft["version"] == 2


def test_message_falls_back_to_the_neutral_prompt():
    assert gate_open_event("g1", "send_student", "")["message"] == "请确认操作"


def test_reissued_card_matches_the_single_builder(env, unit):
    """重发当前门（`_replay_binding_mismatch`）的载荷必须与构造器逐字段相同。"""
    from agent.channel.langgraph_channel import _replay_binding_mismatch

    with unit(env):            # 写入口须落在工作单元里（前置一）
        gid = gate_service.open_gate(env, "conv-om-1", "boolean",
                                     "assign_adaptive_practice", {"student_id": "s1"})
    events = _replay_binding_mismatch("conv-om-1", "g-some-other-gate")
    assert events, "错门决定必须被拒并重发当前门"
    card = json.loads(events[1])
    og = gate_service.get_open_gate(env, "conv-om-1")
    assert card == gate_open_event(og["gate_id"], og["action"], og["message"],
                                   draft_id=og["draft_id"], version=og["version"],
                                   tool_name=og["tool_name"])
    assert card["gate_id"] == gid


def test_gate_open_wire_contract_lives_in_one_file():
    """结构护栏：`gate_open` 字面量与 `g-draft-` 编号格式只允许出现在
    gate_service.py。多一处手写，首次发卡/重开门/重启重建就会各自漂走。"""
    backend = pathlib.Path(__file__).resolve().parents[1]
    allowed = "app/services/gate_service.py"
    patterns = {
        "gate_open 字面量": re.compile(r"""["']type["']\s*:\s*["']gate_open["']"""),
        "草稿门编号格式": re.compile(r"""f?["']g-draft-"""),
    }
    offenders = []
    for base in ("agent", "app"):
        for path in (backend / base).rglob("*.py"):
            rel = path.relative_to(backend).as_posix()
            if rel == allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for label, rx in patterns.items():
                for m in rx.finditer(text):
                    line = text[:m.start()].count("\n") + 1
                    offenders.append(f"{rel}:{line} — {label}")
    assert not offenders, "gate_open 载荷契约被手写复制：" + "; ".join(offenders)


def test_builder_keys_match_the_frontend_event_type():
    """跨端契约守卫：构造器发出的键与前端 `GateOpenEvent` 声明双向对上。

    后端测试拿后端自己的断言，前端测试拿前端手抄的 JSON 字面量——两边各自全绿，
    wire 漂了只有生产环境红（前端读不到 recipient 就出错文案的确认卡）。所以这里
    直接读前端类型声明做对照，两个方向都查：
    ① 构造器多发的键 → 前端 interface 里没有 → 前端静默丢弃；
    ② 前端必填（无 `?`）的键 → 构造器最简载荷必须真的发出来。"""
    ts = (pathlib.Path(__file__).resolve().parents[1]
          / "frontend" / "src" / "lib" / "agent-sse.ts").read_text(encoding="utf-8")
    block = re.search(r"export interface GateOpenEvent\s*\{(.*?)\n\}", ts, re.S)
    assert block, ("前端 agent-sse.ts 里的 GateOpenEvent 被改名或删除"
                   "——wire 两端失去对照物，请同步本守卫")
    body = block.group(1)
    declared = set(re.findall(r"^\s*([a-z_]+)\??:", body, re.M))
    required = set(re.findall(r"^\s*([a-z_]+):", body, re.M))

    full = set(gate_open_event("g1", "send_parent", "m", draft_id="d1", version=1))
    minimal = set(gate_open_event("g1", "", ""))
    assert full <= declared, f"构造器发出前端未声明的键（前端会静默丢弃）：{sorted(full - declared)}"
    assert required <= minimal, f"前端必填而最简门卡不发的键：{sorted(required - minimal)}"

    # recipient 是三值枚举：构造器的兜底值与前端 union 必须一致
    union = re.search(r"recipient\??\s*:\s*([^;\n]+)", body)
    assert union, "前端 recipient 字段类型丢失"
    values = set(re.findall(r"'([a-z_]+)'", union.group(1)))
    assert values == {"student", "parent", "system"}, f"前端 recipient 枚举已变：{sorted(values)}"
    assert {gate_recipient("send_student"), gate_recipient("send_parent"),
            gate_recipient("")} == values
