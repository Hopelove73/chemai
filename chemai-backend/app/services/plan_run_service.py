"""计划运行 service —— todolist（Plan Run）状态流转的唯一家园（ADR-0013）。

interface 即测试面：
    start_run(conversation_id, goal, steps)   新 run 作废该会话旧活跃 run
    mark_step(conversation_id, step, status)  步状态推进（无活跃 run 时静默 no-op）
    get_active_run(conversation_id)           刷新页面/重启后的重同步查询
    record_step_artifact / get_step_artifacts  步骤产物（C2：跨步引用不靠对话历史）
    resolve_tool_args(db, cid, tool, kwargs)  执行前把 ${step_N.field} 按库内产物解析

channel / SSE 适配器按 conversation_id 调用，无需携带 run_id。
步状态机：pending / running / awaiting_confirmation / revising /
completed / failed / cancelled。全部步骤到终态 → run 置 completed。
"""
import json
import logging
import uuid
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_STEP_TERMINAL = {"completed", "failed", "cancelled"}

# ── 步进转移规则（issue #18 阶段 1：状态机的唯一家园，ADR-0015 §4）────
# 会开确认门的工具。门一律挂**工具自己**那一步（gate-lifecycle 批次 1，2026-10-16）。
#
# 这里原是一张「门工具 → 应挂步骤 skill」的映射表，把 generate_* 映射到对应的
# 「发送」步（generate_learning_plan → send_learning_plan）。那张表的前提是
# **计划里存在发送步**——而计划是 LLM 经 plan_task 生成的：端到端实测
# （evals/e2e_long_chain.py）生成的 7 步里根本没有 send_learning_plan，
# 于是 _find_step 找不到，回退到无关的 running 步、或干脆谁也没挂，run 收不了尾。
# 生成即门 —— 门就挂生成步自己身上。
_GATE_TOOLS = frozenset({
    "send_learning_plan", "send_report_to_parent",
    "generate_learning_plan", "generate_parent_report",
    "assign_adaptive_practice", "delete_bank",
})


def _find_step(steps: List[Dict], tool_name: str):
    """按 skill 找可转移的步骤 —— **只认同名步，没有回退**（gate-lifecycle 1.4）。

    曾经有一个 `allow_fallback`：门/拦下路径找不到同名步时，认当前 running/failed
    步（2026-09-14 的「门不只挂同名步骤」）。那条回退是错的：计划里没有同名步，
    恰恰说明**这扇门不属于计划里的任何一步**；硬塞给一个碰巧在跑的无关步就是伪造
    归属 —— 老师看到「第 5 步等待确认」，而第 5 步压根没在等什么。
    成功、失败、开门三类转移一律只认自己那一步；找不到就什么都不做。
    """
    return next((s for s in steps
                 if s.get("skill") == tool_name
                 and s.get("status") in ("pending", "running", "failed",
                                         "awaiting_confirmation")), None)


def _start_next_step(steps: List[Dict], transitions: List[tuple]) -> None:
    """启动下一步 —— 计划里任何时刻只该有**一个** running 步（gate-lifecycle 1.6）。

    LLM 一轮里并行调多个工具时，第二个结果回来会再启动一个 pending 步：计划里就出现
    两个 running，「当前步」不再有唯一答案 —— 门该挂哪一步也跟着不确定（断点②）。
    已经有 running 步时**什么都不启动**：那一步还没跑完，后面的轮不到。
    """
    if any(s.get("status") == "running" for s in steps):
        return
    nxt = next((s for s in steps if s.get("status") == "pending"), None)
    if nxt is not None:
        nxt["status"] = "running"
        transitions.append((nxt["step"], "running"))


