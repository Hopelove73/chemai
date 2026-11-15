"""确认门只读面 —— 4a「待确认」列表（ADR-0015 阶段 4a / 计划 T8）。

这一页**只读**：不开门、不决定、不收敛。假门的收敛归
`gate_service.supersede_stale_gates`（今日只有开门路径调，全库扫描等 T17 的每日作业），
挂在这条读路径上会把决定单元中途别人的事务提交掉（T4 的立项理由）。

收权口径（D3 / R20 等价式）：列表两段的并集 == `owns_conversation` 在员工身份下
的 True 集。登记在别人名下的会话两段都不出现；无登记的学生/家长老会话
（`m-` / `p-` 前缀）一律不出现 —— 聊天页对它们返回 False，摆进教师队列就是
一次跨角色露出。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.middleware.conversation import owns_conversation
from app.models.database import AgentGate, get_db
from app.services.gate_service import gate_service
from app.services.work_unit import work_unit

router = APIRouter()


@router.get("/gates/pending")
def list_pending_gates(
    kind: Optional[str] = Query(
        None, pattern="^(draft|boolean)$",
        description="按门的类型筛选（D14 的唯一筛选维度）。启用后四字段全部随筛选收窄"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("agent", "read")),
):
    """`items` / `owned_total` / `unowned_items` / `unowned_total`（D14/R15 定稿）。

    待确认入口读 `owned_total` 画那颗数量胶囊（NEVER 用 `items.length`，上限会截断它），
    折叠段段头读 `unowned_total`。筛选与 LIMIT 都落在**同一份**已过滤集合上，所以
    假门不占展示名额、计数与页面不会互相打脸。
    """
    return gate_service.pending_gates(db, current_user.get("user_id") or "", kind=kind)


# ── 「结果未知」卡（gate-lifecycle 2.7）──────────────────────────────
# 门的第三态：`claim_gate` 在执行**之前**把门占成 `executing`，正常情况由
# `finish_gate` 收口（成功 → executed，失败 → 退回 open）。两者永远在同一次请求、
# 同一个进程里，所以**启动时**还停在 `executing` 的门 = 执行它的那个进程已经不在了
# —— 动作可能跑过、也可能没跑，系统无从判断，只有老师能核对真实世界。
# 这也是当初决定把它做成一张卡、而不是让系统猜的原因：能看事实就别猜。


class InDoubtDecision(BaseModel):
    """老师的裁定。两个动作正好对应两种现实。"""
    retry: bool


@router.get("/gates/in-doubt")
def list_in_doubt_gates(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("agent", "read")),
):
    """等老师裁定的门（只读）。形状与 `/gates/pending` 同构，前端可共用渲染器。

    **判据与待确认列表不是同一条**：那边问「哪些 open 门算数」，这边问「哪些门需要
    裁定」。尤其是草稿判据在这边不成立 —— in-doubt 最典型的情形就是动作**已经成功**
    （草稿已 sent）、进程死在收口之前，套草稿判据会恰好漏掉最该报的那一类。
    """
    return gate_service.in_doubt_gates(db, current_user.get("user_id") or "")


@router.post("/gates/{gate_id}/in-doubt")
def resolve_in_doubt_gate(
    gate_id: str,
    decision: InDoubtDecision,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("agent", "create", allow_self=True)),
):
    """老师对「结果未知」卡裁定：`retry=false` 确认已执行 / `retry=true` 重试。

    权限走与 `/resume` 同一条（`agent:create`）—— 两次都是"老师对一张确认卡做决定"，
    只是这张卡的选项不一样。归属另判：决定改的是别人的会话状态，不能只看权限。

    409 而不是 400：这次裁定撞上的是**状态**（门已经不是 in_doubt 了 —— 别处已经
    处理过、或它压根没进过那一态），不是请求写得不对。
    """
    row = db.query(AgentGate).filter(AgentGate.gate_id == gate_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="确认门不存在")
    if not owns_conversation(db, row.conversation_id, current_user):
        raise HTTPException(status_code=403, detail="无权处理该会话的确认门")

    with work_unit(db):
        done = gate_service.resolve_in_doubt(db, gate_id, decision.retry)
    if not done:
        raise HTTPException(status_code=409, detail="这扇门已经不需要裁定了，请刷新列表")
    return {"gate_id": gate_id,
            "status": "open" if decision.retry else "executed"}
