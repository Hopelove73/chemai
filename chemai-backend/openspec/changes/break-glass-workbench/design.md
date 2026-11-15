# Design: break-glass-workbench

## Context

后端 `app/api/break_glass.py` 三端点完整：`POST`（理由必填 400、`MAX_MINUTES` 上限、不叠开、审计 `_audit`）、`GET /active`（本人会话 + `remaining_seconds`）、`DELETE`（提前结束）。矩阵中 `break_glass` 资源仅 super_admin 持有（admin 镜像之前追加，语义「admin 不该有它」）。

## Goals / Non-Goals

**Goals:**
- super_admin 对提权全生命周期的界面操作（查/起/停）
- 后端零改动

**Non-Goals:**
- 跨用户提权历史审计视图（审计已在 `OperationLog`，`/admin/logs` 可查）
- 提权后端逻辑调整（时长上限、叠开语义维持现状）

## Decisions

**D1 — 纯前端 change。** 三端点语义与审计完备，界面只是操作面。菜单 gate 用 `break_glass:read`（矩阵自动只给 super_admin），不加新键。

**D2 — 倒计时用 `remaining_seconds` 本地递减，过期主动重查 `/active`。** 到期自动失效由后端保证；前端过期后重查一次避免时钟漂移展示错状态。

**D3 — 发起表单的时长选项提供常用档（15/30/60 分钟），允许自定义但提交前提示上限值。** 后端 400 是最终裁决，前端提示只是体验。

## Risks / Trade-offs

- [提权入口界面化后滥用（随手起提权）] → 既有纪律已在后端（理由必填 + 审计 + 限时 + 不叠开）；界面在发起确认流中回显「此操作将被审计留痕」。
- [倒计时展示与真实过期漂移] → D2 的过期重查兜底。

## Migration Plan

纯前端 + 一行菜单登记；回滚 revert。

## Open Questions

- 无。