def compute_tool_transition(steps: List[Dict], tool_name: str,
                            result) -> List[tuple]:
    """工具结果 → 步转移列表 [(step_no, status)]（纯函数，原地更新 steps）。

    approval_needed = 被门拦下 → awaiting_confirmation，**阻塞**：不标完成、
    不启动下一步（曾被 adapter 当 success 标成 completed——「没确认就往下推」
    的根因）。error → failed（不自动推进）。成功 → completed 并启动下一步。
    """
    if tool_name == "plan_task" or not steps:
        return []
    transitions: List[tuple] = []
    if isinstance(result, dict) and result.get("approval_needed") \
            and tool_name in _GATE_TOOLS:
        # 门挂工具**自己**那一步（1.5）。生成步**不进 completed** —— 它的产物（草稿）
        # 还没被老师确认，这一步的事没办完；等重放成功才由 compute_replay_transition
        # 打勾。原先「生成步打勾 + 门挂发送步」的写法，前提是计划里有发送步。
        target = _find_step(steps, tool_name)
        if target is not None and target.get("status") != "awaiting_confirmation":
            target["status"] = "awaiting_confirmation"
            transitions.append((target["step"], "awaiting_confirmation"))
        return transitions
    if isinstance(result, dict) and result.get("approval_needed"):
        return []  # 工作区外访问等按次审批：不属计划门，不动 todolist
    # gate_open_blocked = 该工具被**已打开的确认门**拦下（2026-09-20 实测：门等待
    # 期间 LLM 多调的发送类工具返回此错误）。语义是「暂停、等**别的**门」，
    # **不是**「我在等确认」——所以不动任何步的状态（1.7）。
    # 曾被标成 awaiting_confirmation：那会给一个**没有任何门挂住**的工具凭空造出
    # 一个永远解不开的等待（2026-10-16 实测：step5 assign_adaptive_practice 卡在
    # 等待确认，run 永远 running）。也不标 failed —— 它没有失败。
    if isinstance(result, dict) and result.get("gate_open_blocked"):
        return []
    # no_draft_to_send = 发送工具被拒、且库里压根没有可发送的草稿（2026-10）。
    # 它同样不是失败：正确动作是回去生成草稿。曾被当成普通 error → 那一步被标
    # failed，老师看到「第 8 步发送家长报告 ✗」，而实际上什么都没发、也没出错。
    if isinstance(result, dict) and result.get("error") == "no_draft_to_send":
        return []
    # plan_dependency_unmet = D1 依赖序暂缓（2026-10-26 8 步长链实测）：前置步
    # 还没终态（常见：挂在确认门上等老师），本次调用**没有执行**、recoverable。
    # 语义是「稍后再来」——不是失败。曾被普通 error 分支标成 failed（终态），
    # 老师批准前置步后模型重试成功、报告真的发出去了，计划卡却永远「✕ 失败」，
    # 「任务已全部完成」到不了 8/8。步骤留在 pending，重试成功照常打勾。
    if isinstance(result, dict) and result.get("error") == "plan_dependency_unmet":
        return []
    # rejected_by_teacher = 老师在确认卡上打回/放弃：是取消，不是失败。
    # pilot（图内挂起）路径下，工具恢复后返回该错误码并走到这里；legacy 路径由
    # _gate_step_events 直接写 cancelled —— 两条机制对同一个决定必须落同一个
    # 终态（2026-10-24 实测：pilot 下第 4 步落 failed，老师看到「失败」而非「已放弃」）。
    if isinstance(result, dict) and result.get("error") == "rejected_by_teacher":
        step = _find_step(steps, tool_name)
        if step is not None and step.get("status") in ("pending", "running",
                                                       "awaiting_confirmation"):
            step["status"] = "cancelled"
            transitions.append((step["step"], "cancelled"))
        return transitions
    # 成功/失败即打勾：只认同名 skill 的步骤（缺陷 #5）——无关工具（read/bash
    # 等）的成功绝不能替 running 步骤打勾，否则计划被野工具误推进/解错步。
    step = _find_step(steps, tool_name)
    if step is None:
        return []
    failed = isinstance(result, dict) and "error" in result
    if failed:
        if step.get("status") in ("pending", "running", "failed"):
            step["status"] = "failed"
            transitions.append((step["step"], "failed"))
        return transitions
    if step.get("status") in ("pending", "running", "failed",
                              "awaiting_confirmation"):
        step["status"] = "completed"
        transitions.append((step["step"], "completed"))
        _start_next_step(steps, transitions)
    return transitions


def compute_abort_transition(steps: List[Dict]) -> List[tuple]:
    """异常中止收尾：running → failed、pending → cancelled（纯函数）。

    与 compute_tail_transition 的差别：那个是「正常收尾但没做完」，这个是「被打断」。
    awaiting_confirmation 一律不动——门实体还在，人补一个决定就能继续，
    把它标掉等于替老师放弃。
    """
    transitions: List[tuple] = []
    for s in steps:
        st = s.get("status")
        if st == "running":
            s["status"] = "failed"
            transitions.append((s["step"], "failed"))
        elif st == "pending":
            s["status"] = "cancelled"
            transitions.append((s["step"], "cancelled"))
    return transitions


def compute_tail_transition(steps: List[Dict]) -> List[tuple]:
    """收尾对账：本轮结束时仍未执行的步骤 → cancelled（纯函数，原地更新 steps）。

    存在理由：计划由模型自己按序执行，模型可以在**任意一步之后**收尾。未执行的步
    原状是 pending —— 既没有完成事件也不报错，用户看到的就是「做到一半没了」。
    2026-10 实测：「帮我准备一次月考」生成 5 步计划，只跑了前 3 步
    （诊断完就收尾），出题与复习计划两步凭空消失且无任何提示。

    状态取 cancelled 而**不是新增 skipped**：前端 reducer 的状态白名单是固定集合
    （frontend/src/lib/agent-stream-reducer.ts:49），未知状态会被丢弃，
    新增取值需要前后端同步改一版，不属本次修复范围。
    """
    transitions: List[tuple] = []
    for s in steps:
        if s.get("status") in ("pending", "running"):
            s["status"] = "cancelled"
            transitions.append((s["step"], "cancelled"))
    return transitions


def _step_by_no(steps: List[Dict], step_no: int, allowed):
    """按门绑定的步号定位步骤（gate-lifecycle 批次 1：门是步归属的唯一真源）。"""
    target = next((s for s in steps if s.get("step") == step_no), None)
    if target is None or target.get("status") not in allowed:
        return None
    return target


def compute_decision_transition(steps: List[Dict], action: str,
                                step_no: int = 0) -> List[tuple]:
    """三值决定 → 步转移（纯函数）。approve 解锁门步骤进入重放中；
    重放结果由 compute_replay_transition 落终态——「未批准」与「重放完成」
    从此是两个状态。

    `step_no` 是门实体上记的绑定步号（`agent_gates.plan_step`）：给了就**只认那一步**，
    对不上即什么都不做（fail-closed）；没给（0 = 存量门未记录）才回退旧行为
    「认当前 awaiting_confirmation 的步」。
    """
    if step_no:
        target = _step_by_no(steps, step_no, ("awaiting_confirmation",))
    else:
        target = next((s for s in steps
                       if s.get("status") == "awaiting_confirmation"), None)
    if target is None:
        return []
    new_status = {"approve": "running", "reject": "revising",
                  "abandon": "cancelled"}.get(action)
    if not new_status:
        return []
    target["status"] = new_status
    return [(target["step"], new_status)]


def compute_replay_transition(steps: List[Dict], tool_name: str,
                              success: bool, step_no: int = 0) -> List[tuple]:
    """批准后重放结果 → 步转移（纯函数）。成功 → completed 并启动下一步；
    失败 → **退回 awaiting_confirmation**（门同时退回 open，老师可重试；
    步跟着门走，重试时才过得了 compute_decision_transition 的接受集）。

    `step_no` 同上 —— **这里是「重放目标从门实体构造」的落点**。原先按
    `skill == tool_name` 找步，而门的 tool_name（`send_learning_plan`）与它实际绑住的
    步（`generate_learning_plan`）可以不同名：决定那边认状态、重放那边认工具名，
    **两个函数对「门绑的是哪一步」各有一套说法**，那一步就永远停在 running。
    """
    if step_no:
        target = _step_by_no(steps, step_no, ("running", "awaiting_confirmation"))
    else:
        target = next((s for s in steps
                       if s.get("skill") == tool_name
                       and s.get("status") in ("running", "awaiting_confirmation")),
                      None)
    if target is None:
        return []
    if success:
        target["status"] = "completed"
        transitions = [(target["step"], "completed")]
        # 计划里若为**这个工具**单独留了一步（`send_*`），那一步的活也办完了。
        #
        # 为什么是两步：老师批准的是**生成步**（生成即门，1.5 —— 卡上确认的就是那份
        # 产物），但重放真正跑的是**发送工具**。也就是说**一次批准办完两件事**：
        # 报告生成了、也发出去了。计划把这两件事写成两步（`PLAN_PROMPT` 明确要求
        # 「提到同步家长必须包含 `generate_parent_report` **与** `send_report_to_parent`
        # 步骤」），两步就该都收。
        #
        # 2026-10-17 的经过值得记：这个"第二步"曾经在 channel 里另有一个调用面
        # （`_persist_send_completion`），它**只认 `running`/`awaiting_confirmation`**，
        # 于是只在"同名步恰好刚被 `_start_next_step` 启动"时才匹配得上 ——
        # 7 步计划里那一步**碰巧**是 running，看着像正常；8 步计划里它还是 `pending`，
        # 匹配不上、那格永远悬着。当时把它当"多余的双写"删掉，端到端当场红在
        # `send_report_to_parent` 停在 running —— 才看清**它是承重的，错的是过滤器**。
        # 现在收进转移规则里：按 skill 认，`pending` 也算数，且明确排除已定位的那一步。
        #
        # 只认**生成→发送配对**（门绑的生成步 ≠ 重放工具）：同名 skill 的另一个步
        # 是另一件事（两个学生、两批练习、两次发送），不能替它打勾。
        twin = None
        if target.get("skill") != tool_name:
            twin = next((s for s in steps
                         if s.get("skill") == tool_name
                         and s.get("step") != target["step"]
                         and s.get("status") in ("pending", "running",
                                                 "awaiting_confirmation", "failed")),
                        None)
        if twin is not None:
            twin["status"] = "completed"
            transitions.append((twin["step"], "completed"))
        _start_next_step(steps, transitions)
        return transitions
    target["status"] = "awaiting_confirmation"
    return [(target["step"], "awaiting_confirmation")]


def extract_step_artifact(result) -> Dict:
    """工具结果 → 可入库的产物 dict（${step_N.field} 的 field 就从这里的键取）。

    只收 dict：工具结果是 json.dumps 出来的对象，adapter 已 parse；列表/字符串结果
    没有字段名可被引用，落库也没人来取。非 JSON 可序列化的值整个丢掉 —— 宁可这一步
    没产物，也不让一次落库失败把后续步骤的参数注入一起带走。
    下划线开头的键是 adapter 的 UI/路由字段（_route / _component / _reminder 等），
    不是产物，引用它们只会把路由信息灌进工具参数。
    """
    if not isinstance(result, dict):
        return {}
    try:
        json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        return {}
    return {k: v for k, v in result.items() if not str(k).startswith("_")}


def _active_run_row(db, conversation_id: str):
    """会话的活跃 run 行（running/paused 中最新一条）；无会话号或无行返回 None。"""
    if not conversation_id:
        return None
    from app.models.database import PlanRun as _Run
    return (db.query(_Run)
            .filter(_Run.conversation_id == conversation_id,
                    _Run.status.in_(("running", "paused")))
            .order_by(_Run.id.desc())
            .first())


def _load_artifacts(db, run_id: str, only_steps: set) -> Dict[int, Dict]:
    """取该 run 的产物表 {步号: 产物}；only_steps 非空时只取这些步。

    按声明取而不是全量取：一步只该看见它自己声明的依赖（depends_on ∪ 参数里显式
    引用的步号），别的步的产物进来只会在解析时多出几个"碰巧同名"的字段。
    """
    from app.models.database import PlanStepArtifact as _Art
    rows = db.query(_Art).filter(_Art.run_id == run_id).all()
    return {r.step: r.artifact for r in rows
            if isinstance(r.artifact, dict)
            and (not only_steps or r.step in only_steps)}


class PlanRunService:
    @staticmethod
    def start_run(db, conversation_id: str, goal: str, steps: List[Dict]) -> Dict:
        if not conversation_id:
            return {}
        from app.models.database import PlanRun as _Run
        db.query(_Run).filter(
            _Run.conversation_id == conversation_id, _Run.status != "completed",
        ).update({"status": "superseded"}, synchronize_session=False)
        run = _Run(
            run_id=uuid.uuid4().hex[:16],
            conversation_id=conversation_id,
            goal=goal or "",
            steps=[dict(s, status=s.get("status", "pending")) for s in steps],
        )
        db.add(run)
        db.commit()
        return {"run_id": run.run_id, "goal": run.goal, "steps": run.steps}

    @staticmethod
    def get_active_run(db, conversation_id: str) -> Optional[Dict]:
        if not conversation_id:
            return None
        from app.models.database import PlanRun as _Run
        run = (db.query(_Run)
               .filter(_Run.conversation_id == conversation_id,
                       _Run.status.in_(("running", "paused")))
               .order_by(_Run.id.desc())
               .first())
        if not run:
            return None
        return {"run_id": run.run_id, "goal": run.goal,
                "steps": run.steps, "status": run.status}

    # ── Paused Run（ADR-0016）：断线/超时/孤儿中断 → paused；显式决定/消息 → running ──

    @staticmethod
    def mark_run_paused(db, conversation_id: str) -> None:
        if not conversation_id:
            return
        from app.models.database import PlanRun as _Run
        db.query(_Run).filter(
            _Run.conversation_id == conversation_id, _Run.status == "running",
        ).update({"status": "paused"}, synchronize_session=False)
        db.commit()

    @staticmethod
    def mark_run_resumed(db, conversation_id: str) -> None:
        if not conversation_id:
            return
        from app.models.database import PlanRun as _Run
        db.query(_Run).filter(
            _Run.conversation_id == conversation_id, _Run.status == "paused",
        ).update({"status": "running"}, synchronize_session=False)
        db.commit()

    @staticmethod
    def mark_step(db, conversation_id: str, step: int, status: str,
                  gate_draft_id: Optional[str] = None) -> Optional[Dict]:
        """推进一步的状态并返回最新 run（无活跃 run 静默 no-op）。
        gate_draft_id：该步挂起的确认门草稿（ADR-0013 重同步依据——
        写进 run 步骤 JSON，服务重启后门卡可从 DB 重建）。
        全部步骤到终态时 run 置 completed。"""
        if not conversation_id:
            return None
        from app.models.database import PlanRun as _Run
        run = (db.query(_Run)
               .filter(_Run.conversation_id == conversation_id,
                       _Run.status.in_(("running", "paused")))
               .order_by(_Run.id.desc())
               .first())
        if not run:
            return None
        # JSON 列必须整体重新赋值——原地 mutate 不会被 SQLAlchemy 检测，
        # 且 commit 后过期重载会直接丢弃变更
        new_steps = []
        for s in (run.steps or []):
            if s.get("step") == step:
                s = dict(s, status=status)
                if gate_draft_id:
                    s["gate_draft_id"] = gate_draft_id
            new_steps.append(dict(s))
        run.steps = new_steps
        if all(s.get("status") in _STEP_TERMINAL for s in new_steps):
            run.status = "completed"
        elif run.status == "paused":
            # 决定/工具驱动的转移 = 老师的显式动作（ADR-0016 §2）→ 运行回到 running
            run.status = "running"
        db.commit()
        return {"run_id": run.run_id, "goal": run.goal,
                "steps": new_steps, "status": run.status}

    # ── 步骤产物（C2：上下文依赖 → 实体依赖，2026-10）──────────────────
    # 跨步引用 ${step_N.field} 原先只能靠对话历史带，而 trim_context
    # （agent/context_manager.py:20-73）超过 30 条消息就只留最近 6 条 + ≤10 条关键词
    # 命中，其余压成 ≤200 字摘要 —— 第 5 步要引用的第 1 步产物早就不在上下文里了。
    # 产物按步落库之后，执行下一步时按键读回，与对话里还剩几条消息无关。

    @staticmethod
    def record_step_artifact(db, conversation_id: str, step: int, artifact: Dict) -> None:
        """某一步的结构化产物入库（plan_step_artifacts，按 run_id + step 幂等 upsert）。

        只记**活跃 run** 的产物：新 plan_task 作废旧 run，旧产物跟着作废，不会漏进
        新一轮的参数表。空产物直接跳过 —— 没有字段名可引用，落一行空 JSON 只会让
        后面「解析成了空值」和「压根没落库」更难分辨。
        """
        if not conversation_id or not step or not isinstance(artifact, dict) or not artifact:
            return
        from app.models.database import PlanStepArtifact as _Art
        run = _active_run_row(db, conversation_id)
        if run is None:
            return
        row = (db.query(_Art)
               .filter(_Art.run_id == run.run_id, _Art.step == step)
               .first())
        if row:
            row.artifact = artifact
        else:
            db.add(_Art(run_id=run.run_id, conversation_id=conversation_id,
                        step=step, artifact=artifact))
        db.commit()

    @staticmethod
    def get_step_artifacts(db, conversation_id: str) -> Dict[int, Dict]:
        """读回活跃 run 已落库的步骤产物（{步号: 产物}）。"""
        if not conversation_id:
            return {}
        run = _active_run_row(db, conversation_id)
        if run is None:
            return {}
        return _load_artifacts(db, run.run_id, set())

    @staticmethod
    def resolve_tool_args(db, conversation_id: str, tool_name: str,
                          kwargs: Optional[Dict] = None) -> Dict:
        """某一步执行前，把参数里的 ${step_N.field} 按库内产物解析好（C2 的注入点）。

        返回可直接喂给工具的完整参数：该步在计划里声明的 args（引用已解析）+ 模型
        这一轮自己传的键，**模型优先** —— 它可能刚按老师的补充改了主意。模型常把
        模板原样抄进参数（PLAN_PROMPT 就是这么教它写的），所以合并后再解析一遍。

        查库异常一律降级成「没有产物」并留痕，绝不向外抛：这只是执行路径上的增强，
        一次库抖动最多让参数不全（工具自带默认值与追问分支），不能让整轮对话因为
        取不到产物而炸掉。
        """
        from agent.planner import referenced_steps, resolve_args

        given = dict(kwargs or {})
        plan_args: Dict = {}
        artifacts: Dict[int, Dict] = {}
        try:
            run = _active_run_row(db, conversation_id)
            if run is not None:
                step = next((s for s in (run.steps or [])
                             if s.get("skill") == tool_name
                             and s.get("status") in ("pending", "running")), None)
                if step:
                    plan_args = step.get("args") or {}
                    declared = set(step.get("depends_on") or [])
                    artifacts = _load_artifacts(
                        db, run.run_id, declared | referenced_steps(plan_args))
        except Exception as e:  # noqa: BLE001
            logger.warning("plan arg resolve failed cid=%s tool=%s: %s",
                           conversation_id, tool_name, e)
            plan_args, artifacts = {}, {}
        resolved, missing = resolve_args(plan_args, artifacts)
        merged, copied = resolve_args({**resolved, **given}, artifacts)
        if missing or copied:
            logger.warning("plan arg refs unresolved cid=%s tool=%s steps=%s",
                           conversation_id, tool_name, missing + copied)
        return merged

    # ── 会话门映射（#13 冒烟补丁 2：裸生成门的 DB 重同步）─────────

    @staticmethod
    def record_gate(db, conversation_id: str, draft_id: str, artifact: str) -> None:
        """门开启时记录 会话→草稿 映射（幂等 upsert）。"""
        if not conversation_id or not draft_id:
            return
        from app.models.database import ConversationGate as _Gate
        row = db.get(_Gate, conversation_id)
        if row:
            row.draft_id = draft_id
            row.artifact = artifact
        else:
            db.add(_Gate(conversation_id=conversation_id,
                         draft_id=draft_id, artifact=artifact))
        db.commit()

    @staticmethod
    def get_gate(db, conversation_id: str) -> Optional[Dict]:
        """读取会话待确认门；草稿已不在待确认态则视为陈旧，惰性清除并返回 None。"""
        if not conversation_id:
            return None
        from app.models.database import ConversationGate as _Gate
        row = db.get(_Gate, conversation_id)
        if not row:
            return None
        from app.services.plan_draft_service import plan_draft_service
        from app.services.parent_report_draft_service import parent_report_draft_service
        svc = (parent_report_draft_service if row.artifact == "parent_report"
               else plan_draft_service)
        d = svc.get_draft(db, row.draft_id)
        if not d or d.get("status") != "awaiting_confirmation":
            db.delete(row)
            db.commit()
            return None
        if row.artifact == "parent_report":
            action, label = "send_parent", "学习报告"
        else:
            action, label = "send_student", "学习计划"
        return {"draft_id": d["draft_id"], "student_id": d["student_id"],
                "version": d["version"], "action": action,
                "message": f"{label}（第 {d['version']} 版）等待确认"}


plan_run_service = PlanRunService()


# ── 转移投影引擎（2026-09-21 架构审查 B）───────────────────────────────
# 「转移 → SSE 事件 + DB 落库」此前在四个调用面各自手搓循环（adapter /
# _decision_unlock_events / _gate_step_events / _persist_send_completion），
# 每加一种路径就要记得再抄一遍——打回无后续、不收尾类 bug 的固定来源。
# 收权：所有调用面只算转移（纯函数），投影与持久化走这一个入口。

def _bind_open_gate(db, cid: str, step_no: int) -> None:
    """门与计划步**同源**（gate-lifecycle 批次 1）：把开着的门绑到它所在的那一步。

    绑定就发生在这里 —— 转移元组给出了步号，`get_open_gate` 给出了门，而「门挂哪
    一步」的判定也只有这一处。绑上之后，决定与重放都按门上的步号走，不再按工具名
    猜（见 compute_decision_transition / compute_replay_transition）。
    """
    from app.services.gate_service import gate_service
    og = gate_service.get_open_gate(db, cid)
    if og:
        gate_service.bind_step(db, og["gate_id"], step_no)


def project_transitions(cid: str, transitions: List[tuple],
                        gate_draft_id: str = "") -> List[str]:
    """把 [(step_no, status)] 投影为 plan_progress SSE 事件串并落库。

    - 事件格式与历史完全一致（前端契约不变）
    - gate_draft_id 仅随 awaiting_confirmation 状态写入（ADR-0013 重同步）
    - 无 cid（单测/内存态）时只产事件不落库
    - 返回值兼容各调用面原有的 list[str] 形状
    """
    import json as _json
    events = []
    for step_no, status in transitions:
        events.append(_json.dumps({
            "type": "plan_progress", "current_step": step_no, "status": status,
        }, ensure_ascii=False))
    if not cid:
        return events
    try:
        from app.models.database import get_db as _get_db
        db = next(_get_db())
        try:
            for step_no, status in transitions:
                plan_run_service.mark_step(
                    db, cid, step_no, status,
                    gate_draft_id=gate_draft_id if status == "awaiting_confirmation" else "")
                if status == "awaiting_confirmation":
                    _bind_open_gate(db, cid, step_no)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        # 落库失败**不阻断** SSE 投影（与各调用面原行为一致）—— 但必须留痕：
        # 内存 plan_steps 已推进、run 行却停在旧步骤，重启后计划卡会凭空退回上一步，
        # 而用户那边只看到"卡住了"。原先这里是 `except: pass`，一个字都不留。
        # 2026-10-17：`langgraph_channel` 里那个专门补这笔落库的 `_persist_send_completion`
        # 删掉（它用旧规则多推一格）之后，**这里成了唯一的落库点**，
        # 那条"没落库必须留痕"的保证也就必须落在这一处。
        logger.warning("plan step persist failed cid=%s transitions=%s: %s",
                       cid, transitions, e)
    return events


def record_transition_artifacts(cid: str, transitions: List[tuple], result) -> None:
    """把刚完成的那些步的产物落库 —— 调用面加一行即可（C2 的落库点）。

    形状与 project_transitions 一致：自己开库、自己提交、失败只留痕不抛（落库失败
    不能阻断这一轮的 SSE 投影）。只落 completed 的步：awaiting_confirmation 的那一步
    产物还没被老师确认，failed 的那一步没有值得给后续步骤引用的东西。
    """
    artifact = extract_step_artifact(result)
    done = [step_no for step_no, status in transitions if status == "completed"]
    if not cid or not artifact or not done:
        return
    try:
        from app.models.database import get_db as _get_db
        db = next(_get_db())
        try:
            for step_no in done:
                plan_run_service.record_step_artifact(db, cid, step_no, artifact)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("plan artifact persist failed cid=%s steps=%s: %s",
                       cid, done, e)
